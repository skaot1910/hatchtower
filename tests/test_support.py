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


def test_geloeschte_server_getrennt_und_kurz(world):
    with db.connect() as con:
        con.execute("INSERT INTO servers (identifier, ptero_id, uuid, name, node_id, egg, present, synced_at)"
                    " VALUES ('alt-1', 99, 'u-alt', 'Alter Server', 1, 'Demo', 0, '2026-10-01T10:00:00+00:00')")
    text = world.client(world.ids["olga"]).get("/admin/system/support").text
    section = text[text.index("5. Servers"):text.index("6. Users")]
    deleted = section[section.index("deleted in the panel"):]
    assert "Alter Server" in deleted and "last seen in the panel 2026-10-01T10:00:00+00:00" in deleted
    assert "srv-a" in section[:section.index("deleted in the panel")]


def test_webhook_erfolg_und_fehler_im_bericht(world, monkeypatch):
    import asyncio
    from hatchtower import notify
    with db.connect() as con:
        con.execute("DELETE FROM notify_webhooks")
        con.execute("INSERT INTO notify_webhooks (title, kind, url_enc, events, created_at) VALUES ('Clan', 'discord',"
                    " ?, 'crash', ?)", (main.vault.encrypt(WEBHOOK), db.utcnow()))
    calls = []

    async def post(url, *a):
        calls.append(url)
        if len(calls) > 1:
            raise RuntimeError("Webhook antwortet mit HTTP 404")
    monkeypatch.setattr(notify, "_post_webhook", post)
    asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "weg", only="webhook"))
    asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "weg", only="webhook"))
    text = world.client(world.ids["olga"]).get("/admin/system/support").text
    section = text[text.index("9. Notifications"):text.index("10. Settings")]
    assert '"Clan" discord to discord.com' in section and "HTTP 404" in section
    assert "last sent ok: 20" in section and "WebhookToken" not in text


def test_adressen_standardmaessig_als_platzhalter(world, monkeypatch):
    monkeypatch.setattr(main.settings, "pterodactyl_url", "https://panel.meine-domain.de")
    monkeypatch.setattr(main.settings, "node_overrides", {"wings.meine-domain.de": "http://192.168.1.20:8080"})
    owner = world.client(world.ids["olga"])
    text = owner.get("/admin/system/support").text
    assert "meine-domain" not in text and "192.168.1.20" not in text
    assert "https://<host-1>" in text and "http://<private-ip-1>:8080" in text
    full = owner.get("/admin/system/support?addresses=1").text
    assert "https://panel.meine-domain.de" in full and "192.168.1.20" in full
    assert world.audit("system.support")[-1]["detail"] == '{"ansehen": false, "adressen": true}'


def test_gleiche_probleme_im_audit_einmal_mit_anzahl(world):
    from hatchtower import audit
    for _ in range(3):
        audit.record("schedule.run", result="error", username="Automation", server="srv-a", detail="Plan: HTTP 404")
    text = world.client(world.ids["olga"]).get("/admin/system/support").text
    section = text[text.index("11. Recent problems"):text.index("12. Updates")]
    assert section.count("Plan: HTTP 404") == 1 and "(×3 since" in section
    assert "last login" not in text


def test_journal_wiederholungen_zusammengefasst(world, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: main.settings)
    monkeypatch.setattr(cli, "apply_db_values", lambda *a: None)
    journal = "".join(f"2026-10-22T17:0{i}:11+02:00 host uvicorn[{100 + i}]: WARNING x: HTTP 404\n" for i in range(5))
    monkeypatch.setattr(sys, "stdin", io.StringIO("Bericht\n15. System journal\n=====\n" + journal +
                                                  "2026-10-22T17:09:00+02:00 host systemd[1]: Started.\n"))
    cli.redact_stdin()
    out = capsys.readouterr().out
    assert out.count("HTTP 404") == 1 and "[×5, last 2026-10-22T17:04:11+02:00 host uvicorn[104]:]" in out
    assert "Started." in out


def test_adressen_auch_am_satzende_und_ipv6():
    cases = {
        "answers at http://192.168.178.97.": "answers at http://<private-ip-1>.",
        "(10.0.0.5) and 8.8.8.8, 1.1.1.1;": "(<private-ip-1>) and <public-ip-1>, <public-ip-2>;",
        "wings at [2a01:4f8::1]:8080": "wings at [<public-ip-1>]:8080",
        "https://panel.my-domain.de/ and panel.my-domain.de.": "https://<host-1>/ and <host-1>.",
    }
    for text, expected in cases.items():
        assert support.anonymize(text) == expected, text
    for keep in ("version 1.2.3.4.5", "at 12:30:00", "2026-10-22T21:35:45+00:00", "127.0.0.1", "::1",
                 "smtp.gmail.com", "hatchtower.statusfeed", "backups.download", "main.py"):
        assert support.anonymize(keep) == keep, keep
