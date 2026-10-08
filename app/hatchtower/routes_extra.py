"""Weitere Seiten von Hatchtower.

Statusseite, Impressum/Datenschutz, Lizenzen, E-Mail (Passwort vergessen, Bestätigung),
Einladungen, Benachrichtigungen, Aufbewahrung, Ankündigungen, Datenumzug, Audit-Export,
Konsolen-Archiv, Favoriten und Datei-Wiederherstellung.

Wird am Ende von main.py importiert und nutzt dessen Helfer.
"""
from __future__ import annotations

import csv
import io
import json
import posixpath
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import quote

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import (APP_NAME, __version__, accounts, audit, db, extras, i18n, legal, logarchive, mailer, notify, retention,
               security, setup, transfer)
from .main import (BACKUP_SUFFIX, SYSTEM_TABS, LoginRequired, app, apply_connection, back, check_csrf, check_csrf_header, clean_path,
                   current_user, file_action, files_back, get_server_or_deny, ip_of, is_https, load_brand,
                   login_response, render, require, require_owner, server_names, settings, throttle, vault)
from .permissions import load_grants
from .views import fmt_dt, is_stale, monogram



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
STATUS_FIELDS = {"name": "Servername aus Pterodactyl", "ingame": "Ingame-Servername", "status": "Status",
                 "started": "Gestartet/Gestoppt (Uhrzeit)", "address": "Adresse (IP und Port)",
                 "password": "Serverpasswort",
                 "uptime": "Verfügbarkeitsbalken (30 Tage)",
                 "incidents": "Störungs- und Wartungshistorie (30 Tage)"}
STATUS_ORDER = {"running": 0, "starting": 1, "stopping": 2, "offline": 3, "suspended": 4, "unknown": 5}
STATUS_DEFAULT = "name,status,address,uptime,incidents"
STATUS_DAYS = 30


def status_fields() -> set[str]:
    raw = db.get_setting("status_fields", STATUS_DEFAULT)
    return {f for f in raw.split(",") if f in STATUS_FIELDS}


def status_servers() -> list[dict]:
    """Nur die eingeschalteten Angaben verlassen den Server – auch in status.json."""
    host = db.get_setting("public_host")
    fields = status_fields()
    with db.connect() as con:
        rows = con.execute(
            "SELECT s.identifier, s.name, s.egg, s.primary_port, s.suspended, s.startup_name, s.startup_password_enc,"
            " st.state, st.updated_at, st.query_name, st.last_started_at, st.last_stopped_at, m.logo_file, m.public_address,"
            " m.ingame_name, m.password_enc"
            " FROM servers s LEFT JOIN server_state st ON st.identifier = s.identifier"
            " JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1 AND m.on_status = 1"
            " ORDER BY s.name COLLATE NOCASE").fetchall()
    result = []
    for r in rows:
        state = "suspended" if r["suspended"] else ("unknown" if is_stale(r["updated_at"]) else (r["state"] or "unknown"))
        running = state in ("running", "starting")
        item = {"identifier": r["identifier"], "egg": r["egg"] or "",
                "monogram": monogram(r["name"] if "name" in fields else (r["egg"] or "?")),
                "logo": f"/status/logo/{r['identifier']}" if r["logo_file"] else None}
        if "name" in fields:
            item["name"] = r["name"]
        if "ingame" in fields:
            item["ingame"] = r["ingame_name"] or r["query_name"] or r["startup_name"] or None
        if "status" in fields:
            item["state"] = state
            item["state_label"] = i18n.t({"suspended": "gesperrt", "unknown": "unbekannt"}.get(state, state))
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
                  fields=fields, summary=status_summary(servers, fields),
                  incidents=status_incidents(servers, fields) if "incidents" in fields else [],
                  history_days=STATUS_DAYS)


def status_label(item: dict) -> str:
    return item.get("name") or item.get("ingame") or item.get("egg") or "Server"


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


