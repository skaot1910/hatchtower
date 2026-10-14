"""Web-Anwendung von Hatchtower."""
from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import re
import time
from contextlib import aclosing, asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile as FormFile

from . import (APP_NAME, __version__, audit, automation, backups, db, diagnose, extras, i18n, legal, logarchive, logos,
               mailer, retention, security, notify, serverlog, setup, statusfeed, themes, updates, zipdownload)
from .config import apply_db_values, format_proxies, is_trusted_proxy, load_settings, parse_proxies
from .panels import PANELS, make_client
from .monitor import Monitor
from .permissions import (BY_KEY, PERMISSIONS, UserContext, create_starter_groups, describe_access,
                          grouped_permissions, load_assignments, load_default_group, load_grants, load_groups,
                          save_assignments, save_default_group, save_group, starter_groups_missing,
                          users_with_default_access, with_dependencies)
from .ptero import PteroClient, PteroError, ReadOnlyViolation
from .views import SERVER_QUERY, fmt_dt, fmt_size, server_view
from .wings import console_stream

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("hatchtower")

BASE = Path(__file__).parent
settings = load_settings()
vault = security.Vault(settings.data_key)
notify.SECRET_KEY = settings.secret_key
throttle = security.LoginThrottle()
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals.update(app_name=APP_NAME, version=__version__, settings=settings, _=i18n.t,
                             languages=i18n.LANGUAGES, all_themes=themes.THEMES)
LANG_COOKIE = "hatchtower_lang"
# Cache-Parameter für CSS/JS, ohne die Versionsnummer öffentlich zu zeigen
templates.env.filters["hash_version"] = lambda v: __import__("hashlib").sha256(
    f"{v}:{settings.secret_key}".encode()).hexdigest()[:10]

# Bereiche der Owner-Navigation mit ihren Reitern: (Adresse, Name, weitere Pfad-Anfänge des Reiters)
NAV_SECTIONS = {
    "users": [
        ("/admin/users", "Benutzer", ("/admin/users/",)),
        ("/admin/users/new", "Neue Benutzer", ("/admin/users/invite",)),
        ("/admin/groups", "Rechtegruppen", ()),
        ("/admin/audit", "Audit-Log", ()),
    ],
    "appearance": [
        ("/admin/appearance", "Erscheinungsbild", ()),
        ("/admin/statuspage", "Statusseite", ()),
        ("/admin/announcements", "Ankündigungen", ()),
        ("/admin/legal", "Rechtliches", ()),
    ],
    "system": [
        ("/admin/system", "Zustand", ()),
        ("/admin/connection", "Panel", ()),
        ("/admin/network", "Netzwerk", ()),
        ("/admin/mail", "E-Mail", ()),
        ("/admin/notify", "Benachrichtigungen", ()),
        ("/admin/data", "Daten", ()),
        ("/admin/updates", "Updates", ("/admin/system/update",)),
    ],
}


def nav_tab(path: str) -> tuple[str | None, str | None]:
    """Bereich und aktiver Reiter zu einer Adresse, sonst (None, None). Der genaueste Treffer gewinnt
    (z. B. „/admin/users/new“ vor dem Pfad-Anfang „/admin/users/“)."""
    best, length = (None, None), -1
    for section, tabs in NAV_SECTIONS.items():
        for href, _label, prefixes in tabs:
            hits = ([len(href) + 1] if path == href else []) + [len(p) for p in prefixes if path.startswith(p)]
            if hits and max(hits) > length:
                best, length = (section, href), max(hits)
    return best


def load_connection() -> None:
    """Liest die in der Oberfläche gespeicherten Verbindungsdaten in die laufenden Einstellungen."""
    with db.connect() as con:
        values = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%'")}
    apply_db_values(settings, values, vault.decrypt)
    i18n.set_panel(settings.panel.name)
    mailer.LOGO_DIR, mailer.TIMEZONE = settings.logo_dir, settings.timezone


async def apply_connection() -> None:
    """Baut die Verbindung zum Panel mit den aktuellen Einstellungen neu auf (ohne Neustart)."""
    load_connection()
    old = app.state.ptero
    new = make_client(settings)
    app.state.ptero = new
    app.state.monitor.client = new
    app.state.automation.client = new
    app.state.logarchive.client = new
    app.state.automation.set_timezone(settings.timezone)
    app.state.monitor.request_sync()
    await old.close()


