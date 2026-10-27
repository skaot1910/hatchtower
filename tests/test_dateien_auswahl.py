"""Datei-Explorer: mehrere Einträge auf einmal löschen und in einen anderen Ordner verschieben."""
from __future__ import annotations

from urllib.parse import unquote

from hatchtower.ptero import PteroError


def _post(c, path, **data):
    return c.post(path, data={"csrf": c.csrf, **data})


def _writes(world, suffix):
    return [w for w in world.ptero.writes if w[1].endswith(suffix)]


def test_mehrere_loeschen_in_einem_aufruf(world):
    c = world.client(world.ids["olga"])
    r = _post(c, "/servers/srv-a/files/delete-many", dir="/plugins", names=["a.txt", "alt", "a.txt"])
    assert r.status_code == 303 and "2 Einträge gelöscht" in unquote(r.headers["location"])
    assert [w[3] for w in _writes(world, "/files/delete")] == [{"root": "/plugins", "files": ["a.txt", "alt"]}]
    assert world.audit("files.delete")


def test_verschieben_mit_vollem_pfad(world):
    c = world.client(world.ids["olga"])
    r = _post(c, "/servers/srv-a/files/move", dir="/", names=["a.txt", "world"], target="/archiv/alt")
    assert r.status_code == 303 and "nach /archiv/alt verschoben" in unquote(r.headers["location"])
    assert [w[3] for w in _writes(world, "/files/rename")] == [{"root": "/", "files": [
        {"from": "/a.txt", "to": "/archiv/alt/a.txt"}, {"from": "/world", "to": "/archiv/alt/world"}]}]
    assert world.audit("files.move")


def test_verschieben_relativ_zum_aktuellen_ordner(world):
    c = world.client(world.ids["olga"])
    _post(c, "/servers/srv-a/files/move", dir="/plugins/alt", names=["x.jar"], target="..")
    _post(c, "/servers/srv-a/files/move", dir="/plugins", names=["y.jar"], target="deaktiviert")
    assert [w[3]["files"] for w in _writes(world, "/files/rename")] == [
        [{"from": "/plugins/alt/x.jar", "to": "/plugins/x.jar"}],
        [{"from": "/plugins/y.jar", "to": "/plugins/deaktiviert/y.jar"}]]


def test_unsinnige_ziele_werden_abgelehnt(world):
    c = world.client(world.ids["olga"])
    for names, target in ((["world"], "/world/region"), (["world"], "/world"), (["a.txt"], "/"), (["a.txt"], "  "),
                          (["../etc"], "/x"), ([], "/x")):
        r = _post(c, "/servers/srv-a/files/move", dir="/", names=names, target=target)
        assert r.status_code == 303, (names, target)
    assert _writes(world, "/files/rename") == []


def test_nur_mit_passendem_recht(world):
    uid = world.user("mod", grants={"srv-a": {"server.view", "sftp.view", "sftp.delete"}})
    c = world.client(uid)
    assert _post(c, "/servers/srv-a/files/move", dir="/", names=["a.txt"], target="/x").status_code == 403
    assert _post(c, "/servers/srv-a/files/delete-many", dir="/", names=["a.txt"]).status_code == 303
    page = c.get("/servers/srv-a/files").text
    assert "data-delete-many" in page and "data-move" not in page and 'name="names"' in page


def test_belegtes_ziel_gibt_verstaendliche_meldung(world, monkeypatch):
    async def busy(identifier, moves):
        raise PteroError("Pterodactyl antwortet mit HTTP 400: Cannot move or rename file, destination already exists..",
                         status=400)
    monkeypatch.setattr(world.ptero, "move", busy)
    c = world.client(world.ids["olga"])
    r = _post(c, "/servers/srv-a/files/move", dir="/", names=["a.txt"], target="/x")
    assert "nichts überschrieben" in unquote(r.headers["location"])
    assert world.audit("files.move")[-1]["result"] == "error"


def test_ordnerauswahl_listet_nur_ordner(world, monkeypatch):
    async def listing(identifier, directory):
        return [{"name": n, "is_file": f, "size": 6, "modified_at": None, "mimetype": ""}
                for n, f in (("world", False), ("a.txt", True), ("Backups", False))]
    monkeypatch.setattr(world.ptero, "list_files", listing)
    c = world.client(world.ids["olga"])
    r = c.get("/api/servers/srv-a/folders", params={"dir": "/plugins/../"})
    assert r.json() == {"dir": "/", "folders": ["Backups", "world"]}
    page = c.get("/servers/srv-a/files").text
    assert 'id="move-dialog"' in page and "data-move-open" in page


def test_belegter_name_wird_vorher_erkannt(world):
    """Pelican answers a rename onto an existing name with a bare HTTP 500 and Calagopus reports success without
    doing anything, so Hatchtower checks the folder first and asks the panel nothing."""
    c = world.client(world.ids["olga"])
    r = _post(c, "/servers/srv-a/files/rename", dir="/", old="a.txt.bak", new="a.txt")
    assert "gibt es in diesem Ordner schon" in unquote(r.headers["location"])
    world.ptero.missing_dirs.discard("/x")
    r = _post(c, "/servers/srv-a/files/move", dir="/plugins", names=["a.txt"], target="/x")
    assert "gibt es am Ziel schon" in unquote(r.headers["location"])
    assert _writes(world, "/files/rename") == []


def test_zip_auswahl_enthaelt_pfadliste():
    import asyncio
    import io
    import zipfile
    from hatchtower import zipdownload

    files = {"/world/playerdata": [{"name": "a.dat", "is_file": True, "size": 3}],
             "/world/playerdata/alt": [{"name": "b.dat", "is_file": True, "size": 3}]}

    class Client:
        async def list_files(self, ident, path):
            if path == "/world/playerdata":
                return files[path] + [{"name": "alt", "is_file": False, "size": 0}]
            return files[path]

        async def download_url(self, ident, path):
            return path

        async def stream_download(self, url):
            yield b"abc"

    async def go():
        client = Client()
        entries = await zipdownload.collect(client, "srv-a", "/world/playerdata", ["a.dat", "alt"])
        return b"".join([chunk async for chunk in zipdownload.stream_zip(
            client, "srv-a", entries, "Europe/Berlin", "Survival", "/world/playerdata")])

    with zipfile.ZipFile(io.BytesIO(asyncio.run(go()))) as zf:
        note = zf.read("HATCHTOWER-PFADE.txt").decode()
        assert zf.read("alt/b.dat") == b"abc"
    assert "Survival (srv-a)" in note and "Ausgewählt im Ordner: /world/playerdata" in note
    assert "\n/world/playerdata/a.dat\n" in note
    assert "\n/world/playerdata/alt/\n" in note and "b.dat" not in note
