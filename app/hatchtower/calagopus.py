"""Adapter für die API von Calagopus.

Calagopus ist eine Neuentwicklung mit eigener API: Admin-Daten unter /api/admin statt
/api/application, Objekte über UUIDs, Antworten ohne „attributes“-Hülle und nur eine Art
API-Key (mit Admin- und Server-Rechten). Diese Klasse bildet alles auf die Schnittstelle von
PteroClient ab, damit der Rest von Hatchtower nichts vom Panel wissen muss. Server werden über
die kurze UUID (uuid_short) angesprochen, die dem „identifier“ von Pterodactyl entspricht.

Die Regeln aus PteroClient gelten unverändert: Im Read-Only-Modus wird nichts geschrieben, Admin-
Daten werden nur gelesen, signierte Wings-URLs müssen auf einen bekannten Node zeigen. Wings ist bei
Calagopus meist über den Proxy des Panels erreichbar (public_url des Nodes).
"""
from __future__ import annotations

import posixpath
from typing import Any
from urllib.parse import urlsplit

from .ptero import PteroClient, PteroError, ServerInfo

PER_PAGE = 100
MAX_PAGES = 50


class CalagopusClient(PteroClient):

    async def _pages(self, path: str, field: str, *, key: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Alle Seiten einer Liste ({field: {total, data}}) lesen."""
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            data = await self._get(path, key=key, params={**(params or {}), "page": page, "per_page": PER_PAGE})
            block = (data or {}).get(field) or {}
            chunk = block.get("data") or []
            items.extend(chunk)
            if len(chunk) < PER_PAGE or len(items) >= int(block.get("total") or 0):
                break
        return items

    # -- Admin-API (nur lesen) ------------------------------------------

    async def list_nodes(self) -> list[dict[str, Any]]:
        nodes = []
        for n in await self._pages("/api/admin/nodes", "nodes", key="app"):
            # public_url zeigt bei aktiviertem Wings-Proxy auf das Panel, sonst direkt auf Wings
            parts = urlsplit(n.get("public_url") or n.get("url") or "")
            host = parts.hostname or ""
            nodes.append({
                "id": n.get("uuid"), "name": n.get("name"), "fqdn": host, "scheme": parts.scheme or "http",
                "daemon_listen": parts.port or (443 if parts.scheme == "https" else 80),
                "maintenance_mode": bool(n.get("maintenance_enabled")),
            })
            if host:
                self._wings_hosts.add(host)
        return nodes

    async def list_servers(self) -> list[ServerInfo]:
        return [_parse_server(s) for s in await self._pages("/api/admin/servers", "servers", key="app")]

    # -- Server (lesen) -------------------------------------------------

    async def resources(self, identifier: str) -> dict[str, Any]:
        data = await self._get(f"/api/client/servers/{identifier}/resources", key="client")
        res = (data or {}).get("resources") or {}
        net = res.get("network") or {}
        return {
            "state": res.get("state") or "unknown",
            "suspended": False,
            "cpu": float(res.get("cpu_absolute") or 0.0),
            "memory_bytes": int(res.get("memory_bytes") or 0),
            "disk_bytes": int(res.get("disk_bytes") or 0),
            "uptime_ms": int(res.get("uptime") or 0),
            "rx_bytes": int(net.get("rx_bytes") or 0),
            "tx_bytes": int(net.get("tx_bytes") or 0),
        }

    async def startup_variables(self, identifier: str) -> dict[str, str]:
        data = await self._get(f"/api/client/servers/{identifier}/startup/variables", key="client")
        result: dict[str, str] = {}
        for v in (data or {}).get("variables") or []:
            env = v.get("env_variable")
            if env:
                value = v.get("value")
                if value is None:
                    value = v.get("default_value")
                result[env] = "" if value is None else str(value)
        return result

    async def list_files(self, identifier: str, directory: str) -> list[dict[str, Any]]:
        entries = await self._pages(f"/api/client/servers/{identifier}/files/list", "entries", key="client",
                                    params={"directory": directory, "sort": "name_asc"})
        files = [{
            "name": e.get("name", ""),
            "is_file": not e.get("directory", False),
            "size": int(e.get("size") or 0),
            "modified_at": e.get("modified"),
            "mimetype": e.get("mime", ""),
        } for e in entries]
        files.sort(key=lambda f: (f["is_file"], f["name"].lower()))
        return files

    async def download_url(self, identifier: str, file: str) -> str:
        root, name = posixpath.split(file)
        data = await self._get(f"/api/client/servers/{identifier}/files/download", key="client",
                               params={"root": root or "/", "files": name, "directory": "false",
                                       "archive_format": "tar_gz"})
        return (data or {}).get("url", "")

    async def websocket_credentials(self, identifier: str) -> tuple[str, str]:
        data = await self._get(f"/api/client/servers/{identifier}/websocket", key="client")
        token, socket = (data or {}).get("token"), (data or {}).get("url")
        if not token or not socket:
            raise PteroError("Keine Websocket-Zugangsdaten erhalten")
        return token, socket

    async def activity(self, identifier: str, limit: int = 25) -> list[dict[str, Any]]:
        """Aktivität im Format von Pterodactyl (event, timestamp, properties)."""
        data = await self._get(f"/api/client/servers/{identifier}/activity", key="client",
                               params={"page": 1, "per_page": limit})
        events = []
        for a in ((data or {}).get("activities") or {}).get("data") or []:
            props = a.get("data") if isinstance(a.get("data"), dict) else {}
            event = str(a.get("event", ""))
            # Calagopus meldet alle Power-Aktionen als server:power.action mit {"action": ...}
            if event == "server:power.action" and props.get("action"):
                event = f"server:power.{props['action']}"
            if a.get("is_schedule") and not event.startswith("server:schedule."):
                event = "server:schedule." + event.removeprefix("server:")
            events.append({"event": event, "timestamp": a.get("created"), "properties": props})
        return events

    async def client_server_count(self) -> int:
        data = await self._get("/api/client/servers", key="client", params={"page": 1, "per_page": 1, "other": "true"})
        own = await self._get("/api/client/servers", key="client", params={"page": 1, "per_page": 1})
        return sum(int(((d or {}).get("servers") or {}).get("total") or 0) for d in (data, own))

    # -- Server (schreibend) --------------------------------------------

    async def power(self, identifier: str, signal: str) -> None:
        if signal not in ("start", "stop", "restart", "kill"):
            raise PteroError("Unbekanntes Signal")
        await self._request("POST", f"/api/client/servers/{identifier}/power", key="client",
                            json_body={"action": signal})

    async def create_folder(self, identifier: str, root: str, name: str) -> None:
        await self._request("POST", f"/api/client/servers/{identifier}/files/create-directory", key="client",
                            json_body={"root": root, "name": name})

    async def _upload_url(self, identifier: str) -> str:
        data = await self._get(f"/api/client/servers/{identifier}/files/upload", key="client")
        return (data or {}).get("url", "")


def _parse_server(s: dict[str, Any]) -> ServerInfo:
    alloc = s.get("allocation") or {}
    ip = alloc.get("ip_alias") or alloc.get("ip")
    port = alloc.get("port")
    limits = s.get("limits") or {}
    return ServerInfo(
        ptero_id=0,
        identifier=s.get("uuid_short", ""),
        uuid=s.get("uuid", ""),
        name=s.get("name", ""),
        node_id=None,
        egg=(s.get("egg") or {}).get("name", ""),
        allocations=[{"id": alloc.get("uuid"), "ip": ip, "port": port}] if alloc else [],
        primary_ip=ip,
        primary_port=port,
        limit_memory=int(limits.get("memory") or 0),
        limit_disk=int(limits.get("disk") or 0),
        limit_cpu=int(limits.get("cpu") or 0),
        suspended=bool(s.get("is_suspended", False)),
    )
