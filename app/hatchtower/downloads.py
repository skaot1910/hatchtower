"""Browser downloads: one log line per download – device and browser, part request or not, bytes sent, finished or
broken off – so a problem with one particular browser (e.g. Safari's download manager, or a home-screen web app
on iOS) shows up in the log and in the support report instead of only on the user's screen."""
from __future__ import annotations

import logging
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


def requested(what: str, user_agent: str | None, range_header: str | None) -> None:
    """For downloads the web server sends by itself (files on disk): at least note the request."""
    log.info("Download %s angefragt: %s%s", what, device(user_agent), f", Teil {range_header}" if range_header else "")
