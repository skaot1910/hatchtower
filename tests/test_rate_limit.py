"""Panel rate limit: only answers to the client key count (other keys have their own limit), and the status poll
stretches its interval so it never uses more than its share of that limit."""
import asyncio
from pathlib import Path

import httpx

from hatchtower import monitor
from hatchtower.config import Settings
from hatchtower.ptero import PteroClient


def client_with(limits: dict[str, tuple[str, str]], **keys) -> PteroClient:
    """PteroClient whose panel answers every request with the rate limit headers of the key used."""
    settings = Settings(secret_key="s", data_key="d", data_dir=Path("."), pterodactyl_url="http://panel.test", **keys)
    client = PteroClient(settings)

    def answer(request: httpx.Request) -> httpx.Response:
        limit, remaining = limits[request.headers["authorization"].removeprefix("Bearer ")]
        return httpx.Response(200, json={"data": []},
                              headers={"x-ratelimit-limit": limit, "x-ratelimit-remaining": remaining})

    client._http = httpx.AsyncClient(base_url="http://panel.test", transport=httpx.MockTransport(answer))
    return client


def test_nur_der_client_key_zaehlt():
    client = client_with({"app": ("240", "239"), "cli": ("128", "20")}, app_key="app", client_key="cli")

    async def run():
        await client._get("/api/client/servers/a/resources", key="client")
        assert client.rate[:2] == (128, 20)
        # The application key has plenty left, but that says nothing about the client key
        await client._get("/api/application/servers", key="app")
        assert client.rate[:2] == (128, 20)
    asyncio.run(run())


def test_eigenes_limit_der_websocket_route():
    """Pterodactyl and Pelican answer /websocket with a limit of 5 per minute that only applies to that route."""
    client = client_with({"cli": ("256", "200")}, app_key="app", client_key="cli")

    def answer(request: httpx.Request) -> httpx.Response:
        own = request.url.path.endswith("/websocket")
        return httpx.Response(200, json={"data": {"token": "t", "socket": "wss://w"}},
                              headers={"x-ratelimit-limit": "5" if own else "256",
                                       "x-ratelimit-remaining": "3" if own else "200"})
    client._http = httpx.AsyncClient(base_url="http://panel.test", transport=httpx.MockTransport(answer))

    async def run():
        await client._get("/api/client/servers/a/resources", key="client")
        await client._get("/api/client/servers/a/websocket", key="client")
        assert client.rate[:2] == (256, 200)
    asyncio.run(run())


def test_ein_key_fuer_alles():
    """Calagopus: the one API key is stored as both keys, so every answer counts."""
    client = client_with({"one": ("128", "100")}, panel_type="calagopus", app_key="one", client_key="one")
    asyncio.run(client._get("/api/admin/servers", key="app"))
    assert client.rate[:2] == (128, 100)


def poller(rate, servers: int, interval: int = 10) -> monitor.Monitor:
    m = monitor.Monitor.__new__(monitor.Monitor)
    m.client = type("C", (), {"rate": rate})()
    m.poll_interval = interval
    m._polled = servers
    return m


def test_abfrage_ohne_angabe_des_panels():
    assert poller(None, 50).poll_delay() == 10


def test_abfrage_streckt_sich_mit_vielen_servern():
    # 128 per minute, half of it for the status poll: 64 requests -> 10 servers fit into 10 seconds
    assert poller((128, 128, 0.0), 10).poll_delay() == 10
    assert poller((128, 128, 0.0), 32).poll_delay() == 30
    # never so long that states would count as unknown (STALE_SECONDS)
    assert poller((128, 128, 0.0), 200).poll_delay() == monitor.POLL_MAX_DELAY
    # Pterodactyl's default of 720 per minute: even 60 servers stay at the configured interval
    assert poller((720, 720, 0.0), 60).poll_delay() == 10


def test_abfrage_bleibt_im_anteil():
    """Up to where the cap starts, the poll never needs more than POLL_SHARE of the limit."""
    for limit in (60, 128, 240, 720):
        for servers in range(1, 200):
            delay = poller((limit, limit, 0.0), servers).poll_delay()
            if delay < monitor.POLL_MAX_DELAY:
                assert servers * 60 / delay <= limit * monitor.POLL_SHARE + 1e-9


def test_systemseite_zeigt_drosselung(world):
    from hatchtower import main
    main.app.state.ptero.rate = (128, 30, 0.0)
    main.app.state.monitor.poll_delay = lambda: 30
    page = world.client(world.ids["olga"]).get("/admin/system").text
    assert "alle 30 s" in page and "verlängert wegen des Rate-Limits" in page
    assert "30 von 128 Anfragen pro Minute frei" in page
