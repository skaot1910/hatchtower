"""Seiten und Aktionen rund um Backups (siehe backups.py).

Wer die Seite sieht, hängt am Recht „Backups sehen“ für mindestens einen Server. Jobs zeigt sie nur,
wenn das jeweilige Recht für alle Server des Jobs gilt; „Alle Server, auch künftige“ ist Owner-Sache,
ebenso die Backup-Ziele (Ordner und Zugangsdaten).
"""
from __future__ import annotations

import asyncio
import json
import posixpath
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from . import audit, backups, db, downloads, i18n, security
from .backups import BackupError, has, user_may
from .ptero import PteroError
from .main import (app, back, check_csrf, check_csrf_header, clean_path, current_user, deny, files_back,
                   get_server_or_deny, ip_of, is_https, render, require, require_owner, server_names, settings, valid_name,
                   vault)
from .views import fmt_dt, fmt_size

MAX_ITEMS = 500
MAX_EXCLUDES = 4000
STORED = "/admin/backups/stored"
TARGETS = "/admin/backups/targets"


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


def fetch_keys(job) -> tuple[str, ...]:
    """Rights for "run and download now". A direct-download job (no target) reads the live server and needs the
    direct download right; for a job with a target it means starting a backup and downloading it."""
    return ("backups.run", "backups.download") if job["target_id"] else ("backups.direct",)


def may_fetch(user, job) -> bool:
    return all(user_may(user, job, k) for k in fetch_keys(job))


def direct_gaps(user, job_list: list[dict], names: dict) -> list[str]:
    """Servers where this user has the direct download right but no direct-download job to use it with."""
    if user.is_owner:
        return []
    usable = set()
    for x in job_list:
        if not x["j"]["target_id"] and x["can_fetch"] and x["idents"]:
            usable |= x["idents"]
    return sorted((names[i] for i in names if has(user, i, "backups.direct") and i not in usable), key=str.casefold)


def owner_direct_gaps(job_rows, names: dict) -> list[str]:
    """For owners: who has the direct download right on a server without a direct-download job they could use."""
    from .permissions import load_grants
    jobs = [set(backups.job_items(j["id"])) for j in job_rows if not j["target_id"] and not j["all_servers"]]
    with db.connect() as con:
        users = con.execute("SELECT id, username FROM users WHERE role != 'owner' AND is_active = 1"
                            " ORDER BY username COLLATE NOCASE").fetchall()
    gaps = []
    for u in users:
        direct = {i for i, keys in load_grants(u["id"]).items() if "backups.direct" in keys and i in names}
        usable = set().union(*(idents for idents in jobs if idents and idents <= direct)) if jobs else set()
        missing = sorted((names[i] for i in direct - usable), key=str.casefold)
        if missing:
            gaps.append(i18n.t("{user}: {servers}", user=u["username"], servers=", ".join(missing)))
    return gaps


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

BACKUP_TABS = (
    # (address, label, who sees the tab)
    # targets first: a stored backup needs one, so that is where setting up starts
    ("/admin/backups/targets", "Backup-Ziele", lambda u: u.is_owner),
    ("/admin/backups", "Jobs", lambda u: True),
    ("/admin/backups/new", "Neuer Job", lambda u: u.can_any("backups.manage")),
    ("/admin/backups/stored", "Gespeicherte Backups", lambda u: u.can_any("backups.list")),
    ("/admin/backups/downloads", "Downloads", lambda u: u.can_any("backups.direct") or u.can_any("backups.download")),
    ("/admin/backups/restores", "Wiederherstellungen", lambda u: u.can_any("backups.list")),
)


def backup_tabs(user) -> list[tuple[str, str]]:
    return [(href, label) for href, label, visible in BACKUP_TABS if visible(user)]


def render_backups(request: Request, user, tab: str, **context):
    """One tab of the backups page. Running operations and results of finished ones show on every tab."""
    return render(request, "backups.html", tab=tab, page_tabs=backup_tabs(user), page_tab=tab, plain_http=not is_https(request),
                  page_tabs_label="Backups", active=active_for(user),
                  finished=finished_results(user, request.query_params.get("done", "")), **context)


def load_finished(kind_filter: str) -> list:
    with db.connect() as con:
        return con.execute(f"SELECT * FROM backup_runs WHERE status NOT IN ('queued', 'running') AND {kind_filter}"
                           " ORDER BY id DESC LIMIT ?", (HISTORY_MAX,)).fetchall()


@app.get("/admin/backups", response_class=HTMLResponse)
async def backups_page(request: Request):
    user = require_backups(request)
    names = server_names()
    target_by_id = {t["id"]: t for t in load_targets()}
    with db.connect() as con:
        jobs = con.execute("SELECT * FROM backup_jobs ORDER BY name COLLATE NOCASE").fetchall()
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
            else i18n.t("Direktdownload (live)"),
            "when": backups.describe_schedule(job),
            "stop": i18n.t(backups.STOP_MODES.get(job["stop_mode"], "")),
            "can_edit": user_may(user, job, "backups.manage"),
            "can_run": bool(job["target_id"]) and user_may(user, job, "backups.run") and not run_problems(user, job),
            "can_fetch": may_fetch(user, job) and not run_problems(user, job),
            "active": manager().job_active(job["id"]),
            "idents": set(items) if not job["all_servers"] else None,
        })
    q = request.query_params
    job_filter = {"server": q.get("jserver", ""), "target": q.get("jtarget", ""), "q": q.get("jq", "").strip()}
    job_servers = sorted({(i, names[i]) for x in job_list for i in (x["idents"] or ()) if i in names},
                         key=lambda kv: kv[1].casefold())
    job_targets = sorted({(str(x["j"]["target_id"] or "download"), x["target"]) for x in job_list},
                         key=lambda kv: kv[1].casefold())
    filtered_jobs = [x for x in job_list
                     if (not job_filter["server"] or x["idents"] is None or job_filter["server"] in x["idents"])
                     and (not job_filter["target"] or str(x["j"]["target_id"] or "download") == job_filter["target"])
                     and (not job_filter["q"] or job_filter["q"].casefold() in x["j"]["name"].casefold())]
    return render_backups(request, user, "/admin/backups", jobs=filtered_jobs, jobs_total=len(job_list),
                          job_filter={**job_filter, "active": any(job_filter.values())}, job_servers=job_servers,
                          job_targets=job_targets, can_create=user.can_any("backups.manage"),
                          jobs_reset="/admin/backups", direct_gaps=direct_gaps(user, job_list, names),
                          owner_direct_gaps=owner_direct_gaps(jobs, names) if user.is_owner else [])


@app.get("/admin/backups/new", response_class=HTMLResponse)
async def backups_new_page(request: Request):
    user = require_backups(request, "backups.manage")
    return render_backups(request, user, "/admin/backups/new", targets=load_targets())


