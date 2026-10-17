"""Server administration through the panel API (optional admin key with write access).

Create servers, change details, resources, ports and startup parameters, reinstall, suspend and delete
servers, and manage the ports of the nodes. All of this needs the admin key. Without it the pages are
still shown, with the forms locked and a hint how to add the key. Writing needs the read-write mode and
the matching right; creating, deleting and suspending servers, changing owner or egg and the ports of the
nodes are reserved for owners.

Imported at the end of main.py and uses its helpers.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import json
import re

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import audit, db, i18n, main, setup
from .main import (app, back, check_csrf, current_user, deny, get_server_or_deny, ip_of, render, require,
                   require_owner, settings, vault)
from .ptero import AdminKeyMissing, PteroError, ReadOnlyViolation
from .views import fmt_dt

ADMIN_CHECK_KEY = "admin_key_check"
MAX_NEW_PORTS = 1000


# -- Availability and helpers ------------------------------------------------------------------

def unavailable() -> str | None:
    """Why server administration can't be used right now: "key" (no admin key), "read_only"; None = usable."""
    if not settings.admin_key:
        return "key"
    if settings.read_only:
        return "read_only"
    return None


def may(user, ident: str, key: str) -> bool:
    """Has the right, ignoring the read-only mode (decides what is shown; writing still checks user.can)."""
    return user.is_owner or key in user.grants.get(ident, set())


def client():
    return app.state.ptero


def server_ref(ident: str) -> str | None:
    with db.connect() as con:
        row = con.execute("SELECT ptero_id, uuid FROM servers WHERE identifier = ? AND present = 1",
                          (ident,)).fetchone()
    return client().admin_ref(row) if row else None


def guard_write() -> None:
    if settings.read_only:
        raise ReadOnlyViolation("Server-Administration ist im Read-Only-Modus gesperrt")


def last_admin_check() -> dict | None:
    try:
        value = json.loads(db.get_setting(ADMIN_CHECK_KEY) or "null")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def diff(before: dict, after: dict) -> dict:
    """Changed fields for the audit log: {field: [before, after]}."""
    return {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}


async def run_admin(request: Request, user, action: str, target: str, ok: str, detail: dict, coro,
                    ident: str | None = None, done: str | None = None) -> RedirectResponse:
    """Run a write to the panel, write the audit log and come back with a message (on success to done)."""
    try:
        await coro
    except AdminKeyMissing:
        return back(target, "Für diese Funktion braucht Hatchtower einen Admin-Key mit Schreibrechten.")
    except PteroError as exc:
        audit.record(action, result="error", user_id=user.id, username=user.username, ip=ip_of(request),
                     server=ident, detail={**detail, "fehler": str(exc)[:300]})
        return back(target, i18n.t("Das Panel hat die Änderung abgelehnt: {grund}", grund=str(exc)))
    audit.record(action, user_id=user.id, username=user.username, ip=ip_of(request), server=ident, detail=detail)
    app.state.monitor.request_sync()
    return back(done or target, ok)


def number(form, name: str, low: int, high: int, default: int = 0) -> int:
    raw = str(form.get(name, "")).strip()
    if raw == "":
        return default
    if not re.fullmatch(r"-?\d{1,9}", raw) or not low <= int(raw) <= high:
        raise ValueError(i18n.t("„{feld}“ muss eine Zahl von {von} bis {bis} sein.", feld=i18n.t(FIELD_LABELS.get(name, name)),
                                von=low, bis=high))
    return int(raw)


FIELD_LABELS = {"memory": "RAM", "swap": "Swap", "disk": "Speicher", "io": "Block-IO-Gewicht", "cpu": "CPU",
                "threads": "CPU-Kerne", "databases": "Datenbanken", "allocations": "Ports", "backups": "Backups",
                "schedules": "Zeitpläne"}
LIMIT_RANGES = {"memory": (0, 10_000_000), "swap": (-1, 10_000_000), "disk": (0, 100_000_000), "io": (0, 1000),
                "cpu": (0, 100_000)}


