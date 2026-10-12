"""Aufbereitung der Serverdaten für die Oberfläche."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .extras import parse_tags
from .i18n import date_format, decimal, t
from .permissions import UserContext
from .security import Vault

STATE_LABELS = {
    "running": "online",
    "starting": "wird gestartet",
    "stopping": "wird gestoppt",
    "offline": "offline",
    "unknown": "unbekannt",
}


STALE_SECONDS = 60     # ältere Zustände gelten als unbekannt (Pterodactyl oder Wings antwortet nicht)


def is_stale(updated_at: str | None) -> bool:
    """True, wenn der gespeicherte Zustand zu alt ist, um ihn noch als aktuell anzuzeigen."""
    if not updated_at:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    return age.total_seconds() > STALE_SECONDS


def monogram(name: str) -> str:
    words = [w for w in re.split(r"[^0-9A-Za-zÄÖÜäöüß]+", name) if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        return words[0][:2].upper()
    return "?"


def fmt_dt(value: str | None, tz: str, seconds: bool = False) -> str:
    if not value:
        return "–"
    try:
        dt = datetime.fromisoformat(value).astimezone(ZoneInfo(tz))
    except ValueError:
        return "–"
    return dt.strftime(date_format(seconds))


def fmt_gb(num_bytes: float | None) -> str:
    gb = (num_bytes or 0) / 1e9
    return decimal(f"{gb:.2f}" if gb < 10 else f"{gb:.1f}")


def fmt_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return decimal(f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}")
        size /= 1024
    return f"{num_bytes} B"


def pct(value: float, limit: float) -> int:
    if limit <= 0:
        return 0
    return max(0, min(100, round(value / limit * 100)))


def _num(value: float) -> str:
    return decimal(f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}")


def cpu_view(cpu: float, limit: int) -> dict:
    """Pterodactyl zählt 100 % pro Kern (400 % = 4 Kerne). Angezeigt wird die Auslastung
    bezogen auf das Limit des Servers, also maximal 100 %."""
    if limit > 0:
        cores = limit / 100
        share = min(cpu / limit * 100, 100.0)
        return {
            "cpu_label": t("CPU · 1 Kern") if cores == 1 else t("CPU · {n} Kerne", n=_num(cores)),
            "cpu_text": f"{share:.0f} %" if share >= 10 or share == 0 else decimal(f"{share:.1f} %"),
            "cpu_pct": round(share),
            "cpu_hint": t("{panel}: {cpu} % von {limit} %", cpu=_num(cpu), limit=limit),
        }
    return {
        "cpu_label": t("CPU · ohne Limit"),
        "cpu_text": t("1 Kern genutzt") if round(cpu) == 100 else t("{n} Kerne genutzt", n=_num(cpu / 100)),
        "cpu_pct": 0,
        "cpu_hint": t("{panel}: {cpu} % (100 % = ein Kern)", cpu=_num(cpu)),
    }


def server_view(row, user: UserContext, vault: Vault, tz: str, public_host: str = "") -> dict:
    ident = row["identifier"]
    state = row["state"] or "unknown"
    stale = is_stale(row["updated_at"])
    if row["suspended"]:
        state_label = t("gesperrt")
    elif stale:
        # Letzter bekannter Zustand ist veraltet: nicht so tun, als liefe alles
        state, state_label = "unknown", t("keine aktuellen Daten")
    else:
        state_label = t(STATE_LABELS.get(state, state))

    ingame_name = row["ingame_name"] or row["query_name"] or row["startup_name"]
    if row["primary_ip"] and row["primary_port"]:
        internal = f"{row['primary_ip']}:{row['primary_port']}"
    else:
        internal = None
    # Adresse für Spieler: eigene Angabe > öffentliche Adresse mit Port > interne Adresse
    if row["public_address"]:
        address = row["public_address"]
    elif public_host and row["primary_port"]:
        address = f"{public_host}:{row['primary_port']}"
    else:
        address = internal or "–"

    password = None
    password_source = None
    if user.can(ident, "server.password"):
        manual = vault.decrypt(row["password_enc"])
        if manual:
            password, password_source = manual, t("manuell")
        else:
            startup = vault.decrypt(row["startup_password_enc"])
            if startup:
                password, password_source = startup, t("aus {panel}")

    running = (row["state"] or "") in ("running", "starting")   # letzter bekannter Zustand
    if running:
        time_label, time_value = t("Gestartet"), fmt_dt(row["last_started_at"], tz)
    else:
        time_label, time_value = t("Heruntergefahren"), fmt_dt(row["last_stopped_at"], tz)

    memory_limit = (row["limit_memory"] or 0) * 1024 * 1024
    disk_limit = (row["limit_disk"] or 0) * 1024 * 1024
    cpu = row["cpu"] or 0.0

    if row["players"] is not None and running:
        players = f"{row['players']} / {row['max_players']}" if row["max_players"] else str(row["players"])
    else:
        players = "–"

    return {
        "identifier": ident,
        "name": row["name"],
        "egg": row["game_name"] or row["egg"] or "",
        "egg_ptero": row["egg"] or "",
        "logo": f"/logos/{row['logo_file']}" if row["logo_file"] else None,
        "monogram": monogram(row["name"]),
        "state": "suspended" if row["suspended"] else state,
        "state_label": state_label,
        "ingame_name": ingame_name or "–",
        "address": address,
        "internal_address": internal,
        "has_public_address": address != internal,
        "password": password,
        "password_source": password_source,
        "show_password_row": user.can(ident, "server.password"),
        "time_label": time_label,
        "time_value": time_value,
        "players": players,
        **cpu_view(cpu, row["limit_cpu"] or 0),
        "mem_text": fmt_gb(row["memory_bytes"]) + (" / " + fmt_gb(memory_limit) + " GB" if memory_limit else " GB"),
        "mem_pct": pct(row["memory_bytes"] or 0, memory_limit),
        "disk_text": fmt_gb(row["disk_bytes"]) + (" / " + fmt_gb(disk_limit) + " GB" if disk_limit else t(" GB / unbegrenzt")),
        "disk_pct": pct(row["disk_bytes"] or 0, disk_limit),
        "updated_at": fmt_dt(row["updated_at"], tz),
        "stale": stale,
        "can_console": user.can(ident, "console.read"),
        "can_archive": user.can(ident, "console.archive"),
        "can_files": user.can(ident, "sftp.view"),
        "can_manage": user.can(ident, "server.manage"),
        "can_start": user.can(ident, "power.start"),
        "can_stop": user.can(ident, "power.stop"),
        "can_restart": user.can(ident, "power.restart"),
        "can_kill": user.is_owner and not user.read_only,
        "can_command": user.can(ident, "console.command"),
        "players_now": row["players"] if running and row["players"] is not None else None,
        "allocations": json.loads(row["allocations"] or "[]"),
        "tags": parse_tags(row["tags"]),
        "on_status": bool(row["on_status"]),
        "favorite": False,
    }


SERVER_QUERY = """
SELECT s.*, st.state, st.last_started_at, st.last_stopped_at, st.cpu, st.memory_bytes, st.disk_bytes,
       st.players, st.max_players, st.query_name, st.updated_at,
       m.logo_file, m.ingame_name, m.game_name, m.password_enc, m.public_address, m.query_port, m.tags, m.on_status
FROM servers s
LEFT JOIN server_state st ON st.identifier = s.identifier
LEFT JOIN server_meta m ON m.identifier = s.identifier
WHERE s.present = 1
"""
