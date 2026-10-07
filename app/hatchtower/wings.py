"""Konsole und Live-Log ueber den Wings-Websocket.

Im Read-Only-Modus darf der Manager an Wings nur Events senden, die nichts
veraendern: Anmeldung, Log-Verlauf anfordern, Statistiken anfordern.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .ptero import PteroClient, PteroError, ReadOnlyViolation

log = logging.getLogger("hatchtower.wings")

READ_ONLY_EVENTS = frozenset({"auth", "send logs", "send stats"})


async def _send(ws, client: PteroClient, event: str, args: list[Any]) -> None:
    if client.settings.read_only and event not in READ_ONLY_EVENTS:
        raise ReadOnlyViolation(f"Websocket-Event '{event}' ist im Read-Only-Modus gesperrt")
    await ws.send(json.dumps({"event": event, "args": args}))


async def console_stream(client: PteroClient, identifier: str) -> AsyncIterator[dict[str, Any]]:
    """Liefert Konsolenzeilen und Statuswechsel eines Servers."""
    token, socket_url = await client.websocket_credentials(identifier)
    socket_url = await client.wings_url(socket_url)


    try:
        async with connect(
            socket_url,
            origin=client.settings.pterodactyl_url,
            open_timeout=10,
            max_size=2**20,
            ping_interval=30,
        ) as ws:
            await _send(ws, client, "auth", [token])
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                event = message.get("event")
                args = message.get("args") or []
                if event == "auth success":
                    await _send(ws, client, "send logs", [None])
                elif event in ("console output", "install output", "daemon message", "daemon error"):
                    for text in args:
                        yield {"type": "line", "text": str(text), "source": event}
                elif event == "status" and args:
                    yield {"type": "status", "state": str(args[0])}
                elif event == "stats" and args:
                    try:
                        s = json.loads(args[0])
                    except (TypeError, ValueError):
                        continue
                    net = s.get("network") or {}
                    yield {"type": "stats", "cpu": float(s.get("cpu_absolute") or 0),
                           "mem": int(s.get("memory_bytes") or 0), "rx": int(net.get("rx_bytes") or 0),
                           "tx": int(net.get("tx_bytes") or 0)}
                elif event in ("token expiring", "token expired"):
                    token, _ = await client.websocket_credentials(identifier)
                    await _send(ws, client, "auth", [token])
                elif event == "jwt error":
                    yield {"type": "error", "text": "Wings hat die Anmeldung abgelehnt"}
                    return
    except (OSError, WebSocketException) as exc:
        log.warning("Websocket zu Wings getrennt: %s", exc.__class__.__name__)
        yield {"type": "error", "text": "Verbindung zu Wings unterbrochen"}
