"""Prüft die Rechte-Matrix (tests/routes.py) gegen die laufende Anwendung.

Für jede Route wird nachgewiesen:
- Ohne Anmeldung kommt niemand an geschützte Routen.
- Ohne das passende Recht wird verweigert, protokolliert, und es ändert sich nichts:
  weder in der Datenbank noch in Pterodactyl.
- Mit genau dem passenden Recht (und seinen Voraussetzungen) funktioniert die Route.
- Ein Recht auf Server A gilt nie für Server B.
- Im Read-Only-Modus erreicht kein Schreibzugriff Pterodactyl, auch nicht vom Owner.
- Ohne CSRF-Token wird keine zustandsändernde Anfrage ausgeführt.
"""
from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from conftest import PNG, World
from hatchtower import db, main, security
from hatchtower.permissions import ALL_KEYS, BY_KEY, PERMISSIONS, with_dependencies
from routes import DOC, ROUTES, Route, render_markdown

SERVER = [r for r in ROUTES if r.access in ("server", "kill")]
SERVER_PERM = [r for r in SERVER if r.access == "server"]
OWNER = [r for r in ROUTES if r.access == "owner"]
PROTECTED = [r for r in ROUTES if r.access in ("login", "owner", "server", "kill", "automation")]
AUTOMATION = [r for r in ROUTES if r.access == "automation"]
CSRF = [r for r in ROUTES if r.csrf in ("form", "header")]
PRELOGIN = [r for r in ROUTES if r.csrf == "prelogin"]
WRITE = [r for r in SERVER if r.access == "kill" or BY_KEY[r.perm].write]
ALL = set(ALL_KEYS)
UPLOADS = {"files": ("notiz.txt", b"hallo", "text/plain"), "logo": ("logo.png", PNG, "image/png"),
           "file": ("export.htexport", b"kein export", "application/octet-stream")}


def ids(routes):
    return [r.id for r in routes]


def dependents(key: str) -> set[str]:
    """Alle Rechte, die `key` voraussetzen (einschließlich `key`)."""
    return {p.key for p in PERMISSIONS if key in with_dependencies({p.key})}


def url(world: World, route: Route, ident: str | None = None) -> str:
    def fill(match):
        name = match.group(1)
        if name == "ident" and ident is not None:
            return ident
        value = route.args.get(name, name)
        return world.args[value]
    return re.sub(r"{(\w+)}", fill, route.path)


def send(world: World, client, route: Route, *, ident: str | None = None, csrf: str | None = "auto",
         prelogin: str | None = None):
    """Schickt die Anfrage so, wie sie die Oberfläche schicken würde (mit gültigem CSRF-Token)."""
    token = client.csrf if csrf == "auto" else csrf
    path = url(world, route, ident)
    if route.method == "GET":
        return client.get(path, params=route.query)
    headers, data = {}, dict(route.form)
    if route.csrf == "form" and token:
        data["csrf"] = token
    if route.csrf == "header" and token:
        headers["X-CSRF-Token"] = token
    if route.csrf == "prelogin" and prelogin:
        data["prelogin"] = prelogin
    if route.json is not None:
        return client.post(path, params=route.query, json=route.json, headers=headers)
    files = {route.upload: UPLOADS[route.upload]} if route.upload else None
    return client.post(path, params=route.query, data=data, files=files, headers=headers)


def assert_denied(world: World, response, before: dict, uid: int | None = None, what: str | None = None):
    assert response.status_code == 403, f"erwartet 403, bekommen {response.status_code}: {response.text[:200]}"
    assert world.snapshot() == before, "eine verweigerte Anfrage hat Daten verändert"
    assert world.ptero.writes == [], f"eine verweigerte Anfrage hat Pterodactyl erreicht: {world.ptero.writes}"
    if uid is not None:
        denied = [a for a in world.audit("access.denied") if a["user_id"] == uid]
        assert denied, "verweigerter Zugriff fehlt im Audit-Log"
        if what is not None:
            assert denied[-1]["detail"] == what


