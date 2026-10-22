"""Benachrichtigungen per Webhook (Discord oder allgemein) und E-Mail.

Ereignisse kommen vom Monitoring und von den Automationen. Jede Meldung hat eine
Sperrfrist pro Server, damit z. B. ein voller Speicher nicht im Minutentakt meldet.
E-Mails bekommen nur Benutzer, die das in ihrem Konto eingeschaltet und ihre
Adresse bestätigt haben, und nur für Server, die sie sehen dürfen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from . import APP_NAME, db, i18n, inbox, mailer, security
from .permissions import load_grants
from .views import fmt_gb

log = logging.getLogger("hatchtower.notify")

EVENTS = {
    "crash": "Server unerwartet beendet",
    "restarted": "Server nach Absturz neu gestartet",
    "restart_failed": "Neustart nach Absturz fehlgeschlagen",
    "disk": "Speicher fast voll",
    "ram": "RAM-Auslastung dauerhaft hoch",
    "cpu": "CPU-Auslastung dauerhaft hoch",
    "schedule_failed": "Zeitplan fehlgeschlagen",
    "panel_down": "{panel} nicht erreichbar",
    "panel_up": "{panel} wieder erreichbar",
    "new_server": "Neuer Server erkannt",
    "new_user": "Neuer Benutzer registriert",
    "backup_failed": "Backup fehlgeschlagen",
    "backup_ok": "Backup erfolgreich",
}
# Verwaltungsmeldungen: E-Mail nur an Owner
PANEL_EVENTS = {"panel_down", "panel_up", "new_server", "new_user", "backup_failed", "backup_ok"}
# Notices for owners in the overview and the bell; each can be switched off (System → Notifications)
OWNER_NOTICES = {"new_server": "Neue Server", "new_user": "Neue Benutzer (Registrierung über Einladungslink)"}


def owner_notice(event: str) -> bool:
    return db.get_setting(f"owner_notice_{event}", "1") != "0"


def events_for(is_owner: bool) -> dict[str, str]:
    """Ereignisse, zu denen dieser Benutzer überhaupt E-Mails bekommen kann (Konto-Seite)."""
    return {k: v for k, v in EVENTS.items() if is_owner or k not in PANEL_EVENTS}
COOLDOWN = {"disk": 6 * 3600, "ram": 3600, "cpu": 3600, "panel_down": 0, "panel_up": 0, "new_server": 0, "new_user": 0,
            "backup_failed": 0, "backup_ok": 0}
DEFAULT_COOLDOWN = 120
COLORS = {"crash": 0xE11D48, "restart_failed": 0xE11D48, "restarted": 0x10B981, "disk": 0xF59E0B,
          "ram": 0xF59E0B, "cpu": 0xF59E0B, "schedule_failed": 0xF59E0B, "panel_down": 0xE11D48,
          "panel_up": 0x10B981, "new_server": 0x8B5CF6, "new_user": 0x8B5CF6, "backup_failed": 0xE11D48, "backup_ok": 0x10B981,
          "test": 0x8B5CF6}
WEBHOOK_TYPES = ("discord", "generic")

# Answers that mean the webhook itself is gone, so retrying is pointless: Discord deleted it (404) or its link is no
# longer valid (401); any receiver may say so with 410 Gone. Everything else may be temporary and is only recorded.
GONE_REASONS = {
    404: "Discord kennt den Webhook nicht mehr (HTTP 404) – er wurde vermutlich in Discord gelöscht.",
    401: "Discord lehnt den Webhook ab (HTTP 401) – der Link ist nicht mehr gültig.",
    410: "Der Empfänger meldet, dass es den Webhook nicht mehr gibt (HTTP 410).",
}


def webhook_gone(kind: str, status: int) -> bool:
    return status == 410 or (kind == "discord" and status in (401, 404))


def gone_reason(code: int | None) -> str:
    return i18n.t(GONE_REASONS.get(code or 0, "Webhook antwortet mit HTTP {code}"), code=code)


class WebhookError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status
SECRET_KEY = ""      # setzt main.py; signiert die Abmeldelinks in den E-Mails


def thresholds() -> dict[str, int]:
    def num(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(db.get_setting(key, str(default)))))
        except ValueError:
            return default
    return {"disk": num("notify_disk_pct", 90, 50, 99), "ram": num("notify_ram_pct", 90, 50, 99),
            "ram_minutes": num("notify_ram_minutes", 5, 1, 60), "cpu": num("notify_cpu_pct", 90, 50, 100),
            "cpu_minutes": num("notify_cpu_minutes", 5, 1, 60)}


def load_webhooks() -> list:
    with db.connect() as con:
        return con.execute("SELECT * FROM notify_webhooks ORDER BY title COLLATE NOCASE, id").fetchall()


def hook_events(hook) -> set[str]:
    return {e for e in (hook["events"] or "").split(",") if e in EVENTS}


def hook_servers(hook) -> set[str]:
    """Leere Menge = alle Server, auch künftige."""
    return {i for i in (hook["servers"] or "").split(",") if i}


def hook_wants(hook, event: str, ident: str | None) -> bool:
    if event == "test":
        return True
    if not hook["enabled"] or event not in hook_events(hook):
        return False
    servers = hook_servers(hook)
    # Meldungen ohne Server (Panel) und „Neuer Server“ gehen an jeden Webhook mit dem Ereignis
    return not servers or not ident or event in ("new_server", "new_user") or ident in servers


def _cooldown_ok(key: str, seconds: int) -> bool:
    now = int(time.time())
    with db.connect() as con:
        row = con.execute("SELECT sent_at FROM notify_log WHERE key = ?", (key,)).fetchone()
        if row and now - row["sent_at"] < seconds:
            return False
        con.execute("INSERT INTO notify_log (key, sent_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET sent_at = ?",
                    (key, now, now))
    return True


def _server_name(ident: str | None) -> str:
    if not ident:
        return ""
    with db.connect() as con:
        row = con.execute("SELECT name FROM servers WHERE identifier = ?", (ident,)).fetchone()
    return row["name"] if row else ident


def _email_recipients(event: str, ident: str | None) -> list[dict]:
    """Empfänger mit Adresse, Name und Sprache – jeder bekommt eine eigene E-Mail."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, username, language, role, email, notify_events FROM users WHERE is_active = 1"
            " AND notify_email = 1 AND email_verified = 1 AND email IS NOT NULL").fetchall()
    result = []
    for r in rows:
        wanted = set((r["notify_events"] or ",".join(EVENTS)).split(","))
        if event not in wanted and event != "test":
            continue
        if event in PANEL_EVENTS and r["role"] != "owner":
            continue
        if ident and r["role"] != "owner" and "server.view" not in load_grants(r["id"]).get(ident, set()):
            continue
        result.append({"id": r["id"], "email": r["email"], "name": r["username"], "language": r["language"]})
    return result


