#!/usr/bin/env bash
# =============================================================================
#  Hatchtower – neue Version auf GitHub veröffentlichen (Git Bash auf dem Entwickler-PC)
#
#  Ablauf:
#    1. Version in app/hatchtower/__init__.py erhöhen und in CHANGELOG.md beschreiben
#    2. Änderungen committen
#    3. bash tools/release.sh
#
#  Das Skript prüft den Stand, setzt den Tag v<version>, lädt Commits und Tag hoch,
#  baut das Paket aus genau diesem Tag (git archive, Linux-Zeilenenden, ohne lokale Dateien),
#  berechnet die SHA-256-Prüfsumme und legt das GitHub-Release mit beiden Dateien an.
#  Die Anmeldung kommt aus dem Git Credential Manager – es wird kein Token ausgegeben.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="${HATCHTOWER_REPO:-skaot1910/hatchtower}"
say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }
PYBIN="$(command -v python3 || command -v python)" || fail "Python wird gebraucht."
# Python unter Windows hängt Wagenrückläufe an – für die Shell entfernen
py() { "$PYBIN" "$@" | tr -d '\r'; }

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/hatchtower/__init__.py)"
[ -n "$VERSION" ] || fail "Version nicht gefunden."
TAG="v$VERSION"
[ "$(tr -d '\r\n' < VERSION)" = "$VERSION" ] || fail "VERSION-Datei passt nicht zu __init__.py ($VERSION)."
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || fail "Bitte auf dem Branch main veröffentlichen."
[ -z "$(git status --porcelain)" ] || fail "Es gibt nicht committete Änderungen. Erst committen."

# Versionshinweise aus CHANGELOG.md (Abschnitt "## <version>")
NOTES="$(py - "$VERSION" <<'PY'
import re, sys
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(r"^## " + re.escape(sys.argv[1]) + r"\b.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
print(m.group(1).strip() if m else "")
PY
)"
[ -n "$NOTES" ] || fail "In CHANGELOG.md fehlt der Abschnitt \"## $VERSION\"."

# Anmeldung aus dem Git Credential Manager (bleibt im Speicher, wird nicht angezeigt)
TOKEN="$(printf 'protocol=https\nhost=github.com\n\n' | git -c credential.interactive=never credential fill 2>/dev/null \
         | sed -n 's/^password=//p')"
[ -n "$TOKEN" ] || fail "Keine GitHub-Anmeldung gefunden. Einmal 'git push' ausführen und im Browser anmelden."
gh_api() { curl -sS -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" "$@"; }

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  [ "$(git rev-list -n1 "$TAG")" = "$(git rev-parse HEAD)" ] || fail "Tag $TAG gibt es schon auf einem anderen Stand."
else
  say "Tag $TAG setzen"
  git tag -a "$TAG" -m "Hatchtower $VERSION"
fi
say "Commits und Tag hochladen"
git push -q origin main
git push -q origin "$TAG"

say "Paket bauen"
mkdir -p dist
PKG="hatchtower-$VERSION.tar.gz"
git archive --format=tar --prefix="hatchtower-$VERSION/" "$TAG" | gzip -n -9 > "dist/$PKG"
( cd dist && sha256sum "$PKG" | sed 's/ \*/  /' > "$PKG.sha256" )
echo "    $(cat "dist/$PKG.sha256")"

say "GitHub-Release anlegen"
BODY="$(py -c 'import json,sys; print(json.dumps({"tag_name": sys.argv[1], "name": "Hatchtower " + sys.argv[2], "body": sys.stdin.read(), "draft": False, "prerelease": False}))' "$TAG" "$VERSION" <<< "$NOTES")"
code=$(gh_api -o dist/release.json -w '%{http_code}' -X POST "https://api.github.com/repos/$REPO/releases" -d "$BODY")
if [ "$code" = "422" ]; then
  say "Release gibt es schon – Dateien werden ersetzt"
  code=$(gh_api -o dist/release.json -w '%{http_code}' "https://api.github.com/repos/$REPO/releases/tags/$TAG")
fi
[ "$code" = "200" ] || [ "$code" = "201" ] || fail "GitHub antwortet mit HTTP $code: $(cat dist/release.json)"
read -r RID UPLOAD < <(py -c 'import json; r=json.load(open("dist/release.json")); print(r["id"], r["upload_url"].split("{")[0])')

# Vorhandene gleichnamige Dateien entfernen, dann hochladen
upload() {
  local file="$1" name; name="$(basename "$1")"
  local old
  old="$(py -c 'import json,sys; r=json.load(open("dist/release.json")); print(next((str(a["id"]) for a in r.get("assets", []) if a["name"]==sys.argv[1]), ""))' "$name")"
  [ -n "$old" ] && gh_api -o /dev/null -X DELETE "https://api.github.com/repos/$REPO/releases/assets/$old"
  code=$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
         --data-binary @"$file" "$UPLOAD?name=$name")
  [ "$code" = "201" ] || fail "Hochladen von $name fehlgeschlagen (HTTP $code)."
  echo "    $name"
}
say "Dateien hochladen"
upload "dist/$PKG"
upload "dist/$PKG.sha256"
rm -f dist/release.json

printf '\n\033[1;32mHatchtower %s ist veröffentlicht.\033[0m\n' "$VERSION"
echo "  https://github.com/$REPO/releases/tag/$TAG"
echo "  Auf dem Server aktualisieren:  hatchtower update"
