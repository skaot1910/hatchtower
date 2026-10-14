"""Editor: welche Dateien sich öffnen lassen (Endung schlägt Mimetype, Binärdateien nicht)."""
from hatchtower.main import looks_editable


def test_text_laut_endung_trotz_octet_stream():
    assert looks_editable("steam_appid.txt", "application/octet-stream")
    assert looks_editable("server.properties", "")


def test_binaerdateien_nicht():
    assert not looks_editable("srcds_linux", "application/x-executable")
    assert not looks_editable("steamclient.so", "application/x-sharedlib")
    assert not looks_editable("world.zip", "application/zip")
    assert not looks_editable("icon.png", "image/png")


def test_skripte_ohne_endung():
    assert looks_editable("srcds_run", "text/plain; charset=utf-8")
