# Rechte-Matrix

Welche Route unter welcher Bedingung erreichbar ist. Erzeugt aus `tests/routes.py` mit `python tests/routes.py`; `tests/test_rechte_matrix.py` prüft jede Zeile automatisch.

Grundregeln:

- Alles, was nicht ausdrücklich erlaubt ist, ist verboten. Owner dürfen alles.
- Server-Routen verlangen immer zuerst **Sehen** (`server.view`), dann das genannte Recht. Ein fremder oder unbekannter Server liefert dieselbe Antwort (403) wie ein fehlendes Recht.
- Rechte mit ✎ sind Schreibrechte: Im Read-Only-Modus hat sie niemand, auch kein Owner.
- Jede verweigerte Aktion landet als `access.denied` im Audit-Log.
- Zustandsändernde Anfragen brauchen ein CSRF-Token (Formularfeld oder Header `X-CSRF-Token`).
- Zeitpläne und geplante Backup-Jobs eines Benutzers laufen nur, solange er aktiv ist und die nötigen Rechte noch hat; gibt es ihn nicht mehr, laufen sie nicht.

## Rechte

| Recht | Name | Schreibrecht | Setzt voraus | Routen | Achtung |
|---|---|---|---|---|---|
| `server.view` | Sehen |  | – | `GET /api/servers/{ident}/metrics`<br>`GET /servers/{ident}`<br>`POST /api/servers/{ident}/favorite` |  |
| `server.password` | Passwort sehen |  | `server.view` |  |  |
| `server.log` | Protokoll sehen |  | `server.view` |  |  |
| `server.players` | Spieler sehen |  | `server.view` | `GET /servers/{ident}/players` |  |
| `server.manage` | Server verwalten |  | `server.view` | `GET /servers/{ident}/settings`<br>`POST /servers/{ident}/logo/delete`<br>`POST /servers/{ident}/logo`<br>`POST /servers/{ident}/settings` |  |
| `power.start` | Start | ✎ | `server.view` | `POST /api/servers/{ident}/power` (start) |  |
| `power.stop` | Stop | ✎ | `server.view` | `POST /api/servers/{ident}/power` (stop) |  |
| `power.restart` | Restart | ✎ | `server.view` | `POST /api/servers/{ident}/power` (restart) |  |
| `console.read` | Konsole lesen |  | `server.view` | `GET /servers/{ident}/console/stream` |  |
| `console.command` | Befehle senden | ✎ | `server.view`, `console.read` | `POST /api/servers/{ident}/command` | Je nach Spiel reichen Befehle weit, z. B. Spieler zu Admins machen oder die Welt verändern. |
| `console.archive` | Konsolen-Archiv sehen |  | `server.view` | `GET /api/servers/{ident}/logs`<br>`GET /servers/{ident}/logs.txt` |  |
| `sftp.view` | Dateien ansehen |  | `server.view` | `GET /api/servers/{ident}/files/search`<br>`GET /api/servers/{ident}/folders`<br>`GET /servers/{ident}/files`<br>`POST /api/servers/{ident}/files/reindex` |  |
| `sftp.download` | Herunterladen |  | `server.view`, `sftp.view` | `GET /servers/{ident}/files/download`<br>`POST /servers/{ident}/files/zip` | Konfigurationsdateien können Passwörter und Zugangsschlüssel enthalten. |
| `sftp.upload` | Hochladen | ✎ | `server.view`, `sftp.view` | `POST /servers/{ident}/files/upload` |  |
| `sftp.edit` | Bearbeiten | ✎ | `server.view`, `sftp.view`, `sftp.download` | `GET /servers/{ident}/files/edit`<br>`POST /servers/{ident}/files/edit`<br>`POST /servers/{ident}/files/restore` |  |
| `sftp.rename` | Umbenennen | ✎ | `server.view`, `sftp.view` | `POST /servers/{ident}/files/move`<br>`POST /servers/{ident}/files/rename` |  |
| `sftp.create_directory` | Ordner anlegen | ✎ | `server.view`, `sftp.view` | `POST /servers/{ident}/files/mkdir` |  |
| `sftp.delete` | Löschen | ✎ | `server.view`, `sftp.view` | `POST /servers/{ident}/files/delete-many`<br>`POST /servers/{ident}/files/delete` |  |
| `automation.manage` | Automationen verwalten | ✎ | `server.view` | `GET /admin/automation`<br>`POST /admin/automation/crash`<br>`POST /admin/automation/schedules/{sched_id}/delete`<br>`POST /admin/automation/schedules/{sched_id}/toggle`<br>`POST /admin/automation/schedules` |  |
| `backups.view` | Backups sehen |  | `server.view` | `GET /admin/backups`<br>`GET /api/backups/status` |  |
| `backups.run` | Backups starten und herunterladen |  | `server.view`, `backups.view` | `GET /admin/backups/runs/{run_id}/download`<br>`POST /admin/backups/jobs/{job_id}/download`<br>`POST /admin/backups/jobs/{job_id}/run`<br>`POST /admin/backups/runs/{run_id}/verify`<br>`POST /api/backups/runs/{run_id}/cancel` | Gibt über das Backup Zugriff auf alle gesicherten Dateien – auch ohne die Rechte unter „Dateien“. |
| `backups.restore` | Backups wiederherstellen | ✎ | `server.view`, `backups.view`, `backups.run` | `GET /admin/backups/runs/{run_id}/restore`<br>`POST /admin/backups/runs/{run_id}/restore`<br>`POST /api/backups/restores/{restore_id}/cancel` | Ersetzt Dateien auf dem Server – auch ohne die Rechte unter „Dateien“. |
| `backups.manage` | Backup-Jobs verwalten |  | `server.view`, `backups.view`, `sftp.view` | `GET /admin/backups/jobs/{job_id}`<br>`POST /admin/backups/jobs/{job_id}/delete`<br>`POST /admin/backups/jobs/{job_id}/items/remove`<br>`POST /admin/backups/jobs/{job_id}/items`<br>`POST /admin/backups/jobs/{job_id}`<br>`POST /admin/backups/jobs`<br>`POST /admin/backups/runs/bulk`<br>`POST /admin/backups/runs/{run_id}/delete`<br>`POST /servers/{ident}/files/backup` |  |
| `admin.view` | Panel-Einstellungen sehen |  | `server.view` | `GET /servers/{ident}/admin` | Startvariablen können Passwörter oder Zugangsschlüssel enthalten. |
| `admin.details` | Name und Beschreibung | ✎ | `server.view`, `admin.view` | `POST /servers/{ident}/admin/details` |  |
| `admin.build` | Ressourcen und Ports | ✎ | `server.view`, `admin.view` | `POST /servers/{ident}/admin/build`<br>`POST /servers/{ident}/admin/ports` | Kann dem Server mehr Leistung geben, als der Node hat, und freie Ports des Nodes belegen. |
| `admin.startup` | Startparameter | ✎ | `server.view`, `admin.view` | `POST /servers/{ident}/admin/startup` | Startvariablen legen je nach Spiel Passwörter, Versionen oder Mods fest. |
| `admin.reinstall` | Neu installieren | ✎ | `server.view`, `admin.view` | `POST /servers/{ident}/admin/reinstall` | Je nach Egg werden dabei Dateien des Servers überschrieben. |

