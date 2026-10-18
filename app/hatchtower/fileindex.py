"""File search per server: a background crawl lists every folder once and stores the entries in SQLite.

The panel has no search endpoint, only "list one folder". Searching live would cost one request per folder on
every keystroke, so Hatchtower walks the tree once (breadth-first, so shallow folders are searchable first),
throttled against the panel's rate limit, and answers searches from the local index. Folders a user opens in the
file manager refresh their part of the index for free (note_listing), and an index older than MAX_AGE is
rebuilt in the background the next time someone searches.
"""
from __future__ import annotations

import asyncio
import logging
import posixpath
import time
from datetime import datetime, timezone
from typing import Any

from . import db
from .ptero import PteroError

log = logging.getLogger("hatchtower.fileindex")

MAX_AGE = 24 * 3600          # rebuild an older index in the background on the next search
MAX_DIRS = 5000              # folders per crawl; beyond that the index is marked incomplete
MAX_ENTRIES = 200_000
MAX_DEPTH = 24
MIN_INTERVAL = 0.5           # seconds between two panel requests of one crawl
RATE_RESERVE = 0.5           # share of the panel's rate limit left for monitoring and the UI
RATE_MAX_WAIT = 120
MAX_FAILURES = 5             # consecutive failed folders before the crawl gives up (panel down)
RESULTS = 100
# Folders listed as entries but not descended into: huge, and nobody looks for files in them
SKIP_DESCEND = {"node_modules", ".git", "__pycache__", ".cache"}

_tasks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}
_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _join(directory: str, name: str) -> str:
    return posixpath.join(directory, name)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _running(ident: str) -> bool:
    entry = _tasks.get(ident)
    if not entry:
        return False
    loop, task = entry
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    return loop is current and not loop.is_closed() and not task.done()


def state(ident: str) -> dict[str, Any] | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM file_index_state WHERE identifier = ?", (ident,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    if out["status"] == "running" and not _running(ident):
        out["status"] = "error"          # crawl died with the process (restart, update)
        out["error"] = out["error"] or "interrupted"
    return out


def age(st: dict[str, Any]) -> float:
    try:
        stamp = datetime.fromisoformat(st["finished_at"] or st["started_at"])
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def ensure(client, ident: str, force: bool = False) -> dict[str, Any] | None:
    """Starts a crawl when there is no index, the last one failed or is stale (or force). Returns the state."""
    st = state(ident)
    if _running(ident):
        return st
    if force or st is None or st["status"] != "done" or age(st) > MAX_AGE:
        loop = asyncio.get_running_loop()
        gen = _begin(ident)          # state is "running" right away, before the task gets its first turn
        _tasks[ident] = (loop, loop.create_task(_crawl(client, ident, gen)))
        st = state(ident)
    return st


async def _pace(client, last: float) -> None:
    """Keeps a gap between requests and waits while the shared rate limit is low."""
    gap = MIN_INTERVAL - (time.monotonic() - last)
    if gap > 0:
        await asyncio.sleep(gap)
    waited = 0
    while waited < RATE_MAX_WAIT:
        rate = getattr(client, "rate", None)
        if not rate:
            return
        limit, remaining, seen = rate
        if remaining >= limit * RATE_RESERVE or time.monotonic() - seen > 20:
            return
        await asyncio.sleep(1)
        waited += 1


def _store(con, ident: str, directory: str, files: list[dict[str, Any]], gen: int) -> None:
    con.execute("DELETE FROM file_index WHERE identifier = ? AND dir = ?", (ident, directory))
    con.executemany(
        "INSERT OR REPLACE INTO file_index (identifier, dir, name, is_dir, size, modified_at, gen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(ident, directory, f["name"], 0 if f["is_file"] else 1, int(f.get("size") or 0), f.get("modified_at"), gen)
         for f in files if f.get("name")])


def _begin(ident: str) -> int:
    with db.connect() as con:
        row = con.execute("SELECT gen FROM file_index_state WHERE identifier = ?", (ident,)).fetchone()
        gen = (row["gen"] if row else 0) + 1
        con.execute(
            "INSERT INTO file_index_state (identifier, status, gen, started_at) VALUES (?, 'running', ?, ?)"
            " ON CONFLICT(identifier) DO UPDATE SET status = 'running', gen = excluded.gen,"
            " started_at = excluded.started_at, finished_at = NULL, dirs = 0, entries = 0, truncated = 0, error = NULL",
            (ident, gen, db.utcnow()))
    return gen


