#!/usr/bin/env bash
# Hatchtower – Test-Instanz für Oberflächentests (Port 8001, nur lokal).
# Eigene Wegwerf-Datenbank, immer read-only, beendet sich nach spätestens 60 Minuten.
# Zugriff vom Admin-PC per SSH-Tunnel:  ssh -L 8001:127.0.0.1:8001 <server>
# Der Test-Login (Benutzer "demo") steht in /root/hatchtower-dev.pw und wird beim Ende gelöscht.
set -euo pipefail
[ -f /etc/hatchtower/hatchtower.env ] || { echo "Hatchtower ist hier nicht installiert"; exit 1; }

T=$(mktemp -d /tmp/hatchtower-dev.XXXX)
chown hatchtower:hatchtower "$T"
set -a; . /etc/hatchtower/hatchtower.env; set +a
export HATCHTOWER_DATA_DIR="$T" HOME="$T"
cd /opt/hatchtower/app
PW="Dev-$(head -c12 /dev/urandom | base64 | tr -dc A-Za-z0-9)-1x"
umask 077; printf '%s\n' "$PW" > /root/hatchtower-dev.pw

setpriv --reuid=hatchtower --regid=hatchtower --init-groups /opt/hatchtower/venv/bin/python - "$PW" <<'EOF'
import os, sqlite3, sys
from pathlib import Path
from hatchtower import db, security
db.init(Path(os.environ["HATCHTOWER_DATA_DIR"]) / "hatchtower.db")
live = "/var/lib/hatchtower/hatchtower.db"
with db.connect() as con:
    if os.path.exists(live):
        src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        for key, value in src.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%' OR key = 'public_host'"):
            con.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    con.execute("INSERT INTO app_settings (key, value) VALUES ('conn_mode', 'read-only') ON CONFLICT(key) DO UPDATE SET value = 'read-only'")
    con.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('demo', ?, 'owner', ?)",
                (security.hash_password(sys.argv[1]), db.utcnow()))
EOF

cleanup() { rm -rf "$T" /root/hatchtower-dev.pw; }
trap cleanup EXIT
timeout 3600 setpriv --reuid=hatchtower --regid=hatchtower --init-groups /opt/hatchtower/venv/bin/uvicorn hatchtower.main:app \
  --host 127.0.0.1 --port 8001 --log-level warning
