"""File search: background index over all folders, search ranking, refresh from the file manager."""
from __future__ import annotations

import asyncio

from hatchtower import db, fileindex
from hatchtower.ptero import PteroError

TREE = {
    "/": [("server.properties", True), ("plugins", False), ("world", False), ("node_modules", False)],
    "/plugins": [("Essentials", False), ("essentials.jar", True)],
    "/plugins/Essentials": [("config.yml", True), ("kits.yml", True)],
    "/world": [("level.dat", True)],
    "/node_modules": [("left-pad", False)],
}


class Client:
    rate = None

    def __init__(self, tree, broken=()):
        self.tree, self.broken, self.calls = tree, set(broken), []

    async def list_files(self, ident, directory):
        self.calls.append(directory)
        if directory in self.broken:
            raise PteroError("kaputt")
        return [{"name": n, "is_file": f, "size": 10, "modified_at": None} for n, f in self.tree.get(directory, [])]


def crawl(client, ident="srv"):
    async def go():
        fileindex.ensure(client, ident, force=True)
        await fileindex._tasks[ident][1]
    asyncio.run(go())


def names(q, ident="srv"):
    return [r["path"] for r in fileindex.search(ident, q)[0]]


def setup_db(tmp_path, monkeypatch):
    db.init(tmp_path / "t.db")
    monkeypatch.setattr(fileindex, "MIN_INTERVAL", 0)


def test_index_findet_dateien_und_ordner_in_allen_ebenen(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    client = Client(TREE)
    crawl(client)
    st = fileindex.state("srv")
    assert st["status"] == "done" and st["dirs"] == 4
    assert "/node_modules" not in client.calls          # listed, but not descended into
    assert names("config.yml") == ["/plugins/Essentials/config.yml"]
    assert names("essentials")[:2] == ["/plugins/Essentials", "/plugins/essentials.jar"]
    assert sorted(names("*.yml")) == ["/plugins/Essentials/config.yml", "/plugins/Essentials/kits.yml"]
    assert names("essentials kits") == ["/plugins/Essentials/kits.yml"]
    assert names("100%") == [] and names("") == []


def test_kaputter_ordner_wird_uebersprungen(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    crawl(Client(TREE, broken={"/plugins"}))
    assert fileindex.state("srv")["status"] == "done"
    assert names("level") == ["/world/level.dat"] and names("config") == []


def test_neuer_lauf_entfernt_verschwundene_eintraege(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    crawl(Client(TREE))
    tree = dict(TREE, **{"/": [("server.properties", True), ("world", False)]})
    crawl(Client(tree))
    assert names("kits") == [] and names("level") == ["/world/level.dat"]


def test_geoeffneter_ordner_aktualisiert_den_index(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    crawl(Client(TREE))
    fileindex.note_listing("srv", "/plugins", [{"name": "neu.yml", "is_file": True, "size": 1, "modified_at": None}])
    assert names("neu.yml") == ["/plugins/neu.yml"]
    assert names("kits") == [] and names("essentials") == []      # removed folder with its content


def test_ohne_index_kein_eintrag_beim_auflisten(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    fileindex.note_listing("srv", "/", [{"name": "a", "is_file": True, "size": 1, "modified_at": None}])
    assert names("a") == [] and fileindex.state("srv") is None


def test_suche_ueber_die_route(world):
    c = world.client(world.ids["olga"])
    r = c.get("/api/servers/srv-a/files/search", params={"q": ""})
    assert r.status_code == 200 and r.json()["index"]["status"] in ("running", "done")
