"""Notification inbox (bell): who sees which entry, read state per user, sources of entries, cleanup."""
import asyncio

from hatchtower import backups, db, inbox, main, notify, retention, statusfeed
from hatchtower.permissions import UserContext, load_grants


def ctx(world, name: str) -> UserContext:
    uid = world.ids[name]
    with db.connect() as con:
        role = con.execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()["role"]
    return UserContext(id=uid, username=name, role=role, read_only=False, grants=load_grants(uid))


def events(world, name: str) -> list[tuple]:
    return [(r["event"], r["identifier"]) for r in inbox.entries(ctx(world, name))]


def fresh(world):
    """World without the sample entry from conftest, plus a user who may not see srv-a."""
    with db.connect() as con:
        con.execute("DELETE FROM inbox")
    world.user("bernd", "user", {"srv-b": {"server.view"}})


def test_absturz_sehen_owner_und_berechtigte(world):
    fresh(world)
    notify.emit(main.vault, "crash", "srv-a", "weg")
    assert events(world, "olga") == [("crash", "srv-a")]
    assert events(world, "victim") == [("crash", "srv-a")]
    assert events(world, "bernd") == []


def test_verwaltungsmeldungen_nur_owner(world):
    fresh(world)
    for event in ("panel_down", "panel_up"):
        notify.emit(main.vault, event, None, "x")
    notify.emit(main.vault, "new_server", "srv-a", "neu")
    inbox.add("update", "owner", None, "Version 9")
    inbox.add("statusfeed_failed", "owner", None, "Clan: HTTP 404")
    assert {e for e, _ in events(world, "olga")} == {"panel_down", "panel_up", "new_server", "update",
                                                     "statusfeed_failed"}
    assert events(world, "victim") == [] and events(world, "bernd") == []


def test_ressourcen_und_backup_ok_nicht_im_postfach(world):
    fresh(world)
    for event in ("disk", "ram", "cpu", "backup_ok"):
        notify.emit(main.vault, event, "srv-a", "x")
    assert events(world, "olga") == []


def test_backup_fehler_owner_zusammenfassung_benutzer_eigener_server(world):
    fresh(world)
    job = {"name": "Welt"}
    rows = [{"identifier": "srv-a", "server_name": "A", "status": "error", "detail": "Platte voll"},
            {"identifier": "srv-b", "server_name": "B", "status": "partial", "detail": None},
            {"identifier": "srv-c", "server_name": "C", "status": "ok", "detail": None}]
    backups.BackupManager._inbox(job, rows, ["Job „Welt“: 1 von 3 Servern gesichert"])
    assert events(world, "olga") == [("backup_failed", None)]
    assert events(world, "victim") == [("backup_failed", "srv-a")]
    assert events(world, "bernd") == [("backup_partial", "srv-b")]
    (row,) = inbox.entries(ctx(world, "victim"))
    assert "Platte voll" in row["detail"] and "srv-b" not in (row["detail"] or "")


def test_abgebrochene_backups_nicht_im_postfach(world):
    fresh(world)
    rows = [{"identifier": "srv-a", "server_name": "A", "status": "cancelled", "detail": None}]
    backups.BackupManager._inbox({"name": "Welt"}, rows, ["x"])
    assert events(world, "olga") == [] and events(world, "victim") == []


def test_gelesen_pro_benutzer(world):
    fresh(world)
    first = inbox.add("crash", "server", "srv-a", "1")
    inbox.add("restarted", "server", "srv-a", "2")
    olga, victim = ctx(world, "olga"), ctx(world, "victim")
    assert inbox.unread_count(olga) == 2 and inbox.unread_count(victim) == 2
    inbox.mark_read(victim, first)
    assert inbox.unread_count(victim) == 1 and inbox.unread_count(olga) == 2
    inbox.mark_all_read(olga)
    assert inbox.unread_count(olga) == 0 and inbox.unread_count(victim) == 1
    inbox.add("crash", "server", "srv-a", "3")
    assert inbox.unread_count(olga) == 1 and len(inbox.entries(olga)) == 3