def assert_ok(response):
    assert response.status_code in (200, 303), f"{response.status_code}: {response.text[:300]}"
    location = response.headers.get("location", "")
    assert "/login" not in location
    assert "Fehlgeschlagen" not in location and "nicht%20lesbar" not in location, location


# == Der Katalog selbst ==========================================================================

def test_katalog_enthaelt_jede_route():
    actual = {(m, r.path) for r in main.app.routes if isinstance(r, APIRoute) for m in r.methods}
    listed = {(r.method, r.path) for r in ROUTES}
    missing = sorted(actual - listed)
    assert not missing, ("Diese Routen stehen nicht in tests/routes.py – bitte mit ihrem Zugriffsrecht "
                         f"eintragen: {missing}")
    assert not sorted(listed - actual), f"Einträge ohne Route: {sorted(listed - actual)}"


def test_katalog_nennt_nur_bekannte_rechte():
    for r in ROUTES:
        assert (r.perm in BY_KEY) == (r.access in ("server", "automation")), r.id
        assert r.access in ("public", "token", "setup", "login", "owner", "server", "kill", "automation"), r.id


def test_jedes_recht_wird_von_einer_route_genutzt():
    used = {r.perm for r in ROUTES if r.perm}
    unused = ALL - used - {"server.password"}   # wirkt auf Inhalte, nicht auf eine Route
    assert not unused, f"Rechte ohne Route: {unused}"


def test_dokumentation_ist_aktuell():
    assert DOC.is_file() and DOC.read_text(encoding="utf-8") == render_markdown(), \
        "docs/rechte-matrix.md ist veraltet – neu erzeugen mit: python tests/routes.py"


# == Anmeldung ====================================================================================

@pytest.mark.parametrize("route", PROTECTED, ids=ids(PROTECTED))
def test_ohne_anmeldung_kein_zugriff(world, route):
    before = world.snapshot()
    response = send(world, world.client(), route)
    if route.path.startswith("/api/"):
        assert response.status_code == 401
    else:
        assert response.status_code == 303 and response.headers["location"] == "/login"
    assert world.snapshot() == before
    assert world.ptero.calls == []


def test_abgelaufene_sitzung_gilt_nicht(world):
    c = world.client(world.ids["olga"])
    with db.connect() as con:
        con.execute("UPDATE sessions SET last_seen = '2000-01-01T00:00:00+00:00'")
    assert c.get("/admin/users").headers["location"] == "/login"


def test_deaktivierter_benutzer_verliert_sitzung_sofort(world):
    uid = world.user("full", grants={"srv-a": ALL})
    c = world.client(uid)
    assert c.get("/servers/srv-a").status_code == 200
    with db.connect() as con:
        con.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
    assert c.get("/servers/srv-a").headers["location"] == "/login"


def test_entzogene_owner_rolle_wirkt_sofort(world):
    uid = world.user("ex-owner", "owner")
    c = world.client(uid)
    assert c.get("/admin/users").status_code == 200
    with db.connect() as con:
        con.execute("UPDATE users SET role = 'user' WHERE id = ?", (uid,))
    assert c.get("/admin/users").status_code == 403


def test_entzogenes_recht_wirkt_sofort(world):
    uid = world.user("full", grants={"srv-a": ALL})
    c = world.client(uid)
    assert c.get("/servers/srv-a/files").status_code == 200
    world.grant(uid, {"srv-a": {"server.view"}})
    assert c.get("/servers/srv-a/files").status_code == 403


def test_passwortwechsel_beendet_alle_sitzungen(world):
    uid = world.user("wechsler")
    a, b = world.client(uid), world.client(uid)
    r = a.post("/account/password", data={"csrf": a.csrf, "current": "Sehr-Langes-Passwort-123",
                                          "new": "Neues-Langes-Passwort-1", "repeat": "Neues-Langes-Passwort-1"})
    assert r.headers["location"] == "/login"
    assert b.get("/account").headers["location"] == "/login"


def test_sitzungscookie_ist_geschuetzt(world):
    c, token = world.anonymous_with_prelogin()
    r = c.post("/login", data={"username": "olga", "password": "Sehr-Langes-Passwort-123", "prelogin": token})
    assert r.status_code == 303
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


