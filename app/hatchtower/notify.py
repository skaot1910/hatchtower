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

from . import APP_NAME, db, i18n, mailer
from .permissions import load_grants

log = logging.getLogger("hatchtower.notify")

EVENTS = {
    "crash": "Server unerwartet beendet",
    "restarted": "Server nach Absturz neu gestartet",
    "restart_failed": "Neustart nach Absturz fehlgeschlagen",
    "disk": "Speicher fast voll",
    "ram": "RAM-Auslastung dauerhaft hoch",
    "cpu": "CPU-Auslastung dauerhaft hoch",
    "schedule_failed": "Zeitplan fehlgeschlagen",
    "panel_down": "Pterodactyl nicht erreichbar",
    "panel_up": "Pterodactyl wieder erreichbar",
    "new_server": "Neuer Server erkannt",
}
PANEL_EVENTS = {"panel_down", "panel_up", "new_server"}      # Verwaltungsmeldungen: E-Mail nur an Owner
COOLDOWN = {"disk": 6 * 3600, "ram": 3600, "cpu": 3600, "panel_down": 0, "panel_up": 0, "new_server": 0}
DEFAULT_COOLDOWN = 120
COLORS = {"crash": 0xE11D48, "restart_failed": 0xE11D48, "restarted": 0x10B981, "disk": 0xF59E0B,
          "ram": 0xF59E0B, "cpu": 0xF59E0B, "schedule_failed": 0xF59E0B, "panel_down": 0xE11D48,
          "panel_up": 0x10B981, "new_server": 0x8B5CF6, "test": 0x8B5CF6}
WEBHOOK_TYPES = ("discord", "generic")


def thresholds() -> dict[str, int]:
    def num(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(db.get_setting(key, str(default)))))
        except ValueError:
            return default
    return {"disk": num("notify_disk_pct", 90, 50, 99), "ram": num("notify_ram_pct", 90, 50, 99),
            "ram_minutes": num("notify_ram_minutes", 5, 1, 60), "cpu": num("notify_cpu_pct", 90, 50, 100),
            "cpu_minutes": num("notify_cpu_minutes", 5, 1, 60)}


def webhook_events() -> set[str]:
    raw = db.get_setting("notify_webhook_events", ",".join(EVENTS))
    return {e for e in raw.split(",") if e in EVENTS}


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


def _email_recipients(event: str, ident: str | None) -> list[str]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, role, email, notify_events FROM users WHERE is_active = 1 AND notify_email = 1"
            " AND email_verified = 1 AND email IS NOT NULL").fetchall()
    result = []
    for r in rows:
        wanted = set((r["notify_events"] or ",".join(EVENTS)).split(","))
        if event not in wanted and event != "test":
            continue
        if event in PANEL_EVENTS and r["role"] != "owner":
            continue
        if ident and r["role"] != "owner" and "server.view" not in load_grants(r["id"]).get(ident, set()):
            continue
        result.append(r["email"])
    return result


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
        raise RuntimeError(i18n.t("Webhook antwortet mit HTTP {code}", code=response.status_code))


async def deliver(vault, event: str, ident: str | None, detail: str = "", *, only: str | None = None) -> list[str]:
    """Verschickt eine Meldung sofort. only = 'webhook' oder 'mail' für Testnachrichten."""
    previous = i18n.current()
    i18n.use_default()
    try:
        return await _deliver(vault, event, ident, detail, only=only)
    finally:
        i18n.set_language(previous)    # bei einer Testnachricht läuft danach die Anfrage weiter


async def _deliver(vault, event: str, ident: str | None, detail: str, *, only: str | None) -> list[str]:
    name = _server_name(ident)
    title = i18n.t(EVENTS.get(event, "Testnachricht"))
    if name:
        title = f"{title}: {name}"
    text = detail or title
    errors = []
    url = vault.decrypt(db.get_setting("notify_webhook_url_enc"))
    if url and only in (None, "webhook") and (event == "test" or event in webhook_events()):
        try:
            await _post_webhook(url, db.get_setting("notify_webhook_type", "discord"), event, title, text, ident, name)
        except Exception as exc:
            errors.append(f"Webhook: {exc}")
    if only in (None, "mail"):
        cfg = mailer.load_config(vault)
        recipients = _email_recipients(event, ident) if only is None else []
        if cfg.ready and recipients:
            try:
                await mailer.send(cfg, recipients, f"[{db.get_setting('brand_name') or APP_NAME}] {title}",
                                  text + mailer.signature())
            except mailer.MailError as exc:
                errors.append(f"E-Mail: {exc}")
    for e in errors:
        log.warning("Benachrichtigung %s: %s", event, e)
    return errors


def emit(vault, event: str, ident: str | None, detail: str = "") -> None:
    """Meldung im Hintergrund verschicken, sofern die Sperrfrist abgelaufen ist."""
    if event not in EVENTS:
        return
    if not _cooldown_ok(f"{event}:{ident}", COOLDOWN.get(event, DEFAULT_COOLDOWN)):
        return
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
                                                  pct=round(disk / disk_limit * 100), used=round(disk / 1e9, 1),
                                                  limit=round(disk_limit / 1e9, 1)))
        if state == "running" and mem_limit > 0 and mem / mem_limit * 100 >= t["ram"]:
            since = self._ram_since.setdefault(ident, time.monotonic())
            if time.monotonic() - since >= t["ram_minutes"] * 60:
                emit(self.vault, "ram", ident, i18n.t("RAM seit {m} Minuten über {pct} % ({used} von {limit} GB).",
                                                     m=t["ram_minutes"], pct=t["ram"], used=round(mem / 1e9, 2),
                                                     limit=round(mem_limit / 1e9, 2)))
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