## Öffentlich

| Methode | Pfad | CSRF | Zweck |
|---|---|---|---|
| GET | `/login` | – | Anmeldeseite |
| POST | `/login` | Vorab-Token | Anmelden |
| GET | `/lang/{code}` | – | Sprache umschalten (angemeldet: im Konto gespeichert) |
| GET | `/status` | – | Öffentliche Statusseite (abschaltbar) |
| GET | `/status.json` | – | Statusseite als JSON, nur freigegebene Angaben |
| GET | `/status/logo/{ident}` | – | Logo eines Servers, nur wenn er auf der Statusseite steht |
| GET | `/impressum` | – | Impressum (abschaltbar) |
| GET | `/datenschutz` | – | Datenschutzerklärung (abschaltbar) |
| GET | `/branding/{filename}` | – | Eigene Logos, nur die aktuell eingestellten Dateien |
| GET | `/forgot` | – | Passwort vergessen (nur mit eingeschalteter E-Mail) |
| POST | `/forgot` | Vorab-Token | Link zum Zurücksetzen anfordern |

## Einmal-Link (ohne Anmeldung)

| Methode | Pfad | CSRF | Zweck |
|---|---|---|---|
| GET | `/reset/{token}` | – | Neues Passwort setzen (Link aus der E-Mail) |
| POST | `/reset/{token}` | Vorab-Token | Neues Passwort speichern |
| GET | `/verify/{token}` | – | E-Mail-Adresse bestätigen |
| GET | `/invite/{token}` | – | Einladung öffnen |
| GET | `/notify/unsubscribe/{token}` | – | E-Mail-Benachrichtigungen abbestellen: Bestätigungsseite |
| POST | `/notify/unsubscribe/{token}` | – | E-Mail-Benachrichtigungen abbestellen (auch Ein-Klick aus dem Mailprogramm, signierter Link statt Formular-Token) |
| POST | `/invite/{token}` | Vorab-Token | Einladung annehmen, Konto anlegen |

