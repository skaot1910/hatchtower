"""Finer backup permissions (see, list, download, direct download, run), migration of existing grants,
job filter and the file browser on the job page."""
from __future__ import annotations

import re
import sqlite3

from hatchtower import backups, db, main
from hatchtower.db import MIGRATIONS
from hatchtower.permissions import load_grants


def page(c, url="/admin/backups") -> str:
    r = c.get(url)
    assert r.status_code == 200, r.status_code
    return r.text


def test_nur_herunterladen_ohne_starten(world):
    uid = world.user("spieler", grants={"srv-a": {"backups.download"}})
    assert load_grants(uid)["srv-a"] >= {"backups.view", "backups.list", "backups.download"}
    c = world.client(uid)
    html = page(c)
    rid, jid = world.args["run_id"], world.args["job_id"]
    assert 'href="/admin/backups/stored"' in html and 'href="/admin/backups/new"' not in html     # tabs
    assert f"/admin/backups/jobs/{jid}/run" not in html and f"/admin/backups/jobs/{jid}/download" not in html
    assert c.get(f"/admin/backups/runs/{rid}/download").status_code in (200, 303)
    assert f"/admin/backups/runs/{rid}/download" in page(c, "/admin/backups/stored")
    assert not world.audit("permission.denied")
    r = c.post(f"/admin/backups/jobs/{jid}/run", data={"csrf": c.csrf})
    assert r.status_code == 403
    assert not main.app.state.backups.runs                       # nothing queued


def test_nur_sehen_zeigt_keine_gespeicherten_backups(world):
    c = world.client(world.user("gast", grants={"srv-a": {"backups.view"}}))
    html = page(c)
    assert "Welt" in html                                         # the job
    assert 'href="/admin/backups/stored"' not in html
    assert c.get("/admin/backups/stored").status_code == 403
    assert c.get(f"/admin/backups/runs/{world.args['run_id']}/download").status_code == 403


def test_liste_ohne_download(world):
    c = world.client(world.user("leser", grants={"srv-a": {"backups.list"}}))
    html = page(c, "/admin/backups/stored")
    assert 'id="gespeichert"' in html and "Welt" in html
    assert f"/admin/backups/runs/{world.args['run_id']}/download" not in html


def test_direktdownload_nur_fuer_direktdownload_jobs(world):
    c = world.client(world.user("zipper", grants={"srv-a": {"backups.direct"}}))
    html = page(c)
    jid, djid = world.args["job_id"], world.args["direct_job_id"]
    assert f"/admin/backups/jobs/{djid}/download" in html
    assert f"/admin/backups/jobs/{jid}/download" not in html and f"/admin/backups/jobs/{jid}/run" not in html
    assert 'href="/admin/backups/downloads"' in html and 'href="/admin/backups/stored"' not in html
    downloads = page(c, "/admin/backups/downloads")
    assert f"/admin/backups/jobs/{djid}/download" in downloads and "Live" in downloads
    # the job with a target is not a direct download: running it live needs other rights
    assert c.post(f"/admin/backups/jobs/{jid}/download", data={"csrf": c.csrf}).status_code == 403


def test_job_mit_ziel_ausfuehren_und_herunterladen(world):
    c = world.client(world.user("sicherer", grants={"srv-a": {"backups.run", "backups.download"}}))
    html = page(c)
    jid, djid = world.args["job_id"], world.args["direct_job_id"]
    assert f"/admin/backups/jobs/{jid}/run" in html and f"/admin/backups/jobs/{jid}/download" in html
    assert f"/admin/backups/jobs/{djid}/download" not in html
    only_run = world.client(world.user("starter", grants={"srv-a": {"backups.run"}}))
    html = page(only_run)
    assert f"/admin/backups/jobs/{jid}/run" in html and f"/admin/backups/jobs/{jid}/download" not in html


def test_hinweis_wenn_kein_direktdownload_job_passt(world):
    c = world.client(world.user("test", grants={"srv-b": {"backups.direct"}}))
    assert "noch keinen Direktdownload-Job" in page(c)
    owner = page(world.client(world.ids["olga"]))
    assert "keinen Direktdownload-Job" in owner and "test: Server srv-b" in owner
    ok = world.client(world.user("passt", grants={"srv-a": {"backups.direct"}}))
    assert "noch keinen Direktdownload-Job" not in page(ok)