# == Owner-Bereich ================================================================================

@pytest.mark.parametrize("route", OWNER, ids=ids(OWNER))
def test_owner_routen_verweigern_benutzern_mit_allen_rechten(world, route):
    uid = world.user("full", grants={ident: ALL for ident in ("srv-a", "srv-b")})
    before = world.snapshot()
    response = send(world, world.client(uid), route)
    assert_denied(world, response, before, uid)


@pytest.mark.parametrize("route", [r for r in OWNER if r.method == "GET"],
                         ids=ids([r for r in OWNER if r.method == "GET"]))
def test_owner_seiten_funktionieren_fuer_owner(world, route):
    response = send(world, world.client(world.ids["olga"]), route)
    assert_ok(response)


def test_vorschau_als_anderer_benutzer_nur_fuer_owner(world):
    uid = world.user("neugierig", grants={"srv-a": {"server.view"}})
    before = world.snapshot()
    assert_denied(world, world.client(uid).get("/", params={"als": world.ids["olga"]}), before, uid, "preview")
    r = world.client(world.ids["olga"]).get("/", params={"als": world.ids["victim"]})
    assert r.status_code == 200 and "Server srv-a" in r.text and "Server srv-b" not in r.text


# == Server-Rechte =================================================================================

@pytest.mark.parametrize("route", SERVER, ids=ids(SERVER))
def test_server_route_ohne_recht_verweigert(world, route):
    if route.access == "kill":
        grants = {"srv-a": ALL}
        what = "power.kill"
    elif route.perm == "server.view":
        grants = {"srv-b": ALL}
        what = "server.view"
    else:
        grants = {"srv-a": ALL - dependents(route.perm)}
        what = route.perm
    uid = world.user("fast-alles", grants=grants)
    before = world.snapshot()
    response = send(world, world.client(uid), route)
    assert_denied(world, response, before, uid, what)
    denied = world.audit("access.denied")[-1]
    assert denied["server"] == "srv-a"


@pytest.mark.parametrize("route", SERVER_PERM, ids=ids(SERVER_PERM))
def test_server_route_mit_genau_diesem_recht_erlaubt(world, route):
    uid = world.user("knapp", grants={"srv-a": with_dependencies({route.perm})})
    response = send(world, world.client(uid), route)
    assert_ok(response)
    if route.write:
        assert world.ptero.writes, "Schreibzugriff hat Pterodactyl nicht erreicht"
    else:
        assert world.ptero.writes == []


def test_kill_fuer_owner_erlaubt(world):
    route = next(r for r in SERVER if r.access == "kill")
    response = send(world, world.client(world.ids["olga"]), route)
    assert_ok(response)
    assert world.ptero.writes[0][1].endswith("/power") and world.ptero.writes[0][3] == {"signal": "kill"}


@pytest.mark.parametrize("ident", ["srv-b", "srv-gone", "gibt-es-nicht"])
@pytest.mark.parametrize("route", SERVER, ids=ids(SERVER))
def test_recht_gilt_nur_fuer_seinen_server(world, route, ident):
    # Alle Rechte auf srv-a und srv-gone, keine auf srv-b. srv-gone ist in Pterodactyl gelöscht.
    uid = world.user("nachbar", grants={"srv-a": ALL, "srv-gone": ALL})
    before = world.snapshot()
    response = send(world, world.client(uid), route, ident=ident)
    assert_denied(world, response, before, uid)


@pytest.mark.parametrize("ident", ["srv-gone", "gibt-es-nicht"])
def test_owner_kommt_nicht_an_unbekannte_server(world, ident):
    c = world.client(world.ids["olga"])
    assert c.get(f"/servers/{ident}").status_code == 403
    assert c.post(f"/api/servers/{ident}/power", json={"signal": "kill"},
                  headers={"X-CSRF-Token": c.csrf}).status_code == 403
    assert world.ptero.writes == []


# == Read-Only-Modus =============================================================================

