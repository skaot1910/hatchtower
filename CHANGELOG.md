# Änderungen

Neue Einträge oben. Die Abschnitte „## <version>“ werden beim Veröffentlichen
automatisch als Beschreibung des GitHub-Releases übernommen (`tools/release.sh`).

## 0.13.1 – 2026-10-07

- Installation: ruhigere Ausgabe, keine Sprach-Warnungen mehr in frischen Debian-Containern

## 0.13.0 – 2026-10-07

- **Installation mit einem Befehl auf Proxmox:** `proxmox/install.sh` legt einen Debian-Container an und installiert die neueste Version von GitHub
- **Installation auf Debian/Ubuntu** über `installer/get.sh` (lädt das Release, prüft die SHA-256-Prüfsumme)
- **`hatchtower update`** holt neue Versionen jetzt selbst von GitHub; `hatchtower update --check` zeigt, ob es eine neue gibt
- **`hatchtower github-token`** hinterlegt einen Lese-Token, solange das Repository privat ist
- **Hinweis auf neue Versionen** unter System (Prüfung zweimal am Tag, installiert nie selbstständig)
- Veröffentlichung neuer Versionen über GitHub-Releases (`tools/release.sh`)

## 0.12.4 – 2026-10-07

- Lizenzen-Reiter: Zusammenfassung, weitere Bestandteile, Marken und vollständige Lizenztexte

## 0.12.3 – 2026-10-07

- Sicherheit: Größenlimits für Anfragen, zusätzliche Header, Version und Paketliste nur angemeldet,
  „Passwort vergessen“ ohne Rückschluss auf vorhandene Konten, strengere systemd-Abschottung

## 0.12.0 – 2026-10-07

- Themes Dunkel/Mittel/Hell in der Kopfzeile umschaltbar
- Statusseite: Gestartet/Gestoppt, Serverpasswort mit Doppelbestätigung, Sortierung nach Onlinestatus
- „Ingame-Name“ heißt jetzt „Ingame-Servername“

## 0.11.0 – 2026-10-07

- Datenumzug, Statusseite, Benachrichtigungen (Webhook/E-Mail), E-Mail mit „Passwort vergessen“,
  Einladungslinks, Impressum und Datenschutz, einstellbare Aufbewahrung, Audit-Export,
  Ankündigungen, einmalige Zeitpläne, Mehrfachauswahl, Datei-Sicherung beim Bearbeiten,
  Log-Archiv, Verfügbarkeit, Tags und Favoriten, Konto selbst löschen

## 0.10.0 – 2026-10-06

- Deutsch und Englisch
