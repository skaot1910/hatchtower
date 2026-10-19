"""Einladungen mit Serverzugriff, Vorschlagsgruppen und verständliche Fehler beim Steuern."""
from __future__ import annotations

import re

from hatchtower import db, main, permissions
from hatchtower.ptero import PteroError

PASSWORD = "Sehr-Langes-Passwort-123"


def _groups() -> dict[str, int]:
    return {g["name"]: g["id"] for g in permissions.load_groups()}


def _accept(world, link: str, username: str) -> int:
    c, prelogin = world.anonymous_with_prelogin()
    r = c.post(link, data={"username": username, "password": PASSWORD, "repeat": PASSWORD, "prelogin": prelogin})
    assert r.headers["location"] == "/"
    with db.connect() as con:
        return con.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]


def _invite(owner, **form) -> str:
    r = owner.post("/admin/users/invite", data={"csrf": owner.csrf, "days": "3", "uses": "1", **form})
    assert r.status_code == 200
    return re.search(r'value="https?://[^"]*(/invite/[^"]+)"', r.text).group(1)


def test_vorschlagsgruppen_einmal_anlegen(world):
    owner = world.client(world.ids["olga"])
    assert owner.post("/admin/groups/starter", data={"csrf": owner.csrf}).status_code == 303
    groups = {g["name"]: set(g["keys"]) for g in permissions.load_groups()}
    assert {"Zuschauer", "Spieler", "Moderator"} <= set(groups)
    assert "power.restart" in groups["Spieler"] and "power.stop" not in groups["Spieler"]
    assert not any(permissions.BY_KEY[k].write for k in groups["Zuschauer"])
    before = len(groups)
    owner.post("/admin/groups/starter", data={"csrf": owner.csrf})
    assert len(permissions.load_groups()) == before
    assert len(world.audit("group.save")) == 1


def test_einladung_gibt_gruppe_fuer_gewaehlte_server(world):
    owner = world.client(world.ids["olga"])
    owner.post("/admin/groups/starter", data={"csrf": owner.csrf})
    link = _invite(owner, access_group=str(_groups()["Spieler"]), access_server=["srv-a", "gibts-nicht"])
    uid = _accept(world, link, "kumpel")
    grants = permissions.load_grants(uid)
    assert {"server.view", "power.start", "power.restart"} <= grants["srv-a"]
    assert "power.stop" not in grants["srv-a"] and "srv-b" not in grants
    assert '"zugriff"' in world.audit("invite.accept")[-1]["detail"]


def test_einladung_fuer_alle_server(world):
    owner = world.client(world.ids["olga"])
    owner.post("/admin/groups/starter", data={"csrf": owner.csrf})
    link = _invite(owner, access_group=str(_groups()["Zuschauer"]), access_all="1")
    uid = _accept(world, link, "zuschauer1")
    assert permissions.load_default_group(uid) == _groups()["Zuschauer"]
    assert "srv-b" in permissions.load_grants(uid)


def test_einladung_ohne_gruppe_oder_als_owner_gibt_keine_serverrechte(world):
    owner = world.client(world.ids["olga"])
    owner.post("/admin/groups/starter", data={"csrf": owner.csrf})
    gid = str(_groups()["Moderator"])
    uid = _accept(world, _invite(owner, access_server="srv-a"), "ohnegruppe")
    assert permissions.load_grants(uid) == {}
    uid = _accept(world, _invite(owner, role="owner", access_group=gid, access_server="srv-a"), "chefin")
    assert permissions.load_assignments(uid) == {}


def test_geloeschte_gruppe_gibt_keinen_zugriff(world):
    owner = world.client(world.ids["olga"])
    owner.post("/admin/groups/starter", data={"csrf": owner.csrf})
    gid = _groups()["Spieler"]
    link = _invite(owner, access_group=str(gid), access_all="1")
    owner.post(f"/admin/groups/{gid}/delete", data={"csrf": owner.csrf})
    uid = _accept(world, link, "spaet")
    assert permissions.load_grants(uid) == {} and permissions.load_default_group(uid) is None


def test_steuerfehler_ohne_technik_fuer_benutzer():
    busy = PteroError("Pterodactyl antwortet mit HTTP 409: conflict", 409)
    assert "HTTP" not in main.power_error_text(busy, technical=False)
    assert "HTTP 409" in main.power_error_text(busy, technical=True)
    down = main.power_error_text(PteroError("Wings nicht erreichbar: ConnectError"), technical=False)
    assert "ConnectError" not in down and "Owner" in down


def test_registrierung_meldet_sich_beim_owner(world):
    owner = world.client(world.ids["olga"])
    owner.post("/admin/groups/starter", data={"csrf": owner.csrf})
    uid = _accept(world, _invite(owner, access_group=str(_groups()["Spieler"]), access_server=["srv-a"]), "neuling")
    page = owner.get("/").text
    assert "Neuer Benutzer: neuling" in page and "Rechte für 1 Server" in page
    assert f'href="/admin/users/{uid}"' in page
    with db.connect() as con:
        bell = con.execute("SELECT detail FROM inbox WHERE event = 'new_user'").fetchall()
    assert len(bell) == 1 and "neuling" in bell[0]["detail"]
    assert "Neuer Benutzer" not in world.client(uid).get("/").text          # only owners see it
    owner.post(f"/admin/users/{uid}/reviewed", data={"csrf": owner.csrf})
    assert "Neuer Benutzer: neuling" not in owner.get("/").text
    assert world.audit("user.reviewed")


def test_hinweise_lassen_sich_abschalten(world):
    owner = world.client(world.ids["olga"])
    assert owner.post("/admin/notify/owner", data={"csrf": owner.csrf, "notice__new_server": "1"}).status_code == 303
    assert db.get_setting("owner_notice_new_user") == "0" and db.get_setting("owner_notice_new_server") == "1"
    _accept(world, _invite(owner), "still")
    assert "Neuer Benutzer: still" not in owner.get("/").text
    with db.connect() as con:
        assert not con.execute("SELECT 1 FROM inbox WHERE event = 'new_user'").fetchone()
    owner.post("/admin/notify/owner", data={"csrf": owner.csrf, "notice__new_user": "1"})
    assert "Neuer Benutzer: still" in owner.get("/").text                    # pending, shown again when on
