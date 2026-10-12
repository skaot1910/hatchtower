#!/usr/bin/env bash
# Hatchtower – Rauchtest mit eigener Test-Datenbank auf Port 8001.
# Berührt die echte Datenbank nicht. Gegen Pterodactyl wird nur gelesen.
set -uo pipefail
[ -f /etc/hatchtower/hatchtower.env ] || { echo "Hatchtower ist hier nicht installiert"; exit 1; }

T=$(mktemp -d /tmp/hatchtower-smoke.XXXX)
chown hatchtower:hatchtower "$T"
set -a; . /etc/hatchtower/hatchtower.env; set +a
# Rauchtest immer read-only: es wird nie etwas am Livesystem ausgelöst
export HATCHTOWER_DATA_DIR="$T" HOME="$T"
cd /opt/hatchtower/app
PY=/opt/hatchtower/venv/bin/python
PW="Smoke-Test-$(head -c9 /dev/urandom | base64 | tr -dc A-Za-z0-9)1!"
U="smoke_owner"

setpriv --reuid=hatchtower --regid=hatchtower --init-groups $PY - "$U" "$PW" <<'EOF'
import os, sqlite3, sys
from pathlib import Path
from hatchtower import db, security
db.init(Path(os.environ["HATCHTOWER_DATA_DIR"]) / "hatchtower.db")
live = "/var/lib/hatchtower/hatchtower.db"
with db.connect() as con:
    # Verbindung der echten Instanz übernehmen (verschlüsselt mit demselben Schlüssel), aber immer nur lesend
    if os.path.exists(live):
        src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        for key, value in src.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%' OR key = 'public_host'"):
            con.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    con.execute("INSERT INTO app_settings (key, value) VALUES ('conn_mode', 'read-only') ON CONFLICT(key) DO UPDATE SET value = 'read-only'")
    con.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'owner', ?)",
                (sys.argv[1], security.hash_password(sys.argv[2]), db.utcnow()))
    con.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('smoke_user', ?, 'user', ?)",
                (security.hash_password(sys.argv[2]), db.utcnow()))
EOF
setpriv --reuid=hatchtower --regid=hatchtower --init-groups /opt/hatchtower/venv/bin/uvicorn hatchtower.main:app \
  --host 127.0.0.1 --port 8001 --log-level warning >"$T/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:8001/login && break; sleep 0.5; done
sleep 8   # Monitoring einmal laufen lassen

B=http://127.0.0.1:8001
J="$T/jar"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "  ✓ $1"; pass=$((pass+1)); else echo "  ✕ $1 (erwartet $3, bekommen $2)"; fail=$((fail+1)); fi; }
code() { curl -s -o /dev/null -w '%{http_code}' -b "$J" -c "$J" "$@"; }
csrf_of() { curl -s -b "$1" $B/account | grep -o 'name="csrf" value="[^"]*"' | head -1 | sed 's/.*value="//;s/"//'; }
SQL() { setpriv --reuid=hatchtower --regid=hatchtower --init-groups $PY -c "import sqlite3,sys;print(sqlite3.connect('$T/hatchtower.db').execute(sys.argv[1]).fetchone()[0])" "$1"; }

check "Ohne Login -> Weiterleitung" "$(code $B/)" 303
check "Login-Seite" "$(code $B/login)" 200
check "Login ohne Formular-Token abgelehnt" "$(code -X POST -d "username=$U&password=x&prelogin=bad" $B/login)" 400
PRE=$(grep hatchtower_prelogin "$J" | awk '{print $7}')
check "Falsches Passwort abgelehnt" "$(code -X POST --data-urlencode "username=$U" --data-urlencode "password=falsch" --data-urlencode "prelogin=$PRE" $B/login)" 400
PRE=$(grep hatchtower_prelogin "$J" | awk '{print $7}')
check "Login als Owner" "$(code -X POST --data-urlencode "username=$U" --data-urlencode "password=$PW" --data-urlencode "prelogin=$PRE" $B/login)" 303
check "Übersicht" "$(code $B/)" 200
check "API Übersicht" "$(code $B/api/overview)" 200
SERVERS=$(curl -s -b "$J" $B/api/overview | $PY -c 'import sys,json; d=json.load(sys.stdin); print(len(d["servers"]))')
echo "    Server sichtbar: $SERVERS"
echo "    Adressen: $(curl -s -b "$J" $B/api/overview | $PY -c 'import sys,json; print(", ".join(s["name"]+"="+s["address"]+"/"+s["state"] for s in json.load(sys.stdin)["servers"][:3]))')"
IDENT=$(curl -s -b "$J" $B/api/overview | $PY -c 'import sys,json; d=json.load(sys.stdin); print(d["servers"][0]["identifier"] if d["servers"] else "")')
if [ -n "$IDENT" ]; then
  check "Serverdetails" "$(code $B/servers/$IDENT)" 200
  check "Verlauf-API (1 h)" "$(code "$B/api/servers/$IDENT/metrics?range=3600")" 200
  echo "    Verlauf: $(curl -s -b "$J" "$B/api/servers/$IDENT/metrics?range=120" | $PY -c 'import sys,json; d=json.load(sys.stdin); print(len(d["points"]), "Punkte, CPU-Limit", d["cpu_limit"], "Bucket", d["bucket"])')"
  check "Verwalten-Seite" "$(code $B/servers/$IDENT/settings)" 200
  check "Dateiliste" "$(code "$B/servers/$IDENT/files?dir=/")" 200
  check "Pfad-Ausbruch wird normalisiert" "$(code "$B/servers/$IDENT/files?dir=/../../etc")" 200
