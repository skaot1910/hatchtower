"""Adapter for the Pterodactyl and Pelican API (same API; Calagopus: calagopus.py).

All access to the panel goes through this class or a subclass. In read-only mode, non-reading HTTP
methods are blocked before a request reaches the live system. Unknown fields in responses are ignored so
panel updates don't break the manager.

Keys: the Application key ("app") is only ever used for reading. The client key ("client") controls the
servers. The optional admin key ("admin", Application key with "Read & Write") is used only for server
administration: creating servers, details, limits, startup, ports, reinstall, suspend, delete.
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


class AdminKeyMissing(PteroError):
    """Server administration was requested, but no admin key (write access) is stored."""


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


OWN_LIMIT_ROUTES = ("/websocket",)


class PteroClient:
    # Last rate limit reported by the panel for the client key: (limit per minute, remaining, time.monotonic()).
    # It applies per API key across all instances using that key; monitoring, backups and the file index pace
    # themselves by it. Answers to the application or admin key have their own limit and are kept apart.
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
        token = {"app": self.settings.app_key, "client": self.settings.client_key,
                 "admin": self.settings.admin_key}.get(key, "")
        if not token:
            if key == "admin":
                raise AdminKeyMissing(t("Für diese Funktion braucht Hatchtower einen Admin-Key mit Schreibrechten."))
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
            # Some routes have a limit of their own (Pterodactyl and Pelican: 5 per minute for /websocket);
            # it says nothing about the general limit the rest of Hatchtower shares
            if (limit.isdigit() and remaining.isdigit() and token == self.settings.client_key
                    and not path.endswith(OWN_LIMIT_ROUTES)):
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

    # -- Server administration (optional admin key, read & write) --------
    #
    # Everything below uses the admin key: a second Application key with "Read & Write". Without it,
    # AdminKeyMissing is raised and the UI explains which key is needed. The read-only mode still blocks
    # every write (_guard). Lists for the forms (users, eggs, nodes, ports) are cached briefly because the
    # panel's rate limit applies per key.

    LIMIT_FIELDS: tuple[str, ...] = ("memory", "swap", "disk", "io", "cpu", "threads")
    FEATURE_FIELDS: tuple[str, ...] = ("databases", "allocations", "backups")
    HAS_OOM = True
    ADMIN_CACHE_SECONDS = 300

    def _admin_cache(self) -> dict:
        cache = self.__dict__.get("_admin_cache_data")
        if cache is None:
            cache = self.__dict__["_admin_cache_data"] = {}
        return cache

    async def _cached(self, name: str, loader, seconds: float | None = None):
        cache = self._admin_cache()
        hit = cache.get(name)
        if hit and time.monotonic() - hit[0] < (seconds or self.ADMIN_CACHE_SECONDS):
            return hit[1]
        value = await loader()
        cache[name] = (time.monotonic(), value)
        return value

    def forget_admin_cache(self, *names: str) -> None:
        """Drop cached lists (all, or the given names and their sub-entries such as "allocs:<node>")."""
        cache = self._admin_cache()
        if not names:
            cache.clear()
        for name in names:
            for key in [k for k in cache if k == name or k.startswith(name + ":")]:
                cache.pop(key, None)

    async def _admin(self, method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
        return await self._request(method, path, key="admin", json_body=body, params=params)

    async def _admin_pages(self, path: str, params: dict | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, 51):
            data = await self._admin("GET", path, params={**(params or {}), "per_page": 100, "page": page})
            items += [i.get("attributes", {}) for i in (data or {}).get("data", [])]
            pagination = ((data or {}).get("meta") or {}).get("pagination") or {}
            if page >= int(pagination.get("total_pages") or 1):
                break
        return items

    def admin_ref(self, row) -> str:
        """How the admin API addresses a server (Pterodactyl/Pelican: numeric id)."""
        return str(row["ptero_id"])

    async def admin_server(self, ref: str) -> dict[str, Any]:
        data = await self._admin("GET", f"/api/application/servers/{ref}",
                                 params={"include": "allocations,variables,user"})
        return _admin_server((data or {}).get("attributes") or {})

    async def admin_users(self) -> list[dict[str, str]]:
        async def load():
            users = [{"id": str(u.get("id")), "name": u.get("username") or "", "email": u.get("email") or ""}
                     for u in await self._admin_pages("/api/application/users")]
            return sorted(users, key=lambda u: u["name"].lower())
        return await self._cached("users", load)

    async def admin_nodes(self) -> list[dict[str, str]]:
        async def load():
            return [{"id": str(n.get("id")), "name": n.get("name") or n.get("fqdn") or ""}
                    for n in await self._admin_pages("/api/application/nodes")]
        return await self._cached("nodes", load)

    async def admin_eggs(self) -> list[dict[str, str]]:
        async def load():
            eggs = []
            if self.settings.panel_type == "pelican":     # Pelican has no nests
                eggs = [{"id": str(e.get("id")), "name": e.get("name") or "", "group": ""}
                        for e in await self._admin_pages("/api/application/eggs")]
            else:
                for nest in await self._admin_pages("/api/application/nests"):
                    eggs += [{"id": str(e.get("id")), "name": e.get("name") or "", "group": nest.get("name") or "",
                              "nest": str(nest.get("id"))}
                             for e in await self._admin_pages(f"/api/application/nests/{nest.get('id')}/eggs")]
            return sorted(eggs, key=lambda e: (e["group"].lower(), e["name"].lower()))
        return await self._cached("eggs", load)

    async def admin_egg(self, egg_id: str) -> dict[str, Any]:
        async def load():
            if self.settings.panel_type == "pelican":
                path = f"/api/application/eggs/{egg_id}"
            else:
                egg = next((e for e in await self.admin_eggs() if e["id"] == str(egg_id)), None)
                if egg is None:
                    raise PteroError(t("Dieses Egg gibt es im Panel nicht."), 404)
                path = f"/api/application/nests/{egg['nest']}/eggs/{egg_id}"
            data = await self._admin("GET", path, params={"include": "variables"})
            return _admin_egg((data or {}).get("attributes") or {})
        return await self._cached(f"egg:{egg_id}", load)

    async def node_allocations(self, node_id: str) -> list[dict[str, Any]]:
        async def load():
            allocs = [{"id": str(a.get("id")), "ip": a.get("ip") or "", "alias": a.get("alias") or "",
                       "port": int(a.get("port") or 0), "assigned": bool(a.get("assigned")), "notes": a.get("notes") or ""}
                      for a in await self._admin_pages(f"/api/application/nodes/{node_id}/allocations")]
            return sorted(allocs, key=lambda a: (a["ip"], a["port"]))
        return await self._cached(f"allocs:{node_id}", load, 60)

    async def create_allocations(self, node_id: str, ip: str, alias: str, ports: list[str]) -> None:
        """Create ports on a node; ports are single ports or ranges like "27015-27020"."""
        await self._admin("POST", f"/api/application/nodes/{node_id}/allocations",
                          {"ip": ip, "alias": alias or None, "ports": ports})
        self.forget_admin_cache(f"allocs:{node_id}")

    async def delete_allocation(self, node_id: str, alloc_id: str) -> None:
        await self._admin("DELETE", f"/api/application/nodes/{node_id}/allocations/{alloc_id}")
        self.forget_admin_cache(f"allocs:{node_id}")

    async def update_details(self, srv: dict, name: str, description: str, owner: str, external_id: str) -> None:
        await self._admin("PATCH", f"/api/application/servers/{srv['ref']}/details",
                          {"name": name, "user": int(owner), "description": description,
                           "external_id": external_id or None})

    async def update_limits(self, srv: dict, limits: dict, features: dict, oom_disabled: bool) -> None:
        await self._build(srv, limits=limits, features=features, oom_disabled=oom_disabled)

    async def update_allocations(self, srv: dict, primary: str, add: list[str], remove: list[str]) -> None:
        await self._build(srv, primary=primary, add=add, remove=remove)
        self.forget_admin_cache(f"allocs:{srv['node']}")

    async def _build(self, srv: dict, *, limits: dict | None = None, features: dict | None = None,
                     oom_disabled: bool | None = None, primary: str = "", add=(), remove=()) -> None:
        """Pterodactyl changes limits and ports in one request, so the current values are always sent along."""
        lim = {**srv["limits"], **(limits or {})}
        # Pterodactyl reads the limits at the top level, Pelican only inside "limits": send both
        values = {"memory": lim["memory"], "swap": lim["swap"], "disk": lim["disk"], "io": lim["io"], "cpu": lim["cpu"],
                  "threads": lim.get("threads") or None,
                  "oom_disabled": srv["oom_disabled"] if oom_disabled is None else oom_disabled}
        await self._admin("PATCH", f"/api/application/servers/{srv['ref']}/build", {
            "allocation": int(primary or srv["primary"]), **values, "limits": values,
            "feature_limits": {**srv["feature_limits"], **(features or {})},
            "add_allocations": [int(a) for a in add], "remove_allocations": [int(a) for a in remove],
        })

    async def update_startup(self, srv: dict, startup: str, image: str, environment: dict[str, str],
                             egg: str = "") -> None:
        await self._admin("PATCH", f"/api/application/servers/{srv['ref']}/startup", {
            "startup": startup, "image": image, "egg": int(egg or srv["egg"]), "environment": environment,
            "skip_scripts": srv.get("skip_scripts", False)})

    async def reinstall(self, srv: dict) -> None:
        await self._admin("POST", f"/api/application/servers/{srv['ref']}/reinstall")

    async def set_suspended(self, srv: dict, suspended: bool) -> None:
        await self._admin("POST", f"/api/application/servers/{srv['ref']}/{'suspend' if suspended else 'unsuspend'}")

    async def delete_server(self, srv: dict, force: bool = False) -> None:
        await self._admin("DELETE", f"/api/application/servers/{srv['ref']}" + ("/force" if force else ""))
        self.forget_admin_cache("allocs")

    async def create_server(self, spec: dict) -> str:
        """Create a server. spec: name, description, owner, node, egg, image, startup, environment, limits,
        features, oom_disabled, allocation, additional, start. Returns the new server's identifier."""
        limits = spec["limits"]
        data = await self._admin("POST", "/api/application/servers", {
            "name": spec["name"], "description": spec.get("description") or "", "user": int(spec["owner"]),
            "egg": int(spec["egg"]), "docker_image": spec["image"], "image": spec["image"], "startup": spec["startup"],
            "environment": spec["environment"],
            "limits": {"memory": limits["memory"], "swap": limits["swap"], "disk": limits["disk"], "io": limits["io"],
                       "cpu": limits["cpu"], "threads": limits.get("threads") or None,
                       "oom_disabled": spec.get("oom_disabled", True)},
            "oom_disabled": spec.get("oom_disabled", True),
            "feature_limits": spec["features"],
            "allocation": {"default": int(spec["allocation"]), "additional": [int(a) for a in spec.get("additional", [])]},
            "start_on_completion": bool(spec.get("start")), "skip_scripts": bool(spec.get("skip_install")),
        })
        self.forget_admin_cache("allocs")
        return ((data or {}).get("attributes") or {}).get("identifier", "")

    async def admin_check(self) -> list[dict[str, Any]]:
        """Which administration rights the admin key has. Changes nothing: write rights are tested with
        deliberately empty requests (no right: HTTP 403, right present: validation error 422)."""
        results: list[dict[str, Any]] = []

        async def probe(label: str, need: str, coro, ok_status: tuple[int, ...] = ()) -> Any:
            try:
                value = await coro
            except PteroError as exc:
                if exc.status in ok_status:
                    results.append({"label": label, "ok": True, "need": need})
                    return None
                if exc.status in (401, 403):
                    results.append({"label": label, "ok": False, "need": need, "status": exc.status})
                    return None
                raise
            results.append({"label": label, "ok": True, "need": need})
            return value

        rw = "Read & Write"
        servers = await probe("Server lesen", "Servers: Read", self._admin("GET", "/api/application/servers",
                                                                           params={"per_page": 1}))
        await probe("Benutzer lesen", "Users: Read", self.admin_users())
        await probe("Eggs lesen", "Eggs: Read" if self.settings.panel_type == "pelican" else "Nests, Eggs: Read",
                    self.admin_eggs())
        nodes = await probe("Nodes lesen", "Nodes: Read", self.admin_nodes()) or []
        if nodes:
            await probe("Ports lesen", "Allocations: Read", self.node_allocations(nodes[0]["id"]))
            await probe("Ports anlegen", f"Allocations: {rw}",
                        self._admin("POST", f"/api/application/nodes/{nodes[0]['id']}/allocations", {}), (422,))
        first = ((servers or {}).get("data") or [{}])[0].get("attributes", {}).get("id")
        if first:
            await probe("Server ändern", f"Servers: {rw}",
                        self._admin("PATCH", f"/api/application/servers/{first}/details", {}), (422,))
        else:
            await probe("Server anlegen", f"Servers: {rw}", self._admin("POST", "/api/application/servers", {}), (422,))
        return results

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


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rules(value: Any) -> str:
    """Validation rules of a variable as one string ("required|string|max:20")."""
    return "|".join(str(r) for r in value) if isinstance(value, list) else str(value or "")


