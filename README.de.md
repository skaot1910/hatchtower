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
2. [Funktionen](#2-funktionen)
3. [Screenshots](#3-screenshots)
4. [Voraussetzungen](#4-voraussetzungen)
5. [Installation](#5-installation)
6. [Erste Einrichtung](#6-erste-einrichtung)
7. [API-Keys](#7-api-keys)
8. [Benutzer und Rechte](#8-benutzer-und-rechte)
9. [Backups](#9-backups)
10. [Aktualisieren](#10-aktualisieren)
11. [Hilfe bei Problemen](#11-hilfe-bei-problemen)
12. [Sicherheit](#12-sicherheit)
13. [Häufige Fragen](#13-häufige-fragen)
14. [Lizenz](#14-lizenz)

---

## 1. Was ist Hatchtower?

Gameserver-Panels wie [Pterodactyl](https://pterodactyl.io), [Pelican](https://pelican.dev) und
[Calagopus](https://calagopus.com) sind für Admins gemacht. Freunde sehen dort zu viel, und das Panel müsste
ins Internet.

**Hatchtower ist eine kleine Webseite neben deinem Panel.** Es spricht im Heimnetz mit dem Panel und zeigt
jeder Person nur, was du erlaubst: Freunde bekommen eine einfache Seite mit Status, Adresse, Passwort und
Neustart-Knopf – du bekommst Konsole, Dateien, Backups, Zeitpläne und Rechte. Dein Panel bleibt, wie es ist.

**Wie es dazu kam:** Meine Freunde sollten unsere Gameserver auch von unterwegs bedienen können, also habe ich
das Pterodactyl-Panel aus dem Internet erreichbar gemacht. Die Seite lud zwar, aber Knöpfe reagierten nicht
zuverlässig, und Konsole und Dateiübertragung hakten – trotz Reverse-Proxy- und WebSocket-Einstellungen. Dazu
kommt, dass das komplette Panel Freunden ohnehin viel zu viel zeigt. Statt das Panel freizugeben, entstand
deshalb Hatchtower: eine kleine, sichere Haustür, hinter der das Panel im Heimnetz bleibt.

> Eine Hatchtower-Installation verbindet sich mit **einem** Panel (mit beliebig vielen Wings-Nodes).

## 2. Funktionen

- **Einfache Ansicht** für Freunde: Status, Adresse, Beitritts-Passwort, Starten/Neustarten – auch am Handy
- **Übersicht** mit CPU, RAM und Speicher live, Favoriten und Tags; Serverseite mit Verlauf, Verfügbarkeit und **Live-Konsole**
- **Dateiverwaltung**: ansehen, hochladen, herunterladen (auch als ZIP), bearbeiten, umbenennen, löschen
- **Rechte pro Benutzer und Server**, wiederverwendbare Rechtegruppen und **Einladungslinks**
- **Automationen**: Neustart nach Absturz, geplantes Starten/Stoppen/Neustarten mit Vorwarnung im Spiel
- **Backups** auf ein Laufwerk oder eine SMB-Freigabe, nach dem Schreiben geprüft und mit wenigen Klicks **wiederherstellbar**
- **Meldungen** per Discord/Webhook und E-Mail, **Audit-Log**, optionale **öffentliche Statusseite**
- **Read-Only-Modus**, eigener Name und Logo, dunkles/mittleres/helles Farbschema, Deutsch und Englisch

## 3. Screenshots

Alle Screenshots zeigen ein Test-Panel mit erfundenen Servern und Benutzern.

**Die einfache Ansicht – das sehen deine Freunde**

<img src="docs/screenshots/simple.png" alt="Einfache Ansicht: Status, Adresse, Passwort, Neustart und Stopp" width="100%">

**Serverdetails** – Status, Ressourcen, Verlauf; Konsole und Protokolle in den Reitern.

<img src="docs/screenshots/server.png" alt="Detailseite eines Servers" width="100%">

**Dateien**

<img src="docs/screenshots/files.png" alt="Dateiverwaltung" width="100%">

**Rechte pro Benutzer und Server**

<img src="docs/screenshots/user-permissions.png" alt="Rechte eines Benutzers auf zwei Servern" width="100%">

**Backups**

<img src="docs/screenshots/backups.png" alt="Backup-Seite" width="100%">

<table>
  <tr>
    <td width="62%"><img src="docs/screenshots/restore.png" alt="Ein Backup wiederherstellen"><br>
      <sub>Ein Backup wiederherstellen.</sub></td>
    <td width="38%"><img src="docs/screenshots/mobile.png" alt="Hatchtower auf dem Handy"><br>
      <sub>Auf dem Handy.</sub></td>
  </tr>
</table>

**Helles Farbschema** (umschalten mit ☾ ◐ ☀ oben rechts)

<img src="docs/screenshots/light-theme.png" alt="Übersicht im hellen Farbschema" width="60%">

## 4. Voraussetzungen

- **Ein Panel** (Pterodactyl, Pelican oder Calagopus) mit Admin-Konto, um die API-Keys anzulegen
- **Ein kleiner Linux-Rechner**: Debian 12/13 (Ubuntu sollte gehen), z. B. ein Proxmox-Container –
  1 CPU-Kern, 512 MB RAM, 2 GB Speicher
- **Netzwerk** von diesem Rechner zum Panel und zu **Wings** auf jedem Node (meist Port `8080`;
  bei Calagopus mit Wings-Proxy reicht das Panel)
- **Root-Zugang** für die Installation

## 5. Installation

### A) Proxmox – neuer Container (empfohlen)

In der Shell deines **Proxmox-Hosts**:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/proxmox/install.sh)"
```

Mit **Enter** die Vorschläge übernehmen. Das Skript legt einen Debian-Container an und installiert
Hatchtower darin.

### B) Vorhandener Debian- oder Ubuntu-Rechner

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/skaot1910/hatchtower/main/installer/get.sh)"
```

<details>
<summary>Optionen</summary>

| Option | Bedeutung |
|---|---|
| `--port 8000` | Port der Webseite (Standard 8000) |
| `--bind 127.0.0.1` | Nur lokal lauschen (Reverse-Proxy auf demselben Rechner) |
| `--allow "192.168.1.5,192.168.1.10"` | Nur diese IPs dürfen zugreifen |

Aufruf: `bash -c "$(curl …)" _ --allow "192.168.1.5"`
</details>

<details>
<summary>Offline aus einem Release-Paket</summary>

`hatchtower-<version>.tar.gz` unter **Releases** herunterladen, dann:

```bash
tar -xzf hatchtower-<version>.tar.gz && cd hatchtower-<version> && bash installer/install.sh
```
</details>

Am Ende zeigt das Skript die **Adresse** (`http://<ip>:8000/setup`) und einen **Einrichtungscode**
(24 Stunden gültig, neuer mit `hatchtower setup-token`).

## 6. Erste Einrichtung

Die Adresse aus dem Skript öffnen. Ein Assistent führt durch:

1. **Owner-Konto** – Einrichtungscode, Benutzername und Passwort (mind. 15 Zeichen)
2. **Panel** – Typ, Adresse und API-Key(s); der Assistent zeigt, wo du sie findest
3. **Wings** – Nodes werden selbst gefunden und geprüft
4. **Prüfen und speichern** – alles wird getestet, ohne etwas am Panel zu ändern
5. **Optionen** – Modus, Adresse für Spieler, Zeitzone, Standardansicht

> 💡 Hatchtower startet im **Read-Only-Modus**. Unter **System → Panel → Modus** auf
> **„Lesen und steuern“** umstellen, wenn alles passt.

<img src="docs/screenshots/connection-wizard.png" alt="Der Assistent erklärt, wie man den API-Key bekommt" width="80%">

## 7. API-Keys

**Pterodactyl und Pelican brauchen zwei Keys:**

1. **Application-API-Key** (Admin-Bereich → Application API / API Keys) mit **allen Rechten auf „Read“**.
2. **Client-API-Key** eines **eigenen Panel-Benutzers** (z. B. `hatchtower-service`) – Administrator oder
   Subuser ausgewählter Server. Im Konto dieses Benutzers unter API Credentials / API Keys anlegen und
   **Allowed IPs** auf deinen Hatchtower-Rechner setzen.

**Calagopus braucht einen Key** von einem eigenen Admin-Benutzer: Admin – Nodes und Server lesen; Server – alles
außer `sftp` und `query-raw`; **Allowed IPs** setzen.

> ⚠️ Ein Client-Key darf alles, was sein Benutzer darf. Deshalb: eigener Benutzer, Allowed IPs und
> Zwei-Faktor-Anmeldung.

Die Keys werden verschlüsselt gespeichert und nie wieder angezeigt. Ändern unter **System → Panel**.

## 8. Benutzer und Rechte

- **Owner** dürfen alles, **Benutzer** nur, was du freigibst – **pro Server**.
- **Einladen** unter **Benutzer → Per Einladungslink** – die Person wählt ihr Passwort selbst. Eine Rechtegruppe
  und Server lassen sich gleich mitgeben.
- **Rechte**: Benutzer öffnen → *Rechte pro Server*, Gruppe oder einzelne Rechte wählen.
  „Alle Server, auch künftige“ gilt auch für neue Server.
- **Rechtegruppen**: *Vorschlagsgruppen anlegen* erzeugt Zuschauer, Spieler und Moderator für den Anfang.
- **Vorschau als …** zeigt die Seite genau so, wie diese Person sie sieht.

Jedes Recht wird bei jeder Anfrage auf dem Server geprüft – ein ausgeblendeter Knopf ist nicht der einzige Schutz.

## 9. Backups

1. **Backup-Ziel** (nur Owner): ein Laufwerk unter `/mnt`, `/media` oder `/srv` oder eine **SMB-Freigabe**.
2. **Backup-Job**: was (ganzer Server oder Ordner), wohin und wann (von Hand oder nach Zeitplan).
   Auf Wunsch wird der Server vorher gestoppt und danach wieder gestartet.
3. Jeder Lauf schreibt **ein ZIP pro Server** und prüft es direkt danach.

**Wiederherstellen** (*Backups → gespeichertes Backup → Wiederherstellen*): entweder **in einen eigenen Ordner**
(sicher, überschreibt nichts) oder **an den ursprünglichen Ort** (ersetzt Dateien, löscht nichts).

Alte Backups entfernt **Aufbewahren: Anzahl / Tage**; das neueste vollständige bleibt immer. Verpasste
Zeitpläne werden nachgeholt (bis 6 Stunden), Fehler lösen eine Meldung aus.

**Hatchtowers eigene Daten** werden jede Nacht nach `/var/backups/hatchtower/` gesichert. Für eine vollständige
Sicherung inklusive Keys **System → Daten → Datenumzug: Exportieren** nutzen und die Datei woanders aufbewahren.

## 10. Aktualisieren

Unter **System → Updates** oder auf dem Rechner:

```bash
hatchtower update
```

Vorher wird die Datenbank gesichert; startet die neue Version nicht, geht Hatchtower automatisch zurück.

## 11. Hilfe bei Problemen

| Problem | Lösung |
|---|---|
| Seite öffnet sich nicht | Adresse/Port prüfen, `systemctl status hatchtower`, `--allow`-Liste |
| Einrichtungscode abgelaufen | `hatchtower setup-token` |
| API-Key abgelehnt (401/403) | Richtiger Key im richtigen Feld? Hatchtower-IP bei **Allowed IPs**? Reparieren unter **System → Panel** |
| Konsole, Dateien oder Backups gehen nicht | Hatchtower erreicht **Wings** nicht (Port `8080`) – Firewalls prüfen |
| Start/Stopp-Knöpfe fehlen | Read-Only-Modus oder fehlendes Recht |
| Backup-Ziel nicht beschreibbar | Den `chown`-Befehl aus der Meldung auf dem **Proxmox-Host** ausführen |
| Owner-Passwort vergessen | `hatchtower reset-password` |
| Etwas anderes | `hatchtower selftest`, `journalctl -u hatchtower -f` |

## 12. Sicherheit

- Aus dem Internet nur über einen **Reverse-Proxy mit HTTPS** (Caddy, Nginx, Pangolin …) und dessen IP unter
  **System → Netzwerk → Reverse-Proxy-IPs** eintragen – siehe [Reverse-Proxy und Pangolin](docs/reverse-proxy.de.md).
- Das **Panel privat lassen**; nur Hatchtower muss erreichbar sein.
- Den Client-Key schützen (eigener Benutzer, Allowed IPs, 2FA).
- Die Einrichtung gleich nach der Installation abschließen und nur nötige Rechte vergeben.

Eingebaut: Argon2id-Passwort-Hashes, Sperre nach Fehlversuchen, CSRF-Schutz, strenge CSP, Rechteprüfung auf dem
Server, erzwungener Read-Only-Modus, verschlüsselte Ablage der Keys.

## 13. Häufige Fragen

**Brauchen meine Freunde ein Panel-Konto?** Nein, nur ein Hatchtower-Konto per Einladungslink.

**Ersetzt es mein Panel?** Nein. Server, Eggs und Nodes verwaltest du weiter im Panel.

**Zeigt es die Spielerzahl?** Nein – das klappt nicht zuverlässig für jedes Spiel.

**Ist es kostenlos?** Ja, unter der AGPL-3.0.

<details>
<summary>Befehle und Pfade</summary>

| Befehl | Was er tut |
|---|---|
| `hatchtower update` | Neueste Version installieren (`--check` prüft nur) |
| `hatchtower selftest` | Verbindung zu Panel und Wings prüfen |
| `hatchtower version` | Installierte Version anzeigen |
| `hatchtower reset-password` | Passwort zurücksetzen und Konto entsperren |
| `hatchtower setup-token` | Neuer Einrichtungscode (nur solange es kein Konto gibt) |

| Pfad | Inhalt |
|---|---|
| `/opt/hatchtower` | Programm |
| `/etc/hatchtower/hatchtower.env` | Interne Schlüssel – **nie weitergeben** |
| `/var/lib/hatchtower/hatchtower.db` | Datenbank |
| `/var/backups/hatchtower/` | Datenbank-Sicherungen |

</details>

**Fehler melden:** ein GitHub-Issue mit Hatchtower- und Panel-Version und was passiert ist. Niemals API-Keys,
Passwörter oder `hatchtower.env` posten.

## 14. Lizenz

Hatchtower ist freie Software unter der **GNU Affero General Public License v3.0**, siehe [`LICENSE`](LICENSE).
Copyright © 2026 skaot1910. Lizenzen der Abhängigkeiten stehen in der Oberfläche unter **Lizenzen**.

🤖 Hatchtower ist gemeinsam mit [Claude](https://claude.ai) (Anthropic) entstanden.

<div align="center">
<sub>Hatchtower ist ein unabhängiges Projekt und gehört nicht zu Pterodactyl, Pelican oder Calagopus.</sub>
</div>
