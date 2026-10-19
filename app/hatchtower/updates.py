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

from markupsafe import Markup, escape

from . import __version__, i18n

log = logging.getLogger("hatchtower.updates")

TOKEN_FILE = Path("/etc/hatchtower/github-token")
INTERVAL = 12 * 3600
MANUAL_COOLDOWN = 5 * 60          # "Jetzt prüfen" höchstens alle 5 Minuten (Schutz vor Missbrauch und GitHub-Limits)
HELPER_UNIT = Path("/etc/systemd/system/hatchtower-update.path")
# Status und Protokoll schreibt root dorthin (für den Dienst nur lesbar); ältere Versionen schrieben in den Datenordner
STATUS_DIR = Path("/var/lib/hatchtower-update")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOTES_MAX = 20_000


PRERELEASE_SETTING = "update_prereleases"     # "1" = also offer pre-releases (e.g. 0.38.0-pre.1)
_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?$")


def version_key(v: str) -> tuple:
    """Sort key following semver precedence: 0.38.0-pre.1 < 0.38.0-pre.2 < 0.38.0-rc.1 < 0.38.0 < 0.38.1."""
    m = _VERSION.match((v or "").strip())
    if not m:
        return ((0, 0, 0), 0, ())
    nums = tuple(int(p) for p in m.group(1).split("."))
    nums += (0,) * (3 - len(nums))
    if not m.group(2):
        return (nums, 1, ())
    # numeric identifiers rank below alphanumeric ones, as in semver
    return (nums, 0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(2).split(".")))


def is_prerelease(v: str | None) -> bool:
    return bool(v) and "-" in str(v)


def _release(data: dict) -> dict:
    return {"version": str(data.get("tag_name", "")).lstrip("v"), "url": data.get("html_url"),
            "notes": str(data.get("body") or "")[:NOTES_MAX], "published_at": data.get("published_at")}


class UpdateCheck:
    def __init__(self) -> None:
        self.repo = os.environ.get("HATCHTOWER_REPO", "skaot1910/hatchtower").strip()
        self.prereleases = False          # mirrors the setting PRERELEASE_SETTING (loaded in start())
        self._stable: dict | None = None  # newest regular release
        self._newest: dict | None = None  # newest release including pre-releases
        self.error: str | None = None
        self.checked_at: str | None = None
        self._task: asyncio.Task | None = None
        self._manual_at = 0.0

    @property
    def _offered(self) -> dict:
        return (self._newest if self.prereleases else self._stable) or {}

    @property
    def latest(self) -> str | None:
        return self._offered.get("version") or None

    @property
    def url(self) -> str | None:
        return self._offered.get("url")

    @property
    def notes(self) -> str:
        """Release notes of the offered version (markdown, from the GitHub release)."""
        return self._offered.get("notes", "")

    @property
    def published_at(self) -> str | None:
        return self._offered.get("published_at")

    @property
    def latest_is_prerelease(self) -> bool:
        return is_prerelease(self.latest)

    @property
    def available(self) -> bool:
        # Strictly newer only: whoever runs a pre-release and turns the switch off is never downgraded,
        # but gets the next regular release that is at least as new.
        return bool(self.latest) and version_key(self.latest) > version_key(__version__)

    def load_setting(self) -> None:
        from . import db
        self.prereleases = db.get_setting(PRERELEASE_SETTING) == "1"

    def set_prereleases(self, on: bool, user_id: int | None) -> None:
        from . import db
        db.set_setting(PRERELEASE_SETTING, "1" if on else "0", user_id)
        self.prereleases = on
        self._announce()

    def start(self) -> None:
        try:
            self.load_setting()
        except Exception:
            log.exception("Update-Einstellung")
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
                # One request covers both channels, so flipping the switch needs no new request
                r = await http.get(f"https://api.github.com/repos/{self.repo}/releases",
                                   params={"per_page": 30}, headers=headers)
            if r.status_code == 200:
                found = [_release(d) for d in r.json() if isinstance(d, dict) and not d.get("draft")]
                found = [x for x in found if _VERSION.match(x["version"])]
                stable = [x for x in found if not is_prerelease(x["version"])]
                self._stable = max(stable, key=lambda x: version_key(x["version"]), default=None)
                self._newest = max(found, key=lambda x: version_key(x["version"]), default=None)
                self.error = None if found else "Kein Release gefunden"
            elif r.status_code == 404:
                self.error = ("Kein Release gefunden" if token else
                              "Repository privat oder noch kein Release – Token setzen mit: hatchtower github-token")
            elif r.status_code == 401:
                self.error = "GitHub-Token abgelehnt – neu setzen mit: hatchtower github-token"
            else:
                self.error = i18n.Msg("GitHub antwortet mit HTTP {code}", code=r.status_code)
        except httpx.HTTPError as exc:
            self.error = i18n.Msg("GitHub nicht erreichbar: {typ}", typ=exc.__class__.__name__)
        self.checked_at = db.utcnow()
        self._announce()

    def _announce(self) -> None:
        """A newer version goes into the owners' inbox once per version."""
        from . import db, inbox
        if not self.available or db.get_setting("inbox_update_version") == self.latest:
            return
        db.set_setting("inbox_update_version", self.latest, None)
        inbox.add("update", "owner", None, i18n.t("Version {neu} ist verfügbar (installiert: {alt}).",
                                                  neu=self.latest, alt=__version__))

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
    status = STATUS_DIR if (STATUS_DIR / "update.state").is_file() else data_dir
    return data_dir / "update.request", status / "update.state", status / "update.log"


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


def release_url(repo: str, version: str) -> str:
    return f"https://github.com/{repo}/releases/tag/v{version}"


def changelog_url(repo: str) -> str:
    return f"https://github.com/{repo}/blob/main/CHANGELOG.md"


_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*")


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(str(escape(text[pos:m.start()])))
        out.append(f"<code>{escape(m.group(1))}</code>" if m.group(1) is not None else f"<strong>{escape(m.group(2))}</strong>")
        pos = m.end()
    out.append(str(escape(text[pos:])))
    return "".join(out)


def notes_html(markdown: str) -> Markup:
    """Release notes as a small, safe subset of markdown: headings, bullet lists, paragraphs, `code` and **bold**.
    Everything is escaped first; links stay plain text."""
    html, items, para = [], [], []

    def flush():
        if items:
            html.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
        if para:
            html.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            html.append(f"<h3>{_inline(line.lstrip('#').strip())}</h3>")
        elif line[:2] in ("- ", "* "):
            if para:
                flush()
            items.append(line[2:].strip())
        elif items and raw[:1] in (" ", "\t"):
            items[-1] += " " + line          # continuation of a bullet
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return Markup("".join(html))
