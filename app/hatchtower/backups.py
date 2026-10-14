"""Backups: Hatchtower holt die Dateien über die Client-API und legt sie als ZIP ab.

Ein Backup-Job enthält Server mit Pfaden („/“ = ganzer Server) oder „Alle Server, auch künftige“.
Ziel ist ein Ordner im Hatchtower-Container (z. B. ein auf Proxmox durchgereichter Mountpoint)
oder der Direktdownload im Browser (nur von Hand). Pro Server und Lauf entsteht eine ZIP-Datei.

Läufe auf ein Ziel stehen in einer gemeinsamen Warteschlange und laufen nacheinander, damit Wings
nicht mehrere Backups gleichzeitig ausliefern muss. Direktdownloads laufen sofort (höchstens einer
pro Benutzer). Gelesen wird nur (Dateiliste, signierte Downloads); geschrieben wird nach Pterodactyl
nur, wenn der Job den Server vorher stoppt und danach wieder startet.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import posixpath
import re
import secrets
import shutil
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import AsyncIterator
from zoneinfo import ZoneInfo

from . import audit, db, i18n, notify
from .permissions import BACKUP_STOP_NEEDS, load_grants
from .ptero import PteroError
from .views import fmt_size

log = logging.getLogger("hatchtower.backups")

ACTOR = "Backup"
STOP_MODES = {"none": "Nicht stoppen", "restart": "Stoppen und danach wieder starten (nur wenn er lief)",
              "stop": "Stoppen und aus lassen"}
NOTIFY_MODES = {"errors": "Nur bei Fehlern", "always": "Immer", "never": "Nie"}
KEEP_COUNTS = (1, 2, 3, 5, 7, 10, 14, 30, 60, 90)
KEEP_DAYS = (1, 3, 7, 14, 30, 60, 90, 180, 365)
STATUS_LABELS = {"queued": "wartet", "running": "läuft", "ok": "fertig", "partial": "mit Fehlern",
                 "error": "fehlgeschlagen", "cancelled": "abgebrochen", "denied": "übersprungen"}
PHASES = {"queued": "Wartet", "warn": "Vorwarnung", "stopping": "Server wird gestoppt",
          "collecting": "Dateien werden gelesen", "copying": "Wird gesichert", "verifying": "Backup-Datei wird geprüft",
          "starting": "Server wird gestartet", "uploading": "Dateien werden zurückgespielt"}

STOP_TIMEOUT = 300          # Sekunden, bis ein gestoppter Server offline sein muss
STOP_POLL = 3
MAX_FILES = 200_000
MAX_DEPTH = 40
TICK = 20
RATE_RESERVE = 0.35         # Anteil am Rate-Limit des Panels, der für Monitoring und Bedienung frei bleibt
RATE_MAX_WAIT = 90
RETRY_WAIT = 3              # Sekunden vor dem zweiten Versuch, wenn ein Download gar nicht erst anläuft
CATCH_UP_HOURS = 6          # Verpasster Zeitplan (Hatchtower war aus) wird so lange danach noch nachgeholt

# Nur hier darf der Dienst schreiben (systemd: ProtectSystem=strict, ReadWritePaths)
ALLOWED_ROOTS = ("/mnt", "/media", "/srv")
# Dateisysteme, die nie als Backup-Ziel taugen
VIRTUAL_FS = {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "mqueue", "securityfs",
              "debugfs", "tracefs", "pstore", "bpf", "autofs", "binfmt_misc", "hugetlbfs", "configfs", "ramfs",
              "efivarfs", "nsfs", "fusectl", "fuse.lxcfs", "rpc_pipefs", "overlay", "squashfs"}


class BackupError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text or "").strip("._") or "backup"


def parse_excludes(text: str | None) -> list[str]:
    return [p.strip().lstrip("/") for p in (text or "").splitlines() if p.strip() and not p.strip().startswith("#")]


def excluded(rel: str, is_dir: bool, patterns: list[str]) -> bool:
    """Muster wie bei .gitignore, vereinfacht: „logs/“ nur Ordner, sonst Name oder Pfad mit * und ?."""
    name = rel.rsplit("/", 1)[-1]
    for p in patterns:
        if p.endswith("/"):
            p = p.rstrip("/")
            if is_dir and (fnmatch(rel, p) or fnmatch(name, p)):
                return True
        elif fnmatch(rel, p) or fnmatch(name, p):
            return True
    return False


def clean_item_path(path: str) -> str:
    return "/" + posixpath.normpath("/" + (path or "/").replace("\\", "/")).lstrip("/")


def minimal_paths(paths: list[str]) -> list[str]:
    """Pfade, die schon in einem anderen enthalten sind, fallen weg („/“ schließt alles ein)."""
    result: list[str] = []
    for p in sorted(set(paths), key=lambda x: (x.count("/"), x)):
        if not any(p == r or r == "/" or p.startswith(r.rstrip("/") + "/") for r in result):
            result.append(p)
    return sorted(result)


# -- Ziele ---------------------------------------------------------------------------

def allowed_path(path: Path, data_dir: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    roots = [data_dir.resolve(), *(Path(r) for r in ALLOWED_ROOTS)]
    return any(resolved == r or r in resolved.parents for r in roots)


def same_device_as_root(path: Path) -> bool:
    try:
        return os.stat(path).st_dev == os.stat("/").st_dev
    except OSError:
        return False


def _unescape(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def list_mounts() -> list[dict]:
    """Eingehängte Datenträger unter /mnt, /media und /srv (z. B. Mountpoints von Proxmox)."""
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen, result = set(), []
    for line in lines:
        parts = line.split()
        if "-" not in parts:
            continue
        sep = parts.index("-")
        mountpoint, fstype = _unescape(parts[4]), parts[sep + 1]
        source = _unescape(parts[sep + 2]) if len(parts) > sep + 2 else ""
        if fstype in VIRTUAL_FS or mountpoint in seen:
            continue
        if not any(mountpoint == r or mountpoint.startswith(r + "/") for r in ALLOWED_ROOTS):
            continue
        if same_device_as_root(Path(mountpoint)):
            continue       # nur ein Ordner der Systemplatte (z. B. von systemd eingeblendet)
        seen.add(mountpoint)
        try:
            usage = shutil.disk_usage(mountpoint)
            free, total = usage.free, usage.total
        except OSError:
            free = total = None
        result.append({"path": mountpoint, "fstype": fstype, "source": source, "free": free, "total": total})
    return sorted(result, key=lambda m: m["path"])


def service_ids() -> tuple[int | None, int | None]:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    return (getuid() if getuid else None, getgid() if getgid else None)


def check_local_path(path: Path, data_dir: Path) -> None:
    """Legt den Ordner bei Bedarf an und prüft, ob Hatchtower dort schreiben darf."""
    if not path.is_absolute() or not allowed_path(path, data_dir):
        raise BackupError(i18n.t("Der Ordner muss unter {roots} oder im Hatchtower-Datenordner liegen.",
                                 roots=", ".join(ALLOWED_ROOTS)))
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f"hatchtower-test-{secrets.token_hex(4)}.tmp"   # ohne Punkt vorne: manche NAS (z. B. ZimaOS) lehnen das ab
        probe.write_bytes(b"ok")
        probe.unlink()
    except PermissionError:
        uid, gid = service_ids()
        raise BackupError(i18n.t(
            "Hatchtower darf in {path} nicht schreiben. Bei einem durchgereichten Mountpoint im unprivilegierten"
            " Container gehört der Ordner auf dem Proxmox-Host meist root. Dort einmal ausführen:"
            " chown -R {uid}:{gid} <Ordner auf dem Host>",
            path=path, uid=100000 + (uid or 0), gid=100000 + (gid or 0))) from None
    except OSError as exc:
        if exc.errno == 30:      # schreibgeschützt: Dienst-Einstellung aus älterer Version
            raise BackupError(i18n.t("{path} ist für den Hatchtower-Dienst schreibgeschützt. Bitte Hatchtower"
                                     " aktualisieren (hatchtower update), danach ist /mnt, /media und /srv freigegeben.",
                                     path=path)) from None
        raise BackupError(i18n.t("Ordner nicht nutzbar: {grund}", grund=exc.strerror or exc)) from None


class Writer:
    """Schreibt eine Backup-Datei als „<name>.part“ und benennt sie erst am Ende um."""

    def __init__(self, target, rel: str):
        self.target, self.rel = target, rel
        self.fileobj = None

    async def open(self):
        self.fileobj = await asyncio.to_thread(self.target.open_part, self.rel)
        return self.fileobj

    async def close(self) -> None:
        await asyncio.to_thread(self.fileobj.close)

    async def commit(self) -> int:
        def _commit():
            self.fileobj.close()
            return self.target.commit(self.rel)
        return await asyncio.to_thread(_commit)

    async def abort(self) -> None:
        def _abort():
            try:
                if self.fileobj:
                    self.fileobj.close()
            finally:
                self.target.discard(self.rel)
        await asyncio.to_thread(_abort)


def clean_rel(rel: str) -> list[str]:
    parts = [p for p in (rel or "").replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise BackupError("Ungültiger Pfad")
    return parts


class LocalTarget:
    """Ordner im Hatchtower-Container: Systemplatte oder durchgereichter Mountpoint.
    Alle Methoden blockieren und werden im Hilfsthread aufgerufen."""

    kind = "local"

    def __init__(self, row):
        self.row = row
        self.base = Path(row["path"])

    def full(self, rel: str) -> Path:
        base = self.base.resolve()
        path = (base / rel).resolve()
        if base not in path.parents:
            raise BackupError("Ungültiger Pfad")
        return path

    def exists(self, rel: str) -> bool:
        return self.full(rel).exists()

    def open_part(self, rel: str):
        path = self.full(rel + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "wb")

    def commit(self, rel: str) -> int:
        final = self.full(rel)
        os.replace(self.full(rel + ".part"), final)
        return final.stat().st_size

    def discard(self, rel: str) -> None:
        self.full(rel + ".part").unlink(missing_ok=True)

    def delete(self, rel: str) -> None:
        self.full(rel).unlink(missing_ok=True)

    def free_bytes(self) -> int | None:
        try:
            return shutil.disk_usage(self.base).free
        except OSError:
            return None

    def read_chunks(self, rel: str, size: int = 1024 * 1024):
        with open(self.full(rel), "rb") as f:
            while chunk := f.read(size):
                yield chunk

    def open_read(self, rel: str):
        return open(self.full(rel), "rb")

    def ready(self, data_dir: Path) -> None:
        """Schutz vor einem nicht eingehängten Laufwerk: Dann ist der Ordner leer und liegt auf der
        Systemplatte – Backups würden still die Platte des Containers füllen."""
        if not self.base.is_dir():
            raise BackupError(i18n.t("Der Ziel-Ordner {path} fehlt – ist das Laufwerk eingehängt?", path=self.base))
        if not allowed_path(self.base, data_dir):
            raise BackupError(i18n.t("Der Ziel-Ordner {path} ist nicht erlaubt.", path=self.base))
        in_data = self.base.resolve() == data_dir.resolve() or data_dir.resolve() in self.base.resolve().parents
        if not in_data and same_device_as_root(self.base):
            raise BackupError(i18n.t("Der Ziel-Ordner {path} liegt auf der Systemplatte – ist das Laufwerk eingehängt?",
                                     path=self.base))

    def check(self, data_dir: Path) -> None:
        self.ready(data_dir)
        check_local_path(self.base, data_dir)

    def close(self) -> None:
        pass


SMB_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
SMB_ERRORS = (
    ("LOGON_FAILURE", "Benutzer oder Passwort falsch"),
    ("ACCOUNT_", "Das Konto ist gesperrt oder abgelaufen"),
    ("BAD_NETWORK_NAME", "Diese Freigabe gibt es auf dem Server nicht"),
    ("ACCESS_DENIED", "Kein Schreibrecht in dieser Freigabe"),
    ("DISK_FULL", "Die Freigabe ist voll"),
    ("NOT_SUPPORTED", "Der Server unterstützt diesen Vorgang nicht"),
    ("Failed to connect", "Server nicht erreichbar (Adresse, Firewall, Port 445?)"),
    ("timed out", "Server nicht erreichbar (Adresse, Firewall, Port 445?)"),
)
SMB_SHARE = re.compile(r"^[^\\/:*?\"<>|]{1,80}$")


class SmbTarget:
    """Freigabe im Netz (Windows, NAS, Samba). Hatchtower spricht SMB selbst, ohne etwas einzuhängen –
    das ginge im unprivilegierten Container gar nicht. Alle Methoden blockieren (Hilfsthread)."""

    kind = "smb"

    def __init__(self, row, vault):
        self.row = row
        self.host, self.share = row["smb_host"] or "", row["smb_share"] or ""
        self.base = row["path"] or ""
        self.user = row["smb_user"] or None
        self.password = (vault.decrypt(row["smb_password_enc"]) if vault and row["smb_password_enc"] else None) or None
        self._cache: dict = {}

    def unc(self, rel: str = "") -> str:
        parts = clean_rel(self.base) + clean_rel(rel)
        return "\\\\" + self.host + "\\" + self.share + "".join("\\" + p for p in parts)

    def _kw(self) -> dict:
        return {"username": self.user, "password": self.password, "connection_cache": self._cache,
                "connection_timeout": 15}

    def _call(self, fn, *args, **kwargs):
        from smbprotocol.exceptions import SMBException
        try:
            return fn(*args, **kwargs, **self._kw())
        except (SMBException, OSError, ValueError) as exc:
            text = str(exc)
            reason = next((i18n.t(msg) for code, msg in SMB_ERRORS if code in text), None) \
                or (text.splitlines() or [""])[0][:200] or exc.__class__.__name__
            raise BackupError(i18n.t("SMB-Freigabe {where}: {grund}", where=f"\\\\{self.host}\\{self.share}",
                                     grund=reason)) from None

    def exists(self, rel: str) -> bool:
        import smbclient.path
        return bool(self._call(smbclient.path.exists, self.unc(rel)))

    def open_part(self, rel: str):
        import smbclient
        parent = "/".join(clean_rel(rel)[:-1])
        self._call(smbclient.makedirs, self.unc(parent), exist_ok=True)
        return self._call(smbclient.open_file, self.unc(rel + ".part"), mode="wb")

    def commit(self, rel: str) -> int:
        import smbclient
        self._call(smbclient.replace, self.unc(rel + ".part"), self.unc(rel))
        return int(self._call(smbclient.stat, self.unc(rel)).st_size)

    def discard(self, rel: str) -> None:
        self.delete(rel + ".part")

    def delete(self, rel: str) -> None:
        import smbclient
        if self.exists(rel):
            self._call(smbclient.remove, self.unc(rel))

    def free_bytes(self) -> int | None:
        import smbclient
        try:
            return int(self._call(smbclient.stat_volume, self.unc()).caller_available_size)
        except BackupError:
            return None

    def read_chunks(self, rel: str, size: int = 1024 * 1024):
        import smbclient
        with self._call(smbclient.open_file, self.unc(rel), mode="rb") as f:
            while chunk := f.read(size):
                yield chunk

    def open_read(self, rel: str):
        import smbclient
        return self._call(smbclient.open_file, self.unc(rel), mode="rb")

    def ready(self, data_dir: Path) -> None:
        import smbclient
        self._call(smbclient.makedirs, self.unc(), exist_ok=True)

    def check(self, data_dir: Path) -> None:
        import smbclient
        self.ready(data_dir)
        probe = f"hatchtower-test-{secrets.token_hex(4)}.tmp"   # ohne Punkt vorne: manche NAS (z. B. ZimaOS) lehnen das ab
        with self._call(smbclient.open_file, self.unc(probe), mode="wb") as f:
            f.write(b"ok")
        self._call(smbclient.remove, self.unc(probe))

    def close(self) -> None:
        try:
            import smbclient
            smbclient.reset_connection_cache(connection_cache=self._cache)
        except Exception:
            pass


def record_check(target_id: int, ok: bool, free: int | None = None, error: str | None = None) -> None:
    """Merkt sich das Ergebnis der letzten Prüfung (Test, Speichern, Backup-Lauf) für die Anzeige."""
    with db.connect() as con:
        con.execute("UPDATE backup_targets SET checked_at = ?, check_ok = ?, check_free = COALESCE(?, check_free),"
                    " check_error = ? WHERE id = ?",
                    (db.utcnow(), 1 if ok else 0, free, None if ok else (error or "")[:300], target_id))


_vault = None      # setzt der BackupManager (SMB-Passwörter liegen verschlüsselt in der Datenbank)


def load_target(target_id: int | None):
    if not target_id:
        return None
    with db.connect() as con:
        row = con.execute("SELECT * FROM backup_targets WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        return None
    return SmbTarget(row, _vault) if row["kind"] == "smb" else LocalTarget(row)


def target_where(row) -> str:
    if row["kind"] == "smb":
        sub = "\\".join(clean_rel(row["path"] or ""))
        return f"\\\\{row['smb_host']}\\{row['smb_share']}" + (f"\\{sub}" if sub else "")
    return row["path"]


def target_label(row) -> str:
    if row is None:
        return i18n.t("Direktdownload")
    return f"{row['name']} ({target_where(row)})"


# -- Jobs ------------------------------------------------------------------------------

def load_job(job_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM backup_jobs WHERE id = ?", (job_id,)).fetchone()


def job_items(job_id: int) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    with db.connect() as con:
        for r in con.execute("SELECT identifier, path FROM backup_job_items WHERE job_id = ? ORDER BY identifier, path",
                             (job_id,)):
            result.setdefault(r["identifier"], []).append(r["path"])
    return result


def present_servers() -> dict[str, str]:
    with db.connect() as con:
        return {r["identifier"]: r["name"] for r in con.execute(
            "SELECT identifier, name FROM servers WHERE present = 1 ORDER BY name COLLATE NOCASE")}


def job_idents(job) -> list[str]:
    """Server, auf die sich ein Job bezieht (für Rechte: auch in Pterodactyl gelöschte)."""
    if job["all_servers"]:
        return list(present_servers())
    return list(job_items(job["id"]))


def has(user, ident: str, key: str) -> bool:
    """Recht ohne Rücksicht auf den Read-Only-Modus (Jobs lassen sich auch dann einrichten)."""
    return user.is_owner or key in user.grants.get(ident, set())


def user_may(user, job, key: str) -> bool:
    if user.is_owner:
        return True
    if job["all_servers"]:
        return False
    idents = list(job_items(job["id"]))
    if not idents:
        return job["created_by"] == user.id and user.can_any(key)
    return all(has(user, i, key) for i in idents)


def stop_problems(user, idents: list[str], stop_mode: str, warn_command: str | None, names: dict[str, str]) -> list[str]:
    """Was dem Benutzer fehlt, um den Job mit dieser Stopp-Einstellung auf diesen Servern zu betreiben."""
    if user.is_owner or stop_mode == "none":
        return []
    from .permissions import BY_KEY
    problems = []
    for ident in idents:
        for need in BACKUP_STOP_NEEDS.get(stop_mode, ()):
            if not has(user, ident, need):
                problems.append(i18n.t("„{recht}“ für {server}", recht=i18n.t(BY_KEY[need].label),
                                       server=names.get(ident, ident)))
        if warn_command and not has(user, ident, "console.command"):
            problems.append(i18n.t("„{recht}“ für {server}", recht=i18n.t("Befehle senden"),
                                   server=names.get(ident, ident)))
    return problems


def creator_allows(job, ident: str) -> bool:
    """Geplante Läufe eines Benutzers (nicht Owner) nur, solange er die nötigen Rechte noch hat."""
    if not job["created_by"]:
        return True
    with db.connect() as con:
        creator = con.execute("SELECT role, is_active FROM users WHERE id = ?", (job["created_by"],)).fetchone()
    if creator is None or creator["role"] == "owner":
        return creator is not None
    need = {"backups.manage", *BACKUP_STOP_NEEDS.get(job["stop_mode"], ())}
    if job["stop_mode"] != "none" and job["warn_command"]:
        need.add("console.command")
    return bool(creator["is_active"]) and need <= load_grants(job["created_by"]).get(ident, set())


def retention_drop(rows, keep_count: int | None, keep_days: int | None, now: datetime | None = None) -> list:
    """Welche gespeicherten Backups (neueste zuerst) die Aufbewahrung löschen darf.

    Nie gelöscht werden das neueste Backup und das neueste vollständige („ok“). So verdrängt ein Backup, in
    dem Dateien fehlen, nie das letzte vollständige – dafür bleibt dann eins mehr liegen als eingestellt."""
    if not keep_count and not keep_days:
        return []
    newest_ok = next((r["id"] for r in rows if r["status"] == "ok"), None)
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=keep_days or 0)).isoformat(timespec="seconds")
    return [r for i, r in enumerate(rows) if i > 0 and r["id"] != newest_ok and (
        (keep_count and i >= keep_count) or (keep_days and (r["finished_at"] or "") < cutoff))]


WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def describe_schedule(job) -> str:
    t = i18n.t
    days = {int(d) for d in (job["weekdays"] or "") if d.isdigit()}
    if not days or not job["time"] or not job["target_id"]:
        return t("nur von Hand")
    if days == set(range(7)):
        day_text = t("täglich")
    elif days == set(range(5)):
        day_text = t("Mo–Fr")
    elif days == {5, 6}:
        day_text = t("Sa+So")
    else:
        day_text = ", ".join(t(WEEKDAYS[d]) for d in sorted(days))
    return t("{days} um {time} Uhr", days=day_text, time=job["time"])


# -- Dateien einsammeln ----------------------------------------------------------------

async def pace(client, rs: "RunState") -> None:
    """Vor jeder Anfrage an das Panel: warten, solange das Rate-Limit knapp ist (Downloads von Wings zählen
    nicht mit). Ohne frische Angabe des Panels (älter als 20 s) geht es weiter, die Anfrage frischt sie auf."""
    waited = 0
    try:
        while waited < RATE_MAX_WAIT:
            rate = getattr(client, "rate", None)
            if not rate:
                return
            limit, remaining, seen = rate
            if remaining >= limit * RATE_RESERVE or time.monotonic() - seen > 20:
                return
            rs.throttled = True
            rs.check_cancel()
            await asyncio.sleep(1)
            waited += 1
    finally:
        rs.throttled = False


@dataclass
class Entry:
    path: str        # Pfad auf dem Server
    arcname: str     # Pfad im ZIP (relativ zum Serverordner)
    is_dir: bool
    size: int
    modified: str | None


async def collect(client, ident: str, paths: list[str], patterns: list[str], rs: "RunState") -> tuple[list[Entry], list[str]]:
    """Alle Dateien der Pfade (Ordner rekursiv), ohne die ausgeschlossenen. Liefert auch fehlende Pfade."""
    entries: list[Entry] = []
    missing: list[str] = []
    queue: list[tuple[str, int]] = []

    def add(item: dict, path: str) -> None:
        rel = path.lstrip("/")
        if item["is_file"]:
            entries.append(Entry(path, rel, False, item["size"], item.get("modified_at")))
            rs.total_bytes += item["size"]
            rs.total_files += 1
            if rs.total_files > MAX_FILES:
                raise BackupError(i18n.t("Mehr als {n} Dateien – bitte einzelne Ordner sichern oder Ausschlüsse setzen.",
                                         n=MAX_FILES))
        else:
            entries.append(Entry(path, rel + "/", True, 0, item.get("modified_at")))

    for path in minimal_paths(paths):
        rs.check_cancel()
        if path == "/":
            queue.append(("/", 1))
            continue
        parent, name = posixpath.split(path)
        await pace(client, rs)
        listing = {f["name"]: f for f in await client.list_files(ident, parent or "/")}
        item = listing.get(name)
        if item is None:
            missing.append(i18n.t("{path}: nicht vorhanden", path=path))
            continue
        add(item, path)
        if not item["is_file"]:
            queue.append((path, 1))

    while queue:
        rs.check_cancel()
        path, depth = queue.pop(0)
        if depth > MAX_DEPTH:
            missing.append(i18n.t("{path}: zu tief verschachtelt", path=path))
            continue
        try:
            await pace(client, rs)
            listing = await client.list_files(ident, path)
        except PteroError as exc:
            if path == "/":
                raise
            missing.append(i18n.t("{path}: nicht lesbar ({grund})", path=path, grund=exc))
            continue
        for item in listing:
            child = posixpath.join(path, item["name"])
            rel = child.lstrip("/")
            if excluded(rel, not item["is_file"], patterns):
                continue
            add(item, child)
            if not item["is_file"]:
                queue.append((child, depth + 1))
    return entries, missing


# -- ZIP -------------------------------------------------------------------------------

class Sink(io.RawIOBase):
    """Nimmt ZIP-Bytes entgegen, bis sie an den Browser weitergereicht werden."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self._buf += data
        return len(data)

    def drain(self) -> bytes:
        data = bytes(self._buf)
        self._buf.clear()
        return data