def status_incidents(servers: list[dict], fields: set[str]) -> list[dict]:
    """Abstürze der angezeigten Server und öffentliche Wartungen der letzten 30 Tage, neueste zuerst.
    Technische Fehlermeldungen werden nicht gezeigt, nur eine allgemeine Beschreibung."""
    from datetime import timedelta, timezone as tz_utc
    since = (datetime.now(tz_utc.utc) - timedelta(days=STATUS_DAYS)).isoformat(timespec="seconds")
    names = {s["identifier"]: status_label(s) for s in servers}
    items = []
    if names:
        marks = ",".join("?" * len(names))
        with db.connect() as con:
            rows = con.execute(f"SELECT identifier, detected_at, outcome FROM crash_events WHERE detected_at >= ?"
                               f" AND identifier IN ({marks}) ORDER BY detected_at DESC LIMIT 50",
                               (since, *names)).fetchall()
        for r in rows:
            outcome = r["outcome"] or ""
            if outcome.startswith(("Neu gestartet", "Restarted")):
                text = "Absturz – von Hatchtower automatisch neu gestartet"
            elif "Wings" in outcome:
                text = "Absturz – der Server hat sich selbst neu gestartet"
            else:
                text = "Server unerwartet beendet"
            items.append({"kind": "crash", "sort": r["detected_at"], "server": names[r["identifier"]],
                          "time": fmt_dt(r["detected_at"], settings.timezone), "text": i18n.t(text)})
    now = db.utcnow()
    with db.connect() as con:
        anns = con.execute("SELECT * FROM announcements WHERE public = 1 AND (ends_at IS NULL OR ends_at >= ?)"
                           " ORDER BY COALESCE(starts_at, created_at) DESC LIMIT 20", (since,)).fetchall()
    for a in anns:
        start = a["starts_at"] or a["created_at"]
        state = ("upcoming" if a["starts_at"] and a["starts_at"] > now else
                 "ended" if a["ends_at"] and a["ends_at"] <= now else "active")
        items.append({"kind": "maintenance", "sort": start, "state": state, "text": a["text"],
                      "time": fmt_dt(start, settings.timezone) + (" – " + fmt_dt(a["ends_at"], settings.timezone)
                                                                    if a["ends_at"] else "")})
    items.sort(key=lambda x: x["sort"], reverse=True)
    return items[:30]


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
                  sqlite_version=sqlite3.sqlite_version, license_file=LICENSE_FILE.is_file())


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
        text = i18n.t("Hallo {name},\n\njemand (hoffentlich du) möchte das Passwort für Hatchtower zurücksetzen. "
                      "Der Link gilt eine Stunde:\n\n{link}\n\nWenn du das nicht warst, ignoriere diese E-Mail. "
                      "Dein Passwort bleibt dann unverändert.", name=row["username"], link=f"{cfg.base_url}/reset/{raw}")
        subject = i18n.t("Passwort zurücksetzen")
        i18n.set_language(page_lang)

        async def deliver(user_id=row["id"], username=row["username"], email=row["email"]):
            try:
                await mailer.send(cfg, email, subject, text + mailer.signature())
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
    audit.record("email.verify", user_id=row["user_id"], username=user["username"] if user else None, ip=ip_of(request))
    return back("/account", "E-Mail-Adresse bestätigt")


