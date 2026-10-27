"""Mehrere Dateien und Ordner als ZIP herunterladen.

Das Archiv wird beim Herunterladen im Manager zusammengesetzt und direkt an den
Browser gestreamt: Auf dem Game-Server wird nichts angelegt, im Manager nichts
zwischengespeichert. Dateien werden unkomprimiert abgelegt (ZIP_STORED), das kostet
praktisch keine CPU; Spieldateien sind meist ohnehin schon komprimiert.
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator
from zoneinfo import ZoneInfo

from .i18n import t
from .ptero import PteroClient, PteroError

MAX_FILES = 1000
MAX_BYTES = 10 * 1024 ** 3
MAX_DEPTH = 25


class ZipLimit(ValueError):
    pass


@dataclass
class Entry:
    path: str        # Pfad auf dem Server
    arcname: str     # Pfad im ZIP
    is_dir: bool
    size: int
    modified: str | None


async def collect(client: PteroClient, ident: str, root: str, names: list[str]) -> list[Entry]:
    """Sammelt alle Dateien der Auswahl (Ordner rekursiv) und prüft die Grenzen vorab."""
    listing = {f["name"]: f for f in await client.list_files(ident, root)}
    entries: list[Entry] = []
    total = 0
    count = 0
    queue: list[tuple[str, str, int]] = []

    def add_file(path: str, arc: str, item: dict) -> None:
        nonlocal total, count
        total += item["size"]
        count += 1
        if count > MAX_FILES:
            raise ZipLimit(t("Mehr als {n} Dateien in der Auswahl. Bitte kleinere Ordner wählen.", n=MAX_FILES))
        if total > MAX_BYTES:
            raise ZipLimit(t("Die Auswahl ist größer als 10 GB. Bitte kleinere Ordner wählen."))
        entries.append(Entry(path, arc, False, item["size"], item.get("modified_at")))

    for name in names:
        item = listing.get(name)
        if item is None:
            raise ZipLimit(t("„{name}“ gibt es in diesem Ordner nicht mehr.", name=name))
        path = posixpath.join(root, name)
        if item["is_file"]:
            add_file(path, name, item)
        else:
            entries.append(Entry(path, name + "/", True, 0, item.get("modified_at")))
            queue.append((path, name, 1))

    while queue:
        path, arc, depth = queue.pop(0)
        if depth > MAX_DEPTH:
            raise ZipLimit(t("Die Ordner sind zu tief verschachtelt."))
        for item in await client.list_files(ident, path):
            child_path = posixpath.join(path, item["name"])
            child_arc = f"{arc}/{item['name']}"
            if item["is_file"]:
                add_file(child_path, child_arc, item)
            else:
                entries.append(Entry(child_path, child_arc + "/", True, 0, item.get("modified_at")))
                queue.append((child_path, child_arc, depth + 1))
    return entries


class _Sink(io.RawIOBase):
    """Nimmt die ZIP-Bytes entgegen, bis sie an den Browser weitergereicht werden."""

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


def paths_note(server: str, ident: str, picked: list[tuple[str, str]], zone: ZoneInfo, folder: str | None = None) -> str:
    """Text of the PATHS.txt: the full server path of each selected file or folder (folders end in /)."""
    lines = [t("Herkunft der Dateien in diesem ZIP (Hatchtower)"),
             t("Server: {name} ({ident})", name=server, ident=ident),
             t("Erstellt: {date}", date=datetime.now(zone).strftime("%Y-%m-%d %H:%M"))]
    if folder:
        lines.append(t("Ausgewählt im Ordner: {path}", path=folder))
    lines += ["", t("Ausgewählte Dateien und Ordner (voller Pfad auf dem Server):"), ""]
    lines += [path.rstrip("/") + "/" if arc.endswith("/") or not arc else path for arc, path in picked]
    return "\n".join(lines) + "\n"


def picked(entries: list) -> list[tuple[str, str]]:
    """The selected files and folders themselves (top level of the ZIP), without their contents."""
    return [(e.arcname, e.path) for e in entries if "/" not in e.arcname.rstrip("/")]


async def stream_zip(client: PteroClient, ident: str, entries: list[Entry], tz: str,
                     server: str = "", folder: str = "/") -> AsyncIterator[bytes]:
    zone = ZoneInfo(tz)
    sink = _Sink()
    failed: list[str] = []
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry.arcname, _date_time(entry.modified, zone))
            if entry.is_dir:
                zf.writestr(info, b"")
                yield sink.drain()
                continue
            info.compress_type = zipfile.ZIP_STORED
            info.file_size = entry.size
            try:
                url = await client.download_url(ident, entry.path)
                with zf.open(info, "w", force_zip64=entry.size > 2 ** 31) as out:
                    async for chunk in client.stream_download(url):
                        out.write(chunk)
                        data = sink.drain()
                        if data:
                            yield data
            except PteroError as exc:
                failed.append(f"{entry.path}: {exc}")
            yield sink.drain()
        if entries:
            zf.writestr(t("HATCHTOWER-PFADE.txt"), paths_note(server, ident, picked(entries), zone, folder))
        if failed:
            zf.writestr(t("FEHLER.txt"), t("Diese Dateien konnten nicht geladen werden:") + "\n\n" + "\n".join(failed) + "\n")
    yield sink.drain()