def _variable(v: dict[str, Any], value: Any = None) -> dict[str, Any]:
    default = v.get("default_value")
    if value is None:
        value = default
    return {"env": v.get("env_variable") or "", "name": v.get("name") or v.get("env_variable") or "",
            "description": v.get("description") or "", "value": "" if value is None else str(value),
            "default": "" if default is None else str(default), "rules": _rules(v.get("rules")),
            "editable": bool(v.get("user_editable", v.get("is_editable", True)))}


def _admin_server(a: dict[str, Any]) -> dict[str, Any]:
    """A server from the Application API in Hatchtower's panel-neutral form."""
    rel = a.get("relationships") or {}
    container = a.get("container") or {}
    env = container.get("environment") or {}
    limits = a.get("limits") or {}
    feats = a.get("feature_limits") or {}
    variables = []
    for item in (rel.get("variables") or {}).get("data") or []:
        v = item.get("attributes") or {}
        if v.get("env_variable"):
            value = v.get("server_value")
            variables.append(_variable(v, env.get(v["env_variable"]) if value is None else value))
    allocations = []
    for item in (rel.get("allocations") or {}).get("data") or []:
        al = item.get("attributes") or {}
        allocations.append({"id": str(al.get("id")), "ip": al.get("ip") or "", "alias": al.get("alias") or "",
                            "port": _num(al.get("port")), "notes": al.get("notes") or "",
                            "primary": al.get("id") == a.get("allocation")})
    user = (rel.get("user") or {}).get("attributes") or {}
    return {
        "ref": str(a.get("id")), "identifier": a.get("identifier") or "", "name": a.get("name") or "",
        "description": a.get("description") or "", "external_id": a.get("external_id") or "",
        "owner": str(a.get("user") or ""), "owner_name": user.get("username") or "",
        "node": str(a.get("node") or ""), "egg": str(a.get("egg") or ""),
        "suspended": bool(a.get("suspended")), "status": a.get("status") or "",
        "limits": {"memory": _num(limits.get("memory")), "swap": _num(limits.get("swap")),
                   "disk": _num(limits.get("disk")), "io": _num(limits.get("io"), 500), "cpu": _num(limits.get("cpu")),
                   "threads": str(limits.get("threads") or "")},
        "oom_disabled": bool(limits.get("oom_disabled", True)),
        "feature_limits": {k: _num(feats.get(k)) for k in ("databases", "allocations", "backups")},
        "startup": container.get("startup_command") or "", "image": container.get("image") or "",
        "skip_scripts": bool(container.get("skip_scripts")),
        "variables": variables, "allocations": sorted(allocations, key=lambda x: (not x["primary"], x["port"])),
        "primary": str(a.get("allocation") or ""),
    }


def _admin_egg(a: dict[str, Any]) -> dict[str, Any]:
    images = a.get("docker_images") or {}
    if isinstance(images, list):
        images = {i: i for i in images}
    if not images and a.get("docker_image"):
        images = {a["docker_image"]: a["docker_image"]}
    variables = [_variable(i.get("attributes") or {})
                 for i in ((a.get("relationships") or {}).get("variables") or {}).get("data") or []]
    return {"id": str(a.get("id")), "name": a.get("name") or "", "startup": a.get("startup") or "",
            "images": dict(images), "variables": [v for v in variables if v["env"]]}


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
