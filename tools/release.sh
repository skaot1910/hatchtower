#!/usr/bin/env bash
# =============================================================================
#  Hatchtower – neue Version auf GitHub veröffentlichen (Git Bash auf dem Entwickler-PC)
#
#  Ablauf:
#    1. Version in app/hatchtower/__init__.py erhöhen und in CHANGELOG.md beschreiben
#    2. Änderungen committen
#    3. bash tools/release.sh          (normales Release, wird allen als Update angeboten)
#       bash tools/release.sh --pre    (Testversion: GitHub-Prerelease, wird nicht automatisch angeboten;
#                                       installieren mit HATCHTOWER_VERSION=<version> hatchtower update)
#
#  Das Skript prüft den Stand, lädt die Commits hoch und wartet, bis die CI (Tests, Lint) für genau
#  diesen Stand grün ist – sonst wird nichts veröffentlicht. Dann setzt es den Tag v<version>,
#  baut das Paket aus genau diesem Tag (git archive, Linux-Zeilenenden, ohne lokale Dateien),
#  berechnet die SHA-256-Prüfsumme und legt das GitHub-Release mit beiden Dateien an.
#  Die Anmeldung kommt aus dem Git Credential Manager – es wird kein Token ausgegeben.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="${HATCHTOWER_REPO:-skaot1910/hatchtower}"
PRE=0
case "${1:-}" in
  --pre) PRE=1;;
  "") ;;
  *) echo "Unbekannte Option: $1 (erlaubt: --pre)"; exit 1;;
esac
say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }
PYBIN="$(command -v python3 || command -v python)" || fail "Python wird gebraucht."
export PYTHONIOENCODING=utf-8   # sonst scheitert Python unter Windows an Zeichen wie „→“
export PYTHONUTF8=1             # Dateien (z. B. die GitHub-Antwort) als UTF-8 lesen, nicht in der Windows-Codepage
# Python unter Windows hängt Wagenrückläufe an – für die Shell entfernen
py() { "$PYBIN" "$@" | tr -d '\r'; }

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/hatchtower/__init__.py)"
[ -n "$VERSION" ] || fail "Version nicht gefunden."
TAG="v$VERSION"
[ "$(tr -d '\r\n' < VERSION)" = "$VERSION" ] || fail "VERSION-Datei passt nicht zu __init__.py ($VERSION)."
# Veröffentlicht wird genau der Stand von main (geht auch aus einer separaten Arbeitskopie/Worktree)
[ "$(git rev-parse HEAD)" = "$(git rev-parse main)" ] || fail "Der ausgecheckte Stand ist nicht main – bitte main veröffentlichen."
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
fi
mkdir -p dist

# --- CI abwarten: veröffentlicht wird nur ein Stand, dessen Prüfungen auf GitHub grün sind ----------
SHA="$(git rev-parse HEAD)"
say "Commits hochladen"
git push -q origin main
say "Warte auf die CI für ${SHA:0:7} (Tests unter Python 3.11 und 3.13, Lint) – dauert ein paar Minuten"
state=waiting
for _ in $(seq 1 90); do          # höchstens 15 Minuten
  gh_api -o dist/checks.json "https://api.github.com/repos/$REPO/commits/$SHA/check-runs?per_page=100" || true
  state="$(py - <<'PY'
import json
try:
    runs = json.load(open("dist/checks.json")).get("check_runs", [])
except (OSError, ValueError):
    runs = []
good = ("success", "skipped", "neutral")
if not runs or any(r["status"] != "completed" for r in runs):
    print("waiting")
elif all(r["conclusion"] in good for r in runs):
    print("ok")
else:
    print("failed " + ", ".join(r["name"] for r in runs if r["conclusion"] not in good))
PY
)"
  case "$state" in
    ok) break;;
    failed*) rm -f dist/checks.json
             fail "Die CI ist rot (${state#failed }) – es wurde nichts veröffentlicht. Details: https://github.com/$REPO/actions";;
  esac
  sleep 10
done
rm -f dist/checks.json
[ "$state" = ok ] || fail "Die CI ist nach 15 Minuten nicht fertig – es wurde nichts veröffentlicht. Später erneut ausführen."
say "CI grün"

if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  say "Tag $TAG setzen"
  git tag -a "$TAG" -m "Hatchtower $VERSION"
fi
git push -q origin "$TAG"

say "Paket bauen"
PKG="hatchtower-$VERSION.tar.gz"
git archive --format=tar --prefix="hatchtower-$VERSION/" "$TAG" | gzip -n -9 > "dist/$PKG"
( cd dist && sha256sum "$PKG" | sed 's/ \*/  /' > "$PKG.sha256" )
echo "    $(cat "dist/$PKG.sha256")"

say "GitHub-Release anlegen"
BODY="$(py -c 'import json,sys; pre = sys.argv[3] == "1"; print(json.dumps({"tag_name": sys.argv[1], "name": "Hatchtower " + sys.argv[2] + (" (Testversion)" if pre else ""), "body": sys.stdin.read(), "draft": False, "prerelease": pre, "make_latest": "false" if pre else "true"}))' "$TAG" "$VERSION" "$PRE" <<< "$NOTES")"
code=$(gh_api -o dist/release.json -w '%{http_code}' -X POST "https://api.github.com/repos/$REPO/releases" -d "$BODY")
if [ "$code" = "422" ]; then
  say "Release gibt es schon – Dateien werden ersetzt"
  code=$(gh_api -o dist/release.json -w '%{http_code}' "https://api.github.com/repos/$REPO/releases/tags/$TAG")
fi
[ "$code" = "200" ] || [ "$code" = "201" ] || fail "GitHub antwortet mit HTTP $code: $(cat dist/release.json)"
read -r _ UPLOAD < <(py -c 'import json; r=json.load(open("dist/release.json")); print(r["id"], r["upload_url"].split("{")[0])')

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
