# Öffentliche Demo

[English](public-demo.md) · **Deutsch**

Im Demo-Modus können sich Besucher Hatchtower ohne Konto ansehen: Die Anmeldeseite zeigt
**„Demo ansehen“**, ein Klick meldet sie als eigener Demo-Benutzer an.

## Was der Demo-Benutzer darf und was nicht

- Er sieht genau die Server und Rechte, die du diesem Benutzer gibst – wie bei jedem anderen Benutzer.
- **Er kann nichts verändern**: Jede schreibende Anfrage wird abgelehnt (Start/Stopp,
  Konsolenbefehle, Dateien, eigenes Passwort, Einstellungen). Das Panel gilt für ihn als nur lesend,
  egal in welchem Modus die Instanz läuft.
- Owner sind nie betroffen: Sie melden sich normal an und arbeiten wie gewohnt.
- Die Demo-Anmeldung funktioniert nie für ein Owner-Konto, auch nicht, wenn man versehentlich eins einträgt.
- Jeder abgelehnte Versuch steht im Audit-Log als „In der Demo blockiert“.

## Einrichten

1. **Erfundene Server verwenden.** Eine Demo ist öffentlich. In der Konsole können IP-Adressen,
   Spielernamen oder Tokens stehen – nimm ein eigenes Panel oder Server ohne private Inhalte.
2. Als Owner einen **normalen Benutzer** (kein Owner) anlegen, z. B. `demo`, und ihm die Rechte geben,
   die du zeigen willst. Empfohlen: ansehen, Beitrittspasswort, Server-Protokoll, Konsole (lesen),
   Konsolen-Archiv, Dateien ansehen. Alles Schreibende weglassen.
3. Demo-Modus mit dem Benutzernamen einschalten:
   - Klassische Installation: `HATCHTOWER_DEMO_USER=demo` in `/etc/hatchtower/hatchtower.env`
     eintragen, dann `systemctl restart hatchtower`.
   - Docker: `HATCHTOWER_DEMO_USER: demo` unter `environment` in der `compose.yaml`, dann
     `docker compose up -d`.
4. Anmeldeseite öffnen – „Demo ansehen“ steht über dem normalen Login.

Ausschalten: Zeile entfernen und neu starten.

## Worauf achten

- **Anfragegrenze des Panels:** Jede offene Konsole und jede Dateiliste kostet Anfragen. Pterodactyl
  erlaubt etwa 128 Anfragen pro Minute und API-Key, geteilt von ganz Hatchtower. Bei vielen
  Besuchern Konsole und Dateien lieber weglassen.
- Die Ansicht des Demo-Benutzers (einfach oder alles) lässt sich auf seiner Benutzerseite wählen und
  festlegen, damit Besucher sehen, was du zeigen willst.
- Die Demo wie jede öffentliche Hatchtower hinter HTTPS betreiben – siehe [Reverse-Proxy und Pangolin](reverse-proxy.de.md).