def test_neue_benutzer_sehen_keine_alten_meldungen(world):
    fresh(world)
    with db.connect() as con:
        con.execute("INSERT INTO inbox (created_at, event, audience, identifier) VALUES"
                    " ('2000-01-01T00:00:00+00:00', 'crash', 'server', 'srv-a')")
    assert events(world, "victim") == []


def test_seite_und_glocke(world):
    fresh(world)
    inbox.add("crash", "server", "srv-a", "Abgestürzt um drei")
    inbox.add("panel_down", "owner", None, "Panel weg")
    page = world.client(world.ids["victim"]).get("/notifications")
    assert page.status_code == 200
    assert "Abgestürzt um drei" in page.text and "Panel weg" not in page.text
    assert 'class="bell-count" aria-hidden="true">1<' in page.text
    owner_page = world.client(world.ids["olga"]).get("/notifications").text
    assert "Panel weg" in owner_page and 'class="bell-count" aria-hidden="true">2<' in owner_page
    assert "Abgestürzt um drei" not in world.client(world.ids["bernd"]).get("/notifications").text


def test_lesen_ueber_formular(world):
    fresh(world)
    own = inbox.add("crash", "server", "srv-a", "x")
    foreign = inbox.add("panel_down", "owner", None, "y")
    c = world.client(world.ids["victim"])
    r = c.post(f"/notifications/{own}/read", data={"csrf": c.csrf, "go": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/servers/srv-a"
    # Entry of the owners: neither marked nor revealed
    r = c.post(f"/notifications/{foreign}/read", data={"csrf": c.csrf, "go": "1"})
    assert r.headers["location"] == "/notifications"
    with db.connect() as con:
        assert [tuple(r) for r in con.execute("SELECT entry_id FROM inbox_read")] == [(own,)]
    o = world.client(world.ids["olga"])
    assert o.post("/notifications/read-all", data={"csrf": o.csrf}).status_code == 303
    assert inbox.unread_count(ctx(world, "olga")) == 0 and inbox.unread_count(ctx(world, "victim")) == 0


def test_statusfeed_fehler_ab_zweitem_mal_und_wieder_ok(world):
    fresh(world)
    feed = statusfeed.StatusFeed(main.vault, lambda f: ("", [], set(), "", ""))
    row = {"id": 7, "name": "Clan"}
    feed._failed(row, "HTTP 404")
    assert events(world, "olga") == []
    feed._failed(row, "HTTP 404")
    feed._failed(row, "HTTP 404")
    assert events(world, "olga") == [("statusfeed_failed", None)]
    assert "Clan: HTTP 404" in inbox.entries(ctx(world, "olga"))[0]["detail"]
    # A restart of Hatchtower does not repeat the entry within 6 hours
    again = statusfeed.StatusFeed(main.vault, lambda f: ("", [], set(), "", ""))
    again._failed(row, "HTTP 404")
    again._failed(row, "HTTP 404")
    assert len(events(world, "olga")) == 1
    assert events(world, "victim") == []


def test_zustellfehler_landen_beim_owner(world, monkeypatch):
    fresh(world)

    async def broken(*a, **k):
        raise RuntimeError("Webhook antwortet mit HTTP 404")
    monkeypatch.setattr(notify, "_post_webhook", broken)
    errors = asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "weg"))
    assert errors
    assert ("delivery_failed", None) in events(world, "olga")
    assert all(e != "delivery_failed" for e, _ in events(world, "victim"))
    # Test messages report their errors directly on the page, not in the inbox
    with db.connect() as con:
        con.execute("DELETE FROM inbox")
    asyncio.run(notify.deliver(main.vault, "test", None, "t", only="webhook"))
    assert events(world, "olga") == []


def test_aufraeumen(world):
    fresh(world)
    with db.connect() as con:
        con.execute("INSERT INTO inbox (created_at, event, audience) VALUES ('2000-01-01T00:00:00+00:00', 'update', 'owner')")
    inbox.add("update", "owner", None, "neu")
    assert retention.cleanup()["inbox"] == 1
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