def _mail_for(event: str, ident: str | None, name: str, detail: str, base_url: str,
              recipient: str | None = None) -> mailer.Mail:
    """Benachrichtigung als E-Mail, in der gerade eingestellten Sprache."""
    label = i18n.t(EVENTS.get(event, "Testnachricht"))
    title = f"{label}: {name}" if name else label
    brand = mailer.brand_name()
    intro = (i18n.t("für den Server „{name}“ gibt es eine neue Meldung.", name=name) if name
             else i18n.t("es gibt eine neue Meldung von {brand}.", brand=brand))
    paragraphs = [intro] + ([detail] if detail and detail != title else [])
    facts = ([(i18n.t("Server"), name)] if name else []) + [(i18n.t("Ereignis"), label),
                                                             (i18n.t("Zeitpunkt"), mailer.now_text())]
    if event.startswith("backup_"):
        link = f"{base_url}/admin/backups"
    elif event in ("panel_down", "panel_up"):
        link = f"{base_url}/admin/system"
    elif ident:
        link = f"{base_url}/servers/{ident}"
    else:
        link = base_url
    to_server = ident and not event.startswith("backup_") and event not in ("panel_down", "panel_up")
    label_button = i18n.t("Server öffnen") if to_server else i18n.t("{brand} öffnen", brand=brand)
    return mailer.Mail(
        subject=f"[{brand}] {title}", heading=title, name=recipient, paragraphs=paragraphs, facts=facts,
        button=(label_button, link) if base_url else None,
        reason=i18n.t("Du bekommst diese E-Mail, weil du unter Konto → Benachrichtigungen E-Mails zu „{ereignis}“"
                      " eingeschaltet hast.", ereignis=label))


