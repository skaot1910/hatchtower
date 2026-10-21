"""Audit-Log: jede Anmeldung, Aktion und Ablehnung wird angehängt, nie verändert."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db

log = logging.getLogger("hatchtower.audit")

ACTION_LABELS = {
    "login": "Anmeldung",
    "logout": "Abmeldung",
    "password.change": "Eigenes Passwort geändert",
    "user.create": "Benutzer angelegt",
    "user.update": "Benutzer geändert",
    "user.disable": "Benutzer deaktiviert",
    "user.enable": "Benutzer wieder aktiviert",
    "user.delete": "Benutzer gelöscht",
    "user.self_delete": "Eigenes Konto gelöscht",
    "system.update_check": "Nach Updates gesucht",
    "system.update_start": "Update angestoßen",
    "system.update_channel": "Vorab-Versionen umgeschaltet",
    "system.update": "Update installiert",
    "system.support": "Support-Bericht erstellt",
    "user.password_reset": "Passwort zurückgesetzt",
    "permissions.update": "Rechte geändert",
    "server.meta.update": "Serverangaben geändert",
    "server.logo.update": "Logo geändert",
    "server.password.view": "Passwort angezeigt",
    "console.open": "Konsole geöffnet",
    "files.list": "Dateien aufgelistet",
    "files.reindex": "Dateisuche neu indiziert",
    "files.download": "Datei heruntergeladen",
    "files.download_zip": "Als ZIP heruntergeladen",
    "access.denied": "Zugriff verweigert",
    "readonly.blocked": "Schreibzugriff blockiert",
    "preview.as_user": "Vorschau als Benutzer",
    "power.start": "Server gestartet",
    "power.stop": "Server gestoppt",
    "power.restart": "Server neu gestartet",
    "power.kill": "Server hart beendet",
    "console.command": "Konsolenbefehl",
    "files.upload": "Dateien hochgeladen",
    "files.edit": "Datei bearbeitet",
    "files.rename": "Umbenannt",
    "files.move": "Verschoben",
    "files.mkdir": "Ordner angelegt",
    "files.delete": "Gelöscht",
    "automation.settings": "Absturz-Neustart geändert",
    "server.new": "Neuer Server erkannt",
    "server.reviewed": "Neuen Server als erledigt markiert",
    "user.reviewed": "Neuen Benutzer als erledigt markiert",
    "automation.crash": "Absturz erkannt",
    "schedule.create": "Zeitplan angelegt",
    "schedule.update": "Zeitplan geändert",
    "schedule.delete": "Zeitplan gelöscht",
    "schedule.run": "Zeitplan ausgeführt",
    "schedule.warn": "Zeitplan-Vorwarnung",
    "settings.update": "Einstellungen geändert",
    "group.save": "Rechtegruppe gespeichert",
    "setup.owner": "Einrichtung: Owner angelegt",
    "connection.update": "Verbindung geändert",
    "connection.check": "Verbindung geprüft (Assistent)",
    "connection.admin_key": "Admin-Key geändert",
    "server.admin.details": "Panel: Name/Beschreibung geändert",
    "server.admin.build": "Panel: Ressourcen geändert",
    "server.admin.ports": "Panel: Ports des Servers geändert",
    "server.admin.startup": "Panel: Startparameter geändert",
    "server.admin.reinstall": "Panel: Neuinstallation gestartet",
    "server.admin.suspend": "Panel: Server gesperrt/entsperrt",
    "server.admin.delete": "Panel: Server gelöscht",
    "server.admin.create": "Panel: Server angelegt",
    "node.ports.create": "Panel: Ports angelegt",
    "node.ports.delete": "Panel: Ports gelöscht",
    "theme.update": "Theme geändert",
    "view.update": "Ansicht geändert",
    "group.delete": "Rechtegruppe gelöscht",
    "user.invite": "Einladung erstellt",
    "user.invite_revoke": "Einladung zurückgezogen",
    "invite.accept": "Benutzer per Einladung angelegt",
    "invite.used_up": "Einladung aufgebraucht",
    "invite.expire": "Einladung abgelaufen",
    "user.export": "Benutzerdaten exportiert",
    "password.reset_request": "Passwort-Link angefordert",
    "password.reset_mail": "Passwort per Link zurückgesetzt",
    "email.change": "E-Mail-Adresse geändert",
    "email.verify": "E-Mail-Adresse bestätigt",
    "settings.mail": "E-Mail-Einstellungen geändert",
    "settings.network": "Netzwerk-Einstellungen geändert",
    "settings.notify": "Benachrichtigungen geändert",
    "notify.unsubscribe": "E-Mail-Benachrichtigungen per Link abbestellt",
    "settings.status": "Statusseite geändert",
    "settings.retention": "Aufbewahrung geändert",
    "settings.legal": "Impressum/Datenschutz geändert",
    "announcement.save": "Ankündigung gespeichert",
    "announcement.delete": "Ankündigung gelöscht",
    "transfer.export": "Daten exportiert",
    "transfer.import": "Daten importiert",
    "audit.export": "Audit-Log exportiert",
    "files.restore": "Datei wiederhergestellt",
    "console.archive_download": "Konsolen-Archiv heruntergeladen",
    "backup.job.create": "Backup-Job angelegt",
    "backup.job.update": "Backup-Job geändert",
    "backup.job.delete": "Backup-Job gelöscht",
    "backup.start": "Backup gestartet",
    "backup.run": "Backup gelaufen",
    "backup.warn": "Backup-Vorwarnung",
    "backup.save": "Spielstand vor dem Backup gespeichert",
    "backup.download": "Backup direkt heruntergeladen",
    "backup.file_download": "Gespeichertes Backup heruntergeladen",
    "backup.file_delete": "Gespeichertes Backup gelöscht",
    "backup.cancel": "Backup abgebrochen",
    "backup.verify": "Backup geprüft",
    "backup.restore": "Backup wiederhergestellt",
    "backup.target.save": "Backup-Ziel angelegt",
    "backup.target.delete": "Backup-Ziel entfernt",
}


def record(
    action: str,
    *,
    result: str = "ok",
    user_id: int | None = None,
    username: str | None = None,
    ip: str | None = None,
    server: str | None = None,
    detail: Any = None,
) -> None:
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)
    try:
        with db.connect() as con:
            con.execute(
                "INSERT INTO audit_log (ts, user_id, username, ip, action, server, detail, result)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (db.utcnow(), user_id, username, ip, action, server, detail, result),
            )
    except Exception:  # Audit darf die Anwendung nie stilllegen, wird aber laut protokolliert
        log.exception("Audit-Eintrag konnte nicht geschrieben werden: %s", action)


# Filter value for entries without a user (e.g. requests before login)
NO_USER = "–"
# Names the application itself writes into the user column
INTERNAL_ACTORS = ("System", "Automation", "Backup", "Monitoring", "Konsole")


def user_choices() -> list[tuple[str, str]]:
    """Suggestions for the user filter: current accounts first, then internal actors, then every other
    name the log contains (deleted accounts, failed logins), last „no user“. Pairs of (value, hint)."""
    with db.connect() as con:
        accounts = [r["username"] for r in con.execute("SELECT username FROM users ORDER BY username COLLATE NOCASE")]
        logged = [r["username"] for r in con.execute(
            "SELECT DISTINCT username FROM audit_log WHERE username IS NOT NULL AND username != ''"
            " ORDER BY username COLLATE NOCASE LIMIT 500")]
        anonymous = con.execute("SELECT 1 FROM audit_log WHERE username IS NULL OR username = '' LIMIT 1").fetchone()
    known = {name.lower() for name in accounts}
    choices = [(name, "") for name in accounts]
    choices += [(name, "Intern") for name in INTERNAL_ACTORS if name in logged]
    known |= {name.lower() for name in INTERNAL_ACTORS}
    choices += [(name, "Kein Konto (gelöscht oder fehlgeschlagene Anmeldung)")
                for name in logged if name.lower() not in known]
    if anonymous:
        choices.append((NO_USER, "Ohne Benutzer"))
    return choices


def match_actions(text: str, translate) -> list[str]:
    """Actions for the action filter: an action key, an exact label, or every action whose label (in German
    or the current language) or key contains the text. Empty list when nothing fits."""
    text = text.strip().casefold()
    if text in ACTION_LABELS:
        return [text]
    labels = {key: (label.casefold(), translate(label).casefold()) for key, label in ACTION_LABELS.items()}
    exact = [key for key, names in labels.items() if text in names]
    return exact or [key for key, names in labels.items() if any(text in n for n in names) or text in key]


def query(limit: int = 100, offset: int = 0, action: str | list[str] | None = None, username: str | None = None,
          server: str | None = None, ip: str | None = None) -> tuple[list, int]:
    where, params = [], []
    if isinstance(action, list):
        where.append(f"action IN ({','.join('?' * len(action))})" if action else "0")
        params.extend(action)
    elif action:
        where.append("action = ?")
        params.append(action)
    if username == NO_USER:
        where.append("(username IS NULL OR username = '')")
    elif username:
        where.append("username = ? COLLATE NOCASE")
        params.append(username)
    if server:
        where.append("server = ?")
        params.append(server)
    if ip:
        # part of an address is enough, e.g. "192.168."
        where.append("ip LIKE ? ESCAPE '\\'")
        params.append("%" + ip.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with db.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM audit_log {clause}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, limit, offset]
        ).fetchall()
    return rows, total
