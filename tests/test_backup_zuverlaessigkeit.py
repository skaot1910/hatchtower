"""Backups: Verhalten bei Fehlern, Prüfung der Backup-Datei, Aufbewahrung, verpasste Zeitpläne und Wiederherstellen."""
from __future__ import annotations

import asyncio
import zipfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from test_backups import TREE, TreePtero, env, run_all, runs, set_job, zip_names  # noqa: F401 (Fixture)

from hatchtower import backups, db, main
from hatchtower.ptero import PteroError


# -- Fehler beim Lesen von Wings ------------------------------------------------------------------

class BrokenWings(TreePtero):
    """Wings nicht erreichbar (z. B. Firewall vor Port 8080): Dateiliste geht, Downloads nicht."""

    async def stream_download(self, url):
        raise PteroError("Wings nicht erreichbar: ConnectError")
        yield b""          # noqa – macht die Funktion zum Generator


def test_wings_nicht_erreichbar_ist_ein_fehler_und_server_startet_wieder(env, monkeypatch):
    monkeypatch.setattr(backups, "RETRY_WAIT", 0)
    env.manager.state.ptero = broken = BrokenWings(main.settings)
    set_job(env.job_id, stop_mode="restart")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "error" and "Keine einzige Datei" in last["detail"]
    assert last["file"] is None and not list(env.target.rglob("*.part"))
    assert not [p for p in env.target.rglob("*.zip") if p.name != "alt.zip"]       # kein leeres ZIP liegen lassen
    assert broken.power_calls == [("srv-a", "stop"), ("srv-a", "start")]
    assert env.sent and env.sent[0][0] == "backup_failed"


def test_unerwarteter_fehler_startet_den_server_trotzdem_wieder(env, monkeypatch):
    async def kaputt(*a, **k):
        raise RuntimeError("Fehler im Code")
    monkeypatch.setattr(backups, "collect", kaputt)
    set_job(env.job_id, stop_mode="restart")
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "error" and "Unerwarteter Fehler" in last["detail"]
    assert env.ptero.power_calls == [("srv-a", "stop"), ("srv-a", "start")]


class FlakyWings(TreePtero):
    """Der erste Download jeder Datei scheitert sofort, der zweite klappt; level.dat bricht mittendrin ab."""

    def __init__(self, settings):
        super().__init__(settings)
        self.tries: dict[str, int] = {}

    async def stream_download(self, url):
        name = url.split("f=", 1)[1]
        self.tries[name] = self.tries.get(name, 0) + 1
        if self.tries[name] == 1:
            raise PteroError("Wings nicht erreichbar: ConnectTimeout")
        if name == "/world/level.dat":
            yield b"halb"
            raise PteroError("Wings nicht erreichbar: ReadTimeout")
        async for chunk in super().stream_download(url):
            yield chunk


def test_zweiter_versuch_und_abgebrochene_datei_werden_gemeldet(env, monkeypatch):
    monkeypatch.setattr(backups, "RETRY_WAIT", 0)
    env.manager.state.ptero = FlakyWings(main.settings)
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "partial"
    assert "level.dat: unvollständig" in last["detail"]
    with zipfile.ZipFile(env.target / last["file"]) as zf:
        assert zf.read("server.properties") == b"x" * 10            # zweiter Versuch hat geklappt
        assert "unvollständig" in zf.read("FEHLER.txt").decode()


# -- Prüfung der Backup-Datei ------------------------------------------------------------------------

def test_jedes_backup_wird_nach_dem_schreiben_geprueft(env):
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "ok" and last["verify_ok"] == 1 and last["verified_at"]


def test_kaputte_datei_wird_verworfen(env, monkeypatch):
    real = backups.verify_file
    monkeypatch.setattr(backups, "verify_file",
                        lambda target, rel, expected, rs=None: "Bad CRC-32" if rel.endswith(".part") else real(target, rel, expected, rs))
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    assert last["status"] == "error" and "fehlerhaft" in last["detail"] and last["file"] is None
    assert not [p for p in env.target.rglob("*") if p.is_file() and p.name != "alt.zip"]


