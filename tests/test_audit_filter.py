"""Audit-Log: Filter nach Aktion (Stichwort), Benutzer (mit Vorschlägen) und IP-Adresse, untereinander kombinierbar."""
import re

from hatchtower import audit


def _fill():
    audit.record("login", user_id=1, username="olga", ip="192.168.1.10")
    audit.record("power.stop", user_id=1, username="olga", ip="192.168.1.10", server="srv-a", detail="olga-stop")
    audit.record("power.start", user_id=2, username="victim", ip="10.0.0.5", server="srv-a", detail="victim-start")
    audit.record("login", result="error", username="eindringling", ip="203.0.113.9", detail="falsches-passwort")
    audit.record("schedule.run", username="Automation", ip="intern", server="srv-a", detail="zeitplan-lauf")
    audit.record("access.denied", result="denied", ip="203.0.113.50", detail="ohne-konto")


def test_vorschlaege_im_benutzerfilter(world):
    _fill()
    page = world.client(world.ids["olga"]).get("/admin/audit").text
    assert '<datalist id="audit-users">' in page
    for name in ("olga", "victim", "Automation", "eindringling", audit.NO_USER):
        assert f'<option value="{name}"' in page
    assert page.index('value="olga"') < page.index('value="Automation"') < page.index('value="eindringling"')
    assert "Kein Konto (gelöscht oder fehlgeschlagene Anmeldung)" in page


def test_benutzerfilter(world):
    _fill()
    c = world.client(world.ids["olga"])
    page = c.get("/admin/audit?username=VICTIM").text
    assert "victim-start" in page and "olga-stop" not in page
    page = c.get("/admin/audit", params={"username": audit.NO_USER}).text
    assert "ohne-konto" in page and "falsches-passwort" not in page and "victim-start" not in page


def test_ip_suche_als_teilstring(world):
    _fill()
    c = world.client(world.ids["olga"])
    page = c.get("/admin/audit?ip=192.168.").text
    assert "olga-stop" in page and "victim-start" not in page and "falsches-passwort" not in page
    page = c.get("/admin/audit?ip=203.0.113").text
    assert "falsches-passwort" in page and "ohne-konto" in page and "olga-stop" not in page
    assert "olga-stop" not in c.get("/admin/audit?ip=%25").text      # % ist kein Platzhalter
    assert "zeitplan-lauf" in c.get("/admin/audit?ip=intern").text


def test_filter_kombinierbar_auch_im_export(world):
    _fill()
    c = world.client(world.ids["olga"])
    page = c.get("/admin/audit?ip=192.168.&action=power.stop&username=olga&server=srv-a").text
    assert "olga-stop" in page and "Anmeldung" not in page.split("<tbody>")[1]
    assert "ip=192.168." in page                                     # Export und Blättern behalten den Filter
    csv = c.get("/admin/audit/export?format=csv&ip=10.0.&server=srv-a").text
    assert "victim-start" in csv and "olga-stop" not in csv
    assert "olga-stop" not in c.get("/admin/audit?ip=192.168.&username=victim").text


def test_nur_owner(world):
    _fill()
    c = world.client(world.ids["victim"])
    assert c.get("/admin/audit?ip=192.168.", follow_redirects=False).status_code in (302, 303, 403)
    assert c.get("/admin/audit/export?ip=192.168.", follow_redirects=False).status_code in (302, 303, 403)


def test_aktionen_sortiert_als_vorschlaege(world):
    page = world.client(world.ids["olga"]).get("/admin/audit").text
    block = page.split('<datalist id="audit-actions">')[1].split("</datalist>")[0]
    labels = [part.split('"')[0] for part in block.split('<option value="')[1:]]
    assert labels == sorted(labels, key=str.casefold) and "Server gestoppt" in labels


def test_aktion_per_stichwort(world):
    _fill()
    audit.record("user.create", user_id=1, username="olga", ip="x", detail="anton-angelegt")
    audit.record("invite.accept", user_id=3, username="soil58", ip="x", detail="per-einladung")
    c = world.client(world.ids["olga"])
    page = c.get("/admin/audit?action=angelegt").text                    # Stichwort: beide Arten
    assert "anton-angelegt" in page and "per-einladung" in page and "olga-stop" not in page
    page = c.get("/admin/audit", params={"action": "Benutzer per Einladung angelegt"}).text
    assert "per-einladung" in page and "anton-angelegt" not in page
    page = c.get("/admin/audit?action=user.create").text                 # alte Links mit Schlüssel gehen weiter
    assert "anton-angelegt" in page and "per-einladung" not in page and 'value="Benutzer angelegt"' in page
    page = c.get("/admin/audit?action=gestopp&ip=192.168.").text
    assert "olga-stop" in page and "victim-start" not in page
    assert "olga-stop" not in c.get("/admin/audit?action=gibtesnicht").text
    csv = c.get("/admin/audit/export?format=csv&action=angelegt").text
    assert "per-einladung" in csv and "olga-stop" not in csv


def test_registrierung_per_einladung_im_audit(world):
    owner = world.client(world.ids["olga"])
    r = owner.post("/admin/users/invite", data={"csrf": owner.csrf, "days": "3", "uses": "1"})
    link = re.search(r'value="https?://[^"]*(/invite/[^"]+)"', r.text).group(1)
    c, prelogin = world.anonymous_with_prelogin()
    pw = "Sehr-Langes-Passwort-123"
    assert c.post(link, data={"username": "soil58", "password": pw, "repeat": pw, "prelogin": prelogin}
                  ).headers["location"] == "/"
    page = owner.get("/admin/audit?action=angelegt").text.split("<tbody>")[1]
    assert "soil58" in page and "Benutzer per Einladung angelegt" in page
