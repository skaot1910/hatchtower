"""Konsole und Live-Log ueber den Wings-Websocket.

Im Read-Only-Modus darf der Manager an Wings nur Events senden, die nichts
veraendern: Anmeldung, Log-Verlauf anfordern, Statistiken anfordern.

Wings accepts the websocket only if the Origin header equals `remote` in its config.yml (or an entry in
`allowed_origins`), compared character by character. The panel address entered in Hatchtower is often a
different one (LAN IP instead of the domain, http instead of https), so several candidates are tried and the
one that works is remembered per Wings node.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from .i18n import t
from .ptero import PteroClient, PteroError, ReadOnlyViolation

log = logging.getLogger("hatchtower.wings")

READ_ONLY_EVENTS = frozenset({"auth", "send logs", "send stats"})

# (panel address in Hatchtower, Wings host:port) -> Origin that Wings accepted
_origins: dict[tuple[str, str], str] = {}


class WingsRejected(PteroError):
    """Wings answered the handshake with an HTTP error (403: no Origin candidate matched)."""


async def _send(ws, client: PteroClient, event: str, args: list[Any]) -> None:
    if client.settings.read_only and event not in READ_ONLY_EVENTS:
        raise ReadOnlyViolation(f"Websocket-Event '{event}' ist im Read-Only-Modus gesperrt")
    await ws.send(json.dumps({"event": event, "args": args}))


def token_issuer(token: str) -> str:
    """Panel address from the websocket JWT ('iss' = APP_URL in Pterodactyl and Pelican, the same value the
    panel writes to Wings as `remote`). Only the claims are read, the signature does not matter here."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        iss = str(claims.get("iss") or "") if isinstance(claims, dict) else ""
    except (IndexError, ValueError, TypeError):
        return ""
    return iss.rstrip("/") if urlsplit(iss).scheme in ("http", "https") and urlsplit(iss).hostname else ""


def _swap_scheme(url: str) -> str:
    if url.startswith("https://"):
        return "http://" + url[len("https://"):]
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return ""


def origin_candidates(panel_url: str, token: str, cached: str = "") -> list[str]:
    """Origins to try, best guess first: last working one, token issuer, panel address, then http/https swapped."""
    base = [o for o in (cached, token_issuer(token), panel_url.rstrip("/")) if o]
    result: list[str] = []
    for o in base + [_swap_scheme(o) for o in base]:
        if o and o not in result:
            result.append(o)
    return result


async def open_socket(client: PteroClient, token: str, socket_url: str) -> tuple[ClientConnection, str]:
    """Connects to Wings, trying the Origin candidates. Returns the connection and the Origin used.
    Raises WingsRejected (handshake refused) or PteroError (Wings not reachable)."""
    panel_url = client.settings.pterodactyl_url
    key = (panel_url, urlsplit(socket_url).netloc)
    candidates = origin_candidates(panel_url, token, _origins.get(key, ""))
    for origin in candidates:
        try:
            ws = await connect(socket_url, origin=origin, open_timeout=10, max_size=2**20, ping_interval=30)
        except InvalidStatus as exc:
            code = exc.response.status_code
            if code == 403:
                continue
            log.warning("Wings lehnt den Websocket ab: HTTP %s", code)
            raise WingsRejected(t("{quelle} antwortet mit HTTP {code}", quelle="Wings", code=code), code) from exc
        except (OSError, WebSocketException) as exc:
            log.warning("Wings nicht erreichbar (Websocket): %s", exc.__class__.__name__)
            raise PteroError(t("Wings nicht erreichbar: {grund}", grund=exc.__class__.__name__)) from exc
        if _origins.get(key) != origin:
            if origin != panel_url:
                log.info("Wings akzeptiert die Konsole mit Origin %s (statt der Panel-Adresse)", origin)
            _origins[key] = origin
        return ws, origin
    _origins.pop(key, None)
    log.warning("Wings lehnt den Websocket ab (HTTP 403), probiert: %s", ", ".join(candidates))
    raise WingsRejected(t("Wings lehnt die Verbindung ab (HTTP 403): Die Panel-Adresse {url} passt nicht zu "
                          "„remote“ in der config.yml von Wings. Abhilfe: dort unter „allowed_origins“ {url} "
                          "eintragen und Wings neu starten.", url=panel_url), 403)


async def console_stream(client: PteroClient, identifier: str) -> AsyncIterator[dict[str, Any]]:
    """Liefert Konsolenzeilen und Statuswechsel eines Servers."""
    token, socket_url = await client.websocket_credentials(identifier)
    socket_url = await client.wings_url(socket_url)
    try:
        ws, _origin = await open_socket(client, token, socket_url)
    except WingsRejected as exc:
        # Retrying does not help until the configuration changes
        yield {"type": "error", "text": str(exc), "fatal": exc.status in (401, 403)}
        return
    except PteroError as exc:
        yield {"type": "error", "text": str(exc)}
        return

    try:
        async with ws:
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
                    yield {"type": "error", "text": t("Wings hat die Anmeldung abgelehnt")}
                    return
    except (OSError, ConnectionClosed, WebSocketException) as exc:
        log.warning("Websocket zu Wings getrennt: %s", exc.__class__.__name__)
        yield {"type": "error", "text": t("Verbindung zu Wings unterbrochen")}


async def probe(client: PteroClient, identifier: str) -> str:
    """Opens and closes the console connection once (for the diagnosis). Returns the Origin Wings accepted;
    raises WingsRejected or PteroError like open_socket."""
    token, socket_url = await client.websocket_credentials(identifier)
    socket_url = await client.wings_url(socket_url)
    ws, origin = await open_socket(client, token, socket_url)
    await ws.close()
    return origin