@app.get("/admin/backups/stored", response_class=HTMLResponse)
async def backups_stored_page(request: Request):
    user = require_backups(request, "backups.list")
    names = server_names()
    with db.connect() as con:
        restores = con.execute("SELECT run_id, finished_at FROM backup_restores WHERE status = 'ok'"
                               " ORDER BY id DESC LIMIT ?", (HISTORY_MAX,)).fetchall()
    restored = {}                      # zuletzt erfolgreich zurückgespielt, pro Backup
    for x in restores:
        if x["run_id"] and x["run_id"] not in restored:
            restored[x["run_id"]] = fmt_dt(x["finished_at"], settings.timezone)
    q = request.query_params
    visible_runs = [r for r in load_finished("kind = 'target'") if has(user, r["identifier"], "backups.list")]
    run_filter = {"server": q.get("server", ""), "job": q.get("job", ""), "status": q.get("status", "")}
    filtered_runs = [r for r in visible_runs
                     if (not run_filter["server"] or r["identifier"] == run_filter["server"])
                     and (not run_filter["job"] or r["job_name"] == run_filter["job"])
                     and (not run_filter["status"] or r["status"] == run_filter["status"])]
    base = "/admin/backups/stored"
    run_page, runs_pager = paginate(filtered_runs, q, "page", base)
    run_list = [{
        "r": r, "at": fmt_dt(r["finished_at"] or r["queued_at"], settings.timezone),
        "status": i18n.t(backups.STATUS_LABELS.get(r["status"], r["status"])),
        "size": fmt_size(r["size"]) if r["size"] else "",
        "data": fmt_size(r["data_bytes"]) if r["data_bytes"] else "",
        "verified": fmt_dt(r["verified_at"], settings.timezone) if r["verified_at"] else None,
        "restored": restored.get(r["id"]),
        "can_download": bool(r["file"]) and has(user, r["identifier"], "backups.download"),
        "can_verify": bool(r["file"]) and has(user, r["identifier"], "backups.run"),
        "can_restore": bool(r["file"]) and user.can(r["identifier"], "backups.restore"),
        "can_delete": bool(r["file"]) and has(user, r["identifier"], "backups.manage"),
    } for r in run_page]
    for x in run_list:                 # Auswahlkästchen nur, wo eine Sammelaktion möglich ist
        x["can_select"] = x["can_delete"] or x["can_verify"]
    panel = await panel_backups(user, names, run_filter["server"])
    return render_backups(request, user, base, panel=panel, panel_name=settings.panel.name,
                          runs=run_list, runs_total=len(visible_runs),
                          runs_count=len(filtered_runs), runs_pager=runs_pager,
                          run_filter={**run_filter, "active": any(run_filter.values())},
                          run_servers=server_choices(visible_runs, names),
                          run_jobs=sorted({r["job_name"] for r in visible_runs if r["job_name"]}, key=str.casefold),
                          status_options=status_options(), runs_reset=base)


# -- Backups made by the panel (Pterodactyl, Pelican, Calagopus) -------------------------------------
# Not Hatchtower's: the panel creates them (by hand, by its own schedules) and keeps them where it stores
# backups, usually on the Wings server. Hatchtower only lists them and streams a download.

PANEL_CACHE_SECONDS = 60        # one listing per server and minute at most: the panel's rate limit is shared
_panel_cache: dict[str, tuple[float, list | str]] = {}


async def panel_backups_of(ident: str) -> list | str:
    """The panel's backups of one server, or the error text. Cached for a minute."""
    hit = _panel_cache.get(ident)
    if hit and time.monotonic() - hit[0] < PANEL_CACHE_SECONDS:
        return hit[1]
    try:
        result: list | str = await app.state.ptero.list_backups(ident)
    except PteroError as exc:
        result = str(exc)
    _panel_cache[ident] = (time.monotonic(), result)
    return result


async def panel_backups(user, names: dict, only: str = "") -> list[dict]:
    """Per server this user may see stored backups of: the panel's backups (or why they could not be read)."""
    idents = [i for i in sorted(names, key=lambda i: names[i].casefold())
              if has(user, i, "backups.list") and (not only or i == only)]
    gate = asyncio.Semaphore(4)

    async def one(ident: str):
        async with gate:
            return await panel_backups_of(ident)
    results = await asyncio.gather(*(one(i) for i in idents))
    missing = missing_files()
    out = []
    for ident, result in zip(idents, results):
        if isinstance(result, str):
            out.append({"ident": ident, "server": names[ident], "error": result, "backups": []})
            continue
        if not result:
            continue
        can_download = has(user, ident, "backups.download")
        can_delete = has(user, ident, "backups.manage")
        forget_missing(ident, {b["uuid"] for b in result})      # deleted in the panel: the mark goes too
        out.append({"ident": ident, "server": names[ident], "error": None, "backups": [{
            **b, "size": fmt_size(b["bytes"]) if b["bytes"] else "",
            "at": fmt_dt(b["completed"] or b["created"], settings.timezone),
            "missing": fmt_dt(missing[b["uuid"]], settings.timezone) if b["uuid"] in missing else None,
            "can_download": can_download and b["successful"] and bool(b["completed"]),
            "can_delete": can_delete and b["uuid"] in missing and not b["locked"]} for b in result]})
    return out


# The panel keeps a backup in its database even when the file on the Wings node is gone (container recreated
# without the backup folder as a volume, folder cleaned up, other backup_directory). It only shows when
# downloading; from then on the backup is marked instead of offering a download that cannot work.

def missing_files() -> dict[str, str]:
    with db.connect() as con:
        return {r["uuid"]: r["detected_at"] for r in con.execute("SELECT uuid, detected_at FROM panel_backup_missing")}


def mark_missing(ident: str, uuid: str, detail: str) -> None:
    with db.connect() as con:
        con.execute("INSERT INTO panel_backup_missing (uuid, identifier, detected_at, detail) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(uuid) DO UPDATE SET detected_at = excluded.detected_at, detail = excluded.detail",
                    (uuid, ident, db.utcnow(), detail[:300]))


def forget_missing(ident: str, existing: set[str] | None = None, uuid: str | None = None) -> None:
    with db.connect() as con:
        if uuid is not None:
            con.execute("DELETE FROM panel_backup_missing WHERE uuid = ?", (uuid,))
        elif existing is not None:
            rows = con.execute("SELECT uuid FROM panel_backup_missing WHERE identifier = ?", (ident,)).fetchall()
            gone = [r["uuid"] for r in rows if r["uuid"] not in existing]
            con.executemany("DELETE FROM panel_backup_missing WHERE uuid = ?", [(g,) for g in gone])


