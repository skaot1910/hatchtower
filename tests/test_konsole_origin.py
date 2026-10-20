"""Console websocket: Wings only accepts the Origin from `remote` in its config.yml (character by character).
Hatchtower tries the token issuer (APP_URL), the panel address and http/https variants and remembers what works."""
import asyncio
import base64
import json
from types import SimpleNamespace

from websockets.asyncio.server import serve

from hatchtower import wings


def jwt(claims: dict) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{part({'alg': 'HS256'})}.{part(claims)}.c2ln"


class FakeClient:
    def __init__(self, panel_url: str, token: str, socket_url: str):
        self.settings = SimpleNamespace(pterodactyl_url=panel_url, read_only=True)
        self.token, self.socket_url = token, socket_url

    async def websocket_credentials(self, identifier):
        return self.token, self.socket_url

    async def wings_url(self, url):
        return url


async def fake_wings(handler_seen: list, remote: str):
    """Like Wings: 403 unless Origin == remote; answers auth and sends one console line."""
    async def handler(ws):
        handler_seen.append(ws.request.headers.get("Origin"))
        async for raw in ws:
            msg = json.loads(raw)
            if msg["event"] == "auth":
                await ws.send(json.dumps({"event": "auth success"}))
            elif msg["event"] == "send logs":
                await ws.send(json.dumps({"event": "console output", "args": ["Hallo"]}))
    return await serve(handler, "127.0.0.1", 0, origins=[remote])


def collect(panel_url: str, token: str, remote: str, port_override: int | None = None):
    async def go():
        seen: list = []
        server = await fake_wings(seen, remote)
        port = port_override or server.sockets[0].getsockname()[1]
        client = FakeClient(panel_url, token, f"ws://127.0.0.1:{port}/api/servers/x/ws")
        items = []
        try:
            async with asyncio.timeout(10):
                async for item in wings.console_stream(client, "x"):
                    items.append(item)
                    if item["type"] in ("line", "error"):
                        break
        finally:
            server.close()
            await server.wait_closed()
        return items, seen
    wings._origins.clear()
    return asyncio.run(go())


def test_origin_aus_dem_token_wenn_panel_adresse_abweicht():
    # Hatchtower uses the LAN address, Wings knows the public domain (APP_URL = iss)
    items, seen = collect("http://192.168.1.10:8080", jwt({"iss": "https://panel.example.de"}),
                          "https://panel.example.de")
    assert items[-1] == {"type": "line", "text": "Hallo", "source": "console output"}
    assert seen == ["https://panel.example.de"]
    assert list(wings._origins.values()) == ["https://panel.example.de"]


def test_panel_adresse_passt_bleibt_erste_wahl():
    items, seen = collect("http://192.168.1.10", jwt({"iss": "http://192.168.1.10"}), "http://192.168.1.10")
    assert items[-1]["type"] == "line" and seen == ["http://192.168.1.10"]


def test_http_https_getauscht():
    items, _ = collect("http://panel.example.de", jwt({"iss": "panel"}), "https://panel.example.de")
    assert items[-1]["type"] == "line"


def test_nichts_passt_endgueltiger_fehler_mit_hinweis():
    items, seen = collect("http://192.168.1.10", jwt({"iss": "https://panel.example.de"}), "https://andere.example")
    assert seen == []
    err = items[-1]
    assert err["type"] == "error" and err["fatal"] is True
    assert "allowed_origins" in err["text"] and "http://192.168.1.10" in err["text"]


def test_wings_nicht_erreichbar_ist_kein_endgueltiger_fehler():
    items, _ = collect("http://192.168.1.10", jwt({}), "http://192.168.1.10", port_override=1)
    assert items[-1]["type"] == "error" and not items[-1].get("fatal")
    assert "Wings nicht erreichbar" in items[-1]["text"]


def test_token_issuer():
    assert wings.token_issuer(jwt({"iss": "https://panel.example.de/"})) == "https://panel.example.de"
    assert wings.token_issuer(jwt({"iss": "panel"})) == ""          # Calagopus
    assert wings.token_issuer("kein-jwt") == ""
    assert wings.token_issuer("a.!!!.c") == ""


def test_reihenfolge_der_kandidaten():
    token = jwt({"iss": "https://panel.example.de"})
    assert wings.origin_candidates("http://192.168.1.10/", token, "http://gemerkt") == [
        "http://gemerkt", "https://panel.example.de", "http://192.168.1.10",
        "https://gemerkt", "http://panel.example.de", "https://192.168.1.10"]
