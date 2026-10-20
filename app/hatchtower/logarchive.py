"""Konsolen-Archiv: schreibt die Konsole laufender Server mit.

Wings hält nur das aktuelle Log im Speicher, nach einem Neustart ist es weg. Hatchtower
hält deshalb pro laufendem Server eine Websocket-Verbindung zu Wings (nur lesend:
Anmeldung und Log-Verlauf anfordern) und speichert die Zeilen für die eingestellte Zeit.
Beim Wiederverbinden schickt Wings die letzten Zeilen erneut; doppelte werden übersprungen.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import deque

from . import db, retention
from .ptero import PteroClient, PteroError
from .wings import console_stream

log = logging.getLogger("hatchtower.logarchive")

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LEVEL_ERROR = re.compile(r"\b(error|err|exception|fatal|severe|critical|traceback|crash(ed)?)\b", re.I)
LEVEL_WARN = re.compile(r"\b(warn(ing)?)\b", re.I)
RECHECK = 30
MAX_LINE = 2000


def level_of(line: str) -> str | None:
    if LEVEL_ERROR.search(line):
        return "error"
    if LEVEL_WARN.search(line):
        return "warn"
    return None


class LogArchive:
    def __init__(self, client: PteroClient):
        self.client = client
        self._tasks: dict[str, asyncio.Task] = {}
        self._seen: dict[str, deque[str]] = {}
        self._main: asyncio.Task | None = None
        self.on_status = None        # Rückruf, wenn Wings einen neuen Zustand meldet (z. B. Absturz, Stopp im Spiel)
        self._last_state: dict[str, str] = {}
        self.on_line = None          # callback per console line (player detection, presence.py)

    def start(self) -> None:
        self._main = asyncio.create_task(self._loop(), name="hatchtower-logarchive")

    async def stop(self) -> None:
        tasks = [t for t in [self._main, *self._tasks.values()] if t]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    @property
    def enabled(self) -> bool:
        # follow even with the archive switched off: player detection reads the same stream
        return self.client.settings.configured

    async def _loop(self) -> None:
        while True:
            try:
                wanted = set()
                if self.enabled:
                    with db.connect() as con:
                        wanted = {r["identifier"] for r in con.execute(
                            "SELECT s.identifier FROM servers s JOIN server_state st ON st.identifier = s.identifier"
                            " WHERE s.present = 1 AND st.state IN ('running', 'starting', 'stopping')")}
                for ident in list(self._tasks):
                    if ident not in wanted or self._tasks[ident].done():
                        self._tasks.pop(ident).cancel()
                for ident in wanted - set(self._tasks):
                    self._tasks[ident] = asyncio.create_task(self._follow(ident), name=f"log-{ident}")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Konsolen-Archiv")
            await asyncio.sleep(RECHECK)

    def _store(self, ident: str, lines: list[str], dedup: bool = False) -> None:
        seen = self._seen.setdefault(ident, deque(maxlen=400))
        rows = []
        now = int(time.time())
        keep = retention.console_days() > 0
        for raw in lines:
            line = ANSI.sub("", raw).rstrip()[:MAX_LINE]
            if not line.strip():
                continue
            if self.on_line:
                try:
                    self.on_line(ident, line)
                except Exception:
                    log.exception("Player detection")
            if not keep:
                continue
            digest = hashlib.blake2s(line.encode(), digest_size=8).hexdigest()
            if dedup and digest in seen:
                continue
            seen.append(digest)
            rows.append((ident, now, level_of(line), line))
        if rows:
            with db.connect() as con:
                con.executemany("INSERT INTO console_lines (identifier, ts, level, line) VALUES (?, ?, ?, ?)", rows)

    def _prime(self, ident: str) -> None:
        """Zuletzt gespeicherte Zeilen merken, damit der Log-Verlauf nach einem Neustart
        von Hatchtower nicht doppelt gespeichert wird."""
        if ident in self._seen:
            return
        seen = self._seen.setdefault(ident, deque(maxlen=400))
        with db.connect() as con:
            rows = con.execute("SELECT line FROM console_lines WHERE identifier = ? ORDER BY id DESC LIMIT 400",
                               (ident,)).fetchall()
        for r in reversed(rows):
            seen.append(hashlib.blake2s(r["line"].encode(), digest_size=8).hexdigest())

    async def _follow(self, ident: str) -> None:
        self._prime(ident)
        delay = 5
        while True:
            buffer: list[str] = []
            rejected = False
            connected = last_flush = time.monotonic()
            # Direkt nach dem Verbinden schickt Wings den Log-Verlauf erneut: nur dann Doppelte überspringen
            fresh = lambda: time.monotonic() - connected < 6  # noqa: E731
            try:
                async for item in console_stream(self.client, ident):
                    if item["type"] == "line" and item.get("source") != "daemon error":
                        buffer.append(item["text"])
                    elif item["type"] == "status":
                        # Wings meldet Zustandswechsel sofort – dann nicht auf die nächste Abfrage warten
                        if self._last_state.get(ident) not in (None, item["state"]) and self.on_status:
                            self.on_status(ident)
                        self._last_state[ident] = item["state"]
                    elif item["type"] == "error":
                        # Wings refuses the connection (wrong Origin): retry rarely, every attempt costs an API call
                        rejected = bool(item.get("fatal"))
                        break
                    if buffer and (len(buffer) >= 50 or time.monotonic() - last_flush > 2):
                        self._store(ident, buffer, fresh())
                        buffer, last_flush = [], time.monotonic()
                        delay = 5
            except asyncio.CancelledError:
                if buffer:
                    self._store(ident, buffer, fresh())
                raise
            except PteroError as exc:
                log.info("Konsolen-Archiv %s: %s", ident, exc)
            except Exception:
                log.exception("Konsolen-Archiv %s", ident)
            if buffer:
                self._store(ident, buffer, fresh())
            await asyncio.sleep(600 if rejected else delay)
            delay = min(delay * 2, 120)


def search(ident: str, q: str = "", level: str = "", since: int = 0, before_id: int | None = None,
           limit: int = 200) -> list[dict]:
    where, params = ["identifier = ?"], [ident]
    if q:
        where.append("line LIKE ? ESCAPE '\\'")
        params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if level in ("error", "warn"):
        where.append("level = ?" if level == "error" else "level IN ('error', 'warn')")
        if level == "error":
            params.append("error")
    if since:
        where.append("ts >= ?")
        params.append(since)
    if before_id:
        where.append("id < ?")
        params.append(before_id)
    with db.connect() as con:
        rows = con.execute(f"SELECT id, ts, level, line FROM console_lines WHERE {' AND '.join(where)}"
                           " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    return [dict(r) for r in rows]