@pytest.mark.parametrize("who", ["owner", "user"])
@pytest.mark.parametrize("route", WRITE, ids=ids(WRITE))
def test_read_only_sperrt_jeden_schreibzugriff(world, route, who):
    world.read_only()
    uid = world.ids["olga"] if who == "owner" else world.user("full", grants={"srv-a": ALL})
    before = world.snapshot()
    response = send(world, world.client(uid), route)
    assert_denied(world, response, before)


@pytest.mark.parametrize("route", [r for r in SERVER_PERM if not BY_KEY[r.perm].write],
                         ids=ids([r for r in SERVER_PERM if not BY_KEY[r.perm].write]))
def test_read_only_laesst_lesen_zu(world, route):
    world.read_only()
    response = send(world, world.client(world.ids["olga"]), route)
    assert_ok(response)
    assert world.ptero.writes == []


def test_read_only_gilt_auch_fuer_websocket_events():
    from hatchtower import wings
    assert wings.READ_ONLY_EVENTS == {"auth", "send logs", "send stats"}


# == CSRF ===========================================================================================

@pytest.mark.parametrize("token", [None, "falsch"])
@pytest.mark.parametrize("route", CSRF, ids=ids(CSRF))
def test_ohne_csrf_token_passiert_nichts(world, route, token):
    c = world.client(world.ids["olga"])
    before = world.snapshot()
    response = send(world, c, route, csrf=token)
    assert response.status_code == 403
    assert world.snapshot() == before
    assert world.ptero.writes == []
    assert c.get("/account").status_code == 200, "Sitzung darf durch die Anfrage nicht enden"


@pytest.mark.parametrize("route", PRELOGIN, ids=ids(PRELOGIN))
def test_anmeldeformulare_brauchen_vorab_token(world, route):
    before = world.snapshot()
    response = send(world, world.client(), route)
    assert world.snapshot() == before
    assert security.SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_login_mit_vorab_token_funktioniert(world):
    c, token = world.anonymous_with_prelogin()
    r = c.post("/login", data={"username": "olga", "password": "Sehr-Langes-Passwort-123", "prelogin": token})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert security.SESSION_COOKIE in r.headers["set-cookie"]


def test_einrichtung_ist_nach_dem_ersten_konto_gesperrt(world):
    c, token = world.anonymous_with_prelogin()
    from hatchtower import setup
    code = setup.new_setup_token()
    before = world.snapshot()
    r = c.post("/setup/owner", data={"token": code, "username": "chef", "password": "Sehr-Langes-Passwort-123",
                                     "repeat": "Sehr-Langes-Passwort-123", "prelogin": token})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert world.snapshot() == before


# == Einmal-Links ===================================================================================

@pytest.mark.parametrize("route", [r for r in ROUTES if r.access == "token"],
                         ids=ids([r for r in ROUTES if r.access == "token"]))
def test_einmal_link_mit_falschem_token_wirkungslos(world, route):
    c, prelogin = world.anonymous_with_prelogin()
    path = route.path.replace("{token}", "falsch")
    before = world.snapshot()
    if route.method == "GET":
        r = c.get(path)
        assert r.status_code == 410
    else:
        r = c.post(path, data={**route.form, "prelogin": prelogin})
        assert r.status_code == 303 and r.headers["location"] == path
    assert world.snapshot() == before


def test_einladung_gilt_nur_einmal(world):
    c, prelogin = world.anonymous_with_prelogin()
    path = f"/invite/{world.args['@invite']}"
    data = {"username": "neuling", "password": "Sehr-Langes-Passwort-123",
            "repeat": "Sehr-Langes-Passwort-123", "prelogin": prelogin}
    assert c.post(path, data=data).headers["location"] == "/"
    with db.connect() as con:
        assert con.execute("SELECT role FROM users WHERE username = 'neuling'").fetchone()["role"] == "user"
    c2, prelogin2 = world.anonymous_with_prelogin()
    r = c2.post(path, data={**data, "username": "zweiter", "prelogin": prelogin2})
    assert r.headers["location"] == path
    with db.connect() as con:
        assert con.execute("SELECT 1 FROM users WHERE username = 'zweiter'").fetchone() is None


