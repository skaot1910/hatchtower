"""Einrichtungsassistent: Verbindungsprüfung (diagnose.py), Entwurf, Speichern nur nach Bestätigung.

Das Panel wird mit httpx.MockTransport nachgebildet; es geht nichts ins Netz.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from hatchtower import db, diagnose, main, setup
from hatchtower.config import DB_KEYS

APP = "ptla_" + "a" * 43
CLIENT = "ptlc_" + "c" * 43


def _list(items, total=None):
    return {"object": "list", "data": [{"attributes": a} for a in items],
            "meta": {"pagination": {"total": total if total is not None else len(items), "total_pages": 1}}}


SERVER = {"identifier": "abc123", "relationships": {"egg": {"object": "egg", "attributes": {"name": "Minecraft"}},
                                                     "allocations": {"object": "list", "data": []}}}
NODE = {"name": "Node 1", "fqdn": "wings.test", "scheme": "https", "daemon_listen": 8080, "daemon_sftp": 2022}


def panel(overrides: dict | None = None):
    """Nachgebildetes Pterodactyl. overrides: Pfad → Antwort (oder Ausnahme), ersetzt das Standardverhalten."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        for key, value in (overrides or {}).items():
            if path == key or (key.endswith("*") and path.startswith(key[:-1])):
                if isinstance(value, Exception):
                    raise value
                return value
        if request.url.host == "wings.test":
            return httpx.Response(401, json={"error": "The required authorization heads were not present"})
        if "authorization" not in request.headers:
            return httpx.Response(401, json={"errors": [{"code": "AuthenticationException"}]})
        if path == "/api/application/nodes":
            return httpx.Response(200, json=_list([NODE]))
        if path == "/api/application/servers":
            return httpx.Response(200, json=_list([SERVER]))
        if path == "/api/client":
            return httpx.Response(200, json=_list([{"identifier": "abc123"}]))
        if path.startswith("/api/client/servers/abc123/"):
            return httpx.Response(200, json={"object": "x", "attributes": {}, "data": []})
        return httpx.Response(404, json={"errors": [{"code": "NotFound"}]})

    transport = httpx.MockTransport(handler)
    transport.seen = seen
    return transport


@pytest.fixture
def net(monkeypatch):
    """Keine echten Verbindungen: TCP-Prüfung und eigene IP fest vorgeben."""
    state = {"tcp": {("wings.test", 8080): True, ("wings.test", 2022): True}}

    async def tcp(host, port):
        return state["tcp"].get((host, port), False)
    monkeypatch.setattr(setup, "tcp_check", tcp)
    monkeypatch.setattr(setup, "manager_ip_towards", lambda url: "10.0.0.5")
    return state


def run(cand, transport):
    return asyncio.run(diagnose.run(main.settings, cand, transport=transport))


def cand(**kw):
    values = {"panel_type": "pterodactyl", "url": "https://panel.test", "app_key": APP, "client_key": CLIENT}
    values.update(kw)
    return diagnose.Candidate(**values)


def titles(report, level=None):
    from hatchtower import i18n
    return [i18n.t(c.title) for c in report.checks if level is None or c.level == level]


# -- Prüfung --------------------------------------------------------------------------------------

def test_alles_in_ordnung(net):
    t = panel()
    report = run(cand(), t)
    assert report.errors == 0 and report.warnings == 0, titles(report)
    assert report.ok_to_save and report.facts["servers"] == 1 and report.facts["client_servers"] == 1
    levels = {f["name"]: f["level"] for f in report.features}
    control = levels.pop("Starten, Stoppen, Konsolenbefehle")
    assert set(levels.values()) == {diagnose.OK}
    assert control == (diagnose.OPTIONAL if main.settings.read_only else diagnose.OK)   # im Nur-lesen-Modus aus
    assert all(r.method == "GET" for r in t.seen), "die Prüfung darf nur lesen"


def test_sftp_ist_nur_optional_wings_nur_warnung(net):
    net["tcp"][("wings.test", 2022)] = False
    report = run(cand(), panel())
    assert report.errors == 0 and report.warnings == 0
    assert any("SFTP" in x for x in titles(report, diagnose.OPTIONAL))
    net["tcp"][("wings.test", 8080)] = False
    report = run(cand(), panel())
    assert report.errors == 0 and report.warnings == 1 and report.ok_to_save
    assert report.caps["wings"] is False


def test_panel_nicht_erreichbar(net):
    exc = httpx.ConnectError("[Errno 111] Connection refused")
    exc.__cause__ = ConnectionRefusedError(111, "Connection refused")
    report = run(cand(), panel({"/api/application/nodes": exc}))
    assert report.errors == 1 and report.skipped == ["app", "client", "wings"] and not report.ok_to_save
    check = report.checks[0]
    assert check.level == diagnose.ERROR and check.fix and "lehnt die Verbindung ab" in str(check.detail)


def test_weiterleitung_auf_https(net):
    t = panel({"/api/application/nodes": httpx.Response(301, headers={"location": "https://panel.test/api/application/nodes"})})
    report = run(cand(url="http://panel.test"), t)
    assert report.errors == 1
    assert any("https://panel.test" in str(f) for f in report.checks[0].fix)


