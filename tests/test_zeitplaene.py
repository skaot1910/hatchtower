"""Schedules and restart after a crash, run by the background scheduler (as "Automation")."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hatchtower import automation, db, notify
from hatchtower.ptero import PteroError

TZ = "Europe/Berlin"


class Panel:
    def __init__(self, state: str = "offline"):
        self.settings = SimpleNamespace(read_only=False)
        self.calls: list[tuple] = []
        self.state = state
        self.events: list[dict] = []
        self.fail = False

    async def power(self, ident, signal):
        if self.fail:
            raise PteroError("Panel antwortet mit HTTP 409", 409)
        self.calls.append(("power", ident, signal))

    async def command(self, ident, text):
        self.calls.append(("command", ident, text))

    async def resources(self, ident):
        return {"state": self.state}

    async def activity(self, ident):
        return self.events


def schedule(world, *, action="restart", at: datetime | None = None, state="running", creator=None, **extra) -> int:
    at = at or datetime.now(ZoneInfo(TZ))
    fields = {"identifier": "srv-a", "name": "Plan", "action": action, "weekdays": str(at.weekday()),
              "time": at.strftime("%H:%M"), "created_by": creator if creator is not None else world.ids["olga"],
              "created_at": db.utcnow(), "only_if_running": 1, **extra}
    with db.connect() as con:
        con.execute("UPDATE server_state SET state = ? WHERE identifier = 'srv-a'", (state,))
        return con.execute(f"INSERT INTO schedules ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                           tuple(fields.values())).lastrowid


def sched(sid: int) -> dict:
    with db.connect() as con:
        return dict(con.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone())


def tick(panel: Panel) -> None:
    asyncio.run(automation.Automation(panel, TZ)._tick())


def test_zeitplan_laeuft_genau_einmal(world):
    panel = Panel()
    sid = schedule(world)
    tick(panel)
    tick(panel)
    assert panel.calls == [("power", "srv-a", "restart")]
    assert "ausgeführt" in sched(sid)["last_result"] and sched(sid)["enabled"] == 1
    assert world.audit("schedule.run")[-1]["username"] == automation.ACTOR


def test_anderer_wochentag_laeuft_nicht(world):
    panel = Panel()
    now = datetime.now(ZoneInfo(TZ))
    schedule(world, at=now, weekdays=str((now.weekday() + 3) % 7))
    tick(panel)
    assert panel.calls == []


@pytest.mark.parametrize("action, state, expected", [
    ("restart", "offline", "nicht gestartet"),
    ("stop", "starting", "nicht gestartet"),
    ("start", "running", "läuft bereits"),
])
def test_uebersprungen_je_nach_zustand(world, action, state, expected):
    panel = Panel()
    sid = schedule(world, action=action, state=state)
    tick(panel)
    assert panel.calls == [] and expected in sched(sid)["last_result"]


def test_auch_wenn_aus_wenn_so_eingestellt(world):
    panel = Panel()
    schedule(world, action="restart", state="offline", only_if_running=0)
    tick(panel)
    assert panel.calls == [("power", "srv-a", "restart")]


def test_befehl(world):
    panel = Panel()
    schedule(world, action="command", command="save-all")
    tick(panel)
    assert panel.calls == [("command", "srv-a", "save-all")]


def test_einmaliger_zeitplan_schaltet_sich_ab(world):
    panel = Panel()
    now = datetime.now(ZoneInfo(TZ))
    sid = schedule(world, run_at=now.replace(tzinfo=None).isoformat(timespec="minutes"), weekdays="")
    tick(panel)
    assert panel.calls == [("power", "srv-a", "restart")] and sched(sid)["enabled"] == 0


def test_ersteller_ohne_recht(world):
    """A user's schedule only runs while that user still holds the rights it needs."""
    uid = world.user("planer", grants={"srv-a": {"automation.manage", "power.restart"}})
    panel = Panel()
    sid = schedule(world, creator=uid)
    world.grant(uid, {"srv-a": {"automation.manage"}})        # restart right taken away
    tick(panel)
    assert panel.calls == [] and "fehlt das Recht" in sched(sid)["last_result"]
    assert world.audit("schedule.run")[-1]["result"] == "denied"


def test_fehler_wird_gemeldet(world, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "emit", lambda vault, event, ident, detail="": sent.append((event, ident)))
    panel = Panel()
    panel.fail = True
    sid = schedule(world)
    asyncio.run(automation.Automation(panel, TZ, vault=object())._tick())
    assert "Fehler" in sched(sid)["last_result"] and sent == [("schedule_failed", "srv-a")]


