"""Web-Anwendung von Hatchtower."""
from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile as FormFile

from . import (APP_NAME, __version__, audit, automation, db, extras, i18n, legal, logarchive, logos, retention, security,
               setup, themes, updates, zipdownload)
from .config import DB_KEYS, apply_db_values, load_settings, parse_proxies
from .monitor import Monitor
from .permissions import (BY_KEY, PERMISSIONS, UserContext, describe_access, grouped_permissions, load_assignments,
                          load_grants, load_groups, save_assignments, save_group, with_dependencies)
from .ptero import PteroClient, PteroError, ReadOnlyViolation
from .views import SERVER_QUERY, fmt_dt, fmt_size, server_view
from .wings import console_stream

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("hatchtower")

BASE = Path(__file__).parent
settings = load_settings()
vault = security.Vault(settings.data_key)
throttle = security.LoginThrottle()
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals.update(app_name=APP_NAME, version=__version__, settings=settings, _=i18n.t,
                             languages=i18n.LANGUAGES, all_themes=themes.THEMES)
LANG_COOKIE = "hatchtower_lang"
# Cache-Parameter für CSS/JS, ohne die Versionsnummer öffentlich zu zeigen
templates.env.filters["hash_version"] = lambda v: __import__("hashlib").sha256(
    f"{v}:{settings.secret_key}".encode()).hexdigest()[:10]

# Reiter unter „System“
SYSTEM_TABS = [
    ("/admin/system", "Übersicht"),
    ("/admin/connection", "Verbindung"),
    ("/admin/mail", "E-Mail"),
    ("/admin/notify", "Benachrichtigungen"),
    ("/admin/statuspage", "Statusseite"),
    ("/admin/announcements", "Ankündigungen"),
    ("/admin/privacy", "Datenschutz"),
    ("/admin/transfer", "Datenumzug"),
]


def load_connection() -> None:
    """Liest die in der Oberfläche gespeicherten Verbindungsdaten in die laufenden Einstellungen."""
    with db.connect() as con:
        values = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%'")}
    apply_db_values(settings, values, vault.decrypt)


async def apply_connection() -> None:
    """Baut die Verbindung zu Pterodactyl mit den aktuellen Einstellungen neu auf (ohne Neustart)."""
    load_connection()
    old = app.state.ptero
    new = PteroClient(settings)
    app.state.ptero = new
    app.state.monitor.client = new
    app.state.automation.client = new
    app.state.logarchive.client = new
    app.state.automation.set_timezone(settings.timezone)
    app.state.monitor.request_sync()
    await old.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(settings.db_path)
    load_connection()
    app.state.ptero = PteroClient(settings)
    app.state.monitor = Monitor(app.state.ptero, vault, settings.poll_interval, settings.sync_interval)
    app.state.automation = automation.Automation(app.state.ptero, settings.timezone, vault)
    app.state.logarchive = logarchive.LogArchive(app.state.ptero)
    app.state.monitor.on_offline = app.state.automation.on_offline
    app.state.monitor.start()
    app.state.automation.start()
    app.state.logarchive.start()
    app.state.updates = updates.UpdateCheck()
    app.state.updates.start()
    cleanup_task = asyncio.create_task(retention.run_forever(), name="hatchtower-retention")
    log.info("%s %s gestartet (Modus: %s, Ziel: %s %s)", APP_NAME, __version__, settings.manager_mode,
             "Pterodactyl", settings.pterodactyl_url)
    yield
    cleanup_task.cancel()
    await app.state.updates.stop()
    await app.state.logarchive.stop()
    await app.state.automation.stop()
    await app.state.monitor.stop()
    await app.state.ptero.close()


app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


# -- Infrastruktur ---------------------------------------------------------

class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message: str = "Dafür fehlt dir die Berechtigung."):
        self.message = message


CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def is_https(request: Request) -> bool:
    peer = request.client.host if request.client else None
    trusted = any(str(p) == peer for p in settings.trusted_proxies)
    return request.url.scheme == "https" or (trusted and request.headers.get("x-forwarded-proto") == "https")


# Höchstgröße einer Anfrage, geprüft bevor der Inhalt gelesen wird (Schutz vor Speicher-/Plattenflutung)
BODY_LIMIT_DEFAULT = 1024 * 1024
BODY_LIMITS = [
    (re.compile(r"^/servers/[^/]+/files/upload$"), 201 * 1024 * 1024),
    (re.compile(r"^/(admin/transfer|setup)/import$"), 51 * 1024 * 1024),
    (re.compile(r"^/(servers/[^/]+/logo|admin/branding/logo/[^/]+)$"), 3 * 1024 * 1024),
    (re.compile(r"^/servers/[^/]+/files/edit$"), 4 * 1024 * 1024),
]


def body_problem(request: Request) -> Response | None:
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    path = request.url.path
    limit = next((size for pattern, size in BODY_LIMITS if pattern.match(path)), BODY_LIMIT_DEFAULT)
    length = request.headers.get("content-length")
    if length is None:
        if request.headers.get("transfer-encoding"):
            return Response("Content-Length required", status_code=411)
        return None
    if not length.isdigit() or int(length) > limit:
        return Response("Request too large", status_code=413)
    # Einrichtung ist nur ohne Benutzer möglich: dann gar nicht erst Daten annehmen
    if path in ("/setup/import", "/setup/owner") and setup.users_exist():
        return RedirectResponse("/login", status_code=303)
    return None


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Sprache: Auswahl im Cookie, sonst Browser, sonst Standard aus dem System.
    # Angemeldete Benutzer überschreiben das in current_user() mit ihrer gespeicherten Wahl.
    i18n.set_language(i18n.valid(request.cookies.get(LANG_COOKIE))
                      or i18n.from_accept_language(request.headers.get("accept-language"))
                      or i18n.valid(db.get_setting("default_language")))
    response = body_problem(request) or await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


def ip_of(request: Request) -> str:
    return security.client_ip(
        request.client.host if request.client else None, request.headers.get("x-forwarded-for"), settings
    )


def current_user(request: Request) -> UserContext:
    row = security.load_session(request.cookies.get(security.SESSION_COOKIE), settings)
    if row is None:
        raise LoginRequired()
    request.state.csrf = row["csrf"]
    user = UserContext(
        id=row["user_id"], username=row["username"], role=row["role"], read_only=settings.read_only,
        grants=load_grants(row["user_id"]), theme=row["theme"], language=row["language"],
    )
    if i18n.valid(row["language"]):
        i18n.set_language(row["language"])
    request.state.user = user
    return user


def require_owner(request: Request) -> UserContext:
    user = current_user(request)
    if not user.is_owner:
        deny(request, user, "admin")
    return user


def deny(request: Request, user: UserContext, what: str, server: str | None = None):
    audit.record("access.denied", result="denied", user_id=user.id, username=user.username,
                 ip=ip_of(request), server=server, detail=what)
    raise Forbidden()


def require(request: Request, user: UserContext, ident: str, key: str) -> None:
    if not user.can(ident, key):
        deny(request, user, key, ident)


async def check_csrf(request: Request) -> None:
    form = await request.form()
    if not security.csrf_ok(getattr(request.state, "csrf", None), form.get("csrf")):
        raise Forbidden("Die Sitzung ist abgelaufen oder das Formular ist ungültig. Lade die Seite neu.")


# -- Erscheinungsbild: Name und Logos -----------------------------------------------

# Gespeichert wird in doppelter Auflösung der Anzeige, damit Logos auf scharfen Bildschirmen nicht verschwimmen.
BRAND_LOGOS = {
    "header": {"key": "brand_logo_header", "box": (360, 72), "label": "Kopfzeile"},
    "login": {"key": "brand_logo_login", "box": (640, 240), "label": "Login"},
}


def load_brand() -> dict:
    with db.connect() as con:
        values = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'brand_%'")}
    custom = (values.get("brand_name") or "").strip()
    return {
        "name": custom or APP_NAME,       # für Browser-Tab
        "custom": custom,                 # eigener Name (optional)
        "product": APP_NAME,              # Hatchtower, wird immer dezent mit angezeigt
        "show_name": values.get("brand_show_name", "1") == "1",
        "logo_header": values.get("brand_logo_header") or None,
        "logo_login": values.get("brand_logo_login") or None,
    }


def check_csrf_header(request: Request) -> None:
    if not security.csrf_ok(getattr(request.state, "csrf", None), request.headers.get("x-csrf-token")):
        raise Forbidden("Die Sitzung ist abgelaufen. Lade die Seite neu.")