## Einrichtung (nur ohne Benutzer)

| Methode | Pfad | CSRF | Zweck |
|---|---|---|---|
| POST | `/setup/owner` | Vorab-Token | Ersten Owner anlegen |
| POST | `/setup/import` | Vorab-Token | Neue Instanz aus Datenumzug befüllen |

## Angemeldet – nur eigenes Konto

| Methode | Pfad | CSRF | Zweck |
|---|---|---|---|
| GET | `/` | – | Übersicht (nur sichtbare Server); ?als= nur für Owner |
| GET | `/api/overview` | – | Live-Werte der Übersicht (nur sichtbare Server) |
| GET | `/theme/{key}` | – | Theme umschalten |
| GET | `/view/{mode}` | – | Einfache oder vollständige Ansicht umschalten |
| POST | `/logout` | Formular | Abmelden |
| GET | `/logos/{filename}` | – | Serverlogos |
| GET | `/about` | – | Über Hatchtower, Paketliste |
| GET | `/about/licenses.txt` | – | Lizenztexte |
| GET | `/account` | – | Eigenes Konto |
| POST | `/account/theme` | Formular | Eigenes Theme |
| POST | `/account/language` | Formular | Eigene Sprache |
| POST | `/account/password` | Formular | Eigenes Passwort ändern |
| POST | `/account/email` | Formular | Eigene E-Mail-Adresse ändern oder entfernen |
| POST | `/account/notify` | Formular | Eigene Benachrichtigungen |
| POST | `/account/delete` | Formular | Eigenes Konto löschen (Passwort nötig) |
| GET | `/account/export` | – | Eigene Daten herunterladen (Art. 15 DSGVO) |
| GET | `/notifications` | – | Benachrichtigungen (Glocke): nur Meldungen zu sichtbaren Servern, Verwaltung nur Owner |
| POST | `/notifications/read-all` | Formular | Eigene Benachrichtigungen als gelesen markieren |
| POST | `/notifications/{entry_id}/read` | Formular | Eine Benachrichtigung als gelesen markieren (nur sichtbare) |

## Server – Recht pro Server

