"""Mehrere Panels: Pterodactyl, Pelican und Calagopus."""
import asyncio
from types import SimpleNamespace

import pytest

from hatchtower import db, i18n, main, panels, setup
from hatchtower.calagopus import CalagopusClient
from hatchtower.config import DB_KEYS
from hatchtower.ptero import PteroClient, ReadOnlyViolation

# Antworten wie vom echten Calagopus (gekürzt)
SERVER = {
    "uuid": "b4cc300e-2788-4ee6-b500-1c2e685965ee", "uuid_short": "b4cc300e", "name": "Minecraft Testwelt",
    "is_suspended": False, "egg": {"uuid": "e1", "name": "Paper"},
    "allocation": {"uuid": "a1", "ip": "0.0.0.0", "ip_alias": "192.168.178.100", "port": 25565, "is_primary": True},
    "limits": {"cpu": 25, "memory": 256, "swap": 0, "disk": 1024},
}
NODE = {"uuid": "n1", "name": "Integrated Node", "maintenance_enabled": False, "sftp_port": 2022,
        "public_url": "http://panel.test:8000/wings-proxy/n1/", "url": "http://localhost:64332/"}


class FakeCalagopus(CalagopusClient):
    def __init__(self, read_only=False, files=250):
        self.settings = SimpleNamespace(read_only=read_only, node_overrides={}, app_key="k", client_key="k")
        self._wings_hosts = set()
        self.calls = []
        self.files = [{"name": f"f{i:03}", "directory": i == 0, "file": i != 0, "size": i, "mime": "text/plain",
                       "modified": "2026-10-12T00:00:00Z"} for i in range(files)]

    async def _request(self, method, path, *, key, params=None, json_body=None, content=None, raw=False):
        self._guard(method)
        if key == "app" and method != "GET":
            raise ReadOnlyViolation("nur lesen")
        self.calls.append((method, path, params, json_body))
        page, per = int((params or {}).get("page", 1)), int((params or {}).get("per_page", 100))
        if path == "/api/admin/servers":
            return {"servers": {"total": 1, "page": 1, "data": [SERVER]}}
        if path == "/api/admin/nodes":
            return {"nodes": {"total": 1, "page": 1, "data": [NODE]}}
        if path.endswith("/resources"):
            return {"resources": {"state": "running", "cpu_absolute": 12.5, "memory_bytes": 1000, "disk_bytes": 5,
                                  "uptime": 60000, "network": {"rx_bytes": 7, "tx_bytes": 8}}}
        if path.endswith("/startup/variables"):
            return {"variables": [{"env_variable": "GAME", "value": "minecraft", "default_value": "x"},
                                  {"env_variable": "LEER", "value": None, "default_value": None}]}
        if path.endswith("/files/list"):
            chunk = self.files[(page - 1) * per: page * per]
            return {"entries": {"total": len(self.files), "page": page, "data": chunk}}
        if path.endswith("/activity"):
            return {"activities": {"total": 3, "data": [
                {"event": "server:power.action", "data": {"action": "stop"}, "created": "2026-10-12T10:00:00Z",
                 "is_schedule": False},
                {"event": "server:console.command", "data": {"command": "stop"}, "created": "2026-10-12T10:01:00Z",
                 "is_schedule": False},
                {"event": "server:power.action", "data": {"action": "restart"}, "created": "2026-10-12T10:02:00Z",
                 "is_schedule": True}]}}
        if path.endswith("/websocket"):
            return {"token": "t", "url": "ws://panel.test:8000/wings-proxy/n1/api/servers/x/ws"}
        if path.endswith("/files/download") or path.endswith("/files/upload"):
            return {"url": "http://panel.test:8000/wings-proxy/n1/download?token=t"}
        return {}


def run(coro):
    return asyncio.run(coro)


def test_calagopus_serverliste():
    c = FakeCalagopus()
    [s] = run(c.list_servers())
    assert (s.identifier, s.uuid, s.name, s.egg) == ("b4cc300e", SERVER["uuid"], "Minecraft Testwelt", "Paper")
    assert (s.primary_ip, s.primary_port, s.limit_memory, s.limit_cpu, s.limit_disk) == ("192.168.178.100", 25565, 256, 25, 1024)
    assert s.allocations == [{"id": "a1", "ip": "192.168.178.100", "port": 25565}]


def test_calagopus_nodes_ueber_wings_proxy():
    c = FakeCalagopus()
    [n] = run(c.list_nodes())
    assert (n["fqdn"], n["daemon_listen"], n["daemon_sftp"], n["scheme"]) == ("panel.test", 8000, 2022, "http")
    # Signierte URLs über den Proxy gelten als bekannter Wings-Node, fremde nicht
    assert run(c.wings_url("http://panel.test:8000/wings-proxy/n1/download?token=t")).startswith("http://panel.test")
    with pytest.raises(Exception):
        run(c.wings_url("http://evil.example/download?token=t"))


def test_calagopus_status_und_variablen():
    c = FakeCalagopus()
    r = run(c.resources("b4cc300e"))
    assert (r["state"], r["cpu"], r["memory_bytes"], r["uptime_ms"], r["rx_bytes"], r["tx_bytes"]) == \
        ("running", 12.5, 1000, 60000, 7, 8)
    assert run(c.startup_variables("b4cc300e")) == {"GAME": "minecraft", "LEER": ""}


def test_calagopus_dateiliste_ueber_mehrere_seiten():
    c = FakeCalagopus(files=250)
    files = run(c.list_files("b4cc300e", "/"))
    assert len(files) == 250
    assert files[0] == {"name": "f000", "is_file": False, "size": 0, "modified_at": "2026-10-12T00:00:00Z",
                        "mimetype": "text/plain"}
    assert [p for m, path, p, b in c.calls if path.endswith("/files/list")][-1]["page"] == 3


