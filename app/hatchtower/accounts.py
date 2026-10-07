"""Einmal-Links für Einladungen, Passwort-Zurücksetzen und E-Mail-Bestätigung.

In der Datenbank steht nur der SHA-256-Hash des Links. Ein Link gilt einmal und läuft ab:
Einladung 7 Tage, Passwort zurücksetzen 1 Stunde, E-Mail bestätigen 24 Stunden.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from . import db

VALID_HOURS = {"invite": 7 * 24, "reset": 1, "verify": 24}


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def valid_username(name: str) -> bool:
    return bool(name) and len(name) <= 32 and all(c.isalnum() or c in "-_." for c in name)


def create(purpose: str, *, user_id: int | None = None, email: str | None = None, data: dict | None = None,
           created_by: int | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with db.connect() as con:
        if purpose in ("reset", "verify") and user_id:
            con.execute("DELETE FROM tokens WHERE purpose = ? AND user_id = ?", (purpose, user_id))
        con.execute(
            "INSERT INTO tokens (token_hash, purpose, user_id, email, data, created_at, expires_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_hash(raw), purpose, user_id, email, json.dumps(data or {}), now.isoformat(timespec="seconds"),
             (now + timedelta(hours=VALID_HOURS[purpose])).isoformat(timespec="seconds"), created_by))
    return raw


def get(raw: str, purpose: str):
    if not raw or len(raw) > 100:
        return None
    with db.connect() as con:
        row = con.execute("SELECT * FROM tokens WHERE token_hash = ? AND purpose = ? AND expires_at > ?",
                          (_hash(raw), purpose, db.utcnow())).fetchone()
    return row


def consume(raw: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash(raw),))


def open_invites() -> list:
    with db.connect() as con:
        return con.execute("SELECT t.*, u.username AS creator FROM tokens t LEFT JOIN users u ON u.id = t.created_by"
                           " WHERE t.purpose = 'invite' AND t.expires_at > ? ORDER BY t.created_at DESC",
                           (db.utcnow(),)).fetchall()


def revoke_invite(token_hash: str) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM tokens WHERE token_hash = ? AND purpose = 'invite'",
                           (token_hash,)).rowcount > 0