| Methode | Pfad | Recht | CSRF | Pterodactyl | Zweck |
|---|---|---|---|---|---|
| GET | `/servers/{ident}` | `server.view` | – | liest | Serverdetails |
| GET | `/servers/{ident}/players` | `server.players` | – | liest | Reiter Spieler: wer online ist und woran es erkannt wird |
| GET | `/api/servers/{ident}/metrics` | `server.view` | – | liest | Verlauf CPU, RAM, Netzwerk |
| POST | `/api/servers/{ident}/favorite` | `server.view` | Header | liest | Favorit an/aus (nur eigene Liste) |
| POST | `/api/servers/{ident}/power` | `power.start` | Header | schreibt | Server starten |
| POST | `/api/servers/{ident}/power` | `power.stop` | Header | schreibt | Server stoppen |
| POST | `/api/servers/{ident}/power` | `power.restart` | Header | schreibt | Server neu starten |
| GET | `/servers/{ident}/console/stream` | `console.read` | – | liest | Live-Konsole |
| GET | `/api/servers/{ident}/logs` | `console.archive` | – | liest | Konsolen-Archiv durchsuchen |
| GET | `/servers/{ident}/logs.txt` | `console.archive` | – | liest | Konsolen-Archiv herunterladen |
| POST | `/api/servers/{ident}/command` | `console.command` | Header | schreibt | Konsolenbefehl senden |
| GET | `/servers/{ident}/files` | `sftp.view` | – | liest | Dateiliste |
| GET | `/servers/{ident}/files/download` | `sftp.download` | – | liest | Datei herunterladen |
| POST | `/servers/{ident}/files/zip` | `sftp.download` | Formular | liest | Auswahl als ZIP herunterladen |
| POST | `/servers/{ident}/files/upload` | `sftp.upload` | Formular | schreibt | Dateien hochladen |
| GET | `/servers/{ident}/files/edit` | `sftp.edit` | – | liest | Datei im Editor öffnen |
| POST | `/servers/{ident}/files/edit` | `sftp.edit` | Formular | schreibt | Datei speichern (alte Fassung als .bak) |
| POST | `/servers/{ident}/files/restore` | `sftp.edit` | Formular | schreibt | .bak wiederherstellen |
| POST | `/servers/{ident}/files/rename` | `sftp.rename` | Formular | schreibt | Umbenennen |
| POST | `/servers/{ident}/files/mkdir` | `sftp.create_directory` | Formular | schreibt | Ordner anlegen |
| POST | `/servers/{ident}/files/delete` | `sftp.delete` | Formular | schreibt | Löschen |
| POST | `/servers/{ident}/files/delete-many` | `sftp.delete` | Formular | schreibt | Mehrere Dateien und Ordner löschen |
| GET | `/api/servers/{ident}/folders` | `sftp.view` | – | liest | Unterordner für die Ordnerauswahl beim Verschieben |
| GET | `/api/servers/{ident}/files/search` | `sftp.view` | – | liest | Dateien und Ordner suchen (lokaler Index) |
| POST | `/api/servers/{ident}/files/reindex` | `sftp.view` | Header | liest | Suchindex der Dateien neu aufbauen (nur lesend) |
| POST | `/servers/{ident}/files/move` | `sftp.rename` | Formular | schreibt | Dateien und Ordner in einen anderen Ordner verschieben |
| POST | `/servers/{ident}/files/backup` | `backups.manage` | Formular | liest | Auswahl zu einem Backup-Job hinzufügen |
| GET | `/servers/{ident}/settings` | `server.manage` | – | liest | Serverangaben im Manager |
| POST | `/servers/{ident}/settings` | `server.manage` | Formular | liest | Serverangaben speichern |
| POST | `/servers/{ident}/logo` | `server.manage` | Formular | liest | Serverlogo hochladen |
| POST | `/servers/{ident}/logo/delete` | `server.manage` | Formular | liest | Serverlogo entfernen |
| GET | `/servers/{ident}/admin` | `admin.view` | – | liest | Panel-Einstellungen (Ändern nur mit Admin-Key und dem jeweiligen Recht) |
| POST | `/servers/{ident}/admin/details` | `admin.details` | Formular | schreibt | Name und Beschreibung im Panel ändern |
| POST | `/servers/{ident}/admin/build` | `admin.build` | Formular | schreibt | RAM, CPU, Speicher und Limits im Panel ändern |
| POST | `/servers/{ident}/admin/ports` | `admin.build` | Formular | schreibt | Ports des Servers ändern (hinzufügen, entfernen, Hauptport) |
| POST | `/servers/{ident}/admin/startup` | `admin.startup` | Formular | schreibt | Startbefehl, Docker-Image und Startvariablen im Panel ändern |
| POST | `/servers/{ident}/admin/reinstall` | `admin.reinstall` | Formular | schreibt | Server im Panel neu installieren |

## Server – nur Owner

