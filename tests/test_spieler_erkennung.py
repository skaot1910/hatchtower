"""Is anyone on the server? Query first, then console patterns, then the traffic estimate."""
import asyncio
import json
import struct

from hatchtower import gamequery
from hatchtower.gamequery import QueryResult
from hatchtower.presence import Presence

START = "2026-10-19T10:00:00+00:00"


def fresh() -> Presence:
    p = Presence()
    p._loaded = True        # nothing to restore from a database
    return p


def poll(p: Presence, total_bytes: int = 0, query=None, prev="running", started=START, state="running"):
    return p.update("srv", state, prev, started, total_bytes, query)


def test_query_entscheidet_vor_allem_anderen():
    p = fresh()
    p.feed("srv", "[12:00:00] [Server thread/INFO]: Alex joined the game")
    presence, source, players, info = poll(p, query=QueryResult("x", 3, 20, 25565, "minecraft"))
    assert (presence, source, players) == ("busy", "query", 3)
    assert json.loads(info)["query"]["protocol"] == "minecraft"


def test_konsole_zaehlt_namen_ohne_doppelte():
    p = fresh()
    poll(p, prev="starting")                                    # start seen: empty really means nobody
    assert poll(p)[:3] == ("empty", "console", 0)
    for _ in range(2):                                          # replayed history after a reconnect
        p.feed("srv", "[12:00:00] [Server thread/INFO]: Alex joined the game")
    p.feed("srv", "[12:00:01] [Server thread/INFO]: Steve joined the game")
    assert poll(p)[:3] == ("busy", "console", 2)
    p.feed("srv", "[12:05:00] [Server thread/INFO]: Alex left the game")
    p.feed("srv", "[12:06:00] [Server thread/INFO]: Steve left the game")
    assert poll(p)[:3] == ("empty", "console", 0)


def test_konsole_ohne_gesehenen_start_ist_unbekannt():
    p = fresh()
    assert poll(p)[:2] == ("unknown", None)


def test_zahlzeilen_und_andere_spiele():
    p = fresh()
    p.feed("srv", "[12:00:00] [Server thread/INFO]: There are 4 of a max of 20 players online: a, b, c, d")
    assert poll(p)[:3] == ("busy", "console", 4)
    p.feed("srv", "Connections 0 ZDOS:1234  sent:0 recv:0")
    assert poll(p)[:3] == ("empty", "console", 0)
    p.feed("srv", "[INFO] Player connected: Kim, xuid: 123")
    assert poll(p)[2] == 1
    p.feed("srv", "2026-10-19 12:00:00 [JOIN] Ole joined the game")
    assert poll(p)[2] == 2
    p.feed("srv", ": 3 players connected.")
    assert poll(p)[2] == 3
    p.feed("srv", "No players connected.")
    assert poll(p)[:3] == ("empty", "console", 0)


def test_neustart_setzt_konsole_zurueck():
    p = fresh()
    p.feed("srv", "[12:00:00] [Server thread/INFO]: Alex joined the game")
    assert poll(p)[2] == 1
    poll(p, state="offline")
    assert poll(p, prev="offline", started="2026-10-19T11:00:00+00:00")[:3] == ("empty", "console", 0)


def test_startzeit_schwankt_ohne_neustart():
    p = fresh()
    p.feed("srv", "[12:00:00] [Server thread/INFO]: Alex joined the game")
    poll(p)
    assert poll(p, started="2026-10-19T10:00:01+00:00")[2] == 1


def test_traffic_schaetzung_mit_gelerntem_leerlauf(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("hatchtower.presence.time.monotonic", lambda: clock[0])
    p = fresh()
    p.traffic.setdefault("srv", __import__("hatchtower.presence", fromlist=["TrafficTrack"]).TrafficTrack())
    p.traffic["srv"].baseline_at = 1e12          # do not read the database
    p.traffic["srv"].learned_idle = 500.0         # idle: 500 B/s
    total = 0
    for _ in range(4):                            # 1 KB/s: still idle
        total += 10 * 1024
        clock[0] += 10
        result = poll(p, total)
    assert result[:2] == ("empty", "traffic")
    for _ in range(13):                           # 20 KB/s: someone is playing
        total += 10 * 20 * 1024
        clock[0] += 10
        result = poll(p, total)
    assert result[:3] == ("busy", "traffic", None)


def test_minecraft_java_ping():
    async def run():
        async def handler(reader, writer):
            await reader.read(256)
            body = json.dumps({"description": {"text": "Test", "extra": [{"text": " Welt"}]},
                               "players": {"online": 2, "max": 10}}).encode()
            data = gamequery._varint(0) + gamequery._varint(len(body)) + body
            writer.write(gamequery._varint(len(data)) + data)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await gamequery.GameQuery()._probe("minecraft", "127.0.0.1", port)

    result = asyncio.run(run())
    assert (result.name, result.players, result.max_players, result.protocol) == ("Test Welt", 2, 10, "minecraft")


def test_bedrock_ping():
    async def run():
        loop = asyncio.get_running_loop()

        class Answer(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                motd = b"MCPE;Bedrock Welt;712;1.21;3;30;123;Level;Survival"
                self.transport.sendto(b"\x1c" + data[1:9] + b"\0" * 8 + gamequery.RAKNET_MAGIC
                                      + struct.pack(">H", len(motd)) + motd, addr)

        transport, _ = await loop.create_datagram_endpoint(Answer, local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        try:
            return await gamequery.GameQuery()._probe("bedrock", "127.0.0.1", port)
        finally:
            transport.close()

    result = asyncio.run(run())
    assert (result.name, result.players, result.max_players, result.protocol) == ("Bedrock Welt", 3, 30, "bedrock")


def test_minecraft_java_nur_bei_minecraft_eggs():
    # the TCP ping shows up as "is connecting" in other games' consoles
    assert gamequery.is_minecraft_java("Paper")
    assert gamequery.is_minecraft_java("Forge Enhanced Minecraft")
    assert not gamequery.is_minecraft_java("Minecraft Bedrock Edition")
    assert not gamequery.is_minecraft_java("Terraria Vanilla")
    assert not gamequery.is_minecraft_java("Counter-Strike: Source")


def test_verlauf_nur_bei_wechseln(world):
    from hatchtower import db
    p = fresh()
    poll(p, prev="starting")
    p.record("srv-a", "empty", "console", 0)
    p.record("srv-a", "empty", "console", 0)                       # unchanged: no new entry
    p.console["srv-a"] = p.console.pop("srv")
    p.feed("srv-a", "[12:00:00] [Server thread/INFO]: Alex joined the game")
    p.record("srv-a", "busy", "console", 1)
    p.record("srv-a", None, None, None)                            # stopped: nothing written
    with db.connect() as con:
        rows = con.execute("SELECT presence, players, evidence FROM presence_log ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("empty", 0, ""), ("busy", 1, "Alex")]
    page = world.client(world.ids["olga"]).get("/servers/srv-a/players").text
    assert "Verlauf" in page and "Alex" in page
