"""Weitere Seiten von Hatchtower.

Statusseite, Impressum/Datenschutz, Lizenzen, E-Mail (Passwort vergessen, Bestätigung),
Einladungen, Benachrichtigungen, Aufbewahrung, Ankündigungen, Datenumzug, Audit-Export,
Konsolen-Archiv, Favoriten und Datei-Wiederherstellung.

Wird am Ende von main.py importiert und nutzt dessen Helfer.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import posixpath
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import (APP_NAME, __version__, accounts, audit, db, downloads, extras, i18n, legal, logarchive, mailer, notify, retention,
               security, setup, statusfeed, themes, transfer)
from .main import (BACKUP_SUFFIX, LoginRequired, announce_new_user, app, apply_connection, back, check_csrf,
                   check_csrf_header, clean_path,
                   current_user, file_action, get_server_or_deny, ip_of, is_https, load_brand,
                   login_response, render, require, require_owner, server_names, settings, throttle, vault)
from .config import is_trusted_proxy
from .panels import PANELS
from .permissions import BY_KEY, load_grants, load_groups, save_assignments, save_default_group
from .views import STATE_LABELS, fmt_dt, is_stale, monogram



_background: set = set()


def ts_text(ts: int) -> str:
    return fmt_dt(datetime.fromtimestamp(ts, timezone.utc).isoformat(), settings.timezone, seconds=True)


def mail_config():
    return mailer.load_config(vault)


def link_base(request: Request) -> str:
    """Basisadresse für Links. Für E-Mails immer die fest eingestellte Adresse (Schutz vor gefälschtem Host)."""
    cfg = mail_config()
    if cfg.base_url:
        return cfg.base_url
    scheme = "https" if is_https(request) else "http"
    return f"{scheme}://{request.headers.get('host', 'localhost')}"


def prelogin_page(request: Request, template: str, status: int = 200, **context) -> HTMLResponse:
    token = security.prelogin_token(settings.secret_key)
    response = render(request, template, status=status, prelogin=token, **context)
    response.set_cookie(security.PRELOGIN_COOKIE, token, httponly=True, samesite="strict",
                        secure=is_https(request), max_age=1800)
    return response


def prelogin_ok(request: Request, value: str) -> bool:
    return security.prelogin_valid(settings.secret_key, request.cookies.get(security.PRELOGIN_COOKIE), value)


def password_error(password: str, repeat: str, username: str) -> str | None:
    if password != repeat:
        return "Die Passwörter stimmen nicht überein."
    return security.password_message(password, username)


def optional_user(request: Request):
    """Angemeldeten Benutzer laden, falls vorhanden (für öffentliche Seiten mit normaler Kopfzeile)."""
    try:
        return current_user(request)
    except LoginRequired:
        return None


def not_found(request: Request) -> HTMLResponse:
    return render(request, "error.html", status=404, title="Nicht gefunden",
                  message="Diese Seite gibt es nicht oder sie ist ausgeschaltet.")


def owner_audit(request: Request, owner, action: str, **kw) -> None:
    audit.record(action, user_id=owner.id, username=owner.username, ip=ip_of(request), **kw)


# == Öffentliche Seiten ======================================================================

# Was die Statusseite zeigen darf (gilt für alle Server gleich)
STATUS_FIELDS = {"name": "Servername aus {panel}", "ingame": "Ingame-Servername", "status": "Status",
                 "started": "Gestartet/Gestoppt (Uhrzeit)", "address": "Adresse (IP und Port)",
                 "password": "Serverpasswort",
                 "uptime": "Verfügbarkeitsbalken (30 Tage)"}
STATUS_ORDER = {"running": 0, "starting": 1, "stopping": 2, "offline": 3, "suspended": 4, "unknown": 5}
STATUS_DEFAULT = "name,status,address,uptime"
STATUS_DAYS = 30
# In Discord eigene Auswahl; Verfügbarkeitsbalken gibt es dort nicht
DISCORD_FIELDS = {k: v for k, v in STATUS_FIELDS.items() if k != "uptime"}
DISCORD_DEFAULT = "name,status,address"


def status_fields() -> set[str]:
    raw = db.get_setting("status_fields", STATUS_DEFAULT)
    return {f for f in raw.split(",") if f in STATUS_FIELDS}


def status_servers(fields: set[str] | None = None, idents: set[str] | None = None) -> list[dict]:
    """Nur die eingeschalteten Angaben verlassen den Server – auch in status.json und Discord.
    Ohne idents: die Server der Statusseite, sonst genau diese (Auswahl eines Discord-Kanals)."""
    host = db.get_setting("public_host")
    fields = status_fields() if fields is None else fields
    if idents is None:
        where, params = "m.on_status = 1", ()
    else:
        where, params = f"s.identifier IN ({','.join('?' * len(idents)) or 'NULL'})", tuple(idents)
    with db.connect() as con:
        rows = con.execute(
            "SELECT s.identifier, s.name, s.egg, s.primary_port, s.suspended, s.startup_name, s.startup_password_enc,"
            " st.state, st.updated_at, st.query_name, st.last_started_at, st.last_stopped_at, m.logo_file, m.public_address,"
            " m.ingame_name, m.game_name, m.password_enc"
            " FROM servers s LEFT JOIN server_state st ON st.identifier = s.identifier"
            f" LEFT JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1 AND {where}"
            " ORDER BY s.name COLLATE NOCASE", params).fetchall()
    result = []
    for r in rows:
        state = "suspended" if r["suspended"] else ("unknown" if is_stale(r["updated_at"]) else (r["state"] or "unknown"))
        running = state in ("running", "starting")
        game = r["game_name"] or r["egg"] or ""
        item = {"identifier": r["identifier"], "egg": game,
                "monogram": monogram(r["name"] if "name" in fields else (game or "?")),
                "logo": f"/status/logo/{r['identifier']}" if r["logo_file"] else None}
        if "name" in fields:
            item["name"] = r["name"]
        if "ingame" in fields:
            item["ingame"] = r["ingame_name"] or r["query_name"] or r["startup_name"] or None
        if "status" in fields:
            item["state"] = state
            item["state_label"] = i18n.t({**STATE_LABELS, "suspended": "gesperrt"}.get(state, state))
        if "started" in fields:
            when = r["last_started_at"] if running else r["last_stopped_at"]
            item["started"] = (i18n.t("Gestartet: {zeit}" if running else "Gestoppt: {zeit}",
                                      zeit=fmt_dt(when, settings.timezone)) if when else None)
        if "password" in fields:
            item["password"] = vault.decrypt(r["password_enc"]) or vault.decrypt(r["startup_password_enc"]) or None
        if "address" in fields:
            item["address"] = r["public_address"] or (f"{host}:{r['primary_port']}" if host and r["primary_port"] else None)
        item["_sort"] = (STATUS_ORDER.get(state, 5), r["name"].lower())
        result.append(item)
    # Sortierung: erst nach Onlinestatus, dann nach Name (auch wenn der Status ausgeblendet ist)
    result.sort(key=lambda x: x.pop("_sort"))
    return result


def feed_fields(feed) -> set[str]:
    return {f for f in (feed["fields"] or "").split(",") if f in DISCORD_FIELDS}


def status_enabled() -> bool:
    return db.get_setting("status_enabled") == "1"


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    if not status_enabled():
        return not_found(request)
    # Angemeldete sehen dieselbe öffentliche Seite, nur mit einem Link zurück zur Übersicht
    logged_in = optional_user(request) is not None
    servers = status_servers()
    fields = status_fields()
    add_status_history(servers, fields)
    return render(request, "status.html", user=None, logged_in=logged_in, servers=servers,
                  title=db.get_setting("status_title") or load_brand()["name"],
                  public_announcements=extras.active_announcements(public_only=True),
                  fields=fields, summary=status_summary(servers, fields))


def status_summary(servers: list[dict], fields: set[str]) -> dict | None:
    """Kopfzeile der Statusseite: wie viele Server laufen. Nur, wenn der Status gezeigt wird."""
    if "status" not in fields or not servers:
        return None
    online = sum(1 for s in servers if s.get("state") in ("running", "starting"))
    unknown = sum(1 for s in servers if s.get("state") == "unknown")
    return {"online": online, "total": len(servers), "all": online == len(servers), "unknown": unknown == len(servers)}


def add_status_history(servers: list[dict], fields: set[str]) -> None:
    """Verfügbarkeitsbalken je Server (nur, wenn eingeschaltet)."""
    for s in servers:
        if "uptime" in fields:
            view = extras.uptime_bars(s["identifier"], STATUS_DAYS, settings.timezone)
            s["bars"], s["uptime_pct"] = view["bars"], view["pct"]


def discord_status_view(feed) -> tuple[str, list[dict], set[str], str, str]:
    """Die Nachricht eines Discord-Kanals: seine Server und Angaben, dazu die Links zur Statusseite
    (nur wenn eingeschaltet) und zur Übersicht der Verwaltung."""
    fields = feed_fields(feed)
    servers = status_servers(fields, statusfeed.feed_servers(feed["id"]))
    base = db.get_setting("status_discord_link").removesuffix("/status")    # beim Speichern gemerkt
    link = f"{base}/status" if base and status_enabled() else ""
    title = feed["title"] or db.get_setting("status_title") or load_brand()["name"]
    return title, servers, fields, link, f"{base}/" if base else ""


# Installed app (Android): Chrome paints the bar behind the gesture navigation with the manifest's theme_color
# (white without one) and only updates it from the same manifest URL. Manifests are fetched without cookies, so the
# URL stays fixed and the colours follow the instance's default theme. Same values as the theme-color meta in base.html.
MANIFEST_COLOURS = {"hell": ("#efebe9", "#f5f3f2"), "nacht": ("#141215", "#101012")}


@app.get("/manifest.webmanifest")
async def web_manifest():
    theme = themes.valid(db.get_setting("default_theme")) or themes.DEFAULT
    theme_colour, background = MANIFEST_COLOURS.get(theme, MANIFEST_COLOURS["nacht"])
    brand = load_brand()
    # Chrome only offers "Install app" with both a 192 px and a 512 px icon
    icons = [{"src": f"/static/{name}-{size}.png?v={__version__}", "type": "image/png", "sizes": f"{size}x{size}",
              "purpose": purpose}
             for name, purpose in (("icon", "any"), ("icon-maskable", "maskable")) for size in (192, 512)]
    body = {"id": "/", "name": brand["name"], "short_name": brand["name"][:30], "start_url": "/", "scope": "/",
            "display": "standalone", "theme_color": theme_colour, "background_color": background, "icons": icons}
    return JSONResponse(body, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
async def service_worker():
    # Served from the root so its scope covers the whole site (a worker under /static/ would only control /static/)
    return FileResponse(Path(__file__).parent / "static" / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/status.json")
async def status_json(request: Request):
    if not status_enabled():
        return JSONResponse({"error": "disabled"}, status_code=404)
    return {"servers": status_servers()}


@app.get("/status/logo/{ident}")
async def status_logo(ident: str):
    if not status_enabled():
        return Response(status_code=404)
    with db.connect() as con:
        row = con.execute("SELECT logo_file FROM server_meta WHERE identifier = ? AND on_status = 1",
                          (ident,)).fetchone()
    if row is None or not row["logo_file"]:
        return Response(status_code=404)
    path = settings.logo_dir / row["logo_file"]
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/impressum", response_class=HTMLResponse)
@app.get("/datenschutz", response_class=HTMLResponse)
async def legal_page(request: Request):
    optional_user(request)
    page = request.url.path.strip("/")
    if not legal.enabled(page) or not legal.text(page).strip():
        return not_found(request)
    title = "Impressum" if page == "impressum" else "Datenschutzerklärung"
    return render(request, "legal.html", page_title=title, content=legal.to_html(legal.text(page)))


def third_party() -> list[dict]:
    result = []
    for dist in metadata.distributions():
        meta = dist.metadata
        name = meta.get("Name")
        if not name or name.lower() in ("pip", "setuptools", "wheel", "hatchtower"):
            continue
        lic = meta.get("License-Expression") or ""
        if not lic:
            classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
            lic = ", ".join(classifiers) or (meta.get("License") or "").split("\n")[0][:80]
        result.append({"name": name, "version": dist.version, "license": lic or "–",
                       "url": meta.get("Home-page") or next((u.split(",", 1)[1].strip() for u in
                                                             (meta.get_all("Project-URL") or [])), "")})
    return sorted(result, key=lambda d: d["name"].lower())


LICENSE_FILE = Path(__file__).resolve().parents[2] / "THIRD_PARTY_LICENSES.txt"


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    current_user(request)   # Paketliste mit Versionen nur für Angemeldete
    import platform
    import sqlite3
    return render(request, "about.html", packages=third_party(), python_version=platform.python_version(),
                  sqlite_version=sqlite3.sqlite_version, license_file=LICENSE_FILE.is_file(),
                  supported_panels=list(PANELS.values()), source_url=source_url())


def source_url() -> str:
    """Quellcode genau dieser Version (AGPL-3.0 §13: Nutzer über das Netz bekommen Zugang zum Quellcode)."""
    import os
    from . import __version__
    repo = os.environ.get("HATCHTOWER_REPO", "").strip() or "skaot1910/hatchtower"
    return f"https://github.com/{repo}/tree/v{__version__}"


@app.get("/about/licenses.txt")
async def about_licenses(request: Request):
    current_user(request)
    if not LICENSE_FILE.is_file():
        return Response(status_code=404)
    return FileResponse(LICENSE_FILE, media_type="text/plain; charset=utf-8")


# == Passwort vergessen, E-Mail bestätigen ===================================================

@app.get("/forgot", response_class=HTMLResponse)
async def forgot_page(request: Request):
    if not mail_config().ready:
        return not_found(request)
    return prelogin_page(request, "forgot.html", sent=False, error=None)


@app.post("/forgot")
async def forgot_send(request: Request, login: str = Form(""), prelogin: str = Form("")):
    cfg = mail_config()
    if not cfg.ready:
        return not_found(request)
    ip = ip_of(request)
    if not prelogin_ok(request, prelogin):
        return prelogin_page(request, "forgot.html", 400, sent=False, error="Das Formular ist abgelaufen. Versuch es noch einmal.")
    if throttle.ip_blocked(ip):
        return prelogin_page(request, "forgot.html", 429, sent=False, error="Zu viele Versuche. Warte ein paar Minuten.")
    throttle.record_failure(ip)   # zählt jede Anfrage, damit niemand massenhaft Mails auslöst
    value = login.strip()
    with db.connect() as con:
        row = con.execute("SELECT * FROM users WHERE is_active = 1 AND email_verified = 1 AND email IS NOT NULL"
                          " AND (username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE)", (value, value)).fetchone()
    if row is not None:
        raw = accounts.create("reset", user_id=row["id"])
        page_lang = i18n.current()
        i18n.set_language(row["language"] or page_lang)
        brand = mailer.brand_name()
        mail = mailer.Mail(
            subject=i18n.t("Passwort zurücksetzen – {brand}", brand=brand),
            heading=i18n.t("Passwort zurücksetzen"),
            name=row["username"],
            paragraphs=[i18n.t("wir haben eine Anfrage erhalten, das Passwort für dein Konto „{name}“ bei {brand}"
                               " zurückzusetzen. Über den folgenden Knopf legst du ein neues Passwort fest.",
                               name=row["username"], brand=brand)],
            button=(i18n.t("Neues Passwort festlegen"), f"{cfg.base_url}/reset/{raw}"),
            facts=[(i18n.t("Konto"), row["username"]), (i18n.t("Angefordert am"), mailer.now_text()),
                   (i18n.t("Gültig"), i18n.t("1 Stunde, nur einmal verwendbar"))],
            note=i18n.t("Du hast das nicht angefordert? Dann kannst du diese E-Mail ignorieren – dein Passwort bleibt"
                        " unverändert. Gib den Link nicht weiter."))
        i18n.set_language(page_lang)

        async def deliver(user_id=row["id"], username=row["username"], email=row["email"]):
            try:
                await mailer.send(cfg, email, mail.subject, mail)
                audit.record("password.reset_request", user_id=user_id, username=username, ip=ip)
            except mailer.MailError as exc:
                audit.record("password.reset_request", result="error", user_id=user_id, username=username,
                             ip=ip, detail=str(exc))

        # Im Hintergrund verschicken: Die Antwortzeit verrät so nicht, ob es das Konto gibt
        import asyncio
        task = asyncio.get_running_loop().create_task(deliver())
        _background.add(task)
        task.add_done_callback(_background.discard)
    return prelogin_page(request, "forgot.html", sent=True, error=None)


@app.get("/reset/{token}", response_class=HTMLResponse)
async def reset_page(request: Request, token: str):
    row = accounts.get(token, "reset")
    if row is None:
        return render(request, "error.html", status=410, title="Link abgelaufen",
                      message="Dieser Link ist abgelaufen oder wurde schon benutzt. Fordere einen neuen an.")
    return prelogin_page(request, "reset.html", token=token, error=None)


@app.post("/reset/{token}")
async def reset_save(request: Request, token: str, password: str = Form(""), repeat: str = Form(""),
                     prelogin: str = Form("")):
    row = accounts.get(token, "reset")
    if row is None:
        return RedirectResponse(f"/reset/{quote(token)}", status_code=303)
    if not prelogin_ok(request, prelogin):
        return prelogin_page(request, "reset.html", 400, token=token, error="Das Formular ist abgelaufen. Versuch es noch einmal.")
    with db.connect() as con:
        user = con.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    error = password_error(password, repeat, user["username"])
    if error:
        return prelogin_page(request, "reset.html", 400, token=token, error=error)
    with db.connect() as con:
        con.execute("UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
                    (security.hash_password(password), user["id"]))
    accounts.consume(token)
    security.delete_user_sessions(user["id"])
    audit.record("password.reset_mail", user_id=user["id"], username=user["username"], ip=ip_of(request))
    return back("/login", "Passwort geändert. Du kannst dich jetzt anmelden.")


@app.get("/verify/{token}")
async def verify_email(request: Request, token: str):
    row = accounts.get(token, "verify")
    if row is None:
        return render(request, "error.html", status=410, title="Link abgelaufen",
                      message="Dieser Link ist abgelaufen oder wurde schon benutzt.")
    with db.connect() as con:
        con.execute("UPDATE users SET email = ?, email_verified = 1 WHERE id = ?", (row["email"], row["user_id"]))
        user = con.execute("SELECT username FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    accounts.consume(token)
    accounts.revoke_resets(row["user_id"])
    audit.record("email.verify", user_id=row["user_id"], username=user["username"] if user else None, ip=ip_of(request))
    return back("/account", "E-Mail-Adresse bestätigt")


async def send_verify_mail(request: Request, user_id: int, username: str, email: str) -> str | None:
    cfg = mail_config()
    with db.connect() as con:
        con.execute("DELETE FROM tokens WHERE purpose = 'verify' AND user_id = ?", (user_id,))
    raw = accounts.create("verify", user_id=user_id, email=email)
    brand = mailer.brand_name()
    mail = mailer.Mail(
        subject=i18n.t("Bitte bestätige deine E-Mail-Adresse – {brand}", brand=brand),
        heading=i18n.t("E-Mail-Adresse bestätigen"),
        name=username,
        paragraphs=[i18n.t("bitte bestätige, dass diese E-Mail-Adresse zu deinem Konto „{name}“ bei {brand} gehört."
                           " Danach kannst du über sie dein Passwort zurücksetzen und Benachrichtigungen erhalten.",
                           name=username, brand=brand)],
        button=(i18n.t("E-Mail-Adresse bestätigen"), f"{cfg.base_url}/verify/{raw}"),
        facts=[(i18n.t("Konto"), username), (i18n.t("E-Mail-Adresse"), email),
               (i18n.t("Gültig"), i18n.t("24 Stunden"))],
        note=i18n.t("Du hast kein Konto bei {brand} oder diese Adresse nicht eingetragen? Dann ignoriere diese"
                    " E-Mail einfach – ohne Bestätigung passiert nichts.", brand=brand))
    try:
        await mailer.send(cfg, email, mail.subject, mail)
    except mailer.MailError as exc:
        accounts.consume(raw)  # sonst stünde „Bestätigung ausstehend“ da, obwohl nie eine Mail ankam
        return str(exc)
    return None


# == Eigenes Konto: E-Mail, Benachrichtigungen, Datenauskunft ===============================

@app.post("/account/email")
async def account_email(request: Request, email: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    email = email.strip()
    if not email:
        with db.connect() as con:
            con.execute("UPDATE users SET email = NULL, email_verified = 0, notify_email = 0 WHERE id = ?", (user.id,))
            con.execute("DELETE FROM tokens WHERE purpose IN ('verify', 'reset') AND user_id = ?", (user.id,))
        audit.record("email.change", user_id=user.id, username=user.username, ip=ip_of(request), detail="entfernt")
        return back("/account#email", "E-Mail-Adresse entfernt")
    if not mail_config().ready:
        return back("/account#email", "E-Mail ist in Hatchtower nicht eingeschaltet")
    with db.connect() as con:
        no_email = con.execute("SELECT no_email FROM users WHERE id = ?", (user.id,)).fetchone()["no_email"]
    if no_email:
        return back("/account#email", "Für dein Konto ist keine E-Mail-Adresse vorgesehen")
    if not mailer.valid_email(email):
        return back("/account#email", "Bitte eine gültige E-Mail-Adresse eingeben")
    error = await send_verify_mail(request, user.id, user.username, email)
    if error:
        return back("/account#email", i18n.t("E-Mail konnte nicht verschickt werden: {grund}", grund=error))
    audit.record("email.change", user_id=user.id, username=user.username, ip=ip_of(request), detail="Bestätigung angefordert")
    return back("/account#email", i18n.t("Bestätigungslink an {email} geschickt. Er gilt 24 Stunden.", email=email))


@app.post("/account/notify")
async def account_notify(request: Request, notify_email: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    form = await request.form()
    shown = notify.events_for(user.is_owner)
    with db.connect() as con:
        # Owner-Meldungen sieht ein Benutzer nicht im Formular; seine bisherige Auswahl dazu bleibt erhalten
        row = con.execute("SELECT notify_events FROM users WHERE id = ?", (user.id,)).fetchone()
        before = set((row["notify_events"] if row["notify_events"] is not None else ",".join(notify.EVENTS)).split(","))
        events = [e for e in notify.EVENTS if (form.get(f"event__{e}") if e in shown else e in before)]
        con.execute("UPDATE users SET notify_email = ?, notify_events = ? WHERE id = ?",
                    (1 if notify_email else 0, ",".join(events), user.id))
    audit.record("settings.notify", user_id=user.id, username=user.username, ip=ip_of(request),
                 detail={"email": bool(notify_email), "events": events})
    return back("/account#benachrichtigungen", "Benachrichtigungen gespeichert")


# == Abmeldelink aus den Benachrichtigungs-Mails (ohne Anmeldung) ================================

def _unsubscribe_target(token: str):
    def lookup(uid: int):
        with db.connect() as con:
            return con.execute("SELECT id, username, email, notify_email FROM users WHERE id = ?", (uid,)).fetchone()
    return security.unsubscribe_user(settings.secret_key, token, lookup)


@app.get("/notify/unsubscribe/{token}")
async def unsubscribe_page(request: Request, token: str):
    """Bestätigungsseite. Abbestellt wird erst per POST, damit Link-Vorschauen in Mailprogrammen
    nicht versehentlich abmelden."""
    row = _unsubscribe_target(token)
    if row is None:
        return render(request, "error.html", status=410, title="Link abgelaufen",
                      message="Dieser Link gilt nicht mehr, z. B. weil sich die E-Mail-Adresse geändert hat."
                              " Benachrichtigungen schaltest du nach der Anmeldung unter Konto aus.")
    return render(request, "unsubscribe.html", token=token, email=row["email"], done=not row["notify_email"])


@app.post("/notify/unsubscribe/{token}")
async def unsubscribe(request: Request, token: str):
    """Abbestellen – auch als Ein-Klick-Abmeldung direkt aus dem Mailprogramm (RFC 8058, ohne Formular-Token;
    der signierte Link ist der Nachweis)."""
    row = _unsubscribe_target(token)
    if row is None:
        return RedirectResponse(f"/notify/unsubscribe/{token}", status_code=303)
    if row["notify_email"]:
        with db.connect() as con:
            con.execute("UPDATE users SET notify_email = 0 WHERE id = ?", (row["id"],))
        audit.record("notify.unsubscribe", user_id=row["id"], username=row["username"], ip=ip_of(request),
                     detail={"email": row["email"]})
    return render(request, "unsubscribe.html", token=token, email=row["email"], done=True)


def user_data(user_id: int) -> dict:
    """Alles, was Hatchtower zu einem Benutzer gespeichert hat (Auskunft nach Art. 15 DSGVO)."""
    with db.connect() as con:
        u = dict(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        u.pop("password_hash", None)
        # Nur Einträge seit Anlage des Kontos: Benutzer-IDs und -namen gelöschter Konten können neu vergeben
        # werden, deren Einträge (mit IP-Adressen) gehören nicht in diese Auskunft
        entries = [dict(r) for r in con.execute(
            "SELECT ts, ip, action, server, detail, result FROM audit_log"
            " WHERE (user_id = ? OR (user_id IS NULL AND username = ?)) AND ts >= ? ORDER BY id",
            (user_id, u["username"], u["created_at"]))]
        sessions = [dict(r) for r in con.execute(
            "SELECT created_at, last_seen, ip, user_agent FROM sessions WHERE user_id = ?", (user_id,))]
        favs = [r["identifier"] for r in con.execute("SELECT identifier FROM favorites WHERE user_id = ?", (user_id,))]
    return {"exported_at": db.utcnow(), "app": f"{APP_NAME} {__version__}", "account": u,
            "permissions": {k: sorted(v) for k, v in load_grants(user_id).items()}, "favorites": favs,
            "sessions": sessions, "audit_log": entries,
            "note": i18n.t("Audit-Einträge werden nach {n} Tagen automatisch gelöscht.", n=retention.audit_days())}


def json_download(data: dict, filename: str) -> Response:
    return Response(json.dumps(data, ensure_ascii=False, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.post("/account/delete")
async def account_delete(request: Request, current: str = Form(""), confirm: str = Form("")):
    """Eigenes Konto unwiderruflich löschen."""
    from .main import active_owner_count
    user = current_user(request)
    await check_csrf(request)
    with db.connect() as con:
        row = con.execute("SELECT password_hash, role, is_active FROM users WHERE id = ?", (user.id,)).fetchone()
    if not confirm:
        return back("/account#loeschen", "Bitte bestätige, dass dein Konto gelöscht werden soll.")
    if not security.verify_password(row["password_hash"], current):
        return back("/account#loeschen", "Das aktuelle Passwort stimmt nicht.")
    if row["role"] == "owner" and row["is_active"] and active_owner_count() <= 1:
        return back("/account#loeschen", "Du bist der letzte Owner. Ernenne zuerst einen anderen Owner, dann kannst du dein Konto löschen.")
    audit.record("user.self_delete", user_id=user.id, username=user.username, ip=ip_of(request),
                 detail={"user": user.username, "role": row["role"]})
    with db.connect() as con:
        if row["role"] != "owner":
            con.execute("DELETE FROM schedules WHERE created_by = ?", (user.id,))
            con.execute("DELETE FROM backup_jobs WHERE created_by = ?", (user.id,))
        con.execute("DELETE FROM users WHERE id = ?", (user.id,))
    response = back("/login", "Dein Konto wurde gelöscht.")
    response.delete_cookie(security.SESSION_COOKIE)
    return response


@app.get("/account/export")
async def account_export(request: Request):
    user = current_user(request)
    audit.record("user.export", user_id=user.id, username=user.username, ip=ip_of(request), detail="eigene Daten")
    return json_download(user_data(user.id), f"hatchtower-{user.username}-daten.json")


@app.get("/admin/users/{user_id}/export")
async def admin_user_export(request: Request, user_id: int):
    owner = require_owner(request)
    with db.connect() as con:
        target = con.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        return back("/admin/users")
    owner_audit(request, owner, "user.export", detail={"user": target["username"]})
    return json_download(user_data(user_id), f"hatchtower-{target['username']}-daten.json")


@app.post("/admin/users/{user_id}/email")
async def admin_user_email(request: Request, user_id: int, email: str = Form(""), confirmed: str = Form("")):
    """Owner trägt die E-Mail-Adresse eines Benutzers ein, ändert oder entfernt sie.
    Ohne „bestätigt“ geht ein Bestätigungslink an die Adresse (wenn E-Mail eingeschaltet ist)."""
    owner = require_owner(request)
    await check_csrf(request)
    target_url = f"/admin/users/{user_id}#email"
    with db.connect() as con:
        target = con.execute("SELECT id, username, email, no_email FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        return back("/admin/users")
    email = email.strip()
    if email and not mail_config().ready:
        return back(target_url, "E-Mail ist in Hatchtower nicht eingeschaltet")
    if email and target["no_email"]:
        return back(target_url, "Dieser Benutzer ist auf „ohne E-Mail-Adresse“ gestellt")
    if email and not mailer.valid_email(email):
        return back(target_url, "Bitte eine gültige E-Mail-Adresse eingeben")
    with db.connect() as con:
        con.execute("DELETE FROM tokens WHERE purpose IN ('verify', 'reset') AND user_id = ?", (user_id,))
        if not email:
            con.execute("UPDATE users SET email = NULL, email_verified = 0, notify_email = 0 WHERE id = ?", (user_id,))
        else:
            con.execute("UPDATE users SET email = ?, email_verified = ? WHERE id = ?",
                        (email, 1 if confirmed else 0, user_id))
    if not email:
        owner_audit(request, owner, "email.change", detail={"user": target["username"], "email": "entfernt"})
        return back(target_url, "E-Mail-Adresse entfernt")
    detail = {"user": target["username"], "email": email, "bestätigt": bool(confirmed)}
    if confirmed:
        owner_audit(request, owner, "email.change", detail=detail)
        return back(target_url, "E-Mail-Adresse gespeichert")
    error = await send_verify_mail(request, user_id, target["username"], email)
    owner_audit(request, owner, "email.change", detail={**detail, "bestätigungslink": "fehlgeschlagen" if error else "verschickt"})
    if error:
        return back(target_url, i18n.t("E-Mail-Adresse gespeichert, aber der Bestätigungslink konnte nicht verschickt"
                                       " werden: {grund}", grund=error))
    return back(target_url, i18n.t("E-Mail-Adresse gespeichert. Bestätigungslink an {email} geschickt.", email=email))


@app.post("/admin/users/{user_id}/no-email")
async def admin_user_no_email(request: Request, user_id: int, no_email: str = Form("")):
    """Benutzer ohne E-Mail-Adresse: braucht keine und kann selbst keine hinterlegen; eine vorhandene wird entfernt."""
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        target = con.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            return back("/admin/users")
        if no_email:
            con.execute("UPDATE users SET no_email = 1, email = NULL, email_verified = 0, notify_email = 0 WHERE id = ?",
                        (user_id,))
            con.execute("DELETE FROM tokens WHERE purpose IN ('verify', 'reset') AND user_id = ?", (user_id,))
        else:
            con.execute("UPDATE users SET no_email = 0 WHERE id = ?", (user_id,))
    owner_audit(request, owner, "user.update", detail={"user": target["username"], "ohne E-Mail": bool(no_email)})
    return back(f"/admin/users/{user_id}#email", "Gespeichert")


@app.post("/admin/users/email-policy")
async def admin_users_email_policy(request: Request, optional: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    db.set_setting("users_email_optional", "1" if optional else "0", owner.id)
    owner_audit(request, owner, "settings.mail", detail={"Benutzer ohne E-Mail-Adresse erlaubt": bool(optional)})
    return back("/admin/users/new#email-pflicht", "Gespeichert")


# == Einladungen ===============================================================================

def invite_access_from_form(form) -> dict | None:
    """Zugriff, den ein eingeladener Benutzer sofort bekommt: eine Rechtegruppe für ausgewählte
    Server oder für „Alle Server, auch künftige“. Ohne Gruppe oder Server: keiner."""
    groups = {g["id"] for g in load_groups()}
    raw = str(form.get("access_group", ""))
    if not raw.isdigit() or int(raw) not in groups:
        return None
    if form.get("access_all"):
        return {"group": int(raw), "all": True, "servers": []}
    known = server_names()
    servers = sorted({str(s) for s in form.getlist("access_server") if str(s) in known})
    return {"group": int(raw), "all": False, "servers": servers} if servers else None


def describe_invite_access(access: dict | None) -> str | None:
    """Kurztext für Liste und Audit-Log, z. B. „Spieler: Valheim, Terraria“."""
    if not access:
        return None
    group = next((g["name"] for g in load_groups() if g["id"] == access.get("group")), None)
    if group is None:
        return i18n.t("Gruppe gelöscht, kein Zugriff")
    if access.get("all"):
        return i18n.t("{gruppe}: alle Server, auch künftige", gruppe=group)
    names = server_names()
    return f"{group}: " + ", ".join(names.get(s, s) for s in access.get("servers", []))


def apply_invite_access(user_id: int, access: dict | None) -> dict | None:
    """Beim Annehmen: Zugriff aus der Einladung vergeben. Die Gruppe wird erst jetzt aufgelöst –
    wurde sie inzwischen gelöscht, bekommt der Benutzer nichts und der Owner vergibt von Hand."""
    if not access:
        return None
    group = next((g for g in load_groups() if g["id"] == access.get("group")), None)
    if group is None:
        return None
    label = f"Gruppe {group['name']}"
    if access.get("all"):
        save_default_group(user_id, group["id"])
        return {"Alle Server, auch künftige": label}
    known = server_names()
    assignments = {s: {"mode": "group", "group_id": group["id"], "keys": set()}
                   for s in access.get("servers", []) if s in known}
    if not assignments:
        return None
    save_assignments(user_id, assignments)
    return {known[s]: label for s in assignments}


def group_has_write(group_id: int | None) -> bool:
    group = next((g for g in load_groups() if g["id"] == group_id), None)
    return bool(group) and any(BY_KEY[k].write for k in group["keys"] if k in BY_KEY)


@app.post("/admin/users/invite", response_class=HTMLResponse)
async def admin_invite(request: Request, role: str = Form("user"), email: str = Form(""), days: int = Form(7),
                       uses: int = Form(1), note: str = Form(""), view_mode: str = Form(""),
                       view_locked: str = Form("")):
    from .main import view_choice
    owner = require_owner(request)
    await check_csrf(request)
    role = "owner" if role == "owner" else "user"
    days = days if days in accounts.INVITE_DAYS else 7
    uses = uses if uses in accounts.INVITE_USES else 1
    email, note = email.strip(), " ".join(note.split())[:200]
    if email and not mailer.valid_email(email):
        return back("/admin/users/new#einladen", "Bitte eine gültige E-Mail-Adresse eingeben")
    view, locked = view_choice(view_mode, view_locked)
    access = invite_access_from_form(await request.form()) if role == "user" else None
    raw = accounts.create("invite", email=email or None,
                          data={"role": role, "view": view, "view_locked": locked, "access": access},
                          created_by=owner.id,
                          hours=days * 24, max_uses=uses, note=note or None, encrypt=vault.encrypt)
    link = f"{link_base(request)}/invite/{raw}"
    message = "Einladung erstellt. Kopiere den Link, er wird nur jetzt angezeigt."
    if email:
        cfg = mail_config()
        if not cfg.ready:
            message = "Einladung erstellt. E-Mail ist nicht eingeschaltet, gib den Link selbst weiter."
        else:
            page_lang = i18n.current()
            i18n.use_default()            # Sprache des Eingeladenen ist unbekannt: Standardsprache der Instanz
            brand = mailer.brand_name()
            facts = [(i18n.t("Eingeladen von"), owner.username),
                     (i18n.t("Rolle"), i18n.t("Owner") if role == "owner" else i18n.t("Rolle|Benutzer"))]
            if access:
                facts.append((i18n.t("Zugriff"), describe_invite_access(access)))
            facts.append((i18n.t("Gültig"), accounts.validity_text(days)))
            mail = mailer.Mail(
                subject=i18n.t("Einladung zu {brand}", brand=brand),
                heading=i18n.t("Du wurdest eingeladen"),
                paragraphs=[i18n.t("{owner} lädt dich zu {brand} ein. Dort siehst und steuerst du die Gameserver,"
                                   " für die du freigeschaltet bist.", owner=owner.username, brand=brand),
                            i18n.t("Über den folgenden Knopf legst du dein Konto an. Benutzername und Passwort"
                                   " wählst du dabei selbst.")],
                button=(i18n.t("Konto anlegen"), f"{cfg.base_url}/invite/{raw}"),
                facts=facts,
                note=i18n.t("Du kannst mit dieser Einladung nichts anfangen? Dann ignoriere diese E-Mail einfach."))
            i18n.set_language(page_lang)
            try:
                await mailer.send(cfg, email, mail.subject, mail)
                message = i18n.t("Einladung an {email} geschickt.", email=email)
            except mailer.MailError as exc:
                message = i18n.t("E-Mail konnte nicht verschickt werden: {grund}", grund=exc)
    owner_audit(request, owner, "user.invite", detail={"role": role, "email": email or None, "invite": note or None,
                                                       "ansicht": (view or "standard") + (" (festgelegt)" if locked else ""),
                                                       "days": days or "∞", "uses": uses or "∞",
                                                       "zugriff": describe_invite_access(access)})
    from .main import admin_users_new
    request.state.invite_link = link
    request.state.invite_message = i18n.t(message)
    if access and access["all"] and group_has_write(access["group"]):
        request.state.invite_message += " " + i18n.t(
            "Achtung: Die Gruppe enthält Steuer- oder Schreibrechte – wer den Link nutzt, kann damit auch jeden neu"
            " hinzukommenden Server sofort steuern.")
    request.state.invite_valid = {"validity": accounts.validity_text(days), "uses": uses}
    return await admin_users_new(request)


@app.post("/admin/users/invites/{token_hash}/revoke")
async def admin_invite_revoke(request: Request, token_hash: str):
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        row = con.execute("SELECT * FROM tokens WHERE token_hash = ? AND purpose = 'invite'", (token_hash,)).fetchone()
    if row is not None and accounts.revoke_invite(token_hash):
        owner_audit(request, owner, "user.invite_revoke", detail=accounts.invite_info(row))
    return back("/admin/users/new#einladen", "Einladung zurückgezogen")


@app.get("/invite/{token}", response_class=HTMLResponse)
async def invite_page(request: Request, token: str):
    row = accounts.get(token, "invite")
    if row is None:
        return render(request, "error.html", status=410, title="Link abgelaufen",
                      message="Diese Einladung ist abgelaufen oder wurde schon benutzt. Bitte frag nach einer neuen.")
    return prelogin_page(request, "invite.html", token=token, error=None, username="", email=row["email"] or "",
                         mail_ready=mail_config().ready, email_required=mailer.email_required(mail_config()))


@app.post("/invite/{token}")
async def invite_accept(request: Request, token: str, username: str = Form(""), password: str = Form(""),
                        repeat: str = Form(""), email: str = Form(""), prelogin: str = Form("")):
    row = accounts.get(token, "invite")
    if row is None:
        return RedirectResponse(f"/invite/{quote(token)}", status_code=303)
    username, email = username.strip(), email.strip()
    ready = mail_config().ready

    def fail(message: str):
        return prelogin_page(request, "invite.html", 400, token=token, error=message, username=username,
                             email=email, mail_ready=ready, email_required=mailer.email_required(mail_config()))

    if not prelogin_ok(request, prelogin):
        return fail("Das Formular ist abgelaufen. Versuch es noch einmal.")
    if throttle.ip_blocked(ip_of(request)):
        return fail("Zu viele Versuche. Warte ein paar Minuten.")
    if not accounts.valid_username(username):
        return fail("Benutzername: 1–32 Zeichen, nur Buchstaben, Zahlen, - _ .")
    error = password_error(password, repeat, username)
    if error:
        return fail(error)
    if not ready:
        email = ""                                       # Feld ist gesperrt, solange E-Mail aus ist
    if not email and mailer.email_required(mail_config()):
        return fail("Bitte gib eine E-Mail-Adresse an")
    if email and not mailer.valid_email(email):
        return fail("Bitte eine gültige E-Mail-Adresse eingeben")
    data = json.loads(row["data"] or "{}")
    role = "owner" if data.get("role") == "owner" else "user"
    view = data.get("view") if data.get("view") in ("simple", "full") else None
    if not accounts.claim_invite(token):
        return RedirectResponse(f"/invite/{quote(token)}", status_code=303)
    try:
        with db.connect() as con:
            user_id = con.execute("INSERT INTO users (username, password_hash, role, created_at, language, view_mode,"
                                  " view_locked) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                  (username, security.hash_password(password), role, db.utcnow(), i18n.current(),
                                   view, 1 if view and data.get("view_locked") else 0)).lastrowid
    except Exception:
        accounts.release_invite(token)
        throttle.record_failure(ip_of(request))
        return fail("Diesen Benutzernamen gibt es schon")
    granted = apply_invite_access(user_id, data.get("access")) if role == "user" else None
    info = accounts.invite_info({**dict(row), "uses": row["uses"] + 1})
    audit.record("invite.accept", user_id=user_id, username=username, ip=ip_of(request),
                 detail={"role": role, **info, **({"zugriff": granted} if granted else {})})
    if accounts.finish_invite(token):
        audit.record("invite.used_up", user_id=user_id, username=username, ip=ip_of(request), detail=info)
    announce_new_user(user_id, username, role, granted)
    target = "/"
    if email and ready:
        if await send_verify_mail(request, user_id, username, email) is None:
            target = "/?ok=" + quote(i18n.t("Willkommen! Bitte bestätige noch deine E-Mail-Adresse über den Link in der E-Mail."))
    return login_response(request, user_id, username, target)


# == System: E-Mail ================================================================================

@app.get("/admin/mail", response_class=HTMLResponse)
async def admin_mail(request: Request):
    require_owner(request)
    cfg = mail_config()
    return render(request, "admin_mail.html", cfg=cfg, has_password=bool(cfg.password),
                  security_options=mailer.SECURITY, own_sender=db.get_setting("mail_from"))


@app.post("/admin/mail")
async def admin_mail_save(request: Request, enabled: str = Form(""), host: str = Form(""), port: str = Form("587"),
                          security_: str = Form("starttls", alias="security"), username: str = Form(""),
                          password: str = Form(""), sender: str = Form(""), sender_name: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    problems = []
    host = host.strip().lower()
    if host and (len(host) > 253 or not all(c.isalnum() or c in ".-" for c in host)):
        problems.append("SMTP-Server")
    if not port.strip().isdigit() or not 1 <= int(port) <= 65535:
        problems.append("Port")
    if sender.strip() and not mailer.valid_email(sender.strip()):
        problems.append("Absenderadresse")
    if enabled and not (host and (sender.strip() or mailer.valid_email(username.strip()))):
        problems.append("zum Einschalten: Server und Absender")
    if problems:
        return back("/admin/mail", i18n.t("Bitte prüfen: {list}", list=", ".join(i18n.t(x) for x in problems)))
    old = mail_config()
    # Das gespeicherte Passwort geht nur an den Server, für den es eingegeben wurde, und nicht unverschlüsselt
    if old.password and not password and (not security.same_host(old.host, host)
                                          or (security_ == "none" and old.security != "none")):
        return back("/admin/mail", "Neuer SMTP-Server oder Verbindung ohne Verschlüsselung: Bitte das Passwort"
                                   " noch einmal eingeben. Das gespeicherte geht nur an den bisherigen Server.")
    if enabled and not mail_config().base_url:
        # Ohne feste Adresse keine Links in E-Mails: die aktuelle übernehmen, änderbar unter System → Netzwerk
        db.set_setting("mail_base_url", link_base(request), owner.id)
    db.set_setting("mail_enabled", "1" if enabled else "0", owner.id)
    db.set_setting("mail_host", host, owner.id)
    db.set_setting("mail_port", port.strip(), owner.id)
    db.set_setting("mail_security", security_ if security_ in mailer.SECURITY else "starttls", owner.id)
    db.set_setting("mail_username", username.strip(), owner.id)
    if password:
        db.set_setting("mail_password_enc", vault.encrypt(password), owner.id)
    db.set_setting("mail_from", sender.strip(), owner.id)
    db.set_setting("mail_from_name", " ".join(sender_name.split())[:60], owner.id)
    owner_audit(request, owner, "settings.mail", detail={"enabled": bool(enabled), "host": host, "port": port.strip(),
                                                          "password": "neu" if password else "unverändert"})
    return back("/admin/mail", "E-Mail-Einstellungen gespeichert")


@app.post("/admin/mail/password/delete")
async def admin_mail_password_delete(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    db.set_setting("mail_password_enc", "", owner.id)
    owner_audit(request, owner, "settings.mail", detail={"password": "entfernt"})
    return back("/admin/mail", "SMTP-Passwort entfernt")


@app.post("/admin/mail/test")
async def admin_mail_test(request: Request, to: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if not mailer.valid_email(to.strip()):
        return back("/admin/mail", "Bitte eine gültige E-Mail-Adresse eingeben")
    cfg = mail_config()
    brand = mailer.brand_name()
    security_label = {"starttls": "STARTTLS", "ssl": "SSL/TLS", "none": i18n.t("keine")}.get(cfg.security, cfg.security)
    mail = mailer.Mail(
        subject=i18n.t("Testnachricht – {brand}", brand=brand),
        heading=i18n.t("Der E-Mail-Versand funktioniert"),
        paragraphs=[i18n.t("diese Testnachricht bestätigt, dass {brand} E-Mails verschicken kann. Einladungen,"
                           " Links zum Zurücksetzen des Passworts und Benachrichtigungen kommen damit an.",
                           brand=brand)],
        button=(i18n.t("{brand} öffnen", brand=brand), cfg.base_url) if cfg.base_url else None,
        facts=[(i18n.t("SMTP-Server"), f"{cfg.host}:{cfg.port}"), (i18n.t("Verschlüsselung"), security_label),
               (i18n.t("Absender"), f"{cfg.sender_name} <{cfg.sender}>"), (i18n.t("Ausgelöst von"), owner.username),
               (i18n.t("Gesendet am"), mailer.now_text())],
        note=i18n.t("Landet diese Nachricht im Spam-Ordner, markiere sie als „Kein Spam“ und prüfe beim Anbieter"
                    " deiner Absenderadresse die Einstellungen für SPF und DKIM."),
        reason=i18n.t("Du bekommst diese E-Mail, weil unter System → E-Mail eine Testnachricht an diese Adresse"
                      " ausgelöst wurde."))
    try:
        await mailer.send(cfg, to.strip(), mail.subject, mail)
    except mailer.MailError as exc:
        return back("/admin/mail", i18n.t("E-Mail konnte nicht verschickt werden: {grund}", grund=exc))
    owner_audit(request, owner, "settings.mail", detail="Testnachricht")
    return back("/admin/mail", i18n.t("Testnachricht an {email} verschickt.", email=to.strip()))


# == System: Netzwerk (Adressen und Reverse-Proxy) ===================================================

@app.get("/admin/network", response_class=HTMLResponse)
async def admin_network(request: Request, pruefen: str = ""):
    from .config import format_proxies
    require_owner(request)
    cfg = mail_config()
    host = urlsplit(cfg.base_url or link_base(request)).hostname or ""
    dns = await setup.dns_check(host) if pruefen and host else None
    limit_mbit, lan_free = downloads.config()
    return render(request, "admin_network.html", base_url=cfg.base_url, suggested_base=link_base(request),
                  public_host=db.get_setting("public_host"), trusted_proxies=format_proxies(settings.trusted_proxies),
                  dns=dns, dns_host=host, limit_mbit=limit_mbit, lan_free=lan_free, my_ip=ip_of(request),
                  my_internal=downloads.is_internal(ip_of(request)), proxy_missing=proxy_missing(request))


def proxy_missing(request: Request) -> bool:
    """A proxy forwards the request (X-Forwarded-For) but is not entered: every visitor then looks like the proxy."""
    if not request.headers.get("x-forwarded-for") or not request.client:
        return False
    try:
        return not is_trusted_proxy(ipaddress.ip_address(request.client.host), settings.trusted_proxies)
    except ValueError:
        return False


@app.post("/admin/network")
async def admin_network_save(request: Request, base_url: str = Form(""), public_host: str = Form(""),
                             trusted_proxies: str = Form("")):
    from .main import clean_public_host, proxies_problem
    owner = require_owner(request)
    await check_csrf(request)
    base = setup.normalize_url(base_url) if base_url.strip() else ""
    if base is None:
        return back("/admin/network", "Adresse von Hatchtower: bitte vollständig angeben, z. B. https://gameserver.example.com")
    if not base and mail_config().enabled:
        return back("/admin/network", "Adresse von Hatchtower: wird für Links in E-Mails gebraucht, solange E-Mail eingeschaltet ist")
    host, problem = clean_public_host(public_host)
    problem = problem or proxies_problem(trusted_proxies)
    if problem:
        return back("/admin/network", problem)
    db.set_setting("mail_base_url", base, owner.id)
    db.set_setting("public_host", host, owner.id)
    setup.save_values({"trusted_proxies": trusted_proxies.strip()}, vault, owner.id)
    await apply_connection()
    owner_audit(request, owner, "settings.network", detail={"adresse": base, "public_host": host,
                                                             "proxy": trusted_proxies.strip()})
    return back("/admin/network", "Netzwerk gespeichert")


@app.post("/admin/network/downloads")
async def admin_network_downloads(request: Request, limit_mbit: str = Form(""), lan_free: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    try:
        mbit = round(float(limit_mbit.strip().replace(",", ".") or 0), 1)
    except ValueError:
        mbit = -1
    if not 0 <= mbit <= 100000:
        return back("/admin/network#downloads", "Download-Grenze: bitte eine Zahl in Mbit/s angeben (0 = keine Grenze)")
    db.set_setting("download_limit_mbit", f"{mbit:g}", owner.id)
    db.set_setting("download_limit_lan_free", "1" if lan_free else "0", owner.id)
    downloads.reload()
    owner_audit(request, owner, "settings.download_limit", detail={"mbit": mbit, "eigenes_netz_frei": bool(lan_free)})
    return back("/admin/network#downloads", "Download-Grenze gespeichert")


# == System: Benachrichtigungen ======================================================================

NOTIFY = "/admin/notify"


@app.get("/admin/notify", response_class=HTMLResponse)
async def admin_notify(request: Request):
    require_owner(request)
    hooks = []
    for h in notify.load_webhooks():
        url = vault.decrypt(h["url_enc"]) or ""
        hooks.append({"h": h, "events": notify.hook_events(h), "servers": notify.hook_servers(h),
                      "hint": (url[:40] + "…") if url else "", "disabled": switched_off(h)})
    with db.connect() as con:
        subscribers = con.execute("SELECT username FROM users WHERE notify_email = 1 AND email_verified = 1"
                                  " AND is_active = 1 ORDER BY username").fetchall()
    return render(request, "admin_notify.html", events=notify.EVENTS, hooks=hooks, servers=present_servers(),
                  owner_notices={k: (v, notify.owner_notice(k)) for k, v in notify.OWNER_NOTICES.items()},
                  limits=notify.thresholds(), subscribers=[r["username"] for r in subscribers],
                  mail_ready=mail_config().ready)


@app.post("/admin/notify")
async def admin_notify_save(request: Request, disk_pct: str = Form("90"), ram_pct: str = Form("90"),
                            ram_minutes: str = Form("5"), cpu_pct: str = Form("90"), cpu_minutes: str = Form("5")):
    owner = require_owner(request)
    await check_csrf(request)
    numbers = {}
    for key, value, lo, hi in (("disk", disk_pct, 50, 99), ("ram", ram_pct, 50, 99), ("ram_minutes", ram_minutes, 1, 60),
                                 ("cpu", cpu_pct, 50, 100), ("cpu_minutes", cpu_minutes, 1, 60)):
        if not value.strip().isdigit() or not lo <= int(value) <= hi:
            return back(NOTIFY, i18n.t("Bitte prüfen: {list}", list=i18n.t("Schwellenwerte")))
        numbers[key] = value.strip()
    db.set_setting("notify_disk_pct", numbers["disk"], owner.id)
    db.set_setting("notify_ram_pct", numbers["ram"], owner.id)
    db.set_setting("notify_ram_minutes", numbers["ram_minutes"], owner.id)
    db.set_setting("notify_cpu_pct", numbers["cpu"], owner.id)
    db.set_setting("notify_cpu_minutes", numbers["cpu_minutes"], owner.id)
    owner_audit(request, owner, "settings.notify", detail=numbers)
    return back(NOTIFY, "Schwellenwerte gespeichert")


@app.post("/admin/notify/owner")
async def admin_notify_owner(request: Request):
    """Which notices owners get in the overview and the bell (new servers, new users)."""
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    chosen = {}
    for key in notify.OWNER_NOTICES:
        chosen[key] = "1" if form.get(f"notice__{key}") else "0"
        db.set_setting(f"owner_notice_{key}", chosen[key], owner.id)
    owner_audit(request, owner, "settings.notify", detail={"hinweise": chosen})
    return back(NOTIFY, "Hinweise gespeichert")


def webhook_form(form, hook=None) -> tuple[dict | None, str | None]:
    """Liest das Formular eines Webhooks. Leere Adresse behält beim Bearbeiten die gespeicherte."""
    title = " ".join(str(form.get("title", "")).split())[:60]
    description = str(form.get("description", "")).strip()[:300]
    url = str(form.get("url", "")).strip()
    if not title:
        return None, "Bitte einen Titel für den Webhook angeben"
    if (url or hook is None) and (not url.startswith(("https://", "http://")) or len(url) > 500
                                  or any(c.isspace() for c in url)):
        return None, "Bitte eine gültige Webhook-Adresse eingeben (https://…)"
    kind = str(form.get("kind", "discord"))
    known = server_names()
    servers = sorted({k.split("__", 1)[1] for k in form.keys() if k.startswith("server__")} & set(known))
    values = {"title": title, "description": description or None,
              "kind": kind if kind in notify.WEBHOOK_TYPES else "discord",
              "events": ",".join(e for e in notify.EVENTS if form.get(f"event__{e}")),
              "servers": "" if form.get("all_servers") else ",".join(servers),
              "enabled": 1 if form.get("enabled") else 0}
    if not form.get("all_servers") and not servers:
        return None, "Bitte Server wählen oder „Alle Server, auch künftige“ ankreuzen"
    if url:
        values["url_enc"] = vault.encrypt(url)
    return values, None


def switched_off(row) -> dict | None:
    """Why and when Hatchtower switched a webhook or Discord status off by itself (None if it did not)."""
    if row["enabled"] or not row["disabled_code"]:
        return None
    return {"reason": notify.gone_reason(row["disabled_code"]), "at": fmt_dt(row["disabled_at"], settings.timezone)}


def webhook_audit(values: dict) -> dict:
    known = server_names()
    return {"webhook": values["title"], "aktiv": bool(values["enabled"]), "format": values["kind"],
            "ereignisse": values["events"].split(",") if values["events"] else [],
            "servers": sorted(known.get(i, i) for i in values["servers"].split(",") if i) or "alle",
            "adresse": "neu" if "url_enc" in values else "unverändert"}


def load_webhook(hook_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM notify_webhooks WHERE id = ?", (hook_id,)).fetchone()


@app.post("/admin/notify/webhook")
async def admin_webhook_create(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    values, error = webhook_form(await request.form())
    if error:
        return back(NOTIFY + "#webhooks", error)
    values["created_at"] = values["updated_at"] = db.utcnow()
    with db.connect() as con:
        con.execute(f"INSERT INTO notify_webhooks ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                    tuple(values.values()))
    owner_audit(request, owner, "settings.notify", detail={**webhook_audit(values), "neu": True})
    return back(NOTIFY + "#webhooks", i18n.t("Webhook „{name}“ angelegt", name=values["title"]))


@app.post("/admin/notify/webhook/{hook_id}")
async def admin_webhook_update(request: Request, hook_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    hook = load_webhook(hook_id)
    if hook is None:
        return back(NOTIFY + "#webhooks")
    values, error = webhook_form(await request.form(), hook)
    if error:
        return back(NOTIFY + "#webhooks", error)
    values["updated_at"] = db.utcnow()
    if values["enabled"]:                    # switched on again (usually with a new link): the notice goes
        values["disabled_code"] = values["disabled_at"] = None
    with db.connect() as con:
        con.execute(f"UPDATE notify_webhooks SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                    (*values.values(), hook_id))
    owner_audit(request, owner, "settings.notify", detail=webhook_audit(values))
    return back(NOTIFY + "#webhooks", i18n.t("Webhook „{name}“ gespeichert", name=values["title"]))


@app.post("/admin/notify/webhook/{hook_id}/delete")
async def admin_webhook_delete(request: Request, hook_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    hook = load_webhook(hook_id)
    if hook is None:
        return back(NOTIFY + "#webhooks")
    with db.connect() as con:
        con.execute("DELETE FROM notify_webhooks WHERE id = ?", (hook_id,))
    owner_audit(request, owner, "settings.notify", detail={"webhook": hook["title"], "geloescht": True})
    return back(NOTIFY + "#webhooks", i18n.t("Webhook „{name}“ entfernt", name=hook["title"]))


@app.post("/admin/notify/webhook/{hook_id}/test")
async def admin_webhook_test(request: Request, hook_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    hook = load_webhook(hook_id)
    if hook is None:
        return back(NOTIFY + "#webhooks")
    errors = await notify.deliver(vault, "test", None, i18n.t("Testnachricht von Hatchtower an „{name}“: Benachrichtigungen funktionieren.",
                                                             name=hook["title"]), only="webhook", hook_id=hook_id)
    owner_audit(request, owner, "settings.notify", detail={"webhook": hook["title"], "test": True},
                result="error" if errors else "ok")
    if errors:
        return back(NOTIFY + "#webhooks", i18n.t("Fehlgeschlagen: {grund}", grund="; ".join(errors)))
    return back(NOTIFY + "#webhooks", i18n.t("Testnachricht an „{name}“ verschickt", name=hook["title"]))


# == System: Statusseite und Discord ===================================================================

STATUSPAGE = "/admin/statuspage"


def present_servers() -> list:
    with db.connect() as con:
        return con.execute("SELECT s.identifier, s.name, COALESCE(m.game_name, s.egg) AS egg,"
                           " COALESCE(m.on_status, 0) AS on_status FROM servers s"
                           " LEFT JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1"
                           " ORDER BY s.name COLLATE NOCASE").fetchall()


@app.get("/admin/statuspage", response_class=HTMLResponse)
async def admin_statuspage(request: Request):
    require_owner(request)
    feed = request.app.state.statusfeed
    feeds = []
    for f in statusfeed.load_feeds():
        url = vault.decrypt(f["url_enc"]) or ""
        feeds.append({"f": f, "servers": statusfeed.feed_servers(f["id"]), "fields": feed_fields(f),
                      "hint": (url[:45] + "…") if url else "",
                      "ok": fmt_dt(feed.last_ok[f["id"]], settings.timezone, seconds=True) if f["id"] in feed.last_ok else None,
                      "error": feed.last_error.get(f["id"]), "disabled": switched_off(f)})
    return render(request, "admin_statuspage.html", servers=present_servers(), enabled=status_enabled(),
                  fields=STATUS_FIELDS, active_fields=status_fields(),
                  status_title=db.get_setting("status_title"), status_url=f"{link_base(request)}/status",
                  feeds=feeds, discord_field_names=DISCORD_FIELDS, discord_default=set(DISCORD_DEFAULT.split(",")))


@app.post("/admin/statuspage")
async def admin_statuspage_save(request: Request, enabled: str = Form(""), title: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    chosen = {k.split("__", 1)[1] for k in form.keys() if k.startswith("show__")}
    known = server_names()
    fields = [f for f in STATUS_FIELDS if form.get(f"field__{f}")]
    if "password" in fields and not form.get("password_confirm"):
        return back(STATUSPAGE, "Passwörter wurden nicht freigegeben: Bitte bestätige ausdrücklich, dass sie öffentlich sichtbar werden.")
    with db.connect() as con:
        for ident in known:
            con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
            con.execute("UPDATE server_meta SET on_status = ? WHERE identifier = ?", (1 if ident in chosen else 0, ident))
    db.set_setting("status_enabled", "1" if enabled else "0", owner.id)
    db.set_setting("status_title", " ".join(title.split())[:60], owner.id)
    db.set_setting("status_fields", ",".join(fields), owner.id)
    db.set_setting("status_discord_link", f"{link_base(request)}/status", owner.id)
    request.app.state.statusfeed.request_update()       # Link zur Statusseite in Discord an/aus
    owner_audit(request, owner, "settings.status", detail={"enabled": bool(enabled), "angaben": fields,
                                                            "servers": sorted(known[i] for i in chosen if i in known)})
    return back(STATUSPAGE, "Statusseite gespeichert")


def feed_form(form, feed=None) -> tuple[dict | None, set[str], str | None]:
    """Liest das Formular eines Discord-Kanals. Leere Webhook-Adresse behält beim Bearbeiten die gespeicherte."""
    name = " ".join(str(form.get("name", "")).split())[:60]
    url = str(form.get("url", "")).strip()
    if not name:
        return None, set(), "Bitte einen Namen für den Kanal angeben"
    if url and not statusfeed.valid_url(url):
        return None, set(), "Bitte eine Discord-Webhook-Adresse eingeben (https://discord.com/api/webhooks/…)"
    if not url and feed is None:
        return None, set(), "Bitte eine Discord-Webhook-Adresse eingeben (https://discord.com/api/webhooks/…)"
    fields = [f for f in DISCORD_FIELDS if form.get(f"field__{f}")]
    if "password" in fields and not form.get("password_confirm"):
        return None, set(), "Passwörter wurden nicht freigegeben: Bitte bestätige ausdrücklich, dass sie im Discord-Kanal sichtbar werden."
    known = server_names()
    servers = {k.split("__", 1)[1] for k in form.keys() if k.startswith("show__")} & set(known)
    values = {"name": name, "enabled": 1 if form.get("enabled") else 0,
              "title": " ".join(str(form.get("title", "")).split())[:60] or None, "fields": ",".join(fields)}
    if url:
        values["url_enc"] = vault.encrypt(url)
        values["msg_ids"] = ""          # neuer Webhook: neue Nachricht posten
    return values, servers, None


def save_feed_servers(con, feed_id: int, servers: set[str]) -> None:
    con.execute("DELETE FROM status_feed_servers WHERE feed_id = ?", (feed_id,))
    con.executemany("INSERT INTO status_feed_servers (feed_id, identifier) VALUES (?, ?)",
                    [(feed_id, i) for i in sorted(servers)])


@app.post("/admin/statuspage/discord")
async def admin_feed_create(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    values, servers, error = feed_form(await request.form())
    if error:
        return back(STATUSPAGE + "#discord", error)
    values["created_at"] = values["updated_at"] = db.utcnow()
    with db.connect() as con:
        feed_id = con.execute(f"INSERT INTO status_feeds ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                              tuple(values.values())).lastrowid
        save_feed_servers(con, feed_id, servers)
    db.set_setting("status_discord_link", f"{link_base(request)}/status", owner.id)
    request.app.state.statusfeed.request_update(feed_id, reset=True)
    known = server_names()
    owner_audit(request, owner, "settings.status", detail={"discord_kanal": values["name"], "neu": True,
                                                            "angaben": values["fields"].split(","),
                                                            "servers": sorted(known[i] for i in servers)})
    return back(STATUSPAGE + "#discord", i18n.t("Discord-Kanal „{name}“ angelegt", name=values["name"]))


def load_feed(feed_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM status_feeds WHERE id = ?", (feed_id,)).fetchone()


@app.post("/admin/statuspage/discord/{feed_id}")
async def admin_feed_update(request: Request, feed_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    feed = load_feed(feed_id)
    if feed is None:
        return back(STATUSPAGE + "#discord")
    form = await request.form()
    values, servers, error = feed_form(form, feed)
    if error:
        return back(STATUSPAGE + "#discord", error)
    old_url = vault.decrypt(feed["url_enc"]) if "url_enc" in values else None
    values["updated_at"] = db.utcnow()
    if values["enabled"]:                    # switched on again (usually with a new link): the notice goes
        values["disabled_code"] = values["disabled_at"] = None
    with db.connect() as con:
        con.execute(f"UPDATE status_feeds SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                    (*values.values(), feed_id))
        save_feed_servers(con, feed_id, servers)
    if old_url:                              # neuer Webhook: alte Nachrichten im alten Kanal aufräumen
        request.app.state.statusfeed.remove(old_url, feed["msg_ids"])
    db.set_setting("status_discord_link", f"{link_base(request)}/status", owner.id)
    request.app.state.statusfeed.request_update(feed_id, reset="url_enc" in values)
    known = server_names()
    owner_audit(request, owner, "settings.status", detail={
        "discord_kanal": values["name"], "aktiv": bool(values["enabled"]), "angaben": values["fields"].split(","),
        "webhook": "neu" if "url_enc" in values else "unverändert", "servers": sorted(known[i] for i in servers)})
    return back(STATUSPAGE + "#discord", i18n.t("Discord-Kanal „{name}“ gespeichert", name=values["name"]))


@app.post("/admin/statuspage/discord/{feed_id}/delete")
async def admin_feed_delete(request: Request, feed_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    feed = load_feed(feed_id)
    if feed is None:
        return back(STATUSPAGE + "#discord")
    with db.connect() as con:
        con.execute("DELETE FROM status_feeds WHERE id = ?", (feed_id,))
    request.app.state.statusfeed.remove(vault.decrypt(feed["url_enc"]), feed["msg_ids"])
    owner_audit(request, owner, "settings.status", detail={"discord_kanal": feed["name"], "geloescht": True})
    return back(STATUSPAGE + "#discord", i18n.t("Discord-Kanal „{name}“ entfernt", name=feed["name"]))


# == System: Ankündigungen ============================================================================

@app.get("/admin/announcements", response_class=HTMLResponse)
async def admin_announcements(request: Request, edit: int | None = None):
    require_owner(request)
    now = db.utcnow()
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM announcements ORDER BY COALESCE(starts_at, created_at) DESC")]
    for r in rows:
        r["state"] = ("upcoming" if r["starts_at"] and r["starts_at"] > now else
                      "ended" if r["ends_at"] and r["ends_at"] <= now else "active")
        r["start_text"] = fmt_dt(r["starts_at"], settings.timezone) if r["starts_at"] else i18n.t("sofort")
        r["end_text"] = fmt_dt(r["ends_at"], settings.timezone) if r["ends_at"] else i18n.t("ohne Ende")
        r["start_input"] = extras.utc_to_local_input(r["starts_at"], settings.timezone)
        r["end_input"] = extras.utc_to_local_input(r["ends_at"], settings.timezone)
    editing = next((r for r in rows if r["id"] == edit), None)
    return render(request, "admin_announcements.html", rows=rows, editing=editing)


@app.post("/admin/announcements")
async def admin_announcement_save(request: Request, ann_id: str = Form(""), text: str = Form(""),
                                  level: str = Form("info"), starts: str = Form(""), ends: str = Form(""),
                                  public: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    text = text.strip()
    if not text or len(text) > 500:
        return back("/admin/announcements", "Text: 1–500 Zeichen")
    try:
        start = extras.local_to_utc(starts, settings.timezone)
        end = extras.local_to_utc(ends, settings.timezone)
    except ValueError:
        return back("/admin/announcements", "Bitte Datum und Uhrzeit prüfen")
    if start and end and end <= start:
        return back("/admin/announcements", "Das Ende muss nach dem Beginn liegen")
    level = "warn" if level == "warn" else "info"
    with db.connect() as con:
        if ann_id.isdigit():
            con.execute("UPDATE announcements SET text = ?, level = ?, starts_at = ?, ends_at = ?, public = ? WHERE id = ?",
                        (text, level, start, end, 1 if public else 0, int(ann_id)))
        else:
            con.execute("INSERT INTO announcements (text, level, starts_at, ends_at, public, created_by, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)", (text, level, start, end, 1 if public else 0, owner.id, db.utcnow()))
    owner_audit(request, owner, "announcement.save", detail={"text": text[:80], "start": start, "end": end})
    return back("/admin/announcements", "Ankündigung gespeichert")


@app.post("/admin/announcements/{ann_id}/delete")
async def admin_announcement_delete(request: Request, ann_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        con.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
    owner_audit(request, owner, "announcement.delete", detail={"id": ann_id})
    return back("/admin/announcements", "Ankündigung gelöscht")


# == Community: Rechtliches (Impressum, Datenschutzerklärung) ===============================

@app.get("/admin/legal", response_class=HTMLResponse)
async def admin_legal(request: Request, vorlage: str = "", sprache: str = ""):
    require_owner(request)
    pages = {}
    for page in legal.PAGES:
        text_value = legal.text(page)
        if vorlage == page:
            text_value = legal.template(page, sprache if sprache in i18n.LANGUAGES else i18n.current())
        pages[page] = {"enabled": legal.enabled(page), "text": text_value, "from_template": vorlage == page}
    return render(request, "admin_legal.html", pages=pages)


@app.post("/admin/legal/{page}")
async def admin_legal_save(request: Request, page: str, enabled: str = Form(""), text: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if page not in legal.PAGES:
        return back("/admin/legal")
    if len(text) > 50000:
        return back("/admin/legal", "Text zu lang (max. 50 000 Zeichen)")
    key = legal.PAGES[page]
    db.set_setting(key + "_enabled", "1" if enabled else "0", owner.id)
    db.set_setting(key + "_text", text.replace("\r\n", "\n").strip(), owner.id)
    owner_audit(request, owner, "settings.legal", detail={"seite": page, "anzeigen": bool(enabled)})
    return back(f"/admin/legal#{page}", "Gespeichert")


# == System: Daten (Aufbewahrung, Export, Import) ======================================================

def verify_owner_password(owner, password: str) -> bool:
    with db.connect() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (owner.id,)).fetchone()
    return bool(row) and security.verify_password(row["password_hash"], password)


@app.get("/admin/data", response_class=HTMLResponse)
async def admin_data(request: Request):
    require_owner(request)
    return render(request, "admin_data.html", choices=db.RETENTION_CHOICES,
                  audit_days=retention.audit_days(), console_days=retention.console_days())


@app.post("/admin/data/retention")
async def admin_data_retention(request: Request, audit_days: str = Form("30"), console_days: str = Form("3")):
    owner = require_owner(request)
    await check_csrf(request)
    try:
        a, c = int(audit_days), int(console_days)
    except ValueError:
        return back("/admin/data", "Ungültige Auswahl")
    if a not in db.RETENTION_CHOICES or (c != 0 and c not in db.RETENTION_CHOICES):
        return back("/admin/data", "Ungültige Auswahl")
    db.set_setting("audit_retention_days", str(a), owner.id)
    db.set_setting("console_retention_days", str(c), owner.id)
    owner_audit(request, owner, "settings.retention", detail={"audit_tage": a, "konsole_tage": c})
    retention.cleanup()
    return back("/admin/data", "Aufbewahrung gespeichert")


@app.post("/admin/data/export")
async def admin_transfer_export(request: Request, passphrase: str = Form(""), repeat: str = Form(""),
                                current: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if not verify_owner_password(owner, current):
        return back("/admin/data", "Das aktuelle Passwort stimmt nicht.")
    if len(passphrase) < 12 or passphrase != repeat:
        return back("/admin/data", "Export-Passwort: mindestens 12 Zeichen, zweimal gleich eingeben")
    import asyncio
    data = await asyncio.to_thread(transfer.build_export, vault, settings.logo_dir, passphrase)
    owner_audit(request, owner, "transfer.export")
    name = f"hatchtower-export-{datetime.now().strftime('%Y-%m-%d_%H-%M')}.htexport"
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


async def read_upload(upload: UploadFile) -> bytes:
    data = await upload.read(transfer.MAX_IMPORT_BYTES + 1)
    await upload.close()
    return data


async def run_import(raw: bytes, passphrase: str) -> dict:
    import asyncio
    data = await asyncio.to_thread(transfer.read_export, raw, passphrase)
    await asyncio.to_thread(transfer.apply_import, data, vault, settings.logo_dir)
    await apply_connection()
    return transfer.summary(data)


@app.post("/admin/data/import")
async def admin_transfer_import(request: Request, file: UploadFile = File(...), passphrase: str = Form(""),
                                current: str = Form(""), confirm: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if not verify_owner_password(owner, current):
        return back("/admin/data", "Das aktuelle Passwort stimmt nicht.")
    if not confirm:
        return back("/admin/data", "Bitte bestätige, dass die Daten dieser Instanz ersetzt werden.")
    try:
        info = await run_import(await read_upload(file), passphrase)
    except transfer.TransferError as exc:
        return back("/admin/data", str(exc))
    audit.record("transfer.import", user_id=owner.id, username=owner.username, ip=ip_of(request), detail=info)
    response = back("/login", i18n.t("Import abgeschlossen: {users} Benutzer, {groups} Gruppen, {schedules} Zeitpläne. "
                                     "Bitte mit einem Konto aus der Exportdatei anmelden.", **info))
    response.delete_cookie(security.SESSION_COOKIE)
    return response


@app.post("/setup/import")
async def setup_import(request: Request, token: str = Form(""), file: UploadFile = File(...),
                       passphrase: str = Form(""), prelogin: str = Form("")):
    """Neue Instanz: Daten aus einer anderen Hatchtower-Installation übernehmen."""
    from .main import owner_step
    if setup.users_exist():
        return RedirectResponse("/login", status_code=303)
    ip = ip_of(request)
    if not prelogin_ok(request, prelogin):
        return owner_step(request, "Das Formular ist abgelaufen. Versuch es noch einmal.", "")
    if throttle.ip_blocked(ip):
        return owner_step(request, "Zu viele Versuche. Warte ein paar Minuten.", "")
    if not setup.check_setup_token(token):
        throttle.record_failure(ip)
        return owner_step(request, "Der Einrichtungscode stimmt nicht oder ist abgelaufen. "
                                   "Einen neuen erzeugst du auf dem Server mit: hatchtower setup-token", "")
    try:
        info = await run_import(await read_upload(file), passphrase)
    except transfer.TransferError as exc:
        throttle.record_failure(ip)
        return owner_step(request, str(exc), "")
    setup.clear_setup_token()
    audit.record("transfer.import", ip=ip, detail=info)
    return back("/login", i18n.t("Import abgeschlossen: {users} Benutzer, {groups} Gruppen, {schedules} Zeitpläne. "
                                 "Bitte mit einem Konto aus der Exportdatei anmelden.", **info))


# == Audit-Export ======================================================================================

def csv_cell(value) -> str:
    """Tabellenprogramme führen Zellen mit = + - @ als Formel aus. Im Audit-Log stehen auch Eingaben von
    Fremden (z. B. der Benutzername eines fehlgeschlagenen Logins), deshalb solche Zellen entschärfen."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/admin/audit/export")
