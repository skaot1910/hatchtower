"""Einrichtung: Einrichtungscode, Verbindungstests und Hilfen für den Assistenten."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import db
from .config import DB_KEYS, Settings
from .i18n import t
from .panels import make_client
from .ptero import PteroClient, PteroError

TOKEN_KEY = "setup_token_hash"
TOKEN_CREATED_KEY = "setup_token_created"
TOKEN_VALID_HOURS = 24
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ohne 0/O und 1/I


COMMON_TIMEZONES = ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/Amsterdam", "Europe/London", "UTC"]


def timezone_choices() -> dict:
    """Zeitzonen für die Auswahlliste: die gängigen zuerst, darunter alle übrigen."""
    from zoneinfo import available_timezones
    others = sorted(z for z in available_timezones()
                    if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")) and z not in COMMON_TIMEZONES)
    return {"common": COMMON_TIMEZONES, "others": others}


def users_exist() -> bool:
    with db.connect() as con:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


# -- Einrichtungscode --------------------------------------------------------------

def new_setup_token() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    token = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    db.set_setting(TOKEN_KEY, hashlib.sha256(raw.encode()).hexdigest(), None)
    db.set_setting(TOKEN_CREATED_KEY, db.utcnow(), None)
    return token


def check_setup_token(given: str) -> bool:
    stored = db.get_setting(TOKEN_KEY)
    created = db.get_setting(TOKEN_CREATED_KEY)
    if not stored or not created:
        return False
    try:
        if datetime.now(timezone.utc) - datetime.fromisoformat(created) > timedelta(hours=TOKEN_VALID_HOURS):
            return False
    except ValueError:
        return False
    raw = re.sub(r"[^A-Z0-9]", "", given.upper())
    return hmac.compare_digest(hashlib.sha256(raw.encode()).hexdigest(), stored)


def clear_setup_token() -> None:
    db.set_setting(TOKEN_KEY, "", None)
    db.set_setting(TOKEN_CREATED_KEY, "", None)


# -- Eingaben prüfen ----------------------------------------------------------------

def normalize_url(url: str) -> str | None:
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
        return None
    return url


def key_problem(key: str, prefix: str) -> str | None:
    """Grobe Prüfung vor dem Verbindungstest. Ohne festes Präfix (Calagopus) nur die Länge."""
    key = key.strip()
    if not prefix:
        if len(key) < 20 or any(c.isspace() for c in key):
            return t("Der Key ist zu kurz oder enthält Leerzeichen. Wurde er vollständig kopiert?")
        return None
    if not key.startswith(prefix):
        return t("Der Key muss mit „{prefix}“ beginnen.", prefix=prefix)
    if len(key) != 48:
        return t("Der Key hat {n} statt 48 Zeichen. Wurde er vollständig kopiert?", n=len(key))
    return None


def mask_key(key: str) -> str:
    return f"{key[:5]}…{key[-4:]}" if key else ""


def manager_ip_towards(url: str) -> str | None:
    """Die eigene IP, mit der der Manager das Panel erreicht (für „Allowed IPs“)."""
    host = urlsplit(url).hostname
    if not host:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 80))
            return s.getsockname()[0]
    except OSError:
        return None


def explain(exc: PteroError, which: str, panel: str | None = None) -> str:
    """Fehler aus Verbindungstests in eine verständliche Meldung übersetzen."""
    extra = {"panel": panel} if panel else {}
    if exc.status == 401:
        return t("{panel} lehnt den {which} ab (HTTP 401). Prüfe, ob er vollständig und noch gültig ist.", which=which, **extra)
    if exc.status == 403:
        return (t("{panel} verweigert den Zugriff (HTTP 403). Beim Client-Key passt meist die „Allowed IP“ nicht", **extra)
                if which == "Client-Key" else t("Der {which} hat nicht genug Rechte (HTTP 403).", which=which))
    if exc.status == 404:
        return t("Unter dieser Adresse antwortet kein {panel}-Panel (HTTP 404). Stimmt die URL?", **extra)
    return str(exc)


# -- Verbindungstests mit vorläufigen Einstellungen ------------------------------------

async def test_panel(settings: Settings, panel_type: str, url: str, app_key: str) -> dict:
    temp = dataclasses.replace(settings, panel_type=panel_type, pterodactyl_url=url, app_key=app_key,
                               client_key="", node_overrides={})
    client = make_client(temp)
    try:
        nodes = await client.list_nodes()
        servers = await client.list_servers()
    finally:
        await client.close()
    return {"nodes": nodes, "servers": len(servers)}


async def test_client(settings: Settings, client_key: str) -> int:
    temp = dataclasses.replace(settings, client_key=client_key)
    client = make_client(temp)
    try:
        return await client.client_server_count()
    finally:
        await client.close()


async def tcp_check(host: str, port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def node_checks(client: PteroClient, overrides: dict) -> list[dict]:
    """Erreichbarkeit aller Wings-Nodes (Daemon- und SFTP-Port), mit eventueller anderer Adresse."""
    result = []
    for n in await client.list_nodes():
        fqdn = n.get("fqdn") or ""
        override = overrides.get(fqdn, "")
        host = urlsplit(override).hostname if "://" in override else (override.split(":")[0] if override else fqdn)
        daemon_port = int(n.get("daemon_listen") or 8080)
        sftp_port = int(n.get("daemon_sftp") or 2022)
        if override and "://" not in override and ":" in override:
            daemon_port = int(override.split(":")[1])
        elif "://" in override and urlsplit(override).port:
            daemon_port = urlsplit(override).port
        daemon_ok, sftp_ok = await asyncio.gather(tcp_check(host, daemon_port), tcp_check(host, sftp_port))
        result.append({
            "name": n.get("name"), "fqdn": fqdn, "scheme": n.get("scheme", "http"), "override": override,
            "host": host, "daemon_port": daemon_port, "sftp_port": sftp_port,
            "daemon_ok": daemon_ok, "sftp_ok": sftp_ok, "maintenance": bool(n.get("maintenance_mode")),
        })
    return result


def save_values(values: dict[str, str], vault, user_id: int | None) -> None:
    """Speichert Verbindungswerte; API-Keys werden verschlüsselt."""
    for name, value in values.items():
        key = DB_KEYS[name]
        if name in ("app_key", "client_key"):
            value = vault.encrypt(value) if value else ""
        elif name == "node_overrides":
            value = json.dumps(value)
        db.set_setting(key, value, user_id)