| Methode | Pfad | CSRF | Zweck |
|---|---|---|---|
| POST | `/api/servers/{ident}/power` | Header | Server hart beenden |

## Automationen – Owner oder Recht pro Server

| Methode | Pfad | Recht | CSRF | Pterodactyl | Zweck |
|---|---|---|---|---|---|
| GET | `/admin/automation` | `automation.manage` | – | liest | Automationen (nur Server mit dem Recht) |
| POST | `/admin/automation/crash` | `automation.manage` | Formular | liest | Neustart bei Absturz |
| POST | `/admin/automation/schedules` | `automation.manage` | Formular | liest | Zeitplan anlegen (Aktion braucht das passende Steuerrecht) |
| POST | `/admin/automation/schedules/{sched_id}/toggle` | `automation.manage` | Formular | liest | Zeitplan pausieren/aktivieren |
| POST | `/admin/automation/schedules/{sched_id}/delete` | `automation.manage` | Formular | liest | Zeitplan löschen |

## Backups – Owner oder Recht pro Server

| Methode | Pfad | Recht | CSRF | Pterodactyl | Zweck |
|---|---|---|---|---|---|
| GET | `/admin/backups` | `backups.view` | – | liest | Backups: Jobs, laufende und gespeicherte Backups (nur Server mit dem Recht) |
| GET | `/api/backups/status` | `backups.view` | – | liest | Fortschritt laufender Backups |
| POST | `/admin/backups/jobs` | `backups.manage` | Formular | liest | Backup-Job anlegen |
| GET | `/admin/backups/jobs/{job_id}` | `backups.manage` | – | liest | Backup-Job bearbeiten (Recht für alle Server des Jobs) |
| POST | `/admin/backups/jobs/{job_id}` | `backups.manage` | Formular | liest | Backup-Job speichern (Stoppen nur mit den Steuerrechten) |
| POST | `/admin/backups/jobs/{job_id}/delete` | `backups.manage` | Formular | liest | Backup-Job löschen |
| POST | `/admin/backups/jobs/{job_id}/items` | `backups.manage` | Formular | liest | Server/Pfad hinzufügen (Recht für diesen Server) |
| POST | `/admin/backups/jobs/{job_id}/items/remove` | `backups.manage` | Formular | liest | Server/Pfad entfernen |
| POST | `/admin/backups/jobs/{job_id}/run` | `backups.run` | Formular | liest | Backup-Job in die Warteschlange stellen |
| POST | `/admin/backups/jobs/{job_id}/download` | `backups.run` | Formular | liest | Backup-Job ausführen und direkt herunterladen |
| GET | `/admin/backups/runs/{run_id}/download` | `backups.run` | – | liest | Gespeichertes Backup herunterladen |
| POST | `/api/backups/runs/{run_id}/cancel` | `backups.run` | Header | liest | Laufendes Backup abbrechen |
| POST | `/admin/backups/runs/{run_id}/verify` | `backups.run` | Formular | liest | Gespeichertes Backup vollständig lesen und alle Prüfsummen prüfen (ändert nichts) |
| GET | `/admin/backups/runs/{run_id}/restore` | `backups.restore` | – | liest | Wiederherstellen: Erklärung und Auswahl |
| POST | `/admin/backups/runs/{run_id}/restore` | `backups.restore` | Formular | schreibt | Backup auf den Server zurückspielen (eigener Ordner oder Originalort; Stoppen nur mit den Steuerrechten) |
| POST | `/api/backups/restores/{restore_id}/cancel` | `backups.restore` | Header | liest | Laufende Wiederherstellung abbrechen |
| POST | `/admin/backups/runs/{run_id}/delete` | `backups.manage` | Formular | liest | Gespeichertes Backup löschen |
| POST | `/admin/backups/runs/bulk` | `backups.manage` | Formular | liest | Mehrere gespeicherte Backups löschen (Prüfen mehrerer: Recht „Backups ausführen“) |

## Nur Owner