def record_finished_update() -> None:
    """Nach einem Update aus der Oberfläche: Ergebnis einmal ins Audit-Log schreiben."""
    state = updates.read_state(settings.data_dir)
    finished = state.get("finished")
    if state.get("state") in ("done", "failed") and finished and db.get_setting("update_audited") != finished:
        audit.record("system.update", result="ok" if state["state"] == "done" else "error", username="System",
                     ip="intern", detail={"von": state.get("from"), "auf": state.get("to") or state.get("from")})
        db.set_setting("update_audited", finished, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(settings.db_path)
    load_connection()
    app.state.ptero = make_client(settings)
    app.state.monitor = Monitor(app.state.ptero, vault, settings.poll_interval, settings.sync_interval)
    app.state.automation = automation.Automation(app.state.ptero, settings.timezone, vault)
    app.state.logarchive = logarchive.LogArchive(app.state.ptero)
    app.state.monitor.on_offline = app.state.automation.on_offline
    app.state.monitor.on_silent_restart = app.state.automation.on_silent_restart
    app.state.logarchive.on_status = lambda ident: app.state.monitor.follow(ident, 20)
    app.state.monitor.on_new_servers = announce_new_servers
    app.state.monitor.start()
    app.state.automation.start()
    app.state.logarchive.start()
    # routes_extra ist am Dateiende schon importiert; dort steht, welche Angaben öffentlich sind
    app.state.statusfeed = statusfeed.StatusFeed(vault, routes_extra.discord_status_view)
    app.state.statusfeed.start()
    app.state.updates = updates.UpdateCheck()
    app.state.updates.start()
    app.state.backups = backups.BackupManager(app.state, vault, settings)
    app.state.backups.start()
    record_finished_update()
    cleanup_task = asyncio.create_task(retention.run_forever(), name="hatchtower-retention")
    log.info("%s %s gestartet (Modus: %s, Ziel: %s %s)", APP_NAME, __version__, settings.manager_mode,
             settings.panel.name, settings.pterodactyl_url)
    yield
    cleanup_task.cancel()
    await app.state.backups.stop()
    await app.state.statusfeed.stop()
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
    trusted = is_trusted_proxy(peer, settings.trusted_proxies)
    return request.url.scheme == "https" or (trusted and request.headers.get("x-forwarded-proto") == "https")


# Zuletzt gesehener Reverse-Proxy, der nicht eingetragen ist: (IP, Zeitpunkt aus time.monotonic())
_untrusted_proxy: list = [None, 0.0]
UNTRUSTED_PROXY_HINT = 86400   # so lange bleibt der Hinweis nach der letzten solchen Anfrage stehen


def note_forwarded(request: Request) -> None:
    """Merkt sich Anfragen mit Proxy-Headern von einer IP, die nicht als Reverse-Proxy eingetragen ist.
    Dann fehlen HTTPS-Erkennung und echte Besucher-IPs, das soll der Owner erfahren."""
    if not (request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-proto")):
        return
    peer = request.client.host if request.client else None
    if peer and not is_trusted_proxy(peer, settings.trusted_proxies):
        _untrusted_proxy[:] = [peer, time.monotonic()]


def untrusted_proxy() -> str | None:
    peer, seen = _untrusted_proxy
    if peer is None or time.monotonic() - seen > UNTRUSTED_PROXY_HINT:
        return None
    if is_trusted_proxy(peer, settings.trusted_proxies):
        return None
    return peer


# Höchstgröße einer Anfrage, geprüft bevor der Inhalt gelesen wird (Schutz vor Speicher-/Plattenflutung)
BODY_LIMIT_DEFAULT = 1024 * 1024
BODY_LIMITS = [
    (re.compile(r"^/servers/[^/]+/files/upload$"), 201 * 1024 * 1024),
    (re.compile(r"^/(admin/data|setup)/import$"), 51 * 1024 * 1024),
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
    note_forwarded(request)
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
        view_mode=row["view_mode"], view_locked=bool(row["view_locked"]),
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


DISCORD_RE = re.compile(r"^https://(discord\.gg|discord\.com/invite)/[A-Za-z0-9-]{2,32}$")


def community_discord() -> str:
    url = db.get_setting("community_discord").strip()
    return url if DISCORD_RE.match(url) else ""


def render(request: Request, template: str, status: int = 200, **context) -> HTMLResponse:
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("csrf", getattr(request.state, "csrf", ""))
    context.setdefault("monitor", app.state.monitor)
    context.setdefault("flash", request.query_params.get("ok"))
    context.setdefault("brand", load_brand())
    context.setdefault("lang", i18n.current())
    context.setdefault("i18n_browser", {"locale": i18n.locale_tag(), "texts": i18n.browser_catalog(),
                                        "panel": settings.panel.name})
    context.setdefault("here", request.url.path + (f"?{request.url.query}" if request.url.query else ""))
    context.setdefault("legal_links", legal.links())
    context.setdefault("discord_url", community_discord())
    if template in ("login.html", "setup.html"):
        from . import mailer
        context.setdefault("forgot_enabled", mailer.load_config(vault).ready)
    section, tab = nav_tab(request.url.path)
    context.setdefault("nav_section", section)
    context.setdefault("nav_tab", tab)
    context.setdefault("section_tabs", NAV_SECTIONS.get(section, []))
    if context.get("user") is not None:
        context.setdefault("announcements", extras.active_announcements())
        context.setdefault("update_available", getattr(app.state, "updates", None) is not None and app.state.updates.available)
        context.setdefault("status_page_on", db.get_setting("status_enabled") == "1")
        if context["user"].is_owner:
            context.setdefault("proxy_warning", untrusted_proxy())
    user = context.get("user")
    context.setdefault("simple_view", simple_view(user))
    context.setdefault("view_locked", view_locked(user))
    context.setdefault("theme", themes.valid(getattr(user, "theme", None)) or themes.valid(db.get_setting("default_theme")) or themes.DEFAULT)
    return templates.TemplateResponse(request, template, context, status_code=status)


VIEW_MODES = ("simple", "full")


def view_choice(view_mode: str | None, view_locked: str | None) -> tuple[str | None, int]:
    """Ansicht aus einem Owner-Formular: (Modus oder None = Standard, festgelegt 0/1).
    Festlegen ohne eigenen Modus legt den aktuellen Standard der Instanz fest."""
    mode = view_mode if view_mode in VIEW_MODES else None
    if not view_locked:
        return mode, 0
    return mode or ("simple" if db.get_setting("default_view") == "simple" else "full"), 1


def view_locked(user: UserContext | None) -> bool:
    return user is not None and user.view_locked and not user.is_owner


def simple_view(user: UserContext | None) -> bool:
    """Einfache Ansicht: eigene Wahl des Benutzers, sonst der Standard der Instanz (gilt nicht für Owner)."""
    if user is None:
        return False
    if user.view_mode in VIEW_MODES:
        return user.view_mode == "simple"
    return not user.is_owner and db.get_setting("default_view") == "simple"


def back(url: str, message: str | None = None) -> RedirectResponse:
    if message:
        message = i18n.t(message)
        url, hash_, anchor = url.partition("#")      # Meldung vor den Anker, sonst kommt sie nicht an
        url += ("&" if "?" in url else "?") + "ok=" + quote(message) + hash_ + anchor
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


@app.exception_handler(RequestValidationError)
async def _invalid_request(request: Request, exc: RequestValidationError):
    """Ungültige Anfrage: nur die Namen der Felder melden, nie die gesendeten Werte (könnten API-Keys sein)."""
    fields = sorted({str(e.get("loc", ["?"])[-1]) for e in exc.errors()})
    return JSONResponse({"error": i18n.t("Ungültige Anfrage"), "fields": fields}, status_code=422)


@app.exception_handler(ReadOnlyViolation)
async def _read_only(request: Request, exc: ReadOnlyViolation):
    user = getattr(request.state, "user", None)
    audit.record("readonly.blocked", result="blocked", user_id=getattr(user, "id", None),
                 username=getattr(user, "username", None), ip=ip_of(request), detail=str(exc))
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": i18n.t("Der Manager ist im Read-Only-Modus.")}, status_code=403)
    return render(request, "error.html", status=403, title="Read-Only-Modus",
                  message="Der Manager ist im Read-Only-Modus. Schreibzugriffe auf {panel} sind gesperrt.")


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
    # Kontosperre gilt pro Konto und IP: Fehlversuche eines Fremden sperren den Besitzer nicht aus
    if row is not None and throttle.account_locked(row["id"], ip, row["password_hash"]):
        security.verify_password(None, password)
        throttle.record_failure(ip)
        audit.record("login", result="blocked", user_id=row["id"], username=row["username"], ip=ip, detail="Konto gesperrt")
        return fail("Anmeldung fehlgeschlagen.")

    ok = security.verify_password(row["password_hash"] if row else None, password)
    if not ok or row is None or not row["is_active"]:
        throttle.record_failure(ip)
        if row is not None:
            throttle.account_failure(row["id"], ip, row["password_hash"])
        audit.record("login", result="failed", user_id=row["id"] if row else None,
                     username=username[:64], ip=ip)
        return fail("Anmeldung fehlgeschlagen.")

    throttle.account_success(row["id"], ip)
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

SETUP_STEPS = [("owner", "Konto"), ("panel", "Panel"), ("client", "Client-Key"), ("nodes", "Wings"),
               ("review", "Prüfen und speichern"), ("options", "Optionen"), ("legal", "Rechtliches"), ("done", "Bericht")]
CONNECTION_STEPS = ("panel", "client", "nodes", "review")


def setup_steps(panel=None, repair: bool = False) -> list[tuple[str, str]]:
    """Schritte des Assistenten; Panels mit nur einem API-Key (Calagopus) brauchen keinen Client-Key-Schritt.
    Beim Reparieren oder Wechseln einer bestehenden Verbindung nur die Verbindungsschritte."""
    panel = panel or settings.panel
    steps = [s for s in SETUP_STEPS if not (panel.single_key and s[0] == "client")]
    if repair:
        steps = [s for s in steps if s[0] in CONNECTION_STEPS]
    return steps


def login_response(request: Request, user_id: int, username: str, target: str) -> RedirectResponse:
    token, _ = security.create_session(user_id, ip_of(request), request.headers.get("user-agent", ""))
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(security.SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=is_https(request), max_age=settings.session_max_hours * 3600)
    return response


def connection_context(step: str | None, draft: dict | None = None) -> dict:
    """Daten für die Formulare von Assistent und Verbindungsseite. Im Assistenten zeigen die Formulare den
    Entwurf (noch nicht gespeichert), sonst die gespeicherte Verbindung."""
    if draft is not None:
        cand = setup.draft_candidate(draft, vault)
        panel, url, app_key, client_key = cand.panel, cand.url, cand.app_key, cand.client_key
        report = draft["report"] if draft["checked_version"] == draft["version"] else None
    else:
        panel, url, app_key, client_key = settings.panel, settings.pterodactyl_url, settings.app_key, settings.client_key
        report = None
    return {
        "panel_type": panel.key, "panel": panel, "panels": list(PANELS.values()),
        "panel_url": url,
        "app_key_masked": setup.mask_key(app_key), "client_key_masked": setup.mask_key(client_key),
        "manager_ip": setup.manager_ip_towards(url) if url else None,
        "mode": settings.manager_mode, "public_host": db.get_setting("public_host"),
        "trusted_proxies": format_proxies(settings.trusted_proxies), "timezone": settings.timezone,
        "timezones": setup.timezone_choices(),
        "nodes": report.nodes if report else [], "report": report, "draft": draft,
        "steps": setup_steps(panel)[1:], "step": step,
    }


def draft_changes(draft: dict) -> list[dict]:
    """Was beim Speichern geändert wird (Übersicht vor dem Speichern). Keys nur maskiert."""
    cand = setup.draft_candidate(draft, vault)
    single = cand.panel.single_key
    configured = settings.configured

    def key_state(name: str, value: str, old: str) -> str:
        if not value:
            return i18n.t("fehlt")
        masked = setup.mask_key(value)
        return i18n.t("{key} (neu)", key=masked) if value != old else i18n.t("{key} (unverändert)", key=masked)

    def overrides_text(values: dict) -> str:
        return ", ".join(f"{k} → {v}" for k, v in values.items()) or i18n.t("keine")

    rows = [
        ("Panel", cand.panel.name, settings.panel.name),
        ("Adresse des Panels", setup.normalize_url(cand.url) or cand.url, settings.pterodactyl_url),
        ("API-Key" if single else "Application-API-Key", key_state("app_key", cand.app_key, settings.app_key),
         setup.mask_key(settings.app_key)),
    ]
    if not single:
        rows.append(("Client-API-Key", key_state("client_key", cand.client_key, settings.client_key),
                     setup.mask_key(settings.client_key)))
    rows.append(("Andere Wings-Adressen", overrides_text(cand.node_overrides), overrides_text(settings.node_overrides)))
    return [{"label": label, "new": new, "old": old if configured else "",
             "changed": configured and old != new and not new.endswith(i18n.t("(unverändert)"))}
            for label, new, old in rows]


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, step: str = ""):
    if not setup.users_exist():
        return owner_step(request, None, "")
    user = current_user(request)
    if not user.is_owner:
        deny(request, user, "setup")
    draft = setup.draft_get(user.id)
    panel = PANELS[draft["panel_type"]] if draft else settings.panel
    if step not in dict(setup_steps(panel)) or step == "owner":
        step = ("panel" if not (settings.pterodactyl_url and settings.app_key)
                else "client" if not settings.client_key else "nodes")
    if step in CONNECTION_STEPS:
        draft = draft or setup.draft_start(user.id, settings, vault)
        panel = PANELS[draft["panel_type"]]
        if step == "client" and panel.single_key:
            step = "nodes"
    repair = bool(draft and draft["repair"] and step in CONNECTION_STEPS)
    extra: dict = {}
    if step == "review" and draft is not None:
        extra["changes"] = draft_changes(draft)
    if step == "done":
        extra["final"] = await final_report()
    steps = setup_steps(panel, repair)
    return render(request, "setup.html", conn=connection_context(step, draft if step in CONNECTION_STEPS else None),
                  step=step, steps=steps if repair else steps[1:], repair=repair, error=None, levels=LEVEL_TEXTS,
                  areas=diagnose.AREAS, **extra)


# Stufen der Prüfergebnisse: Zeichen, Name, Erklärung
LEVEL_TEXTS = {
    diagnose.ERROR: ("✕", "Fehler", "Muss behoben werden, sonst funktioniert Hatchtower nicht. Speichern ist gesperrt."),
    diagnose.WARNING: ("!", "Warnung", "Funktioniert, aber etwas ist eingeschränkt oder riskant. Speichern ist möglich."),
    diagnose.OPTIONAL: ("○", "Optional", "Nur für eine Zusatzfunktion wichtig oder ein Hinweis. Kann so bleiben."),
    diagnose.OK: ("✓", "In Ordnung", ""),
}


async def final_report() -> dict:
    """Ergebnisbericht am Ende des Assistenten: Prüfung der gespeicherten Verbindung und die gewählten Optionen."""
    report = None
    if settings.configured:
        report = await diagnose.run(settings, diagnose.candidate_from_settings(settings))
        db.set_setting("connection_last_check", json.dumps(diagnose.summary(report)), None)
    return {
        "report": report,
        "mode": settings.manager_mode, "timezone": settings.timezone, "public_host": db.get_setting("public_host"),
        "trusted_proxies": format_proxies(settings.trusted_proxies),
        "default_view": db.get_setting("default_view") or "full",
        "legal": [page for page in legal.PAGES if legal.text(page)],
    }


def owner_step(request: Request, error: str | None, username: str) -> HTMLResponse:
    token = security.prelogin_token(settings.secret_key)
    response = render(request, "setup.html", status=400 if error else 200, step="owner", steps=setup_steps()[1:],
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
    problem = security.password_message(password, username)
    if problem:
        return fail(problem)
    with db.connect() as con:
        user_id = con.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'owner', ?)",
            (username, security.hash_password(password), db.utcnow())).lastrowid
    setup.clear_setup_token()
    audit.record("setup.owner", user_id=user_id, username=username, ip=ip)
    return login_response(request, user_id, username, "/setup?step=panel")


def parse_overrides(form) -> tuple[dict, str | None]:
    """Andere Wings-Adressen aus einem Formular (Felder override__<fqdn>)."""
    overrides = {}
    for field, value in form.items():
        if field.startswith("override__") and isinstance(value, str) and value.strip():
            value = value.strip().rstrip("/")
            if not re.fullmatch(r"(https?://)?[A-Za-z0-9.\-]+(:\d{1,5})?", value):
                return {}, i18n.t("Ungültige Adresse: {value}", value=value)
            overrides[field[len("override__"):]] = value
    return overrides, None


@app.post("/admin/connection/check")
async def connection_check(request: Request, step: str = Form("review"), panel_type: str = Form(""),
                           panel_url: str = Form(""), app_key: str = Form(""), client_key: str = Form("")):
    """Assistent: Eingaben in den Entwurf übernehmen und alles prüfen (nur lesend). Speichert nichts in der
    Datenbank und ändert nichts am Panel. Keys stehen nur im Formular, nie in der Weiterleitung."""
    owner = require_owner(request)
    await check_csrf(request)
    draft = setup.draft_for(owner.id, settings, vault)
    values: dict = {}
    if step == "panel":
        panel = PANELS.get(panel_type) or PANELS[draft["panel_type"]]
        values = {"panel_type": panel.key, "url": panel_url.strip().rstrip("/"), "app_key": app_key.strip()}
    elif step == "client":
        values = {"client_key": client_key.strip()}
    elif step == "nodes":
        overrides, problem = parse_overrides(await request.form())
        if problem:
            return back("/setup?step=nodes", problem)
        values = {"node_overrides": overrides}
    else:
        step = "review"
    dropped = setup.draft_update(draft, vault, **values)
    report = await diagnose.run(settings, setup.draft_candidate(draft, vault))
    if dropped:
        report.checks.insert(0, diagnose.Check(
            "app", diagnose.OPTIONAL, i18n.Msg("Gespeicherte Keys wurden nicht übernommen."),
            i18n.Msg("Die neue Adresse zeigt auf einen anderen Server (oder nicht mehr auf https). Bitte die Keys "
                     "neu eintragen."),
            i18n.Msg("Schutz davor, gespeicherte Keys an eine fremde Adresse zu schicken.")))
    draft["report"], draft["checked_version"] = report, draft["version"]
    audit.record("connection.check", result="ok" if report.errors == 0 else "error", user_id=owner.id,
                 username=owner.username, ip=ip_of(request),
                 detail={"schritt": step, "panel": f"{PANELS[draft['panel_type']].name} {draft['url']}",
                         "fehler": report.errors, "warnungen": report.warnings,
                         "keys_verworfen": dropped or None})
    single = PANELS[draft["panel_type"]].single_key
    if step == "panel":
        target = "panel" if report.has_errors("panel", "app") else ("nodes" if single else "client")
    elif step == "client":
        target = "panel" if report.has_errors("panel", "app") else "client" if report.has_errors("client") else "nodes"
    else:
        target = "review"
    return RedirectResponse(f"/setup?step={target}", status_code=303)


@app.post("/admin/connection/save")
async def connection_save(request: Request, confirm: str = Form("")):
    """Assistent: den geprüften Entwurf speichern – nur nach ausdrücklicher Bestätigung und ohne Fehler."""
    owner = require_owner(request)
    await check_csrf(request)
    draft = setup.draft_get(owner.id)
    if draft is None:
        return back("/setup?step=panel", "Die Eingaben sind abgelaufen (nach 30 Minuten oder einem Neustart). "
                                         "Bitte noch einmal eingeben, gespeichert wurde nichts.")
    report = draft["report"]
    if report is None or draft["checked_version"] != draft["version"]:
        return back("/setup?step=review", "Bitte zuerst prüfen. Gespeichert wurde nichts.")
    if not report.ok_to_save:
        return back("/setup?step=review", "Es gibt noch Fehler. Gespeichert wurde nichts.")
    if confirm != "1":
        return back("/setup?step=review", "Bitte bestätige, dass die Zugangsdaten gespeichert werden sollen. "
                                          "Gespeichert wurde nichts.")
    cand = setup.draft_candidate(draft, vault)
    url = setup.normalize_url(cand.url) or cand.url
    switched = settings.configured and cand.panel.key != settings.panel_type
    app_new, client_new = cand.app_key != settings.app_key, cand.effective_client_key != settings.client_key
    setup.save_values({"panel_type": cand.panel.key, "panel_url": url, "app_key": cand.app_key,
                       "client_key": cand.effective_client_key, "node_overrides": cand.node_overrides}, vault, owner.id)
    db.set_setting("connection_last_check", json.dumps(diagnose.summary(report)), owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request), detail={
        "panel": f"{cand.panel.name} {url}", "panel_gewechselt": switched or None,
        "app_key": "neu" if app_new else "unverändert",
        "client_key": None if cand.panel.single_key else ("neu" if client_new else "unverändert"),
        "wings_adressen": cand.node_overrides or "keine Abweichung", "warnungen": report.warnings,
        "bestaetigt": True})
    repair = draft["repair"]
    setup.draft_clear(owner.id)
    if repair:
        return back("/admin/connection", "Verbindung gespeichert.")
    return back("/setup?step=options", "Verbindung gespeichert.")


