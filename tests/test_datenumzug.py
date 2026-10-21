"""Moving an instance: the encrypted export holds everything needed and the import restores it exactly."""
from __future__ import annotations

import json
from urllib.parse import unquote

import pytest
from conftest import PASSWORD

from hatchtower import db, main, transfer

PASS = "Export-Passwort-123"
LOGO = "0123456789abcdef0123456789abcdef.webp"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def fill(world) -> None:
    """Some of everything the export covers, including secrets that are stored encrypted."""
    vault = main.vault
    world.user("spieler", grants={"srv-a": {"server.view", "power.start"}})
    with db.connect() as con:
        gid = con.execute("INSERT INTO perm_groups (name, created_at) VALUES ('Umzugsgruppe', ?)", (db.utcnow(),)).lastrowid
        con.execute("INSERT INTO perm_group_items (group_id, permission) VALUES (?, 'console.read')", (gid,))
        con.execute("INSERT INTO server_meta (identifier, logo_file, ingame_name, password_enc) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(identifier) DO UPDATE SET logo_file = excluded.logo_file,"
                    " ingame_name = excluded.ingame_name, password_enc = excluded.password_enc",
                    ("srv-a", LOGO, "Mein Server", vault.encrypt("geheim-123")))
        con.execute("INSERT INTO schedules (identifier, name, action, weekdays, time, created_by, created_at)"
                    " VALUES ('srv-a', 'Nachts', 'restart', '0123456', '04:00', ?, ?)",
                    (world.ids["spieler"], db.utcnow()))
        con.execute("INSERT INTO notify_webhooks (title, url_enc, events, created_at) VALUES ('Hook', ?, 'crash', ?)",
                    (vault.encrypt("https://example.test/hook"), db.utcnow()))
        con.execute("INSERT INTO backup_targets (name, kind, smb_host, smb_share, smb_user, smb_password_enc,"
                    " created_at) VALUES ('NAS', 'smb', 'nas', 'backup', 'ht', ?, ?)",
                    (vault.encrypt("smb-geheim"), db.utcnow()))
    db.set_setting("status_title", "Unsere Server", None)
    db.set_setting("conn_app_key", vault.encrypt("ptla_app"), None)
    (main.settings.logo_dir / LOGO).write_bytes(WEBP)


def plain(world) -> dict:
    """Snapshot with encrypted columns replaced by their plain text (Fernet output differs every time)."""
    vault = main.vault
    result = {}
    with db.connect() as con:
        for table in (*transfer.TABLES, "app_settings"):
            rows = []
            for r in con.execute(f"SELECT * FROM {table}"):
                row = dict(r)
                for k in list(row):
                    if k.endswith("_enc") or (table == "app_settings" and row.get("key") in transfer.SECRET_SETTINGS
                                              and k == "value"):
                        row[k] = vault.decrypt(row[k]) if row[k] else row[k]
                    if k == "updated_at" and table == "app_settings":
                        row[k] = None
                rows.append(json.dumps(row, sort_keys=True, default=str))
            result[table] = sorted(rows)
    return result


def test_export_und_import_stellen_alles_wieder_her(world):
    fill(world)
    before = plain(world)
    raw = transfer.build_export(main.vault, main.settings.logo_dir, PASS)
    assert raw.startswith(transfer.MAGIC) and b"geheim-123" not in raw and b"ptla_app" not in raw

    # the instance changes meanwhile; the import replaces it with the state of the file
    with db.connect() as con:
        con.execute("DELETE FROM schedules")
        con.execute("UPDATE server_meta SET ingame_name = 'anders'")
    world.user("neu")
    (main.settings.logo_dir / LOGO).unlink()

    data = transfer.read_export(raw, PASS)
    assert transfer.summary(data)["users"] == len(before["users"])
    transfer.apply_import(data, main.vault, main.settings.logo_dir)
    assert plain(world) == before
    assert (main.settings.logo_dir / LOGO).read_bytes() == WEBP


def test_import_meldet_alle_ab(world):
    fill(world)
    olga = world.client(world.ids["olga"])
    assert olga.get("/account").status_code == 200
    transfer.apply_import(transfer.read_export(transfer.build_export(main.vault, main.settings.logo_dir, PASS), PASS),
                          main.vault, main.settings.logo_dir)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert olga.get("/account").status_code == 303


