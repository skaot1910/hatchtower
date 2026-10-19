"""Rechte-Matrix von Hatchtower: jede Route mit der Bedingung, unter der sie erreichbar ist.

Diese Liste ist die eine Quelle der Wahrheit:
- tests/test_rechte_matrix.py prüft jede Route gegen ihren Eintrag und schlägt fehl,
  sobald es eine Route ohne Eintrag gibt (neue Routen müssen bewusst eingeordnet werden).
- docs/rechte-matrix.md wird daraus erzeugt:  python tests/routes.py

Zugriffsarten (access):
  public  ohne Anmeldung
  token   ohne Anmeldung, aber nur mit gültigem Einmal-Link (Einladung, Passwort, E-Mail)
  setup   nur solange es noch keinen Benutzer gibt (Einrichtungscode nötig)
  login   jeder angemeldete Benutzer, wirkt nur auf das eigene Konto
  owner   nur Owner
  server  Server muss sichtbar sein (server.view) und das Recht `perm` muss vorliegen
  kill    nur Owner, und nur im Read-Write-Modus
  automation  Owner oder Recht `automation.manage` – wirkt nur auf die Server, für die es vergeben ist
  backup  Owner oder Backup-Recht `perm` für mindestens einen Server – Jobs nur, wenn es für alle ihre Server gilt
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PASSWORD = "Sehr-Langes-Passwort-123"


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    access: str
    what: str
    perm: str | None = None
    csrf: str | None = None          # form | header | prelogin
    write: bool = False              # schreibt nach Pterodactyl / Wings
    case: str = ""                   # Variante derselben Route (z. B. Power-Signal)
    form: dict = field(default_factory=dict)
    json: dict | None = None
    query: dict = field(default_factory=dict)
    upload: str | None = None        # Name des Datei-Felds (Inhalt liefert der Test)
    args: dict = field(default_factory=dict)   # Platzhalter im Pfad, die vom Standard abweichen

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}" + (f" [{self.case}]" if self.case else "")


R = Route
ROUTES: tuple[Route, ...] = (
    # -- Öffentlich -----------------------------------------------------------------------
    R("GET", "/login", "public", "Anmeldeseite"),
    R("POST", "/login", "public", "Anmelden", csrf="prelogin",
      form={"username": "olga", "password": PASSWORD}),
    R("GET", "/lang/{code}", "public", "Sprache umschalten (angemeldet: im Konto gespeichert)"),
    R("GET", "/status", "public", "Öffentliche Statusseite (abschaltbar)"),
    R("GET", "/manifest.webmanifest", "public", "Web-App-Manifest (Farben und Symbol der installierten App)"),
    R("GET", "/status.json", "public", "Statusseite als JSON, nur freigegebene Angaben"),
    R("GET", "/status/logo/{ident}", "public", "Logo eines Servers, nur wenn er auf der Statusseite steht"),
    R("GET", "/impressum", "public", "Impressum (abschaltbar)"),
    R("GET", "/datenschutz", "public", "Datenschutzerklärung (abschaltbar)"),
    R("GET", "/branding/{filename}", "public", "Eigene Logos, nur die aktuell eingestellten Dateien"),
    R("GET", "/forgot", "public", "Passwort vergessen (nur mit eingeschalteter E-Mail)"),
    R("POST", "/forgot", "public", "Link zum Zurücksetzen anfordern", csrf="prelogin", form={"login": "olga"}),

    # -- Einmal-Links ---------------------------------------------------------------------
    R("GET", "/reset/{token}", "token", "Neues Passwort setzen (Link aus der E-Mail)", args={"token": "@reset"}),
    R("POST", "/reset/{token}", "token", "Neues Passwort speichern", csrf="prelogin",
      form={"password": "Neues-Langes-Passwort-1", "repeat": "Neues-Langes-Passwort-1"}, args={"token": "@reset"}),
    R("GET", "/verify/{token}", "token", "E-Mail-Adresse bestätigen", args={"token": "@verify"}),
    R("GET", "/invite/{token}", "token", "Einladung öffnen", args={"token": "@invite"}),
    R("GET", "/notify/unsubscribe/{token}", "token", "E-Mail-Benachrichtigungen abbestellen: Bestätigungsseite",
      args={"token": "@unsubscribe"}),
    R("POST", "/notify/unsubscribe/{token}", "token",
      "E-Mail-Benachrichtigungen abbestellen (auch Ein-Klick aus dem Mailprogramm, signierter Link statt Formular-Token)",
      args={"token": "@unsubscribe"}),
    R("POST", "/invite/{token}", "token", "Einladung annehmen, Konto anlegen", csrf="prelogin",
      form={"username": "neuling", "password": PASSWORD, "repeat": PASSWORD}, args={"token": "@invite"}),

    # -- Einrichtung ----------------------------------------------------------------------
    R("POST", "/setup/owner", "setup", "Ersten Owner anlegen", csrf="prelogin",
      form={"token": "X", "username": "chef", "password": PASSWORD, "repeat": PASSWORD}),
    R("POST", "/setup/import", "setup", "Neue Instanz aus Datenumzug befüllen", csrf="prelogin",
      form={"token": "X", "passphrase": "x" * 12}, upload="file"),

    # -- Angemeldet, eigenes Konto ----------------------------------------------------------
    R("GET", "/", "login", "Übersicht (nur sichtbare Server); ?als= nur für Owner"),
    R("GET", "/api/overview", "login", "Live-Werte der Übersicht (nur sichtbare Server)"),
    R("GET", "/theme/{key}", "login", "Theme umschalten"),
    R("GET", "/view/{mode}", "login", "Einfache oder vollständige Ansicht umschalten"),
    R("POST", "/logout", "login", "Abmelden", csrf="form"),
    R("GET", "/logos/{filename}", "login", "Serverlogos"),
    R("GET", "/about", "login", "Über Hatchtower, Paketliste"),
    R("GET", "/about/licenses.txt", "login", "Lizenztexte"),
    R("GET", "/account", "login", "Eigenes Konto"),
    R("POST", "/account/theme", "login", "Eigenes Theme", csrf="form", form={"theme": "hell"}),
    R("POST", "/account/language", "login", "Eigene Sprache", csrf="form", form={"language": "en"}),
    R("POST", "/account/password", "login", "Eigenes Passwort ändern", csrf="form",
      form={"current": PASSWORD, "new": "Neues-Langes-Passwort-1", "repeat": "Neues-Langes-Passwort-1"}),
    R("POST", "/account/email", "login", "Eigene E-Mail-Adresse ändern oder entfernen", csrf="form",
      form={"email": ""}),
    R("POST", "/account/notify", "login", "Eigene Benachrichtigungen", csrf="form", form={"notify_email": "1"}),
    R("POST", "/account/delete", "login", "Eigenes Konto löschen (Passwort nötig)", csrf="form",
      form={"current": PASSWORD, "confirm": "1"}),
    R("GET", "/account/export", "login", "Eigene Daten herunterladen (Art. 15 DSGVO)"),
    R("GET", "/notifications", "login", "Benachrichtigungen (Glocke): nur Meldungen zu sichtbaren Servern, Verwaltung nur Owner"),
    R("POST", "/notifications/read-all", "login", "Eigene Benachrichtigungen als gelesen markieren", csrf="form"),
    R("POST", "/notifications/{entry_id}/read", "login", "Eine Benachrichtigung als gelesen markieren (nur sichtbare)",
      csrf="form"),

    # -- Server ---------------------------------------------------------------------------
    R("GET", "/servers/{ident}", "server", "Serverdetails", perm="server.view"),
    R("GET", "/servers/{ident}/players", "server", "Reiter Spieler: wer online ist und woran es erkannt wird", perm="server.players"),
    R("GET", "/api/servers/{ident}/metrics", "server", "Verlauf CPU, RAM, Netzwerk", perm="server.view"),
    R("POST", "/api/servers/{ident}/favorite", "server", "Favorit an/aus (nur eigene Liste)", perm="server.view",
      csrf="header"),
    R("POST", "/api/servers/{ident}/power", "server", "Server starten", perm="power.start", csrf="header",
      write=True, case="start", json={"signal": "start"}),
    R("POST", "/api/servers/{ident}/power", "server", "Server stoppen", perm="power.stop", csrf="header",
      write=True, case="stop", json={"signal": "stop"}),
    R("POST", "/api/servers/{ident}/power", "server", "Server neu starten", perm="power.restart", csrf="header",
      write=True, case="restart", json={"signal": "restart"}),
    R("POST", "/api/servers/{ident}/power", "kill", "Server hart beenden", csrf="header", write=True,
      case="kill", json={"signal": "kill"}),
    R("GET", "/servers/{ident}/console/stream", "server", "Live-Konsole", perm="console.read"),
    R("GET", "/api/servers/{ident}/logs", "server", "Konsolen-Archiv durchsuchen", perm="console.archive"),
    R("GET", "/servers/{ident}/logs.txt", "server", "Konsolen-Archiv herunterladen", perm="console.archive"),
    R("POST", "/api/servers/{ident}/command", "server", "Konsolenbefehl senden", perm="console.command",
      csrf="header", write=True, json={"command": "say hallo"}),
    R("GET", "/servers/{ident}/files", "server", "Dateiliste", perm="sftp.view"),
    R("GET", "/servers/{ident}/files/download", "server", "Datei herunterladen", perm="sftp.download",
      query={"file": "/a.txt"}),
    R("POST", "/servers/{ident}/files/zip", "server", "Auswahl als ZIP herunterladen", perm="sftp.download",
      csrf="form", form={"dir": "/", "names": "a.txt"}),
    R("POST", "/servers/{ident}/files/upload", "server", "Dateien hochladen", perm="sftp.upload", csrf="form",
      write=True, query={"dir": "/"}, upload="files"),
    R("GET", "/servers/{ident}/files/edit", "server", "Datei im Editor öffnen", perm="sftp.edit",
      query={"file": "/a.txt"}),
    R("POST", "/servers/{ident}/files/edit", "server", "Datei speichern (alte Fassung als .bak)", perm="sftp.edit",
      csrf="form", write=True, form={"file": "/a.txt", "content": "neu"}),
    R("POST", "/servers/{ident}/files/restore", "server", ".bak wiederherstellen", perm="sftp.edit", csrf="form",
      write=True, form={"file": "/a.txt"}),
    R("POST", "/servers/{ident}/files/rename", "server", "Umbenennen", perm="sftp.rename", csrf="form",
      write=True, form={"dir": "/", "old": "a.txt", "new": "b.txt"}),
    R("POST", "/servers/{ident}/files/mkdir", "server", "Ordner anlegen", perm="sftp.create_directory",
      csrf="form", write=True, form={"dir": "/", "name": "neu"}),
    R("POST", "/servers/{ident}/files/delete", "server", "Löschen", perm="sftp.delete", csrf="form", write=True,
      form={"dir": "/", "name": "a.txt"}),
    R("POST", "/servers/{ident}/files/delete-many", "server", "Mehrere Dateien und Ordner löschen", perm="sftp.delete",
      csrf="form", write=True, form={"dir": "/", "names": "a.txt"}),
    R("GET", "/api/servers/{ident}/folders", "server", "Unterordner für die Ordnerauswahl beim Verschieben",
      perm="sftp.view", query={"dir": "/"}),
    R("GET", "/api/servers/{ident}/files/search", "server", "Dateien und Ordner suchen (lokaler Index)",
      perm="sftp.view", query={"q": "a.txt"}),
    R("POST", "/api/servers/{ident}/files/reindex", "server", "Suchindex der Dateien neu aufbauen (nur lesend)",
      perm="sftp.view", csrf="header", json={}),
    R("POST", "/servers/{ident}/files/move", "server", "Dateien und Ordner in einen anderen Ordner verschieben",
      perm="sftp.rename", csrf="form", write=True, form={"dir": "/", "names": "a.txt", "target": "/neu"}),
    R("POST", "/servers/{ident}/files/backup", "server", "Auswahl zu einem Backup-Job hinzufügen", perm="backups.manage",
      csrf="form", form={"dir": "/", "names": "a.txt", "job_id": "new"}),
    R("GET", "/servers/{ident}/settings", "server", "Serverangaben im Manager", perm="server.manage"),
    R("POST", "/servers/{ident}/settings", "server", "Serverangaben speichern", perm="server.manage", csrf="form",
      form={"ingame_name": "Gekapert", "game_name": "Gekapert", "public_address": "evil.example:1"}),
    R("POST", "/servers/{ident}/logo", "server", "Serverlogo hochladen", perm="server.manage", csrf="form",
      upload="logo"),
    R("POST", "/servers/{ident}/logo/delete", "server", "Serverlogo entfernen", perm="server.manage", csrf="form"),
    R("GET", "/servers/{ident}/admin", "server",
      "Panel-Einstellungen (Ändern nur mit Admin-Key und dem jeweiligen Recht)", perm="admin.view"),
    R("POST", "/servers/{ident}/admin/details", "server", "Name und Beschreibung im Panel ändern", perm="admin.details",
      csrf="form", write=True, form={"name": "Gekapert", "description": "x"}),
    R("POST", "/servers/{ident}/admin/build", "server", "RAM, CPU, Speicher und Limits im Panel ändern",
      perm="admin.build", csrf="form", write=True,
      form={"memory": "4096", "swap": "0", "disk": "4096", "io": "500", "cpu": "200", "threads": "",
            "databases": "0", "allocations": "1", "backups": "1"}),
    R("POST", "/servers/{ident}/admin/ports", "server", "Ports des Servers ändern (hinzufügen, entfernen, Hauptport)",
      perm="admin.build", csrf="form", write=True, form={"primary": "11", "add": "12"}),
    R("POST", "/servers/{ident}/admin/startup", "server", "Startbefehl, Docker-Image und Startvariablen im Panel ändern",
      perm="admin.startup", csrf="form", write=True,
      form={"startup": "./start.sh --evil", "image": "img:1", "var__GAME": "anders"}),
    R("POST", "/servers/{ident}/admin/reinstall", "server", "Server im Panel neu installieren", perm="admin.reinstall",
      csrf="form", write=True),
    R("POST", "/servers/{ident}/admin/suspend", "owner", "Server im Panel sperren oder entsperren (nur Owner)",
      csrf="form", form={"suspend": "1"}),
    R("POST", "/servers/{ident}/admin/delete", "owner", "Server im Panel löschen (nur Owner, Name zur Bestätigung)",
      csrf="form", form={"confirm_name": "Server srv-a"}),

    # -- Owner: Einrichtung und Verbindung --------------------------------------------------
    R("GET", "/setup", "owner", "Einrichtungsassistent (vor dem ersten Konto öffentlich)"),
    R("POST", "/setup/legal", "owner", "Assistent: Vorlagen für Impressum/Datenschutz anlegen (bleiben aus)", csrf="form",
      form={"page__impressum": "1", "sprache": "de"}),
    R("GET", "/admin/connection", "owner", "System → Panel: Verbindung zum Panel (Pterodactyl, Pelican, Calagopus)"),
    R("GET", "/admin/connection/wizard", "owner", "Verbindung reparieren / anderes Panel: Assistent mit frischem Entwurf"),
    R("POST", "/admin/connection/check", "owner", "Assistent: Eingaben in den Entwurf und prüfen (nur lesend, speichert nichts)",
      csrf="form", form={"step": "panel", "panel_type": "pterodactyl", "panel_url": "https://evil.example",
                         "app_key": "ptla_" + "x" * 43}),
    R("POST", "/admin/connection/save", "owner", "Assistent: geprüften Entwurf speichern (nur mit Bestätigung)", csrf="form",
      form={"confirm": "1"}),
    R("POST", "/admin/connection/discard", "owner", "Assistent: Entwurf verwerfen", csrf="form"),
    R("POST", "/admin/connection/panel", "owner", "Panel-Art, Panel-Adresse und Application-Key (älteres Formular)", csrf="form",
      form={"panel_type": "pterodactyl", "panel_url": "https://evil.example", "app_key": "ptla_" + "x" * 43}),
    R("POST", "/admin/connection/client", "owner", "Client-Key", csrf="form",
      form={"client_key": "ptlc_" + "x" * 43}),
    R("POST", "/admin/connection/nodes", "owner", "Abweichende Wings-Adressen", csrf="form",
      form={"override__wings.test": "10.9.9.9"}),
    R("POST", "/admin/connection/admin-key", "owner",
      "Optionalen Admin-Key (Schreibrechte) prüfen und speichern; die Prüfung ändert nichts am Panel", csrf="form",
      form={"admin_key": "ptla_" + "b" * 43}),
    R("POST", "/admin/connection/admin-key/remove", "owner", "Admin-Key entfernen", csrf="form"),
    R("GET", "/admin/servers", "owner", "Server → Alle Server (Wege zu Verwalten und Panel-Einstellungen)"),
    R("GET", "/admin/servers/new", "owner", "Server → Server anlegen (Node und Egg wählen, dann Formular)",
      query={"node": "1", "egg": "5"}),
    R("POST", "/admin/servers/new", "owner", "Server im Panel anlegen", csrf="form",
      form={"node": "1", "egg": "5", "name": "Neu", "owner": "1", "allocation": "12", "image": "img:1",
            "startup": "./start.sh", "var__GAME": "demo", "memory": "1024", "disk": "1024", "cpu": "100", "io": "500"}),
    R("GET", "/admin/ports", "owner", "Server → Ports der Nodes"),
    R("POST", "/admin/ports", "owner", "Ports auf einem Node anlegen", csrf="form",
      form={"node": "1", "ip": "10.0.0.1", "ports": "30000-30001"}),
    R("POST", "/admin/ports/delete", "owner", "Freie Ports eines Nodes löschen", csrf="form",
      form={"node": "1", "alloc": "12"}),
    R("POST", "/admin/connection/options", "owner", "Modus (im Assistenten auch öffentliche Adresse, Proxy, Zeitzone)",
      csrf="form",
      form={"mode": "read-write", "public_host": "evil.example", "timezone": "UTC"}),

    # -- Owner: Benutzer und Rechte ---------------------------------------------------------
    R("GET", "/admin/users", "owner", "Benutzerliste"),
    R("GET", "/admin/users/new", "owner", "Neue Benutzer: einladen und direkt anlegen"),
    R("POST", "/admin/users", "owner", "Benutzer anlegen", csrf="form",
      form={"username": "eindringling", "password": PASSWORD, "role": "owner"}),
    R("POST", "/admin/users/invite", "owner", "Einladungslink erstellen", csrf="form", form={"role": "owner"}),
    R("POST", "/admin/users/invites/{token_hash}/revoke", "owner", "Einladung zurückziehen", csrf="form"),
    R("GET", "/admin/users/{user_id}", "owner", "Benutzer bearbeiten"),
    R("GET", "/admin/users/{user_id}/export", "owner", "Daten eines Benutzers herunterladen"),
    R("POST", "/admin/users/{user_id}/permissions", "owner", "Rechte pro Server vergeben", csrf="form",
      form={"mode__srv-a": "custom", "perm__srv-a__sftp.delete": "1"}),
    R("POST", "/admin/users/{user_id}/update", "owner", "Rolle, Ansicht und Notiz", csrf="form",
      form={"role": "owner"}),
    R("POST", "/admin/users/{user_id}/active", "owner", "Konto deaktivieren oder wieder aktivieren", csrf="form",
      form={"active": "0"}),
    R("POST", "/admin/users/{user_id}/password", "owner", "Passwort eines Benutzers setzen", csrf="form",
      form={"password": "Neues-Langes-Passwort-1"}),
    R("POST", "/admin/users/{user_id}/email", "owner", "E-Mail-Adresse eines Benutzers setzen oder entfernen",
      csrf="form", form={"email": "user@example.com", "confirmed": "1"}),
    R("POST", "/admin/users/{user_id}/no-email", "owner", "Benutzer auf „ohne E-Mail-Adresse“ stellen",
      csrf="form", form={"no_email": "1"}),
    R("POST", "/admin/users/email-policy", "owner", "Benutzer ohne E-Mail-Adresse erlauben", csrf="form",
      form={"optional": "1"}),
    R("POST", "/admin/users/{user_id}/delete", "owner", "Benutzer löschen", csrf="form"),
    R("POST", "/admin/servers/{ident}/reviewed", "owner", "Hinweis auf neuen Server als erledigt markieren",
      csrf="form"),
    R("POST", "/admin/users/{user_id}/reviewed", "owner", "Hinweis auf neuen Benutzer als erledigt markieren",
      csrf="form"),
    R("POST", "/admin/notify/owner", "owner", "Hinweise für Owner ein- und ausschalten", csrf="form",
      form={"notice__new_server": "1"}),
    R("GET", "/admin/groups", "owner", "Rechtegruppen"),
    R("POST", "/admin/groups", "owner", "Rechtegruppe speichern", csrf="form",
      form={"name": "Gekapert", "perm__sftp.delete": "1"}),
    R("POST", "/admin/groups/starter", "owner", "Vorschlagsgruppen anlegen", csrf="form"),
    R("POST", "/admin/groups/{group_id}/delete", "owner", "Rechtegruppe löschen", csrf="form"),

    # -- Owner: Betrieb -----------------------------------------------------------------------
    R("GET", "/admin/audit", "owner", "Audit-Log"),
    R("GET", "/admin/audit/export", "owner", "Audit-Log als CSV/TXT"),
    R("GET", "/admin/automation", "automation", "Automationen (nur Server mit dem Recht)",
      perm="automation.manage"),
    R("POST", "/admin/automation/crash", "automation", "Neustart bei Absturz", perm="automation.manage",
      csrf="form", form={"crash__srv-a": "1"}),
    R("POST", "/admin/automation/schedules", "automation", "Zeitplan anlegen (Aktion braucht das passende Steuerrecht)",
      perm="automation.manage", csrf="form",
      form={"identifier": "srv-a", "name": "Nachts", "action": "restart", "time": "04:00", "weekdays": "1",
            "repeat": "weekly"}),
    R("POST", "/admin/automation/schedules/{sched_id}/toggle", "automation", "Zeitplan pausieren/aktivieren",
      perm="automation.manage", csrf="form"),
    R("POST", "/admin/automation/schedules/{sched_id}/delete", "automation", "Zeitplan löschen",
      perm="automation.manage", csrf="form"),

    # -- Backups --------------------------------------------------------------------------------
    R("GET", "/admin/backups", "backup", "Backups: Jobs, laufende und gespeicherte Backups (nur Server mit dem Recht)",
      perm="backups.view"),
    R("GET", "/api/backups/status", "backup", "Fortschritt laufender Backups", perm="backups.view"),
    R("POST", "/admin/backups/jobs", "backup", "Backup-Job anlegen", perm="backups.manage", csrf="form",
      form={"name": "Nachts", "target_id": ""}),
    R("GET", "/admin/backups/jobs/{job_id}", "backup", "Backup-Job bearbeiten (Recht für alle Server des Jobs)",
      perm="backups.manage"),
    R("POST", "/admin/backups/jobs/{job_id}", "backup", "Backup-Job speichern (Stoppen nur mit den Steuerrechten)",
      perm="backups.manage", csrf="form", form={"name": "Gekapert", "target_id": "", "stop_mode": "none",
                                                "notify": "errors"}),
    R("POST", "/admin/backups/jobs/{job_id}/delete", "backup", "Backup-Job löschen", perm="backups.manage",
      csrf="form"),
    R("POST", "/admin/backups/jobs/{job_id}/items", "backup", "Server/Pfad hinzufügen (Recht für diesen Server)",
      perm="backups.manage", csrf="form", form={"identifier": "srv-a", "path": "/world"}),
    R("POST", "/admin/backups/jobs/{job_id}/items/remove", "backup", "Server/Pfad entfernen", perm="backups.manage",
      csrf="form", form={"identifier": "srv-a", "path": "/"}),
    R("POST", "/admin/backups/jobs/{job_id}/run", "backup", "Backup-Job in die Warteschlange stellen",
      perm="backups.run", csrf="form"),
    R("POST", "/admin/backups/jobs/{job_id}/download", "backup", "Backup-Job ausführen und direkt herunterladen",
      perm="backups.run", csrf="form"),
    R("GET", "/admin/backups/runs/{run_id}/download", "backup", "Gespeichertes Backup herunterladen",
      perm="backups.run"),
    R("POST", "/api/backups/runs/{run_id}/cancel", "backup", "Laufendes Backup abbrechen", perm="backups.run",
      csrf="header", json={}),
    R("POST", "/admin/backups/runs/{run_id}/verify", "backup",
      "Gespeichertes Backup vollständig lesen und alle Prüfsummen prüfen (ändert nichts)", perm="backups.run",
      csrf="form"),
    R("GET", "/admin/backups/runs/{run_id}/restore", "backup", "Wiederherstellen: Erklärung und Auswahl",
      perm="backups.restore"),
    R("POST", "/admin/backups/runs/{run_id}/restore", "backup",
      "Backup auf den Server zurückspielen (eigener Ordner oder Originalort; Stoppen nur mit den Steuerrechten)",
      perm="backups.restore", csrf="form", write=True, form={"mode": "folder"}),
    R("POST", "/api/backups/restores/{restore_id}/cancel", "backup", "Laufende Wiederherstellung abbrechen",
      perm="backups.restore", csrf="header", json={}, args={"restore_id": "run_id"}),
    R("POST", "/admin/backups/runs/{run_id}/delete", "backup", "Gespeichertes Backup löschen",
      perm="backups.manage", csrf="form"),
    R("POST", "/admin/backups/runs/bulk", "backup",
      "Mehrere gespeicherte Backups löschen (Prüfen mehrerer: Recht „Backups ausführen“)",
      perm="backups.manage", csrf="form", form={"action": "delete"}),
    R("POST", "/admin/backups/targets", "owner", "Backup-Ziel anlegen (Laufwerk im Container oder SMB-Freigabe)",
      csrf="form", form={"name": "Gekapert", "mount": "data", "subdir": "gekapert"}),
    R("POST", "/admin/backups/targets/{target_id}", "owner", "Backup-Ziel ändern (SMB: Verbindung wird geprüft)",
      csrf="form", form={"name": "Gekapert"}),
    R("POST", "/admin/backups/targets/{target_id}/test", "owner", "Backup-Ziel testen", csrf="form"),
    R("POST", "/admin/backups/targets/{target_id}/delete", "owner", "Backup-Ziel entfernen", csrf="form"),

    # -- Owner: Community --------------------------------------------------------------------
    R("GET", "/admin/appearance", "owner", "Community → Erscheinungsbild"),
    R("POST", "/admin/branding", "owner", "Name, Standard-Theme, Discord-Link", csrf="form",
      form={"brand_name": "Gekapert", "show_name": "1"}),
    R("POST", "/admin/branding/logo/{kind}", "owner", "Eigenes Logo hochladen", csrf="form", upload="logo"),
    R("POST", "/admin/branding/logo/{kind}/delete", "owner", "Eigenes Logo entfernen", csrf="form"),
    R("POST", "/admin/appearance/locale", "owner", "Standardsprache und Zeitzone", csrf="form",
      form={"default_language": "en", "timezone": "UTC"}),
    R("GET", "/admin/legal", "owner", "Community → Rechtliches"),
    R("POST", "/admin/legal/{page}", "owner", "Impressum/Datenschutzerklärung", csrf="form",
      form={"enabled": "1", "text": "Gekapert"}),
    R("GET", "/admin/statuspage", "owner", "Statusseite einstellen"),
    R("POST", "/admin/statuspage", "owner", "Statusseite speichern", csrf="form",
      form={"enabled": "1", "show__srv-a": "1", "field__password": "1", "password_confirm": "1"}),
    R("POST", "/admin/statuspage/discord", "owner", "Discord-Kanal hinzufügen (eigene Server-Auswahl)", csrf="form",
      form={"name": "Gekapert", "url": "https://discord.com/api/webhooks/123456789012345678/" + "e" * 68,
            "show__srv-a": "1", "field__name": "1"}),
    R("POST", "/admin/statuspage/discord/{feed_id}", "owner", "Discord-Kanal ändern", csrf="form",
      form={"name": "Gekapert", "show__srv-b": "1", "field__password": "1", "password_confirm": "1"}),
    R("POST", "/admin/statuspage/discord/{feed_id}/delete", "owner", "Discord-Kanal entfernen", csrf="form"),
    R("GET", "/admin/announcements", "owner", "Ankündigungen"),
    R("POST", "/admin/announcements", "owner", "Ankündigung speichern", csrf="form", form={"text": "Gekapert"}),
    R("POST", "/admin/announcements/{ann_id}/delete", "owner", "Ankündigung löschen", csrf="form"),

    # -- Owner: System ----------------------------------------------------------------------
    R("GET", "/admin/system", "owner", "System → Zustand: Verbindung, Monitoring, Selbsttest"),
    R("GET", "/admin/network", "owner", "System → Netzwerk"),
    R("POST", "/admin/network", "owner", "Adresse von Hatchtower, öffentliche Adresse, Reverse-Proxy", csrf="form",
      form={"base_url": "https://evil.example", "public_host": "evil.example", "trusted_proxies": "0.0.0.0/8"}),
    R("GET", "/admin/mail", "owner", "E-Mail-Einstellungen"),
    R("POST", "/admin/mail", "owner", "E-Mail-Einstellungen speichern", csrf="form",
      form={"enabled": "1", "host": "smtp.evil.example", "port": "587", "security": "starttls",
            "sender": "a@evil.example"}),
    R("POST", "/admin/mail/password/delete", "owner", "SMTP-Passwort entfernen", csrf="form"),
    R("POST", "/admin/mail/test", "owner", "Testmail senden", csrf="form", form={"to": "a@example.com"}),
    R("GET", "/admin/notify", "owner", "Benachrichtigungen"),
    R("POST", "/admin/notify", "owner", "Schwellenwerte für Benachrichtigungen", csrf="form",
      form={"disk_pct": "51", "ram_pct": "90", "ram_minutes": "5", "cpu_pct": "90", "cpu_minutes": "5"}),
    R("POST", "/admin/notify/webhook", "owner", "Webhook hinzufügen (Titel, Beschreibung, Ereignisse, Server)",
      csrf="form", form={"title": "Gekapert", "url": "https://evil.example/hook", "event__crash": "1",
                         "all_servers": "1", "enabled": "1"}),
    R("POST", "/admin/notify/webhook/{hook_id}", "owner", "Webhook ändern", csrf="form",
      form={"title": "Gekapert", "url": "https://evil.example/hook", "all_servers": "1"}),
    R("POST", "/admin/notify/webhook/{hook_id}/delete", "owner", "Webhook entfernen", csrf="form"),
    R("POST", "/admin/notify/webhook/{hook_id}/test", "owner", "Testnachricht an einen Webhook", csrf="form"),
    R("GET", "/admin/data", "owner", "System → Daten: Aufbewahrung und Datenumzug"),
    R("POST", "/admin/data/retention", "owner", "Aufbewahrungsfristen", csrf="form",
      form={"audit_days": "1", "console_days": "0"}),
    R("POST", "/admin/data/export", "owner", "Alle Daten verschlüsselt exportieren (Passwort nötig)",
      csrf="form", form={"passphrase": "x" * 12, "repeat": "x" * 12, "current": PASSWORD}),
    R("POST", "/admin/data/import", "owner", "Daten importieren und ersetzen (Passwort nötig)", csrf="form",
      form={"passphrase": "x" * 12, "current": PASSWORD, "confirm": "1"}, upload="file"),
    R("GET", "/admin/updates", "owner", "System → Updates"),
    R("POST", "/admin/system/update-check", "owner", "Nach Updates suchen", csrf="form"),
    R("POST", "/admin/system/update-channel", "owner", "Vorab-Versionen ein/aus", csrf="form"),
    R("POST", "/admin/system/update", "owner", "Update starten", csrf="form"),
    R("GET", "/admin/system/update", "owner", "Update-Fortschritt"),
    R("GET", "/api/system/update-status", "owner", "Update-Status"),
)

ACCESS_TITLES = {
    "public": "Öffentlich",
    "token": "Einmal-Link (ohne Anmeldung)",
    "setup": "Einrichtung (nur ohne Benutzer)",
    "login": "Angemeldet – nur eigenes Konto",
    "server": "Server – Recht pro Server",
    "kill": "Server – nur Owner",
    "automation": "Automationen – Owner oder Recht pro Server",
    "backup": "Backups – Owner oder Recht pro Server",
    "owner": "Nur Owner",
}
CSRF_TEXT = {"form": "Formular", "header": "Header", "prelogin": "Vorab-Token", None: "–"}


def render_markdown() -> str:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    from hatchtower.permissions import PERMISSIONS

    out = [
        "# Rechte-Matrix",
        "",
        "Welche Route unter welcher Bedingung erreichbar ist. Erzeugt aus `tests/routes.py` mit "
        "`python tests/routes.py`; `tests/test_rechte_matrix.py` prüft jede Zeile automatisch.",
        "",
        "Grundregeln:",
        "",
        "- Alles, was nicht ausdrücklich erlaubt ist, ist verboten. Owner dürfen alles.",
        "- Server-Routen verlangen immer zuerst **Sehen** (`server.view`), dann das genannte Recht. "
        "Ein fremder oder unbekannter Server liefert dieselbe Antwort (403) wie ein fehlendes Recht.",
        "- Rechte mit ✎ sind Schreibrechte: Im Read-Only-Modus hat sie niemand, auch kein Owner.",
        "- Jede verweigerte Aktion landet als `access.denied` im Audit-Log.",
        "- Zustandsändernde Anfragen brauchen ein CSRF-Token (Formularfeld oder Header `X-CSRF-Token`).",
        "- Zeitpläne und geplante Backup-Jobs eines Benutzers laufen nur, solange er aktiv ist und die nötigen "
        "Rechte noch hat; gibt es ihn nicht mehr, laufen sie nicht.",
        "",
        "## Rechte",
        "",
        "| Recht | Name | Schreibrecht | Setzt voraus | Routen | Achtung |",
        "|---|---|---|---|---|---|",
    ]
    for p in PERMISSIONS:
        used = sorted({f"`{r.method} {r.path}`" + (f" ({r.case})" if r.case else "")
                       for r in ROUTES if r.perm == p.key})
        out.append(f"| `{p.key}` | {p.label} | {'✎' if p.write else ''} | "
                   f"{', '.join(f'`{d}`' for d in p.requires) or '–'} | {'<br>'.join(used)} | {p.note} |")
    for access, title in ACCESS_TITLES.items():
        rows = [r for r in ROUTES if r.access == access]
        out += ["", f"## {title}", ""]
        if access in ("server", "automation", "backup"):
            out += ["| Methode | Pfad | Recht | CSRF | Pterodactyl | Zweck |", "|---|---|---|---|---|---|"]
            for r in rows:
                out.append(f"| {r.method} | `{r.path}` | `{r.perm}` | {CSRF_TEXT[r.csrf]} | "
                           f"{'schreibt' if r.write else 'liest'} | {r.what} |")
        else:
            out += ["| Methode | Pfad | CSRF | Zweck |", "|---|---|---|---|"]
            for r in rows:
                out.append(f"| {r.method} | `{r.path}` | {CSRF_TEXT[r.csrf]} | {r.what} |")
    return "\n".join(out) + "\n"


DOC = Path(__file__).resolve().parents[1] / "docs" / "rechte-matrix.md"

if __name__ == "__main__":
    DOC.write_text(render_markdown(), encoding="utf-8", newline="\n")
    print(f"geschrieben: {DOC}")
