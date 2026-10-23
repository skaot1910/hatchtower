"""The last log lines of this process, kept in memory for the support report.

The service user cannot read the system journal, so the web interface keeps its own short copy. Tokens in
URLs are cut out right away; the report removes all known secrets once more before it is handed out.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque

MAX_LINES = 1000
_TOKEN = re.compile(r"((?:token|signature|key|password)=)[^&\s\"']+", re.IGNORECASE)

_lines: deque[str] = deque(maxlen=MAX_LINES)
STARTED = time.time()            # imported at start-up by main, so this is when the process started


class _Handler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[0] is not None:
                text += f" [{record.exc_info[0].__name__}]"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record.created))
            text = _TOKEN.sub(r"\1***", text)
            _lines.append(f"{stamp}Z {record.levelname:<7} {record.name}: {text}")
        except Exception:  # logging must never break the application
            pass


_handler = _Handler(level=logging.INFO)


def install() -> None:
    """Attach to the root logger once (uvicorn and all hatchtower loggers propagate there)."""
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)


def lines(limit: int = 400) -> list[str]:
    return list(_lines)[-limit:]
