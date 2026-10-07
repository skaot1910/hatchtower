# Hatchtower

**Management und Monitoring für Pterodactyl-Game-Server.**
Hatchtower ist ein Begleiter für dein Pterodactyl-Panel, kein Ersatz: Pterodactyl bleibt die
Autorität für Server und Status. Hatchtower legt eine eigene Oberfläche mit eigenen Benutzern,
fein steuerbaren Rechten, Live-Monitoring, Automationen und Audit-Log darüber.

## Funktionen

- **Übersicht** als Liste oder Kacheln, Statusfilter, Sortierung, Live-Werte (CPU bezogen aufs Limit, RAM, Speicher)
- **Steuerung:** Start, Stop, Restart, Kill (nur Owner), Konsolenbefehle
- **Live-Konsole** und **Verlauf** von CPU, RAM und Netzwerk (live bis 72 Stunden)
- **Dateien:** ansehen, herunterladen (auch mehrere als ZIP), hochladen, bearbeiten, umbenennen, löschen
- **Eigene Benutzer und Rechte** pro Server, mit selbst erstellten **Rechtegruppen**
- **Automationen:** Neustart bei Absturz, Zeitpläne mit Vorwarnung
- **Audit-Log**, das sich nicht verändern lässt
- **3 Themes** (Dunkel, Mittel, Hell), oben rechts in der Kopfzeile umschaltbar und pro Benutzer gespeichert; eigener Name und eigene Logos
- **Deutsch und Englisch**: Umschalter DE/EN oben rechts (auch auf der Login-Seite), Wahl wird pro Benutzer gespeichert; ohne Wahl gilt die Browsersprache, sonst die Standardsprache unter System → Erscheinungsbild
- **Handy und Tablet** werden automatisch erkannt
- **Read-Only-Modus:** Hatchtower kann komplett nur lesend betrieben werden
- **Favoriten und Tags:** Server oben anheften und nach eigenen Tags filtern; mehrere Server auf einmal starten, stoppen oder neu starten
- **Verfügbarkeit:** Online-Zeit und Abstürze der letzten 30 Tage pro Server
- **Log-Archiv:** Hatchtower schreibt die Konsole laufender Server mit (1–90 Tage oder aus), durchsuchbar, Fehler farbig, Download als .txt
- **Datei-Sicherung:** Beim Bearbeiten bleibt die vorherige Fassung als `.bak` liegen und lässt sich mit einem Klick wiederherstellen
- **Einmalige Zeitpläne** zusätzlich zu wöchentlichen
- **Benachrichtigungen** per Discord-/Webhook und E-Mail: Absturz, Neustart, Neustart fehlgeschlagen, Speicher fast voll, RAM dauerhaft hoch, Zeitplan fehlgeschlagen
- **E-Mail (optional):** eigene Adresse pro Benutzer mit Bestätigung, „Passwort vergessen“, Einladungen per Mail
- **Einladungslinks** statt Startpasswort
- **Öffentliche Statusseite** (abschaltbar): Name, Spiel, Status und Adresse ausgewählter Server – ohne Passwörter und Steuerung
- **Ankündigungen** oben auf jeder Seite, mit geplantem Beginn und Ende
- **Datenschutz:** einstellbare Aufbewahrung (Audit-Log Standard 30 Tage), Impressum und Datenschutzerklärung mit Vorlagen, Datenauskunft pro Benutzer, Audit-Export als CSV/TXT
- **Datenumzug:** alle Benutzer, Rechte, Logos und Einstellungen verschlüsselt exportieren und in einer neuen Instanz einspielen (auch direkt im Einrichtungsassistenten)

## Voraussetzungen

- Debian 12 oder 13 (auch als Proxmox-LXC), 1 CPU-Kern, 512 MB RAM, 2 GB Speicher reichen
- Netzwerkzugriff auf das Pterodactyl-Panel **und** auf die Wings-Server (Daemon-Port, meist 8080, und SFTP-Port, meist 2022)
- Ein Pterodactyl-Admin-Zugang, um zwei API-Keys anzulegen
- Getestet mit Pterodactyl Panel 1.12 und Wings 1.12

## Installation

```bash
tar -xzf hatchtower-<version>.tar.gz
cd hatchtower-<version>
sudo bash installer/install.sh
```

Optionen: `--port 8000` (Port der Oberfläche), `--bind 0.0.0.0`,
`--allow "192.168.1.5,192.168.1.10"` (Port nur für diese IPs öffnen, in einer eigenen
nftables-Tabelle – eine bestehende Firewall wird nicht angefasst).

Am Ende zeigt das Skript die Adresse und einen **Einrichtungscode**. Im Browser führt dann ein
Assistent durch alles Weitere:

1. **Owner-Konto** anlegen (mit dem Einrichtungscode)
2. **Panel-Adresse** und **Application-API-Key** (nur Lese-Rechte)
3. **Client-API-Key** eines eigenen Panel-Benutzers, mit Allowed IP
4. **Wings-Server** prüfen – sie werden automatisch aus dem Panel gelesen, mehrere sind kein Problem.
   Optional pro Node eine andere Adresse, falls er im Panel unter einer hier nicht erreichbaren Adresse steht.
