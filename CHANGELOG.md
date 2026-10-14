# Änderungen

Neue Einträge oben. Die Abschnitte „## <version>“ werden beim Veröffentlichen
automatisch als Beschreibung des GitHub-Releases übernommen (`tools/release.sh`).

## 0.29.1 – 2026-10-14

- **Backups:** Wiederherstellen, Löschen und andere Knöpfe sind wieder immer sichtbar. Sie erschienen teilweise erst
  beim Überfahren der Zeile (gilt auch für Automationen und Ankündigungen); nur die Dateiliste blendet Umbenennen und
  Löschen weiterhin erst beim Überfahren ein
- **README** gekürzt und auf das Wesentliche beschränkt, mit kurzer Entstehungsgeschichte; Screenshots der Übersicht
  in der Listenansicht

## 0.29.0 – 2026-10-14

**Freunde einladen in einem Schritt**
- **Einladung mit Zugriff:** Im Einladungsformular lassen sich eine Rechtegruppe und Server (oder „Alle Server, auch
  künftige“) festlegen. Wer den Link nutzt, sieht die Server direkt nach der Anmeldung – ohne dass der Owner danach
  noch Rechte vergeben muss. Offene Einladungen und das Audit-Log zeigen den Zugriff. Wurde die Gruppe inzwischen
  gelöscht, gibt es keinen Zugriff. Bei „Alle Server“ mit Steuer- oder Schreibrechten erscheint eine Warnung
- **Vorschlagsgruppen:** „Zuschauer“ (sehen, Adresse, Passwort), „Spieler“ (zusätzlich starten und neu starten,
  nicht stoppen) und „Moderator“ (zusätzlich stoppen, Konsole, Protokoll) mit einem Klick anlegen – unter
  Benutzer → Rechtegruppen und im Einladungsformular, solange noch keine Gruppe existiert
- **Verständliche Fehler beim Starten und Stoppen:** Statt „Pterodactyl antwortet mit HTTP 409 …“ steht da z. B.
  „Der Server ist gerade beschäftigt“ oder „Das Panel hat gerade zu viele Anfragen bekommen“. Owner sehen
  zusätzlich die technische Meldung, das Audit-Log bleibt vollständig

**Rechtesystem überprüft**
- Zeitpläne und Backup-Jobs folgen jetzt derselben Regel: Sie laufen nur, solange ihr Ersteller existiert und die
  nötigen Rechte für den Server hat (bisher liefen Zeitpläne eines gelöschten Erstellers weiter)
- Rechtegruppen zeigen, wer sie für „Alle Server, auch künftige“ nutzt; beim Löschen warnt die Bestätigung, dass
  diese Benutzer den Zugriff verlieren, und das Audit-Log vermerkt es
- Hinweise bei Rechten, die mehr erlauben, als der Name vermuten lässt: Backups starten/herunterladen und
  wiederherstellen (umgehen die Datei-Rechte), Herunterladen (Konfigurationsdateien), Befehle senden
- Benutzer ohne Owner-Rolle sehen in den Benachrichtigungen nur noch Meldungen, die sie auch bekommen können
- Neue Tests: Benutzer, der nur neu starten darf, gegen alle anderen Routen; Ersteller-Regeln; Gruppe löschen;
  Einladung mit Zugriff

## 0.28.3 – 2026-10-14

- SFTP-Port (2022) wird nicht mehr geprüft – weder im Selbsttest unter System → Zustand noch in der Wings-Übersicht
  oder im Einrichtungsassistenten. Hatchtower nutzt SFTP nicht; ein geschlossener Port sah bisher wie ein Fehler aus.
  Dateien, Konsole, Uploads und Downloads laufen weiterhin über den Wings-Daemon-Port

## 0.28.2 – 2026-10-14

- **Update erfolgreich:** Nach einem Update über die Oberfläche erscheint eine deutliche grüne Meldung mit der neuen
  Version, das Protokoll klappt sich ein. Schlägt das Update fehl, kommt die Meldung in Rot
- **Wings-Nodes unter System → Panel:** Jeder Node mit Adresse und Port, Verbindungsstand (verbunden, nicht erreichbar,
  antwortet nicht wie erwartet), Wartungsmodus und SFTP-Port. Unter System → Zustand zusätzlich „x von y erreichbar“