def test_anderes_panel_erkannt(net):
    t = panel({"/api/application/nodes": httpx.Response(200, text="<html>SPA</html>"),
               "/api/admin/nodes": httpx.Response(401, json={"errors": ["unauthorized"]})})
    report = run(cand(), t)
    assert report.errors == 1 and "Calagopus" in str(report.checks[0].title)


def test_vertauschte_keys(net):
    report = run(cand(app_key=CLIENT), panel())
    assert report.has_errors("app") and "vertauscht" in str(report.area("app")[0].detail)


def test_key_aus_anderem_panel(net):
    report = run(cand(app_key="papp_" + "x" * 43), panel())
    assert report.has_errors("app") and "Pelican" in str(report.area("app")[0].title)


def test_fehlendes_recht_servers(net):
    t = panel({"/api/application/servers": httpx.Response(403, json={"errors": [{"code": "AccessDenied"}]})})
    report = run(cand(), t)
    assert report.has_errors("app")
    assert any("Servers" in str(f) for c in report.area("app") for f in c.fix)


def test_fehlende_teilrechte_sind_warnungen(net):
    server = dict(SERVER, relationships={"egg": {"object": "null_resource", "attributes": None}})
    t = panel({"/api/application/servers": httpx.Response(200, json=_list([server])),
               "/api/client/servers/abc123/websocket": httpx.Response(403, json={"errors": []})})
    report = run(cand(), t)
    assert report.errors == 0 and report.ok_to_save
    assert report.warnings == 3       # Eggs, Allocations, Konsole
    assert report.caps["console"] is False


def test_client_key_sieht_nur_einen_teil(net):
    t = panel({"/api/application/servers": httpx.Response(200, json=_list([SERVER], total=5))})
    report = run(cand(), t)
    assert report.errors == 0 and any("1 von 5" in x for x in titles(report, diagnose.WARNING))


def test_client_key_403_nennt_allowed_ip(net):
    t = panel({"/api/client": httpx.Response(403, json={"errors": [{"code": "AccessDenied"}]})})
    report = run(cand(), t)
    assert report.has_errors("client")
    assert any("10.0.0.5" in str(f) for c in report.area("client") for f in c.fix)


def test_altes_panel_ohne_aktivitaet(net):
    t = panel({"/api/client/servers/abc123/activity": httpx.Response(404, json={"errors": []})})
    report = run(cand(), t)
    assert report.errors == 0 and any("älter" in x for x in titles(report, diagnose.WARNING))


def test_keys_erscheinen_nie_in_meldungen(net):
    t = panel({"/api/application/servers": httpx.Response(500, json={"errors": [{"detail": f"bad token {APP}"}]})})
    report = run(cand(), t)
    text = repr(report)
    assert APP not in text and CLIENT not in text
    assert diagnose.redact(f"x {CLIENT} y", []) == "x ••• y"


def test_unverschluesselt_im_internet_ist_warnung(net):
    report = run(cand(url="http://panel.example.com"), panel())
    assert any("nicht verschlüsselt" in x for x in titles(report, diagnose.WARNING))
    report = run(cand(url="http://192.168.1.10"), panel())
    assert not any("nicht verschlüsselt" in x for x in titles(report))


# -- Assistent: Entwurf, Prüfen, Speichern -------------------------------------------------------

def _saved() -> dict:
    with db.connect() as con:
        return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%'")}


@pytest.fixture
def fake_check(monkeypatch):
    """diagnose.run ersetzen; merkt sich die geprüften Kandidaten. errors steuert das Ergebnis."""
    calls = []
    state = {"errors": False}

    async def fake(settings, c, **kw):
        calls.append(c)
        report = diagnose.Report(panel_type=c.panel_type, url=c.url)
        if state["errors"] or not c.app_key:
            report.add("app", diagnose.ERROR, diagnose.Msg("Kaputt"))
        else:
            report.add("app", diagnose.OK, diagnose.Msg("Gut"))
        return report
    monkeypatch.setattr(diagnose, "run", fake)
    setup._drafts.clear()
    yield calls, state
    setup._drafts.clear()


def test_pruefen_speichert_nichts(world, fake_check):
    calls, _ = fake_check
    before = _saved()
    c = world.client(world.ids["olga"])
    r = c.post("/admin/connection/check", data={"csrf": c.csrf, "step": "panel", "panel_type": "pterodactyl",
                                                "panel_url": "https://panel.test", "app_key": APP})
    assert r.status_code == 303 and r.headers["location"] == "/setup?step=client"
    assert APP not in r.headers["location"]
    assert _saved() == before and main.settings.app_key == "ptla_test"
    assert calls[-1].app_key == APP and calls[-1].client_key == "ptlc_test"     # gleicher Host: Client-Key bleibt
    page = c.get("/setup?step=review")
    assert page.status_code == 200 and APP not in page.text and "ptla_test" not in page.text
    assert world.audit("connection.check")