async def _crawl(client, ident: str, gen: int) -> None:
    lock = _locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    # One crawl at a time: all servers share the panel's rate limit
    async with lock:
        queue: list[tuple[str, int]] = [("/", 0)]
        dirs = entries = failures = 0
        truncated = False
        last = 0.0
        error = None
        try:
            while queue:
                if dirs >= MAX_DIRS or entries >= MAX_ENTRIES:
                    truncated = True
                    break
                directory, depth = queue.pop(0)
                await _pace(client, last)
                last = time.monotonic()
                try:
                    files = await client.list_files(ident, directory)
                except PteroError as exc:
                    if directory == "/":
                        raise
                    failures += 1
                    if failures >= MAX_FAILURES:
                        raise
                    log.debug("file index %s: skipping %s (%s)", ident, directory, exc)
                    continue
                failures = 0
                dirs += 1
                entries += len(files)
                with db.connect() as con:
                    _store(con, ident, directory, files, gen)
                    con.execute("UPDATE file_index_state SET dirs = ?, entries = ? WHERE identifier = ?",
                                (dirs, entries, ident))
                for f in sorted(files, key=lambda f: str(f.get("name", "")).lower()):
                    if not f["is_file"] and f.get("name") and f["name"] not in SKIP_DESCEND:
                        if depth + 1 <= MAX_DEPTH:
                            queue.append((_join(directory, f["name"]), depth + 1))
                        else:
                            truncated = True
        except PteroError as exc:
            error = str(exc)[:300]
        except asyncio.CancelledError:
            error = "cancelled"
            raise
        except Exception as exc:     # never let a crawl take the app down
            log.exception("file index %s failed", ident)
            error = exc.__class__.__name__
        finally:
            with db.connect() as con:
                if error is None:
                    # Entries the crawl did not see again are gone (deleted, renamed, moved)
                    con.execute("DELETE FROM file_index WHERE identifier = ? AND gen < ?", (ident, gen))
                con.execute(
                    "UPDATE file_index_state SET status = ?, finished_at = ?, dirs = ?, entries = ?, truncated = ?,"
                    " error = ? WHERE identifier = ?",
                    ("done" if error is None else "error", db.utcnow(), dirs, entries, int(truncated), error, ident))


def note_listing(ident: str, directory: str, files: list[dict[str, Any]]) -> None:
    """A folder listed in the file manager: bring its part of an existing index up to date."""
    with db.connect() as con:
        row = con.execute("SELECT gen FROM file_index_state WHERE identifier = ?", (ident,)).fetchone()
        if row is None:
            return
        names = {f["name"] for f in files if not f["is_file"]}
        old = [r["name"] for r in con.execute(
            "SELECT name FROM file_index WHERE identifier = ? AND dir = ? AND is_dir = 1", (ident, directory))]
        for gone in (n for n in old if n not in names):
            sub = _join(directory, gone)
            con.execute("DELETE FROM file_index WHERE identifier = ? AND (dir = ? OR dir LIKE ? ESCAPE '\\')",
                        (ident, sub, _like_escape(sub) + "/%"))
        _store(con, ident, directory, files, row["gen"])


def forget(ident: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM file_index WHERE identifier = ?", (ident,))
        con.execute("DELETE FROM file_index_state WHERE identifier = ?", (ident,))


def search(ident: str, query: str, limit: int = RESULTS) -> tuple[list[dict[str, Any]], bool]:
    """Entries whose path contains every word of the query (case-insensitive). A word with * or ? is a
    wildcard pattern for the name, e.g. "*.yml". Best matches first: words in the name, exact name, shallow."""
    terms = [t.lower() for t in query.split() if t][:8]
    if not terms:
        return [], False
    where, args = ["identifier = ?"], [ident]
    for term in terms:
        if "*" in term or "?" in term:
            where.append("name LIKE ? ESCAPE '\\'")
            args.append(_like_escape(term).replace("*", "%").replace("?", "_"))
        else:
            where.append("(dir || '/' || name) LIKE ? ESCAPE '\\'")
            args.append("%" + _like_escape(term) + "%")
    cap = 2000
    with db.connect() as con:
        rows = con.execute(f"SELECT dir, name, is_dir, size, modified_at FROM file_index WHERE {' AND '.join(where)}"
                           f" LIMIT {cap + 1}", args).fetchall()
    more = len(rows) > cap
    plain = [t for t in terms if "*" not in t and "?" not in t]
    whole = " ".join(plain)

    def score(r) -> tuple:
        name = r["name"].lower()
        path = _join(r["dir"], r["name"])
        return (-sum(t in name for t in plain), name != whole, not (plain and name.startswith(plain[0])),
                path.count("/"), len(path), path.lower())

    ranked = sorted(rows, key=score)
    out = [{"dir": r["dir"], "name": r["name"], "path": _join(r["dir"], r["name"]), "is_dir": bool(r["is_dir"]),
            "size": r["size"], "modified_at": r["modified_at"]} for r in ranked[:limit]]
    return out, more or len(ranked) > limit