def file_missing(exc: PteroError) -> bool:
    """Wings: 404 "The requested backup was not found on this server." – unlike a used link ("resource")."""
    return exc.status == 404 and "backup" in str(exc).lower()


@app.post("/admin/backups/panel/{ident}/{uuid}/delete")
async def panel_backup_delete(request: Request, ident: str, uuid: str):
    """Remove a panel backup whose file is gone from the node, so the dead entry leaves the list (and the panel's
    backup limit). Only for backups marked as missing: everything else is deleted in the panel itself."""
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    names = server_names()
    if ident not in names or not has(user, ident, "backups.manage"):
        deny(request, user, "backups.manage", ident if ident in names else None)
    found = await panel_backups_of(ident)
    backup = next((b for b in found if b["uuid"] == uuid), None) if isinstance(found, list) else None
    if backup is None or uuid not in missing_files():
        return back(STORED + "#panel-backups", "Hier lassen sich nur Backups löschen, deren Datei auf dem Node fehlt.")
    if backup["locked"]:
        return back(STORED + "#panel-backups", i18n.t(
            "„{backup}“ ist im Panel gesperrt. Entsperre es dort zuerst.", backup=backup["name"] or uuid))
    label = {"backup": backup["name"] or uuid, "panel": settings.panel.name}
    try:
        await app.state.ptero.delete_backup(ident, uuid)
    except PteroError as exc:
        audit_user(request, user, "backup.panel_delete", result="error", server=ident, detail={**label, "fehler": str(exc)})
        return back(STORED + "#panel-backups", i18n.t("Löschen in {panel} fehlgeschlagen: {grund}",
                                                      panel=settings.panel.name, grund=exc))
    forget_missing(ident, uuid=uuid)
    _panel_cache.pop(ident, None)
    audit_user(request, user, "backup.panel_delete", server=ident, detail=label)
    return back(STORED + "#panel-backups", i18n.t("„{backup}“ aus {panel} gelöscht", **label))


@app.get("/admin/backups/panel/{ident}/{uuid}/download")
async def panel_backup_download(request: Request, ident: str, uuid: str):
    """Download a backup the panel made, streamed through Hatchtower (Wings is often not reachable from outside)."""
    user = require_backups(request, "backups.download")
    names = server_names()
    if ident not in names or not has(user, ident, "backups.download"):
        deny(request, user, "backups.download", ident if ident in names else None)
    found = await panel_backups_of(ident)
    backup = next((b for b in found if b["uuid"] == uuid), None) if isinstance(found, list) else None
    if backup is None:
        _panel_cache.pop(ident, None)            # maybe brand new: look again next time
        return back(STORED, "Dieses Backup gibt es im Panel nicht (mehr).")
    client = app.state.ptero
    part = request.headers.get("range")
    try:
        status, wings_headers, stream = await fresh_link_stream(
            lambda: client.backup_download_url(ident, uuid, backup.get("streaming", False)), part)
        # the first bytes tell the real format (the panel's storage decides it), so the file name fits
        first = await anext(stream, b"")
    except PteroError as exc:
        downloads.log.warning("Download Panel-Backup %s (%s) fehlgeschlagen: %s: %s", backup["name"], ident,
                              downloads.device(request.headers.get("user-agent")), exc)
        if downloads.counts(part):
            audit_user(request, user, "backup.panel_download", result="error", server=ident,
                       detail={"backup": backup["name"], "panel": settings.panel.name, "fehler": str(exc)})
        if file_missing(exc):
            mark_missing(ident, uuid, str(exc))
            return back(STORED + "#panel-backups", i18n.t(
                "Die Datei zu „{backup}“ fehlt auf dem Wings-Node – {panel} führt das Backup noch, aber die Datei ist"
                " weg. Es ist jetzt in der Liste markiert.", backup=backup["name"] or uuid, panel=settings.panel.name))
        return back(STORED, str(exc))
    forget_missing(ident, uuid=uuid)              # it works (again): no mark
    if not part or part.replace(" ", "").startswith("bytes=0-"):    # the stream starts at the file's beginning
        _panel_formats[uuid] = archive_format(first, client.BACKUP_FORMAT)
    ext, media = _panel_formats.get(uuid, client.BACKUP_FORMAT)
    stamp = (backup["completed"] or backup["created"] or "")[:16].replace("T", "_").replace(":", "-")
    name = f"{backups.slug(names[ident])}-{backups.slug(backup['name'] or 'backup')}-{stamp}.{ext}"
    if downloads.counts(part):                    # a part request of the same download is not a new download
        audit_user(request, user, "backup.panel_download", server=ident,
                   detail={"backup": backup["name"], "panel": settings.panel.name, "groesse": backup["bytes"]})
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
    for key in ("content-length", "content-range", "accept-ranges"):
        if wings_headers.get(key):
            headers[key.title()] = wings_headers[key]
    if "Content-Length" not in headers and backup["bytes"] and client.BACKUP_SIZE_IS_ARCHIVE and status == 200:
        headers["Content-Length"] = str(backup["bytes"])

    async def body():
        try:
            if first:
                yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()                 # a part request stops early: close the connection to Wings
    status, headers, content = downloads.ranged(status, headers, body(), part)
    return StreamingResponse(downloads.tracked(content, f"Panel-Backup {name}", request.headers.get("user-agent"),
                                               status, part, int(headers.get("Content-Length") or 0) or None,
                                               ip_of(request)),
                             status_code=status, media_type=media, headers=headers)


async def fresh_link_stream(get_url, part: str | None):
    """Wings accepts a download link exactly once and answers 404 the second time. Should that happen (a link
    used by something in between), ask the panel for a new link and try once more; a missing backup stays 404."""
    client = app.state.ptero
    try:
        return await client.wings_stream(await get_url(), part)
    except PteroError as exc:
        if exc.status != 404:
            raise
        downloads.log.info("Wings antwortet mit 404, neuer Versuch mit frischem Link: %s", exc)
        return await client.wings_stream(await get_url(), part)


_panel_formats: dict[str, tuple[str, str]] = {}    # format seen at the start of a download, for its part requests


ARCHIVE_MAGIC = ((b"PK\x03\x04", "zip", "application/zip"), (b"\x1f\x8b", "tar.gz", "application/gzip"),
                 (b"\x28\xb5\x2f\xfd", "tar.zst", "application/zstd"), (b"\xfd7zXZ", "tar.xz", "application/x-xz"),
                 (b"BZh", "tar.bz2", "application/x-bzip2"))


def archive_format(first: bytes, default: tuple[str, str]) -> tuple[str, str]:
    return next(((ext, media) for magic, ext, media in ARCHIVE_MAGIC if first.startswith(magic)), default)


DOWNLOAD_KINDS = {"live": "Direktdownload (live)", "stored": "Gespeichertes Backup", "panel": "Panel-Backup"}
REPEAT_SECONDS = 120     # Safari fetches one download in several requests: count them once