async def admin_audit_export(request: Request, format: str = "csv", action: str = "", username: str = "",
                             server: str = "", ip: str = ""):
    owner = require_owner(request)
    action, username, ip = action.strip()[:100], username.strip()[:64], ip.strip()[:64]
    rows, _ = audit.query(limit=1_000_000, action=audit.match_actions(action, i18n.t) if action else None, username=username or None, server=server or None,
                          ip=ip or None)
    names = server_names()
    header = [i18n.t("Zeit"), i18n.t("Spalte|Benutzer"), "IP", i18n.t("Aktion"), i18n.t("Server"), i18n.t("Details"),
              i18n.t("Ergebnis")]
    records = [[fmt_dt(r["ts"], settings.timezone, seconds=True), r["username"] or "", r["ip"] or "",
                i18n.t(audit.ACTION_LABELS.get(r["action"], r["action"])),
                names.get(r["server"], r["server"] or ""), r["detail"] or "", r["result"]] for r in rows]
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    owner_audit(request, owner, "audit.export", detail={"format": format, "eintraege": len(records)})
    if format == "txt":
        lines = [" | ".join(header)] + [" | ".join(str(c).replace("\n", " ") for c in r) for r in records]
        body, media, ext = "\n".join(lines) + "\n", "text/plain; charset=utf-8", "txt"
    else:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(header)
        writer.writerows([csv_cell(c) for c in r] for r in records)
        body, media, ext = "﻿" + buf.getvalue(), "text/csv; charset=utf-8", "csv"
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''hatchtower-audit-{stamp}.{ext}"})