def test_vorwarnung_vor_dem_neustart(world):
    panel = Panel()
    at = datetime.now(ZoneInfo(TZ)) + timedelta(minutes=5)
    sid = schedule(world, at=at, warn_minutes=5, warn_command="say Neustart in {minuten} Minuten")
    tick(panel)
    tick(panel)
    assert panel.calls == [("command", "srv-a", "say Neustart in 5 Minuten")]
    assert sched(sid)["last_warn"] and not sched(sid)["last_run"]


def test_keine_vorwarnung_wenn_aus(world):
    panel = Panel()
    at = datetime.now(ZoneInfo(TZ)) + timedelta(minutes=2)
    schedule(world, at=at, state="offline", warn_minutes=2, warn_command="say gleich")
    tick(panel)
    assert panel.calls == []


def test_beschreibung():
    row = {"run_at": None, "weekdays": "0123456", "time": "04:00"}
    assert automation.describe(row) == "täglich um 04:00 Uhr"
    assert automation.describe({**row, "weekdays": "01234"}) == "Mo–Fr um 04:00 Uhr"
    assert automation.describe({**row, "weekdays": "56"}) == "Sa+So um 04:00 Uhr"
    assert automation.describe({**row, "weekdays": "02"}) == "Mo, Mi um 04:00 Uhr"
    assert automation.describe({**row, "run_at": "2026-12-24T18:30"}).startswith("einmalig am 24.12.2026")
    assert automation.describe({**row, "run_at": "kaputt"}) == "–"
    assert automation.duration_text(30) == "30 Min." and automation.duration_text(90) == "1,5 Std."


# -- Restart after a crash -------------------------------------------------------------------------

def crash(world, monkeypatch, panel: Panel, restart: bool = True) -> list:
    sent = []
    monkeypatch.setattr(notify, "emit", lambda vault, event, ident, detail="": sent.append(event))

    async def no_wait(seconds):
        pass
    monkeypatch.setattr(automation.asyncio, "sleep", no_wait)
    auto = automation.Automation(panel, TZ, vault=object())
    asyncio.run(auto._handle_crash("srv-a", restart))
    return sent


def outcomes() -> list[str]:
    with db.connect() as con:
        return [r["outcome"] for r in con.execute("SELECT outcome FROM crash_events ORDER BY id")]


def test_absturz_wird_neu_gestartet(world, monkeypatch):
    panel = Panel("offline")
    sent = crash(world, monkeypatch, panel)
    assert panel.calls == [("power", "srv-a", "start")]
    assert outcomes() == ["Neu gestartet nach Absturz"] and sent == ["crash", "restarted"]


def test_absturz_ohne_automatischen_neustart(world, monkeypatch):
    panel = Panel("offline")
    sent = crash(world, monkeypatch, panel, restart=False)
    assert panel.calls == [] and "Neustart ist aus" in outcomes()[0] and sent == ["crash"]


def test_wings_hat_schon_neu_gestartet(world, monkeypatch):
    panel = Panel("running")
    crash(world, monkeypatch, panel)
    assert panel.calls == [] and "wieder running" in outcomes()[0]


def test_hoechstens_drei_neustarts_pro_stunde(world, monkeypatch):
    panel = Panel("offline")
    for _ in range(automation.CRASH_LIMIT_PER_HOUR):
        crash(world, monkeypatch, panel)
    sent = crash(world, monkeypatch, panel)
    assert len(panel.calls) == automation.CRASH_LIMIT_PER_HOUR
    assert "Limit" in outcomes()[-1] and sent[-1] == "restart_failed"


