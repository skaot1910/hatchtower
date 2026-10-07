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
#      HATCHTOWER_VERSION   bestimmte Version statt der neuesten, z. B. 0.13.0
#      GITHUB_TOKEN         Lese-Token – nur nötig, solange das Repository privat ist
#      GITHUB_TOKEN_FILE    alternativ: Datei, in der der Token steht
#      HATCHTOWER_SAVE_TOKEN=1   Token nach der Installation für spätere Updates speichern
#
#  Das Paket wird vor der Installation mit der veröffentlichten SHA-256-Prüfsumme verglichen.
# =============================================================================
set -euo pipefail

REPO="${HATCHTOWER_REPO:-skaot1910/hatchtower}"
WANT="${HATCHTOWER_VERSION:-}"
TOKEN_STORE=/etc/hatchtower/github-token
API="https://api.github.com/repos/$REPO"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

MODE=install
case "${1:-}" in
  --update) MODE=update; shift;;
  --check)  MODE=check; shift;;
esac

[ "$(id -u)" -eq 0 ] || fail "Bitte als root ausführen."

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
  [ "$MODE" = check ] && fail "Es fehlen Programme:$need"
  say "Benötigte Programme installieren:$need"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  # shellcheck disable=SC2086
  apt-get install -y -qq --no-install-recommends $need >/dev/null 2>&1
fi

WORK="$(mktemp -d /tmp/hatchtower-get.XXXX)"
trap 'rm -rf "$WORK"' EXIT

# --- Release-Angaben von GitHub holen -----------------------------------------------
if [ -n "$WANT" ]; then URL="$API/releases/tags/v${WANT#v}"; else URL="$API/releases/latest"; fi
code=$(curl -sS -o "$WORK/release.json" -w '%{http_code}' -H "Accept: application/vnd.github+json" "${AUTH[@]}" "$URL" || echo 000)
case "$code" in
  200) ;;
  401) fail "Der GitHub-Token wird abgelehnt (abgelaufen oder falsch). Neu setzen mit: hatchtower github-token";;
  404) if [ -z "$TOKEN" ]; then
         fail "Kein Release gefunden. Ist das Repository privat? Dann GITHUB_TOKEN setzen (siehe README)."
       else
         fail "Kein Release gefunden${WANT:+ für Version $WANT} oder der Token hat keinen Lesezugriff auf $REPO."
       fi;;
  000) fail "GitHub ist nicht erreichbar. Internetverbindung und DNS prüfen.";;
  *)   fail "GitHub antwortet mit HTTP $code.";;
esac

eval "$(python3 - "$WORK/release.json" <<'PY'
import json, shlex, sys
r = json.load(open(sys.argv[1]))
version = r["tag_name"].lstrip("v")
assets = {a["name"]: a["url"] for a in r.get("assets", [])}
pkg = f"hatchtower-{version}.tar.gz"
print(f"VERSION={shlex.quote(version)}")
print(f"PKG_URL={shlex.quote(assets.get(pkg, ''))}")
print(f"SUM_URL={shlex.quote(assets.get(pkg + '.sha256', ''))}")
print(f"PKG={shlex.quote(pkg)}")
PY
)"

INSTALLED="$(cat /opt/hatchtower/VERSION 2>/dev/null || echo "")"
newer() {  # ist $1 neuer als $2?
  [ -z "$2" ] && return 0
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}

if [ "$MODE" = check ]; then
  echo "Installiert: ${INSTALLED:-–}   Neueste auf GitHub: $VERSION"
  if newer "$VERSION" "$INSTALLED"; then echo "Update verfügbar:  hatchtower update"; else echo "Hatchtower ist aktuell."; fi
  exit 0
fi

if [ "$MODE" = update ]; then
  [ -n "$INSTALLED" ] || fail "Hatchtower ist hier nicht installiert."
  if [ -z "$WANT" ] && ! newer "$VERSION" "$INSTALLED"; then
    say "Hatchtower $INSTALLED ist aktuell."
    exit 0
  fi
fi

[ -n "$PKG_URL" ] && [ -n "$SUM_URL" ] || fail "Im Release $VERSION fehlen Paket oder Prüfsumme."

# --- Herunterladen und prüfen ------------------------------------------------------
say "Hatchtower $VERSION herunterladen"
curl -fsSL -H "Accept: application/octet-stream" "${AUTH[@]}" "$PKG_URL" -o "$WORK/$PKG" \
  || fail "Download des Pakets fehlgeschlagen."
curl -fsSL -H "Accept: application/octet-stream" "${AUTH[@]}" "$SUM_URL" -o "$WORK/$PKG.sha256" \
  || fail "Download der Prüfsumme fehlgeschlagen."
( cd "$WORK" && sha256sum -c --status "$PKG.sha256" ) || fail "Prüfsumme stimmt nicht – Paket wird nicht installiert."
say "Prüfsumme in Ordnung"

tar -xzf "$WORK/$PKG" -C "$WORK"
DIR="$WORK/hatchtower-$VERSION"
[ -f "$DIR/installer/install.sh" ] || fail "Das Paket enthält kein Hatchtower."

if [ "$MODE" = update ]; then
  echo "Installiert: $INSTALLED  →  neu: $VERSION"
  bash "$DIR/installer/install.sh" --update "$@"
else
  bash "$DIR/installer/install.sh" "$@"
fi

# Token für spätere Updates speichern (nur auf Wunsch, nur für root und den Dienst lesbar)
if [ "${HATCHTOWER_SAVE_TOKEN:-0}" = "1" ] && [ -n "$TOKEN" ] && [ -d /etc/hatchtower ]; then
  umask 077
  printf '%s\n' "$TOKEN" > "$TOKEN_STORE"
  chown root:hatchtower "$TOKEN_STORE"; chmod 640 "$TOKEN_STORE"
  say "GitHub-Token für Updates gespeichert ($TOKEN_STORE)"
fi
