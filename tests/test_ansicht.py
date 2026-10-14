"""Einfache Ansicht: Umschalten, Standard der Instanz, Vorgabe beim Anlegen/Einladen, vom Owner festgelegt."""
import re

from hatchtower import db

PW = "Sehr-Langes-Passwort-123"


def _view(username):
    with db.connect() as con:
        row = con.execute("SELECT view_mode, view_locked FROM users WHERE username = ?", (username,)).fetchone()
    return row["view_mode"], row["view_locked"]


def _post(client, url, **data):
    return client.post(url, data={"csrf": client.csrf, **data}, follow_redirects=False)


def test_umschalten_und_standard(world):
    user = world.client(world.ids["victim"])
    assert "is-simple" not in user.get("/").text
    db.set_setting("default_view", "simple", None)
    assert "is-simple" in user.get("/").text
    assert "is-simple" not in world.client(world.ids["olga"]).get("/").text   # Owner: Standard gilt nicht
    user.get("/view/full", follow_redirects=False)
    assert "is-simple" not in user.get("/").text and world.audit("view.update")


def test_anlegen_mit_festgelegter_ansicht(world):
    owner = world.client(world.ids["olga"])
    _post(owner, "/admin/users", username="kumpel", password=PW, role="user", no_email="1",
          view_form="1", view_mode="simple", view_locked="1")
    assert _view("kumpel") == ("simple", 1)


def test_festlegen_ohne_modus_nimmt_standard(world):
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    db.set_setting("default_view", "simple", None)
    _post(owner, f"/admin/users/{uid}/update", role="user", is_active="1", view_form="1", view_mode="", view_locked="1")
    with db.connect() as con:
        name = con.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()["username"]
    assert _view(name) == ("simple", 1)


def test_festgelegt_kein_umschalten(world):
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    _post(owner, f"/admin/users/{uid}/update", role="user", is_active="1", view_form="1", view_mode="simple", view_locked="1")
    user = world.client(uid)
    page = user.get("/").text
    assert "is-simple" in page and 'class="view-switch"' not in page
    user.get("/view/full", follow_redirects=False)
    assert "is-simple" in user.get("/").text
    assert "festgelegt" in user.get("/account").text
    _post(owner, f"/admin/users/{uid}/update", role="user", is_active="1", view_form="1", view_mode="full", view_locked="")
    user.get("/view/simple", follow_redirects=False)
    assert "is-simple" in user.get("/").text                                 # wieder frei umschaltbar


def test_einladung_uebernimmt_ansicht(world):
    owner = world.client(world.ids["olga"])
    r = owner.post("/admin/users/invite", data={"csrf": owner.csrf, "days": "3", "uses": "1",
                                                "view_mode": "simple", "view_locked": "1"})
    link = re.search(r'value="https?://[^"]*(/invite/[^"]+)"', r.text).group(1)
    assert "festgelegt" in owner.get("/admin/users/new").text
    c, prelogin = world.anonymous_with_prelogin()
    c.post(link, data={"username": "eingeladen", "password": PW, "repeat": PW, "prelogin": prelogin})
    assert _view("eingeladen") == ("simple", 1)