fi

CSRF=$(csrf_of "$J")
check "Automationen-Seite" "$(code $B/admin/automation)" 200
if [ -n "$IDENT" ]; then
  check "Power ohne CSRF-Header abgelehnt" "$(code -X POST -H 'Content-Type: application/json' -d '{"signal":"restart"}' $B/api/servers/$IDENT/power)" 403
  check "Power im Read-Only-Modus blockiert" "$(code -X POST -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{"signal":"restart"}' $B/api/servers/$IDENT/power)" 403
  check "Befehl im Read-Only-Modus blockiert" "$(code -X POST -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{"command":"help"}' $B/api/servers/$IDENT/command)" 403
  check "Ordner anlegen im Read-Only-Modus blockiert" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'dir=/&name=hatchtower-test' $B/servers/$IDENT/files/mkdir)" 403
  check "Unbekanntes Signal abgelehnt" "$(code -X POST -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{"signal":"explode"}' $B/api/servers/$IDENT/power)" 400
fi
check "Zeitplan mit Fehlern abgelehnt (Weiterleitung mit Hinweis)" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'name=&action=x' $B/admin/automation/schedules)" 303
check "Benutzerverwaltung" "$(code $B/admin/users)" 200
check "Audit-Log" "$(code $B/admin/audit)" 200
check "System" "$(code $B/admin/system)" 200
# Einrichtung, Verbindung, Themes
check "Verbindungsseite" "$(code $B/admin/connection)" 200
check "  ... alle Formulare mit Sicherheitstoken" "$(curl -s -b "$J" $B/admin/connection | grep -c 'name="csrf" value=""')" 0
check "Assistent: Formulare mit Sicherheitstoken" "$(curl -s -b "$J" "$B/setup?step=panel" | grep -c 'name="csrf" value=""')" 0
check "Assistent für Owner erreichbar" "$(code "$B/setup?step=options")" 200
check "Owner-Schritt gesperrt, wenn es schon Benutzer gibt" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d 'token=X&username=x&password=x&repeat=x' $B/setup/owner)" 303
check "Verbindung: Client-Key mit falschem Format abgelehnt" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'client_key=ptlc_kurz&next=/admin/connection' $B/admin/connection/client)" 303
check "  ... und Key unverändert (Verbindung läuft weiter)" "$(code $B/api/overview)" 200
check "Theme umschalten (Kopfzeile)" "$(code "$B/theme/hell?next=/account")" 303
check "  ... wird gespeichert" "$(SQL "select theme from users where username='$U'")" hell
check "  ... und ausgeliefert" "$(curl -s -b "$J" $B/account | grep -c 'data-theme="hell"')" 1
code "$B/theme/hack" >/dev/null
check "Unbekanntes Theme abgelehnt" "$(SQL "select theme from users where username='$U'")" hell
check "Umschalter mit 3 Themes in der Kopfzeile" "$(curl -s -b "$J" $B/ | grep -o 'href="/theme/' | wc -l)" 3
code "$B/theme/nacht" >/dev/null
# Sprache: Browser, Umschalter, Konto
check "Login auf Englisch bei englischem Browser" "$(curl -s -H 'Accept-Language: en-US,en;q=0.8' $B/login | grep -c '<html lang="en"')" 1
check "Login auf Deutsch ohne Browsersprache (Standard)" "$(curl -s $B/login | grep -c '<html lang="de"')" 1
check "Sprache umschalten" "$(code "$B/lang/en?next=/account")" 303
check "  ... im Konto gespeichert" "$(SQL "select language from users where username='$U'")" en
check "  ... Seite auf Englisch" "$(curl -s -b "$J" $B/account | grep -c '<h1>Your account</h1>')" 1
check "  ... Weiterleitung nur auf eigene Seiten" "$(curl -s -o /dev/null -w '%{redirect_url}' -b "$J" -c "$J" "$B/lang/de?next=//evil.example")" "$B/"
check "  ... zurück auf Deutsch" "$(SQL "select language from users where username='$U'")" de
# 0.11: neue Seiten, Aufbewahrung, Statusseite, Rechtliches, Export
for p in /admin/appearance /admin/network /admin/mail /admin/notify /admin/statuspage /admin/announcements /admin/legal /admin/data /admin/updates /about; do
  check "Seite $p" "$(code $B$p)" 200