async def _post_webhook(url: str, kind: str, event: str, title: str, text: str, ident: str | None, name: str) -> None:
    brand = db.get_setting("brand_name") or APP_NAME
    if kind == "discord":
        payload = {"username": brand[:80], "embeds": [{
            "title": title[:250], "description": text[:3500], "color": COLORS.get(event, 0x8B5CF6),
            "timestamp": datetime.now(timezone.utc).isoformat(), "footer": {"text": APP_NAME}}]}
    else:
        payload = {"source": APP_NAME, "event": event, "server": ident, "server_name": name, "title": title,
                   "message": text, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.post(url, json=payload)
    if response.status_code >= 400:
        raise WebhookError(i18n.t("Webhook antwortet mit HTTP {code}", code=response.status_code), response.status_code)


async def deliver(vault, event: str, ident: str | None, detail: str = "", *, only: str | None = None,
                  hook_id: int | None = None) -> list[str]:
    """Verschickt eine Meldung sofort. only = 'webhook' oder 'mail' für Testnachrichten,
    hook_id = nur an diesen einen Webhook."""
    previous = i18n.current()
    i18n.use_default()
    try:
        return await _deliver(vault, event, ident, detail, only=only, hook_id=hook_id)
    finally:
        i18n.set_language(previous)    # bei einer Testnachricht läuft danach die Anfrage weiter


async def _deliver(vault, event: str, ident: str | None, detail: str, *, only: str | None,
                   hook_id: int | None = None) -> list[str]:
    name = _server_name(ident)
    title = i18n.t(EVENTS.get(event, "Testnachricht"))
    if name:
        title = f"{title}: {name}"
    text = detail or title
    errors = []
    if only in (None, "webhook"):
        for hook in load_webhooks():
            if (hook_id is not None and hook["id"] != hook_id) or not hook_wants(hook, event, ident):
                continue
            url = vault.decrypt(hook["url_enc"])
            if not url:
                continue
            try:
                await _post_webhook(url, hook["kind"], event, title, text, ident, name)
                _note_hook(hook["id"], None)
            except Exception as exc:
                errors.append(f"Webhook „{hook['title']}“: {exc}")
                _note_hook(hook["id"], str(exc) or exc.__class__.__name__)
                if isinstance(exc, WebhookError) and webhook_gone(hook["kind"], exc.status):
                    disable_webhook(hook, exc.status)
                    errors[-1] = (f"Webhook „{hook['title']}“: {gone_reason(exc.status)} "
                                  + i18n.t("Er wurde deshalb automatisch abgeschaltet."))
    if only in (None, "mail"):
        cfg = mailer.load_config(vault)
        recipients = _email_recipients(event, ident) if only is None else []
        failed = []
        for r in recipients if cfg.ready else []:
            i18n.set_language(r["language"] or i18n.current())
            mail = _mail_for(event, ident, name, detail, cfg.base_url, r["name"])
            if SECRET_KEY:
                mail.unsubscribe = (f"{cfg.base_url}/notify/unsubscribe/"
                                    f"{security.unsubscribe_token(SECRET_KEY, r['id'], r['email'])}")
            i18n.use_default()
            try:
                await mailer.send(cfg, r["email"], mail.subject, mail)
                db.set_setting("mail_last_ok", db.utcnow(), None)
            except mailer.MailError as exc:
                failed.append(str(exc))
                db.set_setting("mail_last_error", f"{db.utcnow()} {exc}"[:500], None)
        if failed:
            errors.append(f"E-Mail ({len(failed)}/{len(recipients)}): {failed[0]}")
    for e in errors:
        log.warning("Benachrichtigung %s: %s", event, e)
    if errors and event != "test" and only is None:
        # Otherwise nobody notices that a webhook or the mail server stopped working
        inbox.add("delivery_failed", "owner", None, "\n".join([title, *errors]), key="delivery", cooldown=3600)
    return errors


def disable_webhook(hook, status: int) -> None:
    """Switch off a webhook that is gone for good and tell the owners why (instead of failing on every event)."""
    with db.connect() as con:
        con.execute("UPDATE notify_webhooks SET enabled = 0, disabled_code = ?, disabled_at = ? WHERE id = ?",
                    (status, db.utcnow(), hook["id"]))
    inbox.add("webhook_disabled", "owner", None,
              f"{hook['title']}: {gone_reason(status)} "
              + i18n.t("Neuen Webhook unter System → Benachrichtigungen eintragen und wieder einschalten."))
    log.warning("Webhook %s automatisch abgeschaltet: HTTP %s", hook["id"], status)


def _note_hook(hook_id: int, error: str | None) -> None:
    """Remember how the last delivery to this webhook went (shown in the support report)."""
    with db.connect() as con:
        if error is None:
            con.execute("UPDATE notify_webhooks SET last_ok_at = ? WHERE id = ?", (db.utcnow(), hook_id))
        else:
            con.execute("UPDATE notify_webhooks SET last_error_at = ?, last_error = ? WHERE id = ?",
                        (db.utcnow(), error[:500], hook_id))


def emit(vault, event: str, ident: str | None, detail: str = "") -> None:
    """Meldung im Hintergrund verschicken, sofern die Sperrfrist abgelaufen ist."""
    if event not in EVENTS:
        return
    if not _cooldown_ok(f"{event}:{ident}", COOLDOWN.get(event, DEFAULT_COOLDOWN)):
        return
    inbox.from_notify(event, ident, detail)
    try:
        asyncio.get_running_loop().create_task(deliver(vault, event, ident, detail))
    except RuntimeError:
        pass


class ResourceWatch:
    """Prüft bei jeder Abfrage Speicher, RAM und CPU gegen die Schwellen."""

    def __init__(self, vault):
        self.vault = vault
        self._ram_since: dict[str, float] = {}
        self._cpu_since: dict[str, float] = {}

    def check(self, ident: str, state: str, mem: int, mem_limit: int, disk: int, disk_limit: int,
              cpu: float = 0.0, cpu_limit: int = 0) -> None:
        t = thresholds()
        if disk_limit > 0 and disk / disk_limit * 100 >= t["disk"]:
            emit(self.vault, "disk", ident, i18n.t("Speicher bei {pct} % des Limits ({used} von {limit} GB).",
                                                  pct=round(disk / disk_limit * 100), used=fmt_gb(disk),
                                                  limit=fmt_gb(disk_limit)))
        if state == "running" and mem_limit > 0 and mem / mem_limit * 100 >= t["ram"]:
            since = self._ram_since.setdefault(ident, time.monotonic())
            if time.monotonic() - since >= t["ram_minutes"] * 60:
                emit(self.vault, "ram", ident, i18n.t("RAM seit {m} Minuten über {pct} % ({used} von {limit} GB).",
                                                     m=t["ram_minutes"], pct=t["ram"], used=fmt_gb(mem),
                                                     limit=fmt_gb(mem_limit)))
        else:
            self._ram_since.pop(ident, None)
        # CPU: Pterodactyl zählt 100 % je Kern, das Limit ebenso (400 = 4 Kerne). Ohne Limit keine Prüfung.
        if state == "running" and cpu_limit > 0 and cpu / cpu_limit * 100 >= t["cpu"]:
            since = self._cpu_since.setdefault(ident, time.monotonic())
            if time.monotonic() - since >= t["cpu_minutes"] * 60:
                emit(self.vault, "cpu", ident, i18n.t("CPU seit {m} Minuten über {pct} % des Limits ({used} % von {limit} %).",
                                                     m=t["cpu_minutes"], pct=t["cpu"], used=round(cpu),
                                                     limit=cpu_limit))
        else:
            self._cpu_since.pop(ident, None)
