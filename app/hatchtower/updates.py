"""Prüft regelmäßig, ob es auf GitHub eine neuere Hatchtower-Version gibt.

Nur lesend: eine Anfrage alle 12 Stunden an die GitHub-API. Bei einem privaten
Repository wird der Token aus /etc/hatchtower/github-token verwendet (falls vorhanden).
Installiert wird nie automatisch – das macht der Owner mit "hatchtower update".
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

from . import __version__

log = logging.getLogger("hatchtower.updates")

TOKEN_FILE = Path("/etc/hatchtower/github-token")
INTERVAL = 12 * 3600


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
