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


GIB = 1024 ** 3


def fmt_gb(num_bytes: float | None) -> str:
    """Bytes as GB the way the panel counts them (1024³, the panel sets limits in MiB): 16384 MiB is "16",
    not 17.2. Trailing zeros are dropped."""
    gb = (num_bytes or 0) / GIB
    text = f"{gb:.2f}" if gb < 10 else f"{gb:.1f}"
    return decimal(text.rstrip("0").rstrip(".") if "." in text else text)


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
        "can_log": user.can(ident, "server.log"),
        "can_players": user.can(ident, "server.players"),
        "can_files": user.can(ident, "sftp.view"),
        "can_manage": user.can(ident, "server.manage"),
        "can_admin": user.can(ident, "admin.view"),
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


PRESENCE_SOURCES = {"query": "Query-Protokoll", "console": "Konsole", "traffic": "Netzwerk-Traffic (Schätzung)"}


def presence_badge(presence: str | None, source: str | None, players: int | None) -> dict:
    """Verdict shown at the top of the 'Spieler' tab; empty while not running."""
    if not presence:
        return {"presence": "", "presence_label": ""}
    if presence == "busy":
        if source == "traffic" or players is None:
            label = t("vermutlich belegt")
        else:
            label = t("1 Spieler online") if players == 1 else t("{n} Spieler online", n=players)
    elif presence == "empty":
        label = t("vermutlich leer") if source == "traffic" else t("niemand online")
    else:
        label = t("Spieler unbekannt")
    return {"presence": presence, "presence_label": label}


SERVER_QUERY = """
SELECT s.*, st.state, st.last_started_at, st.last_stopped_at, st.cpu, st.memory_bytes, st.disk_bytes,
       st.players, st.max_players, st.query_name, st.updated_at,
       m.logo_file, m.ingame_name, m.game_name, m.password_enc, m.public_address, m.query_port, m.tags, m.on_status
FROM servers s
LEFT JOIN server_state st ON st.identifier = s.identifier
LEFT JOIN server_meta m ON m.identifier = s.identifier
WHERE s.present = 1
"""


QUERY_PROTOCOLS = {"a2s": "Steam-Query (A2S)", "minecraft": "Minecraft Java (Server List Ping)",
                   "bedrock": "Minecraft Bedrock (RakNet-Ping)"}


def presence_detail(row, tz: str, show_lines: bool) -> dict:
    """Everything the 'Spieler' tab shows: the verdict and what each source currently sees."""
    from .presence import fmt_rate   # local import: presence imports db, views stays light

    running = (row["state"] or "") == "running" and not is_stale(row["updated_at"])
    try:
        info = json.loads(row["presence_info"] or "{}")
    except ValueError:
        info = {}
    q, c, tr = info.get("query"), info.get("console") or {}, info.get("traffic") or {}
    source = row["presence_source"] if running else None

    def at(ts) -> str:
        return fmt_dt(datetime.fromtimestamp(ts, timezone.utc).isoformat(), tz, seconds=True) if ts else "–"

    query = {"active": bool(q), "decides": source == "query"}
    if q:
        query.update(protocol=t(QUERY_PROTOCOLS.get(q.get("protocol"), q.get("protocol") or "")), port=q.get("port"),
                     players=q.get("players"), max=q.get("max"), at=at(q.get("at")))

    names = c.get("names") or []
    known = c.get("count") is not None or bool(names) or bool(c.get("since_start"))
    console = {"active": running and known, "decides": source == "console", "game": c.get("game"),
               "names": names, "count": c.get("count") if c.get("count") is not None else len(names),
               "max": c.get("max"), "since_start": bool(c.get("since_start")),
               "last_line": c.get("last_line") if show_lines else None, "last_at": at(c.get("last_at"))}

    traffic = {"active": running and tr.get("rate") is not None and tr.get("threshold") is not None,
               "decides": source == "traffic", "rate": fmt_rate(tr.get("rate")),
               "threshold": fmt_rate(tr.get("threshold")), "baseline": fmt_rate(tr.get("baseline")),
               "learned_idle": fmt_rate(tr.get("learned_idle")), "threshold_from": tr.get("threshold_from"),
               "over": (tr.get("rate") or 0) > (tr.get("threshold") or float("inf"))}

    # one sentence: where the verdict comes from
    reason = ""
    if source == "query" and q:
        reason = t("{protocol} auf Port {port} meldet {n} Spieler.", protocol=query["protocol"], port=q.get("port"),
                   n=f'{q.get("players")} / {q.get("max")}' if q.get("max") else q.get("players"))
    elif source == "console":
        if names:
            reason = t("Laut Konsole beigetreten und nicht wieder gegangen: {names}", names=", ".join(names))
        elif c.get("count") is not None:
            reason = (t("Letzte Konsolenzeile mit Spielerzahl: {line}", line=c.get("last_line"))
                      if show_lines and c.get("last_line") else t("Spielerzahl aus einer Konsolenzeile ({game})",
                                                                   game=c.get("game") or "–"))
        else:
            reason = t("Seit dem Serverstart ist laut Konsole niemand beigetreten.")
    elif source == "traffic":
        reason = t("Netzwerk-Traffic {rate}, Grenze für „belegt“ {threshold}.", rate=traffic["rate"],
                   threshold=traffic["threshold"])

    return {"running": running, "source": source, "reason": reason,
            "source_label": t(PRESENCE_SOURCES[source]) if source in PRESENCE_SOURCES else "",
            **presence_badge((row["presence"] or "unknown") if running else None, row["presence_source"],
                             row["presence_players"]),
            "players": row["presence_players"] if running else None,
            "query": query, "console": console, "traffic": traffic,
            "updated_at": fmt_dt(row["updated_at"], tz, seconds=True)}


HISTORY_ROWS = 20


def presence_history(ident: str, tz: str, show_lines: bool) -> list[dict]:
    """Last changes of the player detection, newest first."""
    from . import db
    with db.connect() as con:
        rows = con.execute("SELECT ts, presence, source, players, evidence FROM presence_log WHERE identifier = ?"
                           " ORDER BY id DESC LIMIT ?", (ident, HISTORY_ROWS)).fetchall()
    items = []
    for r in rows:
        evidence = r["evidence"] or ""
        if r["source"] == "query" and ":" in evidence:
            protocol, port = evidence.split(":", 1)
            evidence = t("{protocol}, Port {port}", protocol=t(QUERY_PROTOCOLS.get(protocol, protocol)), port=port)
        elif r["source"] == "console" and not show_lines:
            evidence = ""
        items.append({"ts": fmt_dt(datetime.fromtimestamp(r["ts"], timezone.utc).isoformat(), tz, seconds=True),
                      **presence_badge(r["presence"], r["source"], r["players"]),
                      "source_label": t(PRESENCE_SOURCES[r["source"]]) if r["source"] in PRESENCE_SOURCES else "–",
                      "evidence": evidence})
    return items
