"""Adapter fuer die API von Pterodactyl und Pelican (gleiche API; Calagopus: calagopus.py).

Der gesamte Zugriff auf das Panel laeuft ueber diese Klasse oder eine Unterklasse. Im Read-Only-Modus
werden nicht-lesende HTTP-Methoden technisch blockiert, bevor eine Anfrage das
Livesystem erreicht. Unbekannte Felder in Antworten werden ignoriert, damit
Panel-Updates den Manager nicht brechen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx

from .config import Settings
from .i18n import panel_name, t

log = logging.getLogger("hatchtower.ptero")

READ_METHODS = frozenset({"GET"})


class ReadOnlyViolation(RuntimeError):
    """Ein schreibender Zugriff wurde im Read-Only-Modus verhindert."""


class PteroError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class ServerInfo:
    ptero_id: int
    identifier: str
    uuid: str
    name: str
    node_id: int | None
    egg: str
    allocations: list[dict[str, Any]]
    primary_ip: str | None
    primary_port: int | None
    limit_memory: int
    limit_disk: int
    limit_cpu: int
    suspended: bool


class PteroClient:
    # Zuletzt gemeldetes Rate-Limit des Panels: (Limit pro Minute, verbleibend, Zeitpunkt). Es gilt pro
    # API-Key für alle Instanzen zusammen; Backups richten sich danach, damit das Monitoring Luft behält.
    rate: tuple[int, int, float] | None = None

    def __init__(self, settings: Settings):
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.pterodactyl_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"Accept": "application/json", "User-Agent": "Hatchtower"},
            follow_redirects=False,
        )
        self._wings_hosts: set[str] = set()

    async def close(self) -> None:
        await self._http.aclose()

    # -- Grundfunktionen -------------------------------------------------

    def _guard(self, method: str) -> None:
        if self.settings.read_only and method.upper() not in READ_METHODS:
            raise ReadOnlyViolation(f"{method} ist im Read-Only-Modus gesperrt")

    async def _request(self, method: str, path: str, *, key: str, params: dict | None = None,
                       json_body: Any = None, content: bytes | None = None, raw: bool = False) -> Any:
        self._guard(method)
        if key == "app" and method.upper() not in READ_METHODS:
            raise ReadOnlyViolation("Die Application-API wird nur lesend verwendet")
        token = self.settings.app_key if key == "app" else self.settings.client_key
        if not token:
            raise PteroError("Kein API-Key für diesen Zugriff hinterlegt")
        headers = {"Authorization": f"Bearer {token}"}
        if content is not None:
            headers["Content-Type"] = "text/plain"
        for attempt in range(4):
            try:
                response = await self._http.request(
                    method, path, params=params, headers=headers, json=json_body, content=content
                )
            except httpx.HTTPError as exc:
                raise PteroError(t("{panel} nicht erreichbar: {grund}", grund=exc.__class__.__name__)) from exc
            limit, remaining = response.headers.get("x-ratelimit-limit", ""), response.headers.get("x-ratelimit-remaining", "")
            if limit.isdigit() and remaining.isdigit():
                self.rate = (int(limit), int(remaining), time.monotonic())
            # Rate-Limit des Panels: lesende Anfragen kurz warten und wiederholen
            if response.status_code != 429 or method.upper() not in READ_METHODS or attempt == 3:
                break
            try:
                wait = min(float(response.headers.get("retry-after", "2")), 30.0)
            except ValueError:
                wait = 2.0
            await asyncio.sleep(max(wait, 1.0))
        if response.status_code >= 400:
            raise PteroError(_error_text(response), response.status_code)
        if raw:
            return response.content
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise PteroError(t("{panel} lieferte kein JSON (falsche URL?)"), response.status_code) from exc

    async def _get(self, path: str, *, key: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, key=key, params=params)

    # -- Application-API (nur lesen) ------------------------------------

    async def list_nodes(self) -> list[dict[str, Any]]:
        data = await self._get("/api/application/nodes", key="app", params={"per_page": 100})
        nodes = [item.get("attributes", {}) for item in data.get("data", [])]
        for node in nodes:
            if node.get("fqdn"):
                self._wings_hosts.add(node["fqdn"])
        return nodes

    async def list_servers(self) -> list[ServerInfo]:
        servers: list[ServerInfo] = []
        page = 1
        while True:
            data = await self._get(
                "/api/application/servers",
                key="app",
                params={"per_page": 100, "page": page, "include": "egg,allocations"},
            )
            for item in data.get("data", []):
                servers.append(_parse_server(item.get("attributes", {})))
            pagination = data.get("meta", {}).get("pagination", {})
            if page >= int(pagination.get("total_pages", 1) or 1):
                break
            page += 1
        return servers

    # -- Client-API (nur lesen) -----------------------------------------

    async def resources(self, identifier: str) -> dict[str, Any]:
        data = await self._get(f"/api/client/servers/{identifier}/resources", key="client")
        attrs = data.get("attributes", {})
        res = attrs.get("resources", {})
        return {
            "state": attrs.get("current_state") or "unknown",
            "suspended": bool(attrs.get("is_suspended", False)),
            "cpu": float(res.get("cpu_absolute") or 0.0),
            "memory_bytes": int(res.get("memory_bytes") or 0),
            "disk_bytes": int(res.get("disk_bytes") or 0),
            "uptime_ms": int(res.get("uptime") or 0),
            "rx_bytes": int(res.get("network_rx_bytes") or 0),
            "tx_bytes": int(res.get("network_tx_bytes") or 0),
        }

    async def startup_variables(self, identifier: str) -> dict[str, str]:
        data = await self._get(f"/api/client/servers/{identifier}/startup", key="client")
        result: dict[str, str] = {}
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            env = attrs.get("env_variable")
            if env:
                value = attrs.get("server_value")
                if value is None:
                    value = attrs.get("default_value")
                result[env] = "" if value is None else str(value)
        return result

    async def list_files(self, identifier: str, directory: str) -> list[dict[str, Any]]:
        data = await self._get(
            f"/api/client/servers/{identifier}/files/list", key="client", params={"directory": directory}
        )
        files = []
        for item in data.get("data", []):
            a = item.get("attributes", {})
            files.append(
                {
                    "name": a.get("name", ""),
                    "is_file": bool(a.get("is_file", True)),
                    "size": int(a.get("size") or 0),
                    "modified_at": a.get("modified_at"),
                    "mimetype": a.get("mimetype", ""),
                }
            )
        files.sort(key=lambda f: (f["is_file"], f["name"].lower()))
        return files

    async def download_url(self, identifier: str, file: str) -> str:
        data = await self._get(
            f"/api/client/servers/{identifier}/files/download", key="client", params={"file": file}
        )
        return data.get("attributes", {}).get("url", "")

    async def websocket_credentials(self, identifier: str) -> tuple[str, str]:
        data = await self._get(f"/api/client/servers/{identifier}/websocket", key="client")
        payload = data.get("data", {})
        token, socket = payload.get("token"), payload.get("socket")
        if not token or not socket:
            raise PteroError("Keine Websocket-Zugangsdaten erhalten")
        return token, socket

    async def client_server_count(self) -> int:
        """Wie viele Server der Client-Key sieht (Verbindungstest)."""
        data = await self._get("/api/client", key="client", params={"type": "admin-all", "per_page": 100})
        return len(data.get("data", [])) if isinstance(data, dict) else 0

    async def activity(self, identifier: str, limit: int = 25) -> list[dict[str, Any]]:
        data = await self._get(
            f"/api/client/servers/{identifier}/activity", key="client",
            params={"per_page": limit, "sort": "-timestamp"},
        )
        return [item.get("attributes", {}) for item in data.get("data", [])]

    async def read_file(self, identifier: str, file: str) -> bytes:
        return await self._request(
            "GET", f"/api/client/servers/{identifier}/files/contents", key="client", params={"file": file}, raw=True
        )

    # -- Client-API (schreibend, nur mit MANAGER_MODE=read-write) --------

    async def power(self, identifier: str, signal: str) -> None:
        if signal not in ("start", "stop", "restart", "kill"):
            raise PteroError("Unbekanntes Signal")
        await self._request("POST", f"/api/client/servers/{identifier}/power", key="client",
                            json_body={"signal": signal})

    async def command(self, identifier: str, command: str) -> None:
        await self._request("POST", f"/api/client/servers/{identifier}/command", key="client",
                            json_body={"command": command})

    async def write_file(self, identifier: str, file: str, data: bytes) -> None:
        await self._request("POST", f"/api/client/servers/{identifier}/files/write", key="client",
                            params={"file": file}, content=data)

    async def rename(self, identifier: str, root: str, old: str, new: str) -> None:
        await self._request("PUT", f"/api/client/servers/{identifier}/files/rename", key="client",
                            json_body={"root": root, "files": [{"from": old, "to": new}]})

    async def move(self, identifier: str, moves: list[tuple[str, str]]) -> None:
        """Verschiebt Dateien und Ordner (volle Pfade, „von“ → „nach“). Wings hängt „to“ an „root“ an,
        deshalb root = „/“; fehlende Zielordner legt Wings selbst an."""
        await self._request("PUT", f"/api/client/servers/{identifier}/files/rename", key="client",
                            json_body={"root": "/", "files": [{"from": a, "to": b} for a, b in moves]})

    async def create_folder(self, identifier: str, root: str, name: str) -> None:
        await self._request("POST", f"/api/client/servers/{identifier}/files/create-folder", key="client",
                            json_body={"root": root, "name": name})

    async def delete_files(self, identifier: str, root: str, names: list[str]) -> None:
        await self._request("POST", f"/api/client/servers/{identifier}/files/delete", key="client",
                            json_body={"root": root, "files": names})

    async def upload(self, identifier: str, directory: str, files: list[tuple[str, Any]]) -> None:
        """Lädt Dateien über eine vom Panel signierte Wings-URL hoch."""
        self._guard("POST")
        url = await self.wings_url(await self._upload_url(identifier))
        # Das Verzeichnis an die signierte URL anhängen. Nicht über params=, das würde
        # den vorhandenen Query-String samt Token ersetzen.
        url = httpx.URL(url).copy_merge_params({"directory": directory})
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0)) as http:
            try:
                response = await http.post(
                    url,
                    files=[("files", (name, fileobj, "application/octet-stream")) for name, fileobj in files],
                )
            except httpx.HTTPError as exc:
                raise PteroError(t("Wings nicht erreichbar: {grund}", grund=exc.__class__.__name__)) from exc
        if response.status_code >= 400:
            raise PteroError(_error_text(response), response.status_code)

    async def _upload_url(self, identifier: str) -> str:
        data = await self._get(f"/api/client/servers/{identifier}/files/upload", key="client")
        return data.get("attributes", {}).get("url", "")

    # -- Wings (signierte Downloads) -------------------------------------

    async def wings_url(self, url: str) -> str:
        """Prüft, dass eine URL auf einen bekannten Wings-Node zeigt, und setzt ggf. die
        in der Oberfläche hinterlegte andere Adresse für diesen Node ein."""
        if not url:
            raise PteroError(t("Keine signierte URL erhalten"))
        if not self._wings_hosts:
            await self.list_nodes()
        parts = urlsplit(str(url))
        host = parts.hostname or ""
        if host not in self._wings_hosts:
            raise PteroError(t("URL zeigt nicht auf einen bekannten Wings-Node"))
        override = self.settings.node_overrides.get(host)
        if not override:
            return str(url)
        if "://" in override:
            o = urlsplit(override)
            scheme = o.scheme
            if parts.scheme.startswith("ws"):
                scheme = "wss" if o.scheme == "https" else "ws"
            netloc = o.netloc
        else:
            scheme = parts.scheme
            netloc = override if ":" in override else f"{override}:{parts.port}" if parts.port else override
        return urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))

    async def stream_download(self, url: str) -> AsyncIterator[bytes]:
        """Lädt eine von Pterodactyl signierte Datei von Wings (nur GET)."""
        self._guard("GET")
        url = await self.wings_url(url)
        # Netzwerkfehler (auch mitten im Download) als PteroError melden, damit Aufrufer wie Backups sie
        # als Fehler dieser einen Datei behandeln und nicht als unerwarteten Absturz
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as http:
                async with http.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise PteroError(t("{quelle} antwortet mit HTTP {code}", quelle="Wings", code=response.status_code), response.status_code)
                    async for chunk in response.aiter_bytes(65536):
                        yield chunk
        except httpx.HTTPError as exc:
            raise PteroError(t("Wings nicht erreichbar: {grund}", grund=exc.__class__.__name__)) from exc


def _error_text(response: httpx.Response) -> str:
    """Fehlermeldung aus einer Panel- oder Wings-Antwort, ohne Rohdaten durchzureichen."""
    detail = request_id = ""
    try:
        body = response.json()
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors and isinstance(errors, list):
            first = errors[0]
            # Pterodactyl/Pelican: [{"code", "detail"}], Calagopus: ["Text"]
            detail = str(first.get("detail") or first.get("code") or "") if isinstance(first, dict) else str(first)
        elif isinstance(body, dict):
            detail = str(body.get("error") or "")
            request_id = str(body.get("request_id") or "")
    except ValueError:
        pass
    source = "Wings" if request_id or "/upload/" in str(response.request.url) else panel_name()
    text = t("{quelle} antwortet mit HTTP {code}", quelle=source, code=response.status_code)
    if detail:
        text += f": {detail[:200]}"
    if request_id:
        text += f" (Request-ID {request_id})"
    return text


def _parse_server(attrs: dict[str, Any]) -> ServerInfo:
    rel = attrs.get("relationships", {})
    egg = rel.get("egg", {}).get("attributes", {}).get("name", "")
    allocations = []
    primary_ip = primary_port = None
    for item in rel.get("allocations", {}).get("data", []):
        a = item.get("attributes", {})
        ip = a.get("ip_alias") or a.get("ip")
        allocations.append({"id": a.get("id"), "ip": ip, "port": a.get("port")})
        if a.get("id") == attrs.get("allocation"):
            primary_ip, primary_port = ip, a.get("port")
    if primary_ip is None and allocations:
        primary_ip, primary_port = allocations[0]["ip"], allocations[0]["port"]
    limits = attrs.get("limits", {})
    return ServerInfo(
        ptero_id=int(attrs.get("id", 0)),
        identifier=attrs.get("identifier", ""),
        uuid=attrs.get("uuid", ""),
        name=attrs.get("name", ""),
        node_id=attrs.get("node"),
        egg=egg,
        allocations=allocations,
        primary_ip=primary_ip,
        primary_port=primary_port,
        limit_memory=int(limits.get("memory") or 0),
        limit_disk=int(limits.get("disk") or 0),
        limit_cpu=int(limits.get("cpu") or 0),
        suspended=bool(attrs.get("suspended", False)),
    )
