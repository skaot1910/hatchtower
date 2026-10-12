<div align="center">

<img src="app/hatchtower/static/favicon.svg" width="88" alt="Hatchtower-Logo">

# Hatchtower

**Die schöne, sichere Oberfläche für deine Game-Server auf Pterodactyl, Pelican oder Calagopus.**

Server starten, Konsole mitlesen, Dateien bearbeiten, Rechte vergeben –
alles an einem Ort, für dich und dein Team.

![Pterodactyl](https://img.shields.io/badge/Pterodactyl-1.15-0ea5e9)
![Pelican](https://img.shields.io/badge/Pelican-1.0-2563eb)
![Calagopus](https://img.shields.io/badge/Calagopus-1.2-f97316)
![Debian](https://img.shields.io/badge/Debian-12%20%7C%2013-a80030)
![Sprachen](https://img.shields.io/badge/Sprachen-DE%20%7C%20EN-22c55e)

<img src="docs/screenshots/dashboard.png" alt="Übersicht aller Server in Hatchtower" width="100%">

</div>

---

## Was ist Hatchtower?

Du betreibst Game-Server mit **Pterodactyl**, **Pelican** oder **Calagopus** und möchtest Freunden,
Moderatoren oder deiner Community Zugriff geben – aber nicht gleich auf alles? Genau dafür gibt es Hatchtower.

Hatchtower ist ein **Begleiter** für dein Panel, **kein Ersatz**. Das Panel bleibt
die Stelle, an der deine Server wirklich laufen. Hatchtower legt sich darüber und bringt:

- 👥 **eigene Benutzer** mit genau den Rechten, die du ihnen gibst – pro Server
- 📈 **Live-Monitoring** mit Verlauf von CPU, RAM und Netzwerk
- ⏰ **Automationen** wie Neustart bei Absturz oder nächtliche Neustarts
- 💾 **Backups** von ganzen Servern oder einzelnen Spielständen – auf ein Laufwerk, eine SMB-Freigabe oder direkt in den Browser
- 📜 ein **Audit-Log**, das festhält, wer wann was getan hat
- 🔒 einen **Nur-Lesen-Modus**, mit dem garantiert nichts am Panel verändert wird

### Unterstützte Panels

| Panel | Getestet mit | Anbindung |
|---|---|---|
| [Pterodactyl](https://pterodactyl.io) | Panel 1.15.1, Wings 1.13.3 | Application-Key (nur lesen) und Client-Key |
| [Pelican](https://pelican.dev) | Panel und Wings 1.0.0-rc.1 | Application-Key (nur lesen) und Client-Key |
| [Calagopus](https://calagopus.com) | Panel und Wings 1.2.5 (integrierte oder eigene Wings) | ein API-Key mit Admin-Lese- und Server-Rechten |

Eine Hatchtower-Installation verwaltet die Server **eines** Panels. Welches, wählst du im
Einrichtungsassistenten; wechseln kannst du später unter **System → Verbindung**.

> **In 5 Minuten startklar:** Ein Befehl installiert alles, danach führt dich ein Assistent im
> Browser Schritt für Schritt durch die Einrichtung.

---

## 🥚 Warum eigentlich „Hatchtower“?

Pterodactyl ist bekanntlich ein Flugsaurier. Und wie es sich für einen ordentlichen Saurier
gehört, ist in seiner Welt alles voller Urzeit-Biologie: Die Server-Vorlagen heißen **Eggs**,
die liegen in **Nests**, und der Dienst, der alles antreibt, heißt **Wings**. 🦖

Irgendjemand muss auf diese ganzen Eier aufpassen. Also: ein Turm. 🗼

- **Hatch** – das Schlüpfen. Aus einem Egg wird ein laufender Server, und jemand sollte
  dabei zusehen, ob er gesund schlüpft oder nachts um drei abstürzt.
- **Tower** – der Wachturm. Von oben hat man den besten Blick auf alle Nester, sieht sofort,
  wer gerade schlüpft, wer schläft und wer schon wieder Unfug in der Konsole treibt.

Zusammen: **Hatchtower**, der Wachturm über der Brutstation. Deshalb ist das Logo auch ein Ei
mit einem Riss. Es schlüpft gerade, keine Sorge, das ist so gewollt. 🐣

---

## Ein Blick hinein

### Alle Server auf einen Blick

Liste oder Kacheln, nach Status oder eigenen Tags filtern, Favoriten oben anheften – und
mehrere Server mit einem Klick gleichzeitig starten, stoppen oder neu starten.

<img src="docs/screenshots/dashboard-tiles.png" alt="Kachelansicht im hellen Theme" width="100%">

### Jeder Server im Detail

Status, Adresse, Passwort, Online-Zeit der letzten 30 Tage, ein Verlauf über bis zu 72 Stunden
und die **Live-Konsole** – inklusive Suche und Download als Textdatei.

<img src="docs/screenshots/server.png" alt="Detailseite eines Servers mit Verlauf und Live-Konsole" width="100%">

### Dateien direkt im Browser

Ansehen, hochladen, herunterladen (auch mehrere als ZIP), umbenennen, löschen und bearbeiten.
Beim Speichern bleibt die alte Fassung als `.bak` liegen – ein Klick, und sie ist wieder da.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/files.png" alt="Dateiverwaltung"></td>
    <td width="50%"><img src="docs/screenshots/editor.png" alt="Datei bearbeiten"></td>
  </tr>
</table>

### Benutzer und Rechte – so fein, wie du willst

Lege Gruppen wie „Moderator“ oder „Spieler“ an und weise sie pro Server zu. Wer nur sehen soll,
ob der Server läuft, sieht genau das – und sonst nichts. Neue Leute lädst du per
**Einladungslink** ein, ganz ohne Passwörter zu verschicken.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/users.png" alt="Benutzerverwaltung"></td>
    <td width="50%"><img src="docs/screenshots/groups.png" alt="Rechtegruppen"></td>
  </tr>
</table>

### Automationen

Stürzt ein Server ab, startet Hatchtower ihn auf Wunsch neu. Zeitpläne (wöchentlich oder
einmalig) starten, stoppen oder starten Server neu – mit Vorwarnung im Spiel, z. B.
„Neustart in 15 Minuten!“.

<img src="docs/screenshots/automation.png" alt="Automationen und Zeitpläne" width="100%">

### Backups

Sichere ganze Server oder nur das, was wirklich zählt – zum Beispiel die Spielstände. Dateien und
Ordner kreuzt du einfach im Datei-Explorer an und fügst sie einem Backup-Job hinzu. Gespeichert
wird als ZIP auf einem Laufwerk (z. B. Proxmox-Mountpoint, USB, NFS), auf einer SMB-Freigabe
(Windows, NAS) oder direkt als Download im Browser. Auf Wunsch wird der Server vorher gestoppt –
mit Vorwarnung im Spiel – und danach wieder gestartet.

### Audit-Log

Jede Aktion wird festgehalten und lässt sich nicht nachträglich verändern. Filtern nach Aktion,
Benutzer und Server, Export als CSV oder TXT.

<img src="docs/screenshots/audit.png" alt="Audit-Log" width="100%">

### Öffentliche Statusseite, Handy und Login

Eine Statusseite für deine Spieler (abschaltbar, ohne Passwörter und ohne Steuerung).
Auf dem Handy passt sich Hatchtower automatisch an.

<table>
  <tr>
    <td width="62%"><img src="docs/screenshots/status.png" alt="Öffentliche Statusseite"><br><br>
                    <img src="docs/screenshots/login.png" alt="Anmeldung"></td>
    <td width="38%"><img src="docs/screenshots/mobile.png" alt="Ansicht auf dem Handy"></td>
  </tr>
</table>

<sub>Alle Screenshots stammen aus einer Demo-Instanz mit erfundenen Servern und Benutzern.</sub>

---

## Alle Funktionen

<details>
<summary><b>Komplette Liste aufklappen</b></summary>

**Server**
- Übersicht als Liste oder Kacheln, Statusfilter, Sortierung, Live-Werte (CPU bezogen aufs Limit, RAM, Speicher)
- Start, Stop, Restart, Kill (nur Owner), Konsolenbefehle
- Mehrere Server auf einmal starten, stoppen oder neu starten
- Live-Konsole und Verlauf von CPU, RAM und Netzwerk (live bis 72 Stunden)
- Favoriten und eigene Tags
- Verfügbarkeit: Online-Zeit und Abstürze der letzten 30 Tage
- Log-Archiv: Konsole laufender Server wird mitgeschrieben (1–90 Tage oder aus), durchsuchbar, Fehler farbig, Download als .txt (eigenes Recht „Konsolen-Archiv sehen“)

**Dateien**
- Ansehen, herunterladen (auch mehrere als ZIP), hochladen, bearbeiten, umbenennen, löschen
- Automatische `.bak`-Sicherung beim Bearbeiten, Wiederherstellen mit einem Klick

**Benutzer und Sicherheit**
- Eigene Benutzer und Rechte pro Server, selbst erstellte Rechtegruppen
- Einladungslinks statt Startpasswort: 1–90 Tage oder unbegrenzt gültig, einmal bis unbegrenzt nutzbar, mit interner Beschreibung; Nutzung und Ablauf stehen im Audit-Log
- Optional E-Mail: eigene Adresse pro Benutzer mit Bestätigung, „Passwort vergessen“, Einladungen per Mail
- Audit-Log, das sich nicht verändern lässt
- Read-Only-Modus: Hatchtower kann komplett nur lesend betrieben werden

**Automationen und Benachrichtigungen**
- Neustart bei Absturz
- Wöchentliche und einmalige Zeitpläne mit Vorwarnung
- Benachrichtigungen per Discord/Webhook und E-Mail: Absturz, Neustart, Neustart fehlgeschlagen,
  Speicher fast voll, RAM dauerhaft hoch, Zeitplan fehlgeschlagen

**Backups**
- Backup-Jobs für einen, mehrere oder alle Server (auch künftige), ganze Server oder ausgewählte Dateien und Ordner, mit Ausschlüssen (z. B. `logs/`, `*.log`)
- Dateien und Ordner direkt im Datei-Explorer zu einem Backup-Job hinzufügen
- Ziele: Laufwerk im Hatchtower-Container (z. B. Proxmox-Mountpoint, USB, NFS), SMB-Freigabe (Windows, NAS, Samba) oder Direktdownload im Browser
- Von Hand oder per Zeitplan, mit Fortschrittsanzeige; laufende Backups lassen sich abbrechen
- Server vorher stoppen (mit Vorwarnung im Spiel) und danach wieder starten
- Aufbewahrung nach Anzahl oder Tagen, Platzprüfung vorab, Meldung per Webhook/E-Mail
- Eigene Rechte: Backups sehen, starten und herunterladen, Backup-Jobs verwalten
- Schont das Anfragen-Limit des Panels, damit Statusabfragen nicht ausfallen

**Community**
- Öffentliche Statusseite (abschaltbar): Name, Spiel, Status und Adresse ausgewählter Server
- Server-Status in Discord, auch in mehreren Kanälen – jeder mit eigener Server-Auswahl und Überschrift
- Ankündigungen oben auf jeder Seite, mit geplantem Beginn und Ende

**Aussehen und Sprache**
- 3 Themes (Dunkel, Mittel, Hell), pro Benutzer gespeichert
- Eigener Name und eigene Logos (z. B. die deiner Community)
- Deutsch und Englisch, automatisch nach Browsersprache oder per Umschalter
- Optimiert für Handy und Tablet

**Datenschutz und Umzug**
- Einstellbare Aufbewahrung (Audit-Log Standard 30 Tage)
- Impressum und Datenschutzerklärung mit Vorlagen, Datenauskunft pro Benutzer
- Datenumzug: alle Benutzer, Rechte, Logos und Einstellungen verschlüsselt exportieren und in einer neuen Instanz einspielen

</details>

---

## Was du brauchst

| | |
|---|---|
| **System** | Debian 12 oder 13 (gerne als Proxmox-LXC) |
| **Leistung** | 1 CPU-Kern, 512 MB RAM, 2 GB Speicher reichen |
| **Netzwerk** | Zugriff auf das Panel **und** die Wings-Server (Daemon-Port meist `8080`, SFTP meist `2022`; bei Calagopus mit Wings-Proxy reicht das Panel) |
| **Panel** | Pterodactyl, Pelican oder Calagopus mit einem Admin-Zugang, um die API-Keys anzulegen (siehe [Unterstützte Panels](#unterstützte-panels)) |

---

## Installation

Wähle **einen** der drei Wege.

### 🅰 Auf Proxmox VE – ein Befehl

In der Shell des Proxmox-Hosts (als root):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

Das Skript fragt Container-ID, Hostname, CPU, RAM, Speicher, Netzwerk und Port ab – jeweils mit
Vorschlag, **Enter** übernimmt ihn. Danach legt es einen Debian-Container an, lädt die neueste
Version, prüft die Prüfsumme und installiert alles.

<details>
<summary>Ohne Rückfragen installieren</summary>

```bash
HT_YES=1 HT_CTID=120 HT_IP=192.168.1.50/24 HT_GW=192.168.1.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

Alle Variablen stehen oben in `proxmox/install.sh`.
</details>

### 🅱 Auf einem vorhandenen Debian/Ubuntu-Server oder LXC

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/installer/get.sh)"
```

Optionen hängst du so an: `bash -c "$(curl …)" _ --allow "192.168.1.5"`

### 🅲 Aus einem Paket (ohne Internetzugriff auf GitHub)

Lade `hatchtower-<version>.tar.gz` unter **Releases** herunter, dann:

```bash
tar -xzf hatchtower-<version>.tar.gz
cd hatchtower-<version>
sudo bash installer/install.sh
```

**Optionen für den Installer**

| Option | Bedeutung |
|---|---|
| `--port 8000` | Port der Oberfläche |
| `--bind 0.0.0.0` | Adresse, auf der Hatchtower lauscht |
| `--allow "192.168.1.5,192.168.1.10"` | Port nur für diese IPs öffnen (eigene nftables-Tabelle, deine Firewall bleibt unberührt) |

<details>
<summary><b>Repository ist privat? So geht's mit GitHub-Token</b></summary>

Solange das Repository privat ist, braucht der Download einen Lese-Token.

**Token anlegen:** GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**. Als Repository nur `hatchtower` wählen, unter
*Permissions* bei **Contents** „Read-only“ einstellen. Der Token darf damit nur lesen und nur
dieses eine Repository.

**Installieren** (der Token wird nicht angezeigt und landet nicht im Verlauf):

```bash
read -rsp "GitHub-Token: " GITHUB_TOKEN && export GITHUB_TOKEN && echo
```

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/proxmox/install.sh)"
```

Für Weg 🅱 stattdessen die Adresse
`https://api.github.com/repos/skaot1910/hatchtower/contents/installer/get.sh` verwenden.

Für spätere Updates hinterlegst du den Token auf dem Server mit `hatchtower github-token`.
Ist das Repository öffentlich, braucht es keinen Token mehr.
</details>

---

## Einrichtung im Browser

Am Ende der Installation siehst du die **Adresse** und einen **Einrichtungscode**. Öffne die
Adresse im Browser – ein Assistent führt dich durch fünf Schritte:

1. **Owner-Konto** anlegen (mit dem Einrichtungscode)
2. **Panel** wählen (Pterodactyl, Pelican oder Calagopus), **Adresse** und **API-Key** eintragen –
   der Assistent zeigt für jedes Panel, wo es den Key gibt und welche Rechte er braucht
3. **Client-API-Key** eines eigenen Panel-Benutzers eintragen (mit *Allowed IP*; entfällt bei Calagopus)
4. **Wings-Server** prüfen – werden automatisch aus dem Panel gelesen, mehrere sind kein Problem
5. **Optionen** wählen: Modus, öffentliche Adresse für Spieler, Reverse-Proxy, Zeitzone

Fertig! 🎉

> 💡 Neue Installationen starten sicherheitshalber im Modus **Nur lesen**. Umstellen kannst du das
> jederzeit unter **System → Verbindung bearbeiten → Modus und Optionen**.
>
> API-Keys werden verschlüsselt gespeichert und nie wieder angezeigt.

**Eigener Look:** Unter **System → Erscheinungsbild** hinterlegst du den Namen und die Logos
deiner Community. Theme und Sprache wählt jeder Benutzer selbst oben rechts.

---

## Update

```bash
hatchtower update
```

Das war's. Hatchtower lädt die neueste Version, prüft die Prüfsumme, **sichert vorher die
Datenbank** und kehrt automatisch zur alten Version zurück, falls die neue nicht startet.
Benutzer, Rechte, Logos, Zeitpläne und Verlauf bleiben erhalten.

Unter **System** zeigt Hatchtower an, wenn eine neue Version bereitsteht. Installiert wird nie
automatisch – nur, wenn du es willst.

---

## Befehle auf dem Server

| Befehl | Was er tut |
|---|---|
| `hatchtower update` | Neueste Version installieren |
| `hatchtower update --check` | Nur nachsehen, ob es ein Update gibt |
| `hatchtower update <paket>` | Ein vorhandenes Paket installieren |
| `hatchtower selftest` | Verbindung zu Panel und Wings prüfen (nur lesend) |
| `hatchtower version` | Installierte Version anzeigen |
| `hatchtower reset-password` | Passwort eines Benutzers neu setzen und Konto entsperren |
| `hatchtower create-owner` | Owner-Konto auf der Konsole anlegen |
| `hatchtower setup-token` | Neuen Einrichtungscode erzeugen (nur solange noch kein Konto existiert) |
| `hatchtower github-token` | Lese-Token für ein privates Repository hinterlegen (`--remove` entfernt ihn) |
| `journalctl -u hatchtower -f` | Log des Dienstes live ansehen |

<details>
<summary>Wo liegt was?</summary>

| Pfad | Inhalt |
|---|---|
| `/opt/hatchtower` | Programm, Python-Umgebung, Installer, Werkzeuge |
| `/etc/hatchtower/hatchtower.env` | interne Schlüssel und Port (nicht weitergeben!) |
| `/var/lib/hatchtower/hatchtower.db` | Datenbank (SQLite) inkl. verschlüsselter API-Keys |
| `/var/backups/hatchtower/` | tägliches Backup um 03:30 (14 Tage) und Backups vor Updates |
| `/etc/hatchtower/github-token` | optional: Lese-Token für Updates aus einem privaten Repository |

</details>

---

## Sicherheit

- 🔑 Starke Passwörter (Argon2id), Sperre nach Fehlversuchen, Sitzungs-Timeouts
- 🛡️ CSRF-Schutz, strenge Content-Security-Policy, kein Einbetten in fremde Seiten
- 🚫 „Alles verboten, was nicht erlaubt ist“ – Rechte pro Benutzer und Server
- 👀 Der Nur-Lesen-Modus ist technisch erzwungen: Schreibende Anfragen erreichen das Panel gar nicht erst
- 🌐 Für den Zugriff von außen bitte einen Reverse-Proxy mit HTTPS nutzen (z. B. Pangolin, Nginx, Traefik)

> ⚠️ Der Client-Key eines Panel-**Admins** darf technisch alles. Schütze ihn mit *Allowed IP*,
> Nur-Lesen-Modus und Audit-Log. Noch vorsichtiger: Lege einen eigenen Panel-Benutzer an und füge
> ihn nur als Subuser zu den gewünschten Servern hinzu.

<details>
<summary><b>Auf einem öffentlichen Server (VPS, Root-Server) betreiben</b></summary>

Hatchtower läuft auch auf einem Server im Internet. Damit es dort sicher ist:

1. **Nur auf localhost lauschen**, wenn der Reverse-Proxy auf demselben Server läuft:
   `sudo bash installer/install.sh --bind 127.0.0.1`. Sonst Port 8000 per Firewall
   (oder `--allow`) nur für den Proxy öffnen. Der Proxy darf sich nicht umgehen lassen.
2. **HTTPS über einen Reverse-Proxy.** Hatchtower selbst spricht nur HTTP. Beispiel mit Caddy
   (holt das Zertifikat automatisch):

   ```
   hatchtower.example.de {
     reverse_proxy 127.0.0.1:8000
   }
   ```

3. **Einrichtung sofort abschließen.** Solange kein Konto existiert, kann jeder mit dem
   Einrichtungscode den Owner anlegen. Den Code also nicht weitergeben und gleich nach der
   Installation einrichten.
4. **Proxy-IP eintragen** unter **System → Verbindung → Reverse-Proxy-IPs** (bei Caddy auf
   demselben Server: `127.0.0.1`). Das ist im Internet Pflicht, nicht optional: Erst dann
   - erkennt Hatchtower HTTPS und setzt die Cookies mit `Secure` sowie HSTS,
   - sieht es die echten IPs der Besucher. Ohne Eintrag kommen alle von der Proxy-IP, und ein
     Angreifer mit zehn falschen Anmeldungen sperrt damit alle Besucher aus.

   Erlaubt sind IP-Adressen und Netze (z. B. `172.16.0.0/12` für Docker). `X-Forwarded-For` wird nur von diesen
   Adressen angenommen, gefälschte Angaben anderer Clients zählen nicht.
5. **Verbindung zum Panel und zu Wings:** Hatchtower muss das Panel und die Wings (meist Port
   `8080`) erreichen. Liegen sie nicht im selben Rechenzentrum, verbinde die Server lieber über
   einen VPN-Tunnel (z. B. WireGuard), statt den Wings-Port offen ins Internet zu stellen.
   Im Panel beim Client-Key *Allowed IP* auf die Adresse von Hatchtower setzen.

</details>

---

## Für Entwickler

<details>
<summary>Werkzeuge und Aufbau</summary>

| Werkzeug | Zweck |
|---|---|
| `bash tools/release.sh` | Neue Version veröffentlichen: Tag, Paket aus Git, Prüfsumme, GitHub-Release (Text aus `CHANGELOG.md`) |
| `tools\build-release.ps1` | Testpaket aus dem Arbeitsordner bauen → `dist\hatchtower-<version>.tar.gz` |
| `tools\deploy.ps1 [-Target host]` | Paket bauen und per `hatchtower update` auf einen Server ausrollen |
| `tools/smoketest.sh` | Rauchtest auf dem Server, eigene Test-Datenbank, immer nur lesend |
| `tools/devserver.sh` | Test-Instanz auf Port 8001 für Oberflächentests (per SSH-Tunnel) |

**Neue Version veröffentlichen:** Version in `app/hatchtower/__init__.py` und `VERSION` erhöhen,
Abschnitt in `CHANGELOG.md` ergänzen, committen, dann in Git Bash `bash tools/release.sh`.

**Aufbau** (`app/hatchtower/`): `main.py` (Web), `panels.py` (unterstützte Panels),
`ptero.py` (Adapter für Pterodactyl und Pelican mit Read-Only-Sperre), `calagopus.py` (Adapter für Calagopus), `wings.py` (Konsole), `monitor.py` (Abgleich, Verlauf), `automation.py`,
`permissions.py`, `security.py`, `setup.py` (Einrichtung), `themes.py`, `db.py` (Migrationen).

**Übersetzungen** liegen in `app/hatchtower/locales/<sprache>.json` (deutscher Text → Übersetzung).
Fehlt eine Übersetzung, erscheint der deutsche Text.

</details>

---

## Lizenz

Die Lizenz von Hatchtower selbst ist noch nicht festgelegt.

Alle Abhängigkeiten stehen unter freien, permissiven Lizenzen (MIT, BSD-3-Clause, Apache-2.0,
PSF-2.0, HPND/MIT-CMU, MPL-2.0 für certifi). Hatchtower enthält keine fremden Schriften, Icons,
Bilder oder JavaScript-Bibliotheken; die Panels werden nur über ihre API angebunden. Die verwendeten
Bibliotheken und ihre Lizenzen findest du in der Oberfläche unter **Lizenzen** (Fußzeile) und als
vollständige Texte in `THIRD_PARTY_LICENSES.txt`, die bei jeder Installation neu erzeugt wird.

<div align="center">
<sub>Hatchtower ist ein unabhängiges Projekt und steht in keiner Verbindung zu den Projekten Pterodactyl, Pelican oder Calagopus.</sub>
</div>