def parse_limits(form, c) -> tuple[dict, dict, bool]:
    limits: dict = {}
    for name in c.LIMIT_FIELDS:
        if name == "threads":
            threads = str(form.get("threads", "")).strip()
            if threads and not re.fullmatch(r"\d{1,3}(-\d{1,3})?(,\d{1,3}(-\d{1,3})?)*", threads):
                raise ValueError(i18n.t("CPU-Kerne: z. B. 0-1 oder 0,2,4 – oder leer lassen."))
            limits["threads"] = threads
        else:
            low, high = LIMIT_RANGES[name]
            limits[name] = number(form, name, low, high)
    if "io" in limits and limits["io"] and not 10 <= limits["io"] <= 1000:
        raise ValueError(i18n.t("Das Block-IO-Gewicht muss zwischen 10 und 1000 liegen."))
    features = {name: number(form, name, 0, 1_000_000) for name in c.FEATURE_FIELDS}
    return limits, features, bool(form.get("oom_disabled"))


def parse_ports(text: str) -> list[str]:
    """"25565, 27015-27020" → ["25565", "27015-27020"] (ports 1024–65535, at most MAX_NEW_PORTS)."""
    parts, total = [], 0
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", part)
        if not m:
            raise ValueError(i18n.t("Ports: einzelne Ports oder Bereiche wie 27015-27020, durch Komma getrennt."))
        low, high = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1024 <= low <= high <= 65535:
            raise ValueError(i18n.t("Ports müssen zwischen 1024 und 65535 liegen."))
        total += high - low + 1
        parts.append(part if m.group(2) else str(low))
    if not parts:
        raise ValueError(i18n.t("Bitte mindestens einen Port angeben."))
    if total > MAX_NEW_PORTS:
        raise ValueError(i18n.t("Höchstens {n} Ports auf einmal.", n=MAX_NEW_PORTS))
    return parts


def environment_from(form, variables: list[dict]) -> dict[str, str]:
    env = {}
    for v in variables:
        value = str(form.get(f"var__{v['env']}", v["value"]))
        if "required" in v["rules"].split("|") and not value.strip():
            raise ValueError(i18n.t("„{name}“ darf nicht leer sein.", name=v["name"]))
        env[v["env"]] = value
    return env