@app.post("/admin/connection/discard")
async def connection_discard(request: Request):
    """Assistent: Entwurf verwerfen. Die gespeicherte Verbindung bleibt, wie sie ist."""
    owner = require_owner(request)
    await check_csrf(request)
    draft = setup.draft_get(owner.id)
    setup.draft_clear(owner.id)
    if draft is not None and draft["repair"]:
        return back("/admin/connection", "Änderungen verworfen. Die bisherige Verbindung bleibt unverändert.")
    return back("/setup?step=panel", "Eingaben verworfen. Gespeichert wurde nichts.")


@app.get("/admin/connection/wizard")
async def connection_wizard(request: Request):
    """Verbindung reparieren oder anderes Panel verbinden: Assistent mit frischem Entwurf aus der gespeicherten
    Verbindung. Bis zur Bestätigung bleibt die bisherige Verbindung aktiv."""
    owner = require_owner(request)
    setup.draft_start(owner.id, settings, vault)
    return RedirectResponse("/setup?step=panel", status_code=303)


def connection_back(target: str, message: str) -> RedirectResponse:
    if not target.startswith(("/setup", "/admin/connection")):
        target = "/admin/connection"
    return back(target, message)


@app.post("/admin/connection/panel")
async def connection_panel(request: Request, panel_url: str = Form(""), panel_type: str = Form(""),
                           app_key: str = Form(""), next: str = Form("/admin/connection")):
    owner = require_owner(request)
    await check_csrf(request)
    panel = PANELS.get(panel_type) or settings.panel
    url = setup.normalize_url(panel_url)
    if url is None:
        return connection_back(next, "Bitte die Panel-Adresse vollständig angeben, z. B. https://panel.example.de")
    # Beim Wechsel des Panels passt der gespeicherte Key nicht mehr, und an einen anderen Host geht er nie
    keep = panel.key == settings.panel_type and setup.same_origin_for_keys(settings.pterodactyl_url, url)
    key = app_key.strip() or (settings.app_key if keep else "")
    key_label = "API-Key" if panel.single_key else "Application-Key"
    if not key:
        return connection_back(next, "Bitte den API-Key eintragen" if panel.single_key
                               else "Bitte den Application-API-Key eintragen")
    problem = setup.key_problem(key, panel.app_prefix)
    if problem:
        return connection_back(next, problem)
    try:
        result = await setup.test_panel(settings, panel.key, url, key)
    except PteroError as exc:
        return connection_back(next, i18n.t("Verbindung fehlgeschlagen: {grund}",
                                            grund=setup.explain(exc, key_label, panel.name)))
    values = {"panel_type": panel.key, "panel_url": url, "app_key": key}
    if panel.single_key:
        values["client_key"] = key
    elif not keep:
        values["client_key"] = ""
    setup.save_values(values, vault, owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"panel": f"{panel.name} {url}", "app_key": "neu" if app_key.strip() else "unverändert"})
    return connection_back(next.replace("step=panel", "step=nodes" if panel.single_key else "step=client"),
                           i18n.t("Verbunden: {nodes} Node(s), {servers} Server gefunden", nodes=len(result["nodes"]), servers=result["servers"]))


