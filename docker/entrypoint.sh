#!/bin/sh
# Start im Container: Schlüssel beim ersten Start erzeugen, Einrichtungscode ausgeben, dann Hatchtower starten.
set -eu
DATA="${HATCHTOWER_DATA_DIR:-/var/lib/hatchtower}"
KEYS="$DATA/keys.env"
mkdir -p "$DATA"

if [ -z "${HATCHTOWER_SECRET_KEY:-}" ] || [ -z "${HATCHTOWER_DATA_KEY:-}" ]; then
  if [ ! -f "$KEYS" ]; then
    umask 077
    python - "$KEYS" <<'PY'
import base64, os, sys
with open(sys.argv[1], "w") as f:
    f.write("HATCHTOWER_SECRET_KEY=%s\n" % base64.urlsafe_b64encode(os.urandom(48)).decode())
    f.write("HATCHTOWER_DATA_KEY=%s\n" % base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
    echo "Hatchtower: created new keys in $KEYS (back them up together with the volume)."
  fi
  set -a; . "$KEYS"; set +a
fi

# Noch kein Benutzer: Einrichtungscode ins Log schreiben
if python -m hatchtower.cli setup-token > /tmp/setup-token 2>/dev/null; then
  echo "----------------------------------------------------------------"
  echo "Setup code (valid for 24 hours): $(sed 's/.*: //' /tmp/setup-token)"
  echo "Setup wizard: http://<server>:${HATCHTOWER_PORT:-8000}/setup"
  echo "----------------------------------------------------------------"
fi
rm -f /tmp/setup-token

exec uvicorn hatchtower.main:app --host 0.0.0.0 --port "${HATCHTOWER_PORT:-8000}" --workers 1 \
  --no-proxy-headers --no-server-header --log-level warning