def clean_text(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


# -- Admin key (System → Panel and setup wizard) ----------------------------------------------

async def test_admin_key(key: str) -> list[dict]:
    temp = dataclasses.replace(settings, admin_key=key)
    c = main.make_client(temp)
    try:
        return await c.admin_check()
    finally:
        await c.close()


def admin_back(target: str, message: str) -> RedirectResponse:
    if not target.startswith(("/setup", "/admin/connection")):
        target = "/admin/connection"
    return back(target + ("#admin-key" if target.startswith("/admin/connection") else ""), message)


@app.post("/admin/connection/admin-key")
async def admin_key_save(request: Request, admin_key: str = Form(""), next: str = Form("/admin/connection")):
    """Store the optional admin key after checking which rights it has (the check changes nothing)."""
    owner = require_owner(request)
    await check_csrf(request)
    key = admin_key.strip()
    if not settings.configured:
        return admin_back(next, "Bitte zuerst die Verbindung zum Panel speichern.")
    if not key:
        return admin_back(next, "Bitte den Admin-Key eintragen.")
    if key in (settings.app_key, settings.client_key):
        return admin_back(next, "Das ist einer der bereits gespeicherten Keys. Für die Server-Administration bitte einen "
                                "eigenen Key mit Schreibrechten anlegen.")
    problem = setup.key_problem(key, settings.panel.app_prefix)
    if problem:
        return admin_back(next, problem)
    try:
        results = await test_admin_key(key)
    except PteroError as exc:
        return admin_back(next, i18n.t("Der Admin-Key wurde abgelehnt: {grund}",
                                       grund=setup.explain(exc, "Admin-Key", settings.panel.name)))
    missing = [r for r in results if r["ok"] is False]
    db.set_setting(ADMIN_CHECK_KEY, json.dumps({"at": db.utcnow(), "results": results, "saved": not missing}), owner.id)
    audit.record("connection.admin_key", result="error" if missing else "ok", user_id=owner.id,
                 username=owner.username, ip=ip_of(request),
                 detail={"fehlende_rechte": [r["need"] for r in missing] or None, "gespeichert": not missing})
    if missing:
        return admin_back(next, i18n.t("Nicht gespeichert: Dem Key fehlen Rechte ({rechte}).",
                                       rechte=", ".join(r["need"] for r in missing)))
    setup.save_values({"admin_key": key}, vault, owner.id)
    settings.admin_key = key
    client().forget_admin_cache()
    if next.startswith("/setup"):
        return back("/setup?step=options", "Admin-Key gespeichert. Die Server-Administration ist eingeschaltet.")
    return admin_back(next, "Admin-Key gespeichert. Die Server-Administration ist eingeschaltet.")


@app.post("/admin/connection/admin-key/remove")
async def admin_key_remove(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    setup.save_values({"admin_key": ""}, vault, owner.id)
    settings.admin_key = ""
    db.set_setting(ADMIN_CHECK_KEY, "", owner.id)
    client().forget_admin_cache()
    audit.record("connection.admin_key", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"entfernt": True})
    return admin_back("/admin/connection", "Admin-Key entfernt. Lösche ihn auch im Panel, wenn du ihn nicht mehr brauchst.")


def admin_key_context() -> dict:
    check = last_admin_check()
    return {"admin_key_masked": setup.mask_key(settings.admin_key), "admin_check": check,
            "admin_check_at": fmt_dt(check.get("at"), settings.timezone) if check and check.get("at") else ""}


# -- Server: panel settings ---------------------------------------------------------------------

def admin_page_target(ident: str, anchor: str = "") -> str:
    return f"/servers/{ident}/admin" + (f"#{anchor}" if anchor else "")


async def load_admin_server(request: Request, user, ident: str):
    """Server (view), admin data from the panel (or None) and an error text."""
    server = get_server_or_deny(request, user, ident)
    ref = server_ref(ident)
    if ref is None:
        deny(request, user, "server.view", ident)
    try:
        return server, await client().admin_server(ref), ""
    except AdminKeyMissing:
        return server, None, ""
    except PteroError as exc:
        return server, None, str(exc)


@app.get("/servers/{ident}/admin", response_class=HTMLResponse)
async def server_admin(request: Request, ident: str, egg: str = ""):
    """Panel settings of a server (right "admin.view"). Every section is shown; sections without the matching
    right, without the admin key or in read-only mode stay locked."""
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "admin.view")
    rights = {key: may(user, ident, key) for key in ("admin.details", "admin.build", "admin.startup",
                                                      "admin.reinstall")}
    reason = unavailable()
    srv = egg_info = None
    users, eggs, free = [], [], []
    error = ""
    if settings.admin_key:
        c = client()
        try:
            ref = server_ref(ident)
            if ref is None:
                deny(request, user, "server.view", ident)
            srv = await c.admin_server(ref)
            if user.is_owner:
                users = await c.admin_users()
                eggs = await c.admin_eggs()
            egg_info = await c.admin_egg(egg if user.is_owner and egg else srv["egg"])
            if rights["admin.build"]:
                free = [a for a in await c.node_allocations(srv["node"]) if not a["assigned"]]
        except AdminKeyMissing:
            pass
        except PteroError as exc:
            error = str(exc)
    egg_change = bool(srv and egg_info and egg_info["id"] != srv["egg"])
    variables = []
    if srv and egg_info:
        current = {v["env"]: v["value"] for v in srv["variables"]}
        variables = [{**v, "value": current.get(v["env"], v["default"]) if not egg_change else v["default"]}
                     for v in egg_info["variables"]]
    return render(request, "server_admin.html", server=server, srv=srv, rights=rights, reason=reason, error=error,
                  users=users, eggs=eggs, egg_info=egg_info, egg_change=egg_change, variables=variables,
                  free=free[:500], c=client())