def test_speichern_nur_mit_bestaetigung(world, fake_check):
    c = world.client(world.ids["olga"])
    c.post("/admin/connection/check", data={"csrf": c.csrf, "step": "panel", "panel_type": "pterodactyl",
                                            "panel_url": "https://panel.test", "app_key": APP})
    before = _saved()
    r = c.post("/admin/connection/save", data={"csrf": c.csrf})
    assert r.status_code == 303 and "step=review" in r.headers["location"] and _saved() == before
    r = c.post("/admin/connection/save", data={"csrf": c.csrf, "confirm": "1"})
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/connection")   # bestehende Verbindung
    saved = _saved()
    assert saved[DB_KEYS["app_key"]] != APP and main.vault.decrypt(saved[DB_KEYS["app_key"]]) == APP
    detail = world.audit("connection.update")[-1]["detail"]
    assert '"bestaetigt": true' in detail and APP not in detail
    assert setup.draft_get(world.ids["olga"]) is None


def test_speichern_bei_fehlern_gesperrt(world, fake_check):
    _, state = fake_check
    state["errors"] = True
    c = world.client(world.ids["olga"])
    c.post("/admin/connection/check", data={"csrf": c.csrf, "step": "panel", "panel_type": "pterodactyl",
                                            "panel_url": "https://panel.test", "app_key": APP})
    before = _saved()
    r = c.post("/admin/connection/save", data={"csrf": c.csrf, "confirm": "1"})
    assert "step=review" in r.headers["location"] and _saved() == before


def test_geaenderte_eingaben_muessen_neu_geprueft_werden(world, fake_check):
    c = world.client(world.ids["olga"])
    c.post("/admin/connection/check", data={"csrf": c.csrf, "step": "panel", "panel_type": "pterodactyl",
                                            "panel_url": "https://panel.test", "app_key": APP})
    draft = setup.draft_get(world.ids["olga"])
    setup.draft_update(draft, main.vault, client_key=CLIENT)          # geändert, aber nicht geprüft
    before = _saved()
    r = c.post("/admin/connection/save", data={"csrf": c.csrf, "confirm": "1"})
    assert "step=review" in r.headers["location"] and _saved() == before


def test_neuer_host_nimmt_gespeicherte_keys_nicht_mit(world, fake_check):
    """H-1: Gespeicherte Keys gehen nie an eine Adresse auf einem anderen Host."""
    calls, _ = fake_check
    c = world.client(world.ids["olga"])
    r = c.post("/admin/connection/check", data={"csrf": c.csrf, "step": "panel", "panel_type": "pterodactyl",
                                                "panel_url": "https://evil.test", "app_key": ""})
    assert r.headers["location"] == "/setup?step=panel"
    assert calls[-1].app_key == "" and calls[-1].client_key == ""
    # Auch https → http auf demselben Host verwirft die Keys, http → https nicht
    assert not setup.same_origin_for_keys("https://panel.test", "http://panel.test")
    assert setup.same_origin_for_keys("http://panel.test", "https://PANEL.test:8443")


def test_alte_panel_route_schickt_key_nicht_an_neuen_host(world, monkeypatch):
    called = []

    async def spy(settings, panel_type, url, key):
        called.append(key)
        return {"nodes": [], "servers": 0}
    monkeypatch.setattr(setup, "test_panel", spy)
    c = world.client(world.ids["olga"])
    c.post("/admin/connection/panel", data={"csrf": c.csrf, "panel_type": "pterodactyl", "panel_url": "https://evil.test",
                                            "app_key": "", "next": "/admin/connection"})
    assert "ptla_test" not in called
    assert main.settings.app_key == "ptla_test" and main.settings.client_key == "ptlc_test"


def test_verwerfen_und_assistent_neu_starten(world, fake_check):
    c = world.client(world.ids["olga"])
    assert c.get("/admin/connection/wizard").headers["location"] == "/setup?step=panel"
    assert setup.draft_get(world.ids["olga"])["repair"] is True
    r = c.post("/admin/connection/discard", data={"csrf": c.csrf})
    assert r.headers["location"].startswith("/admin/connection") and setup.draft_get(world.ids["olga"]) is None


def test_schritte_und_bericht_seiten(world, fake_check, monkeypatch):
    async def final():
        return {"report": None, "mode": "read-only", "timezone": "UTC", "public_host": "", "trusted_proxies": "",
                "default_view": "full", "legal": []}
    monkeypatch.setattr(main, "final_report", final)
    c = world.client(world.ids["olga"])
    for step in ("panel", "client", "nodes", "review", "options", "legal", "done"):
        r = c.get(f"/setup?step={step}")
        assert r.status_code == 200, step
        assert "ptla_test" not in r.text and "ptlc_test" not in r.text


def test_ungueltige_anfrage_zeigt_keine_werte():
    from fastapi.exceptions import RequestValidationError
    from starlette.requests import Request
    response = asyncio.run(main._invalid_request(
        Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
        RequestValidationError([{"loc": ("body", "app_key"), "msg": "bad", "input": APP, "type": "x"}])))
    assert APP.encode() not in response.body and b"app_key" in response.body