- Wings-Nodes werden gleichzeitig geprüft, ein nicht erreichbarer Node hält die Seiten nicht mehr mehrfach auf

## 0.28.1 – 2026-10-14

- `hatchtower github-token` übernimmt den Token jetzt auch, wenn er ohne Zeilenumbruch am Ende kommt (z. B. aus einer
  Datei oder Pipe). Bisher brach der Befehl dann ohne Meldung ab; gleiches im Proxmox-Installer
- README: Hinweis, falls `curl` fehlt, und wie der Token bei der Proxmox-Installation ohne Rückfragen gespeichert wird

## 0.28.0 – 2026-10-14

- **Backups wiederherstellen:** Gespeicherte Backups lassen sich direkt in Hatchtower auf den Server zurückspielen –
  wahlweise in einen eigenen Ordner (`/hatchtower-restore/<Datum>`, nichts wird überschrieben) oder an den
  ursprünglichen Ort (ersetzt gleichnamige Dateien, nur mit Bestätigung). Vorher wird die Backup-Datei geprüft; ist
  sie beschädigt, passiert am Server nichts. Auf Wunsch wird der Server vorher gestoppt und danach wieder gestartet.
  Fortschritt und Abbrechen auf der Backup-Seite, eigene Liste „Wiederherstellungen“
  - Neues Recht **„Backups wiederherstellen“**. Bestehende Rechtegruppen bekommen es nicht automatisch
- **Jedes Backup wird geprüft:** Nach dem Schreiben liest Hatchtower die ZIP-Datei komplett und prüft jede Datei gegen
  ihre Prüfsumme, erst dann gilt das Backup als fertig. „Prüfen“ holt das für ältere Backups nach. Die Liste zeigt
  „geprüft“, „ungeprüft“ und „wiederhergestellt“
- **Zuverlässigere Backups:**
  - Ist Wings nicht erreichbar, wird ein vor dem Backup gestoppter Server jetzt immer wieder gestartet (bisher konnte
    er aus bleiben)
  - Lässt sich keine einzige Datei sichern, ist das ein Fehler und es bleibt kein leeres ZIP liegen
  - Bricht ein Download ab, gibt es einen zweiten Versuch; mittendrin abgebrochene Dateien werden als
    „unvollständig“ gemeldet
  - Die Aufbewahrung löscht nie das neueste vollständige Backup – ein Backup mit Fehlern verdrängt das letzte gute nicht
  - Verpasste Zeitpläne (Hatchtower war aus) werden bis zu 6 Stunden später nachgeholt; kann ein geplantes Backup
    nicht starten, kommt eine Meldung
  - Während einer Wiederherstellung laufen auf demselben Server keine Backups, und die Datei lässt sich nicht löschen
- **Update:** Startet eine neue Version nicht, werden beim automatischen Rückfall jetzt auch Versionsnummer und
  Datenbank zurückgesetzt. Bisher hielt `hatchtower update` die fehlgeschlagene Version danach für installiert
- **Installationsskripte auf Englisch:** Proxmox-Installer, `get.sh`, `install.sh` und der Befehl `hatchtower update`
  sprechen jetzt immer Englisch, ebenso die Beschreibungen der systemd-Dienste und der Docker-Start
- **Oberfläche:**
  - „Benutzer löschen“ steht in einem eigenen, abgesetzten Bereich und nennt die Alternative „Konto deaktivieren“
  - Rechte eines Benutzers erscheinen in der Reihenfolge der Rechteliste statt alphabetisch nach internem Namen
  - Fehlende englische Texte ergänzt („Always“, „Never“, „Waiting“ u. a.), „1 day“ statt „1 days“, keine
    „Eintrag/Einträge“ mehr
  - Die Export-Seite nennt, dass Backup-Jobs, Backup-Ziele und Webhooks mitexportiert werden
- **README neu** (Englisch und Deutsch): für Einsteiger, mit Installation Schritt für Schritt, API-Keys, Rechten,
  Backups und Wiederherstellen, Fehlerbehebung und aktuellen Screenshots