@pytest.mark.parametrize("damage, message", [
    (lambda raw: raw, "Falsches Passwort"),                                  # wrong passphrase, see below
    (lambda raw: raw[:-20] + b"A" * 20, "Falsches Passwort oder beschädigte Datei"),
    (lambda raw: b"PK\x03\x04" + raw, "keine Hatchtower-Exportdatei"),
    (lambda raw: transfer.MAGIC + b"%%%\n" + raw.split(b"\n", 2)[2], "beschädigt"),
])
def test_kaputte_dateien_werden_abgelehnt(world, damage, message):
    raw = transfer.build_export(main.vault, main.settings.logo_dir, PASS)
    phrase = "Falsches-Passwort-1" if message == "Falsches Passwort" else PASS
    with pytest.raises(transfer.TransferError, match=message):
        transfer.read_export(damage(raw), phrase)


def test_ohne_aktiven_owner_kein_import(world):
    with db.connect() as con:
        con.execute("UPDATE users SET is_active = 0 WHERE role = 'owner'")
    raw = transfer.build_export(main.vault, main.settings.logo_dir, PASS)
    with pytest.raises(transfer.TransferError, match="keinen aktiven Owner"):
        transfer.read_export(raw, PASS)


def test_fremde_dateinamen_im_export_werden_nicht_geschrieben(world, tmp_path):
    """A crafted export must not write outside the logo folder or anything that is not a WebP image."""
    data = transfer.read_export(transfer.build_export(main.vault, main.settings.logo_dir, PASS), PASS)
    import base64
    data["files"] = {"../evil.webp": base64.b64encode(WEBP).decode(),
                     "fedcba9876543210fedcba9876543210.webp": base64.b64encode(b"<script>").decode(),
                     LOGO: base64.b64encode(WEBP).decode()}
    transfer.apply_import(data, main.vault, main.settings.logo_dir)
    assert not (main.settings.logo_dir.parent / "evil.webp").exists()
    assert not (main.settings.logo_dir / "fedcba9876543210fedcba9876543210.webp").exists()
    assert (main.settings.logo_dir / LOGO).exists()


def test_export_und_import_ueber_die_oberflaeche(world):
    fill(world)
    olga = world.client(world.ids["olga"])
    r = olga.post("/admin/data/export", data={"csrf": olga.csrf, "passphrase": "kurz", "repeat": "kurz",
                                              "current": PASSWORD})
    assert "mindestens 12 Zeichen" in unquote(r.headers["location"])
    r = olga.post("/admin/data/export", data={"csrf": olga.csrf, "passphrase": PASS, "repeat": PASS,
                                              "current": "falsch"})
    assert "Passwort stimmt nicht" in unquote(r.headers["location"])
    r = olga.post("/admin/data/export", data={"csrf": olga.csrf, "passphrase": PASS, "repeat": PASS,
                                              "current": PASSWORD})
    assert r.status_code == 200 and ".htexport" in r.headers["content-disposition"]
    raw = r.content
    assert world.audit("transfer.export")

    form = {"csrf": olga.csrf, "passphrase": PASS, "current": PASSWORD}
    r = olga.post("/admin/data/import", data=form, files={"file": ("x.htexport", raw)})
    assert "Bitte bestätige" in unquote(r.headers["location"])
    r = olga.post("/admin/data/import", data={**form, "confirm": "1"}, files={"file": ("x.htexport", raw)})
    assert r.headers["location"].startswith("/login") and "Import abgeschlossen" in unquote(r.headers["location"])
    assert world.audit("transfer.import")


def test_import_bei_der_einrichtung_nur_ohne_benutzer(world):
    raw = transfer.build_export(main.vault, main.settings.logo_dir, PASS)
    c, prelogin = world.anonymous_with_prelogin()
    r = c.post("/setup/import", data={"token": "x", "passphrase": PASS, "prelogin": prelogin},
               files={"file": ("x.htexport", raw)})
    assert r.status_code == 303 and r.headers["location"] == "/login"