def test_direktdownloads_getrennt_von_gespeicherten(world):
    with db.connect() as con:
        con.execute("INSERT INTO backup_runs (batch, job_id, job_name, identifier, server_name, kind, status, queued_at,"
                    " finished_at, started_name) VALUES ('b9', ?, 'Live', 'srv-a', 'Server srv-a', 'download', 'ok', ?, ?,"
                    " 'zipper')", (world.args["direct_job_id"], db.utcnow(), db.utcnow()))
    owner = world.client(world.ids["olga"])
    stored = page(owner, "/admin/backups/stored")
    direct = page(owner, "/admin/backups/downloads")
    assert "Welt" in stored and "zipper" not in stored
    history = direct[direct.index('id="verlauf"'):]
    assert "Live" in history and "zipper" in history
    only_list = page(world.client(world.user("leser", grants={"srv-a": {"backups.list"}})), "/admin/backups/downloads")
    assert "zipper" not in only_list and 'id="direkt"' not in only_list


def test_migration_uebernimmt_bisherige_rechte(tmp_path):
    con = sqlite3.connect(tmp_path / "m.db")
    con.executescript("""
        CREATE TABLE perm_group_items (group_id INTEGER, permission TEXT, PRIMARY KEY (group_id, permission));
        CREATE TABLE user_server_permissions (user_id INTEGER, identifier TEXT, permission TEXT,
                                              PRIMARY KEY (user_id, identifier, permission));
        INSERT INTO perm_group_items VALUES (1, 'backups.view'), (2, 'backups.view'), (2, 'backups.run');
        INSERT INTO user_server_permissions VALUES (7, 'a', 'backups.view'), (7, 'b', 'backups.view'),
                                                   (7, 'b', 'backups.run'), (8, 'a', '-');
    """)
    con.executescript(MIGRATIONS[33])
    groups = {(g, p) for g, p in con.execute("SELECT * FROM perm_group_items")}
    users = {(u, i, p) for u, i, p in con.execute("SELECT * FROM user_server_permissions")}
    assert groups == {(1, "backups.view"), (1, "backups.list"), (2, "backups.view"), (2, "backups.list"),
                      (2, "backups.run"), (2, "backups.download"), (2, "backups.direct")}
    assert users == {(7, "a", "backups.view"), (7, "a", "backups.list"), (7, "b", "backups.view"),
                     (7, "b", "backups.list"), (7, "b", "backups.run"), (7, "b", "backups.download"),
                     (7, "b", "backups.direct"), (8, "a", "-")}


def add_job(name: str, target_id, idents: list[str], created_by: int = 1) -> int:
    with db.connect() as con:
        jid = con.execute("INSERT INTO backup_jobs (name, target_id, created_by, created_at) VALUES (?, ?, ?, ?)",
                          (name, target_id, created_by, db.utcnow())).lastrowid
        con.executemany("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, ?, '/')",
                        [(jid, i) for i in idents])
    return jid


def test_jobs_filtern(world):
    add_job("Nachts B", world.args["target_id"], ["srv-b"])
    add_job("Spielstand A", None, ["srv-a"])
    c = world.client(world.ids["olga"])
    html = page(c, "/admin/backups?jq=spiel")
    assert "Spielstand A" in html and "Nachts B" not in html
    html = page(c, "/admin/backups?jserver=srv-b")
    assert "Nachts B" in html and "Spielstand A" not in html
    html = page(c, "/admin/backups?jtarget=download")
    assert "Spielstand A" in html and "Nachts B" not in html
    assert "Kein Backup-Job passt zum Filter." in page(c, "/admin/backups?jq=gibtsnicht")


def test_dateibrowser_im_job(world):
    uid = world.user("planer", grants={"srv-a": {"backups.manage"}})
    jid = add_job("Auswahl", None, [], created_by=uid)
    c = world.client(uid)
    html = page(c, f"/admin/backups/jobs/{jid}")
    assert 'id="dateien"' in html and "Server wählen …" in html and "a.txt" not in html
    html = page(c, f"/admin/backups/jobs/{jid}?server=srv-a&dir=/")
    assert "a.txt.bak" in html and 'form="job-browse-form"' in html
    r = c.post(f"/admin/backups/jobs/{jid}/items",
               data={"csrf": c.csrf, "identifier": "srv-a", "dir": "/config", "names": ["a.txt", "../x"]})
    assert r.status_code == 303 and "server=srv-a" in r.headers["location"]
    assert backups.job_items(jid) == {"srv-a": ["/config/a.txt"]}
    # with exactly one server the browser opens it right away and marks what is already in the job
    html = page(c, f"/admin/backups/jobs/{jid}?dir=/config")
    assert "im Job" in html
    c.post(f"/admin/backups/jobs/{jid}/items", data={"csrf": c.csrf, "identifier": "srv-a", "dir": "/", "whole": "1"})
    assert sorted(backups.job_items(jid)["srv-a"]) == ["/", "/config/a.txt"]
    assert "schon komplett im Job" in page(c, f"/admin/backups/jobs/{jid}?dir=/config")