@app.post("/admin/connection/client")
async def connection_client(request: Request, client_key: str = Form(""), next: str = Form("/admin/connection")):
    owner = require_owner(request)
    await check_csrf(request)
    if settings.panel.single_key:
        return connection_back(next.replace("step=client", "step=nodes"),
                               i18n.t("{panel} braucht nur einen API-Key, der ist schon gespeichert"))
    key = client_key.strip() or settings.client_key
    if not key:
        return connection_back(next, "Bitte den Client-API-Key eintragen")
    problem = setup.key_problem(key, settings.panel.client_prefix)
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
    overrides, problem = parse_overrides(await request.form())
    if problem:
        return connection_back(next, problem)
    setup.save_values({"node_overrides": overrides}, vault, owner.id)
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"wings_adressen": overrides or "keine Abweichung"})
    return connection_back(next.replace("step=nodes", "step=options"), "Wings-Einstellungen gespeichert")


@app.post("/admin/connection/options")
async def connection_options(request: Request, mode: str = Form("read-only"), public_host: str | None = Form(None),
                             trusted_proxies: str | None = Form(None), timezone_: str | None = Form(None, alias="timezone"),
                             default_view: str | None = Form(None), next: str = Form("/admin/connection")):
    """Modus und, im Assistenten, auch öffentliche Adresse, Reverse-Proxy, Zeitzone und Standardansicht.
    Auf der Panel-Seite kommt nur der Modus; fehlende Felder bleiben unverändert."""
    owner = require_owner(request)
    await check_csrf(request)
    values: dict = {"mode": "read-write" if mode == "read-write" else "read-only"}
    detail: dict = {"modus": [settings.manager_mode, values["mode"]]}
    if trusted_proxies is not None:
        problem = proxies_problem(trusted_proxies)
        if problem:
            return connection_back(next, problem)
        values["trusted_proxies"] = detail["proxy"] = trusted_proxies.strip()
    if timezone_ is not None:
        if not setup.valid_timezone(timezone_.strip()):
            return connection_back(next, "Bitte eine Zeitzone aus der Liste wählen")
        values["timezone"] = detail["zeitzone"] = timezone_.strip()
    host = None
    if public_host is not None:
        host, problem = clean_public_host(public_host)
        if problem:
            return connection_back(next, problem)
        detail["public_host"] = host
    setup.save_values(values, vault, owner.id)
    if host is not None:
        db.set_setting("public_host", host, owner.id)
    if default_view in VIEW_MODES:
        db.set_setting("default_view", default_view, owner.id)
        detail["standard_ansicht"] = default_view
    await apply_connection()
    audit.record("connection.update", user_id=owner.id, username=owner.username, ip=ip_of(request), detail=detail)
    if next.startswith("/setup"):
        return back("/setup?step=legal", "Optionen gespeichert")
    return connection_back(next, "Modus gespeichert")


@app.post("/setup/legal")
async def setup_legal(request: Request, sprache: str = Form("")):
    """Letzter Schritt des Assistenten (optional): Vorlagen für Impressum und Datenschutzerklärung übernehmen.
    Die Seiten bleiben ausgeschaltet, bis der Owner die Angaben ergänzt und sie unter Rechtliches einschaltet."""
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    lang = sprache if sprache in i18n.LANGUAGES else i18n.current()
    chosen = [page for page in legal.PAGES if form.get(f"page__{page}")]
    for page in chosen:
        if not legal.text(page):                 # vorhandene Texte nie überschreiben
            db.set_setting(legal.PAGES[page] + "_text", legal.template(page, lang), owner.id)
    if not chosen:
        return back("/setup?step=done", "Einrichtung abgeschlossen. Willkommen bei Hatchtower!")
    audit.record("settings.legal", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"vorlage": chosen, "sprache": lang})
    return back("/setup?step=done", "Einrichtung abgeschlossen. Ergänze die Vorlagen unter Auftritt → Rechtliches "
                                    "und schalte sie ein.")


def proxies_problem(value: str) -> str | None:
    try:
        parse_proxies(value)
    except ValueError:
        return "Reverse-Proxy: bitte IP-Adressen oder Netze (mindestens /8) durch Komma getrennt angeben"
    return None


def clean_public_host(value: str) -> tuple[str, str | None]:
    host = value.strip().lower()
    if host and not re.fullmatch(r"[a-z0-9.\-]{1,253}", host):
        return host, "Öffentliche Adresse: nur Hostname oder IP, ohne http:// und Port"
    return host, None


@app.get("/admin/connection", response_class=HTMLResponse)
async def connection_page(request: Request, check: str = ""):
    """Panel-Seite: gespeicherte Verbindung, Prüfung auf Knopfdruck, Einstieg zum Reparieren oder Wechseln."""
    owner = require_owner(request)
    report = None
    if check and settings.configured:
        report = await diagnose.run(settings, diagnose.candidate_from_settings(settings))
        db.set_setting("connection_last_check", json.dumps(diagnose.summary(report)), owner.id)
    try:
        last = json.loads(db.get_setting("connection_last_check") or "null")
    except ValueError:
        last = None
    last_at = fmt_dt(last.get("at"), settings.timezone) if isinstance(last, dict) else ""
    # Wings-Nodes mit Adresse und Verbindungsstand (eine Anfrage ans Panel, dann direkt zu Wings)
    nodes, nodes_error = [], ""
    if report and report.nodes and all("connected" in n for n in report.nodes):
        nodes = report.nodes
    elif settings.configured:
        try:
            nodes = await diagnose.wings_overview(app.state.ptero, settings.node_overrides, settings.panel.key)
        except PteroError as exc:
            nodes_error = str(exc)
    return render(request, "connection.html", conn=connection_context(None), report=report, last_check=last,
                  last_check_at=last_at, nodes=nodes, nodes_error=nodes_error,
                  draft=setup.draft_get(owner.id), levels=LEVEL_TEXTS, areas=diagnose.AREAS)


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


@app.get("/view/{mode}")
async def switch_view(request: Request, mode: str, next: str = "/"):
    """Einfache oder vollständige Ansicht umschalten (Kopfzeile), im Konto gespeichert."""
    user = current_user(request)
    target = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    if view_locked(user):
        return RedirectResponse(target, status_code=303)
    if mode in VIEW_MODES and mode != user.view_mode:
        with db.connect() as con:
            con.execute("UPDATE users SET view_mode = ? WHERE id = ?", (mode, user.id))
        audit.record("view.update", user_id=user.id, username=user.username, ip=ip_of(request), detail=mode)
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
    down = app.state.monitor.down_since
    return render(request, "dashboard.html", servers=servers, preview=preview,
                  new_servers=pending_reviews() if user.is_owner and not preview else [],
                  fmt_down_since=fmt_dt(down, settings.timezone) if down else None)