def _date_time(value: str | None, tz: ZoneInfo) -> tuple[int, int, int, int, int, int]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(tz)
        if dt.year >= 1980:
            return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    except ValueError:
        pass
    return (1980, 1, 1, 0, 0, 0)


class ZipBuilder:
    """Schreibt ein ZIP in eine Datei oder in einen Sink (Direktdownload).

    Komprimieren und Schreiben laufen in einem Hilfsthread, damit die Weboberfläche flüssig bleibt.
    """

    def __init__(self, fileobj, compress: bool, tz: str):
        self.sink = fileobj if isinstance(fileobj, Sink) else None
        self.method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        self.zf = zipfile.ZipFile(fileobj, "w", self.method, allowZip64=True,
                                  compresslevel=6 if compress else None)
        self.zone = ZoneInfo(tz)
        self.failed: list[str] = []
        self.saved = 0             # Dateien, die vollständig im ZIP stehen
        self.names: list[str] = []  # alle Einträge, für die Prüfung danach

    def drain(self) -> bytes:
        return self.sink.drain() if self.sink else b""

    @staticmethod
    async def _begin(client, ident: str, entry: Entry, rs: "RunState"):
        """Startet den Download einer Datei und liefert (Stream, erstes Stück). Scheitert schon der Anfang,
        gibt es einen zweiten Versuch – bis dahin steht von der Datei noch nichts im ZIP."""
        for attempt in (1, 2):
            stream = None
            try:
                await pace(client, rs)
                url = await client.download_url(ident, entry.path)
                stream = client.stream_download(url)
                return stream, await anext(stream, None)
            except PteroError:
                if stream is not None:
                    await stream.aclose()
                if attempt == 2:
                    raise
                for _ in range(RETRY_WAIT):
                    rs.check_cancel()
                    await asyncio.sleep(1)

    async def add(self, client, ident: str, entries: list[Entry], prefix: str, rs: "RunState") -> AsyncIterator[bytes]:
        for entry in entries:
            rs.check_cancel()
            info = zipfile.ZipInfo(prefix + entry.arcname, _date_time(entry.modified, self.zone))
            if entry.is_dir:
                await asyncio.to_thread(self.zf.writestr, info, b"")
                self.names.append(info.filename)
                yield self.drain()
                continue
            info.compress_type = self.method
            info.file_size = entry.size
            try:
                stream, chunk = await self._begin(client, ident, entry, rs)
            except PteroError as exc:
                self.failed.append(f"{entry.path}: {exc}")
                rs.done_files += 1
                continue
            out = await asyncio.to_thread(self.zf.open, info, "w", force_zip64=entry.size > 2 ** 31)
            written = 0
            try:
                while chunk is not None:
                    rs.check_cancel()
                    await asyncio.to_thread(out.write, chunk)
                    written += len(chunk)
                    rs.done_bytes += len(chunk)
                    data = self.drain()
                    if data:
                        yield data
                    chunk = await anext(stream, None)
                self.saved += 1
            except PteroError as exc:
                # Was schon geschrieben ist, lässt sich aus einem ZIP nicht mehr entfernen: deutlich vermerken
                self.failed.append(i18n.t("{path}: unvollständig, nach {size} abgebrochen ({grund})",
                                          path=entry.path, size=fmt_size(written), grund=exc))
            finally:
                await asyncio.to_thread(out.close)
                await stream.aclose()
            self.names.append(info.filename)
            rs.done_files += 1
            yield self.drain()

    async def note(self, name: str, lines: list[str]) -> bytes:
        if lines:
            await asyncio.to_thread(self.zf.writestr, name, "\n".join(lines) + "\n")
        return self.drain()

    async def close(self) -> bytes:
        await asyncio.to_thread(self.zf.close)
        return self.drain()


