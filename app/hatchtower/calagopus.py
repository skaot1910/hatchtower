"""Adapter für die API von Calagopus.

Calagopus ist eine Neuentwicklung mit eigener API: Admin-Daten unter /api/admin statt
/api/application, Objekte über UUIDs, Antworten ohne „attributes“-Hülle und nur eine Art
API-Key (mit Admin- und Server-Rechten). Diese Klasse bildet alles auf die Schnittstelle von
PteroClient ab, damit der Rest von Hatchtower nichts vom Panel wissen muss. Server werden über
die kurze UUID (uuid_short) angesprochen, die dem „identifier“ von Pterodactyl entspricht.

Die Regeln aus PteroClient gelten unverändert: Im Read-Only-Modus wird nichts geschrieben, signierte
Wings-URLs müssen auf einen bekannten Node zeigen. Wings ist bei Calagopus meist über den Proxy des Panels
erreichbar (public_url des Nodes). Admin data is only read with the main key; writing admin data (server
administration) needs the optional second key with admin write rights (key "admin").
"""
from __future__ import annotations

import posixpath
from typing import Any
from urllib.parse import urlsplit

from .i18n import t
from .ptero import PteroClient, PteroError, ServerInfo, _num, _variable

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

    # Calagopus can stream a backup as ZIP (opens on every computer without extra software), but only "streaming"
    # backups; the others come as they are stored (usually tar.gz). The download names the real format.
    BACKUP_FORMAT = ("tar.gz", "application/gzip")
    BACKUP_SIZE_IS_ARCHIVE = False

    async def list_backups(self, identifier: str) -> list[dict[str, Any]]:
        backups = [{"uuid": b.get("uuid", ""), "name": b.get("name") or "", "bytes": int(b.get("bytes") or 0),
                    "successful": bool(b.get("is_successful")), "locked": bool(b.get("is_locked")),
                    "created": b.get("created"), "completed": b.get("completed"),
                    "streaming": bool(b.get("is_streaming"))}
                   for b in await self._pages(f"/api/client/servers/{identifier}/backups", "backups", key="client",
                                              params={"kind": "server"})]
        backups.sort(key=lambda b: b["created"] or "", reverse=True)
        return backups

    async def backup_download_url(self, identifier: str, uuid: str, streaming: bool = False) -> str:
        data = await self._get(f"/api/client/servers/{identifier}/backups/{uuid}/download", key="client",
                               params={"archive_format": "zip" if streaming else "tar_gz"})
        return (data or {}).get("url", "")

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

    # -- Server administration (optional admin key) ---------------------
    #
    # Same interface as PteroClient. Calagopus addresses servers, users, eggs and ports by UUID and changes
    # a server with a single PATCH; ports and variables have their own endpoints.

    LIMIT_FIELDS = ("memory", "swap", "disk", "io", "cpu")
    FEATURE_FIELDS = ("databases", "allocations", "backups", "schedules")
    HAS_OOM = False

    async def _admin_list(self, path: str, field: str) -> list[dict[str, Any]]:
        return await self._pages(path, field, key="admin")

    def admin_ref(self, row) -> str:
        return str(row["uuid"])

    async def admin_server(self, ref: str) -> dict[str, Any]:
        s = ((await self._admin("GET", f"/api/admin/servers/{ref}")) or {}).get("server") or {}
        allocs = await self._admin_list(f"/api/admin/servers/{ref}/allocations", "allocations")
        variables = ((await self._admin("GET", f"/api/admin/servers/{ref}/variables")) or {}).get("variables") or []
        limits = s.get("limits") or {}
        feats = s.get("feature_limits") or {}
        primary = (s.get("allocation") or {}).get("uuid") or ""
        return {
            "ref": s.get("uuid") or ref, "identifier": s.get("uuid_short") or "", "name": s.get("name") or "",
            "description": s.get("description") or "", "external_id": s.get("external_id") or "",
            "owner": (s.get("owner") or {}).get("uuid") or "", "owner_name": (s.get("owner") or {}).get("username") or "",
            "node": (s.get("node") or {}).get("uuid") or "", "egg": (s.get("egg") or {}).get("uuid") or "",
            "nest": (s.get("nest") or {}).get("uuid") or "",
            "suspended": bool(s.get("is_suspended")), "status": s.get("status") or "",
            "limits": {"memory": _num(limits.get("memory")), "swap": _num(limits.get("swap")),
                       "disk": _num(limits.get("disk")), "io": _num(limits.get("io_weight"), 0),
                       "cpu": _num(limits.get("cpu"))},
            "raw_limits": limits, "oom_disabled": True,
            "feature_limits": {k: _num(feats.get(k)) for k in self.FEATURE_FIELDS},
            "startup": s.get("startup") or "", "image": s.get("image") or "",
            "variables": [_variable(v, v.get("value")) for v in variables if v.get("env_variable")],
            "allocations": sorted(({"id": a.get("uuid") or "", "ip": a.get("ip") or "", "alias": a.get("ip_alias") or "",
                                    "port": _num(a.get("port")), "notes": a.get("notes") or "",
                                    "primary": a.get("uuid") == primary} for a in allocs),
                                  key=lambda x: (not x["primary"], x["port"])),
            "primary": primary,
        }

    async def admin_users(self) -> list[dict[str, str]]:
        async def load():
            users = [{"id": u.get("uuid") or "", "name": u.get("username") or "", "email": u.get("email") or ""}
                     for u in await self._admin_list("/api/admin/users", "users")]
            return sorted(users, key=lambda u: u["name"].lower())
        return await self._cached("users", load)

    async def admin_nodes(self) -> list[dict[str, str]]:
        async def load():
            return [{"id": n.get("uuid") or "", "name": n.get("name") or ""}
                    for n in await self._admin_list("/api/admin/nodes", "nodes")]
        return await self._cached("nodes", load)

    async def admin_eggs(self) -> list[dict[str, str]]:
        async def load():
            data = (await self._admin("GET", "/api/admin/nests/eggs")) or {}
            eggs = [{"id": e.get("uuid") or "", "name": e.get("name") or "", "group": (g.get("nest") or {}).get("name") or "",
                     "nest": (g.get("nest") or {}).get("uuid") or ""}
                    for g in data.get("nests") or [] for e in g.get("eggs") or []]
            return sorted(eggs, key=lambda e: (e["group"].lower(), e["name"].lower()))
        return await self._cached("eggs", load)

    async def admin_egg(self, egg_id: str) -> dict[str, Any]:
        async def load():
            egg = next((e for e in await self.admin_eggs() if e["id"] == egg_id), None)
            if egg is None:
                raise PteroError(t("Dieses Egg gibt es im Panel nicht."), 404)
            base = f"/api/admin/nests/{egg['nest']}/eggs/{egg_id}"
            e = ((await self._admin("GET", base)) or {}).get("egg") or {}
            variables = ((await self._admin("GET", f"{base}/variables")) or {}).get("variables") or []
            commands = e.get("startup_commands") or {}
            return {"id": egg_id, "name": e.get("name") or "", "startup": next(iter(commands.values()), ""),
                    "images": dict(e.get("docker_images") or {}),
                    "variables": [_variable(v) for v in variables if v.get("env_variable")]}
        return await self._cached(f"egg:{egg_id}", load)

    async def node_allocations(self, node_id: str) -> list[dict[str, Any]]:
        async def load():
            allocs = [{"id": a.get("uuid") or "", "ip": a.get("ip") or "", "alias": a.get("ip_alias") or "",
                       "port": _num(a.get("port")), "assigned": a.get("server") is not None, "notes": ""}
                      for a in await self._admin_list(f"/api/admin/nodes/{node_id}/allocations", "allocations")]
            return sorted(allocs, key=lambda a: (a["ip"], a["port"]))
        return await self._cached(f"allocs:{node_id}", load, 60)

    async def create_allocations(self, node_id: str, ip: str, alias: str, ports: list[str]) -> None:
        numbers: list[int] = []
        for part in ports:
            low, _, high = part.partition("-")
            numbers += list(range(int(low), int(high or low) + 1))
        await self._admin("POST", f"/api/admin/nodes/{node_id}/allocations",
                          {"ip": ip, "ip_alias": alias or None, "ports": numbers})
        self.forget_admin_cache(f"allocs:{node_id}")

    async def delete_allocation(self, node_id: str, alloc_id: str) -> None:
        await self._admin("DELETE", f"/api/admin/nodes/{node_id}/allocations",
                          {"selector": {"type": "uuids", "uuids": [alloc_id]}, "force": False})
        self.forget_admin_cache(f"allocs:{node_id}")

    async def update_details(self, srv: dict, name: str, description: str, owner: str, external_id: str) -> None:
        await self._admin("PATCH", f"/api/admin/servers/{srv['ref']}",
                          {"name": name, "description": description or None, "owner_uuid": owner,
                           "external_id": external_id or None})

    async def update_limits(self, srv: dict, limits: dict, features: dict, oom_disabled: bool) -> None:
        raw = dict(srv.get("raw_limits") or {})
        raw.update({"memory": limits["memory"], "swap": limits["swap"], "disk": limits["disk"], "cpu": limits["cpu"],
                    "io_weight": limits["io"] or None})
        await self._admin("PATCH", f"/api/admin/servers/{srv['ref']}",
                          {"limits": raw, "feature_limits": {**srv["feature_limits"], **features}})

    async def update_allocations(self, srv: dict, primary: str, add: list[str], remove: list[str]) -> None:
        base = f"/api/admin/servers/{srv['ref']}/allocations"
        for alloc in add:
            await self._admin("POST", base, {"allocation_uuid": alloc})
        if primary and primary != srv["primary"]:
            await self._admin("PATCH", f"{base}/{primary}", {"primary": True})
        for alloc in remove:
            await self._admin("DELETE", f"{base}/{alloc}")
        self.forget_admin_cache(f"allocs:{srv['node']}")

    async def update_startup(self, srv: dict, startup: str, image: str, environment: dict[str, str],
                             egg: str = "") -> None:
        body: dict[str, Any] = {"startup": startup, "image": image}
        if egg and egg != srv["egg"]:
            body["egg_uuid"] = egg
        await self._admin("PATCH", f"/api/admin/servers/{srv['ref']}", body)
        await self._admin("PUT", f"/api/admin/servers/{srv['ref']}/variables",
                          {"variables": [{"env_variable": k, "value": v} for k, v in environment.items()]})

    async def reinstall(self, srv: dict) -> None:
        await self._admin("POST", f"/api/client/servers/{srv['identifier']}/settings/install",
                          {"truncate_directory": False})

    async def set_suspended(self, srv: dict, suspended: bool) -> None:
        await self._admin("PATCH", f"/api/admin/servers/{srv['ref']}", {"suspended": suspended})

    async def delete_server(self, srv: dict, force: bool = False) -> None:
        await self._admin("DELETE", f"/api/admin/servers/{srv['ref']}", {"force": force, "delete_backups": False})
        self.forget_admin_cache("allocs")

    async def create_server(self, spec: dict) -> str:
        limits = spec["limits"]
        data = await self._admin("POST", "/api/admin/servers", {
            "node_uuid": spec["node"], "owner_uuid": spec["owner"], "egg_uuid": spec["egg"],
            "allocation_uuid": spec["allocation"], "allocation_uuids": list(spec.get("additional", [])),
            "start_on_completion": bool(spec.get("start")), "skip_installer": bool(spec.get("skip_install")),
            "name": spec["name"], "description": spec.get("description") or None, "external_id": None,
            "limits": {"cpu": limits["cpu"], "memory": limits["memory"], "memory_overhead": 0, "swap": limits["swap"],
                       "disk": limits["disk"], "io_weight": limits.get("io") or None},
            "pinned_cpus": [], "startup": spec["startup"], "image": spec["image"],
            "hugepages_passthrough_enabled": False, "kvm_passthrough_enabled": False,
            "feature_limits": {k: spec["features"].get(k, 0) for k in self.FEATURE_FIELDS},
            "variables": [{"env_variable": k, "value": v} for k, v in spec["environment"].items()],
        })
        self.forget_admin_cache("allocs")
        return ((data or {}).get("server") or {}).get("uuid_short", "")

    async def admin_check(self) -> list[dict[str, Any]]:
        """Like PteroClient.admin_check. Calagopus validates requests before checking rights, so only
        "change server" can be tested without effect (an empty PATCH changes nothing); creating servers
        and ports is listed as "not testable"."""
        results: list[dict[str, Any]] = []

        async def probe(label: str, need: str, coro) -> Any:
            try:
                value = await coro
            except PteroError as exc:
                if exc.status in (401, 403):
                    results.append({"label": label, "ok": False, "need": need, "status": exc.status})
                    return None
                raise
            results.append({"label": label, "ok": True, "need": need})
            return value

        servers = await probe("Server lesen", "servers.read", self._admin_list("/api/admin/servers", "servers"))
        await probe("Benutzer lesen", "users.read", self.admin_users())
        await probe("Eggs lesen", "nests.read, eggs.read", self.admin_eggs())
        nodes = await probe("Nodes lesen", "nodes.read", self.admin_nodes()) or []
        if nodes:
            await probe("Ports lesen", "nodes.allocations", self.node_allocations(nodes[0]["id"]))
        if servers:
            await probe("Server ändern", "servers.update", self._admin("PATCH", f"/api/admin/servers/{servers[0]['uuid']}", {}))
        results.append({"label": "Server anlegen und Ports anlegen", "ok": None,
                        "need": "servers.create, servers.delete, servers.allocations, servers.variables, nodes.allocations"})
        return results


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