def test_passwort_link_gilt_nur_fuer_sein_konto_und_einmal(world):
    c, prelogin = world.anonymous_with_prelogin()
    path = f"/reset/{world.args['@reset']}"
    new = "Neues-Langes-Passwort-1"
    assert c.post(path, data={"password": new, "repeat": new, "prelogin": prelogin}).headers["location"].startswith("/login")
    with db.connect() as con:
        rows = {r["username"]: r["password_hash"] for r in con.execute("SELECT username, password_hash FROM users")}
    assert security.verify_password(rows["victim"], new)
    assert security.verify_password(rows["olga"], "Sehr-Langes-Passwort-123")
    assert c.get(path).status_code == 410


# == Was Benutzer zu sehen bekommen ==============================================================

def test_uebersicht_zeigt_nur_sichtbare_server(world):
    uid = world.user("einer", grants={"srv-a": {"server.view"}})
    data = world.client(uid).get("/api/overview").json()
    assert [s["identifier"] for s in data["servers"]] == ["srv-a"]
    page = world.client(uid).get("/").text
    assert "Server srv-a" in page and "Server srv-b" not in page


def test_passwort_nur_mit_recht_passwort_sehen(world):
    without = world.user("ohne", grants={"srv-a": ALL - {"server.password"}})
    with_pw = world.user("mit", grants={"srv-a": {"server.password"}})
    for uid, visible in ((without, False), (with_pw, True)):
        c = world.client(uid)
        overview = c.get("/api/overview").json()["servers"][0]
        assert (overview["password"] == "geheimes-beitrittspasswort") is visible
        assert ("geheimes-beitrittspasswort" in c.get("/servers/srv-a").text) is visible
        assert ("geheimes-beitrittspasswort" in c.get("/").text) is visible


def test_servereinstellungen_zeigen_passwort_nur_mit_recht(world):
    uid = world.user("verwalter", grants={"srv-a": {"server.manage"}})
    page = world.client(uid).get("/servers/srv-a/settings")
    assert page.status_code == 200
    assert "geheimes-beitrittspasswort" not in page.text


def test_servereinstellungen_speichern_ohne_passwortrecht_behaelt_passwort(world):
    uid = world.user("verwalter", grants={"srv-a": {"server.manage"}})
    c = world.client(uid)
    r = c.post("/servers/srv-a/settings", data={"csrf": c.csrf, "ingame_name": "Neu", "password": ""})
    assert r.status_code == 303
    with db.connect() as con:
        enc = con.execute("SELECT password_enc FROM server_meta WHERE identifier = 'srv-a'").fetchone()[0]
    assert main.vault.decrypt(enc) == "geheimes-beitrittspasswort"


def test_servereinstellungen_passwort_entfernen_bleibt_moeglich(world):
    uid = world.user("verwalter", grants={"srv-a": {"server.manage"}})
    c = world.client(uid)
    c.post("/servers/srv-a/settings", data={"csrf": c.csrf, "password": "", "password_clear": "1"})
    with db.connect() as con:
        assert con.execute("SELECT password_enc FROM server_meta WHERE identifier = 'srv-a'").fetchone()[0] is None


def test_owner_sieht_und_leert_passwort_wie_bisher(world):
    c = world.client(world.ids["olga"])
    assert "geheimes-beitrittspasswort" in c.get("/servers/srv-a/settings").text
    c.post("/servers/srv-a/settings", data={"csrf": c.csrf, "password": ""})
    with db.connect() as con:
        assert con.execute("SELECT password_enc FROM server_meta WHERE identifier = 'srv-a'").fetchone()[0] is None


def test_statusseite_zeigt_nur_freigegebenes(world):
    c = world.client()
    data = c.get("/status.json").json()["servers"]
    assert [s["identifier"] for s in data] == ["srv-a"]
    assert "password" not in data[0]
    db.set_setting("status_fields", "name,password", None)
    assert c.get("/status.json").json()["servers"][0]["password"] == "geheimes-beitrittspasswort"
    assert c.get("/status/logo/srv-b").status_code == 404
    db.set_setting("status_enabled", "0", None)
    assert c.get("/status.json").status_code == 404


