"""Öffentliche Demo: Anmeldung ohne Passwort, der Demo-Benutzer darf nichts verändern, Owner arbeiten normal."""
from hatchtower import main, security


def _demo_login(world):
    c, prelogin = world.anonymous_with_prelogin()
    r = c.post("/demo/login", data={"prelogin": prelogin})
    return c, r


def test_ohne_demo_keine_anmeldung(world):
    world.user("gast", grants={"srv-a": {"server.view"}})
    c, r = _demo_login(world)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert security.SESSION_COOKIE not in r.headers.get("set-cookie", "")
    assert "Demo ansehen" not in c.get("/login").text


def test_demo_anmeldung_und_nur_lesen(world, monkeypatch):
    world.user("gast", grants={"srv-a": {"server.view", "power.start", "power.stop", "console.read", "console.command"}})
    monkeypatch.setattr(main.settings, "demo_user", "gast")
    assert "Demo ansehen" in world.client().get("/login").text
    c, r = _demo_login(world)
    assert r.status_code == 303 and r.headers["location"] == "/"
    page = c.get("/")
    assert page.status_code == 200 and "demo-banner" in page.text
    assert world.audit("login")

    before = world.snapshot()
    csrf = c.get("/").text.split('name="csrf-token" content="')[1].split('"')[0]
    r = c.post("/api/servers/srv-a/power", json={"signal": "start"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    r = c.post("/account/language", data={"csrf": csrf, "language": "en"})
    assert r.status_code == 403
    assert world.snapshot() == before
    assert world.ptero.writes == []
    assert world.audit("demo.blocked")

    r = c.post("/logout", data={"csrf": csrf})
    assert r.status_code == 303


def test_owner_ist_nie_demo(world, monkeypatch):
    monkeypatch.setattr(main.settings, "demo_user", "olga")
    c, r = _demo_login(world)
    assert r.headers["location"] == "/login"
    assert security.SESSION_COOKIE not in r.headers.get("set-cookie", "")


def test_owner_bleibt_unberuehrt(world, monkeypatch):
    world.user("gast", grants={"srv-a": {"server.view"}})
    monkeypatch.setattr(main.settings, "demo_user", "gast")
    owner = world.client(world.ids["olga"])
    r = owner.post("/api/servers/srv-a/power", json={"signal": "start"}, headers={"X-CSRF-Token": owner.csrf})
    assert r.status_code == 200
    assert "demo-banner" not in owner.get("/").text
