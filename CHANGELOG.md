# Änderungen

Neue Einträge oben. Die Abschnitte „## <version>“ werden beim Veröffentlichen
automatisch als Beschreibung des GitHub-Releases übernommen (`tools/release.sh`).

## 0.21.1 – 2026-10-12

- Serverstatus umbenannt: „running“ heißt jetzt **online** (deutsch und englisch), „starting / stopping“ heißt auf Deutsch **wird gestartet / wird gestoppt** – auf den Serverkarten, im Statusfilter und auf der öffentlichen Statusseite

## 0.21.0 – 2026-10-12

- **Pelican und Calagopus werden unterstützt:** Hatchtower lässt sich jetzt neben Pterodactyl auch mit [Pelican](https://pelican.dev) und [Calagopus](https://calagopus.com) verbinden. Eine Installation verwaltet weiterhin die Server eines Panels
  - Im Einrichtungsassistenten und unter System → Verbindung wählst du das Panel; zu jedem steht, wo es den API-Key gibt und welche Rechte er braucht
  - **Pelican:** gleiche Funktionen wie mit Pterodactyl, Keys beginnen mit `papp_` bzw. `pacc_`
  - **Calagopus:** nur ein API-Key (Admin-Rechte werden nur gelesen), der Schritt „Client-Key“ entfällt. Konsole, Uploads und Downloads laufen auch über den Wings-Proxy von Calagopus
  - Alle Texte nennen das verbundene Panel statt immer „Pterodactyl“ (z. B. „Pelican antwortet nicht“, „Status laut Calagopus“)
  - Die Seite **Über Hatchtower** (Fußzeile → Lizenzen) zeigt die unterstützten Panels mit den getesteten Versionen und welches verbunden ist
- Bestehende Installationen bleiben ohne Zutun mit Pterodactyl verbunden
- Wechsel des Panels: Wer mit seinen Servern von Pterodactyl zu Pelican oder Calagopus umzieht, trägt unter System → Verbindung das neue Panel samt Key ein. Server mit gleicher Kennung (bei Pelican-Umzügen üblich) behalten Rechte, Zeitpläne und Verlauf

## 0.20.1 – 2026-10-12

- **Abstürze werden jetzt auch erkannt, wenn Wings den Server selbst sofort neu startet** (bei Pterodactyl die Standardeinstellung). Bisher fiel das zwischen zwei Abfragen oft durch: keine Meldung per Discord/E-Mail, kein Eintrag bei den Abstürzen, kein roter Balken in der Verfügbarkeit. Gewollte Neustarts (Hatchtower, Panel, Zeitplan, Backup, Konsolenbefehl `restart`) zählen weiterhin nicht als Absturz
- **Behoben:** Backups zeigten in der englischen Oberfläche und in den Meldungen deutsche Texte („fertig“, „4 von 4 Servern gesichert“)
- **Behoben:** Auf dem Handy ragten Serverkarten mit langem Namen über den Bildschirmrand
- README: Backups und mehrere Discord-Kanäle ergänzt

## 0.20.0 – 2026-10-09

- **Backups (neu):** Hatchtower sichert Server oder ausgewählte Dateien und Ordner (z. B. Spielstände) als ZIP – eine Datei pro Server und Lauf, mit den Pfaden ab dem Serverordner, damit sie sich woanders wieder einspielen lassen
  - Backup-Jobs für einen, mehrere oder alle Server (auch künftige, nur Owner), mit Ausschlüssen (z. B. `logs/`, `*.log`)
  - Im Datei-Explorer: Dateien und Ordner ankreuzen → „Zu Backup-Job hinzufügen“ (bestehender oder neuer Job); man bleibt im Ordner und kann weitere hinzufügen
  - Ziele: Direktdownload im Browser (nur von Hand), Laufwerk im Hatchtower-Container (Auswahl der eingehängten Laufwerke unter /mnt, /media, /srv, z. B. Proxmox-Mountpoint, USB, NFS) oder SMB-Freigabe (Windows, NAS, Samba; Zugangsdaten verschlüsselt). Ergebnis und freier Platz der letzten Prüfung stehen beim Ziel
  - Starten von Hand („Jetzt ausführen“, „Jetzt ausführen und herunterladen“) oder per Zeitplan; mehrere Backups laufen nacheinander, laufende lassen sich abbrechen; Fortschritt mit %, MB, Dateien und Geschwindigkeit
  - Server vorher stoppen (mit Vorwarnung im Spiel) und danach wieder starten (nur wenn er lief) oder aus lassen; der Absturz-Neustart wertet das nicht als Absturz
  - Aufbewahrung nach Anzahl oder Tagen, Platzprüfung vorab, Meldung per Webhook/E-Mail (nur bei Fehlern oder immer)
  - Neue Rechte: „Backups sehen“, „Backups starten und herunterladen“, „Backup-Jobs verwalten“; Ziele verwalten nur Owner
  - Schont das Anfragen-Limit von Pterodactyl: Backups lassen immer rund ein Drittel frei, damit Statusabfragen nicht ausfallen
- **Discord-Status in mehreren Kanälen:** beliebig viele Webhooks, jeder mit eigener Server-Auswahl, eigenen Angaben und eigener Überschrift – unabhängig von der Statusseite. Ein bisher eingerichteter Webhook wird beim Update übernommen
- **Behoben:** Meldungen nach dem Speichern auf Seiten mit Anker (z. B. System → Erscheinungsbild) wurden nicht angezeigt
- Update: neue Abhängigkeit `smbprotocol`; der Dienst darf zusätzlich nach /mnt, /media und /srv schreiben (beides kommt mit `hatchtower update`)

## 0.19.2 – 2026-10-09

- **Offene Einladungslinks wieder kopierbar:** In der Benutzerverwaltung lässt sich der Link einer offenen Einladung über „Link anzeigen“ erneut abrufen und kopieren (gilt für Einladungen ab dieser Version)

## 0.19.1 – 2026-10-08

- **Einladungslinks länger gültig:** zusätzlich 30, 60 oder 90 Tage und unbegrenzt (läuft nie ab, bis zum Zurückziehen oder Aufbrauchen)

## 0.19.0 – 2026-10-08

- **Einladungslinks einstellbar:** gültig 1, 3, 7 oder 14 Tage; nutzbar einmal, 2–10, 25, 50, 100 Mal oder unbegrenzt
  - Optionale interne Beschreibung (z. B. für wen der Link ist), sichtbar in den offenen Einladungen zusammen mit Nutzungen („2/5“) und Ersteller
  - Audit-Log: jede Nutzung mit neuem Benutzer, Beschreibung, Ersteller und Zähler; dazu „Einladung aufgebraucht“ und „Einladung abgelaufen“
- **Notiz pro Benutzer:** in den Benutzerdetails unter „Konto“, nur für Owner sichtbar, auch in der Benutzerliste
- **Neues Recht „Konsolen-Archiv sehen“:** Archiv durchsuchen und herunterladen ist jetzt getrennt von „Konsole lesen“. Wer bisher die Konsole lesen durfte, bekommt das neue Recht beim Update automatisch dazu

## 0.18.0 – 2026-10-08

- **Status in Discord:** Hatchtower postet die Server der Statusseite über einen Discord-Webhook in einen Kanal und hält die Nachricht aktuell (Prüfung jede Minute, bearbeitet nur bei Änderungen)
  - Eigene Auswahl der Angaben für Discord (Servername, Ingame-Servername, Status, Start-/Stoppzeit, Adresse, Passwort – Passwort nur mit ausdrücklicher Bestätigung, im Kanal als Spoiler)
  - Kompakte Darstellung: drei Server pro Reihe mit Trennlinien, oben „X von Y Servern online“ mit farbigen Kästchen und Links zur Statusseite und zur Serververwaltung
  - Viele Server: automatisch auf mehrere Nachrichten verteilt (bis etwa 150 Server); gelöschte Nachrichten werden neu gepostet, überzählige entfernt
  - Einstellungen unter System → Statusseite; der Webhook wird verschlüsselt gespeichert und beim Datenumzug mitgenommen
- **Angezeigtes Spiel überschreibbar:** in den Servereinstellungen („Angezeigtes Spiel“); leer bleibt der Egg-Name aus Pterodactyl. Gilt für Übersicht, Serverseite, Statusseite, Discord und die Serverlisten der Verwaltung

## 0.17.0 – 2026-10-08

- **„Alle Server, auch künftige“** pro Benutzer: eine Rechtegruppe gilt für jeden Server ohne eigene Einstellung – auch für Server, die später in Pterodactyl dazukommen
  - Einstellung pro Server geht immer vor; „Kein Zugriff“ nimmt einzelne Server aus
  - Deutliche Warnung, wenn die Gruppe Steuer- oder Schreibrechte enthält
- **Neue Server werden gemeldet:** Hinweis in der Übersicht für Owner (mit „Rechte prüfen“, „Statusseite“, „Automationen“, „Erledigt“), Eintrag im Audit-Log und Benachrichtigung „Neuer Server erkannt“ mit der Liste der automatisch freigegebenen Benutzer
- Statusseite und Neustart nach Absturz bleiben bei neuen Servern aus, bis ein Owner sie einschaltet

## 0.16.0 – 2026-10-08

- **Neues Recht „Automationen verwalten“** (pro Server): Benutzer können Zeitpläne und den Neustart nach Absturz für ihre Server selbst pflegen – nur mit Aktionen, die sie auch selbst ausführen dürfen. Fehlt dem Ersteller später das Recht, läuft der Zeitplan nicht mehr.
- **Pterodactyl-Ausfall wird erkannt:** Hinweis in der Übersicht, Server zeigen „keine aktuellen Daten“ statt „online“, Statusseite meldet, dass der Status gerade nicht abfragbar ist; Benachrichtigung „Pterodactyl nicht erreichbar“ / „wieder erreichbar“
- **CPU dauerhaft hoch** als neue Benachrichtigung (Schwelle in % des Limits und Dauer einstellbar)
- **Geänderte Limits** (CPU, RAM, Speicher) aus Pterodactyl kommen nach spätestens 30 Sekunden an (vorher 5 Minuten) und sofort nach einem Start oder Neustart
- **Schnellere Statusanzeige:** nach Start/Stopp/Neustart sekündlich, Abstürze laufender Server sofort; Übersicht aktualisiert alle 5 Sekunden
- **Statusseite kompakter:** Verfügbarkeitsbalken in derselben Zeile wie Name und Adresse; Spielerverlauf entfernt
- Verfügbarkeitsbalken (30 Tage) auch in den Serverdetails
- Passwort-Fehlermeldung verständlicher formuliert („Das Passwort darf den Benutzernamen nicht enthalten.“)
- **Sicherheit:** Live-Konsole endet, sobald das Recht entzogen oder die Sitzung ungültig wird; Uploads werden erst nach der Rechteprüfung angenommen; Serverpasswort in den Servereinstellungen nur noch mit dem Recht „Passwort sehen“ sichtbar
- Automatische Tests der Rechte-Matrix (`tests/`) und Dokumentation `docs/rechte-matrix.md`

## 0.15.0 – 2026-10-07

- **Statusseite ausgebaut:**
  - Verfügbarkeitsbalken der letzten 30 Tage pro Server (wie bei Uptime Kuma), mit Prozentwert und Legende
  - Spielerverlauf der letzten 24 Stunden als kleine Grafik (Höchststand und aktuelle Zahl)
  - Störungs- und Wartungshistorie: Abstürze und öffentliche Wartungsankündigungen der letzten 30 Tage
  - Gesamtzustand oben („Alle Server sind online“ bzw. „3 von 8 Servern online“)
  - Alles einzeln ein- und ausschaltbar unter System → Statusseite
- **Community-Discord:** Link oben in der Kopfzeile und auf der Statusseite; einstellbar unter System → Erscheinungsbild
- Hatchtower merkt sich die Spielerzahl jetzt im Verlauf

## 0.14.1 – 2026-10-07

- Update über die Weboberfläche: Ergebnis landet jetzt zuverlässig im Audit-Log

## 0.14.0 – 2026-10-07

- **Update über die Weboberfläche:** unter System → Version und Updates „Update auf … installieren“; Fortschritt und Protokoll live, Ergebnis im Audit-Log
  (die Oberfläche selbst hat keine Root-Rechte – sie stößt nur den Dienst `hatchtower-update` an, der genau `hatchtower update` ausführt)
- **„Jetzt nach Updates suchen“** – höchstens alle 5 Minuten
- Übersicht standardmäßig nach „Status: online zuerst“ sortiert

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
