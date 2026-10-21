"""Console archive: one read-only Wings connection per running server, lines stored without the replay duplicates
Wings sends on every reconnect, and a rarely retried connection when Wings refuses it."""
from __future__ import annotations

import asyncio

import pytest

from hatchtower import db, logarchive


class Stop(BaseException):
    """Ends the endless follow loop in a test."""


def lines(ident: str) -> list[tuple]:
    with db.connect() as con:
        return [(r["level"], r["line"]) for r in con.execute(
            "SELECT level, line FROM console_lines WHERE identifier = ? ORDER BY id", (ident,))]


def archive_with(monkeypatch, *streams, sleeps: list | None = None) -> logarchive.LogArchive:
    """LogArchive whose console connections deliver the given item lists one after another."""
    queue = list(streams)

    async def fake_stream(client, ident):
        if not queue:
            raise Stop()
        for item in queue.pop(0):
            yield item

    async def fake_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    monkeypatch.setattr(logarchive, "console_stream", fake_stream)
    monkeypatch.setattr(logarchive.asyncio, "sleep", fake_sleep)
    return logarchive.LogArchive(client=None)


def follow(arch: logarchive.LogArchive, ident: str = "srv-a") -> None:
    with pytest.raises(Stop):
        asyncio.run(arch._follow(ident))


def line(text: str, source: str = "console output") -> dict:
    return {"type": "line", "text": text, "source": source}


@pytest.mark.parametrize("text, level", [
    ("[12:00] Server started", None),
    ("java.lang.NullPointerException at foo", "error"),
    ("Traceback (most recent call last):", "error"),
    ("[WARN] Can't keep up!", "warn"),
    ("terrorist won the round", None),            # whole words only
    ("java.lang.OutOfMemoryError: Java heap space", "error"),
    ("ZeroDivisionError: division by zero", "error"),
    ("Mirror loaded", None),
])
def test_stufe_einer_zeile(text, level):
    assert logarchive.level_of(text) == level


def test_zeilen_werden_gespeichert_und_bereinigt(world, monkeypatch):
    seen = []
    arch = archive_with(monkeypatch, [line("\x1b[32mGrün\x1b[0m"), line("   "), line("x" * 3000),
                                      line("intern", "daemon error"), line("Fehler: crash")])
    arch.on_line = lambda ident, text: seen.append(text)
    follow(arch)
    stored = lines("srv-a")
    assert stored[0] == (None, "Grün")
    assert len(stored[1][1]) == logarchive.MAX_LINE
    assert stored[2] == ("error", "Fehler: crash")
    assert len(stored) == 3                      # blank line and Wings' own errors are not archived
    assert seen == ["Grün", "x" * logarchive.MAX_LINE, "Fehler: crash"]


def test_wiederverbinden_speichert_den_verlauf_nicht_doppelt(world, monkeypatch):
    history = [line("Zeile 1"), line("Zeile 2")]
    arch = archive_with(monkeypatch, history, history + [line("Zeile 3")])
    follow(arch)
    assert [t for _, t in lines("srv-a")] == ["Zeile 1", "Zeile 2", "Zeile 3"]


def test_nach_neustart_von_hatchtower_kein_doppelter_verlauf(world, monkeypatch):
    follow(archive_with(monkeypatch, [line("Alt 1"), line("Alt 2")]))
    follow(archive_with(monkeypatch, [line("Alt 1"), line("Alt 2"), line("Neu")]))   # fresh instance, primed from db
    assert [t for _, t in lines("srv-a")] == ["Alt 1", "Alt 2", "Neu"]


def test_ohne_aufbewahrung_nur_spielererkennung(world, monkeypatch):
    db.set_setting("console_retention_days", "0", None)
    seen = []
    arch = archive_with(monkeypatch, [line("Steve joined the game")])
    arch.on_line = lambda ident, text: seen.append(text)
    follow(arch)
    assert lines("srv-a") == [] and seen == ["Steve joined the game"]


def test_statuswechsel_loest_rueckruf_aus(world, monkeypatch):
    calls = []
    arch = archive_with(monkeypatch, [{"type": "status", "state": "running"}, {"type": "status", "state": "running"},
                                      {"type": "status", "state": "offline"}])
    arch.on_status = calls.append
    follow(arch)
    assert calls == ["srv-a"]                    # only a real change, not the first state or a repeat


def test_abgelehnt_wird_selten_wiederholt(world, monkeypatch):
    sleeps = []
    arch = archive_with(monkeypatch, [{"type": "error", "text": "403", "fatal": True}],
                        [{"type": "error", "text": "weg"}], [{"type": "error", "text": "weg"}], sleeps=sleeps)
    follow(arch)
    assert sleeps == [600, 10, 20]               # refused: 10 minutes; unreachable: growing backoff


def test_suche(world):
    with db.connect() as con:
        con.executemany("INSERT INTO console_lines (identifier, ts, level, line) VALUES (?, ?, ?, ?)", [
            ("srv-a", 100, None, "50% geladen"), ("srv-a", 200, "warn", "WARN langsam"),
            ("srv-a", 300, "error", "ERROR kaputt"), ("srv-a", 400, None, "spieler_1 joined"),
            ("srv-b", 500, "error", "ERROR anderer Server")])
    text = lambda rows: [r["line"] for r in rows]  # noqa: E731
    assert text(logarchive.search("srv-a")) == ["spieler_1 joined", "ERROR kaputt", "WARN langsam", "50% geladen"]
    assert text(logarchive.search("srv-a", q="%")) == ["50% geladen"]          # LIKE wildcards are literal
    assert text(logarchive.search("srv-a", q="r_1")) == ["spieler_1 joined"]
    assert text(logarchive.search("srv-a", level="error")) == ["ERROR kaputt"]
    assert text(logarchive.search("srv-a", level="warn")) == ["ERROR kaputt", "WARN langsam"]
    assert text(logarchive.search("srv-a", since=250)) == ["spieler_1 joined", "ERROR kaputt"]
    newest = logarchive.search("srv-a", limit=2)
    assert text(logarchive.search("srv-a", before_id=newest[-1]["id"])) == ["WARN langsam", "50% geladen"]


def test_verbindungen_nur_fuer_laufende_server(world, monkeypatch):
    with db.connect() as con:
        con.execute("UPDATE server_state SET state = 'offline'")
        con.execute("UPDATE server_state SET state = 'starting' WHERE identifier = 'srv-a'")
    started = []

    async def fake_follow(self, ident):
        started.append(ident)
        await asyncio.Event().wait()

    async def stop_after_first(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(logarchive.LogArchive, "_follow", fake_follow)
    monkeypatch.setattr(logarchive.asyncio, "sleep", stop_after_first)
    arch = logarchive.LogArchive(client=type("C", (), {"settings": type("S", (), {"configured": True})()})())

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await arch._loop()
        return set(arch._tasks)
    assert asyncio.run(run()) == {"srv-a"}