- Netzwerkfehler beim Herunterladen einzelner Dateien führen zu einer verständlichen Meldung statt zu einem
  internen Fehler
- Hatchtower warnt im Log, wenn die Datenbank von einer neueren Version stammt

## 0.27.2 – 2026-10-14

- **Statusseite:** Die „Störungs- und Wartungshistorie“ ist von der öffentlichen Statusseite entfernt. Sie zeigte
  neben öffentlichen Ankündigungen auch erkannte Abstürze der Server – das gehört nicht auf eine öffentliche Seite.
  Aktive öffentliche Ankündigungen erscheinen weiterhin oben auf der Seite, die Verfügbarkeitsbalken bleiben unverändert.

## 0.27.1 – 2026-10-14

Ersetzt 0.27.0: Der öffentliche Demo-Modus („Demo ansehen“ auf der Anmeldeseite) ist wieder vollständig entfernt.

- **Neuer Einrichtungsassistent für die Verbindung:** Panel, Keys und Wings werden erst geprüft und dann gespeichert –
  und zwar nur nach ausdrücklicher Bestätigung. Bis dahin liegen die Eingaben verschlüsselt in einem Entwurf im
  Arbeitsspeicher (30 Minuten); an Panel, Wings und Servern ändert die Prüfung nichts (nur lesende Anfragen)
  - Jeder Schritt erklärt, warum er nötig ist; neuer Schritt „Prüfen und speichern“ zeigt, was gespeichert wird
    (Keys nur maskiert, beim Reparieren mit „vorher/nachher“), alle Prüfergebnisse und was damit funktioniert
  - Ergebnisse in drei Stufen: **Fehler** (Speichern gesperrt), **Warnung** (funktioniert eingeschränkt),
    **Optional** (nur Zusatzfunktion oder Hinweis) – jeweils mit konkreten Lösungsschritten
  - Automatisch geprüft: Erreichbarkeit des Panels (DNS, Firewall, Zertifikat, http/https, Weiterleitung, Login vor
    dem Panel), ob wirklich das gewählte Panel antwortet, vertauschte oder fremde Keys, Rechte des Application-Keys
    (Nodes, Servers, Eggs, Allocations), sichtbare Server und Teilrechte des Client-Keys (Status, Konsole, Dateien,
    Startvariablen, Aktivität), fehlende Schnittstellen älterer Panels, unverschlüsseltes http im Internet
  - Wings: Daemon-Port und ob dort wirklich Wings antwortet; nicht erreichbar ist eine Warnung, kein Fehler.
    SFTP wird nur noch als optionaler Hinweis gezeigt – Hatchtower braucht es nicht
  - Am Ende ein **Ergebnisbericht** mit Prüfung, gewählten Einstellungen und nächsten Schritten
- **System → Panel:** zeigt die gespeicherte Verbindung und „Zuletzt geprüft“, prüft auf Knopfdruck und öffnet den
  Assistenten zum Reparieren oder zum Wechsel auf ein anderes Panel; die alte Verbindung bleibt bis zum Speichern aktiv
- **Sicherheit:** Gespeicherte Keys werden nie an eine Panel-Adresse auf einem anderen Host (oder von https auf http)
  geschickt, auch nicht im älteren Panel-Formular. Ungültige Anfragen melden nur Feldnamen, nie die gesendeten Werte
- `hatchtower selftest` nutzt dieselbe Prüfung, zeigt Lösungsschritte und schlägt nur bei echten Fehlern fehl
- Bestehende Installationen: keine Änderung an Datenbank oder gespeicherter Verbindung nötig
- **Sicherheit:** Auch SMTP- und SMB-Passwörter gehen nur an den Server, für den sie eingegeben wurden. Bei neuem Host
  (SMTP auch beim Wechsel auf „ohne Verschlüsselung“) muss das Passwort neu eingegeben werden
- **Sicherheit:** Der Anmeldeschutz zählt IPv6-Adressen als /64-Netz. Links zum Zurücksetzen des Passworts verfallen
  bei Passwortwechsel, Sperre und neuer E-Mail-Adresse. Der CSV-Export des Audit-Logs entschärft Formeln (=, +, -, @)
