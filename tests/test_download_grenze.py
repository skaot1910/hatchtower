"""Upload limit for downloads (System → Network): set by the owner, applies to visitors from outside, all downloads
share it, part requests keep working."""
from __future__ import annotations

import asyncio
import time

import pytest

from hatchtower import db, downloads


@pytest.fixture(autouse=True)
def fresh_limit():
    downloads.reload()
    downloads.limiter.free_at = 0.0
    yield
    downloads.reload()


def test_eigenes_netz_erkennen():
    for ip in ("192.168.178.20", "10.1.2.3", "172.20.0.5", "127.0.0.1", "::1", "fd00::5", "::ffff:192.168.1.9"):
        assert downloads.is_internal(ip), ip
    for ip in ("8.8.8.8", "172.32.0.1", "100.101.102.103", "2a01::1", "unbekannt", None):
        assert not downloads.is_internal(ip), ip


def test_einstellen_und_wer_gebremst_wird(world):
    owner = world.client(world.ids["olga"])
    page = owner.get("/admin/network").text
    assert 'id="downloads"' in page and "kommt von außerhalb" in page
    r = owner.post("/admin/network/downloads", data={"csrf": owner.csrf, "limit_mbit": "80", "lan_free": "1"},
                   follow_redirects=False)
    assert r.status_code == 303 and db.get_setting("download_limit_mbit") == "80"
    assert world.audit("settings.download_limit")
    assert downloads.throttled("203.0.113.7") and not downloads.throttled("192.168.178.20")
    owner.post("/admin/network/downloads", data={"csrf": owner.csrf, "limit_mbit": "12,5"})
    assert downloads.config() == (12.5, False) and downloads.throttled("192.168.178.20")
    r = owner.post("/admin/network/downloads", data={"csrf": owner.csrf, "limit_mbit": "viel"}, follow_redirects=False)
    assert "Mbit" in r.headers["location"] and db.get_setting("download_limit_mbit") == "12.5"
    owner.post("/admin/network/downloads", data={"csrf": owner.csrf, "limit_mbit": "0", "lan_free": "1"})
    assert not downloads.throttled("203.0.113.7")


def test_gedrosselter_download_bleibt_vollstaendig(world, caplog):
    db.set_setting("download_limit_mbit", "100", None)
    caplog.set_level("INFO", logger="hatchtower.download")
    c = world.client(world.user("lader", grants={"srv-a": {"backups.download"}}))
    url = "/admin/backups/panel/srv-a/pb-1/download"
    whole = c.get(url)
    assert whole.status_code == 200 and whole.content.startswith(b"\x1f\x8b")
    part = c.get(url, headers={"Range": "bytes=5-12"})
    assert part.status_code == 206 and part.content == whole.content[5:13]
    assert "gedrosselt auf 100 Mbit/s" in caplog.text


def test_tempo_wird_eingehalten_und_geteilt():
    db_rate = 2 * 125000                                  # 2 Mbit/s

    async def one(n):
        for _ in range(n):
            await downloads.limiter.take(25000, db_rate)

    async def main():
        start = time.monotonic()
        await asyncio.gather(one(4), one(4))             # 200 kB together at 250 kB/s
        return time.monotonic() - start
    took = asyncio.run(main())
    assert 0.5 < took < 1.2, took


def test_datei_mit_teilanfragen(tmp_path):
    path = tmp_path / "b.zip"
    path.write_bytes(bytes(range(256)) * 1000)

    def get(range_header):
        headers = {}
        status, body = downloads.file_body(path, path.stat().st_size, range_header, headers)

        async def read():
            return b"".join([c async for c in body])
        return status, headers, asyncio.run(read())
    status, headers, data = get(None)
    assert status == 200 and data == path.read_bytes() and headers["Content-Length"] == "256000"
    status, headers, data = get("bytes=0-1")
    assert status == 206 and data == b"\x00\x01" and headers["Content-Range"] == "bytes 0-1/256000"
    status, headers, data = get("bytes=255990-")
    assert status == 206 and data == path.read_bytes()[255990:]
    status, headers, data = get("bytes=999999-")
    assert status == 416 and data == b"" and "Content-Length" not in headers