5. **Optionen:** Modus (nur lesen / lesen und steuern), öffentliche Adresse für Spieler, Reverse-Proxy,
   Zeitzone (gilt für Zeitpläne und alle angezeigten Uhrzeiten; Standard ist die Zeitzone des Servers)

Den Modus kannst du jederzeit unter **System → Verbindung bearbeiten → Modus und Optionen** umstellen.
Neue Installationen starten mit **Nur lesen**.

## Name und Aussehen

Unter **System → Erscheinungsbild** kannst du einen eigenen Namen (z. B. den deiner Community) und
eigene Logos hinterlegen. Dein Name steht dann groß in der Kopfzeile, „Hatchtower“ dezent darunter
und in der Fußzeile. Theme und Sprache wählt jeder Benutzer oben rechts in der Kopfzeile.

Übersetzungen liegen in `app/hatchtower/locales/<sprache>.json` (deutscher Text → Übersetzung, getrennt für Server und Browser). Fehlt eine Übersetzung, erscheint der deutsche Text.

Alles bleibt später unter **System → Verbindung bearbeiten** änderbar. API-Keys werden verschlüsselt
gespeichert und nie wieder angezeigt.

## Update

```bash
sudo hatchtower update hatchtower-<neue-version>.tar.gz
```

Vor dem Update wird die Datenbank gesichert. Startet die neue Version nicht, kehrt Hatchtower
automatisch zur vorherigen zurück. Benutzer, Rechte, Logos, Zeitpläne und Verlauf bleiben erhalten.

## Befehle auf dem Server

| Befehl | Zweck |
|---|---|
| `hatchtower setup-token` | Neuen Einrichtungscode erzeugen (nur solange noch kein Konto existiert) |
| `hatchtower create-owner` | Owner-Konto auf der Konsole anlegen |
| `hatchtower reset-password` | Passwort eines Benutzers neu setzen und Konto entsperren |
| `hatchtower selftest` | Verbindung zu Panel und Wings prüfen (nur lesend) |
| `hatchtower version` | Installierte Version |
| `hatchtower update <paket>` | Auf eine neue Version aktualisieren |
| `journalctl -u hatchtower -f` | Log des Dienstes |

## Dateien

| Pfad | Inhalt |
|---|---|
| `/opt/hatchtower` | Programm, Python-Umgebung, Installer, Werkzeuge |
| `/etc/hatchtower/hatchtower.env` | interne Schlüssel und Port (nicht weitergeben) |
| `/var/lib/hatchtower/hatchtower.db` | Datenbank (SQLite) inkl. verschlüsselter API-Keys |
| `/var/backups/hatchtower/` | tägliches Backup um 03:30 (14 Tage) und Backups vor Updates |

## Sicherheit

- Eigene Anmeldung mit starken Passwörtern (Argon2id), Sperre nach Fehlversuchen, Sitzungs-Timeouts
- CSRF-Schutz, strenge Content-Security-Policy, kein Einbetten in fremde Seiten
- Rechte pro Benutzer und Server, „alles verboten, was nicht erlaubt ist“
- Read-Only-Modus wird technisch durchgesetzt: Schreibende Anfragen erreichen Pterodactyl gar nicht erst
- Der Client-Key eines Panel-Admins darf technisch alles. Schutz: Allowed IP, Read-Only-Modus, Audit-Log.
  Vorsichtiger: den Panel-Benutzer nur als Subuser zu einzelnen Servern hinzufügen.
- Für den Zugriff von außen einen Reverse-Proxy mit HTTPS verwenden (z. B. Pangolin, Nginx, Traefik)

## Entwicklung

| Werkzeug | Zweck |
|---|---|
| `tools\build-release.ps1` | Installationspaket bauen → `dist\hatchtower-<version>.tar.gz` |
| `tools\deploy.ps1 [-Target host]` | Paket bauen und per `hatchtower update` auf einen Server ausrollen |
| `tools/smoketest.sh` | Rauchtest auf dem Server, eigene Test-Datenbank, immer nur lesend |
| `tools/devserver.sh` | Test-Instanz auf Port 8001 für Oberflächentests (per SSH-Tunnel) |

Aufbau des Programms (`app/hatchtower/`): `main.py` (Web), `ptero.py` (Pterodactyl-Adapter mit
Read-Only-Sperre), `wings.py` (Konsole), `monitor.py` (Abgleich, Verlauf), `automation.py`,
`permissions.py`, `security.py`, `setup.py` (Einrichtung), `themes.py`, `db.py` (Migrationen).

## Lizenz

Die Lizenz von Hatchtower selbst ist noch nicht festgelegt.

Alle Abhängigkeiten stehen unter permissiven Lizenzen (MIT, BSD-3-Clause, Apache-2.0, PSF-2.0, HPND/MIT-CMU, MPL-2.0 für certifi) und sind privat wie kommerziell frei nutzbar. Bei einer Weitergabe müssen ihre Lizenzhinweise beiliegen. Hatchtower enthält keine fremden Schriften, Icons, Bilder oder JavaScript-Bibliotheken; Pterodactyl wird nur über die API angebunden.
Verwendete Open-Source-Bibliotheken und ihre Lizenzen stehen in der Oberfläche unter **Lizenzen** (Fußzeile)
und als vollständige Texte in `THIRD_PARTY_LICENSES.txt`, die bei jeder Installation neu erzeugt wird
(`tools/third_party_licenses.py`).
