"""Browser downloads: one log line per download – device and browser, part request or not, bytes sent, finished or
broken off – so a problem with one particular browser (e.g. Safari's download manager, or a home-screen web app
on iOS) shows up in the log and in the support report instead of only on the user's screen."""
from __future__ import annotations

import logging
import re
import time
from typing import AsyncIterator

log = logging.getLogger("hatchtower.download")


def device(user_agent: str | None) -> str:
    """Rough device and browser from the User-Agent, enough to tell the usual cases apart."""
    ua = user_agent or ""
    system = ("iPad" if "iPad" in ua else "iPhone" if "iPhone" in ua else "Android" if "Android" in ua
              else "Windows" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "?")
    if "FxiOS" in ua or "Firefox" in ua:
        browser = "Firefox"
    elif "EdgiOS" in ua or "Edg/" in ua:
        browser = "Edge"
    elif "CriOS" in ua or "Chrome" in ua:
        browser = "Chrome"
    elif "Safari" in ua:
        browser = "Safari"
    elif "Mobile/" in ua or "AppleWebKit" in ua:
        browser = "web app from the home screen"      # iOS home-screen apps leave out "Safari"
    else:
        browser = "?"
    return f"{system}, {browser}"


async def tracked(body: AsyncIterator[bytes], what: str, user_agent: str | None, status: int,
                  range_header: str | None, total: int | None) -> AsyncIterator[bytes]:
    """Pass the body through and log how the download ended."""
    started, sent, finished = time.monotonic(), 0, False
    try:
        async for chunk in body:
            sent += len(chunk)
            yield chunk
        finished = True
    finally:
        log.info("Download %s: %s, HTTP %s%s, %s von %s Bytes in %.0f s – %s", what, device(user_agent), status,
                 f", Teil {range_header}" if range_header else "", sent, total if total else "?",
                 time.monotonic() - started, "vollständig" if finished else "vom Browser abgebrochen")


def counts(range_header: str | None) -> bool:
    """Whether a request is a new download for the audit log and the history: the whole file, not one of the part
    requests a download manager makes for the same download (Safari first asks for two bytes, then the rest)."""
    return not range_header or range_header.replace(" ", "") == "bytes=0-"


def requested(what: str, user_agent: str | None, range_header: str | None) -> None:
    """For downloads the web server sends by itself (files on disk): at least note the request."""
    log.info("Download %s angefragt: %s%s", what, device(user_agent), f", Teil {range_header}" if range_header else "")


# -- part (Range) requests -----------------------------------------------------------------------------
# Safari's download manager first asks for a few bytes and then for the rest. Wings ignores Range for backups
# and sends the whole file every time, which makes Safari give up ("Download failed"). Hatchtower answers part
# requests itself: it skips the bytes before the part and stops after it.

RANGE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$")


def parse_range(header: str | None, total: int) -> tuple[int, int] | None | bool:
    """(start, end) inclusive for a single range, None for no/unsupported range (send everything),
    False if it cannot be satisfied."""
    m = RANGE.match(header or "")
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if not m.group(1):                                  # bytes=-500: the last 500 bytes
        length = int(m.group(2))
        return (max(0, total - length), total - 1) if length else False
    start = int(m.group(1))
    end = min(int(m.group(2)), total - 1) if m.group(2) else total - 1
    return (start, end) if start <= end and start < total else False


async def _cut(body: AsyncIterator[bytes], start: int, end: int) -> AsyncIterator[bytes]:
    position = 0
    try:
        async for chunk in body:
            chunk_end = position + len(chunk)
            if chunk_end > start:
                piece = chunk[max(0, start - position):end + 1 - position]
                if piece:
                    yield piece
            position = chunk_end
            if position > end:
                break
    finally:
        closer = getattr(body, "aclose", None)
        if closer:
            await closer()                              # stop reading from Wings


def ranged(status: int, headers: dict, body: AsyncIterator[bytes], range_header: str | None):
    """Answer a part request from a whole-file stream. Returns (status, headers, body); headers are changed in place."""
    total = int(headers.get("Content-Length") or 0)
    if status != 200 or not range_header or not total:
        return status, headers, body
    headers["Accept-Ranges"] = "bytes"
    part = parse_range(range_header, total)
    if part is None:
        return status, headers, body
    if part is False:
        headers.pop("Content-Length", None)
        headers["Content-Range"] = f"bytes */{total}"
        return 416, headers, _cut(body, 1, 0)
    start, end = part
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    headers["Content-Length"] = str(end - start + 1)
    return 206, headers, _cut(body, start, end)