@app.get("/api/overview")
async def api_overview(request: Request):
    user = current_user(request)
    m = app.state.monitor
    return {
        "servers": load_servers(user),
        "monitor": {"healthy": m.healthy, "error": i18n.t(m.last_error) if m.last_error else None,
                    "last_ok": fmt_dt(m.last_ok, settings.timezone),
                    "down_since": fmt_dt(m.down_since, settings.timezone) if m.down_since else None},
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
async def server_detail(request: Request, ident: str, log: str = "", log_n: int = serverlog.PAGE):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    server["favorite"] = ident in extras.favorites(user.id)
    log = log if log in serverlog.KINDS else ""
    log_entries, log_more = (serverlog.entries(ident, settings.timezone, log, log_n) if server["can_log"]
                             else ([], False))
    return render(request, "server.html", server=server, uptime=extras.availability(ident),
                  uptime_bars=extras.uptime_bars(ident, 30, settings.timezone)["bars"],
                  archive_days=retention.console_days(), log_entries=log_entries, log_more=log_more,
                  log_kind=log, log_n=min(max(log_n, 1), serverlog.MAX), log_kinds=serverlog.KIND_LABELS,
                  audit_days=retention.audit_days(), log_page=serverlog.PAGE, log_max=serverlog.MAX)


CONSOLE_RECHECK_SECONDS = 15


def session_still_allows(token: str | None, ident: str, key: str) -> bool:
    """Für lange offene Verbindungen: Sitzung und Recht erneut prüfen, ohne die Sitzung zu verlängern."""
    row = security.load_session(token, settings, touch=False)
    if row is None:
        return False
    user = UserContext(row["user_id"], row["username"], row["role"], settings.read_only, load_grants(row["user_id"]))
    return load_server(user, ident) is not None and user.can(ident, key)


@app.get("/servers/{ident}/console/stream")
async def console_sse(request: Request, ident: str):
    user = current_user(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "console.read")
    audit.record("console.open", user_id=user.id, username=user.username, ip=ip_of(request), server=ident)
    token = request.cookies.get(security.SESSION_COOKIE)
    revoked = {"type": "error", "text": i18n.t("Die Sitzung ist abgelaufen oder das Recht wurde entzogen.")}

    async def events():
        # Die Verbindung bleibt lange offen: Entzogene Rechte, gesperrte Konten und
        # abgelaufene Sitzungen beenden sie spätestens nach CONSOLE_RECHECK_SECONDS.
        checked = time.monotonic()
        try:
            async with aclosing(console_stream(app.state.ptero, ident)) as stream:
                async for item in stream:
                    if time.monotonic() - checked >= CONSOLE_RECHECK_SECONDS:
                        if not session_still_allows(token, ident, "console.read"):
                            yield f"data: {json.dumps(revoked, ensure_ascii=False)}\n\n"
                            return
                        checked = time.monotonic()
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
        f["editable"] = f["is_file"] and f["size"] <= MAX_EDIT_BYTES and looks_editable(f["name"], f.get("mimetype", ""))
    return render(request, "files.html", server=server, files=files, directory=directory, crumbs=crumbs,

                  parent=posixpath.dirname(directory) if directory != "/" else None, error=error,
                  can_download=user.can(ident, "sftp.download"), can_upload=user.can(ident, "sftp.upload"),
                  can_mkdir=user.can(ident, "sftp.create_directory"), can_rename=user.can(ident, "sftp.rename"),
                  can_delete=user.can(ident, "sftp.delete"), can_edit=user.can(ident, "sftp.edit"),
                  can_backup=user.can(ident, "backups.manage"),
                  backup_jobs=routes_backup.jobs_for_explorer(user, ident))


@app.get("/api/servers/{ident}/folders")
async def api_folders(request: Request, ident: str, dir: str = "/"):
    """Unterordner eines Ordners – für die Ordnerauswahl beim Verschieben."""
    user = current_user(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.view")
    directory = clean_path(dir)
    try:
        files = await app.state.ptero.list_files(ident, directory)
    except PteroError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    audit.record("files.list", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail=directory)
    return {"dir": directory,
            "folders": sorted((f["name"] for f in files if not f["is_file"]), key=str.lower)}


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


def power_error_text(exc: PteroError, technical: bool) -> str:
    """Verständliche Meldung, wenn Start/Stop scheitert. Die Rohmeldung des Panels (HTTP-Code, Request-ID)
    sehen nur Owner; sie steht immer im Audit-Log."""
    if exc.status == 429:
        text = i18n.t("Das Panel hat gerade zu viele Anfragen bekommen. Versuch es in einer Minute noch einmal.")
    elif exc.status == 409:
        text = i18n.t("Der Server ist gerade beschäftigt, z. B. mit einer Installation, einem Update oder einer"
                      " Übertragung. Versuch es später noch einmal.")
    elif exc.status in (401, 403):
        text = i18n.t("Hatchtower darf diesen Server im Panel gerade nicht steuern.")
    elif exc.status is None or exc.status >= 500:
        text = i18n.t("Der Server ist gerade nicht erreichbar, weil das Panel oder der Spieleserver-Rechner nicht"
                      " antwortet. Versuch es gleich noch einmal.")
    else:
        text = i18n.t("Das hat nicht geklappt. Versuch es noch einmal.")
    if technical:
        return f"{text} ({exc})"
    return text if exc.status in (409, 429) else f"{text} {i18n.t('Wenn es wieder passiert, sag dem Owner Bescheid.')}"


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
        return JSONResponse({"error": power_error_text(exc, user.is_owner)}, status_code=502)
    app.state.automation.note_power(ident, signal)
    app.state.monitor.follow(ident)
    if signal in ("start", "restart"):
        app.state.monitor.request_light_sync()   # beim Start übernommene Limits gleich mitlesen
    audit.record(f"power.{signal}", user_id=user.id, username=user.username, ip=ip_of(request), server=ident)
    return {"ok": True, "message": i18n.t("{action} für {name} an {panel} gesendet.", action=POWER_LABELS[signal], name=server["name"])}


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
# Endungen, die immer als Text gelten, auch wenn das Panel sie anders erkennt (z. B. steam_appid.txt mit Null-Byte)
TEXT_SUFFIXES = (".txt", ".cfg", ".conf", ".ini", ".json", ".json5", ".properties", ".yml", ".yaml", ".toml", ".xml",
                 ".log", ".sh", ".bat", ".lua", ".js", ".csv", ".md", ".env", ".vdf", ".lst", ".list", ".acf")
BINARY_MIME_HINTS = ("executable", "sharedlib", "octet-stream", "zip", "gzip", "x-tar", "compressed", "x-7z", "x-rar",
                     "x-bzip", "x-xz", "zstd", "sqlite", "java-archive", "x-object", "x-mach", "msdownload", "pdf",
                     "x-dosexec", "wasm")


def looks_editable(name: str, mimetype: str) -> bool:
    """Ob sich eine Datei im Editor öffnen lässt: Text laut Endung oder kein Hinweis auf eine Binärdatei."""
    if name.lower().endswith(TEXT_SUFFIXES):
        return True
    mime = (mimetype or "").lower()
    if mime.startswith(("image/", "audio/", "video/", "font/")):
        return False
    return not any(hint in mime for hint in BINARY_MIME_HINTS)
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
        if "already exists" in str(exc):          # Wings: Ziel beim Umbenennen/Verschieben belegt
            return files_back(ident, directory, i18n.t(
                "Am Ziel gibt es schon eine Datei oder einen Ordner mit diesem Namen. Es wurde nichts überschrieben;"
                " bei mehreren Einträgen können andere schon verschoben sein."))
        return files_back(ident, directory, i18n.t("Fehlgeschlagen: {grund}", grund=exc))
    audit.record(action, user_id=user.id, username=user.username, ip=ip_of(request), server=ident, detail=detail)
    return None


@app.post("/servers/{ident}/files/upload")
async def files_upload(request: Request, ident: str, dir: str = "/"):
    user = current_user(request)
    # Rechte vor dem Einlesen prüfen: Ohne Recht werden die (bis zu 200 MB) gar nicht erst angenommen
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.upload")
    directory = clean_path(dir)
    length = int(request.headers.get("content-length") or 0)
    if not length or length > MAX_UPLOAD_BYTES:
        return files_back(ident, directory, "Upload zu groß (max. 200 MB pro Vorgang)")
    form = await request.form(max_files=20, max_fields=5)
    if not security.csrf_ok(getattr(request.state, "csrf", None), form.get("csrf")):
        raise Forbidden("Die Sitzung ist abgelaufen oder das Formular ist ungültig. Lade die Seite neu.")
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


MAX_SELECTED_FILES = 500


def selected_names(form) -> list[str] | None:
    """Ausgewählte Namen aus dem Formular (ohne Doppelte); None, wenn einer ungültig ist."""
    names = list(dict.fromkeys(str(n) for n in form.getlist("names")))[:MAX_SELECTED_FILES]
    return names if names and all(valid_name(n) for n in names) else None


@app.post("/servers/{ident}/files/delete-many")
async def files_delete_many(request: Request, ident: str, dir: str = Form("/")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.delete")
    directory = clean_path(dir)
    names = selected_names(await request.form())
    if names is None:
        return files_back(ident, directory, "Nichts ausgewählt")
    failed = await file_action(request, ident, directory, "files.delete",
                               ", ".join(posixpath.join(directory, n) for n in names),
                               app.state.ptero.delete_files(ident, directory, names))
    return failed or files_back(ident, directory, i18n.t("„{name}“ gelöscht", name=names[0]) if len(names) == 1
                                else i18n.t("{n} Einträge gelöscht", n=len(names)))


@app.post("/servers/{ident}/files/move")
async def files_move(request: Request, ident: str, dir: str = Form("/"), target: str = Form("")):
    """Verschiebt ausgewählte Dateien und Ordner in einen anderen Ordner. Ein Ziel ohne „/“ am Anfang
    gilt relativ zum aktuellen Ordner („..“ geht eine Ebene hoch)."""
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.rename")
    directory = clean_path(dir)
    names = selected_names(await request.form())
    if names is None:
        return files_back(ident, directory, "Nichts ausgewählt")
    target = target.strip()
    if not target or len(target) > 1000:
        return files_back(ident, directory, "Bitte einen Zielordner angeben")
    dest = clean_path(target if target.startswith("/") else posixpath.join(directory, target))
    if dest == directory:
        return files_back(ident, directory, "Die Auswahl liegt schon in diesem Ordner")
    moves = []
    for name in names:
        source = posixpath.join(directory, name)
        if dest == source or dest.startswith(source.rstrip("/") + "/"):
            return files_back(ident, directory, i18n.t("„{name}“ kann nicht in sich selbst verschoben werden",
                                                       name=name))
        moves.append((source, posixpath.join(dest, name)))
    failed = await file_action(request, ident, directory, "files.move",
                               "; ".join(f"{a} → {b}" for a, b in moves), app.state.ptero.move(ident, moves))
    if failed:
        return failed
    text = i18n.t("„{name}“ nach {ziel} verschoben", name=names[0], ziel=dest) if len(names) == 1         else i18n.t("{n} Einträge nach {ziel} verschoben", n=len(names), ziel=dest)
    return files_back(ident, directory, text)


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
    if text is not None:
        # manche Spiele hängen Null-Bytes an (z. B. steam_appid.txt); am Ende sind sie harmlos
        text = text.rstrip("\x00")
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
    # Verwalten heißt nicht Lesen: Klarschrift nur mit dem Recht „Passwort sehen“
    can_see_password = user.can(ident, "server.password")
    return render(request, "server_settings.html", server=server, meta=meta,
                  manual_password=manual_password if can_see_password else None,
                  password_hidden=bool(manual_password) and not can_see_password,
                  public_host=db.get_setting("public_host"),
                  error=None)


def upsert_meta(ident: str, user: UserContext, **fields) -> None:
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
        sets = ", ".join(f"{k} = ?" for k in fields)
        con.execute(f"UPDATE server_meta SET {sets}, updated_at = ?, updated_by = ? WHERE identifier = ?",
                    (*fields.values(), db.utcnow(), user.id, ident))


@app.post("/servers/{ident}/settings")
async def server_settings_save(request: Request, ident: str, ingame_name: str = Form(""), game_name: str = Form(""),
                               password: str = Form(""), public_address: str = Form(""),
                               query_port: str = Form(""), tags: str = Form(""), password_clear: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "server.manage")
    port = None
    if query_port.strip():
        if not query_port.strip().isdigit() or not 1 <= int(query_port) <= 65535:
            return back(f"/servers/{ident}/settings", "Query-Port muss zwischen 1 und 65535 liegen")
        port = int(query_port)
    # Wer das Passwort nicht sieht, bekommt ein leeres Feld: leer heißt dann „unverändert“
    password_field = {}
    if password:
        password_field["password_enc"] = vault.encrypt(password)
    elif password_clear or user.can(ident, "server.password"):
        password_field["password_enc"] = None
    upsert_meta(
        ident, user,
        ingame_name=ingame_name.strip()[:120] or None,
        game_name=" ".join(game_name.split())[:60] or None,
        **password_field,
        public_address=public_address.strip()[:200] or None,
        query_port=port,
        tags=", ".join(extras.parse_tags(tags)) or None,
    )
    app.state.monitor.query.forget(ident)
    audit.record("server.meta.update", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail={"ingame_name": ingame_name.strip() or None, "game_name": game_name.strip() or None,
                         "password_set": bool(password),
                         "password_removed": not password and password_field.get("password_enc", "") is None,
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
        me = con.execute("SELECT email, email_verified, notify_email, notify_events, no_email FROM users WHERE id = ?",
                         (user.id,)).fetchone()
        pending = con.execute("SELECT email FROM tokens WHERE purpose = 'verify' AND user_id = ? AND expires_at > ?",
                              (user.id, db.utcnow())).fetchone()
    return {"themes": themes.THEMES, "me": me, "pending_email": pending["email"] if pending else None,
            "mail_ready": mailer.load_config(vault).ready, "events": notify.events_for(user.is_owner), "audit_days": retention.audit_days(),
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
    problem = security.password_message(new, user.username)
    if problem:
        return render(request, "account.html", status=400, **account_context(user), error=problem)
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
    from . import accounts, mailer
    names = server_names()
    with db.connect() as con:
        users = con.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
    group_names = {g["id"]: g["name"] for g in load_groups()}
    rows = []
    for u in users:
        access = [] if u["role"] == "owner" else describe_access(load_assignments(u["id"]), group_names, names,
                                                                    load_default_group(u["id"]))
        grants = load_grants(u["id"])
        visible = sum(1 for ident in names if "server.view" in grants.get(ident, set()))
        rows.append({"u": u, "access": access, "visible": visible,
                     "last_login": fmt_dt(u["last_login_at"], settings.timezone)})
    # Abschnitte der Liste: Owner, Benutzer, Deaktiviert
    sections = [("Owner", [r for r in rows if r["u"]["is_active"] and r["u"]["role"] == "owner"]),
                ("Benutzer", [r for r in rows if r["u"]["is_active"] and r["u"]["role"] != "owner"]),
                ("Deaktiviert", [r for r in rows if not r["u"]["is_active"]])]
    return render(request, "admin_users.html", sections=[s for s in sections if s[1]], user_count=len(rows),
                  server_count=len(names), invite_count=len(accounts.open_invites()),
                  email_required=mailer.email_required(mailer.load_config(vault)))


@app.get("/admin/users/new", response_class=HTMLResponse)
async def admin_users_new(request: Request):
    """Einladungslinks, offene Einladungen und direktes Anlegen – getrennt von der Benutzerliste."""
    require_owner(request)
    names = server_names()
    from . import accounts, mailer
    from .routes_extra import describe_invite_access, link_base
    base = link_base(request)

    def invite_link(i) -> str | None:
        raw = vault.decrypt(i["token_enc"])
        return f"{base}/invite/{raw}" if raw else None

    invites = [{"hash": i["token_hash"], "role": __import__("json").loads(i["data"] or "{}").get("role", "user"),
                "view": __import__("json").loads(i["data"] or "{}").get("view"),
                "view_locked": __import__("json").loads(i["data"] or "{}").get("view_locked"),
                "email": i["email"], "created": fmt_dt(i["created_at"], settings.timezone),
                "expires": None if i["expires_at"] == accounts.NEVER else fmt_dt(i["expires_at"], settings.timezone),
                "creator": i["creator"], "link": invite_link(i),
                "note": i["note"], "uses": accounts.uses_text(i["uses"], i["max_uses"]),
                "access": describe_invite_access(__import__("json").loads(i["data"] or "{}").get("access"))}
               for i in accounts.open_invites()]
    return render(request, "admin_users_new.html", error=None, invites=invites,
                  invite_link=getattr(request.state, "invite_link", None),
                  invite_message=getattr(request.state, "invite_message", None),
                  invite_valid=getattr(request.state, "invite_valid", None),
                  invite_days=accounts.INVITE_DAYS, invite_uses=accounts.INVITE_USES,
                  groups=load_groups(), servers=sorted(names.items(), key=lambda kv: kv[1].lower()),
                  mail_ready=mailer.load_config(vault).ready, email_optional=mailer.email_optional(),
                  email_required=mailer.email_required(mailer.load_config(vault)))


@app.post("/admin/users")
async def admin_user_create(request: Request, username: str = Form(""), password: str = Form(""),
                            role: str = Form("user"), email: str = Form(""), no_email: str = Form(""),
                            view_mode: str = Form(""), view_locked: str = Form("")):
    from . import mailer
    from .routes_extra import send_verify_mail
    owner = require_owner(request)
    await check_csrf(request)
    username = username.strip()
    if not username or len(username) > 32 or not all(c.isalnum() or c in "-_." for c in username):
        return back("/admin/users/new#anlegen", "Benutzername: 1–32 Zeichen, nur Buchstaben, Zahlen, - _ .")
    problem = security.password_message(password, username)
    if problem:
        return back("/admin/users/new#anlegen", problem)
    cfg = mailer.load_config(vault)
    email = email.strip() if cfg.ready and not no_email else ""   # ohne eingeschaltete E-Mail ist das Feld gesperrt
    if not email and not no_email and mailer.email_required(cfg):
        return back("/admin/users/new#anlegen",
                    "Bitte eine E-Mail-Adresse angeben oder „ohne E-Mail-Adresse“ wählen")
    if email and not mailer.valid_email(email):
        return back("/admin/users/new#anlegen", "Bitte eine gültige E-Mail-Adresse eingeben")
    role = "owner" if role == "owner" else "user"
    view, locked = view_choice(view_mode, view_locked)
    try:
        with db.connect() as con:
            cur = con.execute(
                "INSERT INTO users (username, password_hash, role, created_at, email, no_email, view_mode, view_locked)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (username, security.hash_password(password), role, db.utcnow(), email or None, 1 if no_email else 0,
                 view, locked),
            )
            new_id = cur.lastrowid
    except Exception:
        return back("/admin/users/new#anlegen", "Diesen Benutzernamen gibt es schon")
    audit.record("user.create", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": username, "role": role, "email": email or ("keine" if no_email else None),
                         "ansicht": (view or "standard") + (" (festgelegt)" if locked else "")})
    if email:
        error = await send_verify_mail(request, new_id, username, email)
        if error:
            return back(f"/admin/users/{new_id}", i18n.t(
                "Benutzer angelegt, aber der Bestätigungslink konnte nicht verschickt werden: {grund}", grund=error))
        return back(f"/admin/users/{new_id}", i18n.t(
            "Benutzer angelegt, Bestätigungslink an {email} geschickt. Vergib jetzt die Rechte.", email=email))
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
        servers = con.execute("SELECT s.identifier, s.name, COALESCE(m.game_name, s.egg) AS egg FROM servers s"
                              " LEFT JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1"
                              " ORDER BY s.name COLLATE NOCASE").fetchall()
        pending = con.execute("SELECT email FROM tokens WHERE purpose = 'verify' AND user_id = ? AND expires_at > ?",
                              (user_id, db.utcnow())).fetchone()
    groups = load_groups()
    for g in groups:
        g["has_write"] = any(BY_KEY[k].write for k in g["keys"] if k in BY_KEY)
    from . import mailer
    return render(
        request, "admin_user.html", target=target, servers=servers, assignments=load_assignments(user_id),
        pending_email=pending["email"] if pending else None, mail_ready=mailer.load_config(vault).ready,
        default_group=load_default_group(user_id),
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
    groups = load_groups()
    group_ids = {g["id"]: g["name"] for g in groups}
    assignments: dict[str, dict] = {}
    log_detail: dict[str, str] = {}
    default_raw = str(form.get("default_group", ""))
    default_group = int(default_raw) if default_raw.isdigit() and int(default_raw) in group_ids else None
    if default_group is not None:
        log_detail["Alle Server, auch künftige"] = f"Gruppe {group_ids[default_group]}"
    for ident in known:
        mode = str(form.get(f"mode__{ident}", "auto"))
        if mode == "none":
            assignments[ident] = {"mode": "none", "group_id": None, "keys": set()}
            log_detail[known[ident]] = "kein Zugriff"
        elif mode.startswith("g:") and mode[2:].isdigit() and int(mode[2:]) in group_ids:
            assignments[ident] = {"mode": "group", "group_id": int(mode[2:]), "keys": set()}
            log_detail[known[ident]] = f"Gruppe {group_ids[int(mode[2:])]}"
        elif mode == "custom":
            keys = {k for k in BY_KEY if form.get(f"perm__{ident}__{k}")}
            if keys:
                assignments[ident] = {"mode": "custom", "group_id": None, "keys": keys}
                log_detail[known[ident]] = "individuell: " + ", ".join(sorted(with_dependencies(keys)))
    save_assignments(user_id, assignments)
    save_default_group(user_id, default_group)
    audit.record("permissions.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"user": target["username"], "rechte": log_detail})
    group = next((g for g in groups if g["id"] == default_group), None)
    if group and any(BY_KEY[k].write for k in group["keys"] if k in BY_KEY):
        return back(f"/admin/users/{user_id}", i18n.t(
            "Rechte gespeichert. Achtung: „{gruppe}“ enthält Steuer- oder Schreibrechte – {name} kann damit auch jeden"
            " neu hinzukommenden Server sofort steuern.", gruppe=group["name"], name=target["username"]))
    return back(f"/admin/users/{user_id}", "Rechte gespeichert")


# -- Neue Server ----------------------------------------------------------------------

def announce_new_servers(idents: list[str]) -> None:
    """Pterodactyl liefert einen bisher unbekannten Server: Audit-Log, Meldung an die Owner.
    Freigegeben ist er nur für Benutzer mit „Alle Server, auch künftige“."""
    names = server_names()
    auto = users_with_default_access()
    i18n.use_default()
    for ident in idents:
        name = names.get(ident, ident)
        audit.record("server.new", username="Monitoring", ip="intern", server=ident,
                     detail={"server": name, "automatisch freigegeben": [f"{u['username']} ({u['group_name']})" for u in auto]})
        who = ", ".join(u["username"] for u in auto) or i18n.t("niemand (nur Owner)")
        notify.emit(vault, "new_server", ident, i18n.t(
            "Neuer Server „{name}“ in {panel}. Automatisch freigegeben für: {wer}. Statusseite und Neustart nach"
            " Absturz sind für ihn noch aus.", name=name, wer=who))


def pending_reviews() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT s.identifier, s.name, COALESCE(m.game_name, s.egg) AS egg FROM servers s"
                           " LEFT JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1"
                           " AND s.review_pending = 1 ORDER BY s.name COLLATE NOCASE").fetchall()
    if not rows:
        return []
    who = ", ".join(u["username"] for u in users_with_default_access())
    return [{"identifier": r["identifier"], "name": r["name"], "egg": r["egg"] or "", "auto": who} for r in rows]


@app.post("/admin/servers/{ident}/reviewed")
async def admin_server_reviewed(request: Request, ident: str):
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        changed = con.execute("UPDATE servers SET review_pending = 0 WHERE identifier = ? AND review_pending = 1",
                              (ident,)).rowcount
    if changed:
        audit.record("server.reviewed", user_id=owner.id, username=owner.username, ip=ip_of(request), server=ident)
    return back("/")


# -- Rechtegruppen ------------------------------------------------------------------

@app.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(request: Request, edit: int | None = None):
    require_owner(request)
    groups = load_groups()
    editing = next((g for g in groups if g["id"] == edit), None)
    return render(request, "admin_groups.html", groups=groups, editing=editing, grouped=grouped_permissions(),
                  starter_missing=starter_groups_missing(groups),
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


@app.post("/admin/groups/starter")
async def admin_group_starter(request: Request, target: str = Form("")):
    """Vorschlagsgruppen anlegen (Zuschauer, Spieler, Moderator), vorhandene Namen bleiben unberührt."""
    owner = require_owner(request)
    await check_csrf(request)
    created = create_starter_groups()
    if created:
        audit.record("group.save", user_id=owner.id, username=owner.username, ip=ip_of(request),
                     detail={"vorlagen": created})
    url = "/admin/users/new#einladen" if target == "users" else "/admin/groups"
    if not created:
        return back(url, "Die Vorschlagsgruppen gibt es schon")
    return back(url, i18n.t("Gruppen angelegt: {namen}. Du kannst sie jederzeit anpassen.", namen=", ".join(created)))


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
                 detail={"gruppe": group["name"], "zuweisungen_entfernt": group["usage"],
                         "alle_server_entfernt": group["default_users"]})
    return back("/admin/groups", i18n.t("Gruppe „{name}“ gelöscht", name=group["name"]))


@app.post("/admin/users/{user_id}/update")
async def admin_user_update(request: Request, user_id: int, role: str = Form("user"), note: str | None = Form(None), view_form: str = Form(""), view_mode: str = Form(""),
                            view_locked: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    role = "owner" if role == "owner" else "user"
    active = target["is_active"]          # Aktivieren/Deaktivieren nur über /active (eigener Knopf)
    losing_owner = target["role"] == "owner" and target["is_active"] and role != "owner"
    if losing_owner and active_owner_count() <= 1:
        return back(f"/admin/users/{user_id}", "Es muss mindestens ein aktiver Owner bleiben")
    note = target["note"] if note is None else (note.strip()[:500] or None)
    if not view_form:                     # Formular ohne Ansichtsfelder: unverändert lassen
        view, locked = target["view_mode"], target["view_locked"]
    else:
        view, locked = view_choice(view_mode, view_locked)
    with db.connect() as con:
        con.execute("UPDATE users SET role = ?, is_active = ?, note = ?, view_mode = ?, view_locked = ? WHERE id = ?",
                    (role, active, note, view, locked, user_id))
    detail = {"user": target["username"], "role": role}
    if note != target["note"]:
        detail["note"] = note
    if view != target["view_mode"] or locked != target["view_locked"]:
        detail["ansicht"] = (view or "standard") + (" (festgelegt)" if locked else "")
    audit.record("user.update", user_id=owner.id, username=owner.username, ip=ip_of(request), detail=detail)
    return back(f"/admin/users/{user_id}", "Benutzer gespeichert")


@app.post("/admin/users/{user_id}/active")
async def admin_user_active(request: Request, user_id: int, active: str = Form(""), next: str = Form("")):
    """Konto deaktivieren (Anmelden gesperrt, alle Sitzungen beendet) oder wieder aktivieren. Nur Owner;
    sich selbst und den letzten aktiven Owner kann man nicht deaktivieren."""
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    page = "/admin/users" if next == "list" else f"/admin/users/{user_id}"
    enable = active == "1"
    if bool(target["is_active"]) == enable:
        return back(page)
    if not enable:
        if target["id"] == owner.id:
            return back(page, "Du kannst dich nicht selbst deaktivieren")
        if target["role"] == "owner" and active_owner_count() <= 1:
            return back(page, "Es muss mindestens ein aktiver Owner bleiben")
    with db.connect() as con:
        con.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if enable else 0, user_id))
    if not enable:
        security.delete_user_sessions(user_id)
    audit.record("user.enable" if enable else "user.disable", user_id=owner.id, username=owner.username,
                 ip=ip_of(request), detail={"user": target["username"]})
    if enable:
        return back(page, i18n.t("{name} ist wieder aktiv und kann sich anmelden.", name=target["username"]))
    return back(page, i18n.t("{name} ist deaktiviert und wurde überall abgemeldet.", name=target["username"]))


@app.post("/admin/users/{user_id}/password")
async def admin_user_password(request: Request, user_id: int, password: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    target = get_user_or_404(user_id)
    if target is None:
        return back("/admin/users")
    problem = security.password_message(password, target["username"])
    if problem:
        return back(f"/admin/users/{user_id}", problem)
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
        if target["role"] != "owner":
            con.execute("DELETE FROM schedules WHERE created_by = ?", (user_id,))
            con.execute("DELETE FROM backup_jobs WHERE created_by = ?", (user_id,))
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


def require_automation(request: Request) -> tuple[UserContext, dict[str, str]]:
    """Owner oder Benutzer mit dem Recht „Automationen verwalten“. Liefert die Server, die er verwalten darf."""
    user = current_user(request)
    if not user.can_automate:
        deny(request, user, "automation.manage")
    names = server_names()
    allowed = user.automation_servers(names)
    return user, {i: n for i, n in names.items() if i in allowed}


def crash_allowed(user: UserContext, ident: str) -> bool:
    """Neustart nach Absturz umschalten: Automationen verwalten und den Server selbst starten dürfen."""
    return user.is_owner or (user.can(ident, "automation.manage") and user.can(ident, "power.start"))


def own_schedule(request: Request, user: UserContext, sched_id: int):
    """Zeitplan laden; Benutzer dürfen nur Zeitpläne ändern, die sie auch selbst anlegen dürften."""
    with db.connect() as con:
        row = con.execute("SELECT * FROM schedules WHERE id = ?", (sched_id,)).fetchone()
    if row is not None and not user.schedule_allowed(row["identifier"], row["action"]):
        deny(request, user, "automation.manage", row["identifier"])
    return row


@app.get("/admin/automation", response_class=HTMLResponse)
async def admin_automation(request: Request):
    user, names = require_automation(request)
    marks = ",".join("?" * len(names)) or "NULL"
    idents = tuple(names)
    with db.connect() as con:
        settings_rows = {r["identifier"]: r["crash_restart"] for r in con.execute("SELECT * FROM automation_settings")}
        schedules = con.execute(f"SELECT * FROM schedules WHERE identifier IN ({marks}) ORDER BY time, name",
                                idents).fetchall()
        crashes = con.execute(f"SELECT * FROM crash_events WHERE identifier IN ({marks}) ORDER BY id DESC LIMIT 20",
                              idents).fetchall()
        servers = [s for s in con.execute(
            "SELECT s.identifier, s.name, COALESCE(m.game_name, s.egg) AS egg FROM servers s"
            " LEFT JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1"
            " ORDER BY s.name COLLATE NOCASE")
            if s["identifier"] in names]
    sched_view = [{
        "s": s, "server": names.get(s["identifier"], s["identifier"]), "when": automation.describe(s),
        "action": i18n.t(automation.ACTIONS.get(s["action"], s["action"])),
        "editable": user.schedule_allowed(s["identifier"], s["action"]),
        "duration": automation.duration_text(s["duration_minutes"]) if s["action"] == "update" else "",
        "until": (fmt_dt(s["window_until"], settings.timezone) if s["window_until"] else ""),
    } for s in schedules]
    crash_view = [{"server": names.get(c["identifier"], c["identifier"]),
                   "at": fmt_dt(c["detected_at"], settings.timezone), "outcome": c["outcome"]} for c in crashes]
    # Pro Server die Aktionen, die dieser Benutzer planen darf (blendet im Formular die übrigen aus)
    allowed_actions = {s["identifier"]: " ".join(a for a in automation.ACTIONS if user.schedule_allowed(s["identifier"], a))
                       for s in servers}
    return render(request, "admin_automation.html", servers=servers, crash=settings_rows, schedules=sched_view,
                  crashes=crash_view, weekdays=[i18n.t(d) for d in automation.WEEKDAYS],
                  actions={k: i18n.t(v) for k, v in automation.ACTIONS.items()}, allowed_actions=allowed_actions,
                  crash_allowed={s["identifier"] for s in servers if crash_allowed(user, s["identifier"])},
                  durations={m: automation.duration_text(m) for m in automation.DURATIONS},
                  tz_offset=setup.utc_offset_label(settings.timezone))


@app.post("/admin/automation/crash")
async def admin_automation_crash(request: Request):
    owner, known = require_automation(request)
    await check_csrf(request)
    form = await request.form()
    enabled = {k.split("__", 1)[1] for k in form.keys() if k.startswith("crash__")}
    # Der Neustart nach Absturz startet den Server: wie bei Zeitplänen nur mit dem eigenen Startrecht
    known = {i: n for i, n in known.items() if crash_allowed(owner, i)}
    enabled &= set(known)
    now = db.utcnow()
    with db.connect() as con:
        # Nur Server, die dieser Benutzer verwalten darf – alle anderen bleiben unverändert
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
                                warn_command: str = Form(""), repeat: str = Form("weekly"), date: str = Form(""),
                                duration: str = Form("60")):
    owner, known = require_automation(request)
    await check_csrf(request)
    form = await request.form()
    days = "".join(sorted(d for d in form.getlist("weekdays") if d in "0123456" and len(d) == 1))
    problems = []
    if identifier not in known:
        problems.append("Server wählen")
    if not name.strip() or len(name) > 80:
        problems.append("Name (1–80 Zeichen)")
    if action not in automation.ACTIONS:
        problems.append("Aktion wählen")
    elif identifier in known and not owner.schedule_allowed(identifier, action):
        problems.append("eine Aktion, für die du das Recht hast")
    if warn_command.strip() and identifier in known and not owner.is_owner and not owner.can(identifier, "console.command"):
        problems.append("Vorwarn-Befehl nur mit dem Recht „Befehle senden“")
    if action == "command" and (not command.strip() or len(command) > 500 or "\n" in command):
        problems.append("Befehl angeben")
    minutes = None
    if action == "update":
        minutes = int(duration) if duration.isdigit() and int(duration) in automation.DURATIONS else None
        if minutes is None:
            problems.append("Dauer des Update-Fensters")
        command, only_if_running, warn_minutes, warn_command = "", "", "", ""
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
            " warn_minutes, warn_command, enabled, created_by, created_at, run_at, duration_minutes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (identifier, name.strip(), action, command.strip() or None, days, time_, 1 if only_if_running else 0,
             warn if warn and warn_command.strip() else None, warn_command.strip() or None, owner.id, db.utcnow(),
             run_at, minutes),
        )
    audit.record("schedule.create", user_id=owner.id, username=owner.username, ip=ip_of(request), server=identifier,
                 detail={"name": name.strip(), "action": action, "time": time_, "weekdays": days, "einmalig": run_at,
                         **({"dauer_min": minutes} if minutes else {})})
    return back("/admin/automation", "Zeitplan angelegt")


@app.post("/admin/automation/schedules/{sched_id}/toggle")
async def admin_schedule_toggle(request: Request, sched_id: int):
    owner, _known = require_automation(request)
    await check_csrf(request)
    row = own_schedule(request, owner, sched_id)
    if row is None:
        return back("/admin/automation")
    with db.connect() as con:
        con.execute("UPDATE schedules SET enabled = ? WHERE id = ?", (0 if row["enabled"] else 1, sched_id))
    audit.record("schedule.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 server=row["identifier"], detail={"name": row["name"], "enabled": not row["enabled"]})
    return back("/admin/automation", "Zeitplan " + ("pausiert" if row["enabled"] else "aktiviert"))


@app.post("/admin/automation/schedules/{sched_id}/delete")
async def admin_schedule_delete(request: Request, sched_id: int):
    owner, _known = require_automation(request)
    await check_csrf(request)
    row = own_schedule(request, owner, sched_id)
    if row is None:
        return back("/admin/automation")
    with db.connect() as con:
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
                              default_theme: str = Form(""), discord: str = Form(""),
                              default_view: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    name = " ".join(brand_name.split())
    if len(name) > 40:
        return back("/admin/appearance", "Name: höchstens 40 Zeichen")
    old = load_brand()
    db.set_setting("brand_name", name, owner.id)
    db.set_setting("brand_show_name", "1" if show_name else "0", owner.id)
    if themes.valid(default_theme):
        db.set_setting("default_theme", default_theme, owner.id)
    if default_view in VIEW_MODES:
        db.set_setting("default_view", default_view, owner.id)
    discord = discord.strip()
    if discord and not discord.startswith("http"):
        discord = "https://discord.gg/" + discord      # nur der Einladungscode eingegeben
    if discord and not DISCORD_RE.match(discord):
        return back("/admin/appearance",
                    "Discord: bitte einen Einladungslink wie https://discord.gg/abc123 oder nur den Code eingeben")
    db.set_setting("community_discord", discord, owner.id)
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"eigener_name": [old["custom"], name], "name_neben_logo": bool(show_name),
                         "standard_theme": default_theme, "standard_ansicht": default_view})
    return back("/admin/appearance", "Erscheinungsbild gespeichert")


@app.post("/admin/branding/logo/{kind}")
async def admin_branding_logo(request: Request, kind: str, logo: UploadFile = File(...)):
    owner = require_owner(request)
    await check_csrf(request)
    spec = BRAND_LOGOS.get(kind)
    if spec is None:
        return back("/admin/appearance")
    data = await logo.read(logos.MAX_UPLOAD_BYTES + 1)
    try:
        filename = logos.store_logo(data, settings.logo_dir, spec["box"])
    except logos.LogoError as exc:
        return back("/admin/appearance", str(exc))
    old = db.get_setting(spec["key"])
    db.set_setting(spec["key"], filename, owner.id)
    logos.delete_logo(old, settings.logo_dir)
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail=f"Logo {spec['label']} hochgeladen")
    return back("/admin/appearance", i18n.t("Logo für {ziel} gespeichert", ziel=i18n.t(spec["label"])))