def test_calagopus_aktivitaet_wie_pterodactyl():
    events = run(FakeCalagopus().activity("b4cc300e"))
    assert [e["event"] for e in events] == ["server:power.stop", "server:console.command", "server:schedule.power.restart"]
    assert events[1]["properties"] == {"command": "stop"} and events[0]["timestamp"] == "2026-10-12T10:00:00Z"


def test_calagopus_steuern_und_dateien():
    c = FakeCalagopus()
    run(c.power("b4cc300e", "restart"))
    run(c.create_folder("b4cc300e", "/", "neu"))
    assert ("POST", "/api/client/servers/b4cc300e/power", None, {"action": "restart"}) in c.calls
    assert ("POST", "/api/client/servers/b4cc300e/files/create-directory", None, {"root": "/", "name": "neu"}) in c.calls
    token, url = run(c.websocket_credentials("b4cc300e"))
    assert token == "t" and url.startswith("ws://panel.test")
    dl = [p for m, path, p, b in c.calls if path.endswith("/files/download")]
    run(c.download_url("b4cc300e", "/world/level.dat"))
    dl = [p for m, path, p, b in c.calls if path.endswith("/files/download")][0]
    assert (dl["root"], dl["files"], dl["directory"]) == ("/world", "level.dat", "false")


def test_calagopus_read_only_blockiert_schreiben():
    c = FakeCalagopus(read_only=True)
    with pytest.raises(ReadOnlyViolation):
        run(c.power("b4cc300e", "stop"))
    with pytest.raises(ReadOnlyViolation):
        run(c.command("b4cc300e", "say hi"))
    assert c.calls == []


def test_richtiger_adapter_je_panel():
    base = SimpleNamespace(pterodactyl_url="http://p", app_key="", client_key="", read_only=True, node_overrides={})
    for kind, cls in (("pterodactyl", PteroClient), ("pelican", PteroClient), ("calagopus", CalagopusClient)):
        client = panels.make_client(SimpleNamespace(**vars(base), panel_type=kind))
        assert type(client) is cls
        run(client.close())
    assert panels.valid("unbekannt") == "pterodactyl"


def test_key_pruefung_je_panel():
    assert setup.key_problem("ptla_" + "x" * 43, "ptla_") is None
    assert setup.key_problem("papp_" + "x" * 43, "papp_") is None
    assert setup.key_problem("ptla_" + "x" * 43, "papp_")            # Pterodactyl-Key bei Pelican
    assert setup.key_problem("abcd_" + "x" * 43, "") is None          # Calagopus: kein festes Präfix
    assert setup.key_problem("kurz", "")
    assert setup.key_problem("abcd_ xxxxxxxxxxxxxxxxxxxxxxxxx", "")


def test_platzhalter_panel_name():
    old = i18n.panel_name()
    try:
        i18n.set_panel("Pelican")
        assert i18n.t("{panel} antwortet wieder (Ausfall etwa {n} Minuten).", n=3) == \
            "Pelican antwortet wieder (Ausfall etwa 3 Minuten)."
        i18n.set_language("en")
        assert "Pelican" in i18n.t("{panel} nicht erreichbar: {grund}", grund="X")
    finally:
        i18n.set_language("de")
        i18n.set_panel(old)


def _saved() -> dict:
    with db.connect() as con:
        return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%'")}


def test_assistent_calagopus_ein_key(world, monkeypatch):
    async def ok(settings, panel_type, url, key):
        assert panel_type == "calagopus"
        return {"nodes": [{}], "servers": 3}
    monkeypatch.setattr(setup, "test_panel", ok)
    c = world.client(world.ids["olga"])
    r = c.post("/admin/connection/panel", data={"csrf": c.csrf, "panel_type": "calagopus", "panel_url": "http://cg.test:8000",
                                                 "app_key": "abcd_" + "y" * 43, "next": "/setup?step=panel"})
    assert r.status_code == 303 and "step=nodes" in r.headers["location"]
    saved = _saved()
    assert saved[DB_KEYS["panel_type"]] == "calagopus"
    assert main.vault.decrypt(saved[DB_KEYS["client_key"]]) == "abcd_" + "y" * 43   # gleicher Key für beides
    assert world.audit("connection.update")


def test_panelwechsel_braucht_neuen_key(world, monkeypatch):
    async def never(*a, **k):
        raise AssertionError("ohne Key darf nicht getestet werden")
    monkeypatch.setattr(setup, "test_panel", never)
    before = _saved()
    c = world.client(world.ids["olga"])
    r = c.post("/admin/connection/panel", data={"csrf": c.csrf, "panel_type": "pelican", "panel_url": "https://panel.test",
                                                 "app_key": "", "next": "/admin/connection"})
    assert r.status_code == 303 and _saved() == before


def test_client_key_schritt_entfaellt_bei_calagopus(world, monkeypatch):
    monkeypatch.setattr(main.settings, "panel_type", "calagopus")
    c = world.client(world.ids["olga"])
    r = c.post("/admin/connection/client", data={"csrf": c.csrf, "client_key": "x" * 48, "next": "/setup?step=client"})
    assert r.status_code == 303 and "step=nodes" in r.headers["location"]
    assert DB_KEYS["client_key"] not in _saved() or _saved()[DB_KEYS["client_key"]] == ""
    page = c.get("/admin/connection")
    assert page.status_code == 200 and "Client-API-Key" not in page.text


def test_ueber_seite_nennt_alle_panels(world):
    c = world.client(world.ids["olga"])
    text = c.get("/about").text
    assert all(name in text for name in ("Pterodactyl", "Pelican", "Calagopus"))
