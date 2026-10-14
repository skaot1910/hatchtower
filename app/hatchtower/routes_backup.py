"""Seiten und Aktionen rund um Backups (siehe backups.py).

Wer die Seite sieht, hängt am Recht „Backups sehen“ für mindestens einen Server. Jobs zeigt sie nur,
wenn das jeweilige Recht für alle Server des Jobs gilt; „Alle Server, auch künftige“ ist Owner-Sache,
ebenso die Backup-Ziele (Ordner und Zugangsdaten).
"""
from __future__ import annotations

import asyncio
import posixpath
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import audit, backups, db, i18n, security
from .backups import BackupError, has, user_may
from .main import (app, back, check_csrf, check_csrf_header, clean_path, current_user, deny, files_back,
                   get_server_or_deny, ip_of, render, require, require_owner, server_names, settings, valid_name,
                   vault)
from .views import fmt_dt, fmt_size

MAX_ITEMS = 500
MAX_EXCLUDES = 4000


def manager() -> backups.BackupManager:
    return app.state.backups


def require_backups(request: Request, key: str = "backups.view"):
    """Owner oder Benutzer mit diesem Backup-Recht für mindestens einen Server."""
    user = current_user(request)
    if not user.can_any(key):
        deny(request, user, key)
    return user


def own_job(request: Request, user, job_id: int, key: str):
    job = backups.load_job(job_id)
    if job is not None and not user_may(user, job, key):
        idents = backups.job_idents(job)
        deny(request, user, key, idents[0] if len(idents) == 1 else None)
    return job


def job_back(job_id: int, message: str | None = None):
    return back(f"/admin/backups/jobs/{job_id}", message)


def audit_user(request: Request, user, action: str, **kw) -> None:
    audit.record(action, user_id=user.id, username=user.username, ip=ip_of(request), **kw)


def run_problems(user, job) -> list[str]:
    """Was gegen das Starten durch diesen Benutzer spricht (Steuerrechte bei Jobs mit Stopp)."""
    return backups.stop_problems(user, backups.job_idents(job), job["stop_mode"], job["warn_command"],
                                 server_names())


def load_targets() -> list:
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_targets ORDER BY name COLLATE NOCASE").fetchall()


def target_view(row) -> dict:
    """Für die Liste der Ziele. Bei SMB wird nichts im Netz abgefragt (die Seite soll nicht hängen),
    freien Platz zeigt dort „Testen“."""
    free, system_disk = None, False
    if row["kind"] == "local":
        path = Path(row["path"])
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            pass
        system_disk = backups.same_device_as_root(path) if path.exists() else False
    with db.connect() as con:
        jobs = con.execute("SELECT COUNT(*) FROM backup_jobs WHERE target_id = ?", (row["id"],)).fetchone()[0]
    if free is None and row["check_free"] is not None:
        free = row["check_free"]           # SMB: Stand der letzten Prüfung
    return {"t": row, "where": backups.target_where(row), "free": fmt_size(free) if free is not None else None,
            "checked": fmt_dt(row["checked_at"], settings.timezone) if row["checked_at"] else None,
            "jobs": jobs, "system_disk": system_disk, "has_password": bool(row["smb_password_enc"])}


# -- Übersicht ---------------------------------------------------------------------------