# == Konsolen-Archiv ================================================================================

LOG_RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "all": 0}


@app.get("/api/servers/{ident}/logs")
async def api_logs(request: Request, ident: str, q: str = "", level: str = "", range: str = "24h",
                   before: int | None = None):
    user = current_user(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "console.archive")
    span = LOG_RANGES.get(range, 86400)
    rows = logarchive.search(ident, q.strip()[:200], level, int(time.time()) - span if span else 0, before, 300)
    return {"enabled": retention.console_days() > 0, "days": retention.console_days(),
            "lines": [{"id": r["id"], "t": ts_text(r["ts"]),
                       "level": r["level"], "text": r["line"]} for r in rows]}


@app.get("/servers/{ident}/logs.txt")
async def logs_download(request: Request, ident: str, q: str = "", level: str = "", range: str = "24h"):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "console.archive")
    span = LOG_RANGES.get(range, 86400)
    rows = logarchive.search(ident, q.strip()[:200], level, int(time.time()) - span if span else 0, None, 100000)
    lines = [f"[{ts_text(r['ts'])}] {r['line']}"
             for r in reversed(rows)]
    audit.record("console.archive_download", user_id=user.id, username=user.username, ip=ip_of(request), server=ident,
                 detail={"range": range, "q": q[:80], "zeilen": len(lines)})
    name = f"{server['name']}-log-{datetime.now().strftime('%Y-%m-%d_%H-%M')}.txt"
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# == Favoriten, Datei wiederherstellen ===============================================================