async def send_verify_mail(request: Request, user_id: int, username: str, email: str) -> str | None:
    cfg = mail_config()
    raw = accounts.create("verify", user_id=user_id, email=email)
    text = i18n.t("Hallo {name},\n\nbitte bestätige deine E-Mail-Adresse für Hatchtower. Der Link gilt 24 Stunden:\n\n{link}",
                  name=username, link=f"{cfg.base_url}/verify/{raw}")
    try:
        await mailer.send(cfg, email, i18n.t("E-Mail-Adresse bestätigen"), text + mailer.signature())
    except mailer.MailError as exc:
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
            con.execute("DELETE FROM tokens WHERE purpose = 'verify' AND user_id = ?", (user.id,))
        audit.record("email.change", user_id=user.id, username=user.username, ip=ip_of(request), detail="entfernt")
        return back("/account#email", "E-Mail-Adresse entfernt")
    if not mail_config().ready:
        return back("/account#email", "E-Mail ist in Hatchtower nicht eingeschaltet")
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
    events = [e for e in notify.EVENTS if form.get(f"event__{e}")]
    with db.connect() as con:
        con.execute("UPDATE users SET notify_email = ?, notify_events = ? WHERE id = ?",
                    (1 if notify_email else 0, ",".join(events), user.id))
    audit.record("settings.notify", user_id=user.id, username=user.username, ip=ip_of(request),
                 detail={"email": bool(notify_email), "events": events})
    return back("/account#benachrichtigungen", "Benachrichtigungen gespeichert")


