"""Backups the panel made itself (Pterodactyl, Pelican, Calagopus): listed apart from Hatchtower's, downloadable
with the same rights as stored backups, streamed through Hatchtower."""
from __future__ import annotations

import asyncio

from hatchtower import calagopus, routes_backup
from hatchtower.ptero import PteroError


def page(c, url="/admin/backups/stored") -> str:
    r = c.get(url)
    assert r.status_code == 200, r.status_code
    return r.text


def test_beide_arten_getrennt_und_beschriftet(world):
    html = page(world.client(world.user("lader", grants={"srv-a": {"backups.download"}})))
    hatchtower = html[html.index('id="gespeichert"'):html.index('id="panel-backups"')]
    panel = html[html.index('id="panel-backups"'):]
    assert "Von Hatchtower erstellt" in hatchtower and "Welt" in hatchtower and "Panel-Sicherung" not in hatchtower
    assert "Von Pterodactyl erstellt" in panel and "gespeichert bei Pterodactyl" in panel
    assert "Panel-Sicherung" in panel and "/admin/backups/panel/srv-a/pb-1/download" in panel


def test_nur_sehen_ohne_download(world):
    html = page(world.client(world.user("leser", grants={"srv-a": {"backups.list"}})))
    assert "Panel-Sicherung" in html and "/admin/backups/panel/srv-a/pb-1/download" not in html


def test_download_ueber_hatchtower_und_im_verlauf(world):
    c = world.client(world.user("lader", grants={"srv-a": {"backups.download"}}))
    r = c.get("/admin/backups/panel/srv-a/pb-1/download")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/gzip")
    assert 'Panel-Sicherung-2026-10-20_10-01.tar.gz' in r.headers["content-disposition"]
    assert world.audit("backup.panel_download")
    history = page(c, "/admin/backups/downloads")
    assert "Panel-Backup" in history and "erstellt von Pterodactyl" in history


def test_unbekanntes_backup(world):
    c = world.client(world.ids["olga"])
    r = c.get("/admin/backups/panel/srv-a/gibts-nicht/download")
    assert r.status_code == 303 and "/admin/backups/stored" in r.headers["location"]


def test_panel_nicht_lesbar_wird_gezeigt(world, monkeypatch):
    async def broken(ident):
        raise PteroError("Pterodactyl antwortet mit HTTP 403", 403)
    monkeypatch.setattr(world.ptero, "list_backups", broken)
    html = page(world.client(world.ids["olga"]))
    assert "Backups im Panel nicht lesbar" in html and "HTTP 403" in html


def test_calagopus_liefert_zip():
    seen = {}

    class Fake(calagopus.CalagopusClient):
        def __init__(self):
            pass

        async def _pages(self, path, field, *, key, params=None):
            seen["list"] = (path, field, params)
            return [{"uuid": "u1", "name": "Nacht", "bytes": 5, "is_successful": True, "is_locked": True,
                     "created": "2026-10-01T01:00:00Z", "completed": "2026-10-01T01:02:00Z"}]

        async def _get(self, path, *, key, params=None):
            seen["download"] = (path, params)
            return {"url": "https://node/download?token=t"}
    client = Fake()
    backups = asyncio.run(client.list_backups("abc"))
    assert backups[0]["name"] == "Nacht" and backups[0]["locked"] and seen["list"][2] == {"kind": "server"}
    assert asyncio.run(client.backup_download_url("abc", "u1")) == "https://node/download?token=t"
    assert seen["download"][1] == {"archive_format": "tar_gz"}           # stored backup: as it is stored
    asyncio.run(client.backup_download_url("abc", "u1", streaming=True))
    assert seen["download"][1] == {"archive_format": "zip"}              # streaming backup: ZIP for everyone


def test_format_an_den_ersten_bytes():
    default = ("tar.gz", "application/gzip")
    assert routes_backup.archive_format(b"PK\x03\x04rest", default)[0] == "zip"
    assert routes_backup.archive_format(b"\x1f\x8b\x08\x00", default)[0] == "tar.gz"
    assert routes_backup.archive_format(b"\x28\xb5\x2f\xfd", default)[0] == "tar.zst"
    assert routes_backup.archive_format(b"????", default) == default