def test_datenauskunft_nur_eigene_daten(world):
    uid = world.user("ich", grants={"srv-a": {"server.view"}})
    world.client(world.ids["victim"]).get("/account/export")      # Audit-Eintrag eines anderen
    data = world.client(uid).get("/account/export").json()
    assert data["account"]["username"] == "ich"
    assert "password_hash" not in data["account"]
    assert data["permissions"] == {"srv-a": ["server.view"]}
    assert data["audit_log"] and all(e["action"] == "user.export" for e in data["audit_log"])
    assert len(data["audit_log"]) == 1


def test_favoriten_sind_pro_benutzer(world):
    a = world.user("a", grants={"srv-a": {"server.view"}})
    ca = world.client(a)
    assert ca.post("/api/servers/srv-a/favorite", headers={"X-CSRF-Token": ca.csrf}).json() == {"favorite": True}
    with db.connect() as con:
        assert [tuple(r) for r in con.execute("SELECT user_id, identifier FROM favorites")] == [(a, "srv-a")]


# == Konsole =========================================================================================

def test_konsole_endet_wenn_recht_entzogen_wird(world, monkeypatch):
    uid = world.user("zuhoerer", grants={"srv-a": {"console.read"}})

    async def console(client, identifier):
        yield {"type": "line", "text": "erste Zeile", "source": "console output"}
        world.grant(uid, {"srv-a": {"server.view"}})        # Owner entzieht das Recht
        yield {"type": "line", "text": "geheime Zeile", "source": "console output"}

    monkeypatch.setattr(main, "console_stream", console)
    monkeypatch.setattr(main, "CONSOLE_RECHECK_SECONDS", 0)
    body = world.client(uid).get("/servers/srv-a/console/stream").text
    assert "erste Zeile" in body
    assert "geheime Zeile" not in body


def test_konsole_endet_wenn_konto_deaktiviert_wird(world, monkeypatch):
    uid = world.user("zuhoerer", grants={"srv-a": {"console.read"}})

    async def console(client, identifier):
        yield {"type": "line", "text": "erste Zeile", "source": "console output"}
        with db.connect() as con:
            con.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
        yield {"type": "line", "text": "geheime Zeile", "source": "console output"}

    monkeypatch.setattr(main, "console_stream", console)
    monkeypatch.setattr(main, "CONSOLE_RECHECK_SECONDS", 0)
    body = world.client(uid).get("/servers/srv-a/console/stream").text
    assert "geheime Zeile" not in body


# == Reihenfolge der Prüfungen =======================================================================

def test_upload_prueft_rechte_bevor_die_dateien_angenommen_werden(world):
    uid = world.user("ohne-upload", grants={"srv-a": {"sftp.view"}})
    c = world.client(uid)
    r = c.post("/servers/srv-a/files/upload", params={"dir": "/"}, files={"files": UPLOADS["files"]})
    assert r.status_code == 403
    assert [a["detail"] for a in world.audit("access.denied")] == ["sftp.upload"]


# == Automationen ==================================================================================

def schedules(ident: str | None = None) -> list:
    with db.connect() as con:
        if ident:
            return con.execute("SELECT * FROM schedules WHERE identifier = ?", (ident,)).fetchall()
        return con.execute("SELECT * FROM schedules").fetchall()


@pytest.mark.parametrize("route", AUTOMATION, ids=ids(AUTOMATION))
def test_automationen_ohne_recht_verweigert(world, route):
    uid = world.user("fast-alles", grants={i: ALL - {"automation.manage"} for i in ("srv-a", "srv-b")})
    before = world.snapshot()
    assert_denied(world, send(world, world.client(uid), route), before, uid, "automation.manage")


@pytest.mark.parametrize("route", [r for r in AUTOMATION if r.method == "GET"],
                         ids=ids([r for r in AUTOMATION if r.method == "GET"]))
