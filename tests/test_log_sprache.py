"""Log lines stay in one language (German, the source text), whatever language the UI shows."""
from __future__ import annotations

import logging

import httpx

from hatchtower import db, i18n, ptero


def test_fehler_haben_einen_unuebersetzten_logtext():
    token = i18n._current.set("en")
    try:
        exc = ptero.fail("{panel} nicht erreichbar: {grund}", grund="ConnectError")
        assert str(exc) == "Pterodactyl not reachable: ConnectError"
        assert exc.log_text == "Pterodactyl nicht erreichbar: ConnectError"
        response = httpx.Response(404, json={"errors": [{"detail": "gone"}]},
                                  request=httpx.Request("GET", "http://panel/api"))
        exc = ptero.response_error(response, "Wings")
        assert str(exc) == "Wings responds with HTTP 404: gone" and exc.status == 404
        assert exc.log_text == "Wings antwortet mit HTTP 404: gone"
        assert ptero.PteroError("Text").log_text == "Text"
    finally:
        i18n._current.reset(token)


def test_download_fehler_im_log_auf_deutsch(world, monkeypatch, caplog):
    async def down(url, range_header=None):
        raise ptero.fail("Wings nicht erreichbar: {grund}", grund="ConnectError")
    monkeypatch.setattr(world.ptero, "wings_stream", down)
    uid = world.user("lader", grants={"srv-a": {"sftp.download"}})
    with db.connect() as con:
        con.execute("UPDATE users SET language = 'en' WHERE id = ?", (uid,))
    c = world.client(uid)
    with caplog.at_level(logging.WARNING):
        r = c.get("/servers/srv-a/files/download", params={"file": "/a.txt"})
    assert r.status_code == 502 and "not reachable" in r.text
    assert "Wings nicht erreichbar" in caplog.text and "not reachable" not in caplog.text
