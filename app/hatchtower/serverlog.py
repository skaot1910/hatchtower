"""Protokoll pro Server: passende Einträge aus dem Audit-Log und die Statuswechsel.

Gezeigt wird, was am Server passiert ist (Start, Stop, Abstürze, Konsolenbefehle, Dateien,
Zeitpläne, Backups, Einstellungen), ohne IP-Adressen und ohne reine Lesezugriffe wie
„Dateien aufgelistet“ oder „Konsole geöffnet“.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import audit, db
from .i18n import t
from .views import STATE_LABELS, fmt_dt

# Art des Eintrags -> Aktionen aus dem Audit-Log
KINDS = {
    "power": ("power.start", "power.stop", "power.restart", "power.kill", "automation.crash", "schedule.run"),
    "files": ("files.upload", "files.edit", "files.rename", "files.mkdir", "files.delete", "files.restore",
              "files.download", "files.download_zip"),
    "console": ("console.command", "console.archive_download"),
    "backups": ("backup.run", "backup.job.update", "backup.file_download", "backup.restore", "backup.verify"),
    "settings": ("server.new", "server.meta.update", "server.logo.update", "automation.settings",
                 "schedule.create", "schedule.update", "schedule.delete", "schedule.warn",
                 "server.admin.details", "server.admin.build", "server.admin.ports", "server.admin.startup",
                 "server.admin.reinstall", "server.admin.suspend", "server.admin.create"),
}
KIND_LABELS = {
    "": "Alles",
    "power": "Start, Stop und Abstürze",
    "files": "Dateien",
    "console": "Konsole",
    "backups": "Backups",
    "settings": "Einstellungen und Zeitpläne",
}
ACTIONS = tuple(a for actions in KINDS.values() for a in actions)
KIND_OF = {a: kind for kind, actions in KINDS.items() for a in actions}
STATES = ("running", "offline")       # „wird gestartet“ und „wird gestoppt“ wären nur Rauschen
PAGE = 25
MAX = 500


def _epoch(iso: str) -> float:
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()


def _detail(raw: str | None) -> str:
    """Details lesbar machen: JSON wird zu „Schlüssel: Wert“, leere Werte entfallen."""
    if not raw:
        return ""
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            if val in (None, "", [], False):
                continue
            if isinstance(val, list):
                val = " → ".join(str(v) for v in val)
            parts.append(key if val is True else f"{key}: {val}")
        return ", ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def entries(ident: str, tz: str, kind: str = "", limit: int = PAGE) -> tuple[list[dict], bool]:
    """Neueste Einträge zuerst. Zweiter Wert: ob es ältere gibt."""
    actions = KINDS.get(kind, ACTIONS)
    limit = max(1, min(limit, MAX))
    marks = ",".join("?" * len(actions))
    items = []
    with db.connect() as con:
        rows = con.execute(
            f"SELECT ts, username, action, detail, result FROM audit_log WHERE server = ? AND action IN ({marks})"
            " AND result IN ('ok', 'error') ORDER BY id DESC LIMIT ?", (ident, *actions, limit + 1)).fetchall()
        for r in rows:
            items.append({
                "at": _epoch(r["ts"]), "ts": fmt_dt(r["ts"], tz, seconds=True), "kind": KIND_OF[r["action"]],
                "action": t(audit.ACTION_LABELS.get(r["action"], r["action"])), "username": r["username"] or "",
                "detail": _detail(r["detail"]), "error": r["result"] == "error",
                "crash": r["action"] == "automation.crash",
            })
        if kind in ("", "power"):
            states = con.execute(
                "SELECT ts, state FROM state_changes WHERE identifier = ? AND state IN (?, ?) ORDER BY ts DESC LIMIT ?",
                (ident, *STATES, limit + 1)).fetchall()
            for r in states:
                iso = datetime.fromtimestamp(r["ts"], timezone.utc).isoformat()
                items.append({
                    "at": float(r["ts"]), "ts": fmt_dt(iso, tz, seconds=True), "kind": "state",
                    "action": t("Status: {state}", state=t(STATE_LABELS.get(r["state"], r["state"]))),
                    "username": "", "detail": "", "error": False, "crash": False, "state": r["state"],
                })
    items.sort(key=lambda e: e["at"], reverse=True)
    return items[:limit], len(items) > limit
