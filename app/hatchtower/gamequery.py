"""Player count and in-game server name via query protocols.

Supported: Steam A2S, Minecraft Java (server list ping) and Minecraft Bedrock (RakNet unconnected ping).
No manual setup needed: the ports from Pterodactyl are tried (and port+1 for A2S, as used by Valheim).
Games without one of these protocols simply return nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import struct
import time
from dataclasses import dataclass

import a2s

log = logging.getLogger("hatchtower.query")

DISCOVERY_RETRY_SECONDS = 300
RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
# The Minecraft Java ping opens a TCP connection; other games log that as a player connecting,
# so it is only tried for eggs that look like Minecraft Java
MINECRAFT_JAVA = re.compile(r"minecraft|paper|spigot|bukkit|forge|fabric|purpur|folia|quilt|velocity|bungee"
                            r"|waterfall|sponge|mohist|magma|arclight|modpack|curseforge|ftb", re.I)
BEDROCK = re.compile(r"bedrock|pocketmine|nukkit|geyser", re.I)


def is_minecraft_java(egg: str) -> bool:
    return bool(MINECRAFT_JAVA.search(egg)) and not BEDROCK.search(egg)


@dataclass
class QueryResult:
    name: str
    players: int
    max_players: int
    port: int
    protocol: str = "a2s"


def _varint(n: int) -> bytes:
    n &= 0xFFFFFFFF
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


async def _read_varint(reader: asyncio.StreamReader) -> int:
    value = 0
    for i in range(5):
        b = (await reader.readexactly(1))[0]
        value |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return value
    raise ValueError("varint too long")


def _motd_text(desc) -> str:
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return str(desc.get("text") or "") + "".join(_motd_text(x) for x in desc.get("extra") or [])
    if isinstance(desc, list):
        return "".join(_motd_text(x) for x in desc)
    return ""


async def _minecraft_java(host: str, port: int) -> QueryResult | None:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        hp = host.encode()
        data = _varint(0) + _varint(767) + _varint(len(hp)) + hp + struct.pack(">H", port) + _varint(1)
        writer.write(_varint(len(data)) + data + b"\x01\x00")
        await writer.drain()
        await _read_varint(reader)            # packet length
        if await _read_varint(reader) != 0:   # packet id
            return None
        size = await _read_varint(reader)
        if size > 1 << 20:
            return None
        status = json.loads((await reader.readexactly(size)).decode("utf-8", "replace"))
        players = status.get("players") or {}
        return QueryResult(name=_motd_text(status.get("description")).replace("\n", " ").strip(),
                           players=int(players.get("online") or 0), max_players=int(players.get("max") or 0),
                           port=port, protocol="minecraft")
    finally:
        writer.close()


class _Datagram(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.answer: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr) -> None:
        if not self.answer.done():
            self.answer.set_result(data)

    def error_received(self, exc) -> None:
        if not self.answer.done():
            self.answer.set_exception(exc)


async def _minecraft_bedrock(host: str, port: int) -> QueryResult | None:
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(_Datagram, remote_addr=(host, port))
    try:
        ping = b"\x01" + struct.pack(">Q", int(time.time() * 1000) & (2**64 - 1)) + RAKNET_MAGIC + os.urandom(8)
        transport.sendto(ping)
        data = await proto.answer
    finally:
        transport.close()
    if len(data) < 35 or data[0] != 0x1C:
        return None
    length = struct.unpack(">H", data[33:35])[0]
    fields = data[35:35 + length].decode("utf-8", "replace").split(";")
    if len(fields) < 6 or fields[0] not in ("MCPE", "MCEE"):
        return None
    return QueryResult(name=fields[1], players=int(fields[4] or 0), max_players=int(fields[5] or 0),
                       port=port, protocol="bedrock")


async def _a2s(host: str, port: int, timeout: float) -> QueryResult | None:
    info = await a2s.ainfo((host, port), timeout=timeout)
    return QueryResult(
        name=str(getattr(info, "server_name", "") or ""),
        players=int(getattr(info, "player_count", 0) or 0),
        max_players=int(getattr(info, "max_players", 0) or 0),
        port=port,
    )


class GameQuery:
    def __init__(self, timeout: float = 1.5):
        self.timeout = timeout
        self._known: dict[str, tuple[int, str]] = {}
        self._failed_at: dict[str, float] = {}

    async def _probe(self, protocol: str, host: str, port: int) -> QueryResult | None:
        try:
            if protocol == "a2s":
                return await _a2s(host, port, self.timeout)
            if protocol == "minecraft":
                return await asyncio.wait_for(_minecraft_java(host, port), self.timeout)
            if protocol == "bedrock":
                return await asyncio.wait_for(_minecraft_bedrock(host, port), self.timeout)
        except Exception:
            return None
        return None

    async def _try(self, host: str, port: int, java: bool) -> QueryResult | None:
        protocols = ("a2s", "minecraft", "bedrock") if java else ("a2s", "bedrock")
        results = await asyncio.gather(*(self._probe(p, host, port) for p in protocols))
        return next((r for r in results if r), None)

    async def query(self, identifier: str, host: str | None, ports: list[int],
                    fixed_port: int | None = None, java: bool = False) -> QueryResult | None:
        if not host:
            return None

        known = self._known.get(identifier)
        if known and (not fixed_port or known[0] == fixed_port):
            result = await self._probe(known[1], host, known[0])
            if result:
                return result
            self._known.pop(identifier, None)

        if time.monotonic() - self._failed_at.get(identifier, -1e9) < DISCOVERY_RETRY_SECONDS:
            return None

        if fixed_port:
            candidates = [(fixed_port, True)]
        else:
            candidates = []
            for port in ports:
                if (port, True) not in candidates:
                    candidates.append((port, True))
            for port in ports:   # port+1 only for A2S (Valheim and others)
                if port + 1 not in ports:
                    candidates.append((port + 1, False))
        for port, full in candidates:
            if full:
                result = await self._try(host, port, java)
            else:
                result = await self._probe("a2s", host, port)
            if result:
                self._known[identifier] = (port, result.protocol)
                self._failed_at.pop(identifier, None)
                return result
        self._failed_at[identifier] = time.monotonic()
        return None

    def forget(self, identifier: str) -> None:
        self._known.pop(identifier, None)
        self._failed_at.pop(identifier, None)