def test_pruefung_erkennt_beschaedigte_datei(env, tmp_path):
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    target = backups.load_target(last["target_id"])
    assert backups.verify_file(target, last["file"], None) is None
    path = env.target / last["file"]
    data = bytearray(path.read_bytes())
    pos = data.index(b"x" * 100)               # mitten in r.0.0.mca ein Byte verändern
    data[pos] = ord("y")
    path.write_bytes(bytes(data))
    problem = backups.verify_file(target, last["file"], None)
    assert problem and "beschädigt" in problem
    path.write_bytes(bytes(data[: len(data) // 2]))  # abgeschnitten
    assert backups.verify_file(target, last["file"], None)


def test_pruefen_ueber_die_oberflaeche(env):
    c = env.world.client(env.world.ids["olga"])
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    last = runs()[-1]
    with db.connect() as con:
        con.execute("UPDATE backup_runs SET verified_at = NULL, verify_ok = NULL WHERE id = ?", (last["id"],))
    main.app.state.backups = env.manager

    async def go():
        env.manager.start_verify(backups_run(last["id"]), 1, "olga")
        await asyncio.gather(*env.manager._background)
    asyncio.run(go())
    row = backups_run(last["id"])
    assert row["verify_ok"] == 1 and row["verified_at"]
    assert env.world.audit("backup.verify")
    assert "geprüft" in c.get("/admin/backups").text


def backups_run(run_id):
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_runs WHERE id = ?", (run_id,)).fetchone()


# -- Aufbewahrung ------------------------------------------------------------------------------------

def _row(i, status, days_ago=0):
    finished = (datetime(2026, 10, 14, tzinfo=timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"id": i, "status": status, "finished_at": finished}


def test_aufbewahrung_loescht_nie_das_letzte_vollstaendige_backup():
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    rows = [_row(5, "partial"), _row(4, "partial", 1), _row(3, "ok", 2), _row(2, "ok", 3)]
    dropped = {r["id"] for r in backups.retention_drop(rows, 1, None, now)}
    assert dropped == {4, 2}                      # 5 = neuestes, 3 = neuestes vollständiges
    dropped = {r["id"] for r in backups.retention_drop(rows, None, 1, now)}
    assert 3 not in dropped and 5 not in dropped
    assert backups.retention_drop(rows, None, None, now) == []
    assert {r["id"] for r in backups.retention_drop([_row(2, "ok"), _row(1, "ok", 1)], 1, None, now)} == {1}


def test_backup_mit_fehlern_verdraengt_das_vollstaendige_nicht(env):
    set_job(env.job_id, keep_count=1)
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    good = runs()[-1]
    assert good["status"] == "ok"
    with db.connect() as con:                        # nächster Lauf mit einem fehlenden Pfad → „mit Fehlern“
        con.execute("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, 'srv-a', '/gibts-nicht')",
                    (env.job_id,))
        con.execute("DELETE FROM backup_job_items WHERE job_id = ? AND path = '/'", (env.job_id,))
        con.execute("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, 'srv-a', '/world')", (env.job_id,))
    env.manager.enqueue(backups.load_job(env.job_id))
    run_all(env.manager)
    assert runs()[-1]["status"] == "partial"
    assert backups_run(good["id"])["file"] and (env.target / good["file"]).is_file()


# -- Verpasste Zeitpläne ------------------------------------------------------------------------------

def _job(time="03:00", days="0123456", last_run=None, created="2026-10-01T00:00:00+00:00", updated=None):
    return {"time": time, "weekdays": days, "last_run": last_run, "created_at": created, "updated_at": updated}


def test_zeitplan_puenktlich_nachgeholt_und_nicht_doppelt():
    tz = ZoneInfo("Europe/Berlin")
    at = lambda h, m=0: datetime(2026, 10, 14, h, m, 30, tzinfo=tz)  # noqa: E731
    assert backups.due_slot(_job(), at(3, 0)) == (datetime(2026, 10, 14, 3, 0, tzinfo=tz), False)
    slot = backups.due_slot(_job(), at(5, 10))                       # Hatchtower war um 3 Uhr aus
    assert slot == (datetime(2026, 10, 14, 3, 0, tzinfo=tz), True)
    assert backups.due_slot(_job(last_run=slot[0].isoformat(timespec="minutes")), at(5, 11)) is None
    assert backups.due_slot(_job(), at(10, 0)) is None               # zu lange her
    assert backups.due_slot(_job(updated="2026-10-14T02:30:00+00:00"), at(5, 10)) is None   # erst danach eingestellt
    assert backups.due_slot(_job(days="1"), at(5, 10)) is None       # 14.10.2026 ist ein Mittwoch
    # kurz nach Mitternacht: der Termin von gestern 23:30 wird nachgeholt
    late = datetime(2026, 10, 15, 0, 20, tzinfo=tz)
    assert backups.due_slot(_job(time="23:30"), late) == (datetime(2026, 10, 14, 23, 30, tzinfo=tz), True)


def test_uebersprungener_zeitplan_wird_gemeldet(env):
    now = datetime.now(ZoneInfo(main.settings.timezone))
    set_job(env.job_id, weekdays=str(now.weekday()), time=now.strftime("%H:%M"))
    env.manager.enqueue(backups.load_job(env.job_id))      # der vorige Lauf hängt noch → Zeitplan kann nicht starten
    env.manager._tick()
    assert env.sent and env.sent[-1][0] == "backup_failed" and "nicht gestartet" in env.sent[-1][1]


# -- Wiederherstellen ---------------------------------------------------------------------------------

class RestorePtero(TreePtero):
    def __init__(self, settings, state="running"):
        super().__init__(settings, state)
        self.uploaded: dict[str, bytes] = {}
        self.folders: list[str] = []

    async def upload(self, identifier, directory, files):
        self._guard("POST")
        self.calls.append(("POST", f"upload:{identifier}", directory, [n for n, _ in files]))
        for name, f in files:
            self.uploaded[f"{directory.rstrip('/')}/{name}"] = f.read()

    async def create_folder(self, identifier, root, name):
        self._guard("POST")
        self.folders.append(f"{root.rstrip('/')}/{name}")


def make_stored_backup(env, entries: dict[str, bytes], status="ok", rel="Welt/Server_srv-a/test.zip",
                       ident="srv-a") -> int:
    path = env.target / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    with db.connect() as con:
        return con.execute(
            "INSERT INTO backup_runs (batch, job_id, job_name, identifier, server_name, target_id, kind, file, size,"
            " files, status, queued_at, finished_at) VALUES ('t', ?, 'Welt', ?, ?, ?, 'target', ?, ?,"
            " ?, ?, ?, ?)", (env.job_id, ident, f"Server {ident}", env.world.args["target_id"], rel,
                             path.stat().st_size, len(entries), status, db.utcnow(), db.utcnow())).lastrowid


def restore(env, run_id, mode="folder", stop=False):
    async def go():
        rid = env.manager.start_restore(backups_run(run_id), mode, stop, 1, "olga")
        await asyncio.gather(*env.manager._background)
        return rid
    rid = asyncio.run(go())
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_restores WHERE id = ?", (rid,)).fetchone()


@pytest.fixture
def renv(env):
    env.manager.state.ptero = env.ptero = RestorePtero(main.settings)
    return env


def test_wiederherstellen_in_eigenen_ordner(renv):
    run_id = make_stored_backup(renv, {"server.properties": b"motd=hallo", "world/level.dat": b"L" * 50,
                                       "world/region/r.0.0.mca": b"R" * 5000, "leer/": b"", "FEHLER.txt": b"x"})
    row = restore(renv, run_id)
    assert row["status"] == "ok" and row["mode"] == "folder" and row["files"] == 3, row["detail"]
    dest = row["dest"]
    assert dest.startswith("/hatchtower-restore/")
    assert renv.ptero.uploaded == {f"{dest}/server.properties": b"motd=hallo", f"{dest}/world/level.dat": b"L" * 50,
                                   f"{dest}/world/region/r.0.0.mca": b"R" * 5000}   # ohne FEHLER.txt
    assert renv.ptero.folders == [f"{dest}/leer"]
    assert renv.ptero.power_calls == []
    assert renv.world.audit("backup.restore")


def test_wiederherstellen_an_originalort_mit_stopp(renv):
    run_id = make_stored_backup(renv, {"server.properties": b"alt", "world/level.dat": b"L"})
    row = restore(renv, run_id, mode="overwrite", stop=True)
    assert row["status"] == "ok" and row["dest"] == "/"
    assert set(renv.ptero.uploaded) == {"/server.properties", "/world/level.dat"}
    assert renv.ptero.power_calls == [("srv-a", "stop"), ("srv-a", "start")]


def test_server_der_aus_war_bleibt_beim_wiederherstellen_aus(renv):
    renv.ptero.state = "offline"
    row = restore(renv, make_stored_backup(renv, {"a.txt": b"a"}), mode="overwrite", stop=True)
    assert row["status"] == "ok" and renv.ptero.power_calls == []


def test_beschaedigtes_backup_wird_nicht_zurueckgespielt(renv):
    run_id = make_stored_backup(renv, {"world/level.dat": b"L" * 2000})
    path = renv.target / backups_run(run_id)["file"]
    data = bytearray(path.read_bytes())
    data[48] ^= 0xFF
    path.write_bytes(bytes(data))
    row = restore(renv, run_id, mode="overwrite", stop=True)
    assert row["status"] == "error" and "nichts zurückgespielt" in row["detail"]
    assert renv.ptero.writes == [] and renv.ptero.power_calls == []


def test_unsichere_pfade_im_zip_werden_abgelehnt(renv):
    for evil in ("../ausbruch.txt", "/etc/passwd", "world/../../x", "C:/x"):
        rid = make_stored_backup(renv, {"ok.txt": b"1", evil: b"boese"})
        row = restore(renv, rid, mode="overwrite")
        assert row["status"] == "error" and "Unsicherer Pfad" in row["detail"], evil
    assert renv.ptero.uploaded == {}


def test_grosse_ordner_werden_in_pakete_geteilt(renv, monkeypatch):
    monkeypatch.setattr(backups, "RESTORE_BATCH_FILES", 3)
    files = {f"world/f{i}.dat": bytes([i]) * 10 for i in range(7)}
    restore(renv, make_stored_backup(renv, files))
    uploads = [c for c in renv.ptero.calls if c[1] == "upload:srv-a"]
    assert [len(c[3]) for c in uploads] == [3, 3, 1] and len(renv.ptero.uploaded) == 7


def test_fehler_beim_hochladen_werden_gemeldet(renv):
    async def upload(identifier, directory, files):
        raise PteroError("Wings antwortet mit HTTP 400", 400)
    renv.ptero.upload = upload
    row = restore(renv, make_stored_backup(renv, {"a.txt": b"a"}))
    assert row["status"] == "error" and "a.txt" in row["detail"]


def test_waehrend_der_wiederherstellung_kein_backup_und_kein_loeschen(renv):
    run_id = make_stored_backup(renv, {"a.txt": b"a"})
    rs = backups.RunState(77, "", renv.job_id, "Welt", "srv-a", "Server srv-a", "restore", "manual", 1, "olga")
    rs.source = run_id
    renv.manager.restores[77] = rs
    assert renv.manager.restore_busy("srv-a") and renv.manager.file_in_use(run_id)
    with pytest.raises(backups.BackupError):
        renv.manager.start_restore(backups_run(run_id), "folder", False, 1, "olga")
    renv.manager.enqueue(backups.load_job(renv.job_id))
    run_all(renv.manager)
    assert runs()[-1]["status"] == "error" and "Wiederherstellung" in runs()[-1]["detail"]
    main.app.state.backups = renv.manager
    c = renv.world.client(renv.world.ids["olga"])
    r = c.post(f"/admin/backups/runs/{run_id}/delete", data={"csrf": c.csrf})
    assert "gerade" in r.headers["location"] and backups_run(run_id)["file"]


def test_unterbrochene_wiederherstellung_nach_neustart(renv):
    with db.connect() as con:
        con.execute("INSERT INTO backup_restores (identifier, job_name, mode, dest, status, power_restore, started_at)"
                    " VALUES ('srv-a', 'Welt', 'overwrite', '/', 'running', 1, ?)", (db.utcnow(),))

    async def go():
        renv.manager.recover()
        await asyncio.gather(*renv.manager._background)
    asyncio.run(go())
    with db.connect() as con:
        row = con.execute("SELECT * FROM backup_restores").fetchone()
    assert row["status"] == "error" and "noch einmal" in row["detail"]
    assert renv.ptero.power_calls == [("srv-a", "start")]


def test_wiederherstellen_im_read_only_modus_gesperrt(renv):
    run_id = make_stored_backup(renv, {"a.txt": b"a"})
    renv.world.read_only()
    with pytest.raises(backups.BackupError):
        renv.manager.start_restore(backups_run(run_id), "folder", False, 1, "olga")
    c = renv.world.client(renv.world.ids["olga"])
    assert c.get(f"/admin/backups/runs/{run_id}/restore").status_code == 403


def test_originalort_braucht_bestaetigung_und_stoppen_die_steuerrechte(renv):
    main.app.state.backups = renv.manager
    run_id = make_stored_backup(renv, {"a.txt": b"a"})
    uid = renv.world.user("retter", grants={"srv-a": {"backups.restore", "backups.run", "backups.view"}})
    c = renv.world.client(uid)
    page = c.get(f"/admin/backups/runs/{run_id}/restore")
    assert page.status_code == 200 and "eigenen Ordner" in page.text
    r = c.post(f"/admin/backups/runs/{run_id}/restore", data={"csrf": c.csrf, "mode": "overwrite"})
    assert "best%C3%A4tigen" in r.headers["location"] and not renv.manager.restores
    r = c.post(f"/admin/backups/runs/{run_id}/restore", data={"csrf": c.csrf, "mode": "overwrite", "confirm": "1",
                                                               "stop": "1"})
    assert r.status_code == 403 and not renv.manager.restores


# -- Ergebnis fertiger Vorgänge und Sammelaktionen -----------------------------------------------------

def test_ergebnis_der_wiederherstellung_wird_gemeldet(renv):
    run_id = make_stored_backup(renv, {"server.properties": b"motd", "world/level.dat": b"L"})
    row = restore(renv, run_id)
    c = renv.world.client(renv.world.ids["olga"])
    page = c.get("/admin/backups", params={"done": f"restore-{row['id']},quatsch,verify-x"}).text
    assert "Wiederherstellung abgeschlossen" in page and "2 Dateien" in page and row["dest"] in page
    assert 'id="backup-results"' not in c.get("/admin/backups").text          # ohne „done“ keine Meldung
    # wer den Server nicht sieht, bekommt auch keine Meldung dazu
    uid = renv.world.user("nachbar", grants={"srv-b": {"backups.view"}})
    other = renv.world.client(uid).get("/admin/backups", params={"done": f"restore-{row['id']}"}).text
    assert "Wiederherstellung abgeschlossen" not in other


def test_fehlgeschlagene_pruefung_wird_gemeldet(renv):
    run_id = make_stored_backup(renv, {"a.txt": b"x"})
    with db.connect() as con:
        con.execute("UPDATE backup_runs SET verified_at = ?, verify_ok = 0, verify_detail = 'CRC falsch' WHERE id = ?",
                    (db.utcnow(), run_id))
    page = renv.world.client(renv.world.ids["olga"]).get("/admin/backups", params={"done": f"verify-{run_id}"}).text
    assert "Prüfung fehlgeschlagen" in page and "CRC falsch" in page


def test_mehrere_backups_auf_einmal_loeschen(renv):
    main.app.state.backups = renv.manager
    first = make_stored_backup(renv, {"a.txt": b"1"}, rel="Welt/Server_srv-a/eins.zip")
    second = make_stored_backup(renv, {"a.txt": b"2"}, rel="Welt/Server_srv-a/zwei.zip")
    c = renv.world.client(renv.world.ids["olga"])
    r = c.post("/admin/backups/runs/bulk", data={"csrf": c.csrf, "action": "delete", "run": [str(first), str(second)]})
    assert r.status_code == 303 and "2%20Backups%20gel" in r.headers["location"]
    assert backups_run(first) is None and backups_run(second) is None
    assert not (renv.target / "Welt/Server_srv-a/eins.zip").exists()
    assert len(renv.world.audit("backup.file_delete")) == 2


def test_sammelaktion_nur_wenn_alle_backups_erlaubt_sind(renv):
    main.app.state.backups = renv.manager
    mine = make_stored_backup(renv, {"a.txt": b"1"}, rel="Welt/Server_srv-a/eins.zip")
    other = make_stored_backup(renv, {"a.txt": b"2"}, rel="Welt/Server_srv-b/zwei.zip", ident="srv-b")
    uid = renv.world.user("mod", grants={"srv-a": {"backups.manage"}, "srv-b": {"backups.view"}})
    c = renv.world.client(uid)
    r = c.post("/admin/backups/runs/bulk", data={"csrf": c.csrf, "action": "delete", "run": [str(mine), str(other)]})
    assert r.status_code == 403
    assert backups_run(mine) is not None and backups_run(other) is not None        # auch das eigene bleibt
    page = c.get("/admin/backups").text
    assert f'name="run" value="{mine}"' in page and f'name="run" value="{other}"' not in page


def test_mehrere_backups_pruefen(renv, monkeypatch):
    main.app.state.backups = renv.manager
    ids = [make_stored_backup(renv, {"a.txt": b"1"}, rel=f"Welt/Server_srv-a/{n}.zip") for n in ("eins", "zwei")]
    started = []
    monkeypatch.setattr(renv.manager, "start_verify", lambda run, uid, name: started.append(run["id"]))
    c = renv.world.client(renv.world.ids["olga"])
    r = c.post("/admin/backups/runs/bulk", data={"csrf": c.csrf, "action": "verify", "run": [str(i) for i in ids]})
    assert r.status_code == 303 and "2%20Backups" in r.headers["location"]
    assert started == ids