def download_history(user) -> list[dict]:
    """Every download, newest first: direct downloads from the live server (backup runs) and downloads of stored
    backups (audit log, so it reaches back as far as the audit log is kept). Only servers with the right."""
    rows = []
    for r in load_finished("kind = 'download'"):
        if has(user, r["identifier"], "backups.direct") or has(user, r["identifier"], "backups.download"):
            rows.append({"ts": r["finished_at"] or r["queued_at"], "kind": "live", "job": r["job_name"],
                         "ident": r["identifier"], "server": r["server_name"] or r["identifier"],
                         "who": r["started_name"] or "–", "status": r["status"],
                         "size": r["size"] or r["data_bytes"], "detail": r["detail"] or ""})
    with db.connect() as con:
        audit_rows = con.execute("SELECT ts, username, server, detail, action FROM audit_log"
                                 " WHERE action IN ('backup.file_download', 'backup.panel_download')"
                                 " AND result = 'ok' ORDER BY id DESC LIMIT ?", (HISTORY_MAX,)).fetchall()
    names = server_names()
    last: dict[tuple, datetime] = {}
    for a in audit_rows:
        if not a["server"] or not has(user, a["server"], "backups.download"):
            continue
        try:
            info = json.loads(a["detail"] or "{}")
            at = datetime.fromisoformat(a["ts"])
        except ValueError:
            continue
        panel = a["action"] == "backup.panel_download"
        key = (a["username"], info.get("backup") if panel else info.get("datei"))
        if key in last and (last[key] - at).total_seconds() < REPEAT_SECONDS:
            continue
        last[key] = at
        rows.append({"ts": a["ts"], "kind": "panel" if panel else "stored",
                     "job": i18n.t("erstellt von {panel}", panel=info.get("panel") or settings.panel.name) if panel
                     else info.get("job") or "", "ident": a["server"],
                     "server": names.get(a["server"], a["server"]), "who": a["username"] or "–", "status": "ok",
                     "size": info.get("groesse") if panel else None,
                     "detail": info.get("backup") if panel else posixpath.basename(info.get("datei") or "")})
    rows.sort(key=lambda x: x["ts"] or "", reverse=True)
    return rows


@app.get("/admin/backups/downloads", response_class=HTMLResponse)
async def backups_downloads_page(request: Request):
    """What this user can download from the live server right now, and the history of all downloads."""
    user = require_backups(request)
    names = server_names()
    base = "/admin/backups/downloads"
    with db.connect() as con:
        direct_jobs = con.execute("SELECT * FROM backup_jobs WHERE target_id IS NULL ORDER BY name COLLATE NOCASE").fetchall()
    fetchable = []
    for job in direct_jobs:
        if not user_may(user, job, "backups.direct"):
            continue
        items = backups.job_items(job["id"])
        fetchable.append({"j": job, "active": manager().job_active(job["id"]), "problems": run_problems(user, job),
                          "servers": [i18n.t("Alle Server, auch künftige")] if job["all_servers"]
                          else [names.get(i, i) for i in items],
                          "paths": [p for paths in items.values() for p in paths],
                          "stop": i18n.t(backups.STOP_MODES.get(job["stop_mode"], ""))})
    q = request.query_params
    history = download_history(user)
    flt = {"kind": q.get("kind", ""), "server": q.get("server", "")}
    shown = [x for x in history if (not flt["kind"] or x["kind"] == flt["kind"])
             and (not flt["server"] or x["ident"] == flt["server"])]
    page, pager = paginate(shown, q, "page", base)
    for x in page:
        x["at"] = fmt_dt(x["ts"], settings.timezone)
        x["kind_label"] = i18n.t(DOWNLOAD_KINDS[x["kind"]])
        x["status_label"] = i18n.t(backups.STATUS_LABELS.get(x["status"], x["status"]))
        x["size_text"] = fmt_size(x["size"]) if x["size"] else ""
    servers = sorted({(x["ident"], x["server"]) for x in history}, key=lambda kv: kv[1].casefold())
    return render_backups(request, user, base, fetchable=fetchable, can_direct=user.can_any("backups.direct"),
                          gaps=direct_gaps_for(user, fetchable, names), history=page, history_total=len(history),
                          history_count=len(shown), history_pager=pager, download_filter={**flt, "active": any(flt.values())},
                          download_servers=servers, download_kinds=[(k, i18n.t(v)) for k, v in DOWNLOAD_KINDS.items()])


def direct_gaps_for(user, fetchable: list[dict], names: dict) -> list[str]:
    usable = {i for x in fetchable for i in backups.job_items(x["j"]["id"])}
    if user.is_owner:
        return []
    return sorted((names[i] for i in names if has(user, i, "backups.direct") and i not in usable), key=str.casefold)


@app.get("/admin/backups/restores", response_class=HTMLResponse)
async def backups_restores_page(request: Request):
    user = require_backups(request, "backups.list")
    names = server_names()
    with db.connect() as con:
        restores = con.execute("SELECT * FROM backup_restores ORDER BY id DESC LIMIT ?", (HISTORY_MAX,)).fetchall()
    q = request.query_params
    visible = [x for x in restores if x["status"] != "running" and has(user, x["identifier"], "backups.list")]
    restore_filter = {"server": q.get("server", ""), "status": q.get("status", "")}
    filtered = [x for x in visible
                if (not restore_filter["server"] or x["identifier"] == restore_filter["server"])
                and (not restore_filter["status"] or x["status"] == restore_filter["status"])]
    base = "/admin/backups/restores"
    page, pager = paginate(filtered, q, "page", base)
    restore_list = [{
        "x": x, "at": fmt_dt(x["finished_at"] or x["started_at"], settings.timezone),
        "backup_at": fmt_dt(x["backup_at"], settings.timezone) if x["backup_at"] else "",
        "status": i18n.t(backups.STATUS_LABELS.get(x["status"], x["status"])),
    } for x in page]
    return render_backups(request, user, base, restores=restore_list, restores_total=len(visible),
                          restores_count=len(filtered), restores_pager=pager,
                          restore_filter={**restore_filter, "active": any(restore_filter.values())},
                          restore_servers=server_choices(visible, names), status_options=status_options(),
                          restores_reset=base)


@app.get("/admin/backups/targets", response_class=HTMLResponse)
async def backups_targets_page(request: Request):
    user = require_owner(request)
    targets = load_targets()
    uid, gid = backups.service_ids()
    mounts = backups.list_mounts()
    for m in mounts:
        m["free_text"] = fmt_size(m["free"]) if m["free"] is not None else ""
    return render_backups(request, user, "/admin/backups/targets", targets_view=[target_view(t) for t in targets],
                          mounts=mounts, data_dir=str(settings.data_dir / "backups"),
                          host_uid=100000 + (uid or 0), host_gid=100000 + (gid or 0))


