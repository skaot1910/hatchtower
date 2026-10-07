"""Spielerzahl und Ingame-Servername per Steam-Query (A2S).

Ohne manuelle Freigaben: Der Manager probiert die Ports aus Pterodactyl
(und jeweils Port+1, wie z. B. bei Valheim). Spiele ohne A2S liefern einfach nichts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import a2s

log = logging.getLogger("hatchtower.query")

DISCOVERY_RETRY_SECONDS = 300


@dataclass
class QueryResult:
    name: str
    players: int
    max_players: int
    port: int


class GameQuery:
    def __init__(self, timeout: float = 1.5):
        self.timeout = timeout
        self._known_port: dict[str, int] = {}
        self._failed_at: dict[str, float] = {}

    async def _try(self, host: str, port: int) -> QueryResult | None:
        try:
            info = await a2s.ainfo((host, port), timeout=self.timeout)
        except Exception:
            return None
        return QueryResult(
            name=str(getattr(info, "server_name", "") or ""),
            players=int(getattr(info, "player_count", 0) or 0),
            max_players=int(getattr(info, "max_players", 0) or 0),
            port=port,
        )

    async def query(self, identifier: str, host: str | None, ports: list[int],
                    fixed_port: int | None = None) -> QueryResult | None:
        if not host:
            return None
        if fixed_port:
            return await self._try(host, fixed_port)

        known = self._known_port.get(identifier)
        if known:
            result = await self._try(host, known)
            if result:
                return result
            self._known_port.pop(identifier, None)

        if time.monotonic() - self._failed_at.get(identifier, -1e9) < DISCOVERY_RETRY_SECONDS:
            return None

        candidates: list[int] = []
        for port in ports:
            for candidate in (port, port + 1):
                if candidate not in candidates:
                    candidates.append(candidate)
        for port in candidates:
            result = await self._try(host, port)
            if result:
                self._known_port[identifier] = port
                self._failed_at.pop(identifier, None)
                return result
        self._failed_at[identifier] = time.monotonic()
        return None

    def forget(self, identifier: str) -> None:
        self._known_port.pop(identifier, None)
        self._failed_at.pop(identifier, None)
