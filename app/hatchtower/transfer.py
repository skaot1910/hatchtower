"""Datenumzug: Export und Import aller Einstellungen einer Hatchtower-Instanz.

Die Exportdatei enthält Benutzer (mit Passwort-Hashes), Rechte, Gruppen, Serverangaben,
Logos, Zeitpläne, Ankündigungen und Einstellungen samt API-Keys. Sie wird deshalb immer
mit einem Passwort verschlüsselt (scrypt + Fernet). Nicht exportiert werden Audit-Log,
Sitzungen, Verlauf und Konsolen-Archiv; die Serverliste liest Hatchtower neu aus Pterodactyl.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import __version__, db

MAGIC = b"HATCHTOWER-EXPORT-1\n"
FORMAT = "hatchtower-export"
SECRET_SETTINGS = ("conn_app_key", "conn_client_key", "mail_password_enc", "notify_webhook_url_enc",
                   "status_discord_url_enc")
SKIP_SETTINGS = ("setup_token_hash", "setup_token_created")
TABLES = ("users", "perm_groups", "perm_group_items", "user_server_permissions", "user_server_groups",
          "favorites", "server_meta", "automation_settings", "schedules", "announcements",
          "user_default_access")
LOGO_NAME = re.compile(r"^[0-9a-f]{32}\.webp$")
MAX_IMPORT_BYTES = 50 * 1024 * 1024


class TransferError(ValueError):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def _columns(con, table: str) -> list[str]:
    return [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]


def build_export(vault, logo_dir: Path, passphrase: str) -> bytes:
    data: dict = {"format": FORMAT, "version": 1, "app_version": __version__, "created_at": db.utcnow(),
                  "settings": {}, "secrets": {}, "tables": {}, "files": {}}
    with db.connect() as con:
        for r in con.execute("SELECT key, value FROM app_settings"):
            if r["key"] in SKIP_SETTINGS:
                continue
            if r["key"] in SECRET_SETTINGS:
                data["secrets"][r["key"]] = vault.decrypt(r["value"]) or ""
            else:
                data["settings"][r["key"]] = r["value"]
        for table in TABLES:
            rows = [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
            if table == "server_meta":
                for row in rows:
                    row["password"] = vault.decrypt(row.pop("password_enc", None))
            data["tables"][table] = rows
        logo_names = {r["logo_file"] for r in con.execute("SELECT logo_file FROM server_meta WHERE logo_file IS NOT NULL")}
    logo_names |= {data["settings"].get("brand_logo_header"), data["settings"].get("brand_logo_login")}
    for name in sorted(n for n in logo_names if n and LOGO_NAME.match(n)):
        path = logo_dir / name
        if path.is_file():
            data["files"][name] = base64.b64encode(path.read_bytes()).decode()
    salt = secrets.token_bytes(16)
    token = Fernet(_key(passphrase, salt)).encrypt(json.dumps(data, ensure_ascii=False).encode())
    return MAGIC + base64.b64encode(salt) + b"\n" + token


def read_export(raw: bytes, passphrase: str) -> dict:
    if len(raw) > MAX_IMPORT_BYTES or not raw.startswith(MAGIC):
        raise TransferError("Das ist keine Hatchtower-Exportdatei.")
    try:
        salt_line, token = raw[len(MAGIC):].split(b"\n", 1)
        data = json.loads(Fernet(_key(passphrase, base64.b64decode(salt_line))).decrypt(token.strip()))
    except InvalidToken:
        raise TransferError("Falsches Passwort oder beschädigte Datei.") from None
    except (ValueError, TypeError):
        raise TransferError("Die Datei ist beschädigt.") from None
    if data.get("format") != FORMAT or data.get("version") != 1:
        raise TransferError("Diese Exportdatei stammt aus einer nicht unterstützten Version.")
    if not any(u.get("role") == "owner" and u.get("is_active") for u in data["tables"].get("users", [])):
        raise TransferError("Die Datei enthält keinen aktiven Owner.")
    return data


def summary(data: dict) -> dict:
    t = data.get("tables", {})
    return {"users": len(t.get("users", [])), "groups": len(t.get("perm_groups", [])),
            "schedules": len(t.get("schedules", [])), "servers": len(t.get("server_meta", [])),
            "logos": len(data.get("files", {})), "created_at": data.get("created_at"),
            "app_version": data.get("app_version")}


def apply_import(data: dict, vault, logo_dir: Path) -> None:
    """Ersetzt alle exportierbaren Daten dieser Instanz durch die aus der Datei."""
    logo_dir.mkdir(parents=True, exist_ok=True)
    for name, b64 in data.get("files", {}).items():
        if LOGO_NAME.match(name):
            content = base64.b64decode(b64)
            if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
                (logo_dir / name).write_bytes(content)
    with db.connect() as con:
        for table in reversed(TABLES):
            con.execute(f"DELETE FROM {table}")
        con.execute("DELETE FROM sessions")
        con.execute("DELETE FROM tokens")
        for table in TABLES:
            cols = _columns(con, table)
            for row in data["tables"].get(table, []):
                row = dict(row)
                if table == "server_meta":
                    password = row.pop("password", None)
                    row["password_enc"] = vault.encrypt(password) if password else None
                keys = [k for k in row if k in cols]
                if keys:
                    con.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                                [row[k] for k in keys])
        values = dict(data.get("settings", {}))
        for key, plain in data.get("secrets", {}).items():
            if key in SECRET_SETTINGS:
                values[key] = vault.encrypt(plain) if plain else ""
        for key, value in values.items():
            if key in SKIP_SETTINGS:
                continue
            con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                        (key, value, db.utcnow()))
