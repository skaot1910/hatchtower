"""Kleinere Bausteine: Verfügbarkeit, Ankündigungen, Tags und Favoriten."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db

ONLINE_STATES = ("running", "starting", "stopping")
TAG_RE = re.compile(r"^[\w .+\-]{1,24}$", re.U)


# -- Verfügbarkeit -----------------------------------------------------------------

def availability(ident: str, days: int = 30) -> dict:
    """Anteil der beobachteten Zeit, in der der Server lief, und Zahl der Abstürze."""
    now = int(time.time())
    start = now - days * 86400
    with db.connect() as con:
        before = con.execute("SELECT state, ts FROM state_changes WHERE identifier = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                             (ident, start)).fetchone()
        rows = con.execute("SELECT state, ts FROM state_changes WHERE identifier = ? AND ts >= ? ORDER BY ts",
                           (ident, start)).fetchall()
        since_iso = datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds")
        crashes = con.execute("SELECT COUNT(*) FROM crash_events WHERE identifier = ? AND detected_at >= ?",
                              (ident, since_iso)).fetchone()[0]
    if before is None and not rows:
        return {"pct": None, "crashes": crashes, "days": 0, "window": days}
    state, t = (before["state"], start) if before else (rows[0]["state"], rows[0]["ts"])
    observed_from = t
    online = 0
    for r in rows:
        if r["ts"] <= t:
            state = r["state"]
            continue
        if state in ONLINE_STATES:
            online += r["ts"] - t
        state, t = r["state"], r["ts"]
    if state in ONLINE_STATES:
        online += now - t
    total = max(now - observed_from, 1)
    return {"pct": round(online / total * 100, 1), "crashes": crashes,
            "days": round(total / 86400, 1), "window": days}


# -- Ankündigungen -----------------------------------------------------------------

def active_announcements(public_only: bool = False) -> list[dict]:
    now = db.utcnow()
    query = ("SELECT * FROM announcements WHERE (starts_at IS NULL OR starts_at <= ?)"
             " AND (ends_at IS NULL OR ends_at > ?)")
    if public_only:
        query += " AND public = 1"
    with db.connect() as con:
        return [dict(r) for r in con.execute(query + " ORDER BY id DESC", (now, now))]


def local_to_utc(value: str, tz: str) -> str | None:
    """Eingabe aus <input type=datetime-local> (Zeitzone von Hatchtower) nach UTC-ISO."""
    if not value.strip():
        return None
    dt = datetime.fromisoformat(value.strip()).replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def utc_to_local_input(value: str | None, tz: str) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M")


# -- Tags und Favoriten ----------------------------------------------------------------

def parse_tags(text: str) -> list[str]:
    tags = []
    for part in (text or "").split(","):
        tag = " ".join(part.split())
        if tag and TAG_RE.match(tag) and tag.lower() not in (t.lower() for t in tags):
            tags.append(tag)
    return tags[:8]


def favorites(user_id: int) -> set[str]:
    with db.connect() as con:
        return {r["identifier"] for r in con.execute("SELECT identifier FROM favorites WHERE user_id = ?", (user_id,))}


def toggle_favorite(user_id: int, ident: str) -> bool:
    with db.connect() as con:
        if con.execute("DELETE FROM favorites WHERE user_id = ? AND identifier = ?", (user_id, ident)).rowcount:
            return False
        con.execute("INSERT INTO favorites (user_id, identifier) VALUES (?, ?)", (user_id, ident))
    return True


# -- Statusseite: Verfügbarkeit pro Tag, Spielerverlauf, Störungen --------------------

def daily_availability(ident: str, days: int, tz: str) -> list[dict]:
    """Pro Tag (in der Zeitzone von Hatchtower): Anteil online, Abstürze, ob Daten vorliegen.
    Ältester Tag zuerst, der heutige Tag zuletzt."""
    from datetime import date, timedelta
    zone = ZoneInfo(tz)
    today = datetime.now(zone).date()
    first = today - timedelta(days=days - 1)
    starts = [datetime(d.year, d.month, d.day, tzinfo=zone) for d in (first + timedelta(n) for n in range(days + 1))]
    t0, now = int(starts[0].timestamp()), int(time.time())
    with db.connect() as con:
        before = con.execute("SELECT state, ts FROM state_changes WHERE identifier = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                             (ident, t0)).fetchone()
        rows = con.execute("SELECT state, ts FROM state_changes WHERE identifier = ? AND ts >= ? ORDER BY ts",
                           (ident, t0)).fetchall()
        crashes = con.execute("SELECT detected_at FROM crash_events WHERE identifier = ? AND detected_at >= ?",
                              (ident, starts[0].astimezone(timezone.utc).isoformat(timespec="seconds"))).fetchall()
    # Zeitabschnitte (von, bis, online?) ab dem ersten bekannten Zustand
    segments = []
    state, since = (before["state"], t0) if before else (None, None)
    for r in rows:
        if state is not None:
            segments.append((since, r["ts"], state in ONLINE_STATES))
        state, since = r["state"], r["ts"]
    if state is not None:
        segments.append((since, now, state in ONLINE_STATES))
    crash_days: dict[date, int] = {}
    for c in crashes:
        d = datetime.fromisoformat(c["detected_at"]).astimezone(zone).date()
        crash_days[d] = crash_days.get(d, 0) + 1
    result = []
    for i in range(days):
        a, b = int(starts[i].timestamp()), min(int(starts[i + 1].timestamp()), now)
        observed = online = 0
        for s, e, on in segments:
            lo, hi = max(s, a), min(e, b)
            if hi > lo:
                observed += hi - lo
                online += (hi - lo) if on else 0
        day = starts[i].date()
        result.append({"date": day, "pct": round(online / observed * 100, 1) if observed else None,
                       "crashes": crash_days.get(day, 0)})
    return result


def player_series(ident: str, hours: int = 24, buckets: int = 48) -> dict | None:
    """Spielerzahl der letzten Stunden, auf feste Abschnitte verdichtet (Maximum je Abschnitt)."""
    now = int(time.time())
    start = now - hours * 3600
    with db.connect() as con:
        rows = con.execute("SELECT ts, players FROM metrics WHERE identifier = ? AND ts >= ? AND players IS NOT NULL ORDER BY ts",
                           (ident, start)).fetchall()
    if not rows:
        return None
    size = hours * 3600 / buckets
    values: list[int | None] = [None] * buckets
    for r in rows:
        i = min(int((r["ts"] - start) / size), buckets - 1)
        values[i] = max(values[i] or 0, r["players"])
    peak = max(v for v in values if v is not None)
    return {"values": values, "peak": peak, "current": rows[-1]["players"] if rows else None}


def sparkline(values: list[int | None], width: int = 240, height: int = 36) -> list[str]:
    """SVG-Punktfolgen (eine je zusammenhängendem Abschnitt) für eine kleine Liniengrafik."""
    peak = max([v for v in values if v is not None] or [0]) or 1
    step = width / max(len(values) - 1, 1)
    lines, current = [], []
    for i, v in enumerate(values):
        if v is None:
            if len(current) > 1:
                lines.append(" ".join(current))
            current = []
            continue
        current.append(f"{i * step:.1f},{height - 2 - v / peak * (height - 4):.1f}")
    if len(current) > 1:
        lines.append(" ".join(current))
    elif len(current) == 1:
        x, y = current[0].split(",")
        lines.append(f"{float(x) - 1:.1f},{y} {float(x) + 1:.1f},{y}")
    return lines
