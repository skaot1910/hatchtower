"""Server administration through the panel API (optional admin key with write access).

- Without the admin key every page is shown, but locked with a hint, and nothing reaches the panel.
- Owner-only parts (owner, egg, other images, suspend, delete) stay closed to users with every right.
- Deleting needs the exact server name; ports can only be taken from the free ports of the node.
- The admin key is stored only if the check finds no missing right, and never with an existing key.
"""
from urllib.parse import unquote

from conftest import FakePtero
from hatchtower import db, main
from hatchtower.ptero import PteroError

ALL_ADMIN = {"server.view", "admin.view", "admin.details", "admin.build", "admin.startup", "admin.reinstall"}


def post(world, uid, path, data):
    c = world.client(uid)
    return c.post(path, data={"csrf": c.csrf, **data})


def message(response) -> str:
    return unquote(response.headers.get("location", ""))


def test_ohne_admin_key_gesperrt_mit_hinweis(world, monkeypatch):
    monkeypatch.setattr(main.settings, "admin_key", "")
    owner = world.ids["olga"]
    page = world.client(owner).get("/servers/srv-a/admin")
    assert page.status_code == 200
    assert "Admin-Key hinterlegen" in page.text and "/admin/connection#admin-key" in page.text
    for path in ("/admin/servers/new", "/admin/ports"):
        assert "Admin-Key hinterlegen" in world.client(owner).get(path).text
    r = post(world, owner, "/servers/srv-a/admin/details", {"name": "Neu"})
    assert r.status_code == 303 and "Admin-Key" in message(r)
    assert world.ptero.calls == []


def test_benutzer_sieht_hinweis_ohne_owner_link(world, monkeypatch):
    monkeypatch.setattr(main.settings, "admin_key", "")
    uid = world.user("admin-light", grants={"srv-a": ALL_ADMIN})
    text = world.client(uid).get("/servers/srv-a/admin").text
    assert "Ein Owner kann ihn unter System → Panel hinterlegen." in text
    assert "/admin/connection#admin-key" not in text


def test_link_zu_panel_einstellungen_nur_mit_recht(world):
    assert "/servers/srv-a/admin" not in world.client(world.ids["victim"]).get("/servers/srv-a").text
    uid = world.user("schauer", grants={"srv-a": {"server.view", "admin.view"}})
    assert "/servers/srv-a/admin" in world.client(uid).get("/servers/srv-a").text


def test_nur_owner_aendern_besitzer_egg_und_eigenes_image(world):
    uid = world.user("admin-light", grants={"srv-a": ALL_ADMIN})
    r = post(world, uid, "/servers/srv-a/admin/details", {"name": "Neu", "owner": "99", "external_id": "x"})
    assert r.status_code == 303
    body = [w for w in world.ptero.writes if w[1].endswith("/details")][0][3]
    assert body["user"] == 1 and body["external_id"] is None
    world.ptero.calls.clear()
    r = post(world, uid, "/servers/srv-a/admin/startup",
             {"egg": "99", "startup": "./start.sh", "image": "img:1", "image_custom": "evil/image:1", "var__GAME": "x"})
    body = [w for w in world.ptero.writes if w[1].endswith("/startup")][0][3]
    assert body["egg"] == 5 and body["image"] == "img:1"
    world.ptero.calls.clear()
    r = post(world, uid, "/servers/srv-a/admin/startup", {"startup": "./start.sh", "image": "evil/image:1", "var__GAME": "x"})
    assert "Docker-Image aus der Liste" in message(r) and world.ptero.writes == []


def test_pflichtvariable_darf_nicht_leer_sein(world):
    r = post(world, world.ids["olga"], "/servers/srv-a/admin/startup",
             {"startup": "./start.sh", "image": "img:1", "var__GAME": "  "})
    assert "darf nicht leer sein" in message(r) and world.ptero.writes == []


def test_ports_nur_freie_und_hauptport_bleibt(world):
    owner = world.ids["olga"]
    r = post(world, owner, "/servers/srv-a/admin/ports", {"primary": "11", "add": ["12", "999"], "remove": "11"})
    assert r.status_code == 303
    body = world.ptero.writes[0][3]
    assert body["add_allocations"] == [12] and body["remove_allocations"] == [] and body["allocation"] == 11
    world.ptero.calls.clear()
    r = post(world, owner, "/servers/srv-a/admin/ports", {"primary": "12"})
    assert "Hauptport" in message(r) and world.ptero.writes == []


def test_limits_werden_geprueft(world):
    r = post(world, world.ids["olga"], "/servers/srv-a/admin/build", {"memory": "-5", "disk": "10", "cpu": "10"})
    assert "RAM" in message(r) and world.ptero.writes == []
    r = post(world, world.ids["olga"], "/servers/srv-a/admin/build", {"memory": "1024", "io": "5"})
    assert "Block-IO" in message(r) and world.ptero.writes == []


