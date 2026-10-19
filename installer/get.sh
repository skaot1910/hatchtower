#!/usr/bin/env bash
# =============================================================================
#  Hatchtower – neueste Version von GitHub laden und installieren
#
#  Neu installieren (Debian/Ubuntu, auch im Proxmox-LXC):
#      bash get.sh [Optionen von install.sh, z. B. --allow "192.168.1.5"]
#  Aktualisieren (macht "hatchtower update" ohne Paketangabe):
#      bash get.sh --update
#  Nur nachsehen, ob es eine neue Version gibt:
#      bash get.sh --check
#
#  Umgebungsvariablen:
#      HATCHTOWER_REPO      GitHub-Repository (Standard: skaot1910/hatchtower)
#      HATCHTOWER_VERSION   bestimmte Version statt der neuesten, z. B. 0.13.0 oder 0.38.0-pre.1
#      HATCHTOWER_PRERELEASES=1|0  also consider pre-releases (default for updates: the switch under
#                           System → Updates; for a new installation: no)
#      GITHUB_TOKEN         Lese-Token – nur für ein privates Repository (z. B. eigener Fork) nötig
#      GITHUB_TOKEN_FILE    alternativ: Datei, in der der Token steht
#      HATCHTOWER_SAVE_TOKEN=1   Token nach der Installation für spätere Updates speichern
#
#  Das Paket wird vor der Installation mit der veröffentlichten SHA-256-Prüfsumme verglichen.
# =============================================================================
set -euo pipefail

REPO="${HATCHTOWER_REPO:-skaot1910/hatchtower}"
WANT="${HATCHTOWER_VERSION:-}"
PRE="${HATCHTOWER_PRERELEASES:-}"
TOKEN_STORE=/etc/hatchtower/github-token
DB=/var/lib/hatchtower/hatchtower.db
API="https://api.github.com/repos/$REPO"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

MODE=install
case "${1:-}" in
  --update) MODE=update; shift;;
  --check)  MODE=check; shift;;
esac

[ "$(id -u)" -eq 0 ] || fail "Please run as root."

# Token: Umgebung, Datei oder (für Updates) der gespeicherte Token des Servers
TOKEN="${GITHUB_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -n "${GITHUB_TOKEN_FILE:-}" ] && [ -r "$GITHUB_TOKEN_FILE" ]; then
  TOKEN="$(tr -d ' \r\n' < "$GITHUB_TOKEN_FILE")"
fi
if [ -z "$TOKEN" ] && [ -r "$TOKEN_STORE" ]; then
  TOKEN="$(tr -d ' \r\n' < "$TOKEN_STORE")"
fi
AUTH=()
[ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")

# Werkzeuge, die dieses Skript selbst braucht
need=""
command -v curl >/dev/null || need="$need curl ca-certificates"
command -v python3 >/dev/null || need="$need python3"
command -v sha256sum >/dev/null || need="$need coreutils"
if [ -n "$need" ]; then
  [ "$MODE" = check ] && fail "Missing programs:$need"
  say "Installing required programs:$need"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  # shellcheck disable=SC2086
  apt-get install -y -qq --no-install-recommends $need >/dev/null 2>&1
fi

WORK="$(mktemp -d /tmp/hatchtower-get.XXXX)"
trap 'rm -rf "$WORK"' EXIT

# Pre-releases only when switched on in the web interface (System → Updates), unless overridden
if [ -z "$PRE" ] && [ "$MODE" != install ] && [ -r "$DB" ]; then
  PRE="$(python3 - "$DB" 2>/dev/null <<'PY' || true
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = con.execute("SELECT value FROM app_settings WHERE key = 'update_prereleases'").fetchone()
print(row[0] if row and row[0] else "0")
PY
)"
fi
[ "$PRE" = 1 ] || PRE=0

# Version order as in the web interface: 0.38.0-pre.1 < 0.38.0-rc.1 < 0.38.0 < 0.38.1
cat > "$WORK/version.py" <<'PY'
import re
PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?$")
def key(v):
    m = PATTERN.match((v or "").strip())
    if not m:
        return ((0, 0, 0), 0, ())
    nums = tuple(int(p) for p in m.group(1).split("."))
    nums += (0,) * (3 - len(nums))
    if not m.group(2):
        return (nums, 1, ())
    return (nums, 0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(2).split(".")))
PY