async def admin_server_for(request: Request, user, ident: str, perm: str | None) -> tuple[dict, str]:
    """Checks for a write on a server and loads its current admin data. perm None = owner only."""
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    if perm is None:
        if not user.is_owner:
            deny(request, user, "server.admin", ident)
        guard_write()
    else:
        require(request, user, ident, perm)
    ref = server_ref(ident)
    if ref is None:
        deny(request, user, "server.view", ident)
    return await client().admin_server(ref), ref


async def admin_write(request: Request, ident: str, perm: str | None, handler) -> RedirectResponse:
    """Shared frame of the write routes: rights, current data from the panel, errors as a message."""
    user = current_user(request)
    try:
        srv, _ref = await admin_server_for(request, user, ident, perm)
        return await handler(user, srv, await request.form())
    except AdminKeyMissing:
        return back(admin_page_target(ident), "Für diese Funktion braucht Hatchtower einen Admin-Key mit Schreibrechten.")
    except ValueError as exc:
        return back(admin_page_target(ident), str(exc))
    except PteroError as exc:
        return back(admin_page_target(ident), i18n.t("Das Panel antwortet nicht wie erwartet: {grund}", grund=str(exc)))


@app.post("/servers/{ident}/admin/details")
async def server_admin_details(request: Request, ident: str):
    async def handler(user, srv, form):
        name = clean_text(form.get("name", ""), 191)
        if not name:
            raise ValueError(i18n.t("Bitte einen Namen angeben."))
        description = str(form.get("description", ""))[:2000].strip()
        owner, external_id = srv["owner"], srv["external_id"]
        if user.is_owner:
            owner = str(form.get("owner") or owner)
            external_id = clean_text(form.get("external_id", ""), 191)
        before = {"name": srv["name"], "beschreibung": srv["description"], "besitzer": srv["owner"],
                  "external_id": srv["external_id"]}
        after = {"name": name, "beschreibung": description, "besitzer": owner, "external_id": external_id}
        return await run_admin(request, user, "server.admin.details", admin_page_target(ident, "details"), "Gespeichert",
                               diff(before, after), client().update_details(srv, name, description, owner, external_id),
                               ident)
    return await admin_write(request, ident, "admin.details", handler)


@app.post("/servers/{ident}/admin/build")
async def server_admin_build(request: Request, ident: str):
    async def handler(user, srv, form):
        c = client()
        limits, features, oom = parse_limits(form, c)
        before = {**srv["limits"], **srv["feature_limits"], "oom_disabled": srv["oom_disabled"]}
        after = {**limits, **features, **({"oom_disabled": oom} if c.HAS_OOM else {})}
        return await run_admin(request, user, "server.admin.build", admin_page_target(ident, "ressourcen"),
                               "Gespeichert. Neue Limits gelten nach dem nächsten Neustart des Servers.",
                               diff(before, after), c.update_limits(srv, limits, features, oom), ident)
    return await admin_write(request, ident, "admin.build", handler)


@app.post("/servers/{ident}/admin/ports")
async def server_admin_ports(request: Request, ident: str):
    async def handler(user, srv, form):
        c = client()
        own = {a["id"]: a for a in srv["allocations"]}
        primary = str(form.get("primary") or srv["primary"])
        remove = [a for a in form.getlist("remove") if a in own and a != primary]
        free = {a["id"]: a for a in await c.node_allocations(srv["node"]) if not a["assigned"]}
        add = [a for a in form.getlist("add") if a in free][:50]
        if primary not in own:
            raise ValueError(i18n.t("Der Hauptport muss einer der Ports des Servers sein."))
        if not add and not remove and primary == srv["primary"]:
            return back(admin_page_target(ident, "ports"), "Nichts geändert")
        port = lambda a: f"{a['ip']}:{a['port']}"  # noqa: E731
        detail = {"hauptport": [port(own[srv["primary"]]) if srv["primary"] in own else None, port(own[primary])]
                  if primary != srv["primary"] else None,
                  "hinzu": [port(free[a]) for a in add] or None, "entfernt": [port(own[a]) for a in remove] or None}
        return await run_admin(request, user, "server.admin.ports", admin_page_target(ident, "ports"),
                               "Ports gespeichert. Sie gelten nach dem nächsten Neustart des Servers.",
                               detail, c.update_allocations(srv, primary, add, remove), ident)
    return await admin_write(request, ident, "admin.build", handler)


