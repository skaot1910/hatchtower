#!/usr/bin/env bash
# =============================================================================
#  Hatchtower – Installation und Update
#
#  Neu installieren:   sudo bash install.sh
#  Optionen:           --port 8000            Port der Weboberfläche (Standard 8000)
#                      --bind 0.0.0.0         Adresse, auf der gelauscht wird
#                      --allow "IP1,IP2"      Port nur für diese IPs öffnen (eigene nftables-Tabelle)
#                      --no-start             Dienst nicht starten (für Umzüge)
#  Update:             hatchtower update              (neueste Version von GitHub)
#                      hatchtower update <paket.tar.gz>  (vorhandenes Paket)
#
#  Getestet mit Debian 12 und 13 (auch als Proxmox-LXC).
# =============================================================================
set -euo pipefail

APP_DIR=/opt/hatchtower
ETC_DIR=/etc/hatchtower
DATA_DIR=/var/lib/hatchtower
BACKUP_DIR=/var/backups/hatchtower
ENV_FILE=$ETC_DIR/hatchtower.env
SERVICE=hatchtower
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo unknown)"

PORT=""; BIND=""; ALLOW=""; START=1; UPDATE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --bind) BIND="$2"; shift 2;;
    --allow) ALLOW="$2"; shift 2;;
    --no-start) START=0; shift;;
    --update) UPDATE=1; shift;;
    -h|--help) sed -n '2,15p' "$0"; exit 0;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Please run as root (sudo bash install.sh)."
command -v apt-get >/dev/null || fail "This script needs Debian or Ubuntu (apt)."
[ -f "$SRC/app/hatchtower/main.py" ] || fail "Program files not found. Please run the script from the unpacked package."

FRESH=1
[ -f "$ENV_FILE" ] && FRESH=0
[ "$UPDATE" -eq 1 ] && [ "$FRESH" -eq 1 ] && fail "No existing Hatchtower found. For a new installation, run without --update."

say "Hatchtower $VERSION – $([ "$FRESH" -eq 1 ] && echo installation || echo update)"

# --- Pakete ---------------------------------------------------------------
say "Checking system packages"
export DEBIAN_FRONTEND=noninteractive
NEED="python3 python3-venv sqlite3 ca-certificates curl"
[ -n "$ALLOW" ] && NEED="$NEED nftables"
MISSING=""
for p in $NEED; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
if [ -n "$MISSING" ]; then
  echo "    installing:$MISSING"
  apt-get update -qq >/dev/null
  apt-get install -y -qq --no-install-recommends $MISSING >/dev/null 2>&1 || {
    apt-get install -y --no-install-recommends $MISSING   # bei Fehlern noch einmal mit Ausgabe
  }
fi

# --- Benutzer und Ordner ---------------------------------------------------
id hatchtower >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin hatchtower
install -d -o root  -g root  -m 755 "$APP_DIR"
install -d -o root  -g hatchtower -m 750 "$ETC_DIR"
install -d -o hatchtower -g hatchtower -m 750 "$DATA_DIR" "$DATA_DIR/logos" "$BACKUP_DIR"

# --- Konfiguration (nur bei Neuinstallation erzeugen, nie überschreiben) ----
# Zeitzone des Servers als Standard (in der Oberfläche änderbar)
SYS_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
[ -n "$SYS_TZ" ] || SYS_TZ="$(cat /etc/timezone 2>/dev/null || true)"
[ -n "$SYS_TZ" ] || SYS_TZ="Europe/Berlin"

if [ "$FRESH" -eq 1 ]; then
  say "Creating keys"
  umask 027
  {
    echo "# Hatchtower – created by install.sh. Never share this file!"
    echo "HATCHTOWER_SECRET_KEY=$(head -c48 /dev/urandom | base64 -w0 | tr '+/' '-_')"
    echo "HATCHTOWER_DATA_KEY=$(head -c32 /dev/urandom | base64 -w0 | tr '+/' '-_')"
    echo "HATCHTOWER_DATA_DIR=$DATA_DIR"
    echo "HATCHTOWER_PORT=${PORT:-8000}"
    echo "HATCHTOWER_BIND=${BIND:-0.0.0.0}"
    echo "HATCHTOWER_TZ=$SYS_TZ"
    echo "HATCHTOWER_REPO=${HATCHTOWER_REPO:-skaot1910/hatchtower}"
  } > "$ENV_FILE"
  umask 022
