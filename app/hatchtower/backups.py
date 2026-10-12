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
          "collecting": "Dateien werden gelesen", "copying": "Wird gesichert", "starting": "Server wird gestartet"}

STOP_TIMEOUT = 300          # Sekunden, bis ein gestoppter Server offline sein muss
STOP_POLL = 3
MAX_FILES = 200_000
MAX_DEPTH = 40
TICK = 20
RATE_RESERVE = 0.35         # Anteil am Rate-Limit des Panels, der für Monitoring und Bedienung frei bleibt
RATE_MAX_WAIT = 90

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

    def drain(self) -> bytes:
        return self.sink.drain() if self.sink else b""

    async def add(self, client, ident: str, entries: list[Entry], prefix: str, rs: "RunState") -> AsyncIterator[bytes]:
        for entry in entries:
            rs.check_cancel()
            info = zipfile.ZipInfo(prefix + entry.arcname, _date_time(entry.modified, self.zone))
            if entry.is_dir:
                await asyncio.to_thread(self.zf.writestr, info, b"")
                yield self.drain()
                continue
            info.compress_type = self.method
            info.file_size = entry.size
            try:
                await pace(client, rs)
                url = await client.download_url(ident, entry.path)
                out = await asyncio.to_thread(self.zf.open, info, "w", force_zip64=entry.size > 2 ** 31)
                try:
                    async for chunk in client.stream_download(url):
                        rs.check_cancel()
                        await asyncio.to_thread(out.write, chunk)
                        rs.done_bytes += len(chunk)
                        data = self.drain()
                        if data:
                            yield data
                finally:
                    await asyncio.to_thread(out.close)
            except PteroError as exc:
                self.failed.append(f"{entry.path}: {exc}")
            rs.done_files += 1
            yield self.drain()

    async def note(self, name: str, lines: list[str]) -> bytes:
        if lines:
            await asyncio.to_thread(self.zf.writestr, name, "\n".join(lines) + "\n")
        return self.drain()

    async def close(self) -> bytes:
        await asyncio.to_thread(self.zf.close)
        return self.drain()


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

    def check_cancel(self) -> None:
        if self.cancelled:
            raise Cancelled()

    @property
    def percent(self) -> int:
        if self.phase != "copying":
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
        if self.throttled:
            phase += " · " + t("wartet kurz auf {panel} (Anfragen-Limit)")
        elapsed = time.monotonic() - self.began if self.began else 0
        rate = self.done_bytes / elapsed if elapsed > 1 and self.phase == "copying" else 0
        return {"id": self.run_id, "job": self.job_name, "job_id": self.job_id, "server": self.server_name,
                "ident": self.ident, "kind": self.kind, "phase": phase, "state": self.phase,
                "percent": self.percent, "done": self.done_bytes, "total": self.total_bytes,
                "files_done": self.done_files, "files_total": self.total_files, "rate": rate,
                "throttled": self.throttled}


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
                await builder.note(i18n.t("FEHLER.txt"), self._problem_lines(builder.failed, missing))
                await builder.close()
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
            cols = {"file": rel, "size": size}
        except Cancelled:
            status, detail, cols = "cancelled", i18n.t("Abgebrochen"), {"file": None}
        except (BackupError, PteroError, OSError) as exc:
            status, detail, cols = "error", str(exc), {"file": None}
        note = await self._after(rs, job)
        self._finish(rs, status, (detail + " · " + note if detail and note else detail or note), **cols)
        if status in ("ok", "partial"):
            await self._apply_retention(job, rs.ident)
        target.close()

    @staticmethod
    def _problem_lines(failed: list[str], missing: list[str]) -> list[str]:
        if not failed and not missing:
            return []
        return [i18n.t("Diese Dateien oder Ordner fehlen im Backup:"), "", *missing, *failed]

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
        rs.phase = "stopping"
        self.state.automation.note_power(rs.ident, "stop")
        rs.stopped = True
        if job["stop_mode"] == "restart":
            with db.connect() as con:
                con.execute("UPDATE backup_runs SET power_restore = 1 WHERE id = ?", (rs.run_id,))
        await self.client.power(rs.ident, "stop")
        audit.record("power.stop", username=ACTOR, ip="intern", server=rs.ident,
                     detail=i18n.t("{job}: vor dem Backup", job=job["name"]))
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
        rs.phase = "starting"
        try:
            self.state.automation.note_power(rs.ident, "start")
            await self.client.power(rs.ident, "start")
            self.state.monitor.follow(rs.ident)
            audit.record("power.start", username=ACTOR, ip="intern", server=rs.ident,
                         detail=i18n.t("{job}: nach dem Backup", job=job["name"]))
            return ""
        except Exception as exc:
            audit.record("power.start", result="error", username=ACTOR, ip="intern", server=rs.ident,
                         detail=f"{job['name']}: {exc}")
            return i18n.t("Neustart danach fehlgeschlagen: {grund}", grund=exc)

    # -- Direktdownload ---------------------------------------------------------------

    def begin_download(self, job, user_id: int, user_name: str) -> list[RunState]:
        if user_id in self._downloads:
            raise BackupError(i18n.t("Es läuft schon ein Download von dir. Bitte warten, bis er fertig ist."))
        if self.job_active(job["id"]):
            raise BackupError(i18n.t("Dieser Job läuft gerade schon."))
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
                    before = len(builder.failed)
                    async for data in builder.add(self.client, rs.ident, entries, prefix, rs):
                        if data:
                            yield data
                    problems = builder.failed[before:] + missing
                    yield await builder.note(prefix + i18n.t("FEHLER.txt"), self._problem_lines(builder.failed[before:], missing))
                    status = "partial" if problems else "ok"
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
                "SELECT id, target_id, file, finished_at FROM backup_runs WHERE job_id = ? AND identifier = ?"
                " AND kind = 'target' AND status IN ('ok', 'partial') AND file IS NOT NULL ORDER BY id DESC",
                (job["id"], ident)).fetchall()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=job["keep_days"] or 0)).isoformat(timespec="seconds")
        drop = [r for i, r in enumerate(rows) if i > 0 and (
            (job["keep_count"] and i >= job["keep_count"]) or (job["keep_days"] and (r["finished_at"] or "") < cutoff))]
        for r in drop:
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
            try:
                hour, minute = (int(x) for x in job["time"].split(":"))
            except ValueError:
                continue
            days = {int(d) for d in job["weekdays"] if d.isdigit()}
            if now.weekday() not in days:
                continue
            run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            key = run_at.isoformat(timespec="minutes")
            if not (run_at <= now < run_at + timedelta(seconds=90)) or job["last_run"] == key:
                continue
            with db.connect() as con:
                con.execute("UPDATE backup_jobs SET last_run = ? WHERE id = ?", (key, job["id"]))
            try:
                self.enqueue(job, job["created_by"], ACTOR, trigger="schedule")
            except BackupError as exc:
                stamp = now.strftime(i18n.date_format())
                with db.connect() as con:
                    con.execute("UPDATE backup_jobs SET last_result = ? WHERE id = ?",
                                (f"{stamp} " + i18n.t("übersprungen: {grund}", grund=exc), job["id"]))
                audit.record("backup.run", result="blocked", username=ACTOR, ip="intern",
                             detail=f"{job['name']}: {exc}")

