"""Discord-Statusanzeige: nur freigegebene Angaben, gültige Webhook-Adressen."""
from hatchtower import statusfeed

SERVERS = [
    {"identifier": "a", "name": "Valheim", "state": "running", "state_label": "running", "address": "play.example:2456"},
    {"identifier": "b", "name": "Minecraft", "state": "offline", "state_label": "offline"},
]


def test_build_reihen_mit_trennlinie(world):
    servers = [dict(s, egg="Valheim", ingame="Wikinger EU") for s in SERVERS * 2]
    payloads = statusfeed.build("Meine Server", servers, {"name", "status", "address", "ingame"},
                                "https://ht.example/status", "https://ht.example/")
    assert len(payloads) == 1
    (embed,) = payloads[0]["embeds"]
    assert embed["title"] == "Meine Server" and embed["color"] == statusfeed.COLOR_PARTLY
    assert embed["description"].startswith("🔗 [Statusseite öffnen](https://ht.example/status)  ·  "
                                           "🛠️ [Serververwaltung](https://ht.example/)")
    assert "🟩🟥🟩🟥" in embed["description"]
    names = [f["name"] for f in embed["fields"]]
    assert names == ["Valheim", "Minecraft", "Valheim", "\u200b", "Minecraft"]
    assert "Ingame-Servername: Wikinger EU" in embed["fields"][0]["value"]
    assert "`play.example:2456`" in embed["fields"][0]["value"] and "footer" in embed


def test_build_viele_server_mehrere_nachrichten(world):
    servers = [{"identifier": str(i), "name": f"Server {i}", "state": "running", "egg": "Paper",
                "address": f"play.example.de:{25000 + i}", "ingame": "Survival Welt " + "x" * 30,
                "started": "Gestartet: 08.10. 06:00"} for i in range(60)]
    payloads = statusfeed.build("T", servers, {"name", "status", "address", "ingame", "started"}, "")
    embeds = [e for p in payloads for e in p["embeds"]]
    assert len(payloads) >= 2 and len(embeds) == 4
    assert sum(1 for e in embeds for f in e["fields"] if f["inline"]) == 60
    for p in payloads:
        assert len(p["embeds"]) <= 10 and sum(map(statusfeed._size, p["embeds"])) <= statusfeed.CHARS_PER_MESSAGE
        assert all(len(e["fields"]) <= 25 for e in p["embeds"])
    assert "title" in payloads[0]["embeds"][0] and "footer" in payloads[-1]["embeds"][-1]


def test_build_ohne_status_kein_emoji(world):
    servers = [{"identifier": "a", "name": "Valheim"}]
    (payload,) = statusfeed.build("X", servers, {"name"}, "")
    (embed,) = payload["embeds"]
    assert "description" not in embed and embed["fields"][0]["value"] == "–"


def test_webhook_adresse_nur_discord():
    assert statusfeed.valid_url("https://discord.com/api/webhooks/123456789012345678/" + "a" * 68)
    assert not statusfeed.valid_url("https://evil.example/api/webhooks/123456789012345678/" + "a" * 68)
    assert not statusfeed.valid_url("http://discord.com/api/webhooks/123456789012345678/" + "a" * 68)


def feed(feed_id):
    from hatchtower import db
    with db.connect() as con:
        return con.execute("SELECT * FROM status_feeds WHERE id = ?", (feed_id,)).fetchone()


def test_discord_eigene_auswahl(world):
    from hatchtower import db, routes_extra
    with db.connect() as con:
        con.execute("UPDATE server_meta SET on_status = 0")         # Statusseite zeigt nichts
        con.execute("UPDATE status_feeds SET fields = 'name,uptime'")
    db.set_setting("status_fields", "name,status,address,password", None)
    _, servers, fields, _, _ = routes_extra.discord_status_view(feed(int(world.args["feed_id"])))
    assert fields == {"name"} and [s["identifier"] for s in servers] == ["srv-a"]
    for s in servers:
        assert "name" in s and not {"state", "address", "password"} & set(s)


