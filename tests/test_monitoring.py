"""Monitoring: mirrors the server list from the panel and records states, metrics and state changes.
The panel stays the authority; Hatchtower only adds what the panel does not store (e.g. when a server stopped)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hatchtower import db, main, monitor, notify
from hatchtower.ptero import PteroError, ServerInfo


def info(ident: str, name: str | None = None, port: int = 25565) -> ServerInfo:
    return ServerInfo(ptero_id=1, identifier=ident, uuid=f"uuid-{ident}", name=name or f"Server {ident}", node_id=1,
                      egg="Minecraft", allocations=[{"ip": "10.0.0.1", "port": port}], primary_ip="10.0.0.1",
                      primary_port=port, limit_memory=1024, limit_disk=2048, limit_cpu=100, suspended=False)


class Panel:
    """Just enough of a panel client for the monitor."""

    def __init__(self, servers: list[ServerInfo]):
        self.settings = SimpleNamespace(client_key="cli", configured=True)
        self.servers = servers
        self.states: dict[str, str] = {}
        self.variables: dict[str, dict] = {}
        self.failing: set[str] = set()
        self.requests: list[str] = []
        self.rate = None

    async def list_nodes(self):
        self.requests.append("nodes")
        return []

    async def list_servers(self):
        self.requests.append("servers")
        return list(self.servers)

    async def startup_variables(self, ident):
        self.requests.append(f"startup:{ident}")
        return self.variables.get(ident, {})

    async def resources(self, ident):
        self.requests.append(f"resources:{ident}")
        if ident in self.failing:
            raise PteroError("Panel antwortet nicht", 502)
        return {"state": self.states.get(ident, "running"), "cpu": 12.5, "memory_bytes": 100 << 20,
                "disk_bytes": 200 << 20, "rx_bytes": 1000, "tx_bytes": 2000, "uptime_ms": 60_000, "suspended": False}


def make(world, servers) -> tuple[monitor.Monitor, Panel]:
    with db.connect() as con:                     # start from an empty mirror, as on a fresh instance
        for table in ("server_state", "server_meta", "servers"):
            con.execute(f"DELETE FROM {table}")
    panel = Panel(servers)
    m = monitor.Monitor(panel, main.vault, poll_interval=10, sync_interval=300)

    async def no_query(*a, **k):
        return None
    m.query.query = no_query
    return m, panel


def rows(sql: str, *params) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, params)]


@pytest.mark.parametrize("variables, expected", [
    ({"SERVER_NAME": "Mein Server", "SERVER_PASSWORD": "pw"}, ("Mein Server", "pw")),
    ({"HOSTNAME": "Host", "PASSWORD": ""}, ("Host", None)),
    ({"RCON_PASSWORD": "geheim", "ADMIN_PASSWORD": "geheim", "STEAM_TOKEN": "x"}, (None, None)),
    ({"SERVER_PASS": "eins", "JOIN_PASSWORD": "zwei"}, (None, "eins")),
])
def test_name_und_passwort_aus_startvariablen(variables, expected):
    assert monitor.pick_startup_values(variables) == expected


def test_abgleich_der_serverliste(world):
    m, panel = make(world, [info("srv-1"), info("srv-2")])
    panel.variables["srv-1"] = {"SERVER_NAME": "Insel", "SERVER_PASSWORD": "beitritt"}
    new = []
    m.on_new_servers = new.extend
    asyncio.run(m.sync())
    servers = {r["identifier"]: r for r in rows("SELECT * FROM servers")}
    assert set(servers) == {"srv-1", "srv-2"} and all(s["present"] for s in servers.values())
    assert servers["srv-1"]["startup_name"] == "Insel"
    assert main.vault.decrypt(servers["srv-1"]["startup_password_enc"]) == "beitritt"
    assert new == []                               # the very first sync announces nothing

    # one server disappears, one is new; a light sync keeps the startup values it does not read
    panel.servers = [info("srv-1", name="Insel umbenannt"), info("srv-3")]
    asyncio.run(m.sync(full=False))
    servers = {r["identifier"]: r for r in rows("SELECT * FROM servers")}
    assert not servers["srv-2"]["present"] and servers["srv-3"]["present"]
    assert servers["srv-1"]["name"] == "Insel umbenannt" and servers["srv-1"]["startup_name"] == "Insel"
    assert servers["srv-3"]["review_pending"] == 1 and new == ["srv-3"]
    assert "startup:srv-1" not in panel.requests[panel.requests.index("servers", 2):]


def test_abfrage_speichert_zustand_und_messwerte(world):
    m, panel = make(world, [info("srv-1"), info("srv-2")])
    asyncio.run(m.sync())
    asyncio.run(m.poll())
    state = {r["identifier"]: r for r in rows("SELECT * FROM server_state")}
    assert state["srv-1"]["state"] == "running" and state["srv-1"]["memory_bytes"] == 100 << 20
    assert state["srv-1"]["last_started_at"]
    assert len(rows("SELECT * FROM metrics")) == 2
    assert [r["state"] for r in rows("SELECT state FROM state_changes WHERE identifier = 'srv-1'")] == ["running"]
    assert m._polled == 2


def test_stopp_wird_erkannt_und_gemeldet(world):
    m, panel = make(world, [info("srv-1")])
    offline = []
    m.on_offline = offline.append
    asyncio.run(m.sync())
    asyncio.run(m.poll())
    panel.states["srv-1"] = "offline"
    asyncio.run(m.poll())
    state = rows("SELECT * FROM server_state")[0]
    assert state["state"] == "offline" and state["last_stopped_at"] and offline == ["srv-1"]
    assert [r["state"] for r in rows("SELECT state FROM state_changes ORDER BY rowid")] == ["running", "offline"]


def test_messwerte_hoechstens_alle_zehn_sekunden(world):
    m, panel = make(world, [info("srv-1")])
    asyncio.run(m.sync())
    for _ in range(3):
        asyncio.run(m.poll())
    assert len(rows("SELECT * FROM metrics")) == 1


def test_einzelner_fehler_ist_kein_ausfall(world):
    m, panel = make(world, [info("srv-1"), info("srv-2")])
    asyncio.run(m.sync())
    panel.failing = {"srv-1"}
    asyncio.run(m.poll())                          # one server failing: the panel itself answers
    panel.failing = {"srv-1", "srv-2"}
    with pytest.raises(PteroError):
        asyncio.run(m.poll())


def test_ohne_client_key_keine_abfrage(world):
    m, panel = make(world, [info("srv-1")])
    panel.settings.client_key = ""
    with pytest.raises(PteroError, match="Client-API-Key fehlt"):
        asyncio.run(m.poll())


def test_ausfall_des_panels_erst_nach_zwei_fehlversuchen(world, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "emit", lambda vault, event, ident, detail="": sent.append(event))
    m, panel = make(world, [])
    m._panel_failed(PteroError("weg"))
    assert m.down_since is None and sent == []
    m._panel_failed(PteroError("weg"))
    m._panel_failed(PteroError("weg"))
    assert m.down_since and sent == ["panel_down"]  # reported once, not on every failure
    m._panel_ok()
    assert m.down_since is None and sent == ["panel_down", "panel_up"]
    m._panel_ok()
    assert sent == ["panel_down", "panel_up"]


def test_beobachtung_nach_einer_aktion(world, monkeypatch):
    """After start/stop the server is polled every second for a while, then the follow loop ends by itself."""
    m, panel = make(world, [info("srv-1")])
    asyncio.run(m.sync())
    clock = [1000.0]
    monkeypatch.setattr(monitor.time, "monotonic", lambda: clock[0])

    async def tick(seconds):
        clock[0] += 1

    monkeypatch.setattr(monitor.asyncio, "sleep", tick)

    async def run():
        m.follow("srv-1", seconds=40)
        await m._follow_task
    asyncio.run(run())
    polls = panel.requests.count("resources:srv-1")
    # 30 seconds every second, then every 3 seconds until 40 s have passed
    assert 32 <= polls <= 36 and not m._follow
