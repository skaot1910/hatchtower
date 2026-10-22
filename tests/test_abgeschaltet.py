"""Webhooks that Discord no longer knows are switched off automatically, with a notice why; the inbox is
filterable and shows errors first."""
from __future__ import annotations

import asyncio

import httpx

from hatchtower import db, inbox, main, notify, routes_extra, statusfeed

HOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 68


def feed_row():
    with db.connect() as con:
        return con.execute("SELECT * FROM status_feeds ORDER BY id LIMIT 1").fetchone()


def run_feed(monkeypatch, status: int):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(status, json={"code": 10015, "message": "Unknown Webhook"})
    real = httpx.AsyncClient
    monkeypatch.setattr(statusfeed.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    sf = statusfeed.StatusFeed(main.vault, routes_extra.discord_status_view)
    asyncio.run(sf.update(feed_row()))
    return sf, calls


def test_statusanzeige_mit_geloeschtem_webhook_wird_abgeschaltet(world, monkeypatch):
    sf, calls = run_feed(monkeypatch, 404)
    f = feed_row()
    assert f["enabled"] == 0 and f["disabled_code"] == 404 and f["disabled_at"]
    assert statusfeed.load_feeds(only_enabled=True) == []                 # no retry every minute
    with db.connect() as con:
        entry = con.execute("SELECT * FROM inbox WHERE event = 'statusfeed_disabled'").fetchone()
    assert entry and "gelöscht" in entry["detail"] and "wieder einschalten" in entry["detail"]
    page = world.client(world.ids["olga"]).get("/admin/statuspage").text
    assert "automatisch abgeschaltet" in page and "HTTP 404" in page


def test_voruebergehender_fehler_schaltet_nicht_ab(world, monkeypatch):
    run_feed(monkeypatch, 500)
    assert feed_row()["enabled"] == 1 and feed_row()["disabled_code"] is None


def test_wieder_einschalten_entfernt_den_hinweis(world, monkeypatch):
    run_feed(monkeypatch, 401)
    owner = world.client(world.ids["olga"])
    f = feed_row()
    r = owner.post(f"/admin/statuspage/discord/{f['id']}",
                   data={"csrf": owner.csrf, "name": f["name"], "url": HOOK, "enabled": "1", "field__name": "1",
                         "server__srv-a": "1"})
    assert r.status_code == 303
    f = feed_row()
    assert f["enabled"] == 1 and f["disabled_code"] is None


def add_hook(kind: str = "discord") -> int:
    with db.connect() as con:
        con.execute("DELETE FROM notify_webhooks")
        return con.execute("INSERT INTO notify_webhooks (title, kind, url_enc, events, created_at) VALUES ('Clan', ?, ?,"
                           " 'crash', ?)", (kind, main.vault.encrypt(HOOK), db.utcnow())).lastrowid


def deliver_with(monkeypatch, status: int):
    async def post(*a):
        raise notify.WebhookError(f"HTTP {status}", status)
    monkeypatch.setattr(notify, "_post_webhook", post)
    asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "weg"))


def hook(hid: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM notify_webhooks WHERE id = ?", (hid,)).fetchone()


def test_benachrichtigungs_webhook_404_wird_abgeschaltet(world, monkeypatch):
    hid = add_hook()
    deliver_with(monkeypatch, 404)
    assert hook(hid)["enabled"] == 0 and hook(hid)["disabled_code"] == 404
    page = world.client(world.ids["olga"]).get("/admin/notify").text
    assert "automatisch abgeschaltet" in page and "gelöscht" in page


def test_allgemeiner_webhook_nur_bei_410(world, monkeypatch):
    hid = add_hook("generic")
    deliver_with(monkeypatch, 404)                 # a JSON receiver may be down for a moment
    assert hook(hid)["enabled"] == 1
    deliver_with(monkeypatch, 410)
    assert hook(hid)["enabled"] == 0 and hook(hid)["disabled_code"] == 410


def test_postfach_zaehlt_und_filtert(world):
    for event, ident in (("crash", "srv-a"), ("restart_failed", "srv-a"), ("schedule_failed", "srv-b"),
                         ("restarted", "srv-a"), ("backup_failed", None)):
        inbox.add(event, "owner" if ident is None else "server", ident, "x")
    owner = world.client(world.ids["olga"])
    html = owner.get("/notifications").text
    assert "ungelesene Fehler" in html and 'class="inbox-level"' in html
    errors = owner.get("/notifications?level=error").text
    assert errors.count('class="inbox-item inbox-error') == 4 and "inbox-ok" not in errors   # + the crash of the test world
    backups = owner.get("/notifications?cat=backup").text
    assert backups.count('class="inbox-item') == 1
    only_b = owner.get("/notifications?server=srv-b").text
    assert only_b.count('class="inbox-item') == 1 and "Keine Meldung passt" not in only_b
    assert "Keine Meldung passt zum Filter" in owner.get("/notifications?cat=new").text