else
  [ -n "$PORT" ] && sed -i "s/^HATCHTOWER_PORT=.*/HATCHTOWER_PORT=$PORT/" "$ENV_FILE"
  [ -n "$BIND" ] && sed -i "s/^HATCHTOWER_BIND=.*/HATCHTOWER_BIND=$BIND/" "$ENV_FILE"
  grep -q '^HATCHTOWER_TZ=' "$ENV_FILE" || echo "HATCHTOWER_TZ=$SYS_TZ" >> "$ENV_FILE"
  grep -q '^HATCHTOWER_REPO=' "$ENV_FILE" || echo "HATCHTOWER_REPO=${HATCHTOWER_REPO:-skaot1910/hatchtower}" >> "$ENV_FILE"
fi
chown root:hatchtower "$ENV_FILE"; chmod 640 "$ENV_FILE"
# shellcheck disable=SC1090
. "$ENV_FILE"

# --- Backup vor dem Update -------------------------------------------------
OLD_VERSION="$(cat "$APP_DIR/VERSION" 2>/dev/null || true)"
BK=""
if [ "$FRESH" -eq 0 ] && [ -f "$DATA_DIR/hatchtower.db" ]; then
  BK="$BACKUP_DIR/vor-update-$(date +%F_%H-%M).db"
  say "Backing up the database: $BK"
  sqlite3 "$DATA_DIR/hatchtower.db" ".backup '$BK'"
  chown hatchtower:hatchtower "$BK"
fi

# --- Programm einspielen ---------------------------------------------------
say "Installing the program"
rm -rf "$APP_DIR/app.new" "$APP_DIR/installer.new"
cp -r "$SRC/app" "$APP_DIR/app.new"
cp -r "$SRC/installer" "$APP_DIR/installer.new"
[ -d "$SRC/tools" ] && { rm -rf "$APP_DIR/tools"; cp -r "$SRC/tools" "$APP_DIR/tools"; chmod -R go-w "$APP_DIR/tools"; }
chown -R root:root "$APP_DIR/app.new" "$APP_DIR/installer.new"
chmod -R go-w "$APP_DIR/app.new" "$APP_DIR/installer.new"
rm -rf "$APP_DIR/app.old"
[ -d "$APP_DIR/app" ] && mv "$APP_DIR/app" "$APP_DIR/app.old"
mv "$APP_DIR/app.new" "$APP_DIR/app"
rm -rf "$APP_DIR/installer"; mv "$APP_DIR/installer.new" "$APP_DIR/installer"
echo "$VERSION" > "$APP_DIR/VERSION"
# Lizenztext von Hatchtower (AGPL-3.0) mitliefern
[ -f "$SRC/LICENSE" ] && install -m 644 "$SRC/LICENSE" "$APP_DIR/LICENSE"

say "Setting up the Python environment"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/app/requirements.txt"
# Lizenztexte der verwendeten Pakete ablegen (Hinweispflicht der Open-Source-Lizenzen)
if [ -f "$APP_DIR/tools/third_party_licenses.py" ]; then
  "$APP_DIR/venv/bin/python" "$APP_DIR/tools/third_party_licenses.py" > "$APP_DIR/THIRD_PARTY_LICENSES.txt" || true
fi

install -m 755 "$APP_DIR/installer/hatchtower" /usr/local/bin/hatchtower
ln -sf /usr/local/bin/hatchtower /usr/sbin/hatchtower

# --- Dienste ---------------------------------------------------------------
install -m 644 "$APP_DIR/installer/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
install -m 644 "$APP_DIR/installer/$SERVICE-backup.service" "/etc/systemd/system/$SERVICE-backup.service"
install -m 644 "$APP_DIR/installer/$SERVICE-backup.timer" "/etc/systemd/system/$SERVICE-backup.timer"
install -m 644 "$APP_DIR/installer/$SERVICE-update.service" "/etc/systemd/system/$SERVICE-update.service"
install -m 644 "$APP_DIR/installer/$SERVICE-update.path" "/etc/systemd/system/$SERVICE-update.path"

# --- Optional: Port auf bestimmte IPs beschränken (eigene Tabelle, bestehende Firewall bleibt unberührt)
if [ -n "$ALLOW" ]; then
  say "Allowing port $HATCHTOWER_PORT only for $ALLOW"
  ALLOW_SET="$(echo "$ALLOW" | tr -d ' ')"
  cat > "$ETC_DIR/firewall.nft" <<EOF