def render(request: Request, template: str, status: int = 200, **context) -> HTMLResponse:
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("csrf", getattr(request.state, "csrf", ""))
    context.setdefault("monitor", app.state.monitor)
    context.setdefault("flash", request.query_params.get("ok"))
    context.setdefault("brand", load_brand())
    context.setdefault("lang", i18n.current())
    context.setdefault("i18n_browser", {"locale": i18n.locale_tag(), "texts": i18n.browser_catalog()})
    context.setdefault("here", request.url.path + (f"?{request.url.query}" if request.url.query else ""))
    context.setdefault("legal_links", legal.links())
    context.setdefault("system_tabs", SYSTEM_TABS)
    if template in ("login.html", "setup.html"):
        from . import mailer
        context.setdefault("forgot_enabled", mailer.load_config(vault).ready)
    context.setdefault("in_system", any(request.url.path.startswith(p) for p, _ in SYSTEM_TABS))
    if context.get("user") is not None:
        context.setdefault("announcements", extras.active_announcements())
        context.setdefault("update_available", getattr(app.state, "updates", None) is not None and app.state.updates.available)
        context.setdefault("status_page_on", db.get_setting("status_enabled") == "1")
    user = context.get("user")
    context.setdefault("theme", themes.valid(getattr(user, "theme", None)) or themes.valid(db.get_setting("default_theme")) or themes.DEFAULT)
    return templates.TemplateResponse(request, template, context, status_code=status)


def back(url: str, message: str | None = None) -> RedirectResponse:
    if message:
        message = i18n.t(message)
        url += ("&" if "?" in url else "?") + "ok=" + quote(message)
    return RedirectResponse(url, status_code=303)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    if request.url.path.startswith("/api/") or request.headers.get("accept") == "text/event-stream":
        return JSONResponse({"error": "login"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": i18n.t(exc.message)}, status_code=403)
    return render(request, "error.html", status=403, title="Kein Zugriff", message=exc.message)


@app.exception_handler(ReadOnlyViolation)
async def _read_only(request: Request, exc: ReadOnlyViolation):
    user = getattr(request.state, "user", None)
    audit.record("readonly.blocked", result="blocked", user_id=getattr(user, "id", None),
                 username=getattr(user, "username", None), ip=ip_of(request), detail=str(exc))
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": i18n.t("Der Manager ist im Read-Only-Modus.")}, status_code=403)
    return render(request, "error.html", status=403, title="Read-Only-Modus",
                  message="Der Manager ist im Read-Only-Modus. Schreibzugriffe auf Pterodactyl sind gesperrt.")


def load_servers(user: UserContext) -> list[dict]:
    with db.connect() as con:
        rows = con.execute(SERVER_QUERY + " ORDER BY s.name COLLATE NOCASE").fetchall()
    host = db.get_setting("public_host")
    favs = extras.favorites(user.id)
    result = [server_view(r, user, vault, settings.timezone, host) for r in rows if user.visible(r["identifier"])]
    for s in result:
        s["favorite"] = s["identifier"] in favs
    return result


def load_server(user: UserContext, ident: str) -> dict | None:
    with db.connect() as con:
        row = con.execute(SERVER_QUERY + " AND s.identifier = ?", (ident,)).fetchone()
    if row is None or not user.visible(ident):
        return None
    return server_view(row, user, vault, settings.timezone, db.get_setting("public_host"))


def get_server_or_deny(request: Request, user: UserContext, ident: str) -> dict:
    server = load_server(user, ident)
    if server is None:
        deny(request, user, "server.view", ident)
    return server


# -- Anmeldung ------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not setup.users_exist():
        return RedirectResponse("/setup", status_code=303)
    token = security.prelogin_token(settings.secret_key)
    response = render(request, "login.html", prelogin=token, error=None)
    response.set_cookie(security.PRELOGIN_COOKIE, token, httponly=True, samesite="strict",
                        secure=is_https(request), max_age=900)
    return response


@app.post("/login")
async def login(request: Request, username: str = Form(""), password: str = Form(""), prelogin: str = Form("")):
    ip = ip_of(request)

    def fail(message: str) -> HTMLResponse:
        token = security.prelogin_token(settings.secret_key)
        response = render(request, "login.html", status=400, prelogin=token, error=message)
        response.set_cookie(security.PRELOGIN_COOKIE, token, httponly=True, samesite="strict",
                            secure=is_https(request), max_age=900)
        return response

    if not security.prelogin_valid(settings.secret_key, request.cookies.get(security.PRELOGIN_COOKIE), prelogin):
        return fail("Das Formular ist abgelaufen. Versuch es noch einmal.")
    if throttle.ip_blocked(ip):
        audit.record("login", result="blocked", username=username[:64], ip=ip, detail="zu viele Versuche (IP)")
        return fail("Zu viele Anmeldeversuche. Warte ein paar Minuten.")

    with db.connect() as con:
        row = con.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()

    now = db.utcnow()
    if row is not None and row["locked_until"] and row["locked_until"] > now:
        security.verify_password(None, password)
        throttle.record_failure(ip)
        audit.record("login", result="blocked", user_id=row["id"], username=row["username"], ip=ip, detail="Konto gesperrt")
        return fail("Anmeldung fehlgeschlagen.")

    ok = security.verify_password(row["password_hash"] if row else None, password)
    if not ok or row is None or not row["is_active"]:
        throttle.record_failure(ip)
        if row is not None:
            failed = row["failed_logins"] + 1
            minutes = security.LoginThrottle.lock_minutes(failed)
            locked = None
            if minutes:
                locked = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
            with db.connect() as con:
                con.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?", (failed, locked, row["id"]))
        audit.record("login", result="failed", user_id=row["id"] if row else None,
                     username=username[:64], ip=ip)
        return fail("Anmeldung fehlgeschlagen.")

    with db.connect() as con:
        con.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
                    (now, row["id"]))
    token, _ = security.create_session(row["id"], ip, request.headers.get("user-agent", ""))
    audit.record("login", user_id=row["id"], username=row["username"], ip=ip)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(security.SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=is_https(request), max_age=settings.session_max_hours * 3600)
    response.delete_cookie(security.PRELOGIN_COOKIE)
    return response


# -- Einrichtung (Assistent) und Verbindung ------------------------------------------

SETUP_STEPS = [("owner", "Konto"), ("panel", "Panel"), ("client", "Client-Key"), ("nodes", "Wings"), ("options", "Optionen")]


def login_response(request: Request, user_id: int, username: str, target: str) -> RedirectResponse:
    token, _ = security.create_session(user_id, ip_of(request), request.headers.get("user-agent", ""))
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(security.SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=is_https(request), max_age=settings.session_max_hours * 3600)
    return response


async def connection_context(step: str | None) -> dict:
    """Daten für die Formulare von Assistent und Verbindungsseite."""
    ctx = {
        "panel_url": settings.pterodactyl_url,
        "app_key_masked": setup.mask_key(settings.app_key), "client_key_masked": setup.mask_key(settings.client_key),
        "manager_ip": setup.manager_ip_towards(settings.pterodactyl_url) if settings.pterodactyl_url else None,
        "mode": settings.manager_mode, "public_host": db.get_setting("public_host"),
        "trusted_proxies": ", ".join(str(p) for p in settings.trusted_proxies), "timezone": settings.timezone,
        "timezones": setup.timezone_choices(), "nodes": [], "nodes_error": None,
        "steps": SETUP_STEPS[1:], "step": step,
    }
    if step in (None, "nodes") and settings.pterodactyl_url and settings.app_key:
        try:
            ctx["nodes"] = await setup.node_checks(app.state.ptero, settings.node_overrides)
        except PteroError as exc:
            ctx["nodes_error"] = setup.explain(exc, "Application-Key")
    return ctx


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, step: str = ""):
    if not setup.users_exist():
        return owner_step(request, None, "")
    user = current_user(request)
    if not user.is_owner:
        deny(request, user, "setup")
    if step not in dict(SETUP_STEPS) or step == "owner":
        step = ("panel" if not (settings.pterodactyl_url and settings.app_key)
                else "client" if not settings.client_key else "nodes")
    return render(request, "setup.html", conn=await connection_context(step), step=step, steps=SETUP_STEPS[1:],
                  error=None)


def owner_step(request: Request, error: str | None, username: str) -> HTMLResponse:
    token = security.prelogin_token(settings.secret_key)
    response = render(request, "setup.html", status=400 if error else 200, step="owner", steps=SETUP_STEPS[1:],
                      error=error, username=username, prelogin=token)
    response.set_cookie(security.PRELOGIN_COOKIE, token, httponly=True, samesite="strict",
                        secure=is_https(request), max_age=1800)
    return response


