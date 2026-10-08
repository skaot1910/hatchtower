"""Aufbewahrung: alte Daten werden regelmäßig gelöscht.

- Audit-Log (mit IP-Adressen und Benutzernamen): einstellbar 1–90 Tage, Standard 30
- Konsolen-Archiv: einstellbar 1–90 Tage oder aus, Standard 3
- Statuswechsel und Absturz-Ereignisse (ohne Personenbezug): 90 Tage
- abgelaufene Sitzungen und Einmal-Links sofort (abgelaufene Einladungen werden im Audit-Log vermerkt)
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from . import accounts, db

log = logging.getLogger("hatchtower.retention")

FIXED_DAYS = 90


def days(key: str, default: int, allow_off: bool = False) -> int:
    """Eingestellte Aufbewahrung in Tagen (0 = aus, nur wo erlaubt)."""
    try:
        value = int(db.get_setting(key, str(default)))
    except ValueError:
        return default
    if allow_off and value == 0:
        return 0
    return value if value in db.RETENTION_CHOICES else default


def audit_days() -> int:
    return days("audit_retention_days", 30)


def console_days() -> int:
    return days("console_retention_days", 3, allow_off=True)


def _iso_before(n_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n_days)).isoformat(timespec="seconds")


def cleanup() -> dict[str, int]:
    """Löscht alles, was älter als die Aufbewahrungsdauer ist. Gibt die Anzahl je Bereich zurück."""
    removed: dict[str, int] = {}
    now = int(time.time())
    removed["invites"] = accounts.expire_invites()   # vor dem Löschen der Links, damit der Ablauf im Audit-Log steht
    with db.connect() as con:
        removed["audit"] = con.execute("DELETE FROM audit_log WHERE ts < ?", (_iso_before(audit_days()),)).rowcount
        keep = console_days()
        cutoff = now - keep * 86400 if keep else now + 1
        removed["console"] = con.execute("DELETE FROM console_lines WHERE ts < ?", (cutoff,)).rowcount
        removed["states"] = con.execute("DELETE FROM state_changes WHERE ts < ?", (now - FIXED_DAYS * 86400,)).rowcount
        removed["crashes"] = con.execute("DELETE FROM crash_events WHERE detected_at < ?",
                                         (_iso_before(FIXED_DAYS),)).rowcount
        removed["tokens"] = con.execute("DELETE FROM tokens WHERE expires_at < ?", (db.utcnow(),)).rowcount
        removed["notify"] = con.execute("DELETE FROM notify_log WHERE sent_at < ?", (now - 7 * 86400,)).rowcount
        # Sitzungen: höchstens so alt wie die längste erlaubte Sitzung
        removed["sessions"] = con.execute("DELETE FROM sessions WHERE created_at < ?", (_iso_before(2),)).rowcount
        # Abgelaufene Ankündigungen nach 30 Tagen entfernen
        removed["announcements"] = con.execute(
            "DELETE FROM announcements WHERE ends_at IS NOT NULL AND ends_at < ?", (_iso_before(30),)).rowcount
    return removed


async def run_forever() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(cleanup)
            if any(removed.values()):
                log.info("Aufbewahrung: %s", ", ".join(f"{k} {v}" for k, v in removed.items() if v))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Aufräumen fehlgeschlagen")
        await asyncio.sleep(3600)
