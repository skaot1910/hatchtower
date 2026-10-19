"""Is anyone on the server right now? A layered guess with a visible source.

No single method works for every game, so three sources are tried in order and the first one that
knows something decides:

1. Query protocol (exact): Steam A2S, Minecraft Java (server list ping) or Bedrock (RakNet ping),
   see gamequery.py.
2. Console patterns: join/leave lines of known games, read from the console stream that the
   console archive follows anyway (logarchive.py). Names are kept as a set, so replayed log
   history after a reconnect does not count anyone twice.
3. Network traffic (estimate): Pterodactyl reports the bytes sent and received by the container.
   Clearly more traffic than the server's idle level means "probably someone is on it".

Nothing here queries the Pterodactyl API, so the API rate limit is not affected.
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from . import db

# (game, join pattern, leave pattern); group "name" is the player name
JOIN_LEAVE = [
    ("Minecraft", re.compile(r"\]: (?:\[[^\]]*\] )?(?P<name>[A-Za-z0-9_.]{2,17}) joined the game"),
     re.compile(r"\]: (?:\[[^\]]*\] )?(?P<name>[A-Za-z0-9_.]{2,17}) left the game")),
    ("Minecraft Bedrock", re.compile(r"Player connected: (?P<name>[^,]+), xuid"),
     re.compile(r"Player disconnected: (?P<name>[^,]+), xuid")),
    ("Factorio", re.compile(r"\[JOIN\] (?P<name>\S+) joined the game"),
     re.compile(r"\[LEAVE\] (?P<name>\S+) left the game")),
    ("Terraria", re.compile(r"^(?:: )?(?P<name>[^:<>\[\]]{1,40}) has joined\.$"),
     re.compile(r"^(?:: )?(?P<name>[^:<>\[\]]{1,40}) has left\.$")),
    ("Palworld", re.compile(r"(?P<name>[^\]\[]{1,40}?) joined the server\."),
     re.compile(r"(?P<name>[^\]\[]{1,40}?) left the server\.")),
    ("7 Days to Die", re.compile(r"Player connected, entityid=\d+, name=(?P<name>[^,]+),"),
     re.compile(r"Player disconnected: .*PlayerName='(?P<name>[^']+)'")),
]

# Lines that state the absolute number of players (e.g. answer to "list", Valheim's periodic status)
COUNT_LINES = [
    ("Minecraft", re.compile(r"There are (?P<n>\d+) (?:of a max(?: of)?|out of maximum) (?P<max>\d+) players online")),
    ("Minecraft", re.compile(r"There are (?P<n>\d+)/(?P<max>\d+) players online")),
    ("Valheim", re.compile(r"Connections (?P<n>\d+) ZDOS")),
    ("Terraria", re.compile(r"^(?:: )?(?P<n>\d+) players? connected\.$")),
    ("Terraria", re.compile(r"^(?:: )?(?P<n>No) players connected\.$")),
]

TRAFFIC_WINDOW = 120          # seconds of traffic that make up the current rate
TRAFFIC_MIN_SPAN = 25         # need at least this much data before guessing
BASELINE_REFRESH = 300        # recompute the idle level from the stored metrics every 5 minutes
BASELINE_HOURS = 24
TRAFFIC_FLOOR = 3 * 1024      # at least 3 KB/s above the idle level counts as "someone is on it"
TRAFFIC_FACTOR = 3.0
LOG_KEEP_SECONDS = 30 * 86400


def fmt_rate(bps: float | None) -> str:
    if bps is None:
        return "–"
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


@dataclass
class ConsoleTrack:
    names: set[str] = field(default_factory=set)
    count: int | None = None          # from an absolute count line
    max_players: int | None = None
    game: str | None = None
    since_start: bool = False         # we saw the server start, so an empty set really means nobody
    last_line: str | None = None
    last_at: float | None = None
    started_at: str | None = None     # server start this state belongs to

    def known(self) -> bool:
        return self.count is not None or bool(self.names) or self.since_start

    def players(self) -> int:
        if self.count is not None:
            return self.count
        return len(self.names)


@dataclass
class TrafficTrack:
    samples: deque = field(default_factory=lambda: deque(maxlen=64))   # (monotonic time, total bytes)
    baseline: float | None = None
    baseline_at: float = 0.0
    learned_idle: float | None = None  # idle rate measured while an exact source said "0 players"

    def rate(self) -> float | None:
        if len(self.samples) < 2:
            return None
        t1, b1 = self.samples[-1]
        for t0, b0 in self.samples:
            if t1 - t0 <= TRAFFIC_WINDOW:
                break
        span = t1 - t0
        if span < TRAFFIC_MIN_SPAN or b1 < b0:
            return None
        return (b1 - b0) / span

    def threshold(self) -> tuple[float | None, str | None]:
        if self.learned_idle is not None:
            base, how = self.learned_idle, "learned"
        elif self.baseline is not None:
            base, how = self.baseline, "history"
        else:
            return None, None
        return max(base * TRAFFIC_FACTOR, base + TRAFFIC_FLOOR), how


class Presence:
    def __init__(self) -> None:
        self.console: dict[str, ConsoleTrack] = {}
        self.traffic: dict[str, TrafficTrack] = {}
        self.query_info: dict[str, dict] = {}
        self._loaded = False
        self._logged: dict[str, tuple] = {}

    # -- persistence across Hatchtower restarts -------------------------------------------------

    def _load(self) -> None:
        self._loaded = True
        try:
            with db.connect() as con:
                rows = con.execute("SELECT identifier, presence_info FROM server_state"
                                   " WHERE presence_info IS NOT NULL").fetchall()
        except Exception:
            return
        for r in rows:
            try:
                info = json.loads(r["presence_info"])
            except ValueError:
                continue
            c = info.get("console") or {}
            if c.get("started_at"):
                self.console[r["identifier"]] = ConsoleTrack(
                    names=set(c.get("names") or []), count=c.get("count"), max_players=c.get("max"),
                    game=c.get("game"), since_start=bool(c.get("since_start")),
                    last_line=c.get("last_line"), last_at=c.get("last_at"), started_at=c.get("started_at"))
            idle = (info.get("traffic") or {}).get("learned_idle")
            if idle is not None:
                self.traffic.setdefault(r["identifier"], TrafficTrack()).learned_idle = float(idle)

    # -- console ---------------------------------------------------------------------------------

    def feed(self, ident: str, line: str) -> None:
        """One console line (without ANSI colours)."""
        if not self._loaded:
            self._load()
        text = line.strip()
        if not text:
            return
        for game, pattern in COUNT_LINES:
            m = pattern.search(text)
            if m:
                track = self.console.setdefault(ident, ConsoleTrack())
                track.count = 0 if m.group("n") == "No" else int(m.group("n"))
                if "max" in m.groupdict() and m.group("max"):
                    track.max_players = int(m.group("max"))
                if track.count == 0:
                    track.names.clear()
                track.game, track.last_line, track.last_at = game, text[:300], time.time()
                return
        for game, join, leave in JOIN_LEAVE:
            m = join.search(text)
            if m:
                track = self.console.setdefault(ident, ConsoleTrack())
                track.names.add(m.group("name").strip())
                track.count = None
                track.game, track.last_line, track.last_at = game, text[:300], time.time()
                return
            m = leave.search(text)
            if m:
                track = self.console.setdefault(ident, ConsoleTrack())
                track.names.discard(m.group("name").strip())
                track.count = None
                track.game, track.last_line, track.last_at = game, text[:300], time.time()
                return

    # -- called by the monitor on every status poll ----------------------------------------------

    def update(self, ident: str, state: str, prev_state: str | None, started_at: str | None,
               rx_tx: int, query) -> tuple[str | None, str | None, int | None, str]:
        """Returns (presence, source, players, info json). presence: busy | empty | unknown,
        None while the server is not running."""
        if not self._loaded:
            self._load()
        traffic = self.traffic.setdefault(ident, TrafficTrack())
        if state != "running":
            # a new start begins with nobody on it; old names would be wrong
            track = self.console.get(ident)
            if track is not None:
                self.console[ident] = ConsoleTrack(game=track.game)
            traffic.samples.clear()
            self.query_info.pop(ident, None)
            return None, None, None, self._info(ident)

        track = self.console.setdefault(ident, ConsoleTrack())
        if _restarted(track.started_at, started_at):
            # restarted between two polls (e.g. by Wings after a crash)
            game = track.game
            track = self.console[ident] = ConsoleTrack(game=game, since_start=True)
        if prev_state in ("offline", "starting", "stopping") and not track.started_at:
            track.since_start = True
        track.started_at = started_at

        now = time.monotonic()
        traffic.samples.append((now, rx_tx))
        if now - traffic.baseline_at > BASELINE_REFRESH:
            traffic.baseline_at = now
            traffic.baseline = _history_baseline(ident)

        if query is not None:
            self.query_info[ident] = {"protocol": query.protocol, "port": query.port, "players": query.players,
                                      "max": query.max_players, "at": time.time()}
        else:
            self.query_info.pop(ident, None)

        presence = source = players = None
        if query is not None:
            presence, source, players = ("busy" if query.players > 0 else "empty"), "query", query.players
        elif track.known():
            players = track.players()
            presence, source = ("busy" if players > 0 else "empty"), "console"
        else:
            rate = traffic.rate()
            limit, _how = traffic.threshold()
            if rate is not None and limit is not None:
                presence, source = ("busy" if rate > limit else "empty"), "traffic"
        if presence is None:
            presence = "unknown"

        # An exact "nobody is on it" teaches the traffic estimate what idle looks like
        if source in ("query", "console") and players == 0:
            rate = traffic.rate()
            if rate is not None:
                traffic.learned_idle = rate if traffic.learned_idle is None else (
                    0.8 * traffic.learned_idle + 0.2 * rate)
        return presence, source, players, self._info(ident)

    def evidence(self, ident: str, source: str | None) -> str:
        """Language-neutral proof for the history: protocol and port, names or line, or the traffic numbers."""
        if source == "query":
            q = self.query_info.get(ident) or {}
            return f'{q.get("protocol")}:{q.get("port")}'
        if source == "console":
            c = self.console.get(ident)
            if c is None:
                return ""
            return ", ".join(sorted(c.names)) if c.names else (c.last_line or "") if c.count is not None else ""
        if source == "traffic":
            t = self.traffic.get(ident)
            if t is None:
                return ""
            return f"{fmt_rate(t.rate())} / {fmt_rate(t.threshold()[0])}"
        return ""

    def record(self, ident: str, presence: str | None, source: str | None, players: int | None) -> None:
        """Write a history entry when the verdict or the player count changes (not while stopped)."""
        if presence is None:
            self._logged.pop(ident, None)
            return
        key = (presence, source, players)
        if ident not in self._logged:
            with db.connect() as con:
                last = con.execute("SELECT presence, source, players FROM presence_log WHERE identifier = ?"
                                   " ORDER BY id DESC LIMIT 1", (ident,)).fetchone()
            self._logged[ident] = tuple(last) if last else None
        if self._logged[ident] == key:
            return
        self._logged[ident] = key
        now = int(time.time())
        with db.connect() as con:
            con.execute("INSERT INTO presence_log (identifier, ts, presence, source, players, evidence)"
                        " VALUES (?, ?, ?, ?, ?, ?)", (ident, now, presence, source, players,
                                                       self.evidence(ident, source)[:300]))
            con.execute("DELETE FROM presence_log WHERE ts < ?", (now - LOG_KEEP_SECONDS,))

    def _info(self, ident: str) -> str:
        info: dict = {}
        c = self.console.get(ident)
        if c is not None:
            info["console"] = {"names": sorted(c.names), "count": c.count, "max": c.max_players, "game": c.game,
                               "since_start": c.since_start, "last_line": c.last_line, "last_at": c.last_at,
                               "started_at": c.started_at}
        t = self.traffic.get(ident)
        if t is not None:
            limit, how = t.threshold()
            info["traffic"] = {"rate": t.rate(), "baseline": t.baseline, "learned_idle": t.learned_idle,
                               "threshold": limit, "threshold_from": how}
        q = self.query_info.get(ident)
        if q is not None:
            info["query"] = q
        return json.dumps(info, separators=(",", ":"))


def _restarted(before: str | None, now: str | None) -> bool:
    """Start time moved noticeably (it is derived from the uptime, so it jitters by a second or two)."""
    if not before or not now:
        return False
    try:
        return abs((datetime.fromisoformat(now) - datetime.fromisoformat(before)).total_seconds()) > 30
    except ValueError:
        return False


def _history_baseline(ident: str) -> float | None:
    """Idle level from the stored metrics: the 10th percentile of the per-minute traffic rate over
    the last 24 hours (about one sample per minute; counter resets after a restart are skipped)."""
    since = int(time.time()) - BASELINE_HOURS * 3600
    try:
        with db.connect() as con:
            rows = con.execute("SELECT ts, rx, tx FROM metrics WHERE identifier = ? AND ts >= ? AND ts % 60 < 10"
                               " AND rx IS NOT NULL ORDER BY ts", (ident, since)).fetchall()
    except Exception:
        return None
    rates = []
    for a, b in zip(rows, rows[1:]):
        span = b["ts"] - a["ts"]
        total = (b["rx"] + b["tx"]) - (a["rx"] + a["tx"])
        if 30 <= span <= 180 and total >= 0:
            rates.append(total / span)
    if len(rates) < 15:
        return None
    rates.sort()
    return rates[len(rates) // 10]