def test_liste_wird_eine_minute_gemerkt(world, monkeypatch):
    calls = []
    real = world.ptero.list_backups

    async def counting(ident):
        calls.append(ident)
        return await real(ident)
    monkeypatch.setattr(world.ptero, "list_backups", counting)
    owner = world.client(world.ids["olga"])
    page(owner)
    page(owner)
    assert calls.count("srv-a") == 1
    assert routes_backup.PANEL_CACHE_SECONDS == 60


def test_teilanfragen_wie_safari(world):
    from hatchtower import downloads
    c = world.client(world.user("lader", grants={"srv-a": {"backups.download"}}))
    url = "/admin/backups/panel/srv-a/pb-1/download"
    whole = c.get(url)
    assert whole.status_code == 200 and whole.content.startswith(b"\x1f\x8b")
    probe = c.get(url, headers={"Range": "bytes=0-1"})                  # Safari's first question
    assert probe.status_code == 206 and probe.content == b"\x1f\x8b"
    assert probe.headers["content-range"] == f"bytes 0-1/{len(whole.content)}" and probe.headers["accept-ranges"] == "bytes"
    part = c.get(url, headers={"Range": "bytes=5-12"})                  # across the chunk border of the stream
    assert part.status_code == 206 and part.content == whole.content[5:13]
    rest = c.get(url, headers={"Range": "bytes=2-"})
    assert rest.content == whole.content[2:] and rest.headers["content-length"] == str(len(whole.content) - 2)
    assert c.get(url, headers={"Range": "bytes=999-"}).status_code == 416
    assert part.headers["content-disposition"].endswith(".tar.gz")          # format remembered from the start
    assert len(world.audit("backup.panel_download")) == 1                    # one download, not one per part
    safari_app = ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Mobile/15E148")
    assert downloads.device(safari_app) == "iPad, web app from the home screen"
    assert downloads.device("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36") \
        == "Android, Chrome"


def test_hinweis_bei_http(world):
    html = page(world.client(world.ids["olga"]))
    assert "unsicherer Download" in html


def test_range_auswerten():
    from hatchtower.downloads import parse_range
    assert parse_range("bytes=0-1", 100) == (0, 1)
    assert parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=50-500", 100) == (50, 99)
    assert parse_range("bytes=100-", 100) is False
    assert parse_range("bytes=0-1,5-6", 100) is None              # several ranges: send everything
    assert parse_range(None, 100) is None


def test_fehler_von_wings_wird_gezeigt_und_festgehalten(world, monkeypatch):
    async def gone(url, range_header=None):
        raise PteroError("Wings antwortet mit HTTP 404: The requested backup was not found on this server.", 404)
    monkeypatch.setattr(world.ptero, "wings_stream", gone)
    c = world.client(world.user("lader", grants={"srv-a": {"backups.download"}}))
    r = c.get("/admin/backups/panel/srv-a/pb-1/download")
    assert r.status_code == 303 and "backup%20was%20not%20found" in r.headers["location"]
    entry = world.audit("backup.panel_download")[-1]
    assert entry["result"] == "error" and "not found" in entry["detail"]


def test_einmal_link_wird_einmal_erneuert(world, monkeypatch):
    real = world.ptero.wings_stream
    calls = []

    async def once_used(url, range_header=None):
        calls.append(url)
        if len(calls) == 1:          # the first link was already used: Wings says 404
            raise PteroError("Wings antwortet mit HTTP 404: The requested resource was not found on this server.", 404)
        return await real(url, range_header)
    monkeypatch.setattr(world.ptero, "wings_stream", once_used)
    c = world.client(world.user("lader", grants={"srv-a": {"backups.download"}}))
    r = c.get("/admin/backups/panel/srv-a/pb-1/download")
    assert r.status_code == 200 and len(calls) == 2