- **Rechte:** Den Neustart nach Absturz schalten Benutzer nur noch für Server, die sie selbst starten dürfen. Die
  Datenauskunft enthält keine Einträge früherer Konten mit gleichem Namen oder gleicher ID. Logos sind auf
  4096 × 4096 Pixel begrenzt

- **Docker Compose:** Hatchtower lässt sich jetzt auch als Container betreiben (`docker compose up -d --build`).
  Schlüssel werden beim ersten Start im Volume angelegt, der Einrichtungscode steht im Log, Befehle gehen mit
  `docker compose exec hatchtower hatchtower …`. Die Update-Seite zeigt im Container den passenden Befehl
- **Serverseite mit Reitern:** Übersicht, Konsole, Log-Archiv und Protokoll stehen in Reitern statt untereinander auf
  einer langen Seite; Links wie `#protokoll` öffnen den passenden Reiter
- **Ruhigere Kopfzeile:** Sprache und Abmelden liegen im Kontomenü am eigenen Namen, die Kopfzeile ist auf dem
  Desktop einzeilig. Der Theme-Umschalter bleibt direkt sichtbar
- **Übersicht:** Statusfilter ohne Treffer werden ausgeblendet
- **Dateien:**
  - Ein Klick auf eine bearbeitbare Datei öffnet den Editor; Umbenennen und Löschen erscheinen am Desktop erst beim
    Überfahren der Zeile
  - Programme und andere Binärdateien bieten kein „Bearbeiten“ mehr an; Textdateien mit Null-Bytes am Ende
    (z. B. `steam_appid.txt`) lassen sich wieder bearbeiten
- **Einrichtung:** Im Schritt „Optionen“ wählt man die Ansicht für Benutzer, voreingestellt ist „Einfach“
- README neu: einfache Ansicht zuerst, Vergleich mit Subusern, Anleitung für Reverse-Proxy und Pangolin
  (`docs/reverse-proxy.md`)

## 0.26.0 – 2026-10-14

- **Einfache Ansicht:** Für alle, denen die volle Oberfläche zu viel ist. Pro Server stehen nur Status, Adresse,
  Passwort und große Knöpfe zum Starten und Stoppen da; Auslastung, Diagramme, Log-Archiv, Protokoll und
  Mehrfachauswahl sind ausgeblendet, die Konsole bleibt (mit Recht). Sortieren, Status- und Tag-Filter sowie
  Kacheln/Liste gibt es auch hier
  - Umschalten oben rechts in der Kopfzeile („Einfach | Alles“) oder im Konto unter „Ansicht“
  - Standard für Benutzer unter Auftritt → Erscheinungsbild; Owner sehen alles, solange sie nichts anderes wählen
  - Beim direkten Anlegen, in Einladungslinks und auf der Benutzerseite lässt sich die Ansicht vorgeben und auf
    Wunsch festlegen – dann kann der Benutzer nicht selbst umschalten
- **Bessere Kontraste:** Blasse Schrift erreicht in allen drei Themes den WCAG-Mindestkontrast (4,5:1), Kartenränder
  und Flächen heben sich deutlicher ab, im hellen Theme ist die Akzentschrift dunkler
- Auftritt: Auswahlfelder im Formular „Name und Design“ überlappen sich nicht mehr

## 0.25.1 – 2026-10-14

- **Lizenz:** Hatchtower steht jetzt unter der GNU AGPL-3.0. Der Lizenztext liegt als `LICENSE` im Paket und unter
  `/opt/hatchtower`, eine Zusammenfassung steht auf der Seite „Über Hatchtower“
- **Schlankeres Paket:** Entwickler-Werkzeuge sind nicht mehr enthalten; im Betrieb wird aus `tools/` nur noch das
  Skript für die Lizenzliste gebraucht. Beim Update werden die alten Werkzeuge unter `/opt/hatchtower/tools` entfernt
- README: Installation mit Zugangs-Token überarbeitet

## 0.25.0 – 2026-10-13