done
check "Statusseite ist standardmäßig aus" "$(curl -s -o /dev/null -w '%{http_code}' $B/status)" 404
check "Impressum ist standardmäßig aus" "$(curl -s -o /dev/null -w '%{http_code}' $B/impressum)" 404
check "Audit-Aufbewahrung Standard 30 Tage" "$(SQL "select value from app_settings where key='audit_retention_days'")" 30
check "Frische Audit-Einträge nicht löschbar" "$(setpriv --reuid=hatchtower --regid=hatchtower --init-groups $PY -c "import sqlite3
try:
    c = sqlite3.connect('$T/hatchtower.db'); c.execute('DELETE FROM audit_log'); c.commit(); print('geloescht')
except sqlite3.DatabaseError: print('geschuetzt')")" geschuetzt
check "Audit-Export CSV" "$(code "$B/admin/audit/export?format=csv")" 200
check "Export ohne Passwort abgelehnt (Weiterleitung)" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'current=falsch&passphrase=x&repeat=x' $B/admin/data/export)" 303
check "Passwort vergessen ohne E-Mail-Versand aus" "$(curl -s -o /dev/null -w '%{http_code}' $B/forgot)" 404
check "Zu große Anfrage abgelehnt (413)" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Length: 99999999' --data-binary 'x' --max-time 5 $B/login)" 413
check "Chunked ohne Länge abgelehnt (411)" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Transfer-Encoding: chunked' --data-binary 'x' $B/login)" 411
check "Einrichtungs-Import nach Einrichtung sofort gesperrt" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -F 'file=@/etc/hostname' $B/setup/import)" 303
check "Version ohne Anmeldung verborgen" "$(curl -s $B/login | grep -c "Hatchtower</strong> [0-9]")" 0
check "Lizenztexte für Angemeldete" "$(code $B/about/licenses.txt)" 200
check "Lizenzseite nur angemeldet" "$(curl -s -o /dev/null -w '%{http_code}' $B/about)" 303
check "Sicherheits-Header COOP" "$(curl -s -o /dev/null -D - $B/login | grep -ci '^cross-origin-opener-policy: same-origin')" 1
check "Einladung erstellen" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'role=user' $B/admin/users/invite)" 200
check "POST ohne CSRF abgelehnt" "$(code -X POST -d 'ingame_name=x' $B/servers/${IDENT:-x}/settings)" 403
check "Schwaches Passwort für neuen Benutzer abgelehnt" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'username=weak&password=kurz&role=user' $B/admin/users)" 303
check "  ... und nicht angelegt" "$(SQL "select count(*) from users where username='weak'")" 0

# ZIP-Download (nur lesend): die zwei kleinsten Dateien im Hauptordner des ersten Servers
ZIPNAMES=$(curl -s -b "$J" "$B/servers/$IDENT/files?dir=/" | $PY -c '
import re, sys, html, urllib.parse
units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
rows = re.findall(r"<tr class=\"is-file\">.*?value=\"([^\"]+)\".*?col-size[^>]*>([^<]+)<", sys.stdin.read(), re.S)
def size(t):
    num, unit = t.strip().replace(",", ".").split()
    return float(num) * units.get(unit, 1)
small = sorted(rows, key=lambda r: size(r[1]))[:2]
print("&".join("names=" + urllib.parse.quote(html.unescape(n)) for n, _ in small))')
check "ZIP-Download" "$(curl -s -o "$T/test.zip" -w '%{http_code}' -b "$J" -X POST --data-urlencode "csrf=$CSRF" -d "dir=/&$ZIPNAMES" $B/servers/$IDENT/files/zip)" 200
echo "    ZIP-Inhalt: $($PY -c "import zipfile; z=zipfile.ZipFile('$T/test.zip'); print(z.testzip() or 'gültig', '|', ', '.join(i.filename for i in z.infolist()[:8]))")"
check "ZIP ohne Auswahl abgelehnt" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'dir=/' $B/servers/$IDENT/files/zip)" 303

