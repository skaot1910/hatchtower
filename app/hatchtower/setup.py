"""Einrichtung: Einrichtungscode, Verbindungstests und Hilfen für den Assistenten."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import ipaddress
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


def utc_offset_label(zone: str, now: datetime | None = None) -> str:
    """Aktueller Abstand zu UTC, z. B. „UTC+2“, „UTC+5:30“ oder „UTC−3“ (mit Sommerzeit, falls gerade gültig)."""
    from zoneinfo import ZoneInfo
    offset = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone)).utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{'+' if minutes > 0 else '−'}{hours}" + (f":{rest:02d}" if rest else "")


def timezone_choices() -> dict:
    """Zeitzonen für die Auswahlliste: die gängigen zuerst, darunter alle übrigen, je mit aktuellem UTC-Abstand."""
    from zoneinfo import available_timezones
    others = sorted(z for z in available_timezones()
                    if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")) and z not in COMMON_TIMEZONES)
    now = datetime.now(timezone.utc)
    offsets = {z: utc_offset_label(z, now) for z in COMMON_TIMEZONES + others}
    return {"common": COMMON_TIMEZONES, "others": others, "offsets": offsets}


def valid_timezone(zone: str) -> bool:
    choices = timezone_choices()
    return zone in choices["common"] or zone in choices["others"]


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


def own_addresses() -> list[str]:
    """Eigene ausgehende IPs (IPv4 und IPv6). Ein UDP-connect fragt nur die Routing-Tabelle, es geht kein Paket raus."""
    result = []
    for family, target in ((socket.AF_INET, "1.1.1.1"), (socket.AF_INET6, "2606:4700:4700::1111")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((target, 53))
                result.append(s.getsockname()[0])
        except OSError:
            pass
    return result


async def dns_check(host: str) -> dict:
    """Wohin zeigt der Name (A/AAAA), und ist das diese Maschine? Für die Netzwerk-Seite."""
    own = own_addresses()
    info: dict = {"host": host, "ips": [], "own": own, "error": None, "match": False, "behind_nat": False,
                  "private": False}
    info["behind_nat"] = bool(own) and all(ipaddress.ip_address(a).is_private for a in own)
    try:
        ipaddress.ip_address(host)
        info["ips"] = [host]
    except ValueError:
        try:
            rows = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None), timeout=5)
            info["ips"] = sorted({r[4][0] for r in rows})
        except (OSError, asyncio.TimeoutError, UnicodeError):
            info["error"] = True
            return info
    info["match"] = bool(set(info["ips"]) & set(own))
    info["private"] = all(ipaddress.ip_address(a).is_private for a in info["ips"])
    return info


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
    """Erreichbarkeit aller Wings-Nodes (Daemon-Port), mit eventueller anderer Adresse."""
    async def one(n: dict) -> dict:
        fqdn = n.get("fqdn") or ""
        override = overrides.get(fqdn, "")
        host = urlsplit(override).hostname if "://" in override else (override.split(":")[0] if override else fqdn)
        daemon_port = int(n.get("daemon_listen") or 8080)
        if override and "://" not in override and ":" in override:
            daemon_port = int(override.split(":")[1])
        elif "://" in override and urlsplit(override).port:
            daemon_port = urlsplit(override).port
        daemon_ok = await tcp_check(host, daemon_port)
        return {
            "name": n.get("name"), "fqdn": fqdn, "scheme": n.get("scheme", "http"), "override": override,
            "host": host, "daemon_port": daemon_port,
            "daemon_ok": daemon_ok, "maintenance": bool(n.get("maintenance_mode")),
        }
    # alle Nodes gleichzeitig, damit ein nicht erreichbarer Node die Seite nicht mehrfach aufhält
    return list(await asyncio.gather(*(one(n) for n in await client.list_nodes())))


def save_values(values: dict[str, str], vault, user_id: int | None) -> None:
    """Speichert Verbindungswerte; API-Keys werden verschlüsselt."""
    for name, value in values.items():
        key = DB_KEYS[name]
        if name in ("app_key", "client_key", "admin_key"):
            value = vault.encrypt(value) if value else ""
        elif name == "node_overrides":
            value = json.dumps(value)
        db.set_setting(key, value, user_id)


# -- Entwurf einer Verbindung (Assistent) ------------------------------------------------------
#
# Was im Assistenten eingegeben wird, landet erst in einem Entwurf im Arbeitsspeicher (Keys verschlüsselt,
# je Owner, 30 Minuten gültig). In die Datenbank kommt es erst, wenn der Owner die Prüfung gesehen und das
# Speichern ausdrücklich bestätigt hat. Nach einem Neustart ist der Entwurf weg; die gespeicherte Verbindung
# bleibt davon unberührt.

DRAFT_MINUTES = 30
_drafts: dict[int, dict] = {}


def _draft_alive(draft: dict) -> bool:
    return datetime.now(timezone.utc) - draft["touched"] < timedelta(minutes=DRAFT_MINUTES)


def draft_get(user_id: int) -> dict | None:
    draft = _drafts.get(user_id)
    if draft is not None and not _draft_alive(draft):
        _drafts.pop(user_id, None)
        return None
    return draft


def draft_start(user_id: int, settings: Settings, vault) -> dict:
    """Neuer Entwurf auf Basis der gespeicherten Verbindung (gespeicherte Keys bleiben, bis man sie ersetzt)."""
    draft = {
        "panel_type": settings.panel_type, "url": settings.pterodactyl_url,
        "app_key": vault.encrypt(settings.app_key) if settings.app_key else "",
        "client_key": vault.encrypt(settings.client_key) if settings.client_key and not settings.panel.single_key else "",
        "node_overrides": dict(settings.node_overrides),
        "repair": settings.configured,          # gab es schon eine Verbindung? Dann nach dem Speichern zur Panel-Seite
        "new_keys": set(), "report": None, "version": 0, "checked_version": -1,
        "touched": datetime.now(timezone.utc),
    }
    _drafts[user_id] = draft
    return draft


def draft_for(user_id: int, settings: Settings, vault) -> dict:
    return draft_get(user_id) or draft_start(user_id, settings, vault)


def same_origin_for_keys(old_url: str, new_url: str) -> bool:
    """Dürfen gespeicherte Keys an die neue Adresse? Nur bei gleichem Host (ohne Groß-/Kleinschreibung) und ohne
    Wechsel von https auf http. Sonst könnte, wer eine Owner-Sitzung übernimmt, die Adresse auf einen eigenen
    Server umstellen und bekäme beim Verbindungstest die Keys im Klartext."""
    old, new = urlsplit(old_url or ""), urlsplit(new_url or "")
    if not old.hostname or not new.hostname or old.hostname.lower() != new.hostname.lower():
        return False
    return not (old.scheme == "https" and new.scheme != "https")


def draft_update(draft: dict, vault, **values) -> list[str]:
    """Werte übernehmen. Leere Keys bedeuten „behalten“ – aber nicht beim Wechsel der Panel-Art (alte Keys passen
    nicht) und nie bei einer Adresse auf einem anderen Host (siehe same_origin_for_keys). Gibt die verworfenen
    Keys zurück."""
    dropped: list[str] = []

    def drop(*names: str) -> None:
        for name in names:
            if draft[name] and not values.get(name):
                dropped.append(name)
            draft[name] = ""
            draft["new_keys"].discard(name)

    if "panel_type" in values and values["panel_type"] != draft["panel_type"]:
        draft["panel_type"] = values["panel_type"]
        drop("app_key", "client_key")
        draft["node_overrides"] = {}
    if values.get("url") is not None:
        if draft["url"] and not same_origin_for_keys(draft["url"], values["url"]):
            drop("app_key", "client_key")
            draft["node_overrides"] = {}
        draft["url"] = values["url"]
    for name in ("app_key", "client_key"):
        if values.get(name):
            draft[name] = vault.encrypt(values[name])
            draft["new_keys"].add(name)
    if values.get("node_overrides") is not None:
        draft["node_overrides"] = dict(values["node_overrides"])
    draft["version"] += 1
    draft["touched"] = datetime.now(timezone.utc)
    return dropped


def draft_candidate(draft: dict, vault):
    from .diagnose import Candidate
    return Candidate(panel_type=draft["panel_type"], url=draft["url"] or "",
                     app_key=vault.decrypt(draft["app_key"]) or "" if draft["app_key"] else "",
                     client_key=vault.decrypt(draft["client_key"]) or "" if draft["client_key"] else "",
                     node_overrides=dict(draft["node_overrides"]))


def draft_clear(user_id: int) -> None:
    _drafts.pop(user_id, None)
