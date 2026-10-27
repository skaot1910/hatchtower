"""Backups: Einsammeln, ZIP, Stoppen/Starten, Aufbewahrung, Direktdownload und Wiederaufnahme nach Neustart."""
from __future__ import annotations

import asyncio
import io
import zipfile
from types import SimpleNamespace

import pytest

from conftest import FakePtero
from hatchtower import backups, db, main

TREE = {
    "/": [("server.properties", 10), ("world", None), ("logs", None), ("cache.tmp", 5)],
    "/world": [("level.dat", 20), ("region", None)],
    "/world/region": [("r.0.0.mca", 4096)],
    "/logs": [("latest.log", 30)],
}


class TreePtero(FakePtero):
    """Pterodactyl mit Dateibaum, Serverstatus und Power-Befehlen."""

    def __init__(self, settings, state="running"):
        super().__init__(settings)
        self.state = state
        self.power_calls: list[tuple[str, str]] = []
        self.commands: list[str] = []

    async def list_files(self, identifier, directory):
        self.calls.append(("GET", f"list:{identifier}:{directory}", None, None))
        return [{"name": n, "is_file": size is not None, "size": size or 0, "modified_at": "2026-10-01T12:00:00Z",
                 "mimetype": ""} for n, size in TREE.get(directory, [])]

    async def download_url(self, identifier, file):
        return f"https://wings.test/dl?f={file}"

    async def stream_download(self, url):
        name = url.split("f=", 1)[1]
        size = next(s for d, items in TREE.items() for n, s in items if f"{d.rstrip('/')}/{n}" == name)
        yield b"x" * size

    async def resources(self, identifier):
        return {"state": self.state}

    async def power(self, identifier, signal):
        self._guard("POST")
        self.power_calls.append((identifier, signal))
        self.state = "offline" if signal == "stop" else "running"

    async def command(self, identifier, command):
        self._guard("POST")
        self.commands.append(command)


@pytest.fixture
def env(world, monkeypatch, tmp_path):
    ptero = TreePtero(main.settings)
    notes: list[tuple[str, str]] = []
    state = SimpleNamespace(ptero=ptero, automation=SimpleNamespace(note_power=lambda i, s: notes.append((i, s))),
                            monitor=SimpleNamespace(follow=lambda *a, **k: None))
    manager = backups.BackupManager(state, main.vault, main.settings)
    monkeypatch.setattr(backups, "STOP_POLL", 0)
    sent = []
    monkeypatch.setattr(backups.notify, "emit", lambda vault, event, ident, detail="": sent.append((event, detail)))
    return SimpleNamespace(world=world, ptero=ptero, manager=manager, notes=notes, sent=sent,
                           job_id=int(world.args["job_id"]), target=tmp_path / "bk")