@app.post("/api/servers/{ident}/favorite")
async def api_favorite(request: Request, ident: str):
    user = current_user(request)
    check_csrf_header(request)
    get_server_or_deny(request, user, ident)
    return {"favorite": extras.toggle_favorite(user.id, ident)}


@app.post("/servers/{ident}/files/restore")
async def files_restore(request: Request, ident: str, file: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    get_server_or_deny(request, user, ident)
    require(request, user, ident, "sftp.edit")
    path = clean_path(file)
    directory = posixpath.dirname(path) or "/"

    async def swap():
        # Wiederherstellen tauscht Datei und Sicherung, so lässt sich auch das rückgängig machen
        backup = await app.state.ptero.read_file(ident, path + BACKUP_SUFFIX)
        current = await app.state.ptero.read_file(ident, path)
        await app.state.ptero.write_file(ident, path + BACKUP_SUFFIX, current)
        await app.state.ptero.write_file(ident, path, backup)

    failed = await file_action(request, ident, directory, "files.restore", path, swap())
    return failed or back(f"/servers/{ident}/files/edit?file={quote(path)}",
                          "Vorherige Fassung wiederhergestellt. Die ersetzte liegt jetzt als .bak daneben.")


# == Updates aus der Oberfläche ======================================================

@app.post("/admin/system/update-check")
async def admin_update_check(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    upd = app.state.updates
    if not await upd.check_now():
        return back("/admin/updates", i18n.t("Bitte warten: Erneut suchen ist in {n} Sekunden möglich.",
                                                    n=upd.cooldown_left()))
    owner_audit(request, owner, "system.update_check", detail={"neueste": upd.latest, "fehler": upd.error})
    if upd.error:
        return back("/admin/updates", upd.error)
    if upd.available:
        return back("/admin/updates", i18n.t("Version {v} ist verfügbar.", v=upd.latest))
    return back("/admin/updates", "Hatchtower ist aktuell.")


@app.post("/admin/system/update-channel")
async def admin_update_channel(request: Request, prereleases: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    on = prereleases == "1"
    upd = app.state.updates
    upd.set_prereleases(on, owner.id)
    owner_audit(request, owner, "system.update_channel", detail={"vorab": on})
    if on and upd.available and upd.latest_is_prerelease:
        return back("/admin/updates", i18n.t("Vorab-Versionen sind an. Vorab-Version {v} ist verfügbar.", v=upd.latest))
    return back("/admin/updates", "Vorab-Versionen sind an." if on else "Vorab-Versionen sind aus.")


@app.post("/admin/system/update")
async def admin_update_start(request: Request):
    from . import updates
    owner = require_owner(request)
    await check_csrf(request)
    upd = app.state.updates
    if not updates.helper_installed():
        return back("/admin/updates", "Das Update über die Oberfläche ist hier noch nicht eingerichtet.")
    if not upd.available:
        return back("/admin/updates", "Es ist kein neueres Update bekannt. Bitte zuerst nach Updates suchen.")
    if not updates.request_update(settings.data_dir, upd.latest or ""):
        return RedirectResponse("/admin/system/update", status_code=303)
    owner_audit(request, owner, "system.update_start", detail={"von": __version__, "auf": upd.latest})
    return RedirectResponse("/admin/system/update", status_code=303)


@app.get("/admin/system/update", response_class=HTMLResponse)
async def admin_update_page(request: Request):
    from . import updates
    require_owner(request)
    return render(request, "admin_update.html", state=updates.read_state(settings.data_dir))


@app.get("/api/system/update-status")
async def api_update_status(request: Request):
    from . import updates
    from .main import record_finished_update
    require_owner(request)
    record_finished_update()   # das Ergebnis steht erst nach dem Neustart fest
    state = updates.read_state(settings.data_dir)
    return {"state": state.get("state", ""), "from": state.get("from", ""), "to": state.get("to", ""),
            "running_version": __version__, "log": state.get("log", "")}


# -- Notification inbox (bell) -------------------------------------------------------------

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, f: str = "", level: str = "", cat: str = "", server: str = ""):
    """The inbox, filterable by level, area and server; errors are counted on top so they stand out."""
    from . import inbox
    user = current_user(request)
    only_unread = f == "unread"
    level = level if level in inbox.LEVELS else ""
    cat = cat if cat in inbox.CATEGORIES else ""
    names = server_names()
    rows = inbox.entries(user, only_unread=only_unread)
    unread_errors = sum(1 for r in inbox.entries(user, only_unread=True) if inbox.level(r) == "error")
    # the level counts follow the other filters, so a click on "errors" shows exactly that many
    base = [r for r in rows if (not cat or inbox.category(r) == cat) and (not server or r["identifier"] == server)]
    counts = {key: sum(1 for r in base if inbox.level(r) == key) for key in inbox.LEVELS}
    shown = [r for r in base if not level or inbox.level(r) == level]
    items = [{"id": r["id"], "title": inbox.title(r), "level": inbox.level(r), "detail": r["detail"] or "",
              "level_label": i18n.t(inbox.LEVELS.get(inbox.level(r), "Info")),
              "server": names.get(r["identifier"], r["identifier"]) if r["identifier"] else "",
              "when": fmt_dt(r["created_at"], settings.timezone), "read": bool(r["is_read"]),
              "link": inbox.link(user, r)}
             for r in shown]
    servers = sorted({(r["identifier"], names.get(r["identifier"], r["identifier"])) for r in rows if r["identifier"]},
                     key=lambda kv: kv[1].casefold())
    params = {"f": "unread" if only_unread else "", "level": level, "cat": cat, "server": server}

    def url(**change) -> str:
        query = {k: v for k, v in {**params, **change}.items() if v}
        return "/notifications" + ("?" + "&".join(f"{k}={quote(v)}" for k, v in query.items()) if query else "")
    return render(request, "notifications.html", items=items, only_unread=only_unread,
                  retention_days=inbox.RETENTION_DAYS, unread_errors=unread_errors, counts=counts,
                  levels=[(key, i18n.t(label)) for key, label in inbox.LEVELS.items()],
                  categories=[(key, i18n.t(label)) for key, (label, _events) in inbox.CATEGORIES.items()],
                  servers=servers, flt=params, url=url, total=len(base),
                  filtered=bool(level or cat or server))


@app.post("/notifications/read-all")
async def notifications_read_all(request: Request):
    from . import inbox
    user = current_user(request)
    await check_csrf(request)
    inbox.mark_all_read(user)
    return back("/notifications", "Alle Meldungen als gelesen markiert")


@app.post("/notifications/{entry_id}/read")
async def notifications_read(request: Request, entry_id: int):
    """Mark one entry as read; with go=1 continue to the page it is about."""
    from . import inbox
    user = current_user(request)
    await check_csrf(request)
    row = inbox.entry(user, entry_id)
    if row is None:
        return back("/notifications")
    inbox.mark_read(user, entry_id)
    form = await request.form()
    target = inbox.link(user, row) if form.get("go") == "1" else None
    return RedirectResponse(target or "/notifications", status_code=303)