# Normaler Benutzer ohne Rechte sieht nichts
J2="$T/jar2"
curl -s -o /dev/null -c "$J2" $B/login
PRE2=$(grep hatchtower_prelogin "$J2" | awk '{print $7}')
curl -s -o /dev/null -b "$J2" -c "$J2" -X POST --data-urlencode "username=smoke_user" --data-urlencode "password=$PW" --data-urlencode "prelogin=$PRE2" $B/login
N=$(curl -s -b "$J2" $B/api/overview | $PY -c 'import sys,json; print(len(json.load(sys.stdin)["servers"]))')
check "Benutzer ohne Rechte sieht 0 Server" "$N" 0
check "Benutzer ohne Rechte: Server direkt aufrufen verboten" "$(curl -s -o /dev/null -w '%{http_code}' -b "$J2" $B/servers/${IDENT:-x})" 403
CSRF2=$(csrf_of "$J2")
check "Benutzer ohne Rechte: Power verboten" "$(curl -s -o /dev/null -w '%{http_code}' -b "$J2" -X POST -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF2" -d '{"signal":"start"}' $B/api/servers/${IDENT:-x}/power)" 403
check "Benutzer ohne Rechte: ZIP verboten" "$(curl -s -o /dev/null -w '%{http_code}' -b "$J2" -X POST --data-urlencode "csrf=$CSRF2" -d "dir=/&$ZIPNAMES" $B/servers/$IDENT/files/zip)" 403
check "Benutzer ohne Rechte: Automationen verboten" "$(curl -s -o /dev/null -w '%{http_code}' -b "$J2" $B/admin/automation)" 403
check "Benutzer ohne Rechte: Admin verboten" "$(curl -s -o /dev/null -w '%{http_code}' -b "$J2" $B/admin/users)" 403

# Rechtegruppen: anlegen, zuweisen, wirken lassen
check "Rechtegruppen-Seite" "$(code $B/admin/groups)" 200
check "Gruppe anlegen" "$(code -X POST --data-urlencode "csrf=$CSRF" -d 'name=Testgruppe&perm__power.restart=1' $B/admin/groups)" 303
GID=$(SQL "select id from perm_groups where name='Testgruppe'")
check "Gruppe ergänzt Abhängigkeit (Restart + Sehen)" "$(SQL "select count(*) from perm_group_items where group_id=$GID")" 2
UID2=$(SQL "select id from users where username='smoke_user'")
check "Gruppe zuweisen" "$(code -X POST --data-urlencode "csrf=$CSRF" -d "mode__$IDENT=g:$GID" $B/admin/users/$UID2/permissions)" 303
check "Benutzerseite mit Zuweisung" "$(code $B/admin/users/$UID2)" 200
check "Benutzerliste mit Zuweisung" "$(code $B/admin/users)" 200
N=$(curl -s -b "$J2" $B/api/overview | $PY -c 'import sys,json; print(len(json.load(sys.stdin)["servers"]))')
check "Benutzer mit Gruppe sieht genau 1 Server" "$N" 1
check "Individuelle Rechte speichern" "$(code -X POST --data-urlencode "csrf=$CSRF" -d "mode__$IDENT=custom&perm__${IDENT}__console.read=1" $B/admin/users/$UID2/permissions)" 303
check "  ... mit Abhängigkeit gespeichert" "$(SQL "select count(*) from user_server_permissions where user_id=$UID2")" 2
check "Gruppe löschen" "$(code -X POST --data-urlencode "csrf=$CSRF" $B/admin/groups/$GID/delete)" 303

echo "  Audit-Einträge: $(SQL 'select count(*) from audit_log')"
echo "  Audit nicht löschbar: $(setpriv --reuid=hatchtower --regid=hatchtower --init-groups $PY -c "
import sqlite3
c=sqlite3.connect('$T/hatchtower.db')
try:
    c.execute('delete from audit_log'); print('NEIN')
except Exception as e: print('ja (' + str(e) + ')')")"
echo "  Server-Log (Fehler):"; grep -E 'ERROR|Traceback' "$T/server.log" | head -20 || true
echo "Ergebnis: $pass bestanden, $fail fehlgeschlagen"
[ "$fail" -eq 0 ]