@app.post("/servers/{ident}/admin/startup")
async def server_admin_startup(request: Request, ident: str):
    async def handler(user, srv, form):
        c = client()
        egg = str(form.get("egg") or srv["egg"]) if user.is_owner else srv["egg"]
        egg_info = await c.admin_egg(egg)
        startup = str(form.get("startup", "")).strip()[:4000]
        if not startup:
            raise ValueError(i18n.t("Bitte einen Startbefehl angeben."))
        image = str(form.get("image", "")).strip()
        if user.is_owner and str(form.get("image_custom", "")).strip():
            image = str(form.get("image_custom")).strip()
        allowed = set(egg_info["images"].values()) | ({srv["image"]} if egg == srv["egg"] else set())
        if not image or (image not in allowed and not user.is_owner):
            raise ValueError(i18n.t("Bitte ein Docker-Image aus der Liste wählen."))
        if not re.fullmatch(r"[A-Za-z0-9._/:@-]{1,255}", image):
            raise ValueError(i18n.t("Das Docker-Image hat kein gültiges Format."))
        current = {v["env"]: v["value"] for v in srv["variables"]}
        variables = [{**v, "value": current.get(v["env"], v["default"])} for v in egg_info["variables"]]
        env = environment_from(form, variables)
        before = {"egg": srv["egg"], "startbefehl": srv["startup"], "image": srv["image"]}
        after = {"egg": egg, "startbefehl": startup, "image": image}
        # Only the names of changed variables: values can be passwords and the server log shows the details
        detail = {**diff(before, after),
                  "variablen": sorted(k for k, v in env.items() if current.get(k) != v) or None}
        return await run_admin(request, user, "server.admin.startup", admin_page_target(ident, "start"),
                               "Gespeichert. Die Startparameter gelten nach dem nächsten Neustart des Servers.",
                               detail, c.update_startup(srv, startup, image, env, egg), ident)
    return await admin_write(request, ident, "admin.startup", handler)


@app.post("/servers/{ident}/admin/reinstall")
async def server_admin_reinstall(request: Request, ident: str):
    async def handler(user, srv, form):
        return await run_admin(request, user, "server.admin.reinstall", admin_page_target(ident, "verwaltung"),
                               "Neuinstallation gestartet. Der Server ist währenddessen nicht verfügbar.",
                               {"name": srv["name"]}, client().reinstall(srv), ident)
    return await admin_write(request, ident, "admin.reinstall", handler)


@app.post("/servers/{ident}/admin/suspend")
async def server_admin_suspend(request: Request, ident: str):
    async def handler(user, srv, form):
        suspend = form.get("suspend") == "1"
        return await run_admin(request, user, "server.admin.suspend", admin_page_target(ident, "verwaltung"),
                               "Server gesperrt" if suspend else "Server entsperrt",
                               {"gesperrt": [srv["suspended"], suspend]}, client().set_suspended(srv, suspend), ident)
    return await admin_write(request, ident, None, handler)


@app.post("/servers/{ident}/admin/delete")
async def server_admin_delete(request: Request, ident: str):
    async def handler(user, srv, form):
        if str(form.get("confirm_name", "")).strip() != srv["name"]:
            raise ValueError(i18n.t("Zum Löschen bitte den Namen des Servers genau so eintragen, wie er angezeigt wird."))
        force = form.get("force") == "1"
        return await run_admin(request, user, "server.admin.delete", admin_page_target(ident, "verwaltung"),
                               i18n.t("Server „{name}“ wurde im Panel gelöscht.", name=srv["name"]),
                               {"name": srv["name"], "erzwungen": force or None},
                               client().delete_server(srv, force), ident, done="/")
    return await admin_write(request, ident, None, handler)


