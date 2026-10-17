"""Notification inbox: a history of events in the web interface (bell with a counter in the sidebar).

Webhooks and e-mails only reach people who set them up; failures of those channels themselves (e.g. the
Discord server status) used to show up nowhere. Every entry has an audience:

- owner   only owners (panel down/up, new servers, backup summaries, delivery and status feed errors, updates)
- server  owners and every user who may see the server (server.view): crash, restart, failed schedules
- users   only non-owner users who may see the server (owners get the summary of the same event instead)

Read state is per user: a watermark (`users.inbox_read_upto`, set by "mark all as read") plus single entries
in `inbox_read`. Users only see entries created after their account. Entries are removed after RETENTION_DAYS
(retention.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import db, i18n

log = logging.getLogger("hatchtower.inbox")

RETENTION_DAYS = 30
PAGE_LIMIT = 300

# Event → (title, level). Titles are German keys, translated when shown.
EVENTS = {
    "crash": ("Server unerwartet beendet", "error"),
    "restarted": ("Server nach Absturz neu gestartet", "ok"),
    "restart_failed": ("Neustart nach Absturz fehlgeschlagen", "error"),
    "schedule_failed": ("Zeitplan fehlgeschlagen", "warn"),
    "panel_down": ("{panel} nicht erreichbar", "error"),
    "panel_up": ("{panel} wieder erreichbar", "ok"),
    "new_server": ("Neuer Server erkannt", "info"),
    "backup_failed": ("Backup fehlgeschlagen", "error"),
    "backup_partial": ("Backup mit Fehlern", "warn"),
    "statusfeed_failed": ("Discord-Serverstatus wird nicht aktualisiert", "error"),
    "statusfeed_ok": ("Discord-Serverstatus läuft wieder", "ok"),
    "delivery_failed": ("Benachrichtigung nicht zugestellt", "error"),
    "update": ("Neue Version verfügbar", "info"),
}
# Events from notify.emit that also go into the inbox, with their audience
FROM_NOTIFY = {"crash": "server", "restarted": "server", "restart_failed": "server", "schedule_failed": "server",
               "panel_down": "owner", "panel_up": "owner", "new_server": "owner"}
AUDIENCES = ("owner", "server", "users")


def add(event: str, audience: str, identifier: str | None = None, detail: str = "", *,
        key: str | None = None, cooldown: int = 0) -> int | None:
    """New entry. With key and cooldown (seconds), an entry with the same key in that time is not repeated."""
    if event not in EVENTS or audience not in AUDIENCES:
        return None
    try:
        with db.connect() as con:
            if key and cooldown:
                since = (datetime.now(timezone.utc) - timedelta(seconds=cooldown)).isoformat(timespec="seconds")
                if con.execute("SELECT 1 FROM inbox WHERE dedupe = ? AND created_at >= ?", (key, since)).fetchone():
                    return None
            return con.execute(
                "INSERT INTO inbox (created_at, event, audience, identifier, detail, dedupe) VALUES (?, ?, ?, ?, ?, ?)",
                (db.utcnow(), event, audience, identifier, (detail or "")[:4000] or None, key)).lastrowid
    except Exception:
        # The inbox must never break the event that reports into it
        log.exception("Inbox entry %s failed", event)
        return None


def from_notify(event: str, identifier: str | None, detail: str) -> None:
    audience = FROM_NOTIFY.get(event)
    if audience:
        add(event, audience, identifier, detail)


def visible(user, row) -> bool:
    audience, ident = row["audience"], row["identifier"]
    if audience == "owner":
        return user.is_owner
    if not ident:
        return user.is_owner
    if audience == "users" and user.is_owner:
        return False
    return user.visible(ident)


def _rows(user, *, unread: bool = False, entry_id: int | None = None, limit: int = PAGE_LIMIT) -> list:
    where = " AND i.id > :upto AND r.entry_id IS NULL" if unread else ""
    if entry_id is not None:
        where += " AND i.id = :entry"
    with db.connect() as con:
        me = con.execute("SELECT created_at, inbox_read_upto FROM users WHERE id = ?", (user.id,)).fetchone()
        if me is None:
            return []
        rows = con.execute(
            "SELECT i.*, (i.id <= :upto OR r.entry_id IS NOT NULL) AS is_read FROM inbox i"
            " LEFT JOIN inbox_read r ON r.entry_id = i.id AND r.user_id = :uid"
            f" WHERE i.created_at >= :since{where} ORDER BY i.id DESC LIMIT :limit",
            {"upto": me["inbox_read_upto"], "uid": user.id, "since": me["created_at"], "entry": entry_id,
             "limit": limit}).fetchall()
    return [r for r in rows if visible(user, r)]


def unread_count(user) -> int:
    try:
        return len(_rows(user, unread=True, limit=1000))
    except Exception:
        log.exception("Inbox counter failed")
        return 0


def entries(user, only_unread: bool = False) -> list:
    return _rows(user, unread=only_unread)


def entry(user, entry_id: int):
    """One entry, only if this user may see it."""
    rows = _rows(user, entry_id=entry_id, limit=1)
    return rows[0] if rows else None


def mark_read(user, entry_id: int) -> None:
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO inbox_read (user_id, entry_id) VALUES (?, ?)", (user.id, entry_id))


def mark_all_read(user) -> None:
    """Everything up to the newest entry counts as read (also entries this user cannot see, which is harmless)."""
    with db.connect() as con:
        top = con.execute("SELECT COALESCE(MAX(id), 0) FROM inbox").fetchone()[0]
        con.execute("UPDATE users SET inbox_read_upto = MAX(inbox_read_upto, ?) WHERE id = ?", (top, user.id))
        con.execute("DELETE FROM inbox_read WHERE user_id = ? AND entry_id <= ?", (user.id, top))


def title(row) -> str:
    return i18n.t(EVENTS.get(row["event"], (row["event"], "info"))[0])


def level(row) -> str:
    return EVENTS.get(row["event"], ("", "info"))[1]


def link(user, row) -> str | None:
    """Where the entry leads, only to pages this user may open."""
    event, ident = row["event"], row["identifier"]
    if event in ("panel_down", "panel_up"):
        return "/admin/system"
    if event in ("statusfeed_failed", "statusfeed_ok"):
        return "/admin/statuspage"
    if event == "delivery_failed":
        return "/admin/notify"
    if event == "update":
        return "/admin/updates"
    if event.startswith("backup_"):
        if user.can_backup:
            return "/admin/backups"
        return f"/servers/{ident}" if ident and user.visible(ident) else None
    if event == "schedule_failed" and user.can_automate:
        return "/admin/automation"
    return f"/servers/{ident}" if ident and user.visible(ident) else None


def cleanup() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    with db.connect() as con:
        return con.execute("DELETE FROM inbox WHERE created_at < ?", (cutoff,)).rowcount