def set_job(job_id: int, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    with db.connect() as con:
        con.execute(f"UPDATE backup_jobs SET {sets} WHERE id = ?", (*cols.values(), job_id))


def run_all(manager) -> None:
    async def go():
        while manager._queue:
            rs = manager.runs[manager._queue.pop(0)]
            await manager._run_to_target(rs)
            manager._batch_check(rs)
    asyncio.run(go())


def runs() -> list:
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_runs ORDER BY id").fetchall()


def zip_names(path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


# -- Bausteine ------------------------------------------------------------------------------

def test_ausschluesse_und_pfade():
    assert backups.excluded("logs", True, ["logs/"])
    assert not backups.excluded("logs", False, ["logs/"])
    assert backups.excluded("world/cache.tmp", False, ["*.tmp"])
    assert backups.excluded("world/region/r.0.0.mca", False, ["world/region/*"])
    assert backups.minimal_paths(["/world", "/", "/logs/x"]) == ["/"]
    assert backups.minimal_paths(["/world/region", "/world", "/worldedit"]) == ["/world", "/worldedit"]
    assert backups.slug("Mein Server: Welt/1") == "Mein_Server_Welt_1"


# -- Lauf auf ein Ziel ------------------------------------------------------------------------

def test_ganzer_server_mit_ausschluessen(env):
    set_job(env.job_id, excludes="logs/\n*.tmp")
    job = backups.load_job(env.job_id)
    assert env.manager.enqueue(job, 1, "olga") == 1
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "ok", last["detail"]
    assert last["files"] == 3 and last["data_bytes"] == 10 + 20 + 4096
    path = env.target / last["file"]
    assert path.is_file() and not path.with_name(path.name + ".part").exists()
    assert zip_names(path) == {"server.properties", "world/", "world/level.dat", "world/region/",
                               "world/region/r.0.0.mca", "HATCHTOWER-PFADE.txt"}
    with zipfile.ZipFile(path) as zf:
        note = zf.read("HATCHTOWER-PFADE.txt").decode()
    assert "\n/\n" in note and "level.dat" not in note
    assert env.ptero.writes == [] and env.ptero.power_calls == []
    assert env.sent == []          # Meldung nur bei Fehlern (Standard)


def test_nur_ausgewaehlte_pfade(env):
    with db.connect() as con:
        con.execute("DELETE FROM backup_job_items WHERE job_id = ?", (env.job_id,))
        con.executemany("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, 'srv-a', ?)",
                        [(env.job_id, "/world"), (env.job_id, "/server.properties"), (env.job_id, "/gibts-nicht")])
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "partial" and "gibts-nicht" in last["detail"]
    names = zip_names(env.target / last["file"])
    assert "world/region/r.0.0.mca" in names and "server.properties" in names and "FEHLER.txt" in names
    with zipfile.ZipFile(env.target / last["file"]) as zf:
        note = zf.read("HATCHTOWER-PFADE.txt").decode()
    assert "\n/world/\n" in note and "\n/server.properties\n" in note and "->" not in note
    assert "level.dat" not in note and "gibts-nicht" not in note
    assert not any(n.startswith("logs") for n in names)
    assert env.sent and env.sent[0][0] == "backup_failed"


def test_stoppen_und_wieder_starten(env):
    set_job(env.job_id, stop_mode="restart", warn_minutes=1, warn_command="say Backup in {minuten} min")
    env.manager._sleep = _fast_sleep(env.manager)
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert runs()[-1]["status"] == "ok"
    assert env.ptero.commands == ["say Backup in 1 min"]
    assert env.ptero.power_calls == [("srv-a", "stop"), ("srv-a", "start")]
    assert ("srv-a", "stop") in env.notes and ("srv-a", "start") in env.notes   # kein „Absturz“
    assert runs()[-1]["power_restore"] == 0


def test_server_der_aus_war_bleibt_aus(env):
    env.ptero.state = "offline"
    set_job(env.job_id, stop_mode="restart")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert runs()[-1]["status"] == "ok" and env.ptero.power_calls == []


def test_stoppen_und_aus_lassen(env):
    set_job(env.job_id, stop_mode="stop")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert env.ptero.power_calls == [("srv-a", "stop")]


def test_stoppen_im_read_only_modus_schlaegt_fehl(env):
    env.world.read_only()
    set_job(env.job_id, stop_mode="restart")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "error" and "Read-Only" in last["detail"]
    assert env.ptero.power_calls == []


def test_aufbewahrung_pro_server(env):
    set_job(env.job_id, keep_count=2)
    for _ in range(3):
        env.manager.enqueue(backups.load_job(env.job_id))
        run_all(env.manager)
    stored = [r for r in runs() if r["file"]]
    assert len(stored) == 2
    files = list((env.target / "Welt" / "Server_srv-a").glob("*.zip"))
    assert len(files) == 2
    assert not (env.target / "Welt" / "Server_srv-a" / "alt.zip").exists()


def test_nicht_doppelt_und_abbrechen_in_der_warteschlange(env):
    job = backups.load_job(env.job_id)
    env.manager.enqueue(job)
    with pytest.raises(backups.BackupError):
        env.manager.enqueue(job)
    run_id = env.manager._queue[0]
    assert env.manager.cancel(run_id)
    assert runs()[-1]["status"] == "cancelled" and not env.manager.runs


def test_geplanter_lauf_ohne_recht_des_erstellers(env):
    uid = env.world.user("planer", grants={"srv-a": {"backups.manage"}})
    set_job(env.job_id, created_by=uid)
    env.world.grant(uid, {"srv-a": {"backups.view"}})
    env.manager.enqueue(backups.load_job(env.job_id), uid, "Backup", trigger="schedule")
    run_all(env.manager)
    assert runs()[-1]["status"] == "denied"


def test_zeitplan_startet_zur_uhrzeit(env, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo(main.settings.timezone))
    set_job(env.job_id, weekdays=str(now.weekday()), time=now.strftime("%H:%M"))
    env.manager._tick()
    assert len(env.manager._queue) == 1
    env.manager._tick()                       # dieselbe Minute nicht noch einmal
    assert len(env.manager._queue) == 1


def test_wiederaufnahme_nach_neustart(env):
    set_job(env.job_id, stop_mode="restart")
    with db.connect() as con:
        con.execute("INSERT INTO backup_runs (batch, job_id, job_name, identifier, target_id, status, power_restore,"
                    " file, queued_at) VALUES ('x', ?, 'Welt', 'srv-a', ?, 'running', 1, 'Welt/x.zip', ?)",
                    (env.job_id, env.world.args["target_id"], db.utcnow()))
    (env.target / "Welt" / "x.zip.part").write_bytes(b"halb")

    async def go():
        env.manager.recover()
        await asyncio.gather(*env.manager._background)
    asyncio.run(go())
    assert runs()[-1]["status"] == "error" and runs()[-1]["file"] is None
    assert not (env.target / "Welt" / "x.zip.part").exists()
    assert env.ptero.power_calls == [("srv-a", "start")]


# -- Direktdownload ------------------------------------------------------------------------------

def test_direktdownload_mehrere_server(env):
    with db.connect() as con:
        con.execute("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, 'srv-b', '/world')",
                    (env.job_id,))
    set_job(env.job_id, target_id=None)
    job = backups.load_job(env.job_id)
    states = env.manager.begin_download(job, 1, "olga")
    with pytest.raises(backups.BackupError):
        env.manager.begin_download(job, 1, "olga")      # einer pro Benutzer

    async def go():
        return b"".join([chunk async for chunk in env.manager.stream_download(job, states, 1)])
    data = asyncio.run(go())
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert "Server_srv-a/world/region/r.0.0.mca" in names and "Server_srv-a/logs/latest.log" in names
    assert "Server_srv-b/world/level.dat" in names and not any(n.startswith("Server_srv-b/logs") for n in names)
    assert [r["status"] for r in runs()[-2:]] == ["ok", "ok"]
    assert 1 not in env.manager._downloads and not env.manager.runs


def test_direktdownload_ueber_die_oberflaeche(world):
    c = world.client(world.ids["olga"])
    r = c.post(f"/admin/backups/jobs/{world.args['job_id']}/download", data={"csrf": c.csrf})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert set(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == {"a.txt", "a.txt.bak", "HATCHTOWER-PFADE.txt"}
    assert world.audit("backup.download")


# -- Ziele ---------------------------------------------------------------------------------------

def test_ziel_anlegen_nur_in_erlaubten_ordnern(world, tmp_path):
    c = world.client(world.ids["olga"])
    r = c.post("/admin/backups/targets", data={"csrf": c.csrf, "name": "Daten", "mount": "data", "subdir": "nacht"})
    assert "angelegt" in r.headers["location"]
    assert (tmp_path / "backups" / "nacht").is_dir()
    r = c.post("/admin/backups/targets", data={"csrf": c.csrf, "name": "Böse", "mount": "/etc", "subdir": ""})
    assert "Speicherort" in r.headers["location"] or "Speicherort" in r.text
    r = c.post("/admin/backups/targets", data={"csrf": c.csrf, "name": "Raus", "mount": "data", "subdir": "../../x"})
    with db.connect() as con:
        paths = [row["path"] for row in con.execute("SELECT path FROM backup_targets")]
    assert all(str(tmp_path) in p for p in paths)
    assert backups.allowed_path(tmp_path / "backups", tmp_path)
    assert not backups.allowed_path(tmp_path.parent, tmp_path)


def test_ziel_in_benutzung_nicht_loeschbar(world):
    c = world.client(world.ids["olga"])
    r = c.post(f"/admin/backups/targets/{world.args['target_id']}/delete", data={"csrf": c.csrf})
    assert "benutzt" in r.headers["location"]
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM backup_targets").fetchone()[0] == 1


def _fast_sleep(manager):
    async def sleep(rs, seconds):
        rs.check_cancel()
    return sleep


def test_seiten_nach_einem_lauf(env):
    env.manager.enqueue(backups.load_job(env.job_id), 1, "olga")
    run_all(env.manager)
    last = backups.load_job(env.job_id)["last_result"]
    assert last.endswith(" gesichert") and "nicht" not in last          # ein Server: ohne „1 von 1“
    c = env.world.client(env.world.ids["olga"])
    assert c.get(f"/admin/backups/jobs/{env.job_id}").status_code == 200
    page = c.get("/admin/backups")
    assert page.status_code == 200 and last in page.text


# -- SMB-Freigabe (nachgebildet im Testordner) ------------------------------------------------------

@pytest.fixture
def nas(monkeypatch, tmp_path):
    import os

    import smbclient
    import smbclient.path
    from smbprotocol.exceptions import SMBException
    root = tmp_path / "nas"
    (root / "backup").mkdir(parents=True)

    def local(unc, kw):
        if kw.get("username") != "hatch" or kw.get("password") != "Geheim-123":
            raise SMBException("STATUS_LOGON_FAILURE")
        parts = unc.lstrip("\\").split("\\")
        assert parts[0] == "nas.test"
        if not (root / parts[1]).is_dir():
            raise SMBException("STATUS_BAD_NETWORK_NAME")
        return root.joinpath(parts[1], *parts[2:])

    monkeypatch.setattr(smbclient, "makedirs", lambda p, exist_ok=False, **kw: local(p, kw).mkdir(parents=True, exist_ok=exist_ok))
    monkeypatch.setattr(smbclient, "open_file", lambda p, mode="rb", **kw: open(local(p, kw), mode))
    monkeypatch.setattr(smbclient, "replace", lambda a, b, **kw: os.replace(local(a, kw), local(b, kw)))
    monkeypatch.setattr(smbclient, "stat", lambda p, **kw: os.stat(local(p, kw)))
    monkeypatch.setattr(smbclient, "remove", lambda p, **kw: os.remove(local(p, kw)))
    monkeypatch.setattr(smbclient, "stat_volume", lambda p, **kw: SimpleNamespace(caller_available_size=10 ** 12))
    monkeypatch.setattr(smbclient, "reset_connection_cache", lambda **kw: None)
    monkeypatch.setattr(smbclient.path, "exists", lambda p, **kw: local(p, kw).exists())
    return root / "backup"


def smb_form(c, **extra):
    return {"csrf": c.csrf, "name": "NAS", "kind": "smb", "smb_host": "nas.test", "smb_share": "backup",
            "subdir": "hatchtower", "smb_user": "hatch", "smb_password": "Geheim-123", **extra}


def test_smb_ziel_wird_vor_dem_speichern_geprueft(world, nas):
    c = world.client(world.ids["olga"])
    r = c.post("/admin/backups/targets", data=smb_form(c, smb_password="falsch"))
    assert "Passwort%20falsch" in r.headers["location"]
    r = c.post("/admin/backups/targets", data=smb_form(c, smb_share="gibtsnicht"))
    assert "Freigabe%20gibt%20es" in r.headers["location"]
    r = c.post("/admin/backups/targets", data=smb_form(c, smb_host="evil host/"))
    assert "Server" in r.headers["location"] or "IP" in r.headers["location"]
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM backup_targets WHERE kind = 'smb'").fetchone()[0] == 0
    r = c.post("/admin/backups/targets", data=smb_form(c))
    assert "angelegt" in r.headers["location"]
    assert (nas / "hatchtower").is_dir() and not list((nas / "hatchtower").iterdir())   # Testdatei wieder weg
    with db.connect() as con:
        row = con.execute("SELECT * FROM backup_targets WHERE kind = 'smb'").fetchone()
    assert row["smb_password_enc"] and "Geheim-123" not in row["smb_password_enc"]
    assert main.vault.decrypt(row["smb_password_enc"]) == "Geheim-123"
    assert "Geheim-123" not in c.get("/admin/backups/targets").text
    # Bearbeiten ohne Passwort behält das gespeicherte
    r = c.post(f"/admin/backups/targets/{row['id']}", data=smb_form(c, name="NAS 2", smb_password=""))
    assert "gespeichert" in r.headers["location"]
    with db.connect() as con:
        new = con.execute("SELECT * FROM backup_targets WHERE id = ?", (row["id"],)).fetchone()
    assert new["name"] == "NAS 2" and new["smb_password_enc"] == row["smb_password_enc"]
    assert all("Geheim" not in (a["detail"] or "") for a in world.audit())


def test_backup_auf_smb_und_wieder_herunterladen(env, nas):
    c = env.world.client(env.world.ids["olga"])
    c.post("/admin/backups/targets", data=smb_form(c))
    with db.connect() as con:
        tid = con.execute("SELECT id FROM backup_targets WHERE kind = 'smb'").fetchone()[0]
    set_job(env.job_id, target_id=tid, keep_count=1)
    for _ in range(2):
        env.manager.enqueue(backups.load_job(env.job_id))
        run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "ok" and last["target_id"] == tid
    stored = list((nas / "hatchtower").rglob("*.zip"))
    assert len(stored) == 1 and not list((nas / "hatchtower").rglob("*.part"))      # Aufbewahrung: nur 1
    r = c.get(f"/admin/backups/runs/{last['id']}/download")
    assert r.status_code == 200 and "world/level.dat" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert r"\\nas.test\backup\hatchtower" in c.get("/admin/backups/targets").text


def test_letzte_pruefung_wird_gemerkt(world, nas):
    c = world.client(world.ids["olga"])
    tid = world.args["target_id"]
    c.post(f"/admin/backups/targets/{tid}/test", data={"csrf": c.csrf})
    with db.connect() as con:
        row = con.execute("SELECT * FROM backup_targets WHERE id = ?", (tid,)).fetchone()
    assert row["checked_at"] and row["check_ok"] == 1 and row["check_free"] > 0
    c.post("/admin/backups/targets", data=smb_form(c))
    with db.connect() as con:
        smb = con.execute("SELECT * FROM backup_targets WHERE kind = 'smb'").fetchone()
    assert smb["check_ok"] == 1 and smb["check_free"] == 10 ** 12
    with db.connect() as con:
        con.execute("UPDATE backup_targets SET smb_password_enc = ? WHERE id = ?", (main.vault.encrypt("falsch"), smb["id"]))
    c.post(f"/admin/backups/targets/{smb['id']}/test", data={"csrf": c.csrf})
    with db.connect() as con:
        smb = con.execute("SELECT * FROM backup_targets WHERE id = ?", (smb["id"],)).fetchone()
    assert smb["check_ok"] == 0 and "Passwort falsch" in smb["check_error"] and smb["check_free"] == 10 ** 12
    page = c.get("/admin/backups/targets").text
    assert "Letzte Prüfung" in page and "fehlgeschlagen: " in page and "Schreiben und Löschen klappt" in page


# -- Vorher speichern, ohne zu stoppen ---------------------------------------------------------------

def test_vorher_speichern_ohne_stopp(env, monkeypatch):
    monkeypatch.setattr(backups, "SAVE_WAIT_DEFAULT", 0)
    set_job(env.job_id, save_command="save-all")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert runs()[-1]["status"] == "ok"
    assert env.ptero.commands == ["save-all"] and env.ptero.power_calls == []
    assert env.world.audit("backup.save")[-1]["result"] == "ok"


def test_vorher_speichern_nur_wenn_der_server_laeuft(env, monkeypatch):
    monkeypatch.setattr(backups, "SAVE_WAIT_DEFAULT", 0)
    env.ptero.state = "offline"
    set_job(env.job_id, save_command="save-all")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert runs()[-1]["status"] == "ok" and env.ptero.commands == []


def test_vorher_speichern_entfaellt_beim_stoppen(env):
    set_job(env.job_id, stop_mode="stop", save_command="save-all")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert env.ptero.commands == [] and env.ptero.power_calls == [("srv-a", "stop")]


def test_vorher_speichern_im_read_only_modus_sichert_trotzdem(env):
    env.world.read_only()
    set_job(env.job_id, save_command="save-all")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert runs()[-1]["status"] == "ok" and env.ptero.commands == []
    assert env.world.audit("backup.save")[-1]["result"] == "error"
