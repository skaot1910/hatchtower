"""Mehrere Benachrichtigungs-Webhooks mit Titel, Beschreibung, Ereignissen und Server-Auswahl."""
import asyncio
import sqlite3

from hatchtower import db, notify


def hook(**kw):
    row = {"id": 1, "title": "Alarm", "enabled": 1, "events": "crash,panel_down,new_server", "servers": ""}
    row.update(kw)
    return row


def test_ereignis_und_aktiv():
    assert notify.hook_wants(hook(), "crash", "a")
    assert not notify.hook_wants(hook(), "disk", "a")
    assert not notify.hook_wants(hook(enabled=0), "crash", "a")
    assert notify.hook_wants(hook(enabled=0, events=""), "test", None)     # Testnachricht geht immer


def test_server_auswahl():
    h = hook(servers="a,b")
    assert notify.hook_wants(h, "crash", "a")
    assert not notify.hook_wants(h, "crash", "c")
    assert notify.hook_wants(h, "panel_down", None)      # Panel-Meldungen ohne Server
    assert notify.hook_wants(h, "new_server", "neu")     # neuer Server kann noch nicht ausgewählt sein


def test_alter_webhook_wird_uebernommen(tmp_path):
    path = tmp_path / "alt.db"
    con = sqlite3.connect(path)
    for i, sql in enumerate(db.MIGRATIONS[:21], start=1):
        con.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")
    for key, value in {"notify_webhook_url_enc": "verschluesselt", "notify_webhook_type": "generic",
                       "notify_webhook_events": "crash,disk"}.items():
        con.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    con.execute("INSERT INTO schedules (identifier, name, action, weekdays, time, created_at, run_at)"
                " VALUES ('x', 'Neustart', 'restart', '0', '04:00', 'jetzt', NULL)")
    con.commit()
    con.close()
    db.init(path)
    with db.connect() as con:
        (h,) = con.execute("SELECT * FROM notify_webhooks").fetchall()
        left = con.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE 'notify_webhook_%'").fetchone()[0]
        (s,) = con.execute("SELECT * FROM schedules").fetchall()
    assert (h["title"], h["kind"], h["url_enc"], h["events"], h["servers"], h["enabled"]) == \
        ("Webhook", "generic", "verschluesselt", "crash,disk", "", 1)
    assert left == 0
    assert (s["name"], s["action"], s["duration_minutes"], s["window_until"]) == ("Neustart", "restart", None, None)


def test_alter_webhook_ohne_ereignisliste_bekommt_alle(tmp_path):
    path = tmp_path / "alt.db"
    con = sqlite3.connect(path)
    for i, sql in enumerate(db.MIGRATIONS[:21], start=1):
        con.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")
    con.execute("INSERT INTO app_settings (key, value) VALUES ('notify_webhook_url_enc', 'x')")
    con.commit()
    con.close()
    db.init(path)
    with db.connect() as con:
        (h,) = con.execute("SELECT events FROM notify_webhooks").fetchall()
    assert set(h["events"].split(",")) == set(notify.EVENTS)


def test_versand_nur_an_passende_webhooks(world, monkeypatch):
    from hatchtower import main
    posted = []

    async def fake_post(url, kind, event, title, text, ident, name):
        posted.append(url)
    monkeypatch.setattr(notify, "_post_webhook", fake_post)
    with db.connect() as con:
        con.execute("DELETE FROM notify_webhooks")
        for title, events, servers, enabled in (("alle", "crash", "", 1), ("nur-b", "crash", "srv-b", 1),
                                                ("disk", "disk", "", 1), ("aus", "crash", "", 0)):
            con.execute("INSERT INTO notify_webhooks (title, url_enc, events, servers, enabled, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)", (title, main.vault.encrypt(f"https://h/{title}"), events, servers,
                                                       enabled, db.utcnow()))
    errors = asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "weg", only="webhook"))
    assert errors == [] and posted == ["https://h/alle"]
    posted.clear()
    with db.connect() as con:
        hid = con.execute("SELECT id FROM notify_webhooks WHERE title = 'aus'").fetchone()[0]
    asyncio.run(notify.deliver(main.vault, "test", None, "Test", only="webhook", hook_id=hid))
    assert posted == ["https://h/aus"]


def test_webhook_anlegen_und_aendern(world):
    owner = world.client(world.ids["olga"])
    r = owner.post("/admin/notify/webhook", data={
        "csrf": owner.csrf, "title": "Discord #alarm", "description": "Community, nur Abstürze",
        "url": "https://discord.com/api/webhooks/1/abc", "event__crash": "1", "server__srv-a": "1", "enabled": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as con:
        h = con.execute("SELECT * FROM notify_webhooks WHERE title = 'Discord #alarm'").fetchone()
    assert (h["description"], h["events"], h["servers"], h["enabled"]) == ("Community, nur Abstürze", "crash", "srv-a", 1)
    old_url = h["url_enc"]
    owner.post(f"/admin/notify/webhook/{h['id']}", data={"csrf": owner.csrf, "title": "Neu", "all_servers": "1"},
               follow_redirects=False)
    with db.connect() as con:
        h = con.execute("SELECT * FROM notify_webhooks WHERE id = ?", (h["id"],)).fetchone()
    assert (h["title"], h["servers"], h["enabled"], h["url_enc"]) == ("Neu", "", 0, old_url)   # Adresse bleibt
    assert owner.get("/admin/notify").status_code == 200