def note_names() -> set[str]:
    """Hinweisdateien, die Hatchtower selbst ins ZIP legt (in allen Sprachen) – werden nicht zurückgespielt."""
    names = set()
    for text in ("FEHLER.txt", "ABGEBROCHEN.txt"):
        names.add(text)
        names.update(catalog.get(text, text) for catalog in i18n._catalogs.values())
    return names


def verify_file(target, rel: str, expected: list[str] | None, rs=None) -> str | None:
    """Liest eine fertige Backup-Datei vollständig und prüft jede enthaltene Datei gegen ihre Prüfsumme
    (CRC-32). Liefert None, wenn alles stimmt, sonst den Grund. Blockiert (Hilfsthread)."""
    try:
        with target.open_read(rel) as raw, zipfile.ZipFile(raw) as zf:
            infos = zf.infolist()
            if expected is not None:
                missing = set(expected) - {i.filename for i in infos}
                if missing:
                    return i18n.t("{n} Einträge fehlen in der Datei, z. B. {name}", n=len(missing),
                                  name=sorted(missing)[0])
            if rs is not None:
                rs.verify_total = sum(i.file_size for i in infos) or 1
                rs.verify_done = 0
            for info in infos:
                if info.is_dir():
                    continue
                with zf.open(info) as f:
                    while chunk := f.read(1024 * 1024):
                        if rs is not None:
                            rs.check_cancel()
                            rs.verify_done += len(chunk)
    except Cancelled:
        raise
    except BackupError as exc:
        return str(exc)
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, ValueError) as exc:
        return i18n.t("Datei beschädigt oder unvollständig ({grund})", grund=str(exc)[:200] or exc.__class__.__name__)
    return None