table inet hatchtower
delete table inet hatchtower
table inet hatchtower {
  chain input {
    type filter hook input priority -10; policy accept;
    iif "lo" accept
    meta nfproto ipv4 tcp dport $HATCHTOWER_PORT ip saddr != { $ALLOW_SET } drop
    meta nfproto ipv6 tcp dport $HATCHTOWER_PORT drop
  }
}
EOF
  install -m 644 "$APP_DIR/installer/$SERVICE-firewall.service" "/etc/systemd/system/$SERVICE-firewall.service"
  systemctl daemon-reload
  systemctl enable --now "$SERVICE-firewall.service" >/dev/null
  systemctl restart "$SERVICE-firewall.service"
fi

systemctl daemon-reload
systemctl enable "$SERVICE.service" "$SERVICE-backup.timer" "$SERVICE-update.path" >/dev/null
systemctl start "$SERVICE-backup.timer" "$SERVICE-update.path"

if [ "$START" -eq 0 ]; then
  say "Done (service not started, --no-start)."
  exit 0
fi

say "Starting the service"
systemctl restart "$SERVICE.service"
ok=0
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HATCHTOWER_PORT/login" || true)
  if [ "$code" = "200" ] || [ "$code" = "303" ]; then ok=1; break; fi
  sleep 1
done

if [ "$ok" -ne 1 ]; then
  if [ "$FRESH" -eq 0 ] && [ -d "$APP_DIR/app.old" ]; then
    say "The new version does not start – going back to the previous version"
    systemctl stop "$SERVICE.service" || true
    rm -rf "$APP_DIR/app.failed"; mv "$APP_DIR/app" "$APP_DIR/app.failed"; mv "$APP_DIR/app.old" "$APP_DIR/app"
    # Versionsnummer zurück, sonst hält "hatchtower update" die fehlgeschlagene Version für installiert
    [ -n "$OLD_VERSION" ] && echo "$OLD_VERSION" > "$APP_DIR/VERSION"
    # Datenbank auf den Stand von vor dem Update (die neue Version könnte sie schon umgebaut haben)
    if [ -n "$BK" ] && [ -f "$BK" ]; then
      cp -p "$DATA_DIR/hatchtower.db" "$BACKUP_DIR/nach-fehlgeschlagenem-update-$(date +%F_%H-%M).db" 2>/dev/null || true
      if sqlite3 "$DATA_DIR/hatchtower.db" ".restore '$BK'"; then
        chown hatchtower:hatchtower "$DATA_DIR/hatchtower.db"
        echo "    Database restored to its state before the update ($BK)"
      fi
    fi
    systemctl restart "$SERVICE.service"
  fi
  journalctl -u "$SERVICE" -n 30 --no-pager || true
  fail "Hatchtower does not start. See the log above."
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
LOCAL_ONLY=0
case "$HATCHTOWER_BIND" in
  0.0.0.0|::|"") ;;
  127.*|::1|localhost) LOCAL_ONLY=1; IP="127.0.0.1";;
  *) IP="$HATCHTOWER_BIND";;
esac
echo
if hatchtower setup-token >/tmp/hatchtower-token 2>/dev/null; then
  TOKEN="$(sed 's/.*: //' /tmp/hatchtower-token)"; rm -f /tmp/hatchtower-token
  printf '\033[1;32mHatchtower %s is installed.\033[0m\n\n' "$VERSION"
  echo "  1. Open in your browser:  http://${IP:-SERVER-IP}:$HATCHTOWER_PORT/setup"
  echo "  2. Setup code:            $TOKEN   (valid for 24 hours, new one: hatchtower setup-token)"
  echo
  echo "  The setup wizard guides you through the panel address, API keys and Wings."
  if [ "$LOCAL_ONLY" -eq 1 ]; then
    echo
    echo "  Hatchtower only listens on $HATCHTOWER_BIND. Open it through your reverse proxy (HTTPS)"
    echo "  and then enter the proxy IP under System → Network (for a proxy on this machine: 127.0.0.1)."
  fi
else
  rm -f /tmp/hatchtower-token
  printf '\033[1;32mHatchtower %s is running.\033[0m  http://%s:%s\n' "$VERSION" "${IP:-SERVER-IP}" "$HATCHTOWER_PORT"
fi
echo