def test_mehrere_kanaele_mit_eigener_auswahl(world):
    from hatchtower import db, routes_extra
    c = world.client(world.ids["olga"])
    url = "https://discord.com/api/webhooks/223456789012345678/" + "b" * 68
    r = c.post("/admin/statuspage/discord", data={"csrf": c.csrf, "name": "Freunde", "url": url, "title": "Unsere Runde",
                                                  "show__srv-b": "1", "field__name": "1", "field__status": "1",
                                                  "enabled": "1"})
    assert "angelegt" in r.headers["location"]
    with db.connect() as con:
        rows = {f["name"]: f for f in con.execute("SELECT * FROM status_feeds")}
    assert set(rows) == {"Clan", "Freunde"} and url not in rows["Freunde"]["url_enc"]
    title, servers, _, _, _ = routes_extra.discord_status_view(rows["Freunde"])
    assert title == "Unsere Runde" and [s["identifier"] for s in servers] == ["srv-b"]
    _, servers, _, _, _ = routes_extra.discord_status_view(rows["Clan"])
    assert [s["identifier"] for s in servers] == ["srv-a"]
    # Statusseite bleibt davon unberührt
    with db.connect() as con:
        row = con.execute("SELECT on_status FROM server_meta WHERE identifier = 'srv-b'").fetchone()
    assert row is None or row[0] == 0
    # Bearbeiten ohne neue Adresse behält den Webhook, Auswahl ändert sich
    fid = rows["Freunde"]["id"]
    r = c.post(f"/admin/statuspage/discord/{fid}", data={"csrf": c.csrf, "name": "Freunde", "show__srv-a": "1",
                                                          "show__srv-b": "1", "field__name": "1"})
    assert "gespeichert" in r.headers["location"]
    assert feed(fid)["url_enc"] == rows["Freunde"]["url_enc"] and feed(fid)["enabled"] == 0
    _, servers, _, _, _ = routes_extra.discord_status_view(feed(fid))
    assert {s["identifier"] for s in servers} == {"srv-a", "srv-b"}
    assert c.get("/admin/statuspage").status_code == 200
    r = c.post(f"/admin/statuspage/discord/{fid}/delete", data={"csrf": c.csrf})
    assert feed(fid) is None
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM status_feed_servers WHERE feed_id = ?", (fid,)).fetchone()[0] == 0


def test_kanal_ohne_gueltige_adresse_wird_nicht_angelegt(world):
    from hatchtower import db
    c = world.client(world.ids["olga"])
    c.post("/admin/statuspage/discord", data={"csrf": c.csrf, "name": "Böse", "url": "https://evil.example/hook"})
    c.post("/admin/statuspage/discord", data={"csrf": c.csrf, "name": "Leer"})
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM status_feeds").fetchone()[0] == 1


def test_jeder_kanal_bekommt_eigene_nachrichten(world, monkeypatch):
    import asyncio

    import httpx

    from hatchtower import db, main, routes_extra
    with db.connect() as con:
        fid2 = con.execute("INSERT INTO status_feeds (name, url_enc, fields, created_at) VALUES ('Zwei', ?, 'name', ?)",
                           (main.vault.encrypt("https://discord.com/api/webhooks/323456789012345678/" + "c" * 68),
                            db.utcnow())).lastrowid
        con.execute("INSERT INTO status_feed_servers (feed_id, identifier) VALUES (?, 'srv-b')", (fid2,))
    posted = []

    def handler(request):
        posted.append((request.method, str(request.url)))
        return httpx.Response(200, json={"id": str(1000 + len(posted))})
    real = httpx.AsyncClient
    monkeypatch.setattr(statusfeed.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    sf = statusfeed.StatusFeed(main.vault, routes_extra.discord_status_view)

    async def go():
        for f in statusfeed.load_feeds(only_enabled=True):
            await sf.update(f)
    asyncio.run(go())
    assert len(posted) == 2 and {u.split("/webhooks/")[1][:3] for _, u in posted} == {"123", "323"}
    ids = {f["name"]: f["msg_ids"] for f in statusfeed.load_feeds()}
    assert ids["Clan"] and ids["Zwei"] and ids["Clan"] != ids["Zwei"]
    asyncio.run(go())                     # unverändert: nichts Neues senden
    assert len(posted) == 2


def test_alter_webhook_wird_uebernommen(tmp_path):
    import sqlite3

    from hatchtower import db
    path = tmp_path / "alt.db"
    con = sqlite3.connect(path)
    for i, sql in enumerate(db.MIGRATIONS[:18], start=1):
        con.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")
    con.execute("INSERT INTO servers (identifier, ptero_id, uuid, name) VALUES ('x', 1, 'u', 'X'), ('y', 2, 'v', 'Y')")
    con.execute("INSERT INTO server_meta (identifier, on_status) VALUES ('x', 1), ('y', 0)")
    for key, value in {"status_discord_url_enc": "verschluesselt", "status_discord_enabled": "1",
                       "status_discord_fields": "name,address", "status_discord_msg": "111,222"}.items():
        con.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    con.commit()
    con.close()
    db.init(path)
    with db.connect() as con:
        (f,) = con.execute("SELECT * FROM status_feeds").fetchall()
        servers = [r[0] for r in con.execute("SELECT identifier FROM status_feed_servers")]
        left = con.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE 'status_discord_%' AND key != 'status_discord_link'").fetchone()[0]
    assert (f["url_enc"], f["enabled"], f["fields"], f["msg_ids"]) == ("verschluesselt", 1, "name,address", "111,222")
    assert servers == ["x"] and left == 0


def test_spiel_ueberschreiben(world):
    from hatchtower import db, routes_extra
    with db.connect() as con:
        ident = con.execute("SELECT identifier FROM servers WHERE present = 1").fetchone()["identifier"]
        con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
        con.execute("UPDATE server_meta SET on_status = 1, game_name = 'Mein Spiel' WHERE identifier = ?", (ident,))
    assert any(s["egg"] == "Mein Spiel" for s in routes_extra.status_servers())
