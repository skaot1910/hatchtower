"""Prüft, auf welchen Ports ein Game-Server per Steam-Query (A2S) antwortet.

Aufruf im LXC:  bash /opt/hatchtower/tools/run-py.sh /opt/hatchtower/tools/query-test.py 192.168.1.20 2456 2457
"""
import sys

import a2s

host, ports = sys.argv[1], [int(p) for p in sys.argv[2:]]
for port in ports:
    try:
        info = a2s.info((host, port), timeout=2)
        print(f"{port}: antwortet – {info.server_name} ({info.player_count}/{info.max_players})")
    except Exception as exc:
        print(f"{port}: keine Antwort ({exc.__class__.__name__})")