- **E-Mail-Adressen in der Benutzerverwaltung:** Die Benutzerliste zeigt die hinterlegten Adressen (mit „nicht bestätigt“, „ohne E-Mail-Adresse“ oder „E-Mail-Adresse fehlt“). Auf der Benutzerseite können Owner eine Adresse eintragen, ändern oder entfernen – mit Bestätigungslink oder direkt als bestätigt
  - Beim direkten Anlegen eines Benutzers lässt sich gleich eine Adresse angeben; sie bekommt einen Bestätigungslink
- **E-Mail-Pflicht einstellbar:** Ist E-Mail eingeschaltet, brauchen neue Benutzer (direkt angelegt oder per Einladung) eine Adresse – außer unter Benutzer ist „Benutzer ohne E-Mail-Adresse erlauben“ angehakt
  - Einzelne Benutzer lassen sich „ohne E-Mail-Adresse“ anlegen oder darauf umstellen; sie brauchen keine und können selbst keine hinterlegen
  - Ist E-Mail ausgeschaltet, sind alle Felder zum Hinzufügen gesperrt; vorhandene Adressen lassen sich weiterhin entfernen
- **Verständlichere E-Mail-Fehler:** Statt „Connection unexpectedly closed“ erklärt Hatchtower, ob die Anmeldung abgelehnt wurde (z. B. App-Passwort bei Gmail nötig), Port und Verschlüsselung nicht zusammenpassen oder der Absender abgelehnt wird
- **Absenderadresse optional:** Leer gelassen, ist die Login-Adresse der Absender; ein Hinweis erklärt, warum andere Absender oft nicht gehen
- Ein fehlgeschlagener Bestätigungslink zeigt keine „ausstehende Bestätigung“ mehr an
- **Kopfzeile:** Der eigene Name ist jetzt ein Knopf mit Zahnrad, der deutlich zu „Mein Konto und Einstellungen“ führt. Im Handy-Menü gibt es dafür eine eigene Kontokarte; Darstellung und Sprache brechen um, und „Abmelden“ wird nicht mehr abgeschnitten

## 0.24.0 – 2026-10-13

- **Mehrere Benachrichtigungs-Webhooks:** Unter System → Benachrichtigungen lassen sich beliebig viele Webhooks anlegen, jeder mit **Titel** und **Beschreibung** (z. B. welcher Discord-Kanal), eigenem Format (Discord oder JSON), eigenen Ereignissen und optional nur für bestimmte Server
  - Einzeln pausieren, testen und entfernen; der bisherige Webhook wird automatisch übernommen
  - Auch im Datenumzug (Export/Import) enthalten
- **Update-Fenster** (Automatisierungen): neue Zeitplan-Aktion, die einen Server an gewählten Wochentagen oder einmalig für eine Dauer (30 Minuten bis 12 Stunden) startet, damit er Spiel-Updates laden kann, und danach wieder stoppt
  - Greift nur, wenn der Server in dem Moment aus ist; wird er währenddessen in Hatchtower von Hand gestartet, bleibt er an
  - Braucht die Rechte „Starten“ und „Stoppen“
- **Einrichtung:** neuer optionaler letzter Schritt „Rechtliches“ – legt auf Wunsch die eigenen Vorlagen für Impressum und Datenschutzerklärung an (bleiben aus, bis sie ergänzt und eingeschaltet werden)
- **Rechtliches:** deutlicher Hinweis, dass die Vorlagen keine Rechtsberatung sind und ohne Gewähr für Richtigkeit oder Vollständigkeit
- **Netzwerk:** neuer Abschnitt „Eigene Domain verbinden“ (z. B. Server bei Hetzner, Domain bei Strato: A-Eintrag, Reverse-Proxy, Adresse) mit Prüfung, ob die Domain auf diesen Server zeigt; das Proxy-Feld heißt jetzt „IP des Reverse-Proxys“, damit es nicht mit der Domain verwechselt wird

## 0.23.0 – 2026-10-12

- **Protokoll pro Server:** Auf jeder Serverseite zeigt die neue Karte „Protokoll“, was am Server passiert ist – wer ihn wann gestartet, gestoppt oder neu gestartet hat, Abstürze, Statuswechsel (online/offline, auch wenn im Panel oder im Spiel ausgelöst), Konsolenbefehle, Dateiänderungen, Zeitpläne, Backups und geänderte Einstellungen
  - Filter nach Art (Start/Stop/Abstürze, Dateien, Konsole, Backups, Einstellungen und Zeitpläne), ältere Einträge lassen sich nachladen
  - Ohne IP-Adressen und ohne reine Lesezugriffe wie „Dateien aufgelistet“
  - Reicht so weit zurück wie das Audit-Log (Aufbewahrung unter System → Daten), Statuswechsel 90 Tage