def status_options() -> list[tuple[str, str]]:
    return [(k, i18n.t(backups.STATUS_LABELS[k])) for k in ("ok", "partial", "error", "cancelled", "denied")]


HISTORY_MAX = 2000      # finished backups and restores read for the list (newest first)
PAGE_SIZE = 10


def list_url(base: str, q, drop=(), **set_) -> str:
    """A backups tab URL with the current filters, minus `drop`, plus `set_`."""
    params = {k: v for k, v in q.items() if k not in drop and k != "done" and v}
    params.update({k: str(v) for k, v in set_.items()})
    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in params.items())
    return base + (f"?{query}" if query else "")


def paginate(rows: list, q, key: str, base: str) -> tuple[list, dict | None]:
    """One page of `rows` (PAGE_SIZE each) and the links for the pager, or None for a single page."""
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    try:
        page = min(max(1, int(q.get(key, "1"))), pages)
    except ValueError:
        page = 1
    chunk = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    if pages == 1:
        return chunk, None
    return chunk, {"page": page, "pages": pages,
                   "prev": list_url(base, q, **{key: page - 1}) if page > 1 else None,
                   "next": list_url(base, q, **{key: page + 1}) if page < pages else None}


def server_choices(rows, names: dict) -> list[tuple[str, str]]:
    seen = {}
    for r in rows:
        ident = r["identifier"]
        if ident and ident not in seen:
            seen[ident] = names.get(ident) or (r["server_name"] if "server_name" in r.keys() else None) or ident
    return sorted(seen.items(), key=lambda kv: kv[1].casefold())


MAX_DONE = 20


def finished_results(user, done: str) -> list[dict]:
    """Meldungen zu Vorgängen, die gerade fertig geworden sind. Der Browser hängt ihre Nummern an die
    Adresse („?done=12,verify-12,restore-3“), sobald sie aus „Laufende Vorgänge“ verschwinden."""
    t = i18n.t
    results = []
    for key in [k for k in done.split(",") if k][:MAX_DONE]:
        kind, _sep, num = key.rpartition("-")
        if not num.isdigit() or kind not in ("", "verify", "restore"):
            continue
        with db.connect() as con:
            row = con.execute("SELECT * FROM backup_restores WHERE id = ?" if kind == "restore"
                              else "SELECT * FROM backup_runs WHERE id = ?", (int(num),)).fetchone()
        if row is None or not has(user, row["identifier"], "backups.view") or row["status"] in ("queued", "running"):
            continue
        server = row["server_name"] or row["identifier"]
        label = f"{row['job_name']} · {server}"
        detail = row["detail"] or ""
        if kind == "restore":
            where = t("an den ursprünglichen Ort") if row["mode"] == "overwrite" else                 t("in den Ordner {ordner}", ordner=row["dest"])
            if row["status"] == "ok":
                text = t("Wiederherstellung abgeschlossen: {was} – {n} Dateien {wohin} zurückgespielt.",
                         was=label, n=row["files"] or 0, wohin=where)
            elif row["status"] == "cancelled":
                text = t("Wiederherstellung abgebrochen: {was}. Bis dahin zurückgespielte Dateien bleiben auf dem"
                         " Server.", was=label)
            else:
                text = t("Wiederherstellung {status}: {was}", status=t(backups.STATUS_LABELS.get(row["status"],
                                                                                              row["status"])), was=label)
        elif kind == "verify":
            if row["verify_ok"]:
                text, detail = t("Prüfung bestanden: {was} ist unbeschädigt.", was=label), ""
            else:
                text, detail = t("Prüfung fehlgeschlagen: {was}", was=label), row["verify_detail"] or ""
            results.append({"ok": bool(row["verify_ok"]), "text": text, "detail": detail})
            continue
        elif row["status"] == "ok":
            text = t("Backup fertig: {was}", was=label)
        else:
            text = t("Backup {status}: {was}", status=t(backups.STATUS_LABELS.get(row["status"], row["status"])),
                     was=label)
        results.append({"ok": row["status"] in ("ok", "cancelled"), "text": text, "detail": detail})
    return results


@app.get("/api/backups/status")
async def backups_status(request: Request):
    user = require_backups(request)
    return {"active": active_for(user)}


def active_for(user) -> list[dict]:
    """Laufende Backups, Prüfungen und Wiederherstellungen, die dieser Benutzer sehen darf."""
    active = manager().active_views(lambda i: has(user, i, "backups.view"))
    for a in active:
        if a["kind"] == "verify":
            a["can_cancel"] = False
        elif a["kind"] == "restore":
            a["can_cancel"] = user.can(a["ident"], "backups.restore")
        else:
            a["can_cancel"] = may_cancel(user, a["ident"], a["kind"])
    return active


def may_cancel(user, ident: str, kind: str) -> bool:
    """Cancelling a backup: backups.run, or for a direct download the right to start one."""
    return has(user, ident, "backups.run") or (kind == "download" and has(user, ident, "backups.direct"))


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
        return back("/admin/backups/new", "Bitte Name (1–80 Zeichen) und Ziel angeben")
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
    browser = await job_browser(request, user, job, items, addable)
    return render(request, "backup_job.html", page_tabs=backup_tabs(user), page_tab="/admin/backups",
                  page_tabs_label="Backups", job=job, items=item_view, targets=load_targets(),
                  servers=sorted(addable.items(), key=lambda kv: kv[1].lower()),
                  weekdays=[i18n.t(d) for d in backups.WEEKDAYS],
                  stop_modes={k: i18n.t(v) for k, v in backups.STOP_MODES.items()},
                  notify_modes={k: i18n.t(v) for k, v in backups.NOTIFY_MODES.items()},
                  keep_counts=backups.KEEP_COUNTS, keep_days=backups.KEEP_DAYS, save_waits=backups.SAVE_WAITS,
                  save_wait_default=backups.SAVE_WAIT_DEFAULT,
                  can_run=bool(job["target_id"]) and user_may(user, job, "backups.run") and not run_problems(user, job),
                  can_fetch=may_fetch(user, job) and not run_problems(user, job),
                  savegame_server=names.get(job["save_for"], job["save_for"]) if job["save_for"] else "",
                  active=manager().job_active(job_id), **browser)


def browser_url(job_id: int, ident: str, directory: str = "/") -> str:
    return f"/admin/backups/jobs/{job_id}?server={quote(ident)}&dir={quote(directory)}#dateien"