def test_gewollter_stopp_ist_kein_absturz(world, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    for event in ({"event": "server:power.stop", "timestamp": now},
                  {"event": "server:console.command", "timestamp": now, "properties": {"command": "/stop"}}):
        panel = Panel("offline")
        panel.events = [event]
        assert crash(world, monkeypatch, panel) == [] and panel.calls == []
    # an old stop does not count
    panel = Panel("offline")
    panel.events = [{"event": "server:power.stop",
                     "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}]
    crash(world, monkeypatch, panel)
    assert panel.calls == [("power", "srv-a", "start")]


def test_stopp_ueber_hatchtower_ist_kein_absturz(world, monkeypatch):
    panel = Panel("offline")
    auto = automation.Automation(panel, TZ)
    auto.note_power("srv-a", "stop")
    asyncio.run(auto._handle_crash("srv-a"))
    assert panel.calls == [] and outcomes() == []


def test_im_read_only_modus_kein_neustart(world, monkeypatch):
    with db.connect() as con:
        con.execute("INSERT INTO automation_settings (identifier, crash_restart, updated_at) VALUES ('srv-a', 1, ?)",
                    (db.utcnow(),))
    panel = Panel("offline")
    panel.settings.read_only = True
    started = []
    auto = automation.Automation(panel, TZ)

    async def fake_handle(ident, restart=True):
        started.append(restart)
    monkeypatch.setattr(auto, "_handle_crash", fake_handle)

    async def run():
        auto.on_offline("srv-a")
        await asyncio.gather(*auto._crash_tasks.values())
    asyncio.run(run())
    assert started == [False]


def test_zeitplan_eines_im_panel_geloeschten_servers_ruht(world):
    panel = Panel()
    sid = schedule(world)
    with db.connect() as con:
        con.execute("UPDATE servers SET present = 0 WHERE identifier = 'srv-a'")
    tick(panel)
    assert panel.calls == [] and sched(sid)["last_run"] is None          # no 404, no failure notification
    with db.connect() as con:
        con.execute("UPDATE servers SET present = 1 WHERE identifier = 'srv-a'")
    tick(panel)
    assert panel.calls == [("power", "srv-a", "restart")]               # back in the panel: runs again


# == Editing and history ===========================================================================

def edit_form(c, **fields) -> dict:
    return {"csrf": c.csrf, "identifier": "srv-a", "name": "Plan", "action": "restart", "time": "04:00",
            "weekdays": "1", "repeat": "weekly", **fields}


def test_zeitplan_bearbeiten(world):
    sid = schedule(world, last_run="2026-10-01T04:00", warn_minutes=5, warn_command="say gleich")
    c = world.client(world.ids["olga"])
    html = c.get(f"/admin/automation/schedules/{sid}/edit").text
    assert f'action="/admin/automation/schedules/{sid}"' in html and 'value="Plan"' in html and "say gleich" in html
    r = c.post(f"/admin/automation/schedules/{sid}", data=edit_form(c, name="Nachts", action="command",
                                                                   command="save", time="3:30"))
    assert r.status_code == 303 and "/admin/automation" in r.headers["location"]
    s = sched(sid)
    assert (s["name"], s["action"], s["command"], s["time"], s["weekdays"]) == ("Nachts", "command", "save", "03:30", "1")
    assert s["warn_minutes"] is None and s["last_run"] is None          # new time: the old appointment is forgotten
    assert s["created_by"] == world.ids["olga"] and s["enabled"] == 1
    entry = world.audit("schedule.update")[-1]
    assert '"name": "Plan"' in entry["detail"] and "Nachts" in entry["detail"]


def test_zeitplan_bearbeiten_prueft_wie_anlegen(world):
    sid = schedule(world)
    c = world.client(world.ids["olga"])
    r = c.post(f"/admin/automation/schedules/{sid}", data=edit_form(c, weekdays="", time="25:00"))
    assert f"/admin/automation/schedules/{sid}/edit" in r.headers["location"]
    assert sched(sid)["time"] != "25:00" and sched(sid)["weekdays"]


def test_zeitplan_bearbeiten_nur_mit_recht_fuer_die_neue_aktion(world):
    uid = world.user("planer", grants={"srv-a": {"automation.manage", "power.restart"}})
    sid = schedule(world, creator=uid)
    c = world.client(uid)
    assert c.get(f"/admin/automation/schedules/{sid}/edit").status_code == 200
    c.post(f"/admin/automation/schedules/{sid}", data=edit_form(c, action="stop"))
    assert sched(sid)["action"] == "restart"                          # no right to stop: unchanged


def test_verlauf_filtert(world):
    panel = Panel()
    schedule(world, name="Nachts")
    tick(panel)
    from hatchtower import audit
    audit.record("schedule.run", result="error", username="Automation", ip="intern", server="srv-b",
                 detail="Anderer (Restart): Fehler")
    c = world.client(world.ids["olga"])
    html = c.get("/admin/automation/history").text
    assert "Nachts (Restart)" in html and "Anderer (Restart)" in html
    html = c.get("/admin/automation/history?result=error").text
    assert "Nachts (Restart)" not in html and "Anderer (Restart)" in html
    html = c.get("/admin/automation/history?q=Nachts&kind=run").text
    assert "Nachts (Restart)" in html and "Anderer (Restart)" not in html
    assert "Nachts" not in c.get("/admin/automation/history?kind=crash").text.split("<tbody>")[-1]
    # users only see the servers they may automate
    planer = world.client(world.user("planer", grants={"srv-a": {"automation.manage", "power.restart"}}))
    html = planer.get("/admin/automation/history").text
    assert "Nachts (Restart)" in html and "Anderer (Restart)" not in html
