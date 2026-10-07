"""Prüft regelmäßig, ob es auf GitHub eine neuere Hatchtower-Version gibt.

Nur lesend: eine Anfrage alle 12 Stunden an die GitHub-API, dazu "Jetzt prüfen" in der
Oberfläche (höchstens alle 5 Minuten). Bei einem privaten Repository wird der Token aus
/etc/hatchtower/github-token verwendet (falls vorhanden).

Installiert wird nie automatisch. Ein Owner kann das Update in der Oberfläche anstoßen:
Die Oberfläche läuft ohne Root-Rechte und legt dafür nur eine Anfrage-Datei ab, auf die der
Dienst hatchtower-update.service (root) reagiert – er führt "hatchtower update" aus.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

import httpx

from . import __version__

log = logging.getLogger("hatchtower.updates")

TOKEN_FILE = Path("/etc/hatchtower/github-token")
INTERVAL = 12 * 3600
MANUAL_COOLDOWN = 5 * 60          # "Jetzt prüfen" höchstens alle 5 Minuten (Schutz vor Missbrauch und GitHub-Limits)
HELPER_UNIT = Path("/etc/systemd/system/hatchtower-update.path")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _parse(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in v.lstrip("v").split("."))


class UpdateCheck:
    def __init__(self) -> None:
        self.repo = os.environ.get("HATCHTOWER_REPO", "skaot1910/hatchtower").strip()
        self.latest: str | None = None
        self.url: str | None = None
        self.error: str | None = None
        self.checked_at: str | None = None
        self._task: asyncio.Task | None = None
        self._manual_at = 0.0

    @property
    def available(self) -> bool:
        return bool(self.latest) and _parse(self.latest) > _parse(__version__)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hatchtower-updates")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def check(self) -> None:
        from . import db
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Hatchtower/{__version__}"}
        try:
            token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.is_file() else ""
        except OSError:
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.get(f"https://api.github.com/repos/{self.repo}/releases/latest", headers=headers)
            if r.status_code == 200:
                data = r.json()
                self.latest = str(data.get("tag_name", "")).lstrip("v") or None
                self.url = data.get("html_url")
                self.error = None
            elif r.status_code == 404:
                self.error = ("Kein Release gefunden" if token else
                              "Repository privat oder noch kein Release – Token setzen mit: hatchtower github-token")
            elif r.status_code == 401:
                self.error = "GitHub-Token abgelehnt – neu setzen mit: hatchtower github-token"
            else:
                self.error = f"GitHub antwortet mit HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            self.error = f"GitHub nicht erreichbar: {exc.__class__.__name__}"
        self.checked_at = db.utcnow()

    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Update-Prüfung")
            await asyncio.sleep(INTERVAL)

    # -- Manuelle Prüfung ----------------------------------------------------------

    def cooldown_left(self) -> int:
        return max(0, int(self._manual_at + MANUAL_COOLDOWN - time.monotonic()))

    async def check_now(self) -> bool:
        """Sofort prüfen, aber höchstens alle MANUAL_COOLDOWN Sekunden. False = noch gesperrt."""
        if self.cooldown_left():
            return False
        self._manual_at = time.monotonic()
        await self.check()
        return True


# -- Update aus der Weboberfläche ----------------------------------------------------

def helper_installed() -> bool:
    return HELPER_UNIT.is_file()


def _files(data_dir: Path) -> tuple[Path, Path, Path]:
    return data_dir / "update.request", data_dir / "update.state", data_dir / "update.log"


def read_state(data_dir: Path) -> dict:
    request, state_file, log_file = _files(data_dir)
    state: dict = {}
    try:
        for line in state_file.read_text().splitlines():
            key, _, value = line.partition("=")
            state[key] = value
    except OSError:
        pass
    if request.exists():
        state = {**state, "state": "requested"}
    try:
        text = ANSI.sub("", log_file.read_text(errors="replace"))
        state["log"] = "\n".join(text.splitlines()[-60:])
    except OSError:
        state["log"] = ""
    return state


def request_update(data_dir: Path, target: str) -> bool:
    """Legt die Anfrage ab. False, wenn schon ein Update läuft oder angefragt ist."""
    request, _, _ = _files(data_dir)
    if read_state(data_dir).get("state") in ("requested", "running"):
        return False
    request.write_text(target + "\n")
    return True