async def job_browser(request: Request, user, job, items: dict, addable: dict) -> dict:
    """File browser on the job page: the job's server if it has exactly one, otherwise the one picked above it."""
    if job["all_servers"]:
        return {"browse": None}
    choices = {i: n for i, n in addable.items() if has(user, i, "sftp.view")}
    q = request.query_params
    ident = q.get("server", "")
    if not ident and len(items) == 1:
        ident = next(iter(items))
    if ident not in choices:
        return {"browse": None, "browse_choices": sorted(choices.items(), key=lambda kv: kv[1].lower())}
    directory = clean_path(q.get("dir", "/"))
    error = None
    try:
        files = await app.state.ptero.list_files(ident, directory)
    except PteroError as exc:
        files, error = [], str(exc)
    audit_user(request, user, "files.list", server=ident, detail=directory, result="ok" if error is None else "error")
    chosen = items.get(ident, [])
    covered = next((p for p in chosen if p == "/" or directory == p or directory.startswith(p.rstrip("/") + "/")), None)
    for f in files:
        f["path"] = posixpath.join(directory, f["name"])
        f["size_text"] = fmt_size(f["size"]) if f["is_file"] else ""
        f["modified_text"] = fmt_dt(f["modified_at"], settings.timezone)
        f["in_job"] = f["path"] in chosen
    crumbs, acc = [], ""
    for part in [p for p in directory.split("/") if p]:
        acc += "/" + part
        crumbs.append((part, browser_url(job["id"], ident, acc)))
    return {"browse": {"ident": ident, "name": choices[ident], "dir": directory, "files": files, "error": error,
                       "crumbs": crumbs, "root": browser_url(job["id"], ident),
                       "parent": browser_url(job["id"], ident, posixpath.dirname(directory)) if directory != "/" else None,
                       "url": lambda path: browser_url(job["id"], ident, path),
                       "covered": covered, "dir_in_job": directory in chosen},
            "browse_choices": sorted(choices.items(), key=lambda kv: kv[1].lower())}


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
                          enabled: str = Form(""), save_command: str = Form(""), save_wait: str = Form("")):
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
    if job["save_for"]:
        tid = None
    all_flag = 1 if all_servers and user.is_owner and not job["save_for"] else 0
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
    # Save before the backup: only without a stop (a stopping game saves anyway). The command is fixed in the job,
    # so whoever sets it needs "send commands"; whoever only runs the job does not.
    save_command = save_command.strip() if stop_mode == "none" else ""
    wait = None
    if save_command:
        if len(save_command) > 300 or "\n" in save_command:
            problems.append("Speicher-Befehl")
        ok_wait, wait = _int_choice(save_wait, backups.SAVE_WAITS)
        if not ok_wait:
            problems.append("Wartezeit nach dem Speichern")
        idents = list(backups.present_servers()) if all_flag else list(backups.job_items(job_id))
        lacking = [server_names().get(i, i) for i in idents if not has(user, i, "console.command")]
        if lacking and not user.is_owner:
            problems.append(i18n.t("Speicher-Befehl nur mit dem Recht „Befehle senden“ ({list})",
                                   list=", ".join(lacking[:5])))
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
            " notify = ?, enabled = ?, save_command = ?, save_wait = ?, updated_at = ? WHERE id = ?",
            (name, tid, all_flag, excludes or None, days, clock, stop_mode, warn, warn_command or None, k_count, k_days,
             1 if compress else 0, notify, 1 if enabled else 0, save_command or None, wait, db.utcnow(), job_id))
    audit_user(request, user, "backup.job.update",
               detail={"job": name, "ziel": tid or "download", "zeitplan": f"{days} {clock}" if days else "-",
                       "stopp": stop_mode, "speichern": save_command or "-", "alle_server": bool(all_flag), "aufbewahrung": [k_count, k_days]})
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
    if job["save_for"] and ident != job["save_for"]:
        return "Ein Spielstand-Download gehört zu genau einem Server."
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
async def backup_job_item_add(request: Request, job_id: int, identifier: str = Form(""), path: str = Form("/"),
                              dir: str = Form(""), whole: str = Form("")):
    """Adds a path typed in by hand, or (from the file browser) the ticked entries of folder `dir` or, with
    `whole`, the folder itself."""
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    job = own_job(request, user, job_id, "backups.manage")
    if job is None:
        return back("/admin/backups")
    if identifier not in server_names() or not has(user, identifier, "backups.manage"):
        deny(request, user, "backups.manage", identifier or None)
    if dir:
        directory = clean_path(dir)
        names = [n for n in (await request.form()).getlist("names") if isinstance(n, str) and valid_name(n)]
        paths = [directory] if whole else [posixpath.join(directory, n) for n in names]
        page = browser_url(job_id, identifier, directory)
        if not paths:
            return back(page, "Nichts ausgewählt")
    else:
        paths, page = [clean_path(path)], f"/admin/backups/jobs/{job_id}"
    error = add_items(user, job, identifier, paths)
    if error:
        return back(page, error)
    audit_user(request, user, "backup.job.update", server=identifier, detail={"job": job["name"], "hinzu": paths})
    return back(page, "Hinzugefügt" if len(paths) == 1 else i18n.t("{n} Einträge hinzugefügt", n=len(paths)))


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
    """Runs a job now and streams the ZIP to the browser (read from the live server, nothing is stored)."""
    user = require_backups(request)
    await check_csrf(request)
    job = backups.load_job(job_id)
    if job is None:
        return back("/admin/backups")
    for key in fetch_keys(job):
        if not user_may(user, job, key):
            idents = backups.job_idents(job)
            deny(request, user, key, idents[0] if len(idents) == 1 else None)
    problem = run_problems(user, job)
    if problem:
        return back("/admin/backups", i18n.t("Der Job stoppt den Server vorher, dafür fehlt dir: {list}",
                                             list=", ".join(problem[:5])))
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
    return StreamingResponse(downloads.tracked(manager().stream_download(job, states, user.id), f"Direktdownload {filename}",
                                               request.headers.get("user-agent"), 200, None, None, ip_of(request)),
                             media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# -- Gespeicherte Backups ------------------------------------------------------------------------

def load_run(run_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_runs WHERE id = ?", (run_id,)).fetchone()


@app.get("/admin/backups/runs/{run_id}/download")
async def backup_run_download(request: Request, run_id: int):
    user = require_backups(request, "backups.download")
    run = load_run(run_id)
    if run is None:
        return back(STORED)
    if not has(user, run["identifier"], "backups.download"):
        deny(request, user, "backups.download", run["identifier"])
    target = backups.load_target(run["target_id"])
    if not run["file"] or target is None:
        return back(STORED, "Diese Backup-Datei gibt es nicht mehr.")
    try:
        found = await asyncio.to_thread(target.exists, run["file"])
    except BackupError as exc:
        target.close()
        return back(STORED, str(exc))
    if not found:
        target.close()
        return back(STORED, "Diese Backup-Datei gibt es nicht mehr.")
    part = request.headers.get("range")
    if downloads.counts(part):                    # part requests of one download count once
        audit_user(request, user, "backup.file_download", server=run["identifier"],
                   detail={"job": run["job_name"], "datei": run["file"]})
    stem = posixpath.basename(run["file"]).rsplit(".", 1)[0]
    name = f"{backups.slug(run['job_name'])}-{backups.slug(run['server_name'] or run['identifier'])}-{stem}.zip"
    if target.kind == "local" and not downloads.throttled(ip_of(request)):
        downloads.requested(f"Backup {name}", request.headers.get("user-agent"), part)
        return FileResponse(target.full(run["file"]), media_type="application/zip", filename=name)
    disposition = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
    if target.kind == "local":                    # limited: stream it ourselves, the file response cannot be paced
        path = Path(target.full(run["file"]))
        headers = dict(disposition)
        status, content = downloads.file_body(path, path.stat().st_size, part, headers)
        return StreamingResponse(downloads.tracked(content, f"Backup {name}", request.headers.get("user-agent"), status,
                                                   part, int(headers.get("Content-Length") or 0) or None, ip_of(request)),
                                 status_code=status, media_type="application/zip", headers=headers)

    def chunks():
        try:
            yield from target.read_chunks(run["file"])
        finally:
            target.close()
    # SMB reads block: in a worker thread, as before
    return StreamingResponse(downloads.tracked(iterate_in_threadpool(chunks()), f"Backup {name}", request.headers.get("user-agent"), 200,
                                               None, run["size"], ip_of(request)), media_type="application/zip",
                             headers=disposition)


@app.post("/api/backups/runs/{run_id}/cancel")
async def backup_run_cancel(request: Request, run_id: int):
    user = require_backups(request)
    check_csrf_header(request)
    run = load_run(run_id)
    if run is None:
        return JSONResponse({"error": i18n.t("Diesen Lauf gibt es nicht.")}, status_code=404)
    if not may_cancel(user, run["identifier"], run["kind"]):
        deny(request, user, "backups.run", run["identifier"])
    rs = manager().runs.get(run_id)
    if rs is None or not manager().cancel(run_id):
        return {"ok": False, "message": i18n.t("Der Lauf ist schon beendet.")}
    ident = run["identifier"]
    audit_user(request, user, "backup.cancel", server=ident, detail={"job": rs.job_name})
    return {"ok": True, "message": i18n.t("Wird abgebrochen …")}


@app.post("/admin/backups/runs/{run_id}/verify")
async def backup_run_verify(request: Request, run_id: int):
    user = require_backups(request, "backups.run")
    await check_csrf(request)
    run = load_run(run_id)
    if run is None:
        return back(STORED)
    if not has(user, run["identifier"], "backups.run"):
        deny(request, user, "backups.run", run["identifier"])
    try:
        manager().start_verify(run, user.id, user.username)
    except BackupError as exc:
        return back(STORED, str(exc))
    return back(STORED, "Prüfung gestartet. Hatchtower liest die ganze Datei und prüft jede Datei darin;"
                                  " das Ergebnis steht danach in der Liste.")


# -- Wiederherstellen ------------------------------------------------------------------------------

RESTORE_STOP_NEEDS = ("power.stop", "power.start")


def restorable(request: Request, user, run_id: int):
    """Das gespeicherte Backup, wenn der Benutzer es auf seinen Server zurückspielen darf."""
    run = load_run(run_id)
    if run is None:
        return None
    if not user.can(run["identifier"], "backups.restore"):
        deny(request, user, "backups.restore", run["identifier"])
    return run


@app.get("/admin/backups/runs/{run_id}/restore", response_class=HTMLResponse)
async def backup_restore_page(request: Request, run_id: int):
    user = require_backups(request, "backups.restore")
    run = restorable(request, user, run_id)
    if run is None or not run["file"] or run["kind"] != "target":
        return back(STORED, "Diese Backup-Datei gibt es nicht mehr.")
    ident = run["identifier"]
    names = server_names()
    with db.connect() as con:
        state = con.execute("SELECT state FROM server_state WHERE identifier = ?", (ident,)).fetchone()
    return render(request, "restore.html", page_tabs=backup_tabs(user), page_tab=STORED, page_tabs_label="Backups",
                  run=run, server=names.get(ident, run["server_name"] or ident),
                  present=ident in names, at=fmt_dt(run["finished_at"], settings.timezone),
                  size=fmt_size(run["size"]) if run["size"] else "",
                  verified=fmt_dt(run["verified_at"], settings.timezone) if run["verified_at"] else None,
                  running=(state["state"] if state else "") not in ("offline", ""),
                  can_stop=all(user.can(ident, k) for k in RESTORE_STOP_NEEDS), busy=manager().restore_busy(ident),
                  folder=backups.RESTORE_FOLDER)


@app.post("/admin/backups/runs/{run_id}/restore")
async def backup_restore_start(request: Request, run_id: int, mode: str = Form(""), stop: str = Form(""),
                               confirm: str = Form("")):
    user = require_backups(request, "backups.restore")
    await check_csrf(request)
    run = restorable(request, user, run_id)
    if run is None or not run["file"] or run["kind"] != "target":
        return back(STORED, "Diese Backup-Datei gibt es nicht mehr.")
    page = f"/admin/backups/runs/{run_id}/restore"
    ident = run["identifier"]
    if ident not in server_names():
        return back(page, "Den Server gibt es im Panel nicht mehr. Zurückspielen geht nur auf einen vorhandenen Server.")
    if mode not in ("folder", "overwrite"):
        return back(page, "Bitte wählen, wohin das Backup zurückgespielt wird.")
    if mode == "overwrite" and not confirm:
        return back(page, "Bitte bestätigen, dass gleichnamige Dateien auf dem Server ersetzt werden.")
    if stop:
        for key in RESTORE_STOP_NEEDS:
            require(request, user, ident, key)
    try:
        manager().start_restore(run, mode, bool(stop), user.id, user.username)
    except BackupError as exc:
        return back(page, str(exc))
    audit_user(request, user, "backup.restore", server=ident,
               detail={"job": run["job_name"], "datei": run["file"],
                       "wohin": "Originalort" if mode == "overwrite" else "eigener Ordner", "stoppen": bool(stop),
                       "status": "gestartet"})
    return back("/admin/backups/restores", "Wiederherstellung gestartet. Den Fortschritt siehst du oben unter „Laufende Vorgänge“.")


@app.post("/api/backups/restores/{restore_id}/cancel")
async def backup_restore_cancel(request: Request, restore_id: int):
    user = require_backups(request, "backups.restore")
    check_csrf_header(request)
    rs = manager().restores.get(restore_id)
    if rs is None:
        return {"ok": False, "message": i18n.t("Die Wiederherstellung ist schon beendet.")}
    if not user.can(rs.ident, "backups.restore"):
        deny(request, user, "backups.restore", rs.ident)
    manager().cancel_restore(restore_id)
    audit_user(request, user, "backup.cancel", server=rs.ident, detail={"job": rs.job_name, "art": "Wiederherstellung"})
    return {"ok": True, "message": i18n.t("Wird abgebrochen …")}


async def delete_run(request: Request, user, run) -> str | None:
    """Löscht ein gespeichertes Backup (Datei und Eintrag). Gibt den Grund zurück, wenn es gerade nicht geht."""
    if run["status"] in ("queued", "running"):
        return i18n.t("Läuft noch")
    if manager().file_in_use(run["id"]):
        return i18n.t("Dieses Backup wird gerade geprüft oder zurückgespielt. Bitte warten, bis das fertig ist.")
    await manager().delete_file(run)
    with db.connect() as con:
        con.execute("DELETE FROM backup_runs WHERE id = ?", (run["id"],))
    audit_user(request, user, "backup.file_delete", server=run["identifier"],
               detail={"job": run["job_name"], "datei": run["file"]})
    return None


@app.post("/admin/backups/runs/{run_id}/delete")
async def backup_run_delete(request: Request, run_id: int):
    user = require_backups(request, "backups.manage")
    await check_csrf(request)
    run = load_run(run_id)
    if run is None:
        return back(STORED)
    if not has(user, run["identifier"], "backups.manage"):
        deny(request, user, "backups.manage", run["identifier"])
    problem = await delete_run(request, user, run)
    return back(STORED, problem or "Backup gelöscht")


MAX_BULK = 200
MAX_BULK_VERIFY = 10


@app.post("/admin/backups/runs/bulk")
async def backup_runs_bulk(request: Request, action: str = Form("")):
    """Mehrere gespeicherte Backups auf einmal löschen oder prüfen. Fehlt für eines davon das Recht,
    passiert gar nichts (und der Versuch steht im Audit-Log)."""
    key = {"delete": "backups.manage", "verify": "backups.run"}.get(action)
    if key is None:
        return back(STORED, "Bitte eine Aktion wählen.")
    user = require_backups(request, key)
    await check_csrf(request)
    ids = sorted({int(v) for v in (await request.form()).getlist("run") if str(v).isdigit()})[:MAX_BULK]
    with db.connect() as con:
        runs = [r for r in (con.execute("SELECT * FROM backup_runs WHERE id = ?", (i,)).fetchone() for i in ids)
                if r is not None and r["file"]]
    if not runs:
        return back(STORED, "Keine Backups ausgewählt.")
    for run in runs:
        if not has(user, run["identifier"], key):
            deny(request, user, key, run["identifier"])
    problems = []
    if action == "delete":
        done = 0
        for run in runs:
            problem = await delete_run(request, user, run)
            if problem:
                problems.append(f"{run['job_name']} · {run['server_name'] or run['identifier']}: {problem}")
            else:
                done += 1
        message = i18n.t("1 Backup gelöscht.") if done == 1 else i18n.t("{n} Backups gelöscht.", n=done)
    else:
        runs = [r for r in runs if r["kind"] == "target"]
        if len(runs) > MAX_BULK_VERIFY:
            return back(STORED, i18n.t("Bitte höchstens {n} Backups auf einmal prüfen.",
                                                              n=MAX_BULK_VERIFY))
        started = []
        for run in runs:
            try:
                manager().start_verify(run, user.id, user.username)
                started.append(f"verify-{run['id']}")
            except BackupError as exc:
                problems.append(f"{run['job_name']} · {run['server_name'] or run['identifier']}: {exc}")
        message = i18n.t("Prüfung für {n} Backups gestartet. Das Ergebnis erscheint oben, sobald sie fertig sind.",
                         n=len(started))
    if problems:
        message += " " + i18n.t("Nicht möglich: {liste}", liste="; ".join(problems[:5]))
    return back(STORED, message)


# -- Save game of a server: a direct-download job shown as a button on the server page ----------------

def savegame_job(ident: str):
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_jobs WHERE save_for = ?", (ident,)).fetchone()


def savegame_for(user, ident: str) -> dict:
    """What the server page offers: downloading the save game, or setting that up."""
    job = savegame_job(ident)
    can_setup = has(user, ident, "backups.manage") and has(user, ident, "sftp.view")
    if job is None:
        return {"job": None, "can_setup": can_setup}
    ready = bool(backups.job_items(job["id"]))
    return {"job": job, "can_setup": can_setup, "ready": ready, "active": manager().job_active(job["id"]),
            "can_fetch": ready and may_fetch(user, job), "problems": run_problems(user, job) if ready else []}


@app.post("/servers/{ident}/savegame")
async def savegame_setup(request: Request, ident: str):
    """Creates the save-game job of this server (or opens it) and shows its files to pick the save folder."""
    user = current_user(request)
    await check_csrf(request)
    server = get_server_or_deny(request, user, ident)
    require(request, user, ident, "backups.manage")
    require(request, user, ident, "sftp.view")
    job = savegame_job(ident)
    if job is not None:
        return back(browser_url(job["id"], ident))
    name = i18n.t("Spielstand: {server}", server=server["name"])[:80]
    with db.connect() as con:
        now = db.utcnow()
        job_id = con.execute("INSERT INTO backup_jobs (name, target_id, save_for, created_by, created_at, updated_at)"
                             " VALUES (?, NULL, ?, ?, ?, ?)", (name, ident, user.id, now, now)).lastrowid
    audit_user(request, user, "backup.job.create", server=ident, detail={"job": name, "ziel": "download",
                                                                          "spielstand": True})
    return back(browser_url(job_id, ident), "Spielstand-Download angelegt. Wähle jetzt den Ordner oder die Dateien"
                                            " des Spielstands aus.")


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
            "Neuer Backup-Job „{job}“ angelegt ({n} ausgewählt). Ziel und Zeitplan stellst du unter Backups ein.",
            n=len(paths), job=job["name"]))
    return files_back(ident, directory, i18n.t("{n} ausgewählt und zu „{job}“ hinzugefügt", n=len(paths),
                                               job=job["name"]))


def jobs_for_explorer(user, ident: str) -> list:
    """Jobs, denen dieser Benutzer Dateien dieses Servers hinzufügen darf."""
    if not has(user, ident, "backups.manage"):
        return []
    with db.connect() as con:
        jobs = con.execute("SELECT * FROM backup_jobs WHERE all_servers = 0 ORDER BY name COLLATE NOCASE").fetchall()
    return [j for j in jobs if user_may(user, j, "backups.manage")]


# -- Ziele (nur Owner) ---------------------------------------------------------------------------



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
