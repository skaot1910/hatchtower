<div align="center">

<img src="app/hatchtower/static/favicon.svg" width="88" alt="Hatchtower-Logo">

# Hatchtower

[English](README.md) · **Deutsch**

**Eine einfache, sichere Webseite für deine Gameserver – für dich und deine Freunde,
ohne dein Server-Panel ins Internet zu stellen.**

![Pterodactyl](https://img.shields.io/badge/Pterodactyl-1.15-0ea5e9)
![Pelican](https://img.shields.io/badge/Pelican-1.0-2563eb)
![Calagopus](https://img.shields.io/badge/Calagopus-1.2-f97316)
![Sprachen](https://img.shields.io/badge/Sprachen-DE%20%7C%20EN-22c55e)
![Lizenz](https://img.shields.io/badge/Lizenz-AGPL--3.0-8b5cf6)
![Built with Claude](https://img.shields.io/badge/Built%20with-Claude-d97757)

<img src="docs/screenshots/dashboard.png" alt="Die Hatchtower-Übersicht mit zwei Gameservern" width="100%">

<sub>Die Screenshots zeigen die englische Oberfläche; Hatchtower gibt es auf Deutsch und Englisch.</sub>

</div>

---

## Inhalt

1. [Was ist Hatchtower?](#1-was-ist-hatchtower)
2. [Was kann ich damit machen?](#2-was-kann-ich-damit-machen)
3. [Screenshots](#3-screenshots)
4. [Was brauche ich vor der Installation?](#4-was-brauche-ich-vor-der-installation)
5. [Installation](#5-installation)
6. [Erste Einrichtung](#6-erste-einrichtung)
7. [Panel verbinden (API-Keys erklärt)](#7-panel-verbinden-api-keys-erklärt)
8. [Server und Benutzer verwalten](#8-server-und-benutzer-verwalten)
9. [Dateien und Rechte](#9-dateien-und-rechte)
10. [Backups und Wiederherstellen](#10-backups-und-wiederherstellen)
11. [Hatchtower aktualisieren](#11-hatchtower-aktualisieren)
12. [Hilfe bei Problemen](#12-hilfe-bei-problemen)
13. [Sicherheitsempfehlungen](#13-sicherheitsempfehlungen)
14. [Häufige Fragen](#14-häufige-fragen)
15. [Hilfe bekommen und Fehler melden](#15-hilfe-bekommen-und-fehler-melden)
16. [Lizenz](#16-lizenz)

---

## 1. Was ist Hatchtower?

Viele betreiben Gameserver (Minecraft, Valheim, Terraria …) mit einem **Server-Panel** wie
[Pterodactyl](https://pterodactyl.io), [Pelican](https://pelican.dev) oder [Calagopus](https://calagopus.com).
Das Panel kann viel, ist aber für Admins gemacht. Freunden Zugang zu geben ist oft keine gute Idee: Sie sehen
viel zu viel, und das Panel müsste aus dem Internet erreichbar sein.

**Hatchtower ist eine kleine Webseite, die neben deinem Panel steht.** Es spricht im Heimnetz mit dem Panel und
zeigt jeder Person nur, was du erlaubst:

- Deine Freunde bekommen eine **einfache Seite**: Läuft der Server, wie lautet die Adresse, wie das Passwort –
  und große Knöpfe zum Starten oder Neustarten.
- Du (der **Owner**) bekommst alles andere: Konsole, Dateien, Backups, Zeitpläne, Rechte und ein Protokoll, wer
  was gemacht hat.

Dein Panel läuft unverändert weiter. Hatchtower ersetzt es nicht, sondern ist eine freundliche Haustür davor.

**Passt das für mich?**

| Hatchtower passt, wenn … | Du brauchst es eher nicht, wenn … |
|---|---|
| du Gameserver für Freunde oder eine kleine Community betreibst | nur du selbst das Panel benutzt |
| dein Panel nicht aus dem Internet erreichbar sein soll | deine Freunde mit den Subusern des Panels zufrieden sind |
| Freunde einen Server neu starten sollen, ohne dich zu fragen | du weder Pterodactyl noch Pelican noch Calagopus nutzt |
| du Backups willst, die sich mit wenigen Klicks zurückspielen lassen | |

> Eine Hatchtower-Installation verbindet sich mit **einem** Panel. Mehrere Wings-Nodes dahinter sind kein Problem.

## 2. Was kann ich damit machen?

**Für deine Freunde (einfache Ansicht)**
- Sehen, ob ein Server läuft, mit Adresse und Beitritts-Passwort
- Starten, stoppen oder neu starten – nur wenn du es erlaubst
- Funktioniert auf dem Handy

**Für dich als Owner**
- Übersicht aller Server mit CPU, RAM und Speicher live, Favoriten und Tags
- Serverseite mit Verlaufsdiagrammen, Verfügbarkeit über 30 Tage und **Live-Konsole**
- **Dateiverwaltung** im Browser: ansehen, hochladen, herunterladen (auch mehrere als ZIP), bearbeiten,
  umbenennen, löschen
- **Benutzer und Rechte** pro Server, wiederverwendbare **Rechtegruppen** („Spieler“, „Moderator“),
  „Alle Server, auch künftige“ und **Einladungslinks** statt Passwörter verschicken
- **Automationen**: Neustart nach Absturz, geplantes Starten/Stoppen/Neustarten mit Vorwarnung im Spiel
- **Backups** auf ein Laufwerk oder eine Netzwerkfreigabe (SMB), nach Zeitplan oder von Hand – jede
  Backup-Datei wird nach dem Schreiben geprüft und lässt sich mit wenigen Klicks **wiederherstellen**
- **Meldungen** an Discord (oder einen anderen Webhook) und per E-Mail: Absturz, Neustart fehlgeschlagen,
  Speicher fast voll, Backup fehlgeschlagen …
- **Audit-Log**: wer hat wann was gemacht (Export als CSV/TXT)
- **Öffentliche Statusseite** für Spieler (optional) und Statusmeldungen in Discord
- **Read-Only-Modus**: Hatchtower schaut dann nur zu und ändert garantiert nichts am Panel
- Eigener Name und Logo, drei Farbschemata (dunkel, mittel, hell), Deutsch und Englisch

## 3. Screenshots

Alle Screenshots zeigen die aktuelle Version mit einem Test-Panel und erfundenen Servern und Benutzern.
Adressen in den Bildern sind Beispiele.

**Das sehen deine Freunde – die einfache Ansicht**

<img src="docs/screenshots/simple.png" alt="Einfache Ansicht: Status, Adresse, Passwort, Neustart und Stopp" width="100%">

**Ein Server im Detail** – Status, Adresse, Verfügbarkeit, Ressourcen und Verlauf. Konsole, Log-Archiv und das
Protokoll des Servers stehen in den Reitern.

<img src="docs/screenshots/server.png" alt="Detailseite eines Servers" width="100%">

**Dateien** – direkt im Browser mit den Dateien des Servers arbeiten.

<img src="docs/screenshots/files.png" alt="Dateiverwaltung" width="100%">

**Rechte pro Benutzer und Server** – Gruppe wählen oder einzelne Rechte ankreuzen. Die farbigen Chips zeigen
genau, was die Person darf.

<img src="docs/screenshots/user-permissions.png" alt="Rechte eines Benutzers auf zwei Servern" width="100%">

**Backups** – Jobs, gespeicherte Backups mit Prüfergebnis und Wiederherstellen.

<img src="docs/screenshots/backups.png" alt="Backup-Seite" width="100%">

<table>
  <tr>
    <td width="62%"><img src="docs/screenshots/restore.png" alt="Ein Backup wiederherstellen"><br>
      <sub>Beim Wiederherstellen steht vorher da, was passiert – mit einer sicheren Variante, die nichts überschreibt.</sub></td>
    <td width="38%"><img src="docs/screenshots/mobile.png" alt="Hatchtower auf dem Handy"><br>
      <sub>Auf dem Handy.</sub></td>
  </tr>
</table>

**Helles Farbschema** – jeder kann oben rechts mit ☾ ◐ ☀ zwischen dunkel, mittel und hell wechseln.
Standard ist dunkel.

<img src="docs/screenshots/light-theme.png" alt="Übersicht im hellen Farbschema" width="60%">

## 4. Was brauche ich vor der Installation?

| Was | Details |
|---|---|
| **Ein Panel** | Pterodactyl, Pelican oder Calagopus, auf dem deine Gameserver schon laufen, und ein **Admin-Konto** dort (einmalig, um die API-Keys anzulegen). |
| **Ein kleiner Linux-Rechner für Hatchtower** | Debian 12 oder 13 (getestet), Ubuntu sollte auch gehen. Ideal ist ein Container auf Proxmox. 1 CPU-Kern, 512 MB RAM und 2 GB Speicher reichen. Alternativ jeder Rechner mit **Docker**. |
| **Netzwerk** | Der Hatchtower-Rechner muss dein **Panel** erreichen und den **Wings**-Dienst auf jedem Node (meist Port `8080`). Bei Calagopus mit Wings-Proxy reicht das Panel. |
| **Root-Zugang** | Die Installationsbefehle laufen als `root` (oder mit `sudo`). |

**Begriffe in dieser Anleitung**

- **Panel** – die Webseite, auf der du deine Gameserver verwaltest (Pterodactyl, Pelican oder Calagopus).
- **Wings / Node** – das Programm auf dem Rechner, der die Gameserver wirklich ausführt. Das Panel steuert es.
- **API-Key** – ein langes Passwort für Programme. Damit fragt Hatchtower beim Panel nach oder schickt Befehle.
  Du legst ihn im Panel an.
- **Shell / Terminal** – das Textfenster, in dem du auf einem Linux-Rechner Befehle eintippst
  (bei Proxmox: Host oder Container auswählen → *Shell*).
- **Reverse-Proxy** – ein Programm (z. B. Caddy, Nginx, Pangolin), das eine Webseite mit HTTPS aus dem Internet
  erreichbar macht. Nur nötig, wenn Freunde Hatchtower von unterwegs öffnen sollen.

> 🔒 **Das GitHub-Repository ist derzeit privat.** Die Ein-Zeilen-Befehle unten brauchen dann einen Lese-Token –
> siehe [Installation, solange das Repository privat ist](#installation-solange-das-repository-privat-ist).

## 5. Installation

Wähle **einen** Weg. Mit Proxmox nimm **A**, sonst **B**.

### A) Proxmox: neuer Container mit einem Befehl (empfohlen mit Proxmox)

Öffne die Shell deines **Proxmox-Hosts** (nicht eines Containers) und führe aus:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

Das Skript stellt ein paar Fragen auf Englisch (Container-ID, Netzwerk, Port …). **Enter** übernimmt den Vorschlag in
Klammern – die Vorschläge passen für die meisten Heimnetze. Danach legt es einen kleinen Debian-Container an und
installiert Hatchtower darin.

<details>
<summary>Ohne Rückfragen installieren</summary>

```bash
HT_YES=1 HT_CTID=120 HT_IP=192.168.1.50/24 HT_GW=192.168.1.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

Alle Variablen stehen oben in `proxmox/install.sh`.
</details>

### B) Ein vorhandener Debian- oder Ubuntu-Rechner (empfohlen ohne Proxmox)

Öffne eine Shell auf dem Rechner, auf dem Hatchtower laufen soll, und führe aus:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/installer/get.sh)"
```

Das Skript lädt die neueste Version, prüft die Prüfsumme und installiert sie.
Kommt `curl: command not found`, zuerst curl installieren: `apt-get install -y curl`.

<details>
<summary>Optionen (Port, welche IPs zugreifen dürfen)</summary>

| Option | Bedeutung |
|---|---|
| `--port 8000` | Port der Webseite (Standard 8000) |
| `--bind 127.0.0.1` | Nur lokal lauschen – wenn ein Reverse-Proxy auf demselben Rechner läuft |
| `--allow "192.168.1.5,192.168.1.10"` | Nur diese IP-Adressen dürfen die Seite öffnen (eigene kleine Firewall-Tabelle, deine übrigen Regeln bleiben unberührt) |

Anhängen nach einem Unterstrich: `bash -c "$(curl …)" _ --allow "192.168.1.5"`
</details>

### C) Aus einem heruntergeladenen Paket (ohne Internetzugang zu GitHub)

`hatchtower-<version>.tar.gz` unter **Releases** herunterladen, auf den Rechner kopieren, dann:

```bash
tar -xzf hatchtower-<version>.tar.gz
```

```bash
cd hatchtower-<version> && bash installer/install.sh
```

### D) Docker Compose (wenn du schon Docker nutzt)

Im Ordner mit dem Hatchtower-Quellcode (Git-Checkout oder entpacktes Release-Paket):

```bash
docker compose up -d --build
```

```bash
docker compose logs hatchtower
```

Im Log steht der **Einrichtungscode**. Dann `http://<dein-rechner>:8000/setup` öffnen.

- Alle Daten (Datenbank, Logos, Schlüssel) liegen im Docker-Volume `hatchtower-data`. **Sichere das ganze
  Volume** – die Datei `keys.env` darin wird gebraucht, um die gespeicherten API-Keys zu lesen.
- Für Backups einen Ordner unter `/mnt/…` in den Container einhängen (siehe Kommentar in `compose.yaml`).
- Befehle so: `docker compose exec hatchtower hatchtower reset-password`.
- Update: neue Version holen, dann `docker compose up -d --build`. Der Update-Knopf in Hatchtower gilt nur für
  die Wege A–C.

### So sieht Erfolg aus

Am Ende zeigt das Installationsskript (auf Englisch) etwa:

```
Hatchtower 0.28.0 is installed.

  1. Open in your browser:  http://192.168.1.50:8000/setup
  2. Setup code:            ABCD-EFGH-JKLM-NPQR   (valid for 24 hours, new one: hatchtower setup-token)
```

Also: **1.** diese Adresse im Browser öffnen, **2.** den Einrichtungscode („Setup code“) bereithalten. Er gilt 24 Stunden;
`hatchtower setup-token` erzeugt einen neuen.

### Installation, solange das Repository privat ist

Du brauchst einen **Lese-Token** vom Projekt. Einmal eingeben (wird nicht angezeigt):

```bash
read -rsp "GitHub token: " GITHUB_TOKEN && export GITHUB_TOKEN HATCHTOWER_SAVE_TOKEN=1 && echo
```

Dann **statt** des Befehls aus A oder B einen von diesen:

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/proxmox/install.sh)"
```

```bash
bash -c "$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/skaot1910/hatchtower/contents/installer/get.sh)"
```

Der erste ist für **A** (Proxmox-Host), der zweite für **B**. Der Token wird auf dem Hatchtower-Rechner
gespeichert, damit spätere Updates klappen (bei **A** fragt das Skript vorher – Enter heißt ja; ohne Rückfragen
`HT_SAVE_TOKEN=1` ergänzen). Abgelaufen oder nicht gespeichert? `hatchtower github-token`.

<details>
<summary>Token anlegen (für den Repository-Inhaber)</summary>

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
Repository: nur `hatchtower`, **Contents**: „Read-only“, Ablaufdatum setzen.
</details>

## 6. Erste Einrichtung

Öffne die Adresse aus dem Installationsskript (`http://<adresse>:8000/setup`). Ein Assistent führt Schritt für
Schritt durch. Jeder Schritt hat ein **„Warum?“**, das erklärt, wofür er da ist.

1. **Dein Owner-Konto** – Einrichtungscode eingeben, Benutzername und starkes Passwort (mind. 15 Zeichen)
   wählen. Der Owner darf alles, also gut aufbewahren.
2. **Panel** – Pterodactyl, Pelican oder Calagopus wählen, Adresse des Panels und den ersten API-Key eintragen.
   Der Assistent zeigt genau, wo du im Panel klicken musst. Details im nächsten Abschnitt.
3. **Client-Key** – ein zweiter Key zum Steuern der Server (bei Calagopus nicht nötig).
4. **Wings** – Hatchtower findet deine Nodes selbst und prüft, ob es sie erreicht.
5. **Prüfen und speichern** – Hatchtower testet alles, **ohne etwas an deinem Panel zu ändern**, und zeigt das
   Ergebnis in klaren Worten: *Fehler* (muss behoben werden), *Warnung* (geht eingeschränkt) oder *Optional*.
   Erst nach deiner Bestätigung werden die Keys verschlüsselt gespeichert.
6. **Optionen** – nur lesen oder lesen und steuern, die Adresse für Spieler, die Zeitzone und welche Ansicht neue
   Benutzer bekommen (einfach oder alles). Alles ist später änderbar.

> 💡 Zur Sicherheit startet Hatchtower im **Read-Only-Modus**: Es zeigt alles, ändert aber nichts. Wenn alles
> passt, unter **System → Panel → Modus** auf **„Lesen und steuern“** umstellen.

<img src="docs/screenshots/connection-wizard.png" alt="Der Assistent erklärt, wie man den API-Key bekommt" width="80%">

## 7. Panel verbinden (API-Keys erklärt)

Hatchtower braucht die Erlaubnis, mit deinem Panel zu sprechen. Diese Erlaubnis gibst du mit API-Keys. Der
Assistent zeigt die Schritte auch, mit den passenden Bezeichnungen für dein Panel.

### Pterodactyl und Pelican: zwei Keys

**1. Application-API-Key (nur lesen)** – damit liest Hatchtower die Liste der Server und Nodes.

- Pterodactyl: **Zahnrad** (Admin-Bereich) → **Application API** → **Create New**.
- Pelican: in den **Admin-Bereich** → **API Keys** → neuen Key anlegen.
- Beschreibung z. B. „Hatchtower“. **Alle Rechte auf „Read“** – mehr braucht es nicht.
- Key kopieren. Er beginnt mit `ptla_` (Pterodactyl) und wird nur einmal angezeigt.

**2. Client-API-Key** – damit startet und stoppt Hatchtower Server, liest die Konsole und arbeitet mit Dateien.

1. Im Panel einen **eigenen Benutzer** nur für Hatchtower anlegen (z. B. `hatchtower-service`) mit langem
   Zufallspasswort.
2. Ihn entweder zum **Administrator** machen (dann sieht Hatchtower alle Server, auch neue) **oder** – vorsichtiger –
   nur als **Subuser** zu den gewünschten Servern hinzufügen.
3. Als dieser Benutzer anmelden, im Konto **API Credentials** (Pterodactyl) bzw. **API Keys** (Pelican) öffnen
   und einen Key erstellen.
4. Bei **Allowed IPs** die IP-Adresse deines Hatchtower-Rechners eintragen. Dann funktioniert der Key nur von dort.
5. Key kopieren (bei Pterodactyl beginnt er mit `ptlc_`).

> ⚠️ Ein Client-Key darf alles, was sein Benutzer darf – das Panel kann ihn nicht weiter einschränken. Deshalb:
> eigener Benutzer, **Allowed IPs** setzen, Benutzer mit Zwei-Faktor-Anmeldung schützen.

### Calagopus: ein Key

1. Einen eigenen Benutzer für Hatchtower mit Admin-Rechten und langem Zufallspasswort anlegen.
2. Als dieser Benutzer anmelden, im Konto **API Keys** öffnen und einen Key erstellen.
3. Rechte: **Admin** – Lesezugriff auf Nodes und Server; **Server** – alles außer `sftp` und `query-raw`.
   Bei **Allowed IPs** die IP deines Hatchtower-Rechners eintragen.
4. Die Admin-Rechte nutzt Hatchtower nur lesend.

### Wo die Keys liegen

Die Keys werden **verschlüsselt** auf dem Hatchtower-Rechner gespeichert und nie wieder vollständig angezeigt –
auch nicht für Owner. Später ändern: **System → Panel → Reparieren oder anderes Panel verbinden**. Die bisherige
Verbindung bleibt aktiv, bis du die neue speicherst.

## 8. Server und Benutzer verwalten

### Server

- **Übersicht** zeigt alle Server, die du sehen darfst, als Kacheln oder Liste. Nach Status oder Tag filtern,
  Favoriten markieren.
- **Start / Stopp / Neustart** fragen vorher nach. **Kill** (hartes Beenden, nur Owner) fragt zweimal, weil das
  Spiel dabei nicht speichern kann.
- **Einfach ↔ Alles** oben rechts schaltet die Ansicht um. Als Owner legst du den Standard unter **Auftritt →
  Erscheinungsbild** fest oder legst einen Benutzer auf eine Ansicht fest.

### Benutzer

Es gibt zwei Rollen:

- **Owner** – darf alles, auch Benutzer und Einstellungen verwalten.
- **Benutzer** – darf nur, was du freigibst, und zwar **pro Server**.

**Jemanden einladen:** **Benutzer → Per Einladungslink**. Gültigkeit und wie oft der Link benutzt werden darf
wählen, Link verschicken (z. B. in Discord). Die Person wählt Benutzername und Passwort selbst – du hast mit
ihrem Passwort nie zu tun.

**Rechte vergeben:** Benutzer öffnen → **Rechte pro Server**. Pro Server eine **Gruppe** (z. B. „Spieler“) wählen
oder einzelne Rechte ankreuzen. „Alle Server, auch künftige“ gibt automatisch Zugriff auf neue Server. Die
Zusammenfassung darunter sagt in Worten, was die Person sieht.

**Rechtegruppen** (Benutzer → Rechtegruppen) sind wiederverwendbare Pakete, zum Beispiel:

| Gruppe (Beispiel) | Typische Rechte |
|---|---|
| Nur ansehen | Server, Adresse und Status sehen |
| Spieler | + Passwort sehen, starten, neu starten |
| Moderator | + stoppen, Konsole, Dateien, Automationen, Backups |

Unter **„Was bedeutet welches Recht?“** auf der Benutzerseite ist jedes Recht erklärt. Hatchtower prüft jedes
Recht **auf dem Server** bei jeder Anfrage – ein ausgeblendeter Knopf ist nicht der einzige Schutz. Wer nur neu
starten darf, kommt auch mit selbst gebauten Anfragen nicht an Dateien, Konsole oder Backups (für jede Seite und
Aktion automatisch getestet).

**Vorschau als …** zeigt die Seite genau so, wie diese Person sie sieht.

## 9. Dateien und Rechte

Die Dateiverwaltung (**Dateien** auf der Serverseite) arbeitet über Panel und Wings, wie die des Panels selbst.
Was jemand darf, hängt an diesen Rechten:

| Recht | Erlaubt |
|---|---|
| Dateien ansehen | Ordner und Dateien auflisten |
| Herunterladen | Dateien herunterladen (mehrere als ZIP) |
| Hochladen | Dateien hochladen (bis 200 MB pro Upload) |
| Bearbeiten | Textdateien im Browser ändern – die vorige Fassung bleibt als `.bak` und lässt sich mit einem Klick zurückholen |
| Umbenennen, Ordner anlegen, Löschen | was der Name sagt |

Einen SFTP-Zugang bietet Hatchtower **nicht** an. Pfade bleiben immer im Ordner des jeweiligen Servers.

## 10. Backups und Wiederherstellen

### So funktioniert es

1. **Backup-Ziel** (nur Owner): wohin die Backup-Dateien kommen – ein Laufwerk im Hatchtower-Rechner
   (z. B. Proxmox-Mountpoint, USB-Platte oder NFS unter `/mnt`, `/media` oder `/srv`) oder eine **SMB-Freigabe**
   (Windows-Freigabe, NAS). Anlegen unter **Backups → Backup-Ziele**. *Testen* schreibt und löscht eine kleine
   Datei.
2. **Backup-Job**: was gesichert wird (ganzer Server oder ausgewählte Ordner – auch direkt im Datei-Explorer
   ankreuzbar), wohin (das Ziel) und wann (von Hand oder nach Zeitplan, z. B. täglich 04:00). Auf Wunsch wird der
   Server vorher gestoppt (mit Vorwarnung im Spiel) und danach wieder gestartet – manche Spiele schreiben ihre
   Spielstände nur beim Beenden sauber.
3. Jeder Lauf erzeugt **eine ZIP-Datei pro Server** auf dem Ziel, in Ordnern nach Job und Server, z. B.
   `Spielstaende/Terraria/2026-10-14_04-00.zip`.

### Was „fertig“, „geprüft“ und „wiederhergestellt“ bedeuten

| Anzeige | Bedeutung |
|---|---|
| **fertig** | Die ZIP-Datei wurde vollständig geschrieben. |
| **geprüft** | Hatchtower hat die Datei danach komplett gelesen, jede enthaltene Datei passt zu ihrer Prüfsumme. Jedes neue Backup wird automatisch geprüft; *Prüfen* wiederholt das jederzeit. |
| **mit Fehlern** | Einzelne Dateien fehlen oder sind unvollständig. Die Liste steht in der Zeile und in `FEHLER.txt` im ZIP. |
| **fehlgeschlagen** | Nichts Brauchbares gesichert. Konnte keine einzige Datei gelesen werden, behält Hatchtower kein leeres ZIP. |
| **wiederhergestellt** | Dieses Backup wurde mindestens einmal erfolgreich auf einen Server zurückgespielt. |

Eine geprüfte Datei ist unbeschädigt – ob das Backup alles enthält, was du brauchst, zeigt aber erst eine
Wiederherstellung. Probier ab und zu eine in einen eigenen Ordner; das überschreibt nichts.

### Ein Backup wiederherstellen

**Backups → gespeichertes Backup → Wiederherstellen.** Du wählst:

- **In einen eigenen Ordner (sicher)** – die Dateien landen in `/hatchtower-restore/<Datum>/` auf dem Server.
  Nichts wird überschrieben. Im Datei-Explorer ansehen und verschieben, was du brauchst.
- **An den ursprünglichen Ort** – jede Datei aus dem Backup ersetzt die gleichnamige. Änderungen seit dem Backup
  (z. B. Spielfortschritt) gehen verloren. Vorher muss eine Bestätigung angekreuzt werden. Dateien, die nicht im
  Backup sind, bleiben unverändert – es wird nichts gelöscht.

Auf Wunsch stoppt Hatchtower den Server vorher und startet ihn danach wieder (für den Originalort empfohlen).
Bevor etwas kopiert wird, wird die Backup-Datei geprüft; ist sie beschädigt, passiert am Server nichts. Den
Fortschritt siehst du auf der Backup-Seite und kannst dort abbrechen. Wird Hatchtower währenddessen neu gestartet,
die Wiederherstellung einfach noch einmal starten.

Dafür braucht es das Recht **„Backups wiederherstellen“** (zum Stoppen zusätzlich Start/Stopp). Owner haben es
automatisch.

### Aufbewahrung, Speicherplatz und Schutz

- **Aufbewahren: Anzahl / Tage** löscht ältere Backups automatisch, pro Server. Das **neueste Backup und das
  neueste vollständige bleiben immer**, ein Backup mit Fehlern verdrängt also nie das letzte gute.
- Vor jedem Lauf prüft Hatchtower den freien Platz am Ziel und bricht mit klarer Meldung ab, wenn er nicht reicht.
- Ziele auf derselben Platte wie Hatchtower sind als **„Systemplatte“** markiert: Sie schützen nicht vor einem
  Plattendefekt und können sie volllaufen lassen. Besser ein eigenes Laufwerk oder ein NAS.
- Konnte ein geplantes Backup nicht laufen, weil Hatchtower aus war (Update, Stromausfall), wird es **nachgeholt**,
  bis 6 Stunden nach der geplanten Zeit. Kann ein Job nicht starten oder schlägt er fehl, kommt eine Meldung
  (einrichten unter **System → Benachrichtigungen**; pro Job „nur bei Fehlern“, „immer“ oder „nie“).
- Wer was darf: *Backups sehen*, *Backups starten und herunterladen*, *Backups wiederherstellen* und
  *Backup-Jobs verwalten* sind eigene Rechte. Backup-Ziele (Ordner und NAS-Passwörter) sind Owner-Sache.

### Hatchtower selbst sichern

Hatchtowers eigene Daten (Benutzer, Rechte, Einstellungen, verschlüsselte Keys) sind eine Datenbank unter
`/var/lib/hatchtower/hatchtower.db`. Eine Kopie entsteht **jede Nacht um 03:30** in `/var/backups/hatchtower/`
(14 Tage) und **vor jedem Update**.

⚠️ Die gespeicherten API-Keys und Passwörter lassen sich nur mit dem Schlüssel aus
`/etc/hatchtower/hatchtower.env` lesen. Die nächtliche Kopie enthält diese Datei **nicht**, und beides liegt auf
demselben Rechner. Am einfachsten sicherst du mit **System → Daten → Datenumzug: Exportieren**: eine
passwortgeschützte Datei mit allem (Benutzer, Rechte, Server, Zeitpläne, Backup-Jobs, Einstellungen und Keys).
Bewahre sie woanders auf. Für einen Umzug Hatchtower neu installieren und im Einrichtungsassistenten
**Importieren** wählen.

## 11. Hatchtower aktualisieren

Hatchtower sucht zweimal am Tag nach neuen Versionen und zeigt dann einen Punkt an **System**. Unter
**System → Updates** auf **Update auf … installieren** klicken oder auf dem Hatchtower-Rechner ausführen:

```bash
hatchtower update
```

Was passiert: Die neue Version wird geladen und ihre Prüfsumme geprüft, die **Datenbank gesichert**, die neue
Version installiert und gestartet. Startet sie nicht, geht Hatchtower **automatisch zurück** auf die vorherige
Version und die gesicherte Datenbank. Benutzer, Rechte, Einstellungen, Logos und Verlauf bleiben erhalten. Es wird
nie etwas ohne deinen Auftrag aktualisiert.

Docker: neue Version holen, dann `docker compose up -d --build`.

## 12. Hilfe bei Problemen

| Problem | Was tun |
|---|---|
| **Die Seite öffnet sich nicht** | Adresse und Port prüfen (Standard `8000`). Läuft der Dienst? `systemctl status hatchtower`. Mit `--allow` installiert? Dann dürfen nur die eingetragenen IPs zugreifen. |
| **Einrichtungscode falsch oder abgelaufen** | Auf dem Hatchtower-Rechner neu erzeugen: `hatchtower setup-token` (nur solange es noch kein Konto gibt). |
| **API-Key abgelehnt (401 oder 403)** | Richtigen Key ins richtige Feld? Application-Keys beginnen mit `ptla_`, Client-Keys mit `ptlc_`. Steht die Hatchtower-IP bei **Allowed IPs**? Wurde der Key im Panel gelöscht? Reparieren unter **System → Panel**. |
| **Server erscheinen, aber Konsole, Dateien oder Downloads gehen nicht** | Hatchtower erreicht **Wings** nicht (meist Port `8080` am Node). Firewalls prüfen. Ist der Node von Hatchtower aus unter einer anderen Adresse erreichbar, im Assistenten beim Schritt **Wings** eintragen. |
| **Start/Stopp-Knöpfe fehlen** | Hatchtower steht auf Read-Only (**System → Panel → Modus**) oder dem Benutzer fehlt das Recht. |
| **Backup: „Keine einzige Datei ließ sich sichern“** | Hatchtower sieht die Dateien, kann sie aber nicht von Wings laden – gleiche Ursache wie oben. |
| **Backup-Ziel: „Hatchtower darf in … nicht schreiben“** | Bei einem Proxmox-Mountpoint im unprivilegierten Container den `chown`-Befehl aus der Meldung (steht auch unter *Backups → Backup-Ziele → Laufwerk, NFS oder USB-Platte einhängen*) auf dem **Proxmox-Host** ausführen. |
| **Backup: „Der Ziel-Ordner … liegt auf der Systemplatte – ist das Laufwerk eingehängt?“** | USB-Platte bzw. Mountpoint ist nicht eingehängt. Einhängen und beim Ziel *Testen* klicken. |
| **Wiederherstellen: „zu groß für das Upload-Limit von Wings“** | Eine einzelne Datei ist größer als das Upload-Limit des Nodes (Pterodactyl-Standard: 100 MB). Im Panel beim Node das *Upload Limit* erhöhen oder die Datei von Hand kopieren. |
| **Owner-Passwort vergessen** | Auf dem Hatchtower-Rechner: `hatchtower reset-password` (entsperrt das Konto auch). |
| **Etwas anderes** | `hatchtower selftest` prüft die Verbindung und erklärt Probleme. Log des Dienstes: `journalctl -u hatchtower -f`. |

## 13. Sicherheitsempfehlungen

- **Von außen nur mit HTTPS.** Hatchtower selbst spricht nur HTTP. Sollen Freunde es aus dem Internet erreichen,
  einen Reverse-Proxy mit HTTPS davor setzen (Caddy, Nginx, Traefik, Pangolin …) und dessen IP unter
  **System → Netzwerk → Reverse-Proxy-IPs** eintragen. Schritt für Schritt:
  [Reverse-Proxy und Pangolin](docs/reverse-proxy.de.md).
- **Das Panel privat lassen.** Nur Hatchtower muss von außen erreichbar sein.
- **Den Client-Key schützen**: eigener Panel-Benutzer, **Allowed IPs**, Zwei-Faktor-Anmeldung. Noch vorsichtiger:
  den Benutzer nur als Subuser ausgewählter Server statt als Administrator.
- **Die Einrichtung gleich nach der Installation abschließen.** Bis das Owner-Konto existiert, kann jeder mit dem
  Einrichtungscode es anlegen.
- **Nur so viele Rechte wie nötig.** Mit „Nur ansehen“ oder „Spieler“ anfangen, später erweitern.
- **Ab und zu ins Audit-Log schauen** (Benutzer → Audit-Log).

Was Hatchtower dafür tut: sichere Passwort-Hashes (Argon2id), Sperre nach Fehlversuchen, Sitzungs-Timeouts,
CSRF-Schutz, strenge Content Security Policy, Rechteprüfung auf dem Server bei jeder Anfrage, technisch
erzwungener Read-Only-Modus und verschlüsselte Ablage von API-Keys und Passwörtern.

<details>
<summary><b>Betrieb auf einem öffentlichen Server (VPS, Root-Server)</b></summary>

1. Nur lokal lauschen, wenn der Reverse-Proxy auf demselben Rechner läuft:
   `bash installer/install.sh --bind 127.0.0.1` – sonst Port 8000 nur für den Proxy öffnen.
2. HTTPS über den Proxy, z. B. Caddy (holt das Zertifikat selbst):

   ```
   hatchtower.example.com {
     reverse_proxy 127.0.0.1:8000
   }
   ```

3. Proxy-IP unter **System → Netzwerk → Reverse-Proxy-IPs** eintragen (Caddy auf demselben Rechner:
   `127.0.0.1`). Erst dann setzt Hatchtower sichere Cookies und sieht die echten Besucher-IPs – sonst könnte ein
   Angreifer mit zehn falschen Passwörtern alle aussperren.
4. Hatchtower über ein VPN (z. B. WireGuard) mit Panel und Wings verbinden, statt den Wings-Port ins Internet
   zu öffnen.

</details>

## 14. Häufige Fragen

**Brauchen meine Freunde ein Konto im Panel?**
Nein. Sie bekommen nur ein Hatchtower-Konto (per Einladungslink). Das Panel bleibt deins.

**Ersetzt Hatchtower mein Panel?**
Nein. Server, Eggs, Nodes und Panel-Benutzer legst du weiter im Panel an. Hatchtower liest das und setzt eigene
Benutzer und Rechte obendrauf.

**Was passiert im Read-Only-Modus?**
Hatchtower zeigt alles (Status, Konsole, Dateien, Downloads, Backups), ändert aber nie etwas im Panel – der Code
blockiert jede schreibende Anfrage. Gut zum Ausprobieren.

**Kann Hatchtower mehrere Panels verwalten?**
Eine Installation verbindet sich mit einem Panel. Wechseln geht später (System → Panel), Benutzer, Rechte und
Zeitpläne bleiben erhalten.

**Wo liegen meine Backups?**
Auf dem Backup-Ziel, das du angelegt hast: ein Ordner auf einem Laufwerk des Hatchtower-Rechners oder eine
SMB-Freigabe. Jedes Backup ist eine normale ZIP-Datei, die du auch am PC öffnen kannst.

**Zeigt Hatchtower die Spielerzahl?**
Nein. Das klappt nicht zuverlässig für jedes Spiel, deshalb bietet Hatchtower es nicht an.

**Welche Sprachen gibt es?**
Deutsch und Englisch. Hatchtower folgt der Browsersprache; jeder kann im Kontomenü umschalten. Die
Installationsskripte sprechen immer Englisch.

**Ist es kostenlos?**
Ja, unter der AGPL-3.0 (siehe unten).

## 15. Hilfe bekommen und Fehler melden

1. `hatchtower selftest` ausführen und **System → Zustand** sowie **System → Panel → Verbindung prüfen** ansehen –
   die meisten Verbindungsprobleme werden dort erklärt.
2. [Hilfe bei Problemen](#12-hilfe-bei-problemen) lesen.
3. Ein Issue auf GitHub öffnen – mit Hatchtower-Version (`hatchtower version`), Panel und dessen Version, was du
   gemacht hast, was du erwartet hast und was passiert ist. **Niemals API-Keys, Passwörter oder den Inhalt von
   `/etc/hatchtower/hatchtower.env` posten.**

<details>
<summary>Nützliche Befehle auf dem Hatchtower-Rechner</summary>

| Befehl | Was er tut |
|---|---|
| `hatchtower update` | Neueste Version installieren |
| `hatchtower update --check` | Nur nachsehen, ob es ein Update gibt |
| `hatchtower update <datei.tar.gz>` | Ein heruntergeladenes Paket installieren |
| `hatchtower selftest` | Verbindung zu Panel und Wings prüfen (nur lesend) |
| `hatchtower version` | Installierte Version anzeigen |
| `hatchtower reset-password` | Passwort eines Benutzers zurücksetzen und Konto entsperren |
| `hatchtower create-owner` | Owner-Konto auf der Kommandozeile anlegen |
| `hatchtower setup-token` | Neuer Einrichtungscode (nur solange es kein Konto gibt) |
| `hatchtower github-token` | Lese-Token für Updates speichern, solange das Repository privat ist (`--remove` löscht ihn) |
| `journalctl -u hatchtower -f` | Log des Dienstes live verfolgen |

| Pfad | Inhalt |
|---|---|
| `/opt/hatchtower` | Das Programm |
| `/etc/hatchtower/hatchtower.env` | Interne Schlüssel und Port – **nie weitergeben** |
| `/var/lib/hatchtower/hatchtower.db` | Datenbank mit Benutzern, Einstellungen und verschlüsselten API-Keys |
| `/var/backups/hatchtower/` | Nächtliche Datenbank-Kopie (14 Tage) und Kopien vor Updates |

</details>

<details>
<summary>Für Entwickler</summary>

- Code: `app/hatchtower/` (FastAPI, Jinja2-Vorlagen, reines JavaScript). Panel-Adapter: `ptero.py` (Pterodactyl,
  Pelican) und `calagopus.py`. Backups: `backups.py`, `routes_backup.py`.
- Tests: `pytest tests` – mit einer Rechte-Matrix, die jede Route prüft (`tests/routes.py` →
  [`docs/rechte-matrix.md`](docs/rechte-matrix.md)).
- Code-Kommentare und Commits sind deutsch. Der deutsche Oberflächentext ist der Übersetzungsschlüssel, Englisch
  steht in `app/hatchtower/locales/en.json`.

</details>

## 16. Lizenz

Hatchtower ist freie Software unter der **GNU Affero General Public License v3.0** (AGPL-3.0), siehe
[`LICENSE`](LICENSE). Copyright © 2026 skaot1910.

Kurz gesagt: Du darfst es nutzen, verändern und weitergeben, auch kommerziell. Veränderte Fassungen müssen wieder
unter der AGPL-3.0 stehen – auch wenn du sie nur über ein Netzwerk anbietest. Es gibt keine Gewährleistung.

Alle Abhängigkeiten stehen unter freien Lizenzen (MIT, BSD, Apache-2.0, PSF, MPL-2.0). Die Liste steht in der
Oberfläche unter **Lizenzen** (Fußzeile), die vollständigen Texte in `THIRD_PARTY_LICENSES.txt` auf dem Server.

🤖 Hatchtower ist gemeinsam mit [Claude](https://claude.ai) (Anthropic) entstanden.

<div align="center">
<sub>Hatchtower ist ein unabhängiges Projekt und gehört nicht zu Pterodactyl, Pelican oder Calagopus.</sub>
</div>