@app.post("/setup/owner")
async def setup_owner(request: Request, token: str = Form(""), username: str = Form(""), password: str = Form(""),
                      repeat: str = Form(""), prelogin: str = Form("")):
    if setup.users_exist():
        return RedirectResponse("/login", status_code=303)
    ip = ip_of(request)

    def fail(message: str):
        return owner_step(request, message, username)

    if not security.prelogin_valid(settings.secret_key, request.cookies.get(security.PRELOGIN_COOKIE), prelogin):
        return fail("Das Formular ist abgelaufen. Versuch es noch einmal.")
    if throttle.ip_blocked(ip):
        return fail("Zu viele Versuche. Warte ein paar Minuten.")
    if not setup.check_setup_token(token):
        throttle.record_failure(ip)
        audit.record("setup.owner", result="failed", ip=ip, detail="Einrichtungscode falsch oder abgelaufen")
        return fail("Der Einrichtungscode stimmt nicht oder ist abgelaufen. "
                    "Einen neuen erzeugst du auf dem Server mit: hatchtower setup-token")
    username = username.strip()
    if not username or len(username) > 32 or not all(c.isalnum() or c in "-_." for c in username):
        return fail("Benutzername: 1–32 Zeichen, nur Buchstaben, Zahlen, - _ .")
    if password != repeat:
        return fail("Die Passwörter stimmen nicht überein.")
    problems = security.password_problems(password, username)
    if problems:
        return fail(i18n.t("Das Passwort braucht {list}.", list=", ".join(i18n.t(x) for x in problems)))
    with db.connect() as con:
        user_id = con.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'owner', ?)",
            (username, security.hash_password(password), db.utcnow())).lastrowid
    setup.clear_setup_token()
    audit.record("setup.owner", user_id=user_id, username=username, ip=ip)
    return login_response(request, user_id, username, "/setup?step=panel")


def connection_back(target: str, message: str) -> RedirectResponse:
    if not target.startswith(("/setup", "/admin/connection")):
        target = "/admin/connection"
    return back(target, message)


@app.post("/admin/connection/panel")
async def connection_panel(request: Request, panel_url: str = Form(""),
                           app_key: str = Form(""), next: str = Form("/admin/connection")):
    owner = require_owner(request)
    await check_csrf(request)
    url = setup.normalize_url(panel_url)
    if url is None:
        return connection_back(next, "Bitte die Panel-Adresse vollständig angeben, z. B. https://panel.example.de")
    key = app_key.strip() or settings.app_key
    if not key:
        return connection_back(next, "Bitte den Application-API-Key eintragen")
    problem = setup.key_problem(key, "ptla_")
    if problem:
        return connection_back(next, problem)
    try:
        result = await setup.test_panel(settings, url, key)
    except PteroError as exc:
        return connection_back(next, i18n.t("Verbindung fehlgeschlagen: {grund}", grund=setup.explain(exc, "Application-Key")))
    setup.save_values({"panel_url": url, "app_key": key}, vault, owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"panel": url, "app_key": "neu" if app_key.strip() else "unverändert"})
    return connection_back(next.replace("step=panel", "step=client"),
                           i18n.t("Verbunden: {nodes} Node(s), {servers} Server gefunden", nodes=len(result["nodes"]), servers=result["servers"]))


@app.post("/admin/connection/client")
async def connection_client(request: Request, client_key: str = Form(""), next: str = Form("/admin/connection")):
    owner = require_owner(request)
    await check_csrf(request)
    key = client_key.strip() or settings.client_key
    if not key:
        return connection_back(next, "Bitte den Client-API-Key eintragen")
    problem = setup.key_problem(key, "ptlc_")
    if problem:
        return connection_back(next, problem)
    if not settings.pterodactyl_url:
        return connection_back(next, "Bitte zuerst die Panel-Adresse speichern")
    try:
        count = await setup.test_client(settings, key)
    except PteroError as exc:
        hint = setup.explain(exc, "Client-Key")
        ip = setup.manager_ip_towards(settings.pterodactyl_url)
        if exc.status == 403 and ip:
            hint += i18n.t(". Trage beim Key als Allowed IP {ip} ein", ip=ip)
        return connection_back(next, i18n.t("Client-Key abgelehnt: {grund}", grund=hint))
    setup.save_values({"client_key": key}, vault, owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"client_key": "neu" if client_key.strip() else "unverändert", "server_sichtbar": count})
    return connection_back(next.replace("step=client", "step=nodes"), i18n.t("Client-Key funktioniert: {count} Server sichtbar", count=count))


@app.post("/admin/connection/nodes")
async def connection_nodes(request: Request, next: str = Form("/admin/connection")):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    overrides = {}
    for field, value in form.items():
        if field.startswith("override__") and isinstance(value, str) and value.strip():
            value = value.strip().rstrip("/")
            if not re.fullmatch(r"(https?://)?[A-Za-z0-9.\-]+(:\d{1,5})?", value):
                return connection_back(next, i18n.t("Ungültige Adresse: {value}", value=value))
            overrides[field[len("override__"):]] = value
    setup.save_values({"node_overrides": overrides}, vault, owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"wings_adressen": overrides or "keine Abweichung"})
    return connection_back(next.replace("step=nodes", "step=options"), "Wings-Einstellungen gespeichert")


@app.post("/admin/connection/options")
async def connection_options(request: Request, mode: str = Form("read-only"), public_host: str = Form(""),
                             trusted_proxies: str = Form(""), timezone_: str = Form("Europe/Berlin", alias="timezone"),
                             next: str = Form("/admin/connection")):
    owner = require_owner(request)
    await check_csrf(request)
    try:
        parse_proxies(trusted_proxies)
    except ValueError:
        return connection_back(next, "Reverse-Proxy: bitte IP-Adressen durch Komma getrennt angeben")
    choices = setup.timezone_choices()
    if timezone_.strip() not in choices["common"] + choices["others"]:
        return connection_back(next, "Bitte eine Zeitzone aus der Liste wählen")
    host = public_host.strip().lower()
    if host and not re.fullmatch(r"[a-z0-9.\-]{1,253}", host):
        return connection_back(next, "Öffentliche Adresse: nur Hostname oder IP, ohne http:// und Port")
    mode = "read-write" if mode == "read-write" else "read-only"
    old_mode = settings.manager_mode
    setup.save_values({"mode": mode, "trusted_proxies": trusted_proxies.strip(), "timezone": timezone_.strip()}, vault, owner.id)
    db.set_setting("public_host", host, owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"modus": [old_mode, mode], "public_host": host, "proxy": trusted_proxies.strip(),
                         "zeitzone": timezone_.strip()})
    if next.startswith("/setup"):
        return back("/", "Einrichtung abgeschlossen. Willkommen bei Hatchtower!")
    return connection_back(next, "Optionen gespeichert")


@app.get("/admin/connection", response_class=HTMLResponse)
async def connection_page(request: Request):
    require_owner(request)
    return render(request, "connection.html", conn=await connection_context(None))


@app.get("/theme/{key}")
async def switch_theme(request: Request, key: str, next: str = "/"):
    """Theme umschalten (Kopfzeile), im Konto gespeichert."""
    user = current_user(request)
    target = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    chosen = themes.valid(key)
    if chosen and chosen != user.theme:
        with db.connect() as con:
            con.execute("UPDATE users SET theme = ? WHERE id = ?", (chosen, user.id))
        audit.record("theme.update", user_id=user.id, username=user.username, ip=ip_of(request), detail=chosen)
    return RedirectResponse(target, status_code=303)


@app.get("/lang/{code}")
async def switch_language(request: Request, code: str, next: str = "/"):
    """Sprache umschalten. Angemeldete Benutzer: im Konto gespeichert, sonst im Cookie."""
    lang = i18n.valid(code)
    target = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    response = RedirectResponse(target, status_code=303)
    if lang is None:
        return response
    response.set_cookie(LANG_COOKIE, lang, max_age=365 * 24 * 3600, httponly=True, samesite="lax",
                        secure=is_https(request))
    row = security.load_session(request.cookies.get(security.SESSION_COOKIE), settings)
    if row is not None:
        with db.connect() as con:
            con.execute("UPDATE users SET language = ? WHERE id = ?", (lang, row["user_id"]))
    return response


@app.post("/logout")
async def logout(request: Request):
    user = current_user(request)
    await check_csrf(request)
    security.delete_session(request.cookies.get(security.SESSION_COOKIE))
    audit.record("logout", user_id=user.id, username=user.username, ip=ip_of(request))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(security.SESSION_COOKIE)
    return response


# -- Übersicht -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, als: int | None = None):
    if not setup.users_exist():
        return RedirectResponse("/setup", status_code=303)
    user = current_user(request)
    if user.is_owner and not settings.configured:
        return RedirectResponse("/setup", status_code=303)
    view_user, preview = user, None
    if als is not None:
        if not user.is_owner:
            deny(request, user, "preview")
        with db.connect() as con:
            target = con.execute("SELECT id, username, role FROM users WHERE id = ?", (als,)).fetchone()
        if target is None:
            return back("/admin/users")
        view_user = UserContext(target["id"], target["username"], target["role"], settings.read_only,
                                load_grants(target["id"]))
        preview = target["username"]
        audit.record("preview.as_user", user_id=user.id, username=user.username, ip=ip_of(request),
                     detail=target["username"])
    servers = load_servers(view_user)
    return render(request, "dashboard.html", servers=servers, preview=preview)