@app.get("/admin/backups", response_class=HTMLResponse)
async def backups_page(request: Request):
    user = require_backups(request)
    names = server_names()
    targets = load_targets()
    target_by_id = {t["id"]: t for t in targets}
    with db.connect() as con:
        jobs = con.execute("SELECT * FROM backup_jobs ORDER BY name COLLATE NOCASE").fetchall()
        runs = con.execute("SELECT * FROM backup_runs WHERE status NOT IN ('queued', 'running')"
                           " ORDER BY id DESC LIMIT 300").fetchall()
    job_list = []
    for job in jobs:
        if not user_may(user, job, "backups.view"):
            continue
        items = backups.job_items(job["id"])
        servers = [i18n.t("Alle Server, auch künftige")] if job["all_servers"] else \
            [names.get(i, i18n.t("{ident} (gelöscht)", ident=i)) for i in items]
        job_list.append({
            "j": job, "servers": servers,
            "paths": sum(len(p) for p in items.values()),
            "target": backups.target_label(target_by_id.get(job["target_id"])) if job["target_id"]
            else i18n.t("Direktdownload"),
            "when": backups.describe_schedule(job),
            "stop": i18n.t(backups.STOP_MODES.get(job["stop_mode"], "")),
            "can_edit": user_may(user, job, "backups.manage"),
            "can_run": user_may(user, job, "backups.run") and not run_problems(user, job),
            "active": manager().job_active(job["id"]),
        })
    run_list = [{
        "r": r, "at": fmt_dt(r["finished_at"] or r["queued_at"], settings.timezone),
        "status": i18n.t(backups.STATUS_LABELS.get(r["status"], r["status"])),
        "size": fmt_size(r["size"]) if r["size"] else "",
        "data": fmt_size(r["data_bytes"]) if r["data_bytes"] else "",
        "can_download": bool(r["file"]) and has(user, r["identifier"], "backups.run"),
        "can_delete": bool(r["file"]) and has(user, r["identifier"], "backups.manage"),
    } for r in runs if has(user, r["identifier"], "backups.view")][:100]
    owner_data = {}
    if user.is_owner:
        uid, gid = backups.service_ids()
        mounts = backups.list_mounts()
        for m in mounts:
            m["free_text"] = fmt_size(m["free"]) if m["free"] is not None else ""
        owner_data = {"targets_view": [target_view(t) for t in targets], "mounts": mounts,
                      "data_dir": str(settings.data_dir / "backups"), "host_uid": 100000 + (uid or 0),
                      "host_gid": 100000 + (gid or 0)}
    return render(request, "backups.html", jobs=job_list, runs=run_list, targets=targets,
                  active=manager().active_views(lambda i: has(user, i, "backups.view")),
                  can_create=user.can_any("backups.manage"), **owner_data)


@app.get("/api/backups/status")
async def backups_status(request: Request):
    user = require_backups(request)
    active = manager().active_views(lambda i: has(user, i, "backups.view"))
    for a in active:
        a["can_cancel"] = has(user, a["ident"], "backups.run")
    return {"active": active}


# -- Jobs ------------------------------------------------------------------------------------