@app.post("/admin/branding/logo/{kind}/delete")
async def admin_branding_logo_delete(request: Request, kind: str):
    owner = require_owner(request)
    await check_csrf(request)
    spec = BRAND_LOGOS.get(kind)
    if spec is None:
        return back("/admin/appearance")
    old = db.get_setting(spec["key"])
    db.set_setting(spec["key"], "", owner.id)
    logos.delete_logo(old, settings.logo_dir)
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail=f"Logo {spec['label']} entfernt")
    return back("/admin/appearance", i18n.t("Logo für {ziel} entfernt", ziel=i18n.t(spec["label"])))


@app.get("/admin/system", response_class=HTMLResponse)
async def admin_system(request: Request):
    require_owner(request)
    record_finished_update()
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

    single = settings.panel.single_key
    await check("Admin-API (nur lesen)" if single else "Application-API (nur lesen)", app_api())
    await check("Server-API (nur lesen)" if single else "Client-API (nur lesen)", client_api())
    try:
        node_list = await setup.node_checks(ptero, settings.node_overrides) if settings.configured else []
    except PteroError:
        node_list = []
    for n in node_list:
        checks.append({"label": f"Wings {n['name']}", "ok": n["daemon_ok"],
                       "detail": f"{n['host']}:{n['daemon_port']} " + i18n.t("erreichbar" if n["daemon_ok"] else "nicht erreichbar")})
    m = app.state.monitor
    return render(request, "admin_system.html", checks=checks, last_ok=fmt_dt(m.last_ok, settings.timezone),
                  wings_total=len(node_list), wings_ok=sum(1 for n in node_list if n["daemon_ok"]),
                  public_host=db.get_setting("public_host"),
                  last_sync=fmt_dt(m.last_sync, settings.timezone), last_error=m.last_error)