def test_automationen_fuer_owner(world, route):
    assert_ok(send(world, world.client(world.ids["olga"]), route))


def test_automationen_mit_recht_nur_fuer_eigene_server(world):
    uid = world.user("planer", grants={"srv-a": {"automation.manage", "power.restart"}, "srv-b": {"server.view"}})
    c = world.client(uid)
    page = c.get("/admin/automation")
    assert page.status_code == 200 and "Server srv-a" in page.text and "Server srv-b" not in page.text
    form = {"csrf": c.csrf, "name": "Nachts", "time": "04:00", "weekdays": "1", "repeat": "weekly"}
    assert_ok(c.post("/admin/automation/schedules", data={**form, "identifier": "srv-a", "action": "restart"}))
    assert len(schedules("srv-a")) == 2
    # fremder Server und Aktion ohne eigenes Steuerrecht: nichts angelegt
    c.post("/admin/automation/schedules", data={**form, "identifier": "srv-b", "action": "restart"})
    c.post("/admin/automation/schedules", data={**form, "identifier": "srv-a", "action": "stop"})
    c.post("/admin/automation/schedules", data={**form, "identifier": "srv-a", "action": "restart",
                                                 "warn_minutes": "5", "warn_command": "say bye"})
    assert len(schedules("srv-b")) == 0 and len(schedules("srv-a")) == 2
    assert world.ptero.writes == []


def test_automationen_absturz_neustart_laesst_fremde_server_unveraendert(world):
    with db.connect() as con:
        con.execute("INSERT INTO automation_settings (identifier, crash_restart, updated_at) VALUES ('srv-b', 1, ?)",
                    (db.utcnow(),))
    uid = world.user("planer", grants={"srv-a": {"automation.manage"}})
    c = world.client(uid)
    assert_ok(c.post("/admin/automation/crash", data={"csrf": c.csrf, "crash__srv-a": "1"}))
    with db.connect() as con:
        rows = {r["identifier"]: r["crash_restart"] for r in con.execute("SELECT * FROM automation_settings")}
    assert rows == {"srv-a": 1, "srv-b": 1}


def test_automationen_fremde_zeitplaene_nicht_aenderbar(world):
    # Der Zeitplan aus dem Testaufbau (srv-a, Restart) – ohne Restart-Recht darf der Planer ihn nicht anfassen
    uid = world.user("planer", grants={"srv-a": {"automation.manage", "power.start"}})
    before = world.snapshot()
    sid = world.args["sched_id"]
    c = world.client(uid)
    assert_denied(world, c.post(f"/admin/automation/schedules/{sid}/delete", data={"csrf": c.csrf}), before, uid)


def test_zeitplan_laeuft_nicht_mehr_wenn_dem_ersteller_das_recht_fehlt(world):
    from hatchtower import automation
    uid = world.user("planer", grants={"srv-a": {"automation.manage", "power.restart"}})
    c = world.client(uid)
    c.post("/admin/automation/schedules", data={"csrf": c.csrf, "name": "Nachts", "time": "04:00", "weekdays": "1",
                                                "repeat": "weekly", "identifier": "srv-a", "action": "restart"})
    sched = next(s for s in schedules("srv-a") if s["created_by"] == uid)
    assert automation.creator_allows(sched)
    world.grant(uid, {"srv-a": {"automation.manage"}})
    assert not automation.creator_allows(sched)
    # Zeitpläne eines gelöschten Benutzers verschwinden mit ihm
    o = world.client(world.ids["olga"])
    assert_ok(o.post(f"/admin/users/{uid}/delete", data={"csrf": o.csrf}))
    assert all(s["created_by"] != uid for s in schedules())


def test_passwort_mit_benutzernamen_klare_meldung():
    msg = security.password_message("planer-Sehr-Lang-123!", "planer")
    assert msg == "Das Passwort darf den Benutzernamen nicht enthalten."
    assert security.password_message("kurz", "x").startswith("Das Passwort braucht mindestens 15 Zeichen")
    assert security.password_message("Sehr-Langes-Passwort-123", "olga") is None