# -- Wiederherstellen: Plan und Pakete ---------------------------------------------------------

RESTORE_FOLDER = "/hatchtower-restore"
RESTORE_BATCH_BYTES = 20 * 1024 ** 2     # pro Upload-Anfrage; Wings erlaubt standardmäßig 100 MB
RESTORE_BATCH_FILES = 100
RESTORE_IN_MEMORY = 8 * 1024 ** 2        # größere Dateien gehen über eine temporäre Datei


def restore_plan(infos: list[zipfile.ZipInfo], skip: set[str]) -> tuple[list[zipfile.ZipInfo], list[str]]:
    """Dateien und leere Ordner aus dem ZIP, die zurückgespielt werden. Lehnt Pfade ab, die aus dem
    Serverordner hinausführen könnten (das ZIP liegt auf einem Ziel, das auch andere beschreiben können)."""
    files, dirs = [], []
    for info in infos:
        name = info.filename
        parts = name.rstrip("/").split("/")
        if (not name or name.startswith("/") or "\x00" in name or re.fullmatch(r"[A-Za-z]:", parts[0])
                or any(p in ("", ".", "..") for p in parts)):
            raise BackupError(i18n.t("Unsicherer Pfad im Backup, es wurde nichts zurückgespielt: {name}", name=name[:200]))
        if info.is_dir():
            dirs.append(name.rstrip("/"))
        elif len(parts) == 1 and name in skip:
            continue                       # Hinweisdatei von Hatchtower (FEHLER.txt)
        else:
            files.append(info)
    with_files = {posixpath.dirname(i.filename) for i in files}
    used = set()
    for d in with_files:                   # alle Ordner, in denen (auch tiefer) Dateien landen
        while d:
            used.add(d)
            d = posixpath.dirname(d)
    return files, sorted(d for d in dirs if d not in used)


def restore_batches(files: list[zipfile.ZipInfo]):
    """Pakete (Ordner, Dateien) für je eine Upload-Anfrage: gleicher Ordner, begrenzte Größe und Anzahl."""
    groups: dict[str, list[zipfile.ZipInfo]] = {}
    for info in files:
        groups.setdefault(posixpath.dirname(info.filename), []).append(info)
    for directory in sorted(groups):
        batch, size = [], 0
        for info in groups[directory]:
            if batch and (size + info.file_size > RESTORE_BATCH_BYTES or len(batch) >= RESTORE_BATCH_FILES):
                yield directory, batch
                batch, size = [], 0
            batch.append(info)
            size += info.file_size
        if batch:
            yield directory, batch


def _read_batch(zf: zipfile.ZipFile, batch: list[zipfile.ZipInfo], tmp: Path) -> list[tuple[str, object]]:
    """Entpackt ein Paket für den Upload: kleine Dateien in den Speicher, große in eine temporäre Datei."""
    import tempfile
    parts = []
    try:
        for info in batch:
            f = io.BytesIO() if info.file_size <= RESTORE_IN_MEMORY else tempfile.TemporaryFile(dir=tmp)
            parts.append((posixpath.basename(info.filename), f))
            with zf.open(info) as src:
                shutil.copyfileobj(src, f, 1024 * 1024)
            f.seek(0)
    except BaseException:
        for _, f in parts:
            f.close()
        raise
    return parts


# -- Läufe -----------------------------------------------------------------------------

@dataclass
class RunState:
    run_id: int
    batch: str
    job_id: int | None
    job_name: str
    ident: str
    server_name: str
    kind: str
    trigger: str
    user_id: int | None
    user_name: str | None = None
    paths: list[str] = field(default_factory=list)
    phase: str = "queued"
    total_bytes: int = 0
    done_bytes: int = 0
    total_files: int = 0
    done_files: int = 0
    began: float = 0.0
    until: float = 0.0
    stopped: bool = False
    cancelled: bool = False
    throttled: bool = False
    verify_total: int = 0
    verify_done: int = 0
    source: int | None = None       # Prüfung/Wiederherstellung: ID des gespeicherten Backups

    def check_cancel(self) -> None:
        if self.cancelled:
            raise Cancelled()

    @property
    def percent(self) -> int:
        if self.phase == "verifying":
            return int(min(99, self.verify_done / (self.verify_total or 1) * 100))
        if self.phase not in ("copying", "uploading"):
            return 0
        if self.total_bytes:
            pct = self.done_bytes / self.total_bytes * 100
        elif self.total_files:
            pct = self.done_files / self.total_files * 100
        else:
            pct = 0
        return int(min(99, pct))      # 100 % erst, wenn der Lauf wirklich fertig ist

    def view(self, position: int | None) -> dict:
        t = i18n.t
        phase = t(PHASES.get(self.phase, self.phase))
        if self.phase == "warn":
            phase = t("Vorwarnung gesendet, Stopp in {n} min", n=max(1, round((self.until - time.monotonic()) / 60)))
        elif self.phase == "queued" and position:
            phase = t("Wartet (Platz {n})", n=position)
        elif self.phase == "collecting":
            phase = t("Dateien werden gelesen … {n} gefunden", n=self.total_files)
        elif self.phase == "verifying":
            phase = t("Backup-Datei wird geprüft … {n} %", n=self.percent)
        if self.throttled:
            phase += " · " + t("wartet kurz auf {panel} (Anfragen-Limit)")
        elapsed = time.monotonic() - self.began if self.began else 0
        rate = self.done_bytes / elapsed if elapsed > 1 and self.phase in ("copying", "uploading") else 0
        # Prüfungen und Wiederherstellungen haben eigene Nummern: in der Anzeige mit Vorsilbe
        key = self.run_id if self.kind in ("target", "download") else f"{self.kind}-{self.run_id}"
        cancel = {"verify": None, "restore": f"/api/backups/restores/{self.run_id}/cancel"}.get(
            self.kind, f"/api/backups/runs/{self.run_id}/cancel")
        return {"id": key, "job": self.job_name, "job_id": self.job_id, "server": self.server_name,
                "ident": self.ident, "kind": self.kind, "phase": phase, "state": self.phase,
                "percent": self.percent, "done": self.done_bytes, "total": self.total_bytes,
                "files_done": self.done_files, "files_total": self.total_files, "rate": rate,
                "throttled": self.throttled, "cancel_url": cancel}