def create_job(user, name: str, target_id: int | None) -> int:
    now = db.utcnow()
    with db.connect() as con:
        return con.execute(
            "INSERT INTO backup_jobs (name, target_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, target_id, user.id, now, now)).lastrowid


def valid_target(value: str) -> tuple[bool, int | None]:
    if not value:
        return True, None
    if not value.isdigit():
        return False, None
    return backups.load_target(int(value)) is not None, int(value)


@app.post("/admin/backups/jobs")
async def backup_job_create(request: Request, name: str = Form(""), target_id: str = Form("")):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    name = " ".join(name.split())
    ok, tid = valid_target(target_id)
    if not name or len(name) > 80 or not ok:
        return back("/admin/backups", "Bitte Name (1–80 Zeichen) und Ziel angeben")
    job_id = create_job(user, name, tid)
    audit_user(request, user, "backup.job.create", detail={"job": name, "ziel": tid or "download"})
    return job_back(job_id, "Backup-Job angelegt. Jetzt Server und Ordner hinzufügen.")


@app.get("/admin/backups/jobs/{job_id}", response_class=HTMLResponse)
async def backup_job_page(request: Request, job_id: int):
    user = require_backups(request, "backups.manage")
    job = own_job(request, user, job_id, "backups.manage")
    if job is None:
        return back("/admin/backups")
    names = server_names()
    items = backups.job_items(job_id)
    item_view = [{"ident": i, "server": names.get(i, i18n.t("{ident} (gelöscht)", ident=i)), "paths": paths}
                 for i, paths in sorted(items.items(), key=lambda kv: names.get(kv[0], kv[0]).lower())]
    addable = {i: n for i, n in names.items() if has(user, i, "backups.manage")}
    return render(request, "backup_job.html", job=job, items=item_view, targets=load_targets(),
                  servers=sorted(addable.items(), key=lambda kv: kv[1].lower()),
                  weekdays=[i18n.t(d) for d in backups.WEEKDAYS],
                  stop_modes={k: i18n.t(v) for k, v in backups.STOP_MODES.items()},
                  notify_modes={k: i18n.t(v) for k, v in backups.NOTIFY_MODES.items()},
                  keep_counts=backups.KEEP_COUNTS, keep_days=backups.KEEP_DAYS,
                  can_run=user_may(user, job, "backups.run") and not run_problems(user, job),
                  active=manager().job_active(job_id))


def _int_choice(value: str, choices) -> tuple[bool, int | None]:
    value = value.strip()
    if not value:
        return True, None
    return (value.isdigit() and int(value) in choices), (int(value) if value.isdigit() else None)


@app.post("/admin/backups/jobs/{job_id}")
async def backup_job_save(request: Request, job_id: int, name: str = Form(""), target_id: str = Form(""),
                          all_servers: str = Form(""), excludes: str = Form(""), schedule: str = Form(""),
                          time_: str = Form("", alias="time"), stop_mode: str = Form("none"),
                          warn_minutes: str = Form(""), warn_command: str = Form(""), keep_count: str = Form(""),
                          keep_days: str = Form(""), compress: str = Form(""), notify: str = Form("errors"),
                          enabled: str = Form("")):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    job = own_job(request, user, job_id, "backups.manage")
    if job is None:
        return back("/admin/backups")
    form = await request.form()
    problems = []
    name = " ".join(name.split())
    if not name or len(name) > 80:
        problems.append("Name (1–80 Zeichen)")
    ok, tid = valid_target(target_id)
    if not ok:
        problems.append("Ziel")
    all_flag = 1 if all_servers and user.is_owner else 0
    days = ""
    clock = None
    if schedule and tid:
        days = "".join(sorted(d for d in form.getlist("weekdays") if isinstance(d, str) and len(d) == 1 and d in "0123456"))
        if not days:
            problems.append("mindestens einen Wochentag")
        try:
            hour, minute = (int(x) for x in time_.split(":"))
            assert 0 <= hour < 24 and 0 <= minute < 60
            clock = f"{hour:02d}:{minute:02d}"
        except (ValueError, AssertionError):
            problems.append("Uhrzeit (HH:MM)")
    if stop_mode not in backups.STOP_MODES:
        problems.append("Server vorher")
    warn_command = warn_command.strip()
    warn = None
    if warn_command and stop_mode != "none":
        if not warn_minutes.strip().isdigit() or not 1 <= int(warn_minutes) <= 60:
            problems.append("Vorwarnung 1–60 Minuten")
        else:
            warn = int(warn_minutes)
        if len(warn_command) > 300 or "\n" in warn_command:
            problems.append("Vorwarn-Befehl")
    else:
        warn_command = ""
    ok_count, k_count = _int_choice(keep_count, backups.KEEP_COUNTS)
    ok_days, k_days = _int_choice(keep_days, backups.KEEP_DAYS)
    if not ok_count or not ok_days:
        problems.append("Aufbewahrung")
    if notify not in backups.NOTIFY_MODES:
        problems.append("Meldung")
    excludes = "\n".join(line.strip() for line in excludes.replace("\r", "").split("\n") if line.strip())
    if len(excludes) > MAX_EXCLUDES:
        problems.append("Ausschlüsse (zu lang)")
    if not problems and stop_mode in backups.STOP_MODES:
        idents = list(backups.present_servers()) if all_flag else list(backups.job_items(job_id))
        missing = backups.stop_problems(user, idents, stop_mode, warn_command, server_names())
        if missing:
            problems.append(i18n.t("Server vorher stoppen braucht: {list}", list=", ".join(missing[:5])))
    if problems:
        return job_back(job_id, i18n.t("Bitte prüfen: {list}", list=", ".join(i18n.t(x) for x in problems)))
    with db.connect() as con:
        con.execute(
            "UPDATE backup_jobs SET name = ?, target_id = ?, all_servers = ?, excludes = ?, weekdays = ?, time = ?,"
            " stop_mode = ?, warn_minutes = ?, warn_command = ?, keep_count = ?, keep_days = ?, compress = ?,"
            " notify = ?, enabled = ?, updated_at = ? WHERE id = ?",
            (name, tid, all_flag, excludes or None, days, clock, stop_mode, warn, warn_command or None, k_count, k_days,
             1 if compress else 0, notify, 1 if enabled else 0, db.utcnow(), job_id))
    audit_user(request, user, "backup.job.update",
               detail={"job": name, "ziel": tid or "download", "zeitplan": f"{days} {clock}" if days else "-",
                       "stopp": stop_mode, "alle_server": bool(all_flag), "aufbewahrung": [k_count, k_days]})
    return job_back(job_id, "Backup-Job gespeichert")


@app.post("/admin/backups/jobs/{job_id}/delete")
async def backup_job_delete(request: Request, job_id: int):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    job = own_job(request, user, job_id, "backups.manage")
    if job is None:
        return back("/admin/backups")
    if manager().job_active(job_id):
        return job_back(job_id, "Der Job läuft gerade. Bitte erst abbrechen oder warten.")
    with db.connect() as con:
        con.execute("DELETE FROM backup_jobs WHERE id = ?", (job_id,))
    audit_user(request, user, "backup.job.delete", detail={"job": job["name"]})
    return back("/admin/backups", "Backup-Job gelöscht. Bereits gespeicherte Backups bleiben erhalten.")


def add_items(user, job, ident: str, paths: list[str]) -> str | None:
    """Fügt Pfade eines Servers hinzu. Liefert eine Fehlermeldung oder None."""
    if job["all_servers"]:
        return "Dieser Job sichert schon alle Server komplett."
    if not has(user, ident, "backups.manage") or ident not in server_names():
        return "Für diesen Server fehlt dir das Recht „Backup-Jobs verwalten“."
    missing = backups.stop_problems(user, [ident], job["stop_mode"], job["warn_command"], server_names())
    if missing:
        return i18n.t("Der Job stoppt den Server vorher, dafür fehlt dir: {list}", list=", ".join(missing))
    with db.connect() as con:
        count = con.execute("SELECT COUNT(*) FROM backup_job_items WHERE job_id = ?", (job["id"],)).fetchone()[0]
        if count + len(paths) > MAX_ITEMS:
            return i18n.t("Höchstens {n} Einträge pro Job.", n=MAX_ITEMS)
        con.executemany("INSERT OR IGNORE INTO backup_job_items (job_id, identifier, path) VALUES (?, ?, ?)",
                        [(job["id"], ident, p) for p in paths])
        con.execute("UPDATE backup_jobs SET updated_at = ? WHERE id = ?", (db.utcnow(), job["id"]))
    return None


@app.post("/admin/backups/jobs/{job_id}/items")
async def backup_job_item_add(request: Request, job_id: int, identifier: str = Form(""), path: str = Form("/")):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    job = own_job(request, user, job_id, "backups.manage")
    if job is None:
        return back("/admin/backups")
    path = clean_path(path)
    if identifier not in server_names() or not has(user, identifier, "backups.manage"):
        deny(request, user, "backups.manage", identifier or None)
    error = add_items(user, job, identifier, [path])
    if error:
        return job_back(job_id, error)
    audit_user(request, user, "backup.job.update", server=identifier, detail={"job": job["name"], "hinzu": [path]})
    return job_back(job_id, "Hinzugefügt")


@app.post("/admin/backups/jobs/{job_id}/items/remove")
async def backup_job_item_remove(request: Request, job_id: int, identifier: str = Form(""), path: str = Form("")):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    job = own_job(request, user, job_id, "backups.manage")
    if job is None:
        return back("/admin/backups")
    with db.connect() as con:
        con.execute("DELETE FROM backup_job_items WHERE job_id = ? AND identifier = ? AND path = ?",
                    (job_id, identifier, path))
    audit_user(request, user, "backup.job.update", server=identifier or None,
               detail={"job": job["name"], "entfernt": [path]})
    return job_back(job_id, "Entfernt")


def runnable(request: Request, user, job_id: int):
    job = own_job(request, user, job_id, "backups.run")
    if job is None:
        return None, None
    problems = run_problems(user, job)
    if problems:
        return job, i18n.t("Der Job stoppt den Server vorher, dafür fehlt dir: {list}", list=", ".join(problems[:5]))
    return job, None


@app.post("/admin/backups/jobs/{job_id}/run")
async def backup_job_run(request: Request, job_id: int, next: str = Form("")):
    user = require_backups(request, "backups.run")
    await check_csrf(request)
    job, problem = runnable(request, user, job_id)
    if job is None:
        return back("/admin/backups")
    target = "/admin/backups" if next != "job" else f"/admin/backups/jobs/{job_id}"
    if problem:
        return back(target, problem)
    try:
        n = manager().enqueue(job, user.id, user.username)
    except BackupError as exc:
        return back(target, str(exc))
    audit_user(request, user, "backup.start", detail={"job": job["name"], "server": n})
    return back("/admin/backups", i18n.t("Backup gestartet ({n} Server). Den Fortschritt siehst du hier.", n=n))


@app.post("/admin/backups/jobs/{job_id}/download")
async def backup_job_download(request: Request, job_id: int):
    user = require_backups(request, "backups.run")
    await check_csrf(request)
    job, problem = runnable(request, user, job_id)
    if job is None:
        return back("/admin/backups")
    if problem:
        return back("/admin/backups", problem)
    try:
        states = manager().begin_download(job, user.id, user.username)
    except BackupError as exc:
        return back("/admin/backups", str(exc))
    audit_user(request, user, "backup.download",
               detail={"job": job["name"], "server": [s.server_name for s in states]})
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d_%H-%M")
    base = backups.slug(job["name"])
    if len(states) == 1:
        base += "-" + backups.slug(states[0].server_name)
    filename = f"{base}-{stamp}.zip"
    return StreamingResponse(manager().stream_download(job, states, user.id), media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# -- Gespeicherte Backups ------------------------------------------------------------------------

def load_run(run_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_runs WHERE id = ?", (run_id,)).fetchone()


@app.get("/admin/backups/runs/{run_id}/download")
async def backup_run_download(request: Request, run_id: int):
    user = require_backups(request, "backups.run")
    run = load_run(run_id)
    if run is None:
        return back("/admin/backups")
    if not has(user, run["identifier"], "backups.run"):
        deny(request, user, "backups.run", run["identifier"])
    target = backups.load_target(run["target_id"])
    if not run["file"] or target is None:
        return back("/admin/backups", "Diese Backup-Datei gibt es nicht mehr.")
    try:
        found = await asyncio.to_thread(target.exists, run["file"])
    except BackupError as exc:
        target.close()
        return back("/admin/backups", str(exc))
    if not found:
        target.close()
        return back("/admin/backups", "Diese Backup-Datei gibt es nicht mehr.")
    audit_user(request, user, "backup.file_download", server=run["identifier"],
               detail={"job": run["job_name"], "datei": run["file"]})
    stem = posixpath.basename(run["file"]).rsplit(".", 1)[0]
    name = f"{backups.slug(run['job_name'])}-{backups.slug(run['server_name'] or run['identifier'])}-{stem}.zip"
    if target.kind == "local":
        return FileResponse(target.full(run["file"]), media_type="application/zip", filename=name)

    def chunks():
        try:
            yield from target.read_chunks(run["file"])
        finally:
            target.close()
    return StreamingResponse(chunks(), media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.post("/api/backups/runs/{run_id}/cancel")
async def backup_run_cancel(request: Request, run_id: int):
    user = require_backups(request, "backups.run")
    check_csrf_header(request)
    run = load_run(run_id)
    if run is None:
        return JSONResponse({"error": i18n.t("Diesen Lauf gibt es nicht.")}, status_code=404)
    if not has(user, run["identifier"], "backups.run"):
        deny(request, user, "backups.run", run["identifier"])
    rs = manager().runs.get(run_id)
    if rs is None or not manager().cancel(run_id):
        return {"ok": False, "message": i18n.t("Der Lauf ist schon beendet.")}
    ident = run["identifier"]
    audit_user(request, user, "backup.cancel", server=ident, detail={"job": rs.job_name})
    return {"ok": True, "message": i18n.t("Wird abgebrochen …")}


@app.post("/admin/backups/runs/{run_id}/delete")
async def backup_run_delete(request: Request, run_id: int):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    run = load_run(run_id)
    if run is None:
        return back("/admin/backups")
    if not has(user, run["identifier"], "backups.manage"):
        deny(request, user, "backups.manage", run["identifier"])
    if run["status"] in ("queued", "running"):
        return back("/admin/backups", "Läuft noch")
    await manager().delete_file(run)
    with db.connect() as con:
        con.execute("DELETE FROM backup_runs WHERE id = ?", (run_id,))
    audit_user(request, user, "backup.file_delete", server=run["identifier"],
               detail={"job": run["job_name"], "datei": run["file"]})
    return back("/admin/backups", "Backup gelöscht")


# -- Aus dem Datei-Explorer --------------------------------------------------------------------------

@app.post("/servers/{ident}/files/backup")
async def files_backup_add(request: Request, ident: str, dir: str = Form("/"), job_id: str = Form("")):
    user = current_user(request)
    await check_csrf(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "backups.manage")
    directory = clean_path(dir)
    names = [n for n in (await request.form()).getlist("names") if isinstance(n, str) and valid_name(n)]
    if not names:
        return files_back(ident, directory, "Nichts ausgewählt")
    paths = [posixpath.join(directory, n) for n in names]
    if job_id == "new":
        job = backups.load_job(create_job(user, i18n.t("{server}: Auswahl", server=server["name"])[:80], None))
        audit_user(request, user, "backup.job.create", server=ident, detail={"job": job["name"], "ziel": "download"})
    elif job_id.isdigit():
        job = own_job(request, user, int(job_id), "backups.manage")
        if job is None:
            return files_back(ident, directory, "Diesen Backup-Job gibt es nicht mehr")
    else:
        return files_back(ident, directory, "Bitte einen Backup-Job wählen")
    error = add_items(user, job, ident, paths)
    if error:
        return files_back(ident, directory, error)
    audit_user(request, user, "backup.job.update", server=ident, detail={"job": job["name"], "hinzu": paths})
    # Im Ordner bleiben, damit sich gleich weitere Dateien hinzufügen lassen
    if job_id == "new":
        return files_back(ident, directory, i18n.t(
            "Neuer Backup-Job „{job}“ mit {n} Eintrag/Einträgen angelegt. Ziel und Zeitplan stellst du unter Backups ein.",
            n=len(paths), job=job["name"]))
    return files_back(ident, directory, i18n.t("{n} Eintrag/Einträge zu „{job}“ hinzugefügt", n=len(paths),
                                               job=job["name"]))


def jobs_for_explorer(user, ident: str) -> list:
    """Jobs, denen dieser Benutzer Dateien dieses Servers hinzufügen darf."""
    if not has(user, ident, "backups.manage"):
        return []
    with db.connect() as con:
        jobs = con.execute("SELECT * FROM backup_jobs WHERE all_servers = 0 ORDER BY name COLLATE NOCASE").fetchall()
    return [j for j in jobs if user_may(user, j, "backups.manage")]


# -- Ziele (nur Owner) ---------------------------------------------------------------------------

TARGETS = "/admin/backups#ziele"


def clean_subdir(subdir: str) -> str | None:
    sub = posixpath.normpath("/" + (subdir or "").replace("\\", "/").strip()).lstrip("/")
    if sub.startswith("..") or len(sub) > 200:
        return None
    return "" if sub == "." else sub


def smb_fields(form, old=None) -> tuple[dict | None, str | None]:
    """Liest und prüft die SMB-Angaben. Leeres Passwort behält beim Bearbeiten das gespeicherte."""
    host = str(form.get("smb_host", "")).strip().strip("\\/")
    share = str(form.get("smb_share", "")).strip().strip("\\/")
    user = str(form.get("smb_user", "")).strip()
    password = str(form.get("smb_password", ""))
    sub = clean_subdir(str(form.get("subdir", "")))
    if not backups.SMB_HOST.match(host):
        return None, "Server: Name oder IP-Adresse der Freigabe, z. B. nas oder 192.168.178.20"
    if not backups.SMB_SHARE.match(share):
        return None, "Bitte den Namen der Freigabe angeben, z. B. backup"
    if sub is None:
        return None, "Ungültiger Unterordner"
    if len(user) > 120 or len(password) > 256:
        return None, "Benutzer oder Passwort zu lang"
    if old is not None and not password and not form.get("smb_password_clear"):
        # Das gespeicherte Passwort meldet sich nur am Server an, für den es eingegeben wurde
        if old["smb_password_enc"] and not security.same_host(old["smb_host"], host):
            return None, "Neuer Server: Bitte das Passwort noch einmal eingeben. Das gespeicherte geht nur an den bisherigen."
        password_enc = old["smb_password_enc"]
    else:
        password_enc = vault.encrypt(password) if password else None
    return {"smb_host": host, "smb_share": share, "smb_user": user or None, "smb_password_enc": password_enc,
            "path": sub}, None


async def checked(target) -> tuple[str | None, int | None]:
    """Verbindung prüfen: Ordner anlegen, Testdatei schreiben und löschen. Liefert (Fehlermeldung, freier Platz)."""
    try:
        await asyncio.to_thread(target.check, settings.data_dir)
        return None, await asyncio.to_thread(target.free_bytes)
    except BackupError as exc:
        return str(exc), None
    finally:
        target.close()


@app.post("/admin/backups/targets")
async def backup_target_create(request: Request, name: str = Form(""), kind: str = Form("local"),
                               mount: str = Form(""), subdir: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    name = " ".join(name.split())
    if not name or len(name) > 60:
        return back(TARGETS, "Bitte einen Namen (1–60 Zeichen) angeben")
    now = db.utcnow()
    if kind == "smb":
        fields, error = smb_fields(await request.form())
        if error:
            return back(TARGETS, error)
        row = {"name": name, "kind": "smb", **fields}
        error, free = await checked(backups.SmbTarget(row, vault))
        if error:
            return back(TARGETS, i18n.t("Nicht gespeichert: {grund}", grund=error))
        where = backups.target_where(row)
    else:
        if mount == "data":
            base = settings.data_dir / "backups"
        elif mount in {m["path"] for m in backups.list_mounts()}:
            base = Path(mount)
        else:
            return back(TARGETS, "Bitte einen Speicherort auswählen")
        sub = clean_subdir(subdir)
        if sub is None:
            return back(TARGETS, "Ungültiger Unterordner")
        path = base / sub if sub else base
        try:
            await asyncio.to_thread(backups.check_local_path, path, settings.data_dir)
        except BackupError as exc:
            return back(TARGETS, str(exc))
        row = {"name": name, "kind": "local", "path": str(path)}
        free = shutil.disk_usage(path).free
        where = str(path)
    row.update(checked_at=now, check_ok=1, check_free=free)
    cols = [*row, "created_at", "updated_at"]
    with db.connect() as con:
        con.execute(f"INSERT INTO backup_targets ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    (*row.values(), now, now))
    audit.record("backup.target.save", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"ziel": name, "ort": where})
    return back(TARGETS, i18n.t("Ziel „{name}“ angelegt", name=name))


@app.post("/admin/backups/targets/{target_id}")
async def backup_target_update(request: Request, target_id: int, name: str = Form("")):
    owner = require_owner(request)
    await check_csrf(request)
    with db.connect() as con:
        old = con.execute("SELECT * FROM backup_targets WHERE id = ?", (target_id,)).fetchone()
    if old is None:
        return back(TARGETS)
    name = " ".join(name.split())
    if not name or len(name) > 60:
        return back(TARGETS, "Bitte einen Namen (1–60 Zeichen) angeben")
    fields: dict = {"name": name}
    if old["kind"] == "smb":
        smb, error = smb_fields(await request.form(), old)
        if error:
            return back(TARGETS, error)
        fields.update(smb)
        error, free = await checked(backups.SmbTarget({**dict(old), **fields}, vault))
        if error:
            return back(TARGETS, i18n.t("Nicht gespeichert: {grund}", grund=error))
        fields.update(checked_at=db.utcnow(), check_ok=1, check_free=free, check_error=None)
    with db.connect() as con:
        con.execute(f"UPDATE backup_targets SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = ? WHERE id = ?",
                    (*fields.values(), db.utcnow(), target_id))
    detail = {k: v for k, v in fields.items() if k != "smb_password_enc" and not k.startswith("check")}
    if old["kind"] == "smb":
        detail["passwort"] = "geändert" if fields["smb_password_enc"] != old["smb_password_enc"] else "unverändert"
    audit.record("backup.target.save", user_id=owner.id, username=owner.username, ip=ip_of(request), detail=detail)
    return back(TARGETS, i18n.t("Ziel „{name}“ gespeichert", name=name))


@app.post("/admin/backups/targets/{target_id}/test")
async def backup_target_test(request: Request, target_id: int):
    require_owner(request)
    await check_csrf(request)
    target = backups.load_target(target_id)
    if target is None:
        return back(TARGETS)
    try:
        await asyncio.to_thread(target.check, settings.data_dir)
        free = await asyncio.to_thread(target.free_bytes)
    except BackupError as exc:
        backups.record_check(target_id, False, error=str(exc))
        return back(TARGETS, i18n.t("Test fehlgeschlagen: {grund}", grund=exc))
    finally:
        target.close()
    backups.record_check(target_id, True, free)
    return back(TARGETS, i18n.t("Test erfolgreich: schreiben und löschen klappt, {free} frei.",
                                free=fmt_size(free) if free is not None else "?"))


@app.post("/admin/backups/targets/{target_id}/delete")
async def backup_target_delete(request: Request, target_id: int):
    owner = require_owner(request)
    await check_csrf(request)
    target = backups.load_target(target_id)
    if target is None:
        return back(TARGETS)
    with db.connect() as con:
        used = con.execute("SELECT COUNT(*) FROM backup_jobs WHERE target_id = ?", (target_id,)).fetchone()[0]
        if used:
            return back(TARGETS, "Das Ziel wird noch von Backup-Jobs benutzt")
        con.execute("UPDATE backup_runs SET target_id = NULL, file = NULL WHERE target_id = ?", (target_id,))
        con.execute("DELETE FROM backup_targets WHERE id = ?", (target_id,))
    audit.record("backup.target.delete", user_id=owner.id, username=owner.username, ip=ip_of(request),
                 detail={"ziel": target.row["name"], "ort": backups.target_where(target.row)})
    return back(TARGETS, "Ziel entfernt. Die Dateien darin bleiben erhalten.")
