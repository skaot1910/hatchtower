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


def test_discord_eigene_auswahl(world):
    from hatchtower import db, routes_extra
    with db.connect() as con:
        idents = [r["identifier"] for r in con.execute("SELECT identifier FROM servers WHERE present = 1")]
        for ident in idents:
            con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
        con.execute("UPDATE server_meta SET on_status = 1")
    db.set_setting("status_fields", "name,status,address,password", None)
    db.set_setting("status_discord_fields", "name,uptime", None)
    _, servers, fields, _, _ = routes_extra.discord_status_view()
    assert idents and fields == {"name"} and len(servers) == len(idents)
    for s in servers:
        assert "name" in s and not {"state", "address", "password"} & set(s)


def test_spiel_ueberschreiben(world):
    from hatchtower import db, routes_extra
    with db.connect() as con:
        ident = con.execute("SELECT identifier FROM servers WHERE present = 1").fetchone()["identifier"]
        con.execute("INSERT OR IGNORE INTO server_meta (identifier) VALUES (?)", (ident,))
        con.execute("UPDATE server_meta SET on_status = 1, game_name = 'Mein Spiel' WHERE identifier = ?", (ident,))
    assert any(s["egg"] == "Mein Spiel" for s in routes_extra.status_servers())
