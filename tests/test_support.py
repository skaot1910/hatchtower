"""Support report: everything useful in one text file, nothing secret in it."""
from __future__ import annotations

import io
import logging
import sys

from hatchtower import cli, db, main, support

KEYS = {"app_key": "ptla_GeheimerAppKey1234567890", "client_key": "ptlc_GeheimerClientKey123456",
        "admin_key": "ptla_GeheimerAdminKey12345678"}
WEBHOOK = "https://discord.com/api/webhooks/123456/WebhookToken-abcdefghijklmnopqrstuvwxyz"


def plant_secrets(world, monkeypatch) -> None:
    for name, value in KEYS.items():
        monkeypatch.setattr(main.settings, name, value)
    with db.connect() as con:
        con.execute("INSERT INTO notify_webhooks (title, kind, url_enc, events, created_at) VALUES ('Alarm 2', 'discord',"
                    " ?, 'crash', ?)", (main.vault.encrypt(WEBHOOK), db.utcnow()))
        con.execute("UPDATE backup_targets SET smb_password_enc = ?", (main.vault.encrypt("SmbPasswort-987"),))
        con.execute("UPDATE servers SET startup_password_enc = ? WHERE identifier = 'srv-a'",
                    (main.vault.encrypt("Beitritt-4711"),))
        con.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('mail_password_enc', ?), ('mail_from', 'clan@example.org')",
                    (main.vault.encrypt("MailPasswort-555"),))
    world.user("geheimname", grants={"srv-a": {"server.view", "backups.download"}})
    # a secret that slipped into a log line is removed as well
    logging.getLogger("hatchtower.test").warning("Anfrage mit %s fehlgeschlagen", KEYS["client_key"])


def test_bericht_enthaelt_das_wichtige_und_nichts_geheimes(world, monkeypatch):
    plant_secrets(world, monkeypatch)
    owner = world.client(world.ids["olga"])
    r = owner.get("/admin/system/support")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"].startswith('attachment; filename="hatchtower-support-')
    text = r.text
    for heading in ("1. Overview", "2. Connection to the panel", "3. Connection check", "5. Servers",
                    "6. Users and permissions", "8. Backups", "10. Settings", "11. Recent problems", "13. Log"):
        assert heading in text, heading
    assert main.__version__ in text and "https://panel.test" in text and "srv-a" in text
    assert "backups.download" in text                          # rights of the anonymous user
    for secret in (*KEYS.values(), WEBHOOK, "WebhookToken-abcdefghijklmnopqrstuvwxyz", "SmbPasswort-987",
                   "Beitritt-4711", "MailPasswort-555", main.settings.secret_key, main.settings.data_key,
                   "geheimname", "clan@example.org"):
        assert secret not in text, secret
    assert "Anfrage mit *** fehlgeschlagen" in text and "***@example.org" in text
    assert world.audit("system.support")


def test_bericht_im_browser_ansehen(world):
    r = world.client(world.ids["olga"]).get("/admin/system/support?view=1")
    assert r.status_code == 200 and r.headers["content-disposition"] == "inline"


def test_kaputter_teil_kostet_nicht_den_ganzen_bericht(world, monkeypatch):
    def broken(out):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(support, "servers_section", broken)
    text = world.client(world.ids["olga"]).get("/admin/system/support").text
    assert "could not be collected: RuntimeError: kaputt" in text and "6. Users and permissions" in text


def test_redact_filter_der_kommandozeile(world, monkeypatch, capsys):
    plant_secrets(world, monkeypatch)
    monkeypatch.setattr(cli, "load_settings", lambda: main.settings)
    monkeypatch.setattr(cli, "apply_db_values", lambda *a: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"journal: key={KEYS['app_key']} hook {WEBHOOK}\n"))
    assert cli.redact_stdin() == 0
    out = capsys.readouterr().out
    assert KEYS["app_key"] not in out and "WebhookToken" not in out and out.startswith("journal: key=***")


def test_namen_aus_fehlgeschlagenen_logins_erscheinen_nicht(world):
    from hatchtower import audit
    uid = world.user("Brezelbaron")
    audit.record("login", result="denied", username="Brezelbaron", detail="Passwort falsch")
    audit.record("login", result="denied", username="Tippfehler-Name", detail="Unbekannter Benutzer")
    text = world.client(world.ids["olga"]).get("/admin/system/support").text
    assert "Brezelbaron" not in text and "Tippfehler-Name" not in text
    assert f"user #{uid}" in text and "(unknown name)" in text