def test_dateibrowser_nur_fuer_eigene_server(world):
    uid = world.user("planer", grants={"srv-a": {"backups.manage"}, "srv-b": {"server.view"}})
    jid = add_job("Auswahl", None, [], created_by=uid)
    c = world.client(uid)
    html = page(c, f"/admin/backups/jobs/{jid}?server=srv-b")
    assert "a.txt" not in html and 'value="srv-b"' not in html
    r = c.post(f"/admin/backups/jobs/{jid}/items",
               data={"csrf": c.csrf, "identifier": "srv-b", "dir": "/", "names": ["a.txt"]})
    assert r.status_code == 403 and backups.job_items(jid) == {}


def tabs(html: str) -> list[str]:
    nav = html[html.index('<nav class="subnav"'):]
    return re.findall(r'<a href="([^"]+)"', nav[:nav.index("</nav>")])


def test_reiter_nur_mit_recht(world, monkeypatch):
    from hatchtower import routes_backup
    owner = world.client(world.ids["olga"])
    assert tabs(page(owner)) == ["/admin/backups/targets", "/admin/backups", "/admin/backups/new",
                                 "/admin/backups/stored", "/admin/backups/downloads", "/admin/backups/restores"]
    new = page(owner, "/admin/backups/new")
    assert "Für gespeicherte Backups stehen diese Backup-Ziele bereit: Platte." in new
    monkeypatch.setattr(routes_backup, "load_targets", lambda: [])
    assert "Es gibt noch kein Backup-Ziel." in page(owner, "/admin/backups/new")
    assert tabs(page(world.client(world.user("gast", grants={"srv-a": {"backups.view"}})))) == ["/admin/backups"]
    assert tabs(page(world.client(world.user("lader", grants={"srv-a": {"backups.download"}})))) == [
        "/admin/backups", "/admin/backups/stored", "/admin/backups/downloads", "/admin/backups/restores"]
    planer = world.client(world.user("planer", grants={"srv-a": {"automation.manage"}}))
    assert tabs(page(planer, "/admin/automation")) == ["/admin/automation", "/admin/automation/new",
                                                       "/admin/automation/crash"]


def test_download_verlauf_zaehlt_wiederholte_anfragen_einmal(world):
    from hatchtower import audit
    for _ in range(3):                  # Safari: one download, several requests
        audit.record("backup.file_download", username="lader", server="srv-a",
                     detail={"job": "Welt", "datei": "Welt/Server_srv-a/alt.zip"})
    html = page(world.client(world.user("lader", grants={"srv-a": {"backups.download"}})), "/admin/backups/downloads")
    history = html[html.index('id="verlauf"'):]
    assert history.count("alt.zip") == 1 and "Gespeichertes Backup" in history
    # the direct download right alone does not show who downloaded stored backups
    other = page(world.client(world.user("zipper", grants={"srv-a": {"backups.direct"}})), "/admin/backups/downloads")
    assert "alt.zip" not in other


def test_zeitplaene_filtern(world):
    from hatchtower import db as _db
    with _db.connect() as con:
        con.execute("INSERT INTO schedules (identifier, name, action, weekdays, time, enabled, created_by, created_at)"
                    " VALUES ('srv-b', 'Abendstart', 'start', '1', '18:00', 0, 1, ?)", (_db.utcnow(),))
    owner = world.client(world.ids["olga"])
    assert "Abendstart" in page(owner, "/admin/automation") and "Neustart" in page(owner, "/admin/automation")
    html = page(owner, "/admin/automation?state=off")
    assert "Abendstart" in html and ">Neustart<" not in html
    html = page(owner, "/admin/automation?server=srv-a")
    assert "Abendstart" not in html
    assert "Kein Zeitplan passt zum Filter." in page(owner, "/admin/automation?q=xyz")