- **Neues Recht „Protokoll sehen“** (Gruppe Allgemein), pro Benutzer und Server oder in Rechtegruppen vergebbar. Bestehende Benutzer und Gruppen bekommen es nicht automatisch

## 0.22.0 – 2026-10-12

- **Menü neu gegliedert:** Die Hauptleiste hat jetzt die Bereiche **Benutzer**, **Auftritt** und **System**, jeder mit eigenen Reitern
  - **Benutzer:** Benutzer, Rechtegruppen und Audit-Log
  - **Auftritt** (was Besucher und Spieler sehen): Erscheinungsbild, Statusseite, Ankündigungen und Rechtliches (Impressum, Datenschutzerklärung)
  - **System:** Zustand, Panel, Netzwerk, E-Mail, Benachrichtigungen, Daten und Updates
- **Netzwerk** (neu): Adresse von Hatchtower (bisher unter E-Mail), öffentliche Adresse für Spieler und Reverse-Proxy-IPs (bisher unter Verbindung) an einer Stelle
- **Erscheinungsbild** hat eine eigene Seite, mit Standardsprache und **Zeitzone** im Abschnitt „Sprache und Zeit“
- **Zeitzone:** Jede Auswahl zeigt den aktuellen Abstand zu UTC, z. B. „Europe/Berlin (UTC+2)“. Bei den Zeitplänen steht, in welcher Zeitzone sie laufen
- **Updates** haben einen eigenen Reiter; der Hinweis auf eine neue Version in der Leiste führt direkt dorthin
- **Daten:** Aufbewahrung, Export und Import auf einer Seite (bisher Datenschutz und Datenumzug)
- Der Link zur öffentlichen Statusseite steht nur noch in der Übersicht, nicht mehr zusätzlich in der Hauptleiste
- Doppelte Rücklinks wie „← System“ entfernt; die Reiterleiste führt jetzt überall zurück

## 0.21.3 – 2026-10-12

- **Übersicht: „Alle auswählen“** – ein Haken wählt alle gerade sichtbaren Server (passend zu Status- und Tag-Filter) für Start, Restart oder Stop auf einmal aus
- **Betrieb auf öffentlichen Servern** (VPS, Root-Server) abgesichert:
  - **Behoben:** Wer einen Benutzernamen kannte, konnte das Konto mit wenigen falschen Anmeldungen dauerhaft für alle sperren. Die Sperre gilt jetzt nur noch für die IP, von der die Fehlversuche kamen; eine Passwortänderung (auch per `hatchtower reset-password`) hebt sie auf
  - Läuft Hatchtower hinter einem Reverse-Proxy, der nicht eingetragen ist, zeigt es dem Owner eine Warnung. Ohne Eintrag werden HTTPS (geschützte Cookies, HSTS) und die echten Besucher-IPs nicht erkannt
  - Unter System → Verbindung lassen sich als Reverse-Proxy jetzt auch Netze eintragen (z. B. `172.16.0.0/12` für Docker)
  - Der Installer weist bei `--bind 127.0.0.1` auf den Reverse-Proxy hin, statt eine nicht erreichbare Adresse anzuzeigen
  - README: neuer Abschnitt „Auf einem öffentlichen Server betreiben“
- Auf dem Handy sind die Auswahl-Haken der Übersicht etwas größer

## 0.21.2 – 2026-10-12

- **Behoben:** Bei der Panel-Auswahl im Einrichtungsassistenten und bei der Modus-Auswahl (Nur lesen / Lesen und steuern) standen die Auswahlkreise über dem Text statt daneben
- **Behoben:** In der englischen Übersicht hieß der Statusfilter noch „wird gestartet / wird gestoppt“
- Getestete Versionen bei Pelican und Calagopus verständlicher („Panel/Wings 1.0.0-rc.1“)

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