class BackupManager:
    def __init__(self, state, vault, settings):
        self.state = state          # app.state: ptero, automation, monitor
        self.vault = vault
        self.settings = settings
        self.runs: dict[int, RunState] = {}
        self._queue: list[int] = []
        global _vault
        _vault = vault
        self._wake: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []
        self._background: set[asyncio.Task] = set()
        self._downloads: set[int] = set()
        self.checks: dict[int, RunState] = {}       # laufende Prüfungen gespeicherter Backups (nach Lauf-ID)
        self.restores: dict[int, RunState] = {}     # laufende Wiederherstellungen (nach ID in backup_restores)

    @property
    def client(self):
        return self.state.ptero

    # -- Lebenszyklus --------------------------------------------------------

    def start(self) -> None:
        self.recover()
        self._tasks = [asyncio.create_task(self._worker(), name="hatchtower-backup"),
                       asyncio.create_task(self._scheduler(), name="hatchtower-backup-plan")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _event(self) -> asyncio.Event:
        if self._wake is None:
            self._wake = asyncio.Event()
        return self._wake

    def recover(self) -> None:
        """Nach einem Neustart von Hatchtower: unterbrochene Läufe abschließen, Reste löschen,
        für ein Backup gestoppte Server wieder starten."""
        with db.connect() as con:
            rows = con.execute("SELECT * FROM backup_runs WHERE status IN ('queued', 'running')").fetchall()
            con.execute("UPDATE backup_runs SET status = 'error', detail = ?, finished_at = ?, file = NULL"
                        " WHERE status IN ('queued', 'running')",
                        (i18n.t("Unterbrochen: Hatchtower wurde neu gestartet"), db.utcnow()))
        for row in rows:
            if row["file"] and row["kind"] == "target":
                target = load_target(row["target_id"])
                if target is not None:
                    self._spawn(self._discard(target, row["file"]))
            if row["power_restore"] and not self.settings.read_only:
                self._spawn(self._restore_power(row["identifier"], row["job_name"]))
        with db.connect() as con:
            restores = con.execute("SELECT * FROM backup_restores WHERE status = 'running'").fetchall()
            con.execute("UPDATE backup_restores SET status = 'error', detail = ?, finished_at = ?, power_restore = 0"
                        " WHERE status = 'running'",
                        (i18n.t("Unterbrochen: Hatchtower wurde neu gestartet. Ein Teil der Dateien kann schon"
                                " zurückgespielt sein – die Wiederherstellung einfach noch einmal starten."), db.utcnow()))
        for row in restores:
            if row["power_restore"] and not self.settings.read_only:
                self._spawn(self._restore_power(row["identifier"], row["job_name"] or ""))
        tmp = self.settings.data_dir / "restore-tmp"
        if tmp.is_dir():
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    async def _discard(target, rel: str) -> None:
        try:
            await asyncio.to_thread(target.discard, rel)
        except (OSError, BackupError):
            pass
        finally:
            target.close()

    async def _restore_power(self, ident: str, job_name: str) -> None:
        try:
            self.state.automation.note_power(ident, "start")
            await self.client.power(ident, "start")
            audit.record("power.start", username=ACTOR, ip="intern", server=ident,
                         detail=i18n.t("{job}: nach unterbrochenem Backup wieder gestartet", job=job_name))
        except Exception as exc:
            audit.record("power.start", result="error", username=ACTOR, ip="intern", server=ident,
                         detail=f"{job_name}: {exc}")

    # -- Anzeige ------------------------------------------------------------------

    def active_views(self, visible) -> list[dict]:
        result = []
        for rs in sorted(self.runs.values(), key=lambda r: r.run_id):
            if not visible(rs.ident):
                continue
            pos = self._queue.index(rs.run_id) + 1 if rs.run_id in self._queue else None
            result.append(rs.view(pos))
        for rs in [*self.checks.values(), *self.restores.values()]:
            if visible(rs.ident):
                result.append(rs.view(None))
        return result

    def job_active(self, job_id: int) -> bool:
        return any(r.job_id == job_id for r in self.runs.values())

    # -- Starten --------------------------------------------------------------------

    def _servers_of(self, job) -> list[tuple[str, str, list[str]]]:
        names = present_servers()
        if job["all_servers"]:
            return [(i, n, ["/"]) for i, n in names.items()]
        return [(i, names[i], paths) for i, paths in job_items(job["id"]).items() if i in names]

    def _create_runs(self, job, kind: str, trigger: str, user_id: int | None, user_name: str | None,
                     status: str) -> list[RunState]:
        servers = self._servers_of(job)
        if not servers:
            raise BackupError(i18n.t("Der Job enthält keine (vorhandenen) Server."))
        batch = secrets.token_hex(8)
        states = []
        now = db.utcnow()
        with db.connect() as con:
            for ident, name, paths in servers:
                run_id = con.execute(
                    "INSERT INTO backup_runs (batch, job_id, job_name, identifier, server_name, target_id, kind, status,"
                    " trigger, started_by, started_name, queued_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (batch, job["id"], job["name"], ident, name, job["target_id"], kind, status, trigger, user_id,
                     user_name, now)).lastrowid
                states.append(RunState(run_id, batch, job["id"], job["name"], ident, name, kind, trigger, user_id,
                                       user_name, paths))
        for rs in states:
            self.runs[rs.run_id] = rs
        return states

    def enqueue(self, job, user_id: int | None = None, user_name: str | None = None, trigger: str = "manual") -> int:
        """Stellt einen Lauf (alle Server des Jobs) in die Warteschlange. Liefert die Anzahl Server."""
        if not job["target_id"]:
            raise BackupError(i18n.t("Dieser Job lädt direkt herunter und läuft nur von Hand."))
        if load_target(job["target_id"]) is None:
            raise BackupError(i18n.t("Das Backup-Ziel gibt es nicht mehr."))
        if self.job_active(job["id"]):
            raise BackupError(i18n.t("Dieser Job läuft gerade schon."))
        states = self._create_runs(job, "target", trigger, user_id, user_name, "queued")
        self._queue.extend(rs.run_id for rs in states)
        self._event().set()
        return len(states)

    def cancel(self, run_id: int) -> bool:
        rs = self.runs.get(run_id)
        if rs is None:
            return False
        rs.cancelled = True
        if run_id in self._queue:
            self._queue.remove(run_id)
            self._finish(rs, "cancelled", i18n.t("Abgebrochen, bevor es losging"))
            self._batch_check(rs)
        return True

    # -- Abarbeiten -----------------------------------------------------------------

    async def _worker(self) -> None:
        wake = self._event()
        while True:
            while not self._queue:
                wake.clear()
                await wake.wait()
            run_id = self._queue.pop(0)
            rs = self.runs.get(run_id)
            if rs is None:
                continue
            try:
                await self._run_to_target(rs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Backup-Lauf %s", run_id)
                self._finish(rs, "error", str(exc) or exc.__class__.__name__)
            self._batch_check(rs)

    def _mark_started(self, rs: RunState, **cols) -> None:
        rs.began = time.monotonic()
        sets = ", ".join(f"{k} = ?" for k in ("status", "started_at", *cols))
        with db.connect() as con:
            con.execute(f"UPDATE backup_runs SET {sets} WHERE id = ?",
                        ("running", db.utcnow(), *cols.values(), rs.run_id))

    def _finish(self, rs: RunState, status: str, detail: str = "", **cols) -> None:
        self.runs.pop(rs.run_id, None)
        i18n.use_default()
        cols.setdefault("power_restore", 0)
        sets = ", ".join(f"{k} = ?" for k in ("status", "detail", "finished_at", "files", "data_bytes", *cols))
        with db.connect() as con:
            con.execute(f"UPDATE backup_runs SET {sets} WHERE id = ?",
                        (status, detail[:2000] or None, db.utcnow(), rs.done_files, rs.done_bytes, *cols.values(),
                         rs.run_id))
        audit.record("backup.run", result={"ok": "ok", "partial": "error", "error": "error", "cancelled": "blocked",
                                           "denied": "denied"}.get(status, "ok"),
                     user_id=rs.user_id, username=rs.user_name, ip="intern",
                     server=rs.ident, detail=f"{rs.job_name}: {i18n.t(STATUS_LABELS.get(status, status))}"
                                             + (f" – {detail}" if detail else ""))

    async def _run_to_target(self, rs: RunState) -> None:
        job = load_job(rs.job_id) if rs.job_id else None
        target = load_target(job["target_id"]) if job is not None else None
        if job is None or target is None:
            self._finish(rs, "error", i18n.t("Job oder Ziel gibt es nicht mehr."))
            return
        if rs.trigger == "schedule" and not creator_allows(job, rs.ident):
            self._finish(rs, "denied", i18n.t("übersprungen: dem Ersteller fehlt das Recht dafür"))
            return
        if any(r.ident == rs.ident for r in self.restores.values()):
            # Ein Backup mitten in einer Wiederherstellung wäre ein halber Stand
            self._finish(rs, "error", i18n.t("übersprungen: Auf dem Server läuft gerade eine Wiederherstellung."))
            return
        try:
            await asyncio.to_thread(target.ready, self.settings.data_dir)
            rel = await asyncio.to_thread(self._file_name, job, rs, target)
        except BackupError as exc:
            self._mark_started(rs)
            self._finish(rs, "error", str(exc))
            record_check(job["target_id"], False, error=str(exc))
            target.close()
            return
        self._mark_started(rs, file=rel)
        status, detail, cols = "error", "", {}
        try:
            await self._before(rs, job)
            rs.phase = "collecting"
            entries, missing = await collect(self.client, rs.ident, rs.paths, parse_excludes(job["excludes"]), rs)
            free = await asyncio.to_thread(target.free_bytes)
            need = rs.total_bytes // 2 if job["compress"] else rs.total_bytes
            if free is not None and free < need + 50 * 1024 ** 2:
                raise BackupError(i18n.t("Zu wenig Platz am Ziel: {free} frei, gebraucht etwa {need}.",
                                         free=fmt_size(free), need=fmt_size(need)))
            rs.phase = "copying"
            writer = Writer(target, rel)
            fileobj = await writer.open()
            builder = None
            try:
                builder = ZipBuilder(fileobj, bool(job["compress"]), self.settings.timezone)
                async for _ in builder.add(self.client, rs.ident, entries, "", rs):
                    pass
                wanted = sum(1 for e in entries if not e.is_dir)
                if wanted and not builder.saved:
                    # Ein ZIP ganz ohne Dateien ist kein Backup – und darf bei der Aufbewahrung keins verdrängen
                    raise BackupError(i18n.t("Keine einzige Datei ließ sich sichern. Erster Fehler: {grund}",
                                             grund=builder.failed[0] if builder.failed else "?"))
                await builder.note(i18n.t("FEHLER.txt"), self._problem_lines(builder.failed, missing))
                await builder.close()
                await writer.close()
                # Erst prüfen, dann umbenennen: Eine fehlerhafte Datei erscheint nie als fertiges Backup
                rs.phase = "verifying"
                problem = await asyncio.to_thread(verify_file, target, rel + ".part", builder.names, rs)
                if problem:
                    raise BackupError(i18n.t("Die fertige Backup-Datei ist fehlerhaft und wurde verworfen: {grund}",
                                             grund=problem))
                size = await writer.commit()
                record_check(job["target_id"], True, await asyncio.to_thread(target.free_bytes))
            except BaseException:
                if builder is not None:      # ZIP schließen, bevor die Datei verworfen wird
                    try:
                        await asyncio.to_thread(builder.zf.close)
                    except Exception:
                        pass
                await writer.abort()
                raise
            problems = builder.failed + missing
            status = "partial" if problems else "ok"
            detail = "; ".join(problems[:20]) + (" …" if len(problems) > 20 else "")
            cols = {"file": rel, "size": size, "verified_at": db.utcnow(), "verify_ok": 1}
        except Cancelled:
            status, detail, cols = "cancelled", i18n.t("Abgebrochen"), {"file": None}
        except (BackupError, PteroError, OSError) as exc:
            status, detail, cols = "error", str(exc), {"file": None}
        except Exception as exc:
            # Unerwarteter Fehler: trotzdem weiter unten den Server wieder starten
            log.exception("Backup-Lauf %s", rs.run_id)
            status, detail, cols = "error", i18n.t("Unerwarteter Fehler: {grund}",
                                                   grund=str(exc)[:200] or exc.__class__.__name__), {"file": None}
        try:
            note = await self._after(rs, job)
            self._finish(rs, status, (detail + " · " + note if detail and note else detail or note), **cols)
            if status in ("ok", "partial"):
                await self._apply_retention(job, rs.ident)
        finally:
            target.close()

    @staticmethod
    def _problem_lines(failed: list[str], missing: list[str]) -> list[str]:
        if not failed and not missing:
            return []
        return [i18n.t("Diese Dateien oder Ordner fehlen im Backup oder sind unvollständig:"), "", *missing, *failed]

    def _file_name(self, job, rs: RunState, target) -> str:
        stamp = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%Y-%m-%d_%H-%M")
        base = f"{slug(job['name'])}/{slug(rs.server_name)}/{stamp}"
        rel, n = base + ".zip", 2
        while target.exists(rel) or target.exists(rel + ".part"):
            rel, n = f"{base}_{n}.zip", n + 1
        return rel

    # -- Stoppen und Starten ------------------------------------------------------------

    async def _sleep(self, rs: RunState, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rs.check_cancel()
            await asyncio.sleep(min(1.0, end - time.monotonic()))

    async def _before(self, rs: RunState, job) -> None:
        """Vorwarnung und Stopp, falls der Job das will. Merkt sich in rs.stopped, ob gestoppt wurde."""
        if job["stop_mode"] == "none":
            return
        state = (await self.client.resources(rs.ident))["state"]
        if state == "offline":
            return
        if self.settings.read_only:
            raise BackupError(i18n.t("Stoppen vor dem Backup ist im Read-Only-Modus nicht möglich."))
        if job["warn_minutes"] and job["warn_command"] and state == "running":
            minutes = int(job["warn_minutes"])
            text = job["warn_command"].replace("{minuten}", str(minutes)).replace("{minutes}", str(minutes))
            try:
                await self.client.command(rs.ident, text)
                audit.record("backup.warn", username=ACTOR, ip="intern", server=rs.ident, detail=f"{job['name']}: {text}")
            except PteroError as exc:
                audit.record("backup.warn", result="error", username=ACTOR, ip="intern", server=rs.ident,
                             detail=f"{job['name']}: {exc}")
            rs.phase = "warn"
            rs.until = time.monotonic() + minutes * 60
            await self._sleep(rs, minutes * 60)
        if job["stop_mode"] == "restart":
            with db.connect() as con:
                con.execute("UPDATE backup_runs SET power_restore = 1 WHERE id = ?", (rs.run_id,))
        await self._stop_and_wait(rs, i18n.t("{job}: vor dem Backup", job=job["name"]))

    async def _stop_and_wait(self, rs: RunState, detail: str) -> None:
        """Stoppt den Server und wartet, bis er wirklich aus ist. Setzt rs.stopped."""
        rs.phase = "stopping"
        self.state.automation.note_power(rs.ident, "stop")
        rs.stopped = True
        await self.client.power(rs.ident, "stop")
        audit.record("power.stop", username=ACTOR, ip="intern", server=rs.ident, detail=detail)
        self.state.monitor.follow(rs.ident)
        deadline = time.monotonic() + STOP_TIMEOUT
        while True:
            await self._sleep(rs, STOP_POLL)
            self.state.automation.note_power(rs.ident, "stop")
            if (await self.client.resources(rs.ident))["state"] == "offline":
                return
            if time.monotonic() > deadline:
                raise BackupError(i18n.t("Der Server ist nach {n} Minuten noch nicht gestoppt.", n=STOP_TIMEOUT // 60))

    async def _after(self, rs: RunState, job) -> str:
        """Startet einen für das Backup gestoppten Server wieder (nur Modus „wieder starten“)."""
        if not rs.stopped or job["stop_mode"] != "restart":
            return ""
        return await self._start_again(rs, i18n.t("{job}: nach dem Backup", job=job["name"]), job["name"])

    async def _start_again(self, rs: RunState, detail: str, label: str) -> str:
        """Startet einen gestoppten Server wieder. Liefert einen Hinweis, falls das nicht klappt."""
        rs.phase = "starting"
        try:
            self.state.automation.note_power(rs.ident, "start")
            await self.client.power(rs.ident, "start")
            self.state.monitor.follow(rs.ident)
            audit.record("power.start", username=ACTOR, ip="intern", server=rs.ident, detail=detail)
            return ""
        except Exception as exc:
            audit.record("power.start", result="error", username=ACTOR, ip="intern", server=rs.ident,
                         detail=f"{label}: {exc}")
            return i18n.t("Neustart danach fehlgeschlagen: {grund}", grund=exc)

    # -- Direktdownload ---------------------------------------------------------------

    def begin_download(self, job, user_id: int, user_name: str) -> list[RunState]:
        if user_id in self._downloads:
            raise BackupError(i18n.t("Es läuft schon ein Download von dir. Bitte warten, bis er fertig ist."))
        if self.job_active(job["id"]):
            raise BackupError(i18n.t("Dieser Job läuft gerade schon."))
        if any(r.ident in job_idents(job) for r in self.restores.values()):
            raise BackupError(i18n.t("Auf einem Server dieses Jobs läuft gerade eine Wiederherstellung."))
        states = self._create_runs(job, "download", "manual", user_id, user_name, "running")
        self._downloads.add(user_id)
        return states

    async def stream_download(self, job, states: list[RunState], user_id: int) -> AsyncIterator[bytes]:
        sink = Sink()
        builder = ZipBuilder(sink, bool(job["compress"]), self.settings.timezone)
        multi = len(states) > 1
        open_states = list(states)
        try:
            for rs in states:
                prefix = slug(rs.server_name) + "/" if multi else ""
                self._mark_started(rs)
                status, detail = "error", ""
                try:
                    await self._before(rs, job)
                    rs.phase = "collecting"
                    entries, missing = await collect(self.client, rs.ident, rs.paths,
                                                     parse_excludes(job["excludes"]), rs)
                    rs.phase = "copying"
                    before, saved_before = len(builder.failed), builder.saved
                    async for data in builder.add(self.client, rs.ident, entries, prefix, rs):
                        if data:
                            yield data
                    problems = builder.failed[before:] + missing
                    yield await builder.note(prefix + i18n.t("FEHLER.txt"), self._problem_lines(builder.failed[before:], missing))
                    status = "partial" if problems else "ok"
                    if builder.saved == saved_before and any(not e.is_dir for e in entries):
                        status = "error"        # keine einzige Datei im ZIP
                    detail = "; ".join(problems[:20])
                except Cancelled:
                    status, detail = "cancelled", i18n.t("Abgebrochen")
                except (BackupError, PteroError, OSError) as exc:
                    status, detail = "error", str(exc)
                    yield await builder.note(prefix + i18n.t("FEHLER.txt"), [str(exc)])
                note = await self._after(rs, job)
                self._finish(rs, status, (detail + " · " + note if detail and note else detail or note),
                             size=None)
                open_states.remove(rs)
                if status == "cancelled":
                    for rest in list(open_states):
                        self._finish(rest, "cancelled", i18n.t("Abgebrochen"))
                        open_states.remove(rest)
                    yield await builder.note(i18n.t("ABGEBROCHEN.txt"), [i18n.t("Das Backup wurde abgebrochen und ist unvollständig.")])
                    break
            yield await builder.close()
        except BaseException:
            # Browser geschlossen: hier nicht mehr warten, Aufräumen im Hintergrund
            for rs in open_states:
                self._spawn(self._abandon(rs, job))
            raise
        finally:
            self._downloads.discard(user_id)

    async def _abandon(self, rs: RunState, job) -> None:
        note = await self._after(rs, job)
        detail = i18n.t("Download abgebrochen (Browser geschlossen)")
        self._finish(rs, "cancelled", detail + (" · " + note if note else ""))

    # -- Gespeichertes Backup prüfen ---------------------------------------------------------

    def start_verify(self, run, user_id: int, user_name: str) -> None:
        """Liest eine gespeicherte Backup-Datei im Hintergrund vollständig und prüft alle Prüfsummen."""
        if run["id"] in self.checks:
            raise BackupError(i18n.t("Dieses Backup wird gerade schon geprüft."))
        if any(r.ident == run["identifier"] for r in self.restores.values()):
            raise BackupError(i18n.t("Für diesen Server läuft gerade eine Wiederherstellung."))
        target = load_target(run["target_id"])
        if not run["file"] or target is None:
            raise BackupError(i18n.t("Diese Backup-Datei gibt es nicht mehr."))
        rs = RunState(run["id"], "", run["job_id"], run["job_name"], run["identifier"],
                      run["server_name"] or run["identifier"], "verify", "manual", user_id, user_name)
        rs.phase, rs.began, rs.source = "verifying", time.monotonic(), run["id"]
        self.checks[run["id"]] = rs
        self._spawn(self._verify_stored(rs, target, run))

    async def _verify_stored(self, rs: RunState, target, run) -> None:
        try:
            problem = await asyncio.to_thread(verify_file, target, run["file"], None, rs)
        except Exception as exc:          # z. B. Ziel nicht erreichbar
            problem = str(exc) or exc.__class__.__name__
        finally:
            target.close()
            self.checks.pop(run["id"], None)
        with db.connect() as con:
            con.execute("UPDATE backup_runs SET verified_at = ?, verify_ok = ?, verify_detail = ? WHERE id = ?",
                        (db.utcnow(), 0 if problem else 1, (problem or "")[:500] or None, run["id"]))
        audit.record("backup.verify", result="error" if problem else "ok", user_id=rs.user_id,
                     username=rs.user_name, ip="intern", server=rs.ident,
                     detail=f"{run['job_name']}: {run['file']}" + (f" – {problem}" if problem else ""))

    # -- Wiederherstellen ---------------------------------------------------------------------

    def file_in_use(self, run_id: int) -> bool:
        """Wird diese Backup-Datei gerade geprüft oder zurückgespielt? (Dann nicht löschen.)"""
        return any(r.source == run_id for r in [*self.checks.values(), *self.restores.values()])

    def restore_busy(self, ident: str) -> str | None:
        """Warum auf diesem Server gerade keine Wiederherstellung starten darf (oder None)."""
        if any(r.ident == ident for r in self.restores.values()):
            return i18n.t("Für diesen Server läuft gerade schon eine Wiederherstellung.")
        if any(r.ident == ident for r in [*self.runs.values(), *self.checks.values()]):
            return i18n.t("Für diesen Server läuft gerade ein Backup oder eine Prüfung. Bitte warten, bis es fertig ist.")
        return None

    def start_restore(self, run, mode: str, stop: bool, user_id: int, user_name: str) -> int:
        """Spielt ein gespeichertes Backup im Hintergrund auf den Server zurück. Liefert die ID der Wiederherstellung.

        mode „folder“: in einen neuen Ordner /hatchtower-restore/<Datum> – nichts wird überschrieben.
        mode „overwrite“: an den ursprünglichen Ort; gleichnamige Dateien werden ersetzt, andere bleiben."""
        if mode not in ("folder", "overwrite"):
            raise BackupError("mode")
        busy = self.restore_busy(run["identifier"])
        if busy:
            raise BackupError(busy)
        if self.settings.read_only:
            raise BackupError(i18n.t("Im Read-Only-Modus lässt sich nichts wiederherstellen."))
        target = load_target(run["target_id"])
        if not run["file"] or target is None:
            raise BackupError(i18n.t("Diese Backup-Datei gibt es nicht mehr."))
        stamp = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%Y-%m-%d_%H-%M-%S")
        dest = "/" if mode == "overwrite" else f"{RESTORE_FOLDER}/{stamp}"
        with db.connect() as con:
            restore_id = con.execute(
                "INSERT INTO backup_restores (run_id, identifier, server_name, job_name, backup_at, mode, dest, status,"
                " started_by, started_name, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (run["id"], run["identifier"], run["server_name"], run["job_name"], run["finished_at"], mode, dest,
                 user_id, user_name, db.utcnow())).lastrowid
        rs = RunState(restore_id, "", run["job_id"], run["job_name"], run["identifier"],
                      run["server_name"] or run["identifier"], "restore", "manual", user_id, user_name)
        rs.phase, rs.began, rs.source = "verifying", time.monotonic(), run["id"]
        self.restores[restore_id] = rs
        self._spawn(self._restore(rs, target, run, dest, stop))
        return restore_id

    def cancel_restore(self, restore_id: int) -> bool:
        rs = self.restores.get(restore_id)
        if rs is None:
            return False
        rs.cancelled = True
        return True

    async def _restore(self, rs: RunState, target, run, dest: str, stop: bool) -> None:
        status, detail, failed = "error", "", []
        zf = raw = None
        tmp = self.settings.data_dir / "restore-tmp"
        try:
            # 1. Datei vollständig prüfen, bevor auf dem Server irgendetwas passiert
            problem = await asyncio.to_thread(verify_file, target, run["file"], None, rs)
            if problem:
                raise BackupError(i18n.t("Die Backup-Datei ist fehlerhaft, es wurde nichts zurückgespielt: {grund}",
                                         grund=problem))
            raw = await asyncio.to_thread(target.open_read, run["file"])
            zf = await asyncio.to_thread(zipfile.ZipFile, raw)
            files, dirs = restore_plan(zf.infolist(), note_names())
            rs.total_files = len(files)
            rs.total_bytes = sum(i.file_size for i in files)
            # 2. Server stoppen (nur wenn gewünscht und er läuft)
            if stop and (await self.client.resources(rs.ident))["state"] != "offline":
                with db.connect() as con:
                    con.execute("UPDATE backup_restores SET power_restore = 1 WHERE id = ?", (rs.run_id,))
                await self._stop_and_wait(rs, i18n.t("vor dem Wiederherstellen eines Backups"))
            # 3. Dateien hochladen, ordnerweise in kleinen Paketen (Upload-Limit von Wings)
            rs.phase = "uploading"
            await asyncio.to_thread(tmp.mkdir, parents=True, exist_ok=True)
            for directory, batch in restore_batches(files):
                rs.check_cancel()
                where = posixpath.normpath(posixpath.join(dest, directory)) if directory else dest
                parts = await asyncio.to_thread(_read_batch, zf, batch, tmp)
                try:
                    await pace(self.client, rs)
                    await self.client.upload(rs.ident, where, parts)
                    rs.done_files += len(batch)
                except PteroError as exc:
                    reason = str(exc)
                    if exc.status in (400, 413) and sum(i.file_size for i in batch) > 50 * 1024 ** 2:
                        reason = i18n.t("zu groß für das Upload-Limit von Wings ({grund})", grund=exc)
                    failed.extend(f"{i.filename}: {reason}" for i in batch)
                finally:
                    for _, f in parts:
                        f.close()
                rs.done_bytes += sum(i.file_size for i in batch)
            # Leere Ordner gibt es im Upload nicht – anlegen, damit die Struktur stimmt
            for directory in dirs:
                parent, name = posixpath.split(posixpath.join(dest, directory))
                try:
                    await pace(self.client, rs)
                    await self.client.create_folder(rs.ident, parent or "/", name)
                except PteroError:
                    pass          # gibt es meist schon
            status = "partial" if failed and rs.done_files else "error" if failed else "ok"
            detail = "; ".join(failed[:20]) + (" …" if len(failed) > 20 else "")
        except Cancelled:
            status, detail = "cancelled", i18n.t("Abgebrochen – bis dahin zurückgespielte Dateien bleiben auf dem Server.")
        except (BackupError, PteroError, OSError, zipfile.BadZipFile) as exc:
            detail = str(exc)
        except Exception as exc:
            log.exception("Wiederherstellung %s", rs.run_id)
            detail = i18n.t("Unerwarteter Fehler: {grund}", grund=str(exc)[:200] or exc.__class__.__name__)
        finally:
            for closable in (zf, raw):
                if closable is not None:
                    try:
                        await asyncio.to_thread(closable.close)
                    except Exception:
                        pass
            target.close()
        note = ""
        if rs.stopped:
            note = await self._start_again(rs, i18n.t("nach dem Wiederherstellen eines Backups"), run["job_name"])
        self.restores.pop(rs.run_id, None)
        i18n.use_default()
        detail = detail + " · " + note if detail and note else detail or note
        with db.connect() as con:
            con.execute("UPDATE backup_restores SET status = ?, detail = ?, files = ?, data_bytes = ?, finished_at = ?,"
                        " power_restore = 0 WHERE id = ?",
                        (status, detail[:2000] or None, rs.done_files, rs.done_bytes, db.utcnow(), rs.run_id))
        where = i18n.t("in den Ordner {dest}", dest=dest) if dest != "/" else i18n.t("an den ursprünglichen Ort")
        audit.record("backup.restore", result={"ok": "ok", "cancelled": "blocked"}.get(status, "error"),
                     user_id=rs.user_id, username=rs.user_name, ip="intern", server=rs.ident,
                     detail=f"{run['job_name']} ({run['file']}) {where}: {i18n.t(STATUS_LABELS.get(status, status))}"
                            + (f" – {detail}" if detail else ""))

    # -- Abschluss eines Laufs mit allen Servern -----------------------------------------

    def _batch_check(self, rs: RunState) -> None:
        if any(r.batch == rs.batch for r in self.runs.values()):
            return
        with db.connect() as con:
            rows = con.execute("SELECT * FROM backup_runs WHERE batch = ? ORDER BY id", (rs.batch,)).fetchall()
            job = con.execute("SELECT * FROM backup_jobs WHERE id = ?", (rs.job_id,)).fetchone() if rs.job_id else None
        if not rows or job is None:
            return
        ok = sum(1 for r in rows if r["status"] == "ok")
        bad = [r for r in rows if r["status"] != "ok"]
        i18n.use_default()
        stamp = datetime.now(ZoneInfo(self.settings.timezone)).strftime(i18n.date_format())
        if len(rows) == 1:
            summary = i18n.t("gesichert") if ok else i18n.t("nicht gesichert")
        else:
            summary = i18n.t("{ok} von {n} Servern gesichert", ok=ok, n=len(rows))
        with db.connect() as con:
            con.execute("UPDATE backup_jobs SET last_result = ? WHERE id = ?", (f"{stamp} {summary}", job["id"]))
        if rows[0]["kind"] != "target" or job["notify"] == "never" or (not bad and job["notify"] != "always"):
            return
        lines = [i18n.t("Job „{job}“: {summary}", job=job["name"], summary=summary)]
        for r in rows:
            label = i18n.t(STATUS_LABELS.get(r["status"], r["status"]))
            lines.append(f"• {r['server_name']}: {label}" + (f" – {r['detail'][:300]}" if r["detail"] else ""))
        notify.emit(self.vault, "backup_failed" if bad else "backup_ok", None, "\n".join(lines))

    # -- Aufbewahrung ----------------------------------------------------------------------

    async def _apply_retention(self, job, ident: str) -> None:
        if not job["keep_count"] and not job["keep_days"]:
            return
        with db.connect() as con:
            rows = con.execute(
                "SELECT id, target_id, file, finished_at, status FROM backup_runs WHERE job_id = ? AND identifier = ?"
                " AND kind = 'target' AND status IN ('ok', 'partial') AND file IS NOT NULL ORDER BY id DESC",
                (job["id"], ident)).fetchall()
        for r in retention_drop(rows, job["keep_count"], job["keep_days"]):
            await self.delete_file(r)

    async def delete_file(self, row) -> None:
        target = load_target(row["target_id"])
        if target is not None and row["file"]:
            try:
                await asyncio.to_thread(target.delete, row["file"])
            except (OSError, BackupError) as exc:
                log.warning("Backup-Datei %s nicht gelöscht: %s", row["file"], exc)
                return
            finally:
                target.close()
        with db.connect() as con:
            con.execute("UPDATE backup_runs SET file = NULL WHERE id = ?", (row["id"],))

    # -- Zeitplan --------------------------------------------------------------------------

    async def _scheduler(self) -> None:
        while True:
            try:
                self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Backup-Zeitplan")
            await asyncio.sleep(TICK)

    def _tick(self) -> None:
        i18n.use_default()
        tz = ZoneInfo(self.settings.timezone)
        now = datetime.now(tz)
        with db.connect() as con:
            jobs = con.execute("SELECT * FROM backup_jobs WHERE enabled = 1 AND weekdays != '' AND time IS NOT NULL"
                               " AND target_id IS NOT NULL").fetchall()
        for job in jobs:
            slot = due_slot(job, now)
            if slot is None:
                continue
            run_at, late = slot
            with db.connect() as con:
                con.execute("UPDATE backup_jobs SET last_run = ? WHERE id = ?",
                            (run_at.isoformat(timespec="minutes"), job["id"]))
            planned = run_at.strftime(i18n.date_format())
            try:
                self.enqueue(job, job["created_by"], ACTOR, trigger="schedule")
                if late:
                    audit.record("backup.run", username=ACTOR, ip="intern",
                                 detail=i18n.t("{job}: geplant für {zeit}, nachgeholt (Hatchtower lief zu der Zeit nicht)",
                                               job=job["name"], zeit=planned))
            except BackupError as exc:
                stamp = now.strftime(i18n.date_format())
                with db.connect() as con:
                    con.execute("UPDATE backup_jobs SET last_result = ? WHERE id = ?",
                                (f"{stamp} " + i18n.t("übersprungen: {grund}", grund=exc), job["id"]))
                audit.record("backup.run", result="blocked", username=ACTOR, ip="intern",
                             detail=f"{job['name']}: {exc}")
                if job["notify"] != "never":
                    notify.emit(self.vault, "backup_failed", None,
                                i18n.t("Job „{job}“ (geplant für {zeit}) wurde nicht gestartet: {grund}",
                                       job=job["name"], zeit=planned, grund=exc))


def due_slot(job, now: datetime) -> tuple[datetime, bool] | None:
    """Ob ein geplanter Job jetzt laufen soll: (geplanter Zeitpunkt, nachgeholt?) oder None.

    Pünktlich heißt: bis 90 s nach der Uhrzeit. Lief Hatchtower zu der Zeit nicht (Update, Neustart, Stromausfall),
    wird der letzte verpasste Termin bis CATCH_UP_HOURS danach nachgeholt – aber nur einmal, und nur wenn der Job
    damals schon so eingestellt war."""
    try:
        hour, minute = (int(x) for x in job["time"].split(":"))
    except (ValueError, AttributeError):
        return None
    days = {int(d) for d in job["weekdays"] or "" if d.isdigit()}
    for back in (0, 1):                     # heute oder (kurz nach Mitternacht) gestern
        day = (now - timedelta(days=back)).date()
        if day.weekday() not in days:
            continue
        run_at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo)
        if run_at > now:
            continue
        try:
            last = datetime.fromisoformat(job["last_run"]) if job["last_run"] else None
        except ValueError:
            last = None
        if last is not None and last >= run_at:
            return None                     # diesen (oder einen späteren) Termin gab es schon
        if now < run_at + timedelta(seconds=90):
            return run_at, False
        try:
            changed = datetime.fromisoformat(job["updated_at"] or job["created_at"])
        except (TypeError, ValueError):
            return None
        if now - run_at <= timedelta(hours=CATCH_UP_HOURS) and changed < run_at:
            return run_at, True
        return None
    return None