# -- Owner: create servers, ports of the nodes ----------------------------------------------------

@app.get("/admin/servers", response_class=HTMLResponse)
async def admin_servers(request: Request):
    """All servers in one list, with the way into their settings (Hatchtower) and panel settings."""
    user = require_owner(request)
    servers = main.load_servers(user)
    return render(request, "admin_servers.html", servers=servers, admin_ready=bool(settings.admin_key))


@app.get("/admin/servers/new", response_class=HTMLResponse)
async def admin_server_new(request: Request, node: str = "", egg: str = ""):
    """Create a server: first node and egg, then everything else (variables and images come from the egg)."""
    require_owner(request)
    reason = unavailable()
    nodes, eggs, users, free = [], [], [], []
    egg_info = None
    error = ""
    if settings.admin_key:
        c = client()
        try:
            nodes, eggs, users = await c.admin_nodes(), await c.admin_eggs(), await c.admin_users()
            if node and egg and any(n["id"] == node for n in nodes) and any(e["id"] == egg for e in eggs):
                egg_info = await c.admin_egg(egg)
                free = [a for a in await c.node_allocations(node) if not a["assigned"]]
        except AdminKeyMissing:
            pass
        except PteroError as exc:
            error = str(exc)
    return render(request, "admin_server_new.html", reason=reason, error=error, nodes=nodes, eggs=eggs, users=users,
                  node=node, egg=egg, egg_info=egg_info, free=free[:500], c=client())


