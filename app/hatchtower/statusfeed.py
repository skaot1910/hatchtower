"""Serverstatus als Discord-Nachricht(en), auch in mehreren Kanälen.

Hatchtower postet über je einen Webhook Nachrichten in einen Discord-Kanal und bearbeitet danach
immer dieselben Nachrichten. Jeder Kanal hat seine eigene Server-Auswahl, eigene Angaben und eine
eigene Überschrift, unabhängig von der Statusseite. Je drei Server nebeneinander, zwischen den
Reihen eine Trennlinie.

Viele Server: pro Embed höchstens SERVERS_PER_EMBED Server (Discord: max. 25 Felder), die Embeds
werden zu Nachrichten gepackt (Discord: max. 10 Embeds und rund 6000 Zeichen pro Nachricht).
Kommen Server hinzu, wird hinten eine Nachricht angehängt; fallen welche weg, wird die überzählige
gelöscht. Ist eine Nachricht im Kanal gelöscht worden, werden ab dort alle neu gepostet, damit die
Reihenfolge stimmt.

Geprüft wird jede Minute, bearbeitet nur bei Änderungen und spätestens alle REFRESH Sekunden
(damit die Zeitangabe zeigt, dass die Anzeige lebt). Discord erlaubt pro Webhook mehrere Anfragen
pro Sekunde, das bleibt weit darunter.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from . import APP_NAME, db, i18n, inbox, notify

log = logging.getLogger("hatchtower.statusfeed")

URL_RE = re.compile(r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d{5,25}/[A-Za-z0-9_-]{20,100}$")
CHECK_INTERVAL = 60
REFRESH = 600
PER_ROW = 3                  # Discord zeigt drei Inline-Felder nebeneinander
SERVERS_PER_EMBED = 15       # 5 Reihen + 4 Trennlinien = 19 von 25 Feldern
EMBEDS_PER_MESSAGE = 10
CHARS_PER_MESSAGE = 5500     # Discord-Grenze 6000, etwas Luft
MAX_MESSAGES = 5
SEPARATOR = {"name": "​", "value": "━━━━━━━━━━━━━━━━━━━━━━━━", "inline": False}
EMOJI = {"running": "🟢", "starting": "🟡", "stopping": "🟠", "offline": "🔴", "suspended": "⛔", "unknown": "⚪"}
SQUARE = {"running": "🟩", "starting": "🟨", "stopping": "🟧", "offline": "🟥"}
LABELS = {"running": "Online", "starting": "Startet", "stopping": "Wird gestoppt", "offline": "Offline",
          "suspended": "Gesperrt", "unknown": "Unbekannt"}
COLOR_OK, COLOR_PARTLY, COLOR_DOWN, COLOR_NEUTRAL = 0x10B981, 0xF59E0B, 0xE11D48, 0x8B5CF6


def valid_url(url: str) -> bool:
    return bool(URL_RE.match(url))


def load_feeds(only_enabled: bool = False) -> list:
    with db.connect() as con:
        return con.execute("SELECT * FROM status_feeds" + (" WHERE enabled = 1" if only_enabled else "")
                           + " ORDER BY name COLLATE NOCASE, id").fetchall()


def feed_servers(feed_id: int) -> set[str]:
    with db.connect() as con:
        return {r["identifier"] for r in con.execute(
            "SELECT identifier FROM status_feed_servers WHERE feed_id = ?", (feed_id,))}


def _label(s: dict) -> str:
    return s.get("name") or s.get("ingame") or s.get("egg") or "Server"


def _field(s: dict, show_state: bool) -> dict:
    """Ein Server als Kachel: Angaben untereinander."""
    label = _label(s)
    lines = []
    if show_state:
        lines.append(f"{EMOJI.get(s.get('state'), '⚪')} **{i18n.t(LABELS.get(s.get('state'), 'Unbekannt'))}**")
    if s.get("egg") and s["egg"] != label:
        lines.append(f"🎮 {s['egg']}")
    if s.get("address"):
        lines.append(f"`{s['address']}`")
    if s.get("ingame") and s["ingame"] != label:
        lines.append(i18n.t("Ingame-Servername: {name}", name=s["ingame"]))
    if s.get("password"):
        lines.append(i18n.t("Passwort: {pw}", pw=f"||{s['password'][:100]}||"))
    if s.get("started"):
        lines.append(f"🕒 {s['started']}")
    return {"name": label[:100], "value": ("\n".join(lines) or "–")[:400], "inline": True}


def _fields(servers: list[dict], show_state: bool) -> list[dict]:
    """Reihen zu je drei Servern, dazwischen eine Trennlinie."""
    result = []
    for i in range(0, len(servers), PER_ROW):
        if result:
            result.append(SEPARATOR)
        result += [_field(s, show_state) for s in servers[i:i + PER_ROW]]
    return result


def _size(embed: dict) -> int:
    return (len(embed.get("title", "")) + len(embed.get("description", "")) + len(embed.get("footer", {}).get("text", ""))
            + sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", [])))


def build(title: str, servers: list[dict], fields: set[str], link: str, manage_link: str = "") -> list[dict]:
    """Discord-Nachrichten aus den bereits gefilterten Angaben (ohne Zeitstempel).
    Übersicht und Link zur Statusseite stehen oben in der ersten, die Fußzeile unter der letzten Nachricht."""
    show_state = "status" in fields
    color, summary = COLOR_NEUTRAL, []
    links = ([f"🔗 [{i18n.t('Statusseite öffnen')}]({link})"] if link else []) + (
        [f"🛠️ [{i18n.t('Serververwaltung')}]({manage_link})"] if manage_link else [])
    if links:
        summary.append("  ·  ".join(links))
    if show_state and servers:
        online = sum(1 for s in servers if s.get("state") in ("running", "starting"))
        color = COLOR_OK if online == len(servers) else (COLOR_DOWN if online == 0 else COLOR_PARTLY)
        summary.append(i18n.t("**{online} von {total}** Servern online", online=online, total=len(servers)))
        if len(servers) <= 60:
            squares = "".join(SQUARE.get(s.get("state"), "⬜") for s in servers)
            summary += [squares[i:i + 15] for i in range(0, len(squares), 15)]
    if not servers:
        summary.append(i18n.t("Zurzeit sind keine Server für diesen Kanal ausgewählt."))
    limit = MAX_MESSAGES * EMBEDS_PER_MESSAGE * SERVERS_PER_EMBED
    if len(servers) > limit:
        summary.append(i18n.t("Weitere {n} Server passen nicht mehr in die Nachrichten.", n=len(servers) - limit))
        servers = servers[:limit]

    embeds = [{"color": color, "fields": _fields(servers[i:i + SERVERS_PER_EMBED], show_state)}
              for i in range(0, len(servers), SERVERS_PER_EMBED)] or [{"color": color}]
    embeds[0]["title"] = title[:250]
    if link:
        embeds[0]["url"] = link
    if summary:
        embeds[0]["description"] = "\n".join(summary)[:3500]
    embeds[-1]["footer"] = {"text": f"{APP_NAME} · {i18n.t('aktualisiert')}"}

    # Embeds zu Nachrichten packen: höchstens 10 und unter der Zeichengrenze
    messages: list[list[dict]] = []
    for embed in embeds:
        last = messages[-1] if messages else None
        if (last is None or len(last) >= EMBEDS_PER_MESSAGE
                or sum(map(_size, last)) + _size(embed) > CHARS_PER_MESSAGE):
            messages.append([embed])
        else:
            last.append(embed)
    messages = messages[:MAX_MESSAGES]
    # components leer: entfernt die Schaltfläche älterer Versionen
    return [{"username": title[:80], "embeds": m, "components": []} for m in messages]


class StatusFeed:
    """Hält die Discord-Nachrichten aller eingeschalteten Kanäle aktuell. provider(feed) liefert für einen
    Kanal (Titel, Server, Angaben, Link Statusseite, Link Verwaltung)."""

    def __init__(self, vault, provider: Callable[[object], tuple[str, list[dict], set[str], str, str]]):
        self.vault = vault
        self.provider = provider
        self.last_ok: dict[int, str] = {}
        self.last_error: dict[int, str] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_hash: dict[int, str] = {}
        self._last_sent: dict[int, float] = {}
        self._background: set[asyncio.Task] = set()
        self._fails: dict[int, int] = {}
        self._reported: set[int] = set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="hatchtower-statusfeed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def request_update(self, feed_id: int | None = None, reset: bool = False) -> None:
        """Nach dem Speichern sofort aktualisieren (reset: Status vergessen, z. B. bei neuem Webhook).
        Ohne feed_id gilt es für alle Kanäle (z. B. geänderte Serverangaben)."""
        ids = [feed_id] if feed_id is not None else list(self._last_hash)
        for fid in ids:
            self._last_hash.pop(fid, None)
            if reset:
                self.last_ok.pop(fid, None)
                self.last_error.pop(fid, None)
        self._wake.set()

    def remove(self, url: str | None, msg_ids: str) -> None:
        """Gelöschter Kanal: seine Nachrichten im Hintergrund aus Discord entfernen."""
        ids = [i for i in (msg_ids or "").split(",") if i.isdigit()]
        if not url or not valid_url(url) or not ids:
            return

        async def go():
            async with httpx.AsyncClient(timeout=10) as http:
                for message_id in ids:
                    await self._delete(http, url, message_id)
        task = asyncio.get_running_loop().create_task(go())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run(self) -> None:
        while True:
            for feed in load_feeds(only_enabled=True):
                try:
                    await self.update(feed)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._failed(feed, i18n.t("Interner Fehler: {typ}", typ=exc.__class__.__name__))
                    log.exception("Discord-Statusanzeige %s", feed["id"])
            try:
                await asyncio.wait_for(self._wake.wait(), CHECK_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def update(self, feed) -> None:
        fid = feed["id"]
        url = self.vault.decrypt(feed["url_enc"])
        if not url or not valid_url(url):
            return
        previous = i18n.current()
        i18n.use_default()
        try:
            payloads = build(*self.provider(feed))
        finally:
            i18n.set_language(previous)
        digest = hashlib.sha256(json.dumps(payloads, sort_keys=True).encode()).hexdigest()
        if digest == self._last_hash.get(fid) and time.monotonic() - self._last_sent.get(fid, 0.0) < REFRESH:
            return
        payloads[-1]["embeds"][-1]["timestamp"] = datetime.now(timezone.utc).isoformat()
        async with httpx.AsyncClient(timeout=10) as http:
            response = await self._sync(http, url, payloads, fid)
        if response.status_code >= 400 and notify.webhook_gone("discord", response.status_code):
            self._disable(feed, response.status_code)
            return
        if response.status_code >= 400:
            # 429 (zu viele Anfragen) erledigt sich beim nächsten Durchlauf von selbst
            self._failed(feed, i18n.t("Webhook antwortet mit HTTP {code}", code=response.status_code))
            log.warning("Discord-Statusanzeige %s: HTTP %s", fid, response.status_code)
            return
        self._last_hash[fid] = digest
        self._last_sent[fid] = time.monotonic()
        self.last_ok[fid] = db.utcnow()
        self.last_error.pop(fid, None)
        self._fails.pop(fid, None)
        if fid in self._reported:
            self._reported.discard(fid)
            inbox.add("statusfeed_ok", "owner", None, feed["name"])

    def _disable(self, feed, status: int) -> None:
        """The webhook is gone (a deleted message is simply posted again, so this is the webhook itself):
        switch the feed off instead of retrying every minute, and tell the owners why."""
        fid = feed["id"]
        with db.connect() as con:
            con.execute("UPDATE status_feeds SET enabled = 0, disabled_code = ?, disabled_at = ? WHERE id = ?",
                        (status, db.utcnow(), fid))
        self.last_error[fid] = notify.gone_reason(status)
        self._fails.pop(fid, None)
        self._reported.discard(fid)
        inbox.add("statusfeed_disabled", "owner", None,
                  f"{feed['name']}: {notify.gone_reason(status)} "
                  + i18n.t("Neuen Webhook unter Community → Statusseite eintragen und die Anzeige wieder einschalten."))
        log.warning("Discord-Statusanzeige %s automatisch abgeschaltet: HTTP %s", fid, status)

    def _failed(self, feed, message: str) -> None:
        """Remember the error; from the second failure in a row it goes into the owners' inbox (a single
        HTTP 429 sorts itself out on the next run). Repeated at most every 6 hours, also across restarts."""
        fid = feed["id"]
        self.last_error[fid] = message
        self._fails[fid] = self._fails.get(fid, 0) + 1
        if self._fails[fid] >= 2 and fid not in self._reported:
            self._reported.add(fid)
            inbox.add("statusfeed_failed", "owner", None, f"{feed['name']}: {message}",
                      key=f"statusfeed:{fid}", cooldown=6 * 3600)

    @staticmethod
    def _messages(fid: int) -> list[str]:
        with db.connect() as con:
            row = con.execute("SELECT msg_ids FROM status_feeds WHERE id = ?", (fid,)).fetchone()
        return [i for i in (row["msg_ids"] if row else "").split(",") if i.isdigit()]

    @staticmethod
    def _remember(fid: int, ids: list[str]) -> None:
        with db.connect() as con:
            con.execute("UPDATE status_feeds SET msg_ids = ? WHERE id = ?", (",".join(ids), fid))

    async def _sync(self, http: httpx.AsyncClient, url: str, payloads: list[dict], fid: int) -> httpx.Response:
        """Vorhandene Nachrichten der Reihe nach bearbeiten, fehlende anhängen, überzählige löschen.
        Gibt die letzte Antwort zurück (bei einem Fehler die fehlgeschlagene)."""
        old = self._messages(fid)
        kept: list[str] = []
        response = httpx.Response(200)
        try:
            for index, payload in enumerate(payloads):
                if index < len(old):
                    response = await http.patch(f"{url}/messages/{old[index]}", json=payload)
                    if response.status_code != 404:
                        if response.status_code >= 400:
                            return response
                        kept.append(old[index])
                        continue
                    # Nachricht im Kanal gelöscht: die folgenden auch entfernen und ab hier neu posten
                    for later in old[index + 1:]:
                        await self._delete(http, url, later)
                    old = old[:index]
                response = await http.post(url, params={"wait": "true"}, json=payload)
                if response.status_code >= 400:
                    return response
                kept.append(str(response.json().get("id", "")))
                await asyncio.sleep(0.5)          # mehrere Nachrichten: Discord nicht drängeln
            for extra in old[len(payloads):]:     # weniger Server als vorher
                await self._delete(http, url, extra)
            old = []
            return response
        finally:
            # Auch nach einem Fehler: bekannte Nachrichten merken, damit nichts doppelt gepostet wird
            self._remember(fid, kept + old[len(kept):])

    @staticmethod
    async def _delete(http: httpx.AsyncClient, url: str, message_id: str) -> None:
        response = await http.delete(f"{url}/messages/{message_id}")
        if response.status_code >= 400 and response.status_code != 404:
            log.warning("Discord-Statusanzeige: alte Nachricht nicht gelöscht (HTTP %s)", response.status_code)