@app.get("/api/overview")
async def api_overview(request: Request):
    user = current_user(request)
    m = app.state.monitor
    return {
        "servers": load_servers(user),
        "monitor": {"healthy": m.healthy, "error": i18n.t(m.last_error) if m.last_error else None,"last_ok": fmt_dt(m.last_ok, settings.timezone)},
    }


# -- Verlauf: CPU, RAM, Netzwerk -----------------------------------------------

METRIC_RANGES = (120, 3600, 6 * 3600, 12 * 3600, 24 * 3600, 72 * 3600)
METRIC_POINTS = 300


@app.get("/api/servers/{ident}/metrics")
async def api_metrics(request: Request, ident: str, range: int = 3600):
    user = current_user(request)
    get_server_or_deny(request, user, ident)
    span = range if range in METRIC_RANGES else 3600
    now = int(time.time())
    with db.connect() as con:
        limits = con.execute("SELECT limit_cpu, limit_memory FROM servers WHERE identifier = ?", (ident,)).fetchone()
        rows = con.execute(
            "SELECT ts, cpu, mem, rx, tx FROM metrics WHERE identifier = ? AND ts >= ? ORDER BY ts",
            (ident, now - span - 60),
        ).fetchall()
    cpu_limit = limits["limit_cpu"] if limits else 0

    # Netzwerk: Pterodactyl liefert Summen seit Containerstart -> in Bytes pro Sekunde umrechnen
    samples = []
    prev = None
    for r in rows:
        rx_rate = tx_rate = None
        if prev is not None and 0 < r["ts"] - prev["ts"] <= 120:
            dt = r["ts"] - prev["ts"]
            rx_rate = max(r["rx"] - prev["rx"], 0) / dt
            tx_rate = max(r["tx"] - prev["tx"], 0) / dt
        prev = r
        if r["ts"] < now - span:
            continue
        cpu = r["cpu"] or 0.0
        cpu_pct = min(cpu / cpu_limit * 100, 100.0) if cpu_limit else cpu
        samples.append((r["ts"], cpu_pct, r["mem"] or 0, rx_rate, tx_rate))

    bucket = max(1, span // METRIC_POINTS)
    points = []
    if bucket <= 15:
        points = [list(s) for s in samples]
    else:
        groups: dict[int, list] = {}
        for s in samples:
            groups.setdefault(s[0] // bucket, []).append(s)
        def avg(group: list, i: int) -> float | None:
            values = [x[i] for x in group if x[i] is not None]
            return sum(values) / len(values) if values else None

        for key in sorted(groups):
            g = groups[key]
            points.append([key * bucket + bucket // 2, avg(g, 1), avg(g, 2), avg(g, 3), avg(g, 4)])
    return {
        "range": span, "now": now, "bucket": bucket, "points": points,
        "cpu_limit": cpu_limit, "mem_limit": (limits["limit_memory"] or 0) * 1024 * 1024 if limits else 0,
    }


# -- Serverdetails, Konsole, Dateien ---------------------------------------

@app.get("/servers/{ident}", response_class=HTMLResponse)
async def server_detail(request: Request, ident: str):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    server["favorite"] = ident in extras.favorites(user.id)
    return render(request, "server.html", server=server, uptime=extras.availability(ident),
                  archive_days=retention.console_days())


@app.get("/servers/{ident}/console/stream")
async def console_sse(request: Request, ident: str):
    user = current_user(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "console.read")
    audit.record("console.open", user_id=user.id, username=user.username, ip=ip_of(request), server=ident)

    async def events():
        try:
            async for item in console_stream(app.state.ptero, ident):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except PteroError as exc:
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def clean_path(path: str) -> str:
    return "/" + posixpath.normpath("/" + (path or "/").replace("\\", "/")).lstrip("/")


@app.get("/servers/{ident}/files", response_class=HTMLResponse)
async def files_page(request: Request, ident: str, dir: str = "/"):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.view")
    directory = clean_path(dir)
    error = None
    try:
        files = await app.state.ptero.list_files(ident, directory)
    except PteroError as exc:
        files, error = [], str(exc)
    audit.record("files.list", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail=directory, result="ok" if error is None else "error")
    crumbs = []
    acc = ""
    for part in [p for p in directory.split("/") if p]:
        acc += "/" + part
        crumbs.append((part, acc))
    for f in files:
        f["path"] = posixpath.join(directory, f["name"])
        f["size_text"] = fmt_size(f["size"]) if f["is_file"] else ""
        f["modified_text"] = fmt_dt(f["modified_at"], settings.timezone)
    return render(request, "files.html", server=server, files=files, directory=directory, crumbs=crumbs,
                  parent=posixpath.dirname(directory) if directory != "/" else None, error=error,
                  can_download=user.can(ident, "sftp.download"), can_upload=user.can(ident, "sftp.upload"),
                  can_mkdir=user.can(ident, "sftp.create_directory"), can_rename=user.can(ident, "sftp.rename"),
                  can_delete=user.can(ident, "sftp.delete"), can_edit=user.can(ident, "sftp.edit"))


@app.get("/servers/{ident}/files/download")
async def files_download(request: Request, ident: str, file: str):
    user = current_user(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.download")
    path = clean_path(file)
    try:
        url = await app.state.ptero.download_url(ident, path)
        stream = app.state.ptero.stream_download(url)
        first = await stream.__anext__()
    except StopAsyncIteration:
        first, stream = b"", None
    except PteroError as exc:
        audit.record("files.download", result="error", user_id=user.id, username=user.username,
                     ip=ip_of(request), server=ident, detail=f"{path}: {exc}")
        return render(request, "error.html", status=502, title="Download fehlgeschlagen", message=str(exc))
    audit.record("files.download", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail=path)

    async def body():
        yield first
        if stream is not None:
            async for chunk in stream:
                yield chunk

    name = posixpath.basename(path) or "download"
    return StreamingResponse(body(), media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
    })


# -- Steuerung: Start, Stop, Restart, Befehle ---------------------------------

POWER_LABELS = {"start": "Start", "stop": "Stop", "restart": "Restart", "kill": "Kill"}
POWER_COOLDOWN = 5.0
_power_last: dict[str, float] = {}


async def json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@app.post("/api/servers/{ident}/power")
async def api_power(request: Request, ident: str):
    user = current_user(request)
    check_csrf_header(request)
    server = get_server_or_deny(request, user, ident)
    signal = str((await json_body(request)).get("signal", ""))
    if signal not in POWER_LABELS:
        return JSONResponse({"error": i18n.t("Unbekannte Aktion.")}, status_code=400)
    if signal == "kill":
        if not user.is_owner:
            deny(request, user, "power.kill", ident)
    else:
        require(request, user, ident, f"power.{signal}")
    now = asyncio.get_running_loop().time()
    if now - _power_last.get(ident, -1e9) < POWER_COOLDOWN:
        return JSONResponse({"error": i18n.t("Bitte ein paar Sekunden warten.")}, status_code=429)
    _power_last[ident] = now
    try:
        await app.state.ptero.power(ident, signal)
    except PteroError as exc:
        audit.record(f"power.{signal}", result="error", user_id=user.id, username=user.username,
                     ip=ip_of(request), server=ident, detail=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=502)
    app.state.automation.note_power(ident, signal)
    app.state.monitor.follow(ident)
    audit.record(f"power.{signal}", user_id=user.id, username=user.username, ip=ip_of(request), server=ident)
    return {"ok": True, "message": i18n.t("{action} für {name} an Pterodactyl gesendet.", action=POWER_LABELS[signal], name=server["name"])}


@app.post("/api/servers/{ident}/command")
async def api_command(request: Request, ident: str):
    user = current_user(request)
    check_csrf_header(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "console.command")
    command = str((await json_body(request)).get("command", "")).strip()
    if not command or len(command) > 500 or any(c in command for c in "\r\n\x00"):
        return JSONResponse({"error": i18n.t("Befehl leer oder ungültig (max. 500 Zeichen, eine Zeile).")}, status_code=400)
    try:
        await app.state.ptero.command(ident, command)
    except PteroError as exc:
        audit.record("console.command", result="error", user_id=user.id, username=user.username,
                     ip=ip_of(request), server=ident, detail=f"{command} → {exc}")
        message = i18n.t("Der Server ist nicht gestartet.") if exc.status == 502 else str(exc)
        return JSONResponse({"error": message}, status_code=502)
    if command.lstrip("/").split(" ")[0].lower() in automation.STOP_WORDS:
        app.state.automation.note_power(ident, "stop")
    audit.record("console.command", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail=command)
    return {"ok": True}


# -- Dateien schreiben ------------------------------------------------------------

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_EDIT_BYTES = 1024 * 1024
BACKUP_SUFFIX = ".bak"


def valid_name(name: str) -> bool:
    return (
        bool(name) and len(name) <= 255 and name not in (".", "..")
        and not any(c in name for c in "/\\") and all(ord(c) >= 32 for c in name)
    )


def files_back(ident: str, directory: str, message: str) -> RedirectResponse:
    return back(f"/servers/{ident}/files?dir={quote(directory)}", message)


async def file_action(request: Request, ident: str, directory: str, action: str, detail: str, coro):
    """Führt eine Dateiaktion aus, protokolliert sie und leitet zurück."""
    user = request.state.user
    try:
        await coro
    except PteroError as exc:
        audit.record(action, result="error", user_id=user.id, username=user.username, ip=ip_of(request),
                     server=ident, detail=f"{detail}: {exc}")
        return files_back(ident, directory, i18n.t("Fehlgeschlagen: {grund}", grund=exc))
    audit.record(action, user_id=user.id, username=user.username, ip=ip_of(request), server=ident, detail=detail)
    return None


@app.post("/servers/{ident}/files/upload")
async def files_upload(request: Request, ident: str, dir: str = "/"):
    user = current_user(request)
    directory = clean_path(dir)
    length = int(request.headers.get("content-length") or 0)
    if not length or length > MAX_UPLOAD_BYTES:
        return files_back(ident, directory, "Upload zu groß (max. 200 MB pro Vorgang)")
    form = await request.form(max_files=20, max_fields=5)
    if not security.csrf_ok(getattr(request.state, "csrf", None), form.get("csrf")):
        raise Forbidden("Die Sitzung ist abgelaufen oder das Formular ist ungültig. Lade die Seite neu.")
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.upload")
    uploads = [f for f in form.getlist("files") if isinstance(f, FormFile) and f.filename]
    names = [posixpath.basename(f.filename.replace("\\", "/")) for f in uploads]
    if not uploads or not all(valid_name(n) for n in names):
        return files_back(ident, directory, "Keine gültigen Dateien ausgewählt")
    for f in uploads:
        f.file.seek(0)
    failed = await file_action(request, ident, directory, "files.upload",
                               f"{directory}: {', '.join(names)}",
                               app.state.ptero.upload(ident, directory, [(n, f.file) for n, f in zip(names, uploads)]))
    for f in uploads:
        await f.close()
    return failed or files_back(ident, directory, i18n.t("{n} Datei(en) hochgeladen", n=len(names)))


@app.post("/servers/{ident}/files/mkdir")
async def files_mkdir(request: Request, ident: str, dir: str = Form("/"), name: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.create_directory")
    directory, name = clean_path(dir), name.strip()
    if not valid_name(name):
        return files_back(ident, directory, "Ungültiger Ordnername")
    failed = await file_action(request, ident, directory, "files.mkdir",
                               posixpath.join(directory, name), app.state.ptero.create_folder(ident, directory, name))
    return failed or files_back(ident, directory, i18n.t("Ordner „{name}“ angelegt", name=name))


@app.post("/servers/{ident}/files/rename")
async def files_rename(request: Request, ident: str, dir: str = Form("/"), old: str = Form(""), new: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.rename")
    directory, new = clean_path(dir), new.strip()
    if not valid_name(old) or not valid_name(new):
        return files_back(ident, directory, "Ungültiger Name")
    failed = await file_action(request, ident, directory, "files.rename",
                               f"{posixpath.join(directory, old)} → {new}",
                               app.state.ptero.rename(ident, directory, old, new))
    return failed or files_back(ident, directory, i18n.t("„{old}“ heißt jetzt „{new}“", old=old, new=new))


@app.post("/servers/{ident}/files/delete")
async def files_delete(request: Request, ident: str, dir: str = Form("/"), name: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.delete")
    directory = clean_path(dir)
    if not valid_name(name):
        return files_back(ident, directory, "Ungültiger Name")
    failed = await file_action(request, ident, directory, "files.delete",
                               posixpath.join(directory, name), app.state.ptero.delete_files(ident, directory, [name]))
    return failed or files_back(ident, directory, i18n.t("„{name}“ gelöscht", name=name))


@app.get("/servers/{ident}/files/edit", response_class=HTMLResponse)
async def files_edit(request: Request, ident: str, file: str):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.edit")
    path = clean_path(file)
    directory = posixpath.dirname(path) or "/"
    try:
        data = await app.state.ptero.read_file(ident, path)
    except PteroError as exc:
        return files_back(ident, directory, i18n.t("Datei nicht lesbar: {grund}", grund=exc))
    if len(data) > MAX_EDIT_BYTES:
        return files_back(ident, directory, "Datei ist zu groß zum Bearbeiten (max. 1 MB)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None or "\x00" in text:
        return files_back(ident, directory, "Das ist keine Textdatei und kann hier nicht bearbeitet werden")
    eol = "crlf" if "\r\n" in text else "lf"
    try:
        has_backup = any(f["is_file"] and f["name"] == posixpath.basename(path) + BACKUP_SUFFIX
                         for f in await app.state.ptero.list_files(ident, directory))
    except PteroError:
        has_backup = False
    return render(request, "file_edit.html", server=server, path=path, directory=directory,
                  content=text, eol=eol, has_backup=has_backup and not path.endswith(BACKUP_SUFFIX))


@app.post("/servers/{ident}/files/edit")
async def files_edit_save(request: Request, ident: str, file: str = Form(""), content: str = Form(""),
                          eol: str = Form("lf")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.edit")
    path = clean_path(file)
    directory = posixpath.dirname(path) or "/"
    text = content.replace("\r\n", "\n")
    if eol == "crlf":
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        return files_back(ident, directory, "Inhalt zu groß (max. 1 MB)")

    async def save_with_backup():
        # Vorherige Fassung als <datei>.bak sichern, damit sie sich wiederherstellen lässt
        previous = await app.state.ptero.read_file(ident, path)
        if previous != data:
            await app.state.ptero.write_file(ident, path + BACKUP_SUFFIX, previous)
        await app.state.ptero.write_file(ident, path, data)

    failed = await file_action(request, ident, directory, "files.edit", path, save_with_backup())
    return failed or back(f"/servers/{ident}/files/edit?file={quote(path)}",
                          "Gespeichert. Die vorherige Fassung liegt als .bak daneben.")


@app.post("/servers/{ident}/files/zip")
async def files_zip(request: Request, ident: str, dir: str = Form("/")):
    user = current_user(request)
    await check_csrf(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.download")
    directory = clean_path(dir)
    names = [n for n in (await request.form()).getlist("names") if isinstance(n, str) and valid_name(n)]
    if not names:
        return files_back(ident, directory, "Nichts ausgewählt")
    try:
        entries = await zipdownload.collect(app.state.ptero, ident, directory, names)
    except zipdownload.ZipLimit as exc:
        return files_back(ident, directory, str(exc))
    except PteroError as exc:
        return files_back(ident, directory, i18n.t("Auswahl nicht lesbar: {grund}", grund=exc))
    files = [e for e in entries if not e.is_dir]
    audit.record("files.download_zip", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail=f"{directory}: {', '.join(names)} ({len(files)} Dateien, {fmt_size(sum(e.size for e in files))})")
    base = re.sub(r"[^\w.-]+", "_", server["name"]).strip("_") or "server"
    if directory != "/":
        base += "-" + re.sub(r"[^\w.-]+", "_", posixpath.basename(directory))
    filename = f"{base}-{datetime.now().strftime('%Y-%m-%d_%H-%M')}.zip"
    return StreamingResponse(
        zipdownload.stream_zip(app.state.ptero, ident, entries, settings.timezone),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# -- Serverangaben im Manager (verändert Pterodactyl nicht) -----------------

@app.get("/servers/{ident}/settings", response_class=HTMLResponse)
async def server_settings(request: Request, ident: str):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "server.manage")
    with db.connect() as con:
        meta = con.execute("SELECT * FROM server_meta WHERE identifier = ?", (ident,)).fetchone()
    manual_password = vault.decrypt(meta["password_enc"]) if meta else None
    return render(request, "server_settings.html", server=server, meta=meta, manual_password=manual_password,
                  public_host=db.get_setting("public_host"),
                  error=None)


def upsert_meta(ident: str, user: UserContext, **fields) -> None:
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
        sets = ", ".join(f"{k} = ?" for k in fields)
        con.execute(f"UPDATE server_meta SET {sets}, updated_at = ?, updated_by = ? WHERE identifier = ?",
                    (*fields.values(), db.utcnow(), user.id, ident))


@app.post("/servers/{ident}/settings")
async def server_settings_save(request: Request, ident: str, ingame_name: str = Form(""),
                               password: str = Form(""), public_address: str = Form(""),
                               query_port: str = Form(""), tags: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "server.manage")
    port = None
    if query_port.strip():
        if not query_port.strip().isdigit() or not 1 <= int(query_port) <= 65535:
            return back(f"/servers/{ident}/settings", "Query-Port muss zwischen 1 und 65535 liegen")
        port = int(query_port)
    upsert_meta(
        ident, user,
        ingame_name=ingame_name.strip()[:120] or None,
        password_enc=vault.encrypt(password) if password else None,
        public_address=public_address.strip()[:200] or None,
        query_port=port,
        tags=", ".join(extras.parse_tags(tags)) or None,
    )
    app.state.monitor.query.forget(ident)
    audit.record("server.meta.update", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail={"ingame_name": ingame_name.strip() or None, "password_set": bool(password),
                         "public_address": public_address.strip() or None, "query_port": port,
                         "tags": extras.parse_tags(tags)})
    return back(f"/servers/{ident}/settings", "Gespeichert")


@app.post("/servers/{ident}/logo")
async def server_logo(request: Request, ident: str, logo: UploadFile = File(...)):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "server.manage")
    data = await logo.read(logos.MAX_UPLOAD_BYTES + 1)
    try:
        filename = logos.store_logo(data, settings.logo_dir)
    except logos.LogoError as exc:
        return back(f"/servers/{ident}/settings", str(exc))
    with db.connect() as con:
        old = con.execute("SELECT logo_file FROM server_meta WHERE identifier = ?", (ident,)).fetchone()
    upsert_meta(ident, user, logo_file=filename)
    logos.delete_logo(old["logo_file"] if old else None, settings.logo_dir)
    audit.record("server.logo.update", user_id=user.id, username=user.username, ip=ip_of(request), server=ident)
    return back(f"/servers/{ident}/settings", "Logo gespeichert")


@app.post("/servers/{ident}/logo/delete")
async def server_logo_delete(request: Request, ident: str):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "server.manage")
    with db.connect() as con:
        old = con.execute("SELECT logo_file FROM server_meta WHERE identifier = ?", (ident,)).fetchone()
    upsert_meta(ident, user, logo_file=None)
    logos.delete_logo(old["logo_file"] if old else None, settings.logo_dir)
    audit.record("server.logo.update", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail="entfernt")
    return back(f"/servers/{ident}/settings", "Logo entfernt")


@app.get("/logos/{filename}")
async def logo_file(request: Request, filename: str):
    current_user(request)
    if len(filename) != 37 or not filename.endswith(".webp") or not all(c in "0123456789abcdef" for c in filename[:32]):
        return Response(status_code=404)
    path = settings.logo_dir / filename
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


# -- Eigenes Konto ------------------------------------------------------------

def account_context(user: UserContext) -> dict:
    from . import mailer, notify
    with db.connect() as con:
        me = con.execute("SELECT email, email_verified, notify_email, notify_events FROM users WHERE id = ?",
                         (user.id,)).fetchone()
        pending = con.execute("SELECT email FROM tokens WHERE purpose = 'verify' AND user_id = ? AND expires_at > ?",
                              (user.id, db.utcnow())).fetchone()
    return {"themes": themes.THEMES, "me": me, "pending_email": pending["email"] if pending else None,
            "mail_ready": mailer.load_config(vault).ready, "events": notify.EVENTS, "audit_days": retention.audit_days(),
            "my_events": set((me["notify_events"] if me["notify_events"] is not None else ",".join(notify.EVENTS)).split(","))}


@app.get("/account", response_class=HTMLResponse)
async def account(request: Request):
    user = current_user(request)
    return render(request, "account.html", error=None, **account_context(user))


@app.post("/account/theme")
async def account_theme(request: Request, theme: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    chosen = themes.valid(theme)
    if chosen is None:
        return back("/account", "Unbekanntes Theme")
    with db.connect() as con:
        con.execute("UPDATE users SET theme = ? WHERE id = ?", (chosen, user.id))
    audit.record("theme.update", user_id=user.id, username=user.username, ip=ip_of(request), detail=chosen)
    name = next(t["name"] for t in themes.THEMES if t["key"] == chosen)
    return back("/account", i18n.t("Theme „{name}“ aktiviert", name=i18n.t(name)))


@app.post("/account/language")
async def account_language(request: Request, language: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    chosen = i18n.valid(language)
    if chosen is None:
        return back("/account", "Unbekannte Sprache")
    with db.connect() as con:
        con.execute("UPDATE users SET language = ? WHERE id = ?", (chosen, user.id))
    i18n.set_language(chosen)
    response = back("/account", i18n.t("Sprache: {name}", name=i18n.LANGUAGES[chosen]))
    response.set_cookie(LANG_COOKIE, chosen, max_age=365 * 24 * 3600, httponly=True, samesite="lax",
                        secure=is_https(request))
    return response


@app.post("/account/password")
async def account_password(request: Request, current: str = Form(""), new: str = Form(""), repeat: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    with db.connect() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
    if not security.verify_password(row["password_hash"], current):
        return render(request, "account.html", status=400, **account_context(user), error="Das aktuelle Passwort stimmt nicht.")
    if new != repeat:
        return render(request, "account.html", status=400, **account_context(user), error="Die neuen Passwörter stimmen nicht überein.")
    problems = security.password_problems(new, user.username)
    if problems:
        return render(request, "account.html", status=400, **account_context(user), error=i18n.t("Das Passwort braucht {list}.", list=", ".join(i18n.t(x) for x in problems)))
    with db.connect() as con:
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (security.hash_password(new), user.id))
    security.delete_user_sessions(user.id)
    audit.record("password.change", user_id=user.id, username=user.username, ip=ip_of(request))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(security.SESSION_COOKIE)
    return response


# -- Verwaltung (nur Owner) -----------------------------------------------------

def server_names() -> dict[str, str]:
    with db.connect() as con:
        return {r["identifier"]: r["name"] for r in
                con.execute("SELECT identifier, name FROM servers WHERE present = 1")}


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    require_owner(request)
    names = server_names()
    with db.connect() as con:
        users = con.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
    group_names = {g["id"]: g["name"] for g in load_groups()}
    rows = []
    for u in users:
        access = [] if u["role"] == "owner" else describe_access(load_assignments(u["id"]), group_names, names)
        rows.append({"u": u, "access": access, "last_login": fmt_dt(u["last_login_at"], settings.timezone)})
    from . import accounts, mailer
    invites = [{"hash": i["token_hash"], "role": __import__("json").loads(i["data"] or "{}").get("role", "user"),
                "email": i["email"], "created": fmt_dt(i["created_at"], settings.timezone),
                "expires": fmt_dt(i["expires_at"], settings.timezone), "creator": i["creator"]}
               for i in accounts.open_invites()]
    return render(request, "admin_users.html", rows=rows, error=None, server_count=len(names), invites=invites,
                  invite_link=getattr(request.state, "invite_link", None),
                  invite_message=getattr(request.state, "invite_message", None),
                  mail_ready=mailer.load_config(vault).ready)


@app.post("/admin/users")
async def admin_user_create(request: Request, username: str = Form(""), password: str = Form(""),
                            role: str = Form("user")):
    owner = require_owner(request)
    await check_csrf(request)
    username = username.strip()
    if not username or len(username) > 32 or not all(c.isalnum() or c in "-_." for c in username):
        return back("/admin/users", "Benutzername: 1–32 Zeichen, nur Buchstaben, Zahlen, - _ .")
    problems = security.password_problems(password, username)
    if problems:
        return back("/admin/users", i18n.t("Passwort braucht {list}", list=", ".join(i18n.t(x) for x in problems)))
    role = "owner" if role == "owner" else "user"
    try:
        with db.connect() as con:
            cur = con.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, security.hash_password(password), role, db.utcnow()),
            )
            new_id = cur.lastrowid
    except Exception:
        return back("/admin/users", "Diesen Benutzernamen gibt es schon")
    audit.record("user.create", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": username, "role": role})
    return back(f"/admin/users/{new_id}", "Benutzer angelegt. Vergib jetzt die Rechte.")


def get_user_or_404(user_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def active_owner_count() -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM users WHERE role = 'owner' AND is_active = 1").fetchone()[0]


@app.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, user_id: int):
    require_owner(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    with db.connect() as con:
        servers = con.execute("SELECT identifier, name, egg FROM servers WHERE present = 1 ORDER BY name COLLATE NOCASE").fetchall()
    groups = load_groups()
    return render(
        request, "admin_user.html", target=target, servers=servers, assignments=load_assignments(user_id),
        groups=groups, grouped=grouped_permissions(), permissions=PERMISSIONS,
        deps={p.key: list(p.requires) for p in PERMISSIONS},
        labels={p.key: i18n.t(p.label) for p in PERMISSIONS},
        group_keys={str(g["id"]): g["keys"] for g in groups},
    )


@app.post("/admin/users/{user_id}/permissions")
async def admin_user_permissions(request: Request, user_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    form = await request.form()
    known = server_names()
    group_ids = {g["id"]: g["name"] for g in load_groups()}
    assignments: dict[str, dict] = {}
    log_detail: dict[str, str] = {}
    for ident in known:
        mode = str(form.get(f"mode__{ident}", "none"))
        if mode.startswith("g:") and mode[2:].isdigit() and int(mode[2:]) in group_ids:
            assignments[ident] = {"mode": "group", "group_id": int(mode[2:]), "keys": set()}
            log_detail[known[ident]] = f"Gruppe {group_ids[int(mode[2:])]}"
        elif mode == "custom":
            keys = {k for k in BY_KEY if form.get(f"perm__{ident}__{k}")}
            if keys:
                assignments[ident] = {"mode": "custom", "group_id": None, "keys": keys}
                log_detail[known[ident]] = "individuell: " + ", ".join(sorted(with_dependencies(keys)))
    save_assignments(user_id, assignments)
    audit.record("permissions.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": target["username"], "rechte": log_detail})
    return back(f"/admin/users/{user_id}", "Rechte gespeichert")


# -- Rechtegruppen ------------------------------------------------------------------

@app.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(request: Request, edit: int | None = None):
    require_owner(request)
    groups = load_groups()
    editing = next((g for g in groups if g["id"] == edit), None)
    return render(request, "admin_groups.html", groups=groups, editing=editing, grouped=grouped_permissions(),
                  labels={p.key: i18n.t(p.label) for p in PERMISSIONS}, deps={p.key: list(p.requires) for p in PERMISSIONS})


@app.post("/admin/groups")
async def admin_group_save(request: Request, group_id: str = Form(""), name: str = Form(""),
                           description: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    name = name.strip()
    keys = {k for k in BY_KEY if form.get(f"perm__{k}")}
    gid = int(group_id) if group_id.isdigit() else None
    target = f"/admin/groups?edit={gid}" if gid else "/admin/groups"
    if not name or len(name) > 40:
        return back(target, "Name: 1–40 Zeichen")
    if not keys:
        return back(target, "Wähle mindestens ein Recht")
    try:
        gid = save_group(gid, name, description.strip()[:200], keys)
    except Exception:
        return back(target, "Diesen Gruppennamen gibt es schon")
    audit.record("group.save", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"gruppe": name, "rechte": sorted(with_dependencies(keys))})
    return back("/admin/groups", i18n.t("Gruppe „{name}“ gespeichert", name=name))


@app.post("/admin/groups/{group_id}/delete")
async def admin_group_delete(request: Request, group_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    group = next((g for g in load_groups() if g["id"] == group_id), None)
    if group is None:
        return back("/admin/groups")
    with db.connect() as con:
        con.execute("DELETE FROM perm_groups WHERE id = ?", (group_id,))
    audit.record("group.delete", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"gruppe": group["name"], "zuweisungen_entfernt": group["usage"]})
    return back("/admin/groups", i18n.t("Gruppe „{name}“ gelöscht", name=group["name"]))


@app.post("/admin/users/{user_id}/update")
async def admin_user_update(request: Request, user_id: int, role: str = Form("user"), is_active: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    role = "owner" if role == "owner" else "user"
    active = 1 if is_active else 0
    losing_owner = target["role"] == "owner" and target["is_active"] and (role != "owner" or not active)
    if losing_owner and active_owner_count() <= 1:
        return back(f"/admin/users/{user_id}", "Es muss mindestens ein aktiver Owner bleiben")
    with db.connect() as con:
        con.execute("UPDATE users SET role = ?, is_active = ? WHERE id = ?", (role, active, user_id))
    if not active:
        security.delete_user_sessions(user_id)
    audit.record("user.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": target["username"], "role": role, "active": bool(active)})
    return back(f"/admin/users/{user_id}", "Benutzer gespeichert")


@app.post("/admin/users/{user_id}/password")
async def admin_user_password(request: Request, user_id: int, password: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    problems = security.password_problems(password, target["username"])
    if problems:
        return back(f"/admin/users/{user_id}", i18n.t("Passwort braucht {list}", list=", ".join(i18n.t(x) for x in problems)))
    with db.connect() as con:
        con.execute("UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
                    (security.hash_password(password), user_id))
    security.delete_user_sessions(user_id)
    audit.record("user.password_reset", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": target["username"]})
    return back(f"/admin/users/{user_id}", "Passwort gesetzt")


@app.post("/admin/users/{user_id}/delete")
async def admin_user_delete(request: Request, user_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    if target["id"] == owner.id:
        return back(f"/admin/users/{user_id}", "Du kannst dich nicht selbst löschen")
    if target["role"] == "owner" and target["is_active"] and active_owner_count() <= 1:
        return back(f"/admin/users/{user_id}", "Es muss mindestens ein aktiver Owner bleiben")
    with db.connect() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    audit.record("user.delete", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": target["username"]})
    return back("/admin/users", "Benutzer gelöscht")


@app.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit(request: Request, page: int = 1, action: str = "", username: str = "", server: str = ""):
    require_owner(request)
    page = max(1, page)
    rows, total = audit.query(limit=50, offset=(page - 1) * 50, action=action or None,
                              username=username or None, server=server or None)
    names = server_names()
    entries = [{
        "ts": fmt_dt(r["ts"], settings.timezone, seconds=True),
        "username": r["username"] or "–", "ip": r["ip"] or "–",
        "action": i18n.t(audit.ACTION_LABELS.get(r["action"], r["action"])), "action_key": r["action"],
        "server": names.get(r["server"], r["server"]) if r["server"] else "–",
        "detail": r["detail"] or "", "result": r["result"],
    } for r in rows]
    return render(request, "admin_audit.html", entries=entries, page=page, pages=max(1, -(-total // 50)),
                  total=total, actions={k: i18n.t(v) for k, v in audit.ACTION_LABELS.items()}, f_action=action, f_username=username,
                  f_server=server, servers=names, retention_days=retention.audit_days(),
                  export_query=f"action={quote(action)}&username={quote(username)}&server={quote(server)}")


@app.get("/admin/automation", response_class=HTMLResponse)
async def admin_automation(request: Request):
    require_owner(request)
    names = server_names()
    with db.connect() as con:
        settings_rows = {r["identifier"]: r["crash_restart"] for r in con.execute("SELECT * FROM automation_settings")}
        schedules = con.execute("SELECT * FROM schedules ORDER BY time, name").fetchall()
        crashes = con.execute("SELECT * FROM crash_events ORDER BY id DESC LIMIT 20").fetchall()
        servers = con.execute("SELECT identifier, name, egg FROM servers WHERE present = 1 ORDER BY name COLLATE NOCASE").fetchall()
    sched_view = [{
        "s": s, "server": names.get(s["identifier"], s["identifier"]), "when": automation.describe(s),
        "action": i18n.t(automation.ACTIONS.get(s["action"], s["action"])),
    } for s in schedules]
    crash_view = [{"server": names.get(c["identifier"], c["identifier"]),
                   "at": fmt_dt(c["detected_at"], settings.timezone), "outcome": c["outcome"]} for c in crashes]
    return render(request, "admin_automation.html", servers=servers, crash=settings_rows, schedules=sched_view,
                  crashes=crash_view, weekdays=[i18n.t(d) for d in automation.WEEKDAYS],
                  actions={k: i18n.t(v) for k, v in automation.ACTIONS.items()})


@app.post("/admin/automation/crash")
async def admin_automation_crash(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    enabled = {k.split("__", 1)[1] for k in form.keys() if k.startswith("crash__")}
    known = server_names()
    now = db.utcnow()
    with db.connect() as con:
        for ident in known:
            con.execute(
                "INSERT INTO automation_settings (identifier, crash_restart, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(identifier) DO UPDATE SET crash_restart = excluded.crash_restart,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (ident, 1 if ident in enabled else 0, now, owner.id),
            )
    audit.record("automation.settings", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"crash_restart": sorted(known[i] for i in enabled if i in known)})
    return back("/admin/automation", "Absturz-Neustart gespeichert")


@app.post("/admin/automation/schedules")
async def admin_schedule_create(request: Request, identifier: str = Form(""), name: str = Form(""),
                                action: str = Form(""), command: str = Form(""), time_: str = Form("", alias="time"),
                                only_if_running: str = Form(""), warn_minutes: str = Form(""),
                                warn_command: str = Form(""), repeat: str = Form("weekly"), date: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    days = "".join(sorted(d for d in form.getlist("weekdays") if d in "0123456" and len(d) == 1))
    problems = []
    if identifier not in server_names():
        problems.append("Server wählen")
    if not name.strip() or len(name) > 80:
        problems.append("Name (1–80 Zeichen)")
    if action not in automation.ACTIONS:
        problems.append("Aktion wählen")
    if action == "command" and (not command.strip() or len(command) > 500 or "\n" in command):
        problems.append("Befehl angeben")
    run_at = None
    if repeat == "once":
        days = ""
        try:
            run_date = datetime.strptime(date.strip(), "%Y-%m-%d").date()
            hour, minute = (int(x) for x in time_.split(":"))
            run_dt = datetime(run_date.year, run_date.month, run_date.day, hour, minute, tzinfo=ZoneInfo(settings.timezone))
            if run_dt <= datetime.now(ZoneInfo(settings.timezone)):
                problems.append("Zeitpunkt in der Zukunft")
            run_at = run_dt.replace(tzinfo=None).isoformat(timespec="minutes")
        except ValueError:
            problems.append("Datum")
    elif not days:
        problems.append("mindestens einen Wochentag")
    try:
        hour, minute = (int(x) for x in time_.split(":"))
        assert 0 <= hour < 24 and 0 <= minute < 60
        time_ = f"{hour:02d}:{minute:02d}"
    except (ValueError, AssertionError):
        problems.append("Uhrzeit (HH:MM)")
    warn = None
    if warn_minutes.strip():
        if not warn_minutes.strip().isdigit() or not 1 <= int(warn_minutes) <= 60:
            problems.append("Vorwarnung 1–60 Minuten")
        else:
            warn = int(warn_minutes)
    if problems:
        return back("/admin/automation", i18n.t("Bitte prüfen: {list}", list=", ".join(i18n.t(x) for x in problems)))
    with db.connect() as con:
        con.execute(
            "INSERT INTO schedules (identifier, name, action, command, weekdays, time, only_if_running,"
            " warn_minutes, warn_command, enabled, created_by, created_at, run_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (identifier, name.strip(), action, command.strip() or None, days, time_, 1 if only_if_running else 0,
             warn if warn and warn_command.strip() else None, warn_command.strip() or None, owner.id, db.utcnow(),
             run_at),
        )
    audit.record("schedule.create", user_id=owner.id, username=owner.username, ip=ip_of(request), server=identifier,
                 detail={"name": name.strip(), "action": action, "time": time_, "weekdays": days, "einmalig": run_at})
    return back("/admin/automation", "Zeitplan angelegt")


@app.post("/admin/automation/schedules/{sched_id}/toggle")
async def admin_schedule_toggle(request: Request, sched_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        row = con.execute("SELECT * FROM schedules WHERE id = ?", (sched_id,)).fetchone()
        if row is None:
            return back("/admin/automation")
        con.execute("UPDATE schedules SET enabled = ? WHERE id = ?", (0 if row["enabled"] else 1, sched_id))
    audit.record("schedule.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 server=row["identifier"], detail={"name": row["name"], "enabled": not row["enabled"]})
    return back("/admin/automation", "Zeitplan " + ("pausiert" if row["enabled"] else "aktiviert"))


@app.post("/admin/automation/schedules/{sched_id}/delete")
async def admin_schedule_delete(request: Request, sched_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        row = con.execute("SELECT * FROM schedules WHERE id = ?", (sched_id,)).fetchone()
        if row is None:
            return back("/admin/automation")
        con.execute("DELETE FROM schedules WHERE id = ?", (sched_id,))
    audit.record("schedule.delete", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 server=row["identifier"], detail={"name": row["name"]})
    return back("/admin/automation", "Zeitplan gelöscht")


@app.get("/branding/{filename}")
async def branding_file(filename: str):
    """Logos des Managers, auch ohne Anmeldung (Login-Seite). Nur die aktuell eingestellten Dateien."""
    brand = load_brand()
    if filename not in (brand["logo_header"], brand["logo_login"]):
        return Response(status_code=404)
    path = settings.logo_dir / filename
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/admin/branding")
async def admin_branding_save(request: Request, brand_name: str = Form(""), show_name: str = Form(""),
                              default_theme: str = Form(""), default_language: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    name = " ".join(brand_name.split())
    if len(name) > 40:
        return back("/admin/system#erscheinungsbild", "Name: höchstens 40 Zeichen")
    old = load_brand()
    db.set_setting("brand_name", name, owner.id)
    db.set_setting("brand_show_name", "1" if show_name else "0", owner.id)
    if themes.valid(default_theme):
        db.set_setting("default_theme", default_theme, owner.id)
    if i18n.valid(default_language):
        db.set_setting("default_language", default_language, owner.id)
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"eigener_name": [old["custom"], name], "name_neben_logo": bool(show_name),
                         "standard_theme": default_theme, "standardsprache": default_language})
    return back("/admin/system#erscheinungsbild", "Erscheinungsbild gespeichert")


@app.post("/admin/branding/logo/{kind}")
async def admin_branding_logo(request: Request, kind: str, logo: UploadFile = File(...)):
    owner = require_owner(request)
    await check_csrf(request)
    spec = BRAND_LOGOS.get(kind)
    if spec is None:
        return back("/admin/system#erscheinungsbild")
    data = await logo.read(logos.MAX_UPLOAD_BYTES + 1)
    try:
        filename = logos.store_logo(data, settings.logo_dir, spec["box"])
    except logos.LogoError as exc:
        return back("/admin/system#erscheinungsbild", str(exc))
    old = db.get_setting(spec["key"])
    db.set_setting(spec["key"], filename, owner.id)
    logos.delete_logo(old, settings.logo_dir)
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail=f"Logo {spec['label']} hochgeladen")
    return back("/admin/system#erscheinungsbild", i18n.t("Logo für {ziel} gespeichert", ziel=i18n.t(spec["label"])))


@app.post("/admin/branding/logo/{kind}/delete")
async def admin_branding_logo_delete(request: Request, kind: str):
    owner = require_owner(request)
    await check_csrf(request)
    spec = BRAND_LOGOS.get(kind)
    if spec is None:
        return back("/admin/system#erscheinungsbild")
    old = db.get_setting(spec["key"])
    db.set_setting(spec["key"], "", owner.id)
    logos.delete_logo(old, settings.logo_dir)
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail=f"Logo {spec['label']} entfernt")
    return back("/admin/system#erscheinungsbild", i18n.t("Logo für {ziel} entfernt", ziel=i18n.t(spec["label"])))


@app.get("/admin/system", response_class=HTMLResponse)
async def admin_system(request: Request):
    require_owner(request)
    ptero: PteroClient = app.state.ptero
    checks = []

    async def check(label: str, coro):
        try:
            detail = await coro
            checks.append({"label": label, "ok": True, "detail": detail})
        except Exception as exc:
            checks.append({"label": label, "ok": False, "detail": str(exc) or exc.__class__.__name__})

    async def app_api():
        nodes = await ptero.list_nodes()
        return i18n.t("{n} Node(s)", n=len(nodes)) + ": " + ", ".join(f"{n.get('name')} ({n.get('fqdn')})" for n in nodes)

    async def client_api():
        servers = await ptero.list_servers()
        if not servers:
            return i18n.t("keine Server")
        res = await ptero.resources(servers[0].identifier)
        return i18n.t("Status von „{name}“ lesbar: {state}", name=servers[0].name, state=res["state"])

    await check("Application-API (nur lesen)", app_api())
    await check("Client-API (nur lesen)", client_api())
    try:
        node_list = await setup.node_checks(ptero, settings.node_overrides) if settings.configured else []
    except PteroError:
        node_list = []
    for n in node_list:
        checks.append({"label": f"Wings {n['name']}: Daemon", "ok": n["daemon_ok"],
                       "detail": f"{n['host']}:{n['daemon_port']} " + i18n.t("erreichbar" if n["daemon_ok"] else "nicht erreichbar")})
        checks.append({"label": f"Wings {n['name']}: SFTP", "ok": n["sftp_ok"],
                       "detail": f"{n['host']}:{n['sftp_port']} " + i18n.t("erreichbar" if n["sftp_ok"] else "nicht erreichbar")})
    m = app.state.monitor
    return render(request, "admin_system.html", checks=checks, last_ok=fmt_dt(m.last_ok, settings.timezone),
                  public_host=db.get_setting("public_host"), themes=themes.THEMES,
                  default_theme=themes.valid(db.get_setting("default_theme")) or themes.DEFAULT,
                  default_language=i18n.valid(db.get_setting("default_language")) or i18n.DEFAULT,
                  last_sync=fmt_dt(m.last_sync, settings.timezone), last_error=m.last_error,
                  upd=app.state.updates, upd_checked=fmt_dt(app.state.updates.checked_at, settings.timezone))


# Weitere Seiten (Statusseite, E-Mail, Einladungen, Datenumzug, ...)
from . import routes_extra  # noqa: E402,F401