@app.get("/admin/updates", response_class=HTMLResponse)
async def admin_updates(request: Request):
    require_owner(request)
    record_finished_update()
    return render(request, "admin_updates.html",
                  upd=app.state.updates, upd_checked=fmt_dt(app.state.updates.checked_at, settings.timezone),
                  upd_cooldown=app.state.updates.cooldown_left(), upd_helper=updates.helper_installed(),
                  upd_running=updates.read_state(settings.data_dir).get("state") in ("requested", "running"))


@app.get("/admin/appearance", response_class=HTMLResponse)
async def admin_appearance(request: Request):
    require_owner(request)
    return render(request, "admin_appearance.html", themes=themes.THEMES,
                  default_theme=themes.valid(db.get_setting("default_theme")) or themes.DEFAULT,
                  default_view=db.get_setting("default_view", "full"),
                  default_language=i18n.valid(db.get_setting("default_language")) or i18n.DEFAULT,
                  timezones=setup.timezone_choices(), timezone=settings.timezone)


@app.post("/admin/appearance/locale")
async def admin_appearance_locale(request: Request, default_language: str = Form(""),
                                  timezone_: str = Form("", alias="timezone")):
    owner = require_owner(request)
    await check_csrf(request)
    zone = timezone_.strip()
    if not setup.valid_timezone(zone):
        return back("/admin/appearance#sprache-zeit", "Bitte eine Zeitzone aus der Liste wählen")
    if i18n.valid(default_language):
        db.set_setting("default_language", default_language, owner.id)
    old_zone = settings.timezone
    setup.save_values({"timezone": zone}, vault, owner.id)
    await apply_connection()
    audit.record("settings.update", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"standardsprache": default_language, "zeitzone": [old_zone, zone]})
    return back("/admin/appearance#sprache-zeit", "Sprache und Zeit gespeichert")


# Weitere Seiten (Statusseite, E-Mail, Einladungen, Datenumzug, ...)
from . import routes_extra  # noqa: E402,F401
from . import routes_backup  # noqa: E402,F401
