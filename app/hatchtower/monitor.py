"""Hintergrund-Worker: spiegelt Pterodactyl-Daten und beobachtet Statuswechsel.

Pterodactyl bleibt die Autorität: Der Status wird unverändert übernommen.
Der Manager ergänzt nur, was Pterodactyl nicht speichert (z. B. wann ein
Server heruntergefahren wurde), und kennzeichnet das als eigene Beobachtung.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from . import db, i18n, notify
from .gamequery import GameQuery
from .ptero import PteroClient, PteroError
from .security import Vault

log = logging.getLogger("hatchtower.monitor")

NAME_VARS = re.compile(r"^(SERVER_?NAME|SRV_?NAME|SERVER_?HOSTNAME|HOSTNAME|SESSION_?NAME|WORLD_?NAME_?DISPLAY)$", re.I)
PASSWORD_VARS = re.compile(r"^(SERVER_?PASS(WORD)?|SRV_?(PW|PASS(WORD)?)|JOIN_?PASS(WORD)?|GAME_?PASS(WORD)?|PASSWORD|PASS)$", re.I)
NEVER_SHOW = re.compile(r"ADMIN|RCON|RMT|STEAM|TOKEN|API|SECRET|KEY|OWNER", re.I)


def pick_startup_values(variables: dict[str, str]) -> tuple[str | None, str | None]:
    name = password = None
    for env, value in variables.items():
        if not value or NEVER_SHOW.search(env):
            continue
        if name is None and NAME_VARS.match(env):
            name = value
        elif password is None and PASSWORD_VARS.match(env):
            password = value
    return name, password


METRICS_KEEP_SECONDS = 73 * 3600   # 72 Stunden plus Puffer
METRICS_MIN_GAP = 10              # höchstens ein Messpunkt pro 10 s und Server
LIGHT_SYNC_INTERVAL = 30          # Serverliste mit Limits (CPU, RAM, Speicher): eine Anfrage je 100 Server
PANEL_DOWN_AFTER = 2              # so viele Fehlversuche in Folge, bis Pterodactyl als ausgefallen gilt

POLL_QUERY = (
    "SELECT s.identifier, s.primary_ip, s.allocations, s.limit_memory, s.limit_disk, s.limit_cpu, m.query_port AS fixed_port,"
    " st.state AS prev_state FROM servers s"
    " LEFT JOIN server_meta m ON m.identifier = s.identifier"
    " LEFT JOIN server_state st ON st.identifier = s.identifier"
    " WHERE s.present = 1"
)


class Monitor:
    def __init__(self, client: PteroClient, vault: Vault, poll_interval: int, sync_interval: int):
        self.client = client
        self.vault = vault
        self.poll_interval = poll_interval
        self.sync_interval = sync_interval
        self.query = GameQuery()
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.last_sync: str | None = None
        self._task: asyncio.Task | None = None
        self.on_offline = None  # Rückruf für Automationen (Absturz-Erkennung)
        self._follow: dict[str, float] = {}
        self._last_sample: dict[str, float] = {}
        self._follow_task: asyncio.Task | None = None
        self._next_sync = 0.0
        self._next_light = 0.0
        self.watch = notify.ResourceWatch(vault)
        self._tracked: set[str] = set()
        self.down_since: str | None = None      # seit wann Pterodactyl nicht antwortet
        self._fails = 0

    def request_sync(self) -> None:
        """Serverliste beim nächsten Durchlauf neu aus Pterodactyl lesen (z. B. nach neuer Verbindung)."""
        self._next_sync = 0.0
        self.last_error = None

    def request_light_sync(self) -> None:
        """Limits beim nächsten Durchlauf neu lesen (z. B. nach einem Neustart, der geänderte Limits übernimmt)."""
        self._next_light = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="hatchtower-monitor")

    async def stop(self) -> None:
        for task in (self._task, self._follow_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def follow(self, identifier: str, seconds: int = 90) -> None:
        """Nach einer Aktion den Server kurz eng beobachten, damit Zwischenstände wie
        starting oder stopping sofort sichtbar werden: die ersten 30 Sekunden jede Sekunde,
        danach alle 3 Sekunden – nur dieser Server."""
        now = time.monotonic()
        self._follow[identifier] = (now, now + seconds)
        if self._follow_task is None or self._follow_task.done():
            self._follow_task = asyncio.create_task(self._follow_loop(), name="hatchtower-follow")

    async def _follow_loop(self) -> None:
        await asyncio.sleep(0.5)    # Wings braucht einen Moment, bis der neue Zustand gemeldet wird
        last: dict[str, float] = {}
        while self._follow:
            now = time.monotonic()
            for ident in [i for i, (_, until) in self._follow.items() if until < now]:
                self._follow.pop(ident, None)
                last.pop(ident, None)
            for ident, (started, _) in list(self._follow.items()):
                gap = 1 if now - started < 30 else 3
                if now - last.get(ident, 0) < gap:
                    continue
                last[ident] = now
                try:
                    await self.refresh(ident)
                except PteroError as exc:
                    log.info("Beobachtung von %s: %s", ident, exc)
            await asyncio.sleep(1)

    async def refresh(self, identifier: str) -> None:
        with db.connect() as con:
            row = con.execute(POLL_QUERY + " AND s.identifier = ?", (identifier,)).fetchone()
        if row is not None:
            await self._poll_one(row)

    @property
    def healthy(self) -> bool:
        return self.last_error is None and self.last_ok is not None

    async def _run(self) -> None:
        while True:
            try:
                if not self.client.settings.configured:
                    self.last_error = "Noch nicht eingerichtet: Verbindung zu Pterodactyl fehlt"
                    await asyncio.sleep(5)
                    continue
                if time.monotonic() >= self._next_sync:
                    await self.sync()
                    self._next_sync = time.monotonic() + self.sync_interval
                    self._next_light = time.monotonic() + LIGHT_SYNC_INTERVAL
                elif time.monotonic() >= self._next_light:
                    await self.sync(full=False)
                    self._next_light = time.monotonic() + LIGHT_SYNC_INTERVAL
                await self.poll()
                self.last_ok = db.utcnow()
                self.last_error = None
                self._panel_ok()
            except asyncio.CancelledError:
                raise
            except PteroError as exc:
                self.last_error = str(exc)
                log.warning("Monitoring: %s", exc)
                self._panel_failed(exc)
            except Exception as exc:
                self.last_error = i18n.Msg("Interner Fehler: {typ}", typ=exc.__class__.__name__)
                log.exception("Monitoring-Fehler")
            await asyncio.sleep(self.poll_interval)

    def _panel_failed(self, exc: Exception) -> None:
        self._fails += 1
        if self._fails == PANEL_DOWN_AFTER and self.down_since is None:
            self.down_since = db.utcnow()
            i18n.use_default()
            notify.emit(self.vault, "panel_down", None, i18n.t(
                "Hatchtower bekommt von Pterodactyl keine Daten mehr ({grund}). Die angezeigten Zustände sind"
                " nicht aktuell, bis das Panel wieder antwortet.", grund=exc))

    def _panel_ok(self) -> None:
        self._fails = 0
        if self.down_since is not None:
            since = datetime.fromisoformat(self.down_since)
            minutes = max(1, round((datetime.now(timezone.utc) - since).total_seconds() / 60))
            self.down_since = None
            i18n.use_default()
            notify.emit(self.vault, "panel_up", None, i18n.t(
                "Pterodactyl antwortet wieder (Ausfall etwa {n} Minuten).", n=minutes))

    async def sync(self, full: bool = True) -> None:
        """Serverliste mit Limits aus Pterodactyl. full=False liest die Startvariablen
        (eine Anfrage je Server) nicht neu – die bleiben dann unverändert."""
        if full:
            await self.client.list_nodes()
        servers = await self.client.list_servers()
        startup: dict[str, tuple[str | None, str | None]] = {}
        if full and self.client.settings.client_key:
            for server in servers:
                try:
                    startup[server.identifier] = pick_startup_values(
                        await self.client.startup_variables(server.identifier)
                    )
                except PteroError as exc:
                    log.info("Startup-Variablen von %s nicht lesbar: %s", server.identifier, exc)

        now = db.utcnow()
        with db.connect() as con:
            con.execute("UPDATE servers SET present = 0")
            for s in servers:
                name, password = startup.get(s.identifier, (None, None))
                con.execute(
                    """
                    INSERT INTO servers (identifier, ptero_id, uuid, name, node_id, egg, allocations,
                        primary_ip, primary_port, limit_memory, limit_disk, limit_cpu, suspended, present,
                        startup_name, startup_password_enc, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(identifier) DO UPDATE SET
                        ptero_id = excluded.ptero_id, uuid = excluded.uuid, name = excluded.name,
                        node_id = excluded.node_id, egg = excluded.egg, allocations = excluded.allocations,
                        primary_ip = excluded.primary_ip, primary_port = excluded.primary_port,
                        limit_memory = excluded.limit_memory, limit_disk = excluded.limit_disk,
                        limit_cpu = excluded.limit_cpu, suspended = excluded.suspended, present = 1,
                        startup_name = COALESCE(excluded.startup_name, servers.startup_name),
                        startup_password_enc = COALESCE(excluded.startup_password_enc, servers.startup_password_enc),
                        synced_at = excluded.synced_at
                    """,
                    (
                        s.identifier, s.ptero_id, s.uuid, s.name, s.node_id, s.egg, json.dumps(s.allocations),
                        s.primary_ip, s.primary_port, s.limit_memory, s.limit_disk, s.limit_cpu,
                        int(s.suspended), name, self.vault.encrypt(password) if password else None, now,
                    ),
                )
        self.last_sync = now
        if full:
            with db.connect() as con:
                con.execute("DELETE FROM metrics WHERE ts < ?", (int(time.time()) - METRICS_KEEP_SECONDS,))

    async def poll(self) -> None:
        if not self.client.settings.client_key:
            raise PteroError("Client-API-Key fehlt: Status kann nicht gelesen werden")
        with db.connect() as con:
            rows = con.execute(POLL_QUERY).fetchall()

        results = await asyncio.gather(*(self._poll_one(row) for row in rows), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors and len(errors) == len(rows):
            raise errors[0] if isinstance(errors[0], PteroError) else PteroError(str(errors[0]))

    async def _poll_one(self, row) -> None:
        ident = row["identifier"]
        res = await self.client.resources(ident)
        now = datetime.now(timezone.utc)
        now_s = now.isoformat(timespec="seconds")
        state = res["state"]
        prev = row["prev_state"]

        started_at = None
        if state in ("running", "starting") and res["uptime_ms"] > 0:
            started_at = (now - timedelta(milliseconds=res["uptime_ms"])).isoformat(timespec="seconds")
        stopped_at = now_s if prev in ("running", "starting", "stopping") and state == "offline" else None
        if prev in ("running", "starting") and state == "offline" and self.on_offline:
            self.on_offline(ident)
        first_seen = ident not in self._tracked
        self._tracked.add(ident)
        with db.connect() as con:
            # Messung der Verfügbarkeit beginnt mit dem ersten bekannten Zustand
            if state != prev or (first_seen and not con.execute(
                    "SELECT 1 FROM state_changes WHERE identifier = ? LIMIT 1", (ident,)).fetchone()):
                con.execute("INSERT INTO state_changes (identifier, ts, state) VALUES (?, ?, ?)",
                            (ident, int(time.time()), state))
        try:
            i18n.use_default()
            self.watch.check(ident, state, res["memory_bytes"], (row["limit_memory"] or 0) * 1024 * 1024,
                             res["disk_bytes"], (row["limit_disk"] or 0) * 1024 * 1024,
                             res["cpu"] or 0.0, row["limit_cpu"] or 0)
        except Exception:
            log.exception("Ressourcen-Prüfung")

        players = max_players = query_name = query_port = None
        if state == "running":
            ports = [a["port"] for a in json.loads(row["allocations"] or "[]") if a.get("port")]
            q = await self.query.query(ident, row["primary_ip"], ports, row["fixed_port"])
            if q:
                players, max_players, query_name, query_port = q.players, q.max_players, q.name, q.port
        else:
            self.query.forget(ident)

        if time.time() - self._last_sample.get(ident, 0) >= METRICS_MIN_GAP:
            self._last_sample[ident] = time.time()
            with db.connect() as con:
                con.execute("INSERT INTO metrics (identifier, ts, cpu, mem, rx, tx, players) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ident, int(time.time()), res["cpu"], res["memory_bytes"], res["rx_bytes"], res["tx_bytes"],
                             players))
        with db.connect() as con:
            con.execute(
                """
                INSERT INTO server_state (identifier, state, last_started_at, last_stopped_at, cpu,
                    memory_bytes, disk_bytes, uptime_ms, players, max_players, query_name, query_port, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identifier) DO UPDATE SET
                    state = excluded.state,
                    last_started_at = COALESCE(excluded.last_started_at, server_state.last_started_at),
                    last_stopped_at = COALESCE(excluded.last_stopped_at, server_state.last_stopped_at),
                    cpu = excluded.cpu, memory_bytes = excluded.memory_bytes, disk_bytes = excluded.disk_bytes,
                    uptime_ms = excluded.uptime_ms, players = excluded.players, max_players = excluded.max_players,
                    query_name = COALESCE(excluded.query_name, server_state.query_name),
                    query_port = COALESCE(excluded.query_port, server_state.query_port),
                    updated_at = excluded.updated_at
                """,
                (ident, state, started_at, stopped_at, res["cpu"], res["memory_bytes"], res["disk_bytes"],
                 res["uptime_ms"], players, max_players, query_name, query_port, now_s),
            )
