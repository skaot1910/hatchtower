"""Absturz-Erkennung, wenn Wings den Server schneller neu startet, als Hatchtower abfragt."""
import asyncio
from datetime import datetime, timedelta, timezone

from hatchtower import automation, db, monitor

NOW = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)


def ago(seconds: int) -> str:
    return (NOW - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def test_laufzeit_gesunken_ist_neustart():
    assert monitor.silent_restart(3_600_000, 4_000, ago(10), NOW)
    assert monitor.silent_restart(3_600_000, 0, ago(10), NOW)


def test_laufzeit_normal_weiter_ist_kein_neustart():
    assert not monitor.silent_restart(3_600_000, 3_610_000, ago(10), NOW)
    assert not monitor.silent_restart(3_600_000, 3_599_000, ago(10), NOW)   # kleine Messschwankung


def test_ohne_frische_vorherige_messung_kein_neustart():
    assert not monitor.silent_restart(None, 4_000, ago(10), NOW)
    assert not monitor.silent_restart(0, 4_000, ago(10), NOW)
    assert not monitor.silent_restart(3_600_000, 4_000, ago(300), NOW)       # Abfragen lange ausgefallen
    assert not monitor.silent_restart(3_600_000, 4_000, None, NOW)
    assert not monitor.silent_restart(3_600_000, None, ago(10), NOW)


class FakeClient:
    def __init__(self, events=()):
        self.events = list(events)
        self.settings = type("S", (), {"read_only": False})()

    async def activity(self, identifier):
        return self.events


def run_silent(auto: automation.Automation, ident: str) -> list:
    sent = []
    auto._emit = lambda event, i, detail="": sent.append((event, i))
    asyncio.run(auto._handle_silent_restart(ident))
    return sent


def crashes(ident: str) -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM crash_events WHERE identifier = ?", (ident,)).fetchone()[0]


def test_unerwarteter_neustart_wird_als_absturz_gemeldet(world):
    auto = automation.Automation(FakeClient(), "Europe/Berlin")
    before = crashes("silent1")
    assert run_silent(auto, "silent1") == [("crash", "silent1")]
    assert crashes("silent1") == before + 1


def test_neustart_ueber_hatchtower_ist_kein_absturz(world):
    auto = automation.Automation(FakeClient(), "Europe/Berlin")
    auto.note_power("silent2", "restart")
    assert run_silent(auto, "silent2") == []
    assert crashes("silent2") == 0


def test_neustart_im_panel_ist_kein_absturz(world):
    stamp = datetime.now(timezone.utc).isoformat()
    for event in ({"event": "server:power.restart", "timestamp": stamp},
                  {"event": "server:console.command", "timestamp": stamp, "properties": {"command": "restart"}}):
        auto = automation.Automation(FakeClient([event]), "Europe/Berlin")
        assert run_silent(auto, "silent3") == []
    assert crashes("silent3") == 0
