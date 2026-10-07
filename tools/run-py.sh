#!/usr/bin/env bash
# Führt ein Python-Skript mit der Konfiguration von Hatchtower als Benutzer hatchtower aus.
# Aufruf:  bash /opt/hatchtower/tools/run-py.sh /opt/hatchtower/tools/query-test.py 192.168.1.20 2456
set -euo pipefail
set -a; . /etc/hatchtower/hatchtower.env; set +a
cd /opt/hatchtower/app
export HOME=/var/lib/hatchtower PYTHONPATH=/opt/hatchtower/app
exec setpriv --reuid=hatchtower --regid=hatchtower --init-groups /opt/hatchtower/venv/bin/python "$@"
