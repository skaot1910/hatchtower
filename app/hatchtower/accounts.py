"""Einmal-Links für Einladungen, Passwort-Zurücksetzen und E-Mail-Bestätigung.

In der Datenbank steht nur der SHA-256-Hash des Links. Links laufen ab: Einladung 1–90 Tage
oder unbegrenzt (Standard 7), Passwort zurücksetzen 1 Stunde, E-Mail bestätigen 24 Stunden. Passwort- und E-Mail-Links
gelten einmal, Einladungen so oft wie eingestellt (max_uses, 0 = unbegrenzt).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from . import audit, db
from .i18n import t

VALID_HOURS = {"invite": 7 * 24, "reset": 1, "verify": 24}
INVITE_DAYS = (1, 3, 7, 14, 30, 60, 90, 0)   # 0 = läuft nicht ab
NEVER = "9999-12-31T23:59:59+00:00"
INVITE_USES = (*range(1, 11), 25, 50, 100, 0)   # 0 = unbegrenzt


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def valid_username(name: str) -> bool:
    return bool(name) and len(name) <= 32 and all(c.isalnum() or c in "-_." for c in name)


def create(purpose: str, *, user_id: int | None = None, email: str | None = None, data: dict | None = None,
           created_by: int | None = None, hours: int | None = None, max_uses: int = 1,
           note: str | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with db.connect() as con:
        if purpose in ("reset", "verify") and user_id:
            con.execute("DELETE FROM tokens WHERE purpose = ? AND user_id = ?", (purpose, user_id))
        con.execute(
            "INSERT INTO tokens (token_hash, purpose, user_id, email, data, created_at, expires_at, created_by,"
            " max_uses, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_hash(raw), purpose, user_id, email, json.dumps(data or {}), now.isoformat(timespec="seconds"),
             NEVER if hours == 0 else
             (now + timedelta(hours=hours or VALID_HOURS[purpose])).isoformat(timespec="seconds"), created_by,
             max_uses, note))
    return raw


def get(raw: str, purpose: str):
    if not raw or len(raw) > 100:
        return None
    with db.connect() as con:
        row = con.execute("SELECT * FROM tokens WHERE token_hash = ? AND purpose = ? AND expires_at > ?"
                          " AND (max_uses = 0 OR uses < max_uses)",
                          (_hash(raw), purpose, db.utcnow())).fetchone()
    return row


def consume(raw: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash(raw),))


def claim_invite(raw: str) -> bool:
    """Belegt eine Nutzung der Einladung (atomar, damit gleichzeitige Anmeldungen das Limit nicht überschreiten)."""
    with db.connect() as con:
        return con.execute("UPDATE tokens SET uses = uses + 1 WHERE token_hash = ? AND purpose = 'invite'"
                           " AND expires_at > ? AND (max_uses = 0 OR uses < max_uses)",
                           (_hash(raw), db.utcnow())).rowcount > 0


def release_invite(raw: str) -> None:
    """Gibt eine belegte Nutzung zurück, wenn das Konto doch nicht angelegt wurde."""
    with db.connect() as con:
        con.execute("UPDATE tokens SET uses = uses - 1 WHERE token_hash = ? AND uses > 0", (_hash(raw),))


def finish_invite(raw: str) -> bool:
    """Löscht die Einladung, wenn alle Nutzungen verbraucht sind. True = jetzt aufgebraucht."""
    with db.connect() as con:
        return con.execute("DELETE FROM tokens WHERE token_hash = ? AND purpose = 'invite'"
                           " AND max_uses > 0 AND uses >= max_uses", (_hash(raw),)).rowcount > 0


def invite_info(row) -> dict:
    """Angaben zu einer Einladung fürs Audit-Log."""
    with db.connect() as con:
        creator = con.execute("SELECT username FROM users WHERE id = ?", (row["created_by"],)).fetchone()
    return {"invite": row["note"] or None, "created_by": creator["username"] if creator else None,
            "created": row["created_at"], "uses": uses_text(row["uses"], row["max_uses"])}


def validity_text(days: int) -> str:
    """„gilt 7 Tage“ bzw. „unbegrenzt gültig“ (übersetzt)."""
    if days == 0:
        return t("unbegrenzt gültig")
    return t("gilt 1 Tag") if days == 1 else t("gilt {n} Tage", n=days)


def uses_text(uses: int, max_uses: int) -> str:
    return f"{uses}/{max_uses or '∞'}"


def expire_invites() -> int:
    """Abgelaufene Einladungen ins Audit-Log schreiben und entfernen (läuft mit der Aufbewahrung)."""
    with db.connect() as con:
        rows = con.execute("SELECT * FROM tokens WHERE purpose = 'invite' AND expires_at < ?",
                           (db.utcnow(),)).fetchall()
    for row in rows:
        audit.record("invite.expire", detail={**invite_info(row), "expired": row["expires_at"]})
        with db.connect() as con:
            con.execute("DELETE FROM tokens WHERE token_hash = ?", (row["token_hash"],))
    return len(rows)


def open_invites() -> list:
    with db.connect() as con:
        return con.execute("SELECT t.*, u.username AS creator FROM tokens t LEFT JOIN users u ON u.id = t.created_by"
                           " WHERE t.purpose = 'invite' AND t.expires_at > ? ORDER BY t.created_at DESC",
                           (db.utcnow(),)).fetchall()


def revoke_invite(token_hash: str) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM tokens WHERE token_hash = ? AND purpose = 'invite'",
                           (token_hash,)).rowcount > 0