# --- Release-Angaben von GitHub holen -----------------------------------------------
if [ -n "$WANT" ]; then URL="$API/releases/tags/v${WANT#v}"
elif [ "$PRE" = 1 ]; then URL="$API/releases?per_page=30"   # newest of all releases, pre-releases included
else URL="$API/releases/latest"; fi
code=$(curl -sS -o "$WORK/release.json" -w '%{http_code}' -H "Accept: application/vnd.github+json" "${AUTH[@]}" "$URL" || echo 000)
case "$code" in
  200) ;;
  401) fail "The GitHub token was rejected (expired or wrong). Set a new one with: hatchtower github-token";;
  404) if [ -z "$TOKEN" ]; then
         fail "No release found for $REPO. (For a private repository, set GITHUB_TOKEN.)"
       else
         fail "No release found${WANT:+ for version $WANT}, or the token has no read access to $REPO."
       fi;;
  000) fail "GitHub cannot be reached. Check the internet connection and DNS.";;
  *)   fail "GitHub answered with HTTP $code.";;
esac

eval "$(python3 - "$WORK/release.json" "$WORK" <<'PY'
import json, shlex, sys
sys.path.insert(0, sys.argv[2])
from version import PATTERN, key
r = json.load(open(sys.argv[1]))
if isinstance(r, list):
    found = [x for x in r if not x.get("draft") and PATTERN.match(str(x.get("tag_name", "")))]
    if not found:
        print("fail 'No release found.'")
        sys.exit()
    r = max(found, key=lambda x: key(x["tag_name"]))
version = r["tag_name"].lstrip("v")
assets = {a["name"]: a["url"] for a in r.get("assets", [])}
pkg = f"hatchtower-{version}.tar.gz"
print(f"VERSION={shlex.quote(version)}")
print(f"PKG_URL={shlex.quote(assets.get(pkg, ''))}")
print(f"SUM_URL={shlex.quote(assets.get(pkg + '.sha256', ''))}")
print(f"PKG={shlex.quote(pkg)}")
print(f"IS_PRE={1 if r.get('prerelease') or '-' in version else 0}")
PY
)"

INSTALLED="$(cat /opt/hatchtower/VERSION 2>/dev/null || echo "")"
newer() {  # ist $1 neuer als $2?
  [ -z "$2" ] && return 0
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from version import key; sys.exit(key(sys.argv[2]) <= key(sys.argv[3]))' \
    "$WORK" "$1" "$2"
}
LABEL="$VERSION"; [ "$IS_PRE" = 1 ] && LABEL="$VERSION (pre-release)"

if [ "$MODE" = check ]; then
  echo "Installed: ${INSTALLED:-–}   Newest on GitHub: $LABEL"
  [ "$PRE" = 1 ] && echo "Pre-releases are included (switch under System → Updates)."
  if newer "$VERSION" "$INSTALLED"; then echo "Update available:  hatchtower update"; else echo "Hatchtower is up to date."; fi
  exit 0
fi

if [ "$MODE" = update ]; then
  [ -n "$INSTALLED" ] || fail "Hatchtower is not installed here."
  if [ -z "$WANT" ] && ! newer "$VERSION" "$INSTALLED"; then
    say "Hatchtower $INSTALLED is up to date."
    exit 0
  fi
fi

[ -n "$PKG_URL" ] && [ -n "$SUM_URL" ] || fail "Release $VERSION is missing the package or the checksum."

# --- Herunterladen und prüfen ------------------------------------------------------
say "Downloading Hatchtower $LABEL"
curl -fsSL -H "Accept: application/octet-stream" "${AUTH[@]}" "$PKG_URL" -o "$WORK/$PKG" \
  || fail "Downloading the package failed."
curl -fsSL -H "Accept: application/octet-stream" "${AUTH[@]}" "$SUM_URL" -o "$WORK/$PKG.sha256" \
  || fail "Downloading the checksum failed."
( cd "$WORK" && sha256sum -c --status "$PKG.sha256" ) || fail "Checksum does not match – the package is not installed."
say "Checksum OK"

tar -xzf "$WORK/$PKG" -C "$WORK"
DIR="$WORK/hatchtower-$VERSION"
[ -f "$DIR/installer/install.sh" ] || fail "The package does not contain Hatchtower."

if [ "$MODE" = update ]; then
  echo "Installed: $INSTALLED  →  new: $LABEL"
  bash "$DIR/installer/install.sh" --update "$@"
else
  bash "$DIR/installer/install.sh" "$@"
fi

# Token für spätere Updates speichern (nur auf Wunsch, nur für root und den Dienst lesbar)
if [ "${HATCHTOWER_SAVE_TOKEN:-0}" = "1" ] && [ -n "$TOKEN" ] && [ -d /etc/hatchtower ]; then
  umask 077
  printf '%s\n' "$TOKEN" > "$TOKEN_STORE"
  chown root:hatchtower "$TOKEN_STORE"; chmod 640 "$TOKEN_STORE"
  say "GitHub token stored for updates ($TOKEN_STORE)"
fi
