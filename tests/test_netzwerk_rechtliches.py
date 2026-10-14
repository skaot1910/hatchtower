"""Einrichtung: optionale Vorlagen für Impressum/Datenschutz. Netzwerk: Prüfung, wohin die Domain zeigt."""
import asyncio

from hatchtower import db, legal, setup


def test_vorlagen_im_assistenten_bleiben_aus(world):
    db.set_setting("legal_impressum_text", "", None)
    db.set_setting("legal_impressum_enabled", "0", None)
    owner = world.client(world.ids["olga"])
    r = owner.post("/setup/legal", data={"csrf": owner.csrf, "page__impressum": "1", "sprache": "de"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/setup?step=done")
    assert legal.text("impressum") == legal.template("impressum", "de")
    assert not legal.enabled("impressum")
    assert legal.text("datenschutz") == ""


def test_vorhandener_text_wird_nicht_ueberschrieben(world):
    db.set_setting("legal_impressum_text", "Mein Impressum", None)
    owner = world.client(world.ids["olga"])
    owner.post("/setup/legal", data={"csrf": owner.csrf, "page__impressum": "1"}, follow_redirects=False)
    assert legal.text("impressum") == "Mein Impressum"


def test_nichts_gewaehlt_schliesst_ab(world):
    owner = world.client(world.ids["olga"])
    r = owner.post("/setup/legal", data={"csrf": owner.csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/setup?step=done")


def test_dns_pruefung(monkeypatch):
    monkeypatch.setattr(setup, "own_addresses", lambda: ["9.9.9.9"])
    hit = asyncio.run(setup.dns_check("9.9.9.9"))
    assert hit["match"] and not hit["error"]
    other = asyncio.run(setup.dns_check("1.1.1.1"))
    assert not other["match"] and not other["behind_nat"]
    monkeypatch.setattr(setup, "own_addresses", lambda: ["192.168.1.20"])
    nat = asyncio.run(setup.dns_check("1.1.1.1"))
    assert nat["behind_nat"] and not nat["match"]
    assert asyncio.run(setup.dns_check("192.168.1.50"))["private"]
    assert asyncio.run(setup.dns_check("gibt-es-nicht.invalid"))["error"]


def test_netzwerkseite_mit_pruefung(world, monkeypatch):
    async def fake(host):
        return {"host": host, "ips": ["9.9.9.9"], "own": ["9.9.9.9"], "error": None, "match": True,
                "behind_nat": False, "private": False}
    monkeypatch.setattr(setup, "dns_check", fake)
    page = world.client(world.ids["olga"]).get("/admin/network", params={"pruefen": "1"}).text
    assert "9.9.9.9" in page