def test_loeschen_nur_mit_genauem_namen(world):
    owner = world.ids["olga"]
    r = post(world, owner, "/servers/srv-a/admin/delete", {"confirm_name": "server srv-a"})
    assert "Namen des Servers" in message(r) and world.ptero.writes == []
    r = post(world, owner, "/servers/srv-a/admin/delete", {"confirm_name": "Server srv-a", "force": "1"})
    assert r.headers["location"].startswith("/?ok=")
    assert world.ptero.writes[0][:2] == ("DELETE", "/api/application/servers/1/force")
    with db.connect() as con:
        row = con.execute("SELECT result, detail FROM audit_log WHERE action = 'server.admin.delete'").fetchone()
    assert row["result"] == "ok" and "Server srv-a" in row["detail"]


def test_read_only_sperrt_owner_aktionen(world):
    world.read_only()
    owner = world.ids["olga"]
    for path, data in (("/servers/srv-a/admin/suspend", {"suspend": "1"}),
                       ("/servers/srv-a/admin/delete", {"confirm_name": "Server srv-a"}),
                       ("/admin/ports", {"node": "1", "ip": "10.0.0.1", "ports": "30000"}),
                       ("/admin/servers/new", {"node": "1", "egg": "5", "name": "X"})):
        assert post(world, owner, path, data).status_code == 403, path
    assert world.ptero.writes == []
    page = world.client(owner).get("/servers/srv-a/admin")
    assert page.status_code == 200 and "Modus „Nur lesen“" in page.text


def test_server_anlegen(world):
    r = post(world, world.ids["olga"], "/admin/servers/new",
             {"node": "1", "egg": "5", "name": "Neue Welt", "owner": "1", "allocation": "12", "image": "img:1",
              "startup": "./start.sh", "var__GAME": "demo", "memory": "2048", "disk": "4096", "cpu": "150",
              "io": "500", "start": "1"})
    assert "angelegt" in message(r)
    method, path, _, body = world.ptero.writes[0]
    assert (method, path) == ("POST", "/api/application/servers")
    assert body["allocation"] == {"default": 12, "additional": []} and body["environment"] == {"GAME": "demo"}
    assert body["limits"]["memory"] == 2048 and body["start_on_completion"] is True
    with db.connect() as con:
        assert con.execute("SELECT server FROM audit_log WHERE action = 'server.admin.create'").fetchone()[0] == "srv-new"


def test_server_anlegen_nur_mit_freiem_port(world):
    r = post(world, world.ids["olga"], "/admin/servers/new",
             {"node": "1", "egg": "5", "name": "X", "owner": "1", "allocation": "11", "image": "img:1",
              "var__GAME": "demo"})
    assert "freien Port" in message(r) and world.ptero.writes == []


def test_ports_anlegen_prueft_eingaben(world):
    owner = world.ids["olga"]
    for data, text in (({"ip": "kein-ip", "ports": "30000"}, "IP-Adresse"),
                       ({"ip": "10.0.0.1", "ports": "80"}, "1024"),
                       ({"ip": "10.0.0.1", "ports": "30000-32000"}, "Höchstens")):
        assert text in message(post(world, owner, "/admin/ports", {"node": "1", **data}))
    assert world.ptero.writes == []
    post(world, owner, "/admin/ports", {"node": "1", "ip": "10.0.0.1", "ports": "30000, 30005-30006"})
    assert world.ptero.writes[0][3] == {"ip": "10.0.0.1", "alias": None, "ports": ["30000", "30005-30006"]}


def test_belegte_ports_lassen_sich_nicht_loeschen(world):
    owner = world.ids["olga"]
    r = post(world, owner, "/admin/ports/delete", {"node": "1", "alloc": "11"})
    assert "freie Ports" in message(r) and world.ptero.writes == []
    post(world, owner, "/admin/ports/delete", {"node": "1", "alloc": "12"})
    assert world.ptero.writes == [("DELETE", "/api/application/nodes/1/allocations/12", None, None)]


def test_admin_key_wird_nur_mit_allen_rechten_gespeichert(world, monkeypatch):
    owner = world.ids["olga"]
    monkeypatch.setattr(main.settings, "admin_key", "")
    key = "ptla_" + "c" * 43
    r = post(world, owner, "/admin/connection/admin-key", {"admin_key": main.settings.app_key})
    assert "bereits gespeicherten Keys" in message(r)

    # the check runs with its own client for the new key
    monkeypatch.setattr(main, "make_client", lambda settings: FakePtero(settings))

    async def denied(*a, **k):
        raise PteroError("verweigert", 403)
    original = FakePtero._admin
    monkeypatch.setattr(FakePtero, "_admin", lambda self, method, path, body=None, params=None:
                        denied() if method != "GET" else original(self, method, path, body, params))
    r = post(world, owner, "/admin/connection/admin-key", {"admin_key": key})
    assert "Nicht gespeichert" in message(r) and main.settings.admin_key == ""
    assert db.get_setting("conn_admin_key") == ""

    monkeypatch.setattr(FakePtero, "_admin", original)
    r = post(world, owner, "/admin/connection/admin-key", {"admin_key": key})
    assert "gespeichert" in message(r) and main.settings.admin_key == key
    stored = db.get_setting("conn_admin_key")
    assert stored and key not in stored and main.vault.decrypt(stored) == key
    page = world.client(owner).get("/admin/connection")
    assert key not in page.text and "ptla_…cccc" in page.text

    post(world, owner, "/admin/connection/admin-key/remove", {})
    assert main.settings.admin_key == "" and db.get_setting("conn_admin_key") == ""