def user_data(user_id: int) -> dict:
    """Alles, was Hatchtower zu einem Benutzer gespeichert hat (Auskunft nach Art. 15 DSGVO)."""
    with db.connect() as con:
        u = dict(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        u.pop("password_hash", None)
        entries = [dict(r) for r in con.execute(
            "SELECT ts, ip, action, server, detail, result FROM audit_log WHERE user_id = ? OR username = ? ORDER BY id",
            (user_id, u["username"]))]
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


# == Einladungen ===============================================================================

@app.post("/admin/users/invite", response_class=HTMLResponse)
async def admin_invite(request: Request, role: str = Form("user"), email: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    role = "owner" if role == "owner" else "user"
    email = email.strip()
    if email and not mailer.valid_email(email):
        return back("/admin/users#einladen", "Bitte eine gültige E-Mail-Adresse eingeben")
    raw = accounts.create("invite", email=email or None, data={"role": role}, created_by=owner.id)
    link = f"{link_base(request)}/invite/{raw}"
    message = "Einladung erstellt. Kopiere den Link, er wird nur jetzt angezeigt."
    if email:
        cfg = mail_config()
        if not cfg.ready:
            message = "Einladung erstellt. E-Mail ist nicht eingeschaltet, gib den Link selbst weiter."
        else:
            text = i18n.t("Hallo,\n\ndu wurdest zu {brand} eingeladen. Über diesen Link legst du dein Konto an "
                          "(gilt 7 Tage):\n\n{link}", brand=load_brand()["name"], link=f"{cfg.base_url}/invite/{raw}")
            try:
                await mailer.send(cfg, email, i18n.t("Einladung zu {brand}", brand=load_brand()["name"]),
                                  text + mailer.signature())
                message = i18n.t("Einladung an {email} geschickt.", email=email)
            except mailer.MailError as exc:
                message = i18n.t("E-Mail konnte nicht verschickt werden: {grund}", grund=exc)
    owner_audit(request, owner, "user.invite", detail={"role": role, "email": email or None})
    from .main import admin_users
    request.state.invite_link = link
    request.state.invite_message = i18n.t(message)
    return await admin_users(request)


@app.post("/admin/users/invites/{token_hash}/revoke")
async def admin_invite_revoke(request: Request, token_hash: str):
    owner = require_owner(request)
    await check_csrf(request)
    if accounts.revoke_invite(token_hash):
        owner_audit(request, owner, "user.invite_revoke")
    return back("/admin/users#einladen", "Einladung zurückgezogen")


@app.get("/invite/{token}", response_class=HTMLResponse)
async def invite_page(request: Request, token: str):
    row = accounts.get(token, "invite")
    if row is None:
        return render(request, "error.html", status=410, title="Link abgelaufen",
                      message="Diese Einladung ist abgelaufen oder wurde schon benutzt. Bitte frag nach einer neuen.")
    return prelogin_page(request, "invite.html", token=token, error=None, username="", email=row["email"] or "",
                         mail_ready=mail_config().ready)


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
                             email=email, mail_ready=ready)

    if not prelogin_ok(request, prelogin):
        return fail("Das Formular ist abgelaufen. Versuch es noch einmal.")
    if throttle.ip_blocked(ip_of(request)):
        return fail("Zu viele Versuche. Warte ein paar Minuten.")
    if not accounts.valid_username(username):
        return fail("Benutzername: 1–32 Zeichen, nur Buchstaben, Zahlen, - _ .")
    error = password_error(password, repeat, username)
    if error:
        return fail(error)
    if email and not mailer.valid_email(email):
        return fail("Bitte eine gültige E-Mail-Adresse eingeben")
    role = "owner" if json.loads(row["data"] or "{}").get("role") == "owner" else "user"
    try:
        with db.connect() as con:
            user_id = con.execute("INSERT INTO users (username, password_hash, role, created_at, language)"
                                  " VALUES (?, ?, ?, ?, ?)",
                                  (username, security.hash_password(password), role, db.utcnow(), i18n.current())).lastrowid
    except Exception:
        throttle.record_failure(ip_of(request))
        return fail("Diesen Benutzernamen gibt es schon")
    accounts.consume(token)
    audit.record("invite.accept", user_id=user_id, username=username, ip=ip_of(request), detail={"role": role})
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
    suggested = cfg.base_url or link_base(request)
    return render(request, "admin_mail.html", cfg=cfg, has_password=bool(cfg.password), suggested_base=suggested,
                  security_options=mailer.SECURITY)


@app.post("/admin/mail")
async def admin_mail_save(request: Request, enabled: str = Form(""), host: str = Form(""), port: str = Form("587"),
                          security_: str = Form("starttls", alias="security"), username: str = Form(""),
                          password: str = Form(""), sender: str = Form(""), sender_name: str = Form(""),
                          base_url: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    problems = []
    host = host.strip()
    if host and (len(host) > 253 or not all(c.isalnum() or c in ".-" for c in host)):
        problems.append("SMTP-Server")
    if not port.strip().isdigit() or not 1 <= int(port) <= 65535:
        problems.append("Port")
    if sender.strip() and not mailer.valid_email(sender.strip()):
        problems.append("Absenderadresse")
    base = setup.normalize_url(base_url) if base_url.strip() else ""
    if base_url.strip() and base is None:
        problems.append("Adresse von Hatchtower")
    if enabled and not (host and sender.strip() and base):
        problems.append("zum Einschalten: Server, Absender und Adresse von Hatchtower")
    if problems:
        return back("/admin/mail", i18n.t("Bitte prüfen: {list}", list=", ".join(i18n.t(x) for x in problems)))
    db.set_setting("mail_enabled", "1" if enabled else "0", owner.id)
    db.set_setting("mail_host", host, owner.id)
    db.set_setting("mail_port", port.strip(), owner.id)
    db.set_setting("mail_security", security_ if security_ in mailer.SECURITY else "starttls", owner.id)
    db.set_setting("mail_username", username.strip(), owner.id)
    if password:
        db.set_setting("mail_password_enc", vault.encrypt(password), owner.id)
    db.set_setting("mail_from", sender.strip(), owner.id)
    db.set_setting("mail_from_name", " ".join(sender_name.split())[:60], owner.id)
    db.set_setting("mail_base_url", base or "", owner.id)
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
    try:
        await mailer.send(mail_config(), to.strip(), i18n.t("Testnachricht von Hatchtower"),
                          i18n.t("Wenn du das liest, funktioniert der E-Mail-Versand.") + mailer.signature())
    except mailer.MailError as exc:
        return back("/admin/mail", i18n.t("E-Mail konnte nicht verschickt werden: {grund}", grund=exc))
    owner_audit(request, owner, "settings.mail", detail="Testnachricht")
    return back("/admin/mail", i18n.t("Testnachricht an {email} verschickt.", email=to.strip()))


# == System: Benachrichtigungen ======================================================================

@app.get("/admin/notify", response_class=HTMLResponse)
async def admin_notify(request: Request):
    require_owner(request)
    url = vault.decrypt(db.get_setting("notify_webhook_url_enc")) or ""
    with db.connect() as con:
        subscribers = con.execute("SELECT username FROM users WHERE notify_email = 1 AND email_verified = 1"
                                  " AND is_active = 1 ORDER BY username").fetchall()
    return render(request, "admin_notify.html", events=notify.EVENTS, active=notify.webhook_events(),
                  webhook_set=bool(url), webhook_hint=(url[:40] + "…") if url else "",
                  webhook_type=db.get_setting("notify_webhook_type", "discord"), limits=notify.thresholds(),
                  subscribers=[r["username"] for r in subscribers], mail_ready=mail_config().ready)


@app.post("/admin/notify")
async def admin_notify_save(request: Request, webhook_url: str = Form(""), webhook_type: str = Form("discord"),
                            disk_pct: str = Form("90"), ram_pct: str = Form("90"), ram_minutes: str = Form("5"),
                            cpu_pct: str = Form("90"), cpu_minutes: str = Form("5"), remove_webhook: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    url = webhook_url.strip()
    if url and (not url.startswith(("https://", "http://")) or len(url) > 500 or any(c.isspace() for c in url)):
        return back("/admin/notify", "Bitte eine gültige Webhook-Adresse eingeben (https://…)")
    numbers = {}
    for key, value, lo, hi in (("disk", disk_pct, 50, 99), ("ram", ram_pct, 50, 99), ("ram_minutes", ram_minutes, 1, 60),
                                 ("cpu", cpu_pct, 50, 100), ("cpu_minutes", cpu_minutes, 1, 60)):
        if not value.strip().isdigit() or not lo <= int(value) <= hi:
            return back("/admin/notify", i18n.t("Bitte prüfen: {list}", list=i18n.t("Schwellenwerte")))
        numbers[key] = value.strip()
    if url:
        db.set_setting("notify_webhook_url_enc", vault.encrypt(url), owner.id)
    elif remove_webhook:
        db.set_setting("notify_webhook_url_enc", "", owner.id)
    db.set_setting("notify_webhook_type", webhook_type if webhook_type in notify.WEBHOOK_TYPES else "discord", owner.id)
    db.set_setting("notify_webhook_events", ",".join(e for e in notify.EVENTS if form.get(f"event__{e}")), owner.id)
    db.set_setting("notify_disk_pct", numbers["disk"], owner.id)
    db.set_setting("notify_ram_pct", numbers["ram"], owner.id)
    db.set_setting("notify_ram_minutes", numbers["ram_minutes"], owner.id)
    db.set_setting("notify_cpu_pct", numbers["cpu"], owner.id)
    db.set_setting("notify_cpu_minutes", numbers["cpu_minutes"], owner.id)
    owner_audit(request, owner, "settings.notify", detail={"webhook": "neu" if url else ("entfernt" if remove_webhook else "unverändert"),
                                                            "type": webhook_type, **numbers})
    return back("/admin/notify", "Benachrichtigungen gespeichert")


@app.post("/admin/notify/test")
async def admin_notify_test(request: Request):
    owner = require_owner(request)
    await check_csrf(request)
    if not db.get_setting("notify_webhook_url_enc"):
        return back("/admin/notify", "Es ist noch kein Webhook eingetragen")
    errors = await notify.deliver(vault, "test", None, i18n.t("Testnachricht von Hatchtower: Benachrichtigungen funktionieren."),
                                  only="webhook")
    owner_audit(request, owner, "settings.notify", detail="Testnachricht", result="error" if errors else "ok")
    if errors:
        return back("/admin/notify", i18n.t("Fehlgeschlagen: {grund}", grund="; ".join(errors)))
    return back("/admin/notify", "Testnachricht verschickt")


# == System: Statusseite ==============================================================================

@app.get("/admin/statuspage", response_class=HTMLResponse)
async def admin_statuspage(request: Request):
    require_owner(request)
    with db.connect() as con:
        servers = con.execute("SELECT s.identifier, s.name, s.egg, COALESCE(m.on_status, 0) AS on_status FROM servers s"
                              " LEFT JOIN server_meta m ON m.identifier = s.identifier WHERE s.present = 1"
                              " ORDER BY s.name COLLATE NOCASE").fetchall()
    return render(request, "admin_statuspage.html", servers=servers, enabled=status_enabled(),
                  fields=STATUS_FIELDS, active_fields=status_fields(),
                  status_title=db.get_setting("status_title"), status_url=f"{link_base(request)}/status")


@app.post("/admin/statuspage")
async def admin_statuspage_save(request: Request, enabled: str = Form(""), title: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    form = await request.form()
    chosen = {k.split("__", 1)[1] for k in form.keys() if k.startswith("show__")}
    known = server_names()
    with db.connect() as con:
        for ident in known:
            con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
            con.execute("UPDATE server_meta SET on_status = ? WHERE identifier = ?", (1 if ident in chosen else 0, ident))
    fields = [f for f in STATUS_FIELDS if form.get(f"field__{f}")]
    if "password" in fields and not form.get("password_confirm"):
        return back("/admin/statuspage", "Passwörter wurden nicht freigegeben: Bitte bestätige ausdrücklich, dass sie öffentlich sichtbar werden.")
    db.set_setting("status_enabled", "1" if enabled else "0", owner.id)
    db.set_setting("status_title", " ".join(title.split())[:60], owner.id)
    db.set_setting("status_fields", ",".join(fields), owner.id)
    owner_audit(request, owner, "settings.status", detail={"enabled": bool(enabled), "angaben": fields,
                                                            "servers": sorted(known[i] for i in chosen if i in known)})
    return back("/admin/statuspage", "Statusseite gespeichert")


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


# == System: Datenschutz (Aufbewahrung, Impressum, Datenschutzerklärung) ====================

@app.get("/admin/privacy", response_class=HTMLResponse)
async def admin_privacy(request: Request, vorlage: str = "", sprache: str = ""):
    require_owner(request)
    pages = {}
    for page in legal.PAGES:
        text_value = legal.text(page)
        if vorlage == page:
            text_value = legal.template(page, sprache if sprache in i18n.LANGUAGES else i18n.current())
        pages[page] = {"enabled": legal.enabled(page), "text": text_value, "from_template": vorlage == page}
    return render(request, "admin_privacy.html", pages=pages, choices=db.RETENTION_CHOICES,
                  audit_days=retention.audit_days(), console_days=retention.console_days())


@app.post("/admin/privacy/retention")
async def admin_privacy_retention(request: Request, audit_days: str = Form("30"), console_days: str = Form("3")):
    owner = require_owner(request)
    await check_csrf(request)
    try:
        a, c = int(audit_days), int(console_days)
    except ValueError:
        return back("/admin/privacy", "Ungültige Auswahl")
    if a not in db.RETENTION_CHOICES or (c != 0 and c not in db.RETENTION_CHOICES):
        return back("/admin/privacy", "Ungültige Auswahl")
    db.set_setting("audit_retention_days", str(a), owner.id)
    db.set_setting("console_retention_days", str(c), owner.id)
    owner_audit(request, owner, "settings.retention", detail={"audit_tage": a, "konsole_tage": c})
    retention.cleanup()
    return back("/admin/privacy", "Aufbewahrung gespeichert")


@app.post("/admin/privacy/legal/{page}")
async def admin_privacy_legal(request: Request, page: str, enabled: str = Form(""), text: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if page not in legal.PAGES:
        return back("/admin/privacy")
    if len(text) > 50000:
        return back("/admin/privacy", "Text zu lang (max. 50 000 Zeichen)")
    key = legal.PAGES[page]
    db.set_setting(key + "_enabled", "1" if enabled else "0", owner.id)
    db.set_setting(key + "_text", text.replace("\r\n", "\n").strip(), owner.id)
    owner_audit(request, owner, "settings.legal", detail={"seite": page, "anzeigen": bool(enabled)})
    return back(f"/admin/privacy#{page}", "Gespeichert")


# == System: Datenumzug ================================================================================

def verify_owner_password(owner, password: str) -> bool:
    with db.connect() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (owner.id,)).fetchone()
    return bool(row) and security.verify_password(row["password_hash"], password)


@app.get("/admin/transfer", response_class=HTMLResponse)
async def admin_transfer(request: Request):
    require_owner(request)
    return render(request, "admin_transfer.html")


@app.post("/admin/transfer/export")
async def admin_transfer_export(request: Request, passphrase: str = Form(""), repeat: str = Form(""),
                                current: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if not verify_owner_password(owner, current):
        return back("/admin/transfer", "Das aktuelle Passwort stimmt nicht.")
    if len(passphrase) < 12 or passphrase != repeat:
        return back("/admin/transfer", "Export-Passwort: mindestens 12 Zeichen, zweimal gleich eingeben")
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


@app.post("/admin/transfer/import")
async def admin_transfer_import(request: Request, file: UploadFile = File(...), passphrase: str = Form(""),
                                current: str = Form(""), confirm: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    if not verify_owner_password(owner, current):
        return back("/admin/transfer", "Das aktuelle Passwort stimmt nicht.")
    if not confirm:
        return back("/admin/transfer", "Bitte bestätige, dass die Daten dieser Instanz ersetzt werden.")
    try:
        info = await run_import(await read_upload(file), passphrase)
    except transfer.TransferError as exc:
        return back("/admin/transfer", str(exc))
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

@app.get("/admin/audit/export")
async def admin_audit_export(request: Request, format: str = "csv", action: str = "", username: str = "",
                             server: str = ""):
    owner = require_owner(request)
    rows, _ = audit.query(limit=1_000_000, action=action or None, username=username or None, server=server or None)
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
        writer.writerows(records)
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
    require(request, user, ident, "console.read")
    span = LOG_RANGES.get(range, 86400)
    rows = logarchive.search(ident, q.strip()[:200], level, int(time.time()) - span if span else 0, before, 300)
    return {"enabled": retention.console_days() > 0, "days": retention.console_days(),
            "lines": [{"id": r["id"], "t": ts_text(r["ts"]),
                       "level": r["level"], "text": r["line"]} for r in rows]}


@app.get("/servers/{ident}/logs.txt")
async def logs_download(request: Request, ident: str, q: str = "", level: str = "", range: str = "24h"):
    user = current_user(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "console.read")
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
        return back("/admin/system#version", i18n.t("Bitte warten: Erneut suchen ist in {n} Sekunden möglich.",
                                                    n=upd.cooldown_left()))
    owner_audit(request, owner, "system.update_check", detail={"neueste": upd.latest, "fehler": upd.error})
    if upd.error:
        return back("/admin/system#version", upd.error)
    if upd.available:
        return back("/admin/system#version", i18n.t("Version {v} ist verfügbar.", v=upd.latest))
    return back("/admin/system#version", "Hatchtower ist aktuell.")


@app.post("/admin/system/update")
async def admin_update_start(request: Request):
    from . import updates
    owner = require_owner(request)
    await check_csrf(request)
    upd = app.state.updates
    if not updates.helper_installed():
        return back("/admin/system#version", "Das Update über die Oberfläche ist hier noch nicht eingerichtet.")
    if not upd.available:
        return back("/admin/system#version", "Es ist kein neueres Update bekannt. Bitte zuerst nach Updates suchen.")
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