| Methode | Pfad | CSRF | Zweck |
|---|---|---|---|
| POST | `/servers/{ident}/admin/suspend` | Formular | Server im Panel sperren oder entsperren (nur Owner) |
| POST | `/servers/{ident}/admin/delete` | Formular | Server im Panel löschen (nur Owner, Name zur Bestätigung) |
| GET | `/setup` | – | Einrichtungsassistent (vor dem ersten Konto öffentlich) |
| POST | `/setup/legal` | Formular | Assistent: Vorlagen für Impressum/Datenschutz anlegen (bleiben aus) |
| GET | `/admin/connection` | – | System → Panel: Verbindung zum Panel (Pterodactyl, Pelican, Calagopus) |
| GET | `/admin/connection/wizard` | – | Verbindung reparieren / anderes Panel: Assistent mit frischem Entwurf |
| POST | `/admin/connection/check` | Formular | Assistent: Eingaben in den Entwurf und prüfen (nur lesend, speichert nichts) |
| POST | `/admin/connection/save` | Formular | Assistent: geprüften Entwurf speichern (nur mit Bestätigung) |
| POST | `/admin/connection/discard` | Formular | Assistent: Entwurf verwerfen |
| POST | `/admin/connection/panel` | Formular | Panel-Art, Panel-Adresse und Application-Key (älteres Formular) |
| POST | `/admin/connection/client` | Formular | Client-Key |
| POST | `/admin/connection/nodes` | Formular | Abweichende Wings-Adressen |
| POST | `/admin/connection/admin-key` | Formular | Optionalen Admin-Key (Schreibrechte) prüfen und speichern; die Prüfung ändert nichts am Panel |
| POST | `/admin/connection/admin-key/remove` | Formular | Admin-Key entfernen |
| GET | `/admin/servers` | – | Server → Alle Server (Wege zu Verwalten und Panel-Einstellungen) |
| GET | `/admin/servers/new` | – | Server → Server anlegen (Node und Egg wählen, dann Formular) |
| POST | `/admin/servers/new` | Formular | Server im Panel anlegen |
| GET | `/admin/ports` | – | Server → Ports der Nodes |
| POST | `/admin/ports` | Formular | Ports auf einem Node anlegen |
| POST | `/admin/ports/delete` | Formular | Freie Ports eines Nodes löschen |
| POST | `/admin/connection/options` | Formular | Modus (im Assistenten auch öffentliche Adresse, Proxy, Zeitzone) |
| GET | `/admin/users` | – | Benutzerliste |
| GET | `/admin/users/new` | – | Neue Benutzer: einladen und direkt anlegen |
| POST | `/admin/users` | Formular | Benutzer anlegen |
| POST | `/admin/users/invite` | Formular | Einladungslink erstellen |
| POST | `/admin/users/invites/{token_hash}/revoke` | Formular | Einladung zurückziehen |
| GET | `/admin/users/{user_id}` | – | Benutzer bearbeiten |
| GET | `/admin/users/{user_id}/export` | – | Daten eines Benutzers herunterladen |
| POST | `/admin/users/{user_id}/permissions` | Formular | Rechte pro Server vergeben |
| POST | `/admin/users/{user_id}/update` | Formular | Rolle, Ansicht und Notiz |
| POST | `/admin/users/{user_id}/active` | Formular | Konto deaktivieren oder wieder aktivieren |
| POST | `/admin/users/{user_id}/password` | Formular | Passwort eines Benutzers setzen |
| POST | `/admin/users/{user_id}/email` | Formular | E-Mail-Adresse eines Benutzers setzen oder entfernen |
| POST | `/admin/users/{user_id}/no-email` | Formular | Benutzer auf „ohne E-Mail-Adresse“ stellen |
| POST | `/admin/users/email-policy` | Formular | Benutzer ohne E-Mail-Adresse erlauben |
| POST | `/admin/users/{user_id}/delete` | Formular | Benutzer löschen |
| POST | `/admin/servers/{ident}/reviewed` | Formular | Hinweis auf neuen Server als erledigt markieren |
| GET | `/admin/groups` | – | Rechtegruppen |
| POST | `/admin/groups` | Formular | Rechtegruppe speichern |
| POST | `/admin/groups/starter` | Formular | Vorschlagsgruppen anlegen |
| POST | `/admin/groups/{group_id}/delete` | Formular | Rechtegruppe löschen |
| GET | `/admin/audit` | – | Audit-Log |
| GET | `/admin/audit/export` | – | Audit-Log als CSV/TXT |
| POST | `/admin/backups/targets` | Formular | Backup-Ziel anlegen (Laufwerk im Container oder SMB-Freigabe) |
| POST | `/admin/backups/targets/{target_id}` | Formular | Backup-Ziel ändern (SMB: Verbindung wird geprüft) |
| POST | `/admin/backups/targets/{target_id}/test` | Formular | Backup-Ziel testen |
| POST | `/admin/backups/targets/{target_id}/delete` | Formular | Backup-Ziel entfernen |
| GET | `/admin/appearance` | – | Community → Erscheinungsbild |
| POST | `/admin/branding` | Formular | Name, Standard-Theme, Discord-Link |
| POST | `/admin/branding/logo/{kind}` | Formular | Eigenes Logo hochladen |
| POST | `/admin/branding/logo/{kind}/delete` | Formular | Eigenes Logo entfernen |
| POST | `/admin/appearance/locale` | Formular | Standardsprache und Zeitzone |
| GET | `/admin/legal` | – | Community → Rechtliches |
| POST | `/admin/legal/{page}` | Formular | Impressum/Datenschutzerklärung |
| GET | `/admin/statuspage` | – | Statusseite einstellen |
| POST | `/admin/statuspage` | Formular | Statusseite speichern |
| POST | `/admin/statuspage/discord` | Formular | Discord-Kanal hinzufügen (eigene Server-Auswahl) |
| POST | `/admin/statuspage/discord/{feed_id}` | Formular | Discord-Kanal ändern |
| POST | `/admin/statuspage/discord/{feed_id}/delete` | Formular | Discord-Kanal entfernen |
| GET | `/admin/announcements` | – | Ankündigungen |
| POST | `/admin/announcements` | Formular | Ankündigung speichern |
| POST | `/admin/announcements/{ann_id}/delete` | Formular | Ankündigung löschen |
| GET | `/admin/system` | – | System → Zustand: Verbindung, Monitoring, Selbsttest |
| GET | `/admin/network` | – | System → Netzwerk |
| POST | `/admin/network` | Formular | Adresse von Hatchtower, öffentliche Adresse, Reverse-Proxy |
| GET | `/admin/mail` | – | E-Mail-Einstellungen |
| POST | `/admin/mail` | Formular | E-Mail-Einstellungen speichern |
| POST | `/admin/mail/password/delete` | Formular | SMTP-Passwort entfernen |
| POST | `/admin/mail/test` | Formular | Testmail senden |
| GET | `/admin/notify` | – | Benachrichtigungen |
| POST | `/admin/notify` | Formular | Schwellenwerte für Benachrichtigungen |
| POST | `/admin/notify/webhook` | Formular | Webhook hinzufügen (Titel, Beschreibung, Ereignisse, Server) |
| POST | `/admin/notify/webhook/{hook_id}` | Formular | Webhook ändern |
| POST | `/admin/notify/webhook/{hook_id}/delete` | Formular | Webhook entfernen |
| POST | `/admin/notify/webhook/{hook_id}/test` | Formular | Testnachricht an einen Webhook |
| GET | `/admin/data` | – | System → Daten: Aufbewahrung und Datenumzug |
| POST | `/admin/data/retention` | Formular | Aufbewahrungsfristen |
| POST | `/admin/data/export` | Formular | Alle Daten verschlüsselt exportieren (Passwort nötig) |
| POST | `/admin/data/import` | Formular | Daten importieren und ersetzen (Passwort nötig) |
| GET | `/admin/updates` | – | System → Updates |
| POST | `/admin/system/update-check` | Formular | Nach Updates suchen |
| POST | `/admin/system/update` | Formular | Update starten |
| GET | `/admin/system/update` | – | Update-Fortschritt |
| GET | `/api/system/update-status` | – | Update-Status |
