"""Update-Fenster: Server regelmäßig für eine Dauer starten (nur wenn er aus ist) und danach wieder stoppen."""
import asyncio
from datetime import datetime, timedelta, timezone

from hatchtower import automation, db


class FakeClient:
    def __init__(self):
        self.settings = type("S", (), {"read_only": False})()
        self.calls = []

    async def power(self, identifier, signal):
        self.calls.append((identifier, signal))


def make(state: str, minutes: int = 60) -> int:
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO server_state (identifier, state) VALUES ('srv-a', ?)", (state,))
        return con.execute("INSERT INTO schedules (identifier, name, action, weekdays, time, created_at,"
                           " duration_minutes) VALUES ('srv-a', 'Updates', 'update', '0123456', '04:00', ?, ?)",
                           (db.utcnow(), minutes)).lastrowid


def row(sched_id: int):
    with db.connect() as con:
        return con.execute("SELECT sc.*, st.state FROM schedules sc LEFT JOIN server_state st"
                           " ON st.identifier = sc.identifier WHERE sc.id = ?", (sched_id,)).fetchone()


def set_state(state: str) -> None:
    with db.connect() as con:
        con.execute("UPDATE server_state SET state = ? WHERE identifier = 'srv-a'", (state,))


def expire(sched_id: int) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds")
    with db.connect() as con:
        con.execute("UPDATE schedules SET window_until = ? WHERE id = ?", (past, sched_id))


def test_startet_nur_wenn_aus_und_stoppt_danach(world):
    client = FakeClient()
    auto = automation.Automation(client, "UTC")
    sid = make("offline", 90)
    asyncio.run(auto._execute(row(sid), "k1"))
    assert client.calls == [("srv-a", "start")]
    until = datetime.fromisoformat(row(sid)["window_until"])
    assert timedelta(minutes=89) < until - datetime.now(timezone.utc) <= timedelta(minutes=90)
    set_state("running")
    asyncio.run(auto._close_windows())                    # noch nicht abgelaufen
    assert client.calls == [("srv-a", "start")]
    expire(sid)
    asyncio.run(auto._close_windows())
    assert client.calls == [("srv-a", "start"), ("srv-a", "stop")]
    assert row(sid)["window_until"] is None and "gestoppt" in row(sid)["last_result"]


def test_laeuft_schon_dann_nichts(world):
    client = FakeClient()
    auto = automation.Automation(client, "UTC")
    sid = make("running")
    asyncio.run(auto._execute(row(sid), "k1"))
    assert client.calls == [] and row(sid)["window_until"] is None
    assert "nicht aus" in row(sid)["last_result"]


def test_von_hand_gestartet_bleibt_an(world):
    client = FakeClient()
    auto = automation.Automation(client, "UTC")
    sid = make("offline")
    asyncio.run(auto._execute(row(sid), "k1"))
    auto.note_power("srv-a", "restart")                   # jemand startet in Hatchtower neu
    set_state("running")
    expire(sid)
    asyncio.run(auto._close_windows())
    assert client.calls == [("srv-a", "start")]
    assert row(sid)["window_until"] is None


def test_schon_aus_kein_stopp(world):
    client = FakeClient()
    auto = automation.Automation(client, "UTC")
    sid = make("offline")
    asyncio.run(auto._execute(row(sid), "k1"))
    expire(sid)
    asyncio.run(auto._close_windows())
    assert client.calls == [("srv-a", "start")]


def test_rechte_brauchen_start_und_stopp(world):
    from hatchtower.permissions import AUTOMATION_NEEDS
    assert set(AUTOMATION_NEEDS["update"]) == {"power.start", "power.stop"}


def test_anlegen_mit_dauer(world):
    owner = world.client(world.ids["olga"])
    r = owner.post("/admin/automation/schedules", data={
        "csrf": owner.csrf, "identifier": "srv-a", "name": "Spiel-Updates", "action": "update", "time": "04:00",
        "weekdays": ["1", "4"], "duration": "120", "warn_minutes": "5", "warn_command": "say hi"},
        follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as con:
        s = con.execute("SELECT * FROM schedules WHERE name = 'Spiel-Updates'").fetchone()
    assert (s["action"], s["duration_minutes"], s["weekdays"], s["warn_command"]) == ("update", 120, "14", None)
    page = owner.get("/admin/automation").text
    assert "Spiel-Updates" in page and "2 Std." in page


def test_ungueltige_dauer(world):
    owner = world.client(world.ids["olga"])
    owner.post("/admin/automation/schedules", data={
        "csrf": owner.csrf, "identifier": "srv-a", "name": "Kaputt", "action": "update", "time": "04:00",
        "weekdays": ["1"], "duration": "7"}, follow_redirects=False)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM schedules WHERE name = 'Kaputt'").fetchone()[0] == 0
