"""Protokoll auf der Serverseite: nur mit dem Recht „Protokoll sehen“, nur der eigene Server, ohne IP."""
import time

from hatchtower import audit, db


def _fill():
    audit.record("power.restart", user_id=1, username="olga", ip="203.0.113.7", server="srv-a")
    audit.record("files.edit", user_id=1, username="olga", ip="203.0.113.7", server="srv-a", detail="/server.cfg")
    audit.record("files.list", user_id=1, username="olga", ip="203.0.113.7", server="srv-a", detail="/geheim-liste")
    audit.record("access.denied", username="victim", ip="203.0.113.8", server="srv-a", result="denied")
    audit.record("power.stop", user_id=1, username="olga", ip="203.0.113.7", server="srv-b", detail="anderer-server")
    audit.record("automation.crash", username="Hatchtower", ip="intern", server="srv-a", detail="Neu gestartet")
    with db.connect() as con:
        con.execute("INSERT INTO state_changes (identifier, ts, state) VALUES ('srv-a', ?, 'offline')",
                    (int(time.time()) - 60,))


def test_ohne_recht_kein_protokoll(world):
    _fill()
    page = world.client(world.ids["victim"]).get("/servers/srv-a")
    assert page.status_code == 200 and 'id="protokoll"' not in page.text and "server.cfg" not in page.text


def test_mit_recht_eigener_server_ohne_ip(world):
    _fill()
    world.grant(world.ids["victim"], {"srv-a": {"server.view", "server.log"}})
    text = world.client(world.ids["victim"]).get("/servers/srv-a").text
    assert 'id="protokoll"' in text
    assert "Server neu gestartet" in text and "/server.cfg" in text and "Absturz erkannt" in text
    assert "Status: offline" in text
    assert "203.0.113" not in text                        # keine IP-Adressen
    assert "/geheim-liste" not in text                    # reine Lesezugriffe nicht
    assert "anderer-server" not in text                   # nur dieser Server
    assert "Zugriff verweigert" not in text


def test_filter_und_mehr_laden(world):
    _fill()
    for i in range(30):
        audit.record("console.command", user_id=1, username="olga", ip="x", server="srv-a", detail=f"say {i}")
    c = world.client(world.ids["olga"])
    files = c.get("/servers/srv-a?log=files").text
    assert "/server.cfg" in files and "say 1" not in files and "Status: offline" not in files
    first = c.get("/servers/srv-a?log=console").text
    assert "say 29" in first and "say 0<" not in first and "log_n=75" in first
    more = c.get("/servers/srv-a?log=console&log_n=75").text
    assert "say 0<" in more and "Ältere Einträge anzeigen" not in more
    assert c.get("/servers/srv-a?log=quatsch&log_n=-5").status_code == 200


def test_nach_benutzer_filtern_und_sortieren(world):
    _fill()
    audit.record("files.upload", user_id=2, username="anton", ip="x", server="srv-a", detail="/mods/a.jar")
    audit.record("power.stop", user_id=1, username="olga", ip="x", server="srv-a", detail="zuletzt")
    c = world.client(world.ids["olga"])
    page = c.get("/servers/srv-a").text
    assert '<option value="anton"' in page and '<option value="olga"' in page and 'value="victim"' not in page
    only = c.get("/servers/srv-a?log_user=anton").text
    assert "/mods/a.jar" in only and "/server.cfg" not in only and "Status: offline" not in only
    assert '<option value="anton" selected' in only
    ignored = c.get("/servers/srv-a?log_user=gibtsnicht").text        # unknown user: no filter
    assert "/server.cfg" in ignored
    sorted_page = c.get("/servers/srv-a?log_sort=user").text
    anton, hatchtower, olga = (sorted_page.index(x) for x in ("/mods/a.jar", "Neu gestartet", "zuletzt"))
    assert anton < hatchtower < olga < sorted_page.index("Status: offline")
    assert sorted_page.index("zuletzt") < sorted_page.index("/server.cfg")    # newest first per user