@app.post("/admin/servers/new")
async def admin_server_create(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    guard_write()
    form = await request.form()
    node, egg = str(form.get("node", "")), str(form.get("egg", ""))
    target = f"/admin/servers/new?node={node}&egg={egg}"
    c = client()
    try:
        egg_info = await c.admin_egg(egg)
        free = {a["id"]: a for a in await c.node_allocations(node) if not a["assigned"]}
        name = clean_text(form.get("name", ""), 191)
        if not name:
            raise ValueError(i18n.t("Bitte einen Namen angeben."))
        users = {u["id"] for u in await c.admin_users()}
        if str(form.get("owner", "")) not in users:
            raise ValueError(i18n.t("Bitte einen Besitzer wählen."))
        allocation = str(form.get("allocation", ""))
        if allocation not in free:
            raise ValueError(i18n.t("Bitte einen freien Port als Hauptport wählen."))
        additional = [a for a in form.getlist("additional") if a in free and a != allocation][:50]
        image = str(form.get("image", "")).strip()
        if str(form.get("image_custom", "")).strip():
            image = str(form.get("image_custom")).strip()
        if not image or not re.fullmatch(r"[A-Za-z0-9._/:@-]{1,255}", image):
            raise ValueError(i18n.t("Bitte ein Docker-Image wählen."))
        startup = str(form.get("startup", "")).strip()[:4000] or egg_info["startup"]
        limits, features, oom = parse_limits(form, c)
        env = environment_from(form, egg_info["variables"])
        spec = {"name": name, "description": str(form.get("description", ""))[:2000].strip(),
                "owner": str(form.get("owner")), "node": node, "egg": egg, "image": image, "startup": startup,
                "environment": env, "limits": limits, "features": features, "oom_disabled": oom,
                "allocation": allocation, "additional": additional, "start": form.get("start") == "1"}
        ident = await c.create_server(spec)
    except AdminKeyMissing:
        return back(target, "Für diese Funktion braucht Hatchtower einen Admin-Key mit Schreibrechten.")
    except ValueError as exc:
        return back(target, str(exc))
    except PteroError as exc:
        audit.record("server.admin.create", result="error", user_id=owner.id, username=owner.username,
                     ip=ip_of(request), detail={"name": form.get("name"), "fehler": str(exc)[:300]})
        return back(target, i18n.t("Das Panel hat den Server nicht angelegt: {grund}", grund=str(exc)))
    audit.record("server.admin.create", user_id=owner.id, username=owner.username, ip=ip_of(request), server=ident or None,
                 detail={"name": name, "node": node, "egg": egg_info["name"], "port": free[allocation]["port"],
                         "ram": limits.get("memory"), "speicher": limits.get("disk"), "cpu": limits.get("cpu")})
    app.state.monitor.request_sync()
    return back("/admin/servers/new", i18n.t("Server „{name}“ angelegt (Kennung {ident}). {panel} installiert ihn jetzt; "
                                             "er erscheint in wenigen Sekunden in der Übersicht.", name=name,
                                             ident=ident or "?"))


@app.get("/admin/ports", response_class=HTMLResponse)
async def admin_ports(request: Request, node: str = ""):
    require_owner(request)
    reason = unavailable()
    nodes, allocations = [], []
    error = ""
    names: dict[str, str] = {}
    if settings.admin_key:
        c = client()
        try:
            nodes = await c.admin_nodes()
            node = node if any(n["id"] == node for n in nodes) else (nodes[0]["id"] if nodes else "")
            if node:
                allocations = await c.node_allocations(node)
        except AdminKeyMissing:
            pass
        except PteroError as exc:
            error = str(exc)
        with db.connect() as con:
            for row in con.execute("SELECT name, allocations FROM servers WHERE present = 1"):
                for a in json.loads(row["allocations"] or "[]"):
                    names[str(a.get("id"))] = row["name"]
    ips = sorted({a["ip"] for a in allocations})
    return render(request, "admin_ports.html", reason=reason, error=error, nodes=nodes, node=node,
                  allocations=allocations, names=names, ips=ips,
                  free_count=sum(1 for a in allocations if not a["assigned"]))


@app.post("/admin/ports")
async def admin_ports_create(request: Request, node: str = Form(""), ip: str = Form(""), alias: str = Form(""),
                             ports: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    guard_write()
    target = f"/admin/ports?node={node}"
    try:
        try:
            address = str(ipaddress.ip_address(ip.strip()))
        except ValueError:
            raise ValueError(i18n.t("Bitte eine gültige IP-Adresse angeben.")) from None
        port_list = parse_ports(ports)
        alias = clean_text(alias, 191)
        if alias and not re.fullmatch(r"[A-Za-z0-9.\-:]+", alias):
            raise ValueError(i18n.t("Alias: nur Hostname oder IP."))
        if not any(n["id"] == node for n in await client().admin_nodes()):
            raise ValueError(i18n.t("Diesen Node gibt es im Panel nicht."))
    except AdminKeyMissing:
        return back(target, "Für diese Funktion braucht Hatchtower einen Admin-Key mit Schreibrechten.")
    except ValueError as exc:
        return back(target, str(exc))
    except PteroError as exc:
        return back(target, str(exc))
    return await run_admin(request, owner, "node.ports.create", target, "Ports angelegt",
                           {"node": node, "ip": address, "alias": alias or None, "ports": port_list},
                           client().create_allocations(node, address, alias, port_list))


@app.post("/admin/ports/delete")
async def admin_ports_delete(request: Request, node: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    guard_write()
    target = f"/admin/ports?node={node}"
    form = await request.form()
    c = client()
    try:
        current = {a["id"]: a for a in await c.node_allocations(node)}
    except AdminKeyMissing:
        return back(target, "Für diese Funktion braucht Hatchtower einen Admin-Key mit Schreibrechten.")
    except PteroError as exc:
        return back(target, str(exc))
    chosen = [current[a] for a in form.getlist("alloc") if a in current and not current[a]["assigned"]][:200]
    if not chosen:
        return back(target, "Bitte freie Ports auswählen. Ports, die ein Server nutzt, lassen sich nicht löschen.")

    async def delete_all():
        for a in chosen:
            await c.delete_allocation(node, a["id"])
    return await run_admin(request, owner, "node.ports.delete", target,
                           i18n.t("{n} Port(s) gelöscht", n=len(chosen)),
                           {"node": node, "ports": [f"{a['ip']}:{a['port']}" for a in chosen]}, delete_all())
