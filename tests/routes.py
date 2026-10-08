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

    # -- Server ---------------------------------------------------------------------------
    R("GET", "/servers/{ident}", "server", "Serverdetails", perm="server.view"),
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
    R("GET", "/api/servers/{ident}/logs", "server", "Konsolen-Archiv durchsuchen", perm="console.read"),
    R("GET", "/servers/{ident}/logs.txt", "server", "Konsolen-Archiv herunterladen", perm="console.read"),
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
    R("GET", "/servers/{ident}/settings", "server", "Serverangaben im Manager", perm="server.manage"),
    R("POST", "/servers/{ident}/settings", "server", "Serverangaben speichern", perm="server.manage", csrf="form",
      form={"ingame_name": "Gekapert", "public_address": "evil.example:1"}),
    R("POST", "/servers/{ident}/logo", "server", "Serverlogo hochladen", perm="server.manage", csrf="form",
      upload="logo"),
    R("POST", "/servers/{ident}/logo/delete", "server", "Serverlogo entfernen", perm="server.manage", csrf="form"),

    # -- Owner: Einrichtung und Verbindung --------------------------------------------------
    R("GET", "/setup", "owner", "Einrichtungsassistent (vor dem ersten Konto öffentlich)"),
    R("GET", "/admin/connection", "owner", "Verbindung zu Pterodactyl"),
    R("POST", "/admin/connection/panel", "owner", "Panel-Adresse und Application-Key", csrf="form",
      form={"panel_url": "https://evil.example", "app_key": "ptla_" + "x" * 43}),
    R("POST", "/admin/connection/client", "owner", "Client-Key", csrf="form",
      form={"client_key": "ptlc_" + "x" * 43}),
    R("POST", "/admin/connection/nodes", "owner", "Abweichende Wings-Adressen", csrf="form",
      form={"override__wings.test": "10.9.9.9"}),
    R("POST", "/admin/connection/options", "owner", "Modus, öffentliche Adresse, Proxy, Zeitzone", csrf="form",
      form={"mode": "read-write", "public_host": "evil.example", "timezone": "UTC"}),

    # -- Owner: Benutzer und Rechte ---------------------------------------------------------
    R("GET", "/admin/users", "owner", "Benutzerliste"),
    R("POST", "/admin/users", "owner", "Benutzer anlegen", csrf="form",
      form={"username": "eindringling", "password": PASSWORD, "role": "owner"}),
    R("POST", "/admin/users/invite", "owner", "Einladungslink erstellen", csrf="form", form={"role": "owner"}),
    R("POST", "/admin/users/invites/{token_hash}/revoke", "owner", "Einladung zurückziehen", csrf="form"),
    R("GET", "/admin/users/{user_id}", "owner", "Benutzer bearbeiten"),
    R("GET", "/admin/users/{user_id}/export", "owner", "Daten eines Benutzers herunterladen"),
    R("POST", "/admin/users/{user_id}/permissions", "owner", "Rechte pro Server vergeben", csrf="form",
      form={"mode__srv-a": "custom", "perm__srv-a__sftp.delete": "1"}),
    R("POST", "/admin/users/{user_id}/update", "owner", "Rolle und Aktiv-Status", csrf="form",
      form={"role": "owner", "is_active": "1"}),
    R("POST", "/admin/users/{user_id}/password", "owner", "Passwort eines Benutzers setzen", csrf="form",
      form={"password": "Neues-Langes-Passwort-1"}),
    R("POST", "/admin/users/{user_id}/delete", "owner", "Benutzer löschen", csrf="form"),
    R("GET", "/admin/groups", "owner", "Rechtegruppen"),
    R("POST", "/admin/groups", "owner", "Rechtegruppe speichern", csrf="form",
      form={"name": "Gekapert", "perm__sftp.delete": "1"}),
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

    # -- Owner: System ----------------------------------------------------------------------
    R("GET", "/admin/system", "owner", "System: Übersicht und Prüfungen"),
    R("POST", "/admin/branding", "owner", "Name, Standard-Theme, Standardsprache", csrf="form",
      form={"brand_name": "Gekapert", "show_name": "1"}),
    R("POST", "/admin/branding/logo/{kind}", "owner", "Eigenes Logo hochladen", csrf="form", upload="logo"),
    R("POST", "/admin/branding/logo/{kind}/delete", "owner", "Eigenes Logo entfernen", csrf="form"),
    R("GET", "/admin/mail", "owner", "E-Mail-Einstellungen"),
    R("POST", "/admin/mail", "owner", "E-Mail-Einstellungen speichern", csrf="form",
      form={"enabled": "1", "host": "smtp.evil.example", "port": "587", "security": "starttls",
            "sender": "a@evil.example", "base_url": "https://evil.example"}),
    R("POST", "/admin/mail/password/delete", "owner", "SMTP-Passwort entfernen", csrf="form"),
    R("POST", "/admin/mail/test", "owner", "Testmail senden", csrf="form", form={"to": "a@example.com"}),
    R("GET", "/admin/notify", "owner", "Benachrichtigungen"),
    R("POST", "/admin/notify", "owner", "Benachrichtigungen speichern", csrf="form",
      form={"webhook_url": "https://evil.example/hook", "disk_pct": "90", "ram_pct": "90", "ram_minutes": "5"}),
    R("POST", "/admin/notify/test", "owner", "Test-Webhook senden", csrf="form"),
    R("GET", "/admin/statuspage", "owner", "Statusseite einstellen"),
    R("POST", "/admin/statuspage", "owner", "Statusseite speichern", csrf="form",
      form={"enabled": "1", "show__srv-a": "1", "field__password": "1", "password_confirm": "1"}),
    R("GET", "/admin/announcements", "owner", "Ankündigungen"),
    R("POST", "/admin/announcements", "owner", "Ankündigung speichern", csrf="form", form={"text": "Gekapert"}),
    R("POST", "/admin/announcements/{ann_id}/delete", "owner", "Ankündigung löschen", csrf="form"),
    R("GET", "/admin/privacy", "owner", "Datenschutz"),
    R("POST", "/admin/privacy/retention", "owner", "Aufbewahrungsfristen", csrf="form",
      form={"audit_days": "1", "console_days": "0"}),
    R("POST", "/admin/privacy/legal/{page}", "owner", "Impressum/Datenschutzerklärung", csrf="form",
      form={"enabled": "1", "text": "Gekapert"}),
    R("GET", "/admin/transfer", "owner", "Datenumzug"),
    R("POST", "/admin/transfer/export", "owner", "Alle Daten verschlüsselt exportieren (Passwort nötig)",
      csrf="form", form={"passphrase": "x" * 12, "repeat": "x" * 12, "current": PASSWORD}),
    R("POST", "/admin/transfer/import", "owner", "Daten importieren und ersetzen (Passwort nötig)", csrf="form",
      form={"passphrase": "x" * 12, "current": PASSWORD, "confirm": "1"}, upload="file"),
    R("POST", "/admin/system/update-check", "owner", "Nach Updates suchen", csrf="form"),
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
        "",
        "## Rechte",
        "",
        "| Recht | Name | Schreibrecht | Setzt voraus | Routen |",
        "|---|---|---|---|---|",
    ]
    for p in PERMISSIONS:
        used = sorted({f"`{r.method} {r.path}`" + (f" ({r.case})" if r.case else "")
                       for r in ROUTES if r.perm == p.key})
        out.append(f"| `{p.key}` | {p.label} | {'✎' if p.write else ''} | "
                   f"{', '.join(f'`{d}`' for d in p.requires) or '–'} | {'<br>'.join(used)} |")
    for access, title in ACCESS_TITLES.items():
        rows = [r for r in ROUTES if r.access == access]
        out += ["", f"## {title}", ""]
        if access in ("server", "automation"):
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
