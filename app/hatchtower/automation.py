"""Automationen: Neustart bei Absturz und Zeitpläne.

Bewusst sparsam: eine Prüfung alle 20 Sekunden aus der lokalen Datenbank,
Anfragen an Pterodactyl nur, wenn tatsächlich etwas auszuführen ist.
Verpasste Termine (z. B. während der Manager neu startet) werden nicht nachgeholt.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import audit, db, i18n, notify
from .ptero import PteroClient, PteroError

log = logging.getLogger("hatchtower.automation")

ACTOR = "Automation"
INTENT_WINDOW = 180          # Sekunden, in denen ein Stopp als gewollt gilt
CRASH_GRACE = 90             # Wartezeit, ob Wings den Server selbst neu startet
CRASH_LIMIT_PER_HOUR = 3
STOP_WORDS = {"stop", "quit", "exit", "shutdown", "end", "save-and-exit", "quit()", "halt", "restart", "reboot"}
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
ACTIONS = {"start": "Start", "stop": "Stop", "restart": "Restart", "command": "Befehl", "update": "Update-Fenster"}
# Update-Fenster: Server wird gestartet (nur wenn er aus ist) und nach dieser Dauer wieder gestoppt
DURATIONS = (30, 60, 90, 120, 180, 240, 360, 480, 720)


def creator_allows(sched) -> bool:
    """Zeitpläne von Benutzern (nicht Owner) laufen nur, solange der Ersteller die nötigen Rechte noch hat.
    Zeitpläne gelöschter Benutzer werden beim Löschen entfernt (und liefen sonst auch nicht mehr)."""
    from .permissions import AUTOMATION_NEEDS, creator_allows as still_allowed
    need = {"automation.manage", *AUTOMATION_NEEDS.get(sched["action"], ("-",))}
    if sched["warn_command"]:
        need.add("console.command")
    return still_allowed(sched["created_by"], sched["identifier"], need)


class Automation:
    def __init__(self, client: PteroClient, tz: str, vault=None):
        self.client = client
        self.vault = vault
        self.tz = ZoneInfo(tz)
        self._manual: dict[str, tuple[str, float]] = {}
        self._window_started: dict[str, float] = {}     # Server → wann ein Update-Fenster ihn gestartet hat
        self._crash_tasks: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None

    def set_timezone(self, tz: str) -> None:
        self.tz = ZoneInfo(tz)

    # -- Lebenszyklus ------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hatchtower-scheduler")

    async def stop(self) -> None:
        tasks = [t for t in [self._task, *self._crash_tasks.values()] if t]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    @property
    def active(self) -> bool:
        return not self.client.settings.read_only

    # -- Gewollte Aktionen merken ---------------------------------------------

    def note_power(self, identifier: str, signal: str) -> None:
        self._manual[identifier] = (signal, time.monotonic())

    def _manual_stop_recent(self, identifier: str) -> bool:
        signal, at = self._manual.get(identifier, ("", 0.0))
        return signal in ("stop", "kill", "restart") and time.monotonic() - at < INTENT_WINDOW

    async def _ptero_stop_recent(self, identifier: str) -> bool:
        try:
            events = await self.client.activity(identifier)
        except PteroError:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=INTENT_WINDOW)
        for e in events:
            try:
                ts = datetime.fromisoformat(str(e.get("timestamp", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            name = str(e.get("event", ""))
            if name.startswith(("server:power.stop", "server:power.kill", "server:power.restart", "server:schedule.")):
                return True
            if name == "server:console.command":
                cmd = str((e.get("properties") or {}).get("command", "")).strip().lstrip("/").lower()
                if cmd.split(" ")[0] in STOP_WORDS:
                    return True
        return False

    # -- Absturz-Neustart ----------------------------------------------------------

    def _emit(self, event: str, identifier: str | None, detail: str = "") -> None:
        if self.vault is not None:
            notify.emit(self.vault, event, identifier, detail)

    def on_offline(self, identifier: str) -> None:
        """Vom Monitoring aufgerufen, wenn ein Server von running auf offline wechselt.
        Erkannt und gemeldet wird immer, neu gestartet nur mit eingeschaltetem Absturz-Neustart
        und im Modus „Lesen und steuern“."""
        if identifier in self._crash_tasks:
            return
        with db.connect() as con:
            row = con.execute("SELECT crash_restart FROM automation_settings WHERE identifier = ?",
                              (identifier,)).fetchone()
        restart = bool(row and row["crash_restart"]) and self.active
        task = asyncio.create_task(self._handle_crash(identifier, restart))
        self._crash_tasks[identifier] = task
        task.add_done_callback(lambda _t, i=identifier: self._crash_tasks.pop(i, None))

    def on_silent_restart(self, identifier: str) -> None:
        """Vom Monitoring aufgerufen, wenn die Laufzeit eines laufenden Servers zurückgesprungen ist, ohne dass er
        offline zu sehen war. Meist hat Wings ihn nach einem Absturz sofort selbst neu gestartet – das wird wie ein
        Absturz erfasst und gemeldet, ein eigener Neustart ist nicht nötig."""
        if identifier in self._crash_tasks:
            return
        task = asyncio.create_task(self._handle_silent_restart(identifier))
        self._crash_tasks[identifier] = task
        task.add_done_callback(lambda _t, i=identifier: self._crash_tasks.pop(i, None))

    async def _handle_silent_restart(self, identifier: str) -> None:
        i18n.use_default()
        if self._manual_stop_recent(identifier) or await self._ptero_stop_recent(identifier):
            return  # gewollter Neustart (Hatchtower, Panel, Zeitplan oder Konsolenbefehl)
        i18n.use_default()
        self._record_crash(identifier, i18n.t("Absturz erkannt, Wings hat den Server sofort neu gestartet"))
        self._emit("crash", identifier, i18n.t("Der Server ist abgestürzt und wurde von Wings sofort wieder gestartet."))

    def _record_crash(self, identifier: str, outcome: str, result: str = "ok") -> None:
        with db.connect() as con:
            con.execute("INSERT INTO crash_events (identifier, detected_at, outcome) VALUES (?, ?, ?)",
                        (identifier, db.utcnow(), outcome))
        audit.record("automation.crash", result=result, username=ACTOR, ip="intern", server=identifier,
                     detail=outcome)

    async def _handle_crash(self, identifier: str, restart: bool = True) -> None:
        i18n.use_default()
        if self._manual_stop_recent(identifier) or await self._ptero_stop_recent(identifier):
            return  # gewollt beendet, kein Absturz
        self._emit("crash", identifier, i18n.t("Der Server ist ohne Stopp-Befehl offline gegangen.") + " " + (
            i18n.t("Hatchtower prüft in 90 Sekunden und startet ihn bei Bedarf neu.") if restart
            else i18n.t("Der automatische Neustart ist für diesen Server aus.")))
        await asyncio.sleep(CRASH_GRACE)
        i18n.use_default()
        try:
            state = (await self.client.resources(identifier))["state"]
        except PteroError as exc:
            self._record_crash(identifier, i18n.t("Status nicht lesbar: {grund}", grund=exc), "error")
            return
        if state != "offline":
            self._record_crash(identifier, i18n.t("Absturz erkannt, Server ist wieder {state} (Wings hat neu gestartet)", state=state))
            return
        if await self._ptero_stop_recent(identifier):
            return
        if not restart:
            self._record_crash(identifier, i18n.t("Absturz erkannt, automatischer Neustart ist aus"), "blocked")
            return
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        with db.connect() as con:
            recent = con.execute(
                "SELECT COUNT(*) FROM crash_events WHERE identifier = ? AND detected_at > ? AND (outcome LIKE 'Neu gestartet%' OR outcome LIKE 'Restarted%')",
                (identifier, since),
            ).fetchone()[0]
        if recent >= CRASH_LIMIT_PER_HOUR:
            outcome = i18n.t("Absturz erkannt, kein Neustart: Limit von {n} Neustarts pro Stunde erreicht", n=CRASH_LIMIT_PER_HOUR)
            self._record_crash(identifier, outcome, "blocked")
            self._emit("restart_failed", identifier, outcome)
            return
        try:
            await self.client.power(identifier, "start")
            self.note_power(identifier, "start")
            self._record_crash(identifier, i18n.t("Neu gestartet nach Absturz"))
            self._emit("restarted", identifier, i18n.t("Hatchtower hat den Server nach dem Absturz wieder gestartet."))
        except PteroError as exc:
            outcome = i18n.t("Neustart fehlgeschlagen: {grund}", grund=exc)
            self._record_crash(identifier, outcome, "error")
            self._emit("restart_failed", identifier, outcome)

    # -- Zeitpläne -----------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                if self.active:
                    await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Zeitplan-Fehler")
            await asyncio.sleep(20)

    def _run_times(self, sched, now: datetime) -> list[datetime]:
        if sched["run_at"]:
            try:
                return [datetime.fromisoformat(sched["run_at"]).replace(tzinfo=self.tz)]
            except ValueError:
                return []
        hour, minute = (int(x) for x in sched["time"].split(":"))
        days = {int(d) for d in sched["weekdays"] if d.isdigit()}
        result = []
        for offset in (0, 1):
            day = (now + timedelta(days=offset)).date()
            if day.weekday() in days:
                result.append(datetime(day.year, day.month, day.day, hour, minute, tzinfo=self.tz))
        return result

    async def _tick(self) -> None:
        i18n.use_default()
        now = datetime.now(self.tz)
        with db.connect() as con:
            # Only servers that still exist in the panel: a deleted server would answer every run with HTTP 404 and
            # send a failure notification each time. If it comes back, its schedules simply run again.
            schedules = con.execute(
                "SELECT sc.*, st.state FROM schedules sc"
                " JOIN servers sv ON sv.identifier = sc.identifier AND sv.present = 1"
                " LEFT JOIN server_state st ON st.identifier = sc.identifier"
                " WHERE sc.enabled = 1"
            ).fetchall()
        for sched in schedules:
            for run_at in self._run_times(sched, now):
                key = run_at.isoformat(timespec="minutes")
                if sched["warn_minutes"] and sched["warn_command"]:
                    warn_at = run_at - timedelta(minutes=sched["warn_minutes"])
                    if warn_at <= now < warn_at + timedelta(seconds=90) and sched["last_warn"] != key:
                        await self._warn(sched, key)
                if run_at <= now < run_at + timedelta(seconds=90) and sched["last_run"] != key:
                    await self._execute(sched, key)
        await self._close_windows()

    def _started_by_hand(self, identifier: str) -> bool:
        """Wurde der Server nach dem Start durch das Update-Fenster in Hatchtower von Hand (neu) gestartet?"""
        signal, at = self._manual.get(identifier, ("", 0.0))
        return signal in ("start", "restart") and at > self._window_started.get(identifier, float("inf"))

    async def _close_windows(self) -> None:
        """Beendet abgelaufene Update-Fenster. Gestoppt wird nur, wenn der Server noch läuft und
        ihn in der Zwischenzeit niemand von Hand gestartet hat."""
        now = datetime.now(timezone.utc)
        with db.connect() as con:
            windows = con.execute(
                "SELECT sc.*, st.state FROM schedules sc LEFT JOIN server_state st ON st.identifier = sc.identifier"
                " WHERE sc.window_until IS NOT NULL").fetchall()
        for sched in windows:
            try:
                if datetime.fromisoformat(sched["window_until"]) > now:
                    continue
            except ValueError:
                pass
            ident, result_flag = sched["identifier"], "ok"
            if sched["state"] not in ("running", "starting"):
                result = i18n.t("Update-Fenster beendet, Server war schon aus")
            elif self._started_by_hand(ident):
                result = i18n.t("Update-Fenster beendet, Server läuft weiter (zwischendurch von Hand gestartet)")
            else:
                try:
                    self.note_power(ident, "stop")
                    await self.client.power(ident, "stop")
                    result = i18n.t("Update-Fenster beendet, Server gestoppt")
                except PteroError as exc:
                    result, result_flag = i18n.t("Stopp nach Update-Fenster fehlgeschlagen: {grund}", grund=exc), "error"
                    self._emit("schedule_failed", ident, f"{sched['name']}: {result}")
            self._window_started.pop(ident, None)
            with db.connect() as con:
                con.execute("UPDATE schedules SET window_until = NULL, last_result = ? WHERE id = ?",
                            (f"{datetime.now(self.tz).strftime(i18n.date_format())} {result}", sched["id"]))
            audit.record("schedule.run", result=result_flag, username=ACTOR, ip="intern", server=ident,
                         detail=f"{sched['name']} ({i18n.t(ACTIONS['update'])}): {result}")

    async def _warn(self, sched, key: str) -> None:
        with db.connect() as con:
            con.execute("UPDATE schedules SET last_warn = ? WHERE id = ?", (key, sched["id"]))
        if sched["state"] != "running" or sched["action"] == "update" or not creator_allows(sched):
            return
        text = sched["warn_command"].replace("{minuten}", str(sched["warn_minutes"])).replace("{minutes}", str(sched["warn_minutes"]))
        try:
            await self.client.command(sched["identifier"], text)
            audit.record("schedule.warn", username=ACTOR, ip="intern", server=sched["identifier"],
                         detail=f"{sched['name']}: {text}")
        except PteroError as exc:
            audit.record("schedule.warn", result="error", username=ACTOR, ip="intern",
                         server=sched["identifier"], detail=f"{sched['name']}: {exc}")

    async def _execute(self, sched, key: str) -> None:
        action, ident, state = sched["action"], sched["identifier"], sched["state"]
        result_flag, window_until = "ok", None
        if not creator_allows(sched):
            result, result_flag = i18n.t("übersprungen: dem Ersteller fehlt das Recht dafür"), "denied"
        elif action == "start" and state in ("running", "starting"):
            result = i18n.t("übersprungen: läuft bereits")
        elif action == "update" and sched["window_until"]:
            result = i18n.t("übersprungen: Update-Fenster läuft noch")
        elif action == "update" and state != "offline":
            result = i18n.t("übersprungen: Server ist nicht aus")
        elif action not in ("start", "update") and sched["only_if_running"] and state != "running":
            result = i18n.t("übersprungen: Server ist nicht gestartet")
        else:
            try:
                if action == "update":
                    self.note_power(ident, "start")
                    await self.client.power(ident, "start")
                    self._window_started[ident] = time.monotonic()
                    end = datetime.now(timezone.utc) + timedelta(minutes=sched["duration_minutes"] or 60)
                    window_until = end.isoformat(timespec="seconds")
                    result = i18n.t("gestartet, Stopp um {time} Uhr", time=end.astimezone(self.tz).strftime("%H:%M"))
                elif action == "command":
                    await self.client.command(ident, sched["command"] or "")
                else:
                    self.note_power(ident, action)
                    await self.client.power(ident, action)
                if action != "update":
                    result = i18n.t("ausgeführt")
            except PteroError as exc:
                result, result_flag = i18n.t("Fehler: {grund}", grund=exc), "error"
                self._emit("schedule_failed", ident, f"{sched['name']}: {result}")
        with db.connect() as con:
            con.execute("UPDATE schedules SET last_run = ?, last_result = ?, enabled = ?,"
                        " window_until = COALESCE(?, window_until) WHERE id = ?",
                        (key, f"{datetime.now(self.tz).strftime(i18n.date_format())} {result}",
                         0 if sched["run_at"] else 1, window_until, sched["id"]))
        audit.record("schedule.run", result=result_flag, username=ACTOR, ip="intern", server=ident,
                     detail=f"{sched['name']} ({i18n.t(ACTIONS[action])}): {result}")


def duration_text(minutes: int | None) -> str:
    minutes = minutes or 60
    if minutes < 60:
        return i18n.t("{n} Min.", n=minutes)
    hours = minutes / 60
    return i18n.t("{n} Std.", n=f"{hours:g}".replace(".", "," if i18n.current() == "de" else "."))


def describe(sched) -> str:
    t = i18n.t
    if sched["run_at"]:
        try:
            when = datetime.fromisoformat(sched["run_at"])
        except ValueError:
            return "–"
        return t("einmalig am {date} um {time} Uhr", date=when.strftime(i18n.date_format().split(",")[0]),
                 time=when.strftime("%H:%M"))
    days = {int(d) for d in sched["weekdays"] if d.isdigit()}
    if days == set(range(7)):
        day_text = t("täglich")
    elif days == set(range(5)):
        day_text = t("Mo–Fr")
    elif days == {5, 6}:
        day_text = t("Sa+So")
    else:
        day_text = ", ".join(t(WEEKDAYS[d]) for d in sorted(days))
    return t("{days} um {time} Uhr", days=day_text, time=sched["time"])
