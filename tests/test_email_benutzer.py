"""E-Mail-Adressen: Owner trägt sie für Benutzer ein, Liste zeigt sie, verständliche SMTP-Fehler."""
import smtplib

import pytest

from hatchtower import db, mailer


def _user(uid):
    with db.connect() as con:
        return con.execute("SELECT email, email_verified, notify_email FROM users WHERE id = ?", (uid,)).fetchone()


def _mail_an(monkeypatch, optional=False):
    for key, value in {"mail_enabled": "1", "mail_host": "smtp.example.com", "mail_from": "h@example.com",
                       "mail_base_url": "http://hatchtower.test", "users_email_optional": "1" if optional else "0"}.items():
        db.set_setting(key, value, None)
    sent = []

    async def fake_send(cfg, to, subject, text):
        sent.append(to)
    monkeypatch.setattr(mailer, "send", fake_send)
    return sent


def _post(client, url, **data):
    return client.post(url, data={"csrf": client.csrf, **data}, follow_redirects=False)


def test_owner_setzt_bestaetigte_adresse(world, monkeypatch):
    _mail_an(monkeypatch)
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    assert _post(owner, f"/admin/users/{uid}/email", email="v@example.com", confirmed="1").status_code in (302, 303)
    assert tuple(_user(uid))[:2] == ("v@example.com", 1)
    assert "v@example.com" in owner.get("/admin/users").text
    assert 'id="email"' in owner.get(f"/admin/users/{uid}").text
    assert world.audit("email.change")


def test_unbestaetigt_mit_link(world, monkeypatch):
    sent = _mail_an(monkeypatch)
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    _post(owner, f"/admin/users/{uid}/email", email="v@example.com")
    assert tuple(_user(uid))[:2] == ("v@example.com", 0) and sent == ["v@example.com"]
    assert "nicht bestätigt" in owner.get("/admin/users").text


def test_ohne_mailversand_kein_hinzufuegen_aber_entfernen(world):
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    _post(owner, f"/admin/users/{uid}/email", email="v@example.com", confirmed="1")
    assert _user(uid)["email"] is None
    with db.connect() as con:
        con.execute("UPDATE users SET email = 'alt@example.com', email_verified = 1 WHERE id = ?", (uid,))
    assert "disabled" in owner.get(f"/admin/users/{uid}").text
    _post(owner, f"/admin/users/{uid}/email", email="")
    assert tuple(_user(uid)) == (None, 0, 0)


def test_ungueltig(world, monkeypatch):
    _mail_an(monkeypatch)
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    _post(owner, f"/admin/users/{uid}/email", email="kaputt", confirmed="1")
    assert _user(uid)["email"] is None


def test_benutzer_darf_fremde_adresse_nicht_setzen(world, monkeypatch):
    _mail_an(monkeypatch)
    victim = world.client(world.ids["victim"])
    r = _post(victim, f"/admin/users/{world.ids['olga']}/email", email="x@example.com", confirmed="1")
    assert r.status_code in (302, 303, 403, 404)
    assert _user(world.ids["olga"])["email"] is None


def test_ohne_email_schalter(world, monkeypatch):
    _mail_an(monkeypatch)
    owner, uid = world.client(world.ids["olga"]), world.ids["victim"]
    _post(owner, f"/admin/users/{uid}/email", email="v@example.com", confirmed="1")
    _post(owner, f"/admin/users/{uid}/no-email", no_email="1")
    assert tuple(_user(uid)) == (None, 0, 0)
    _post(owner, f"/admin/users/{uid}/email", email="v@example.com", confirmed="1")      # Owner: gesperrt
    victim = world.client(uid)
    _post(victim, "/account/email", email="v@example.com")                              # Benutzer selbst: gesperrt
    assert _user(uid)["email"] is None
    assert "keine E-Mail-Adresse vorgesehen" in victim.get("/account").text
    _post(owner, f"/admin/users/{uid}/no-email")
    _post(owner, f"/admin/users/{uid}/email", email="v@example.com", confirmed="1")
    assert _user(uid)["email"] == "v@example.com"


PW = "Ein-Langes-Passwort-42!"


def test_anlegen_pflicht_und_ausnahme(world, monkeypatch):
    sent = _mail_an(monkeypatch)
    owner = world.client(world.ids["olga"])
    _post(owner, "/admin/users", username="ohne", password=PW)
    with db.connect() as con:
        assert con.execute("SELECT 1 FROM users WHERE username = 'ohne'").fetchone() is None   # Pflicht greift
    _post(owner, "/admin/users", username="ohne", password=PW, no_email="1")
    _post(owner, "/admin/users", username="mit", password=PW, email="mit@example.com")
    with db.connect() as con:
        rows = {r["username"]: (r["email"], r["no_email"]) for r in con.execute("SELECT * FROM users")}
    assert rows["ohne"] == (None, 1) and rows["mit"] == ("mit@example.com", 0) and sent == ["mit@example.com"]


def test_anlegen_ohne_pflicht(world, monkeypatch):
    _mail_an(monkeypatch, optional=True)
    owner = world.client(world.ids["olga"])
    _post(owner, "/admin/users", username="frei", password=PW)
    with db.connect() as con:
        assert con.execute("SELECT no_email FROM users WHERE username = 'frei'").fetchone()["no_email"] == 0


def test_anlegen_mail_aus_ignoriert_feld(world):
    owner = world.client(world.ids["olga"])
    _post(owner, "/admin/users", username="aus", password=PW, email="aus@example.com")
    with db.connect() as con:
        assert con.execute("SELECT email FROM users WHERE username = 'aus'").fetchone()["email"] is None


def test_pflicht_einstellung(world):
    owner = world.client(world.ids["olga"])
    _post(owner, "/admin/users/email-policy", optional="1")
    assert mailer.email_optional()
    _post(owner, "/admin/users/email-policy")
    assert not mailer.email_optional()


def test_absender_faellt_auf_login_zurueck(world):
    from hatchtower.main import vault
    db.set_setting("mail_username", "login@example.com", None)
    db.set_setting("mail_from", "", None)
    assert mailer.load_config(vault).sender == "login@example.com"
    db.set_setting("mail_from", "anders@example.com", None)
    assert mailer.load_config(vault).sender == "anders@example.com"


def _cfg():
    return mailer.MailConfig(enabled=True, host="smtp.example.com", port=465, security="ssl", username="u",
                             password="p", sender="a@example.com", sender_name="H", base_url="http://x")


class _Server:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")


def test_abbruch_beim_login_wird_erklaert(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", _Server)
    with pytest.raises(mailer.MailError, match="App-Passwort"):
        mailer._send_blocking(_cfg(), ["b@example.com"], "Betreff", "Text")
