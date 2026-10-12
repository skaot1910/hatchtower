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
    "user.delete": "Benutzer gelöscht",
    "user.self_delete": "Eigenes Konto gelöscht",
    "system.update_check": "Nach Updates gesucht",
    "system.update_start": "Update angestoßen",
    "system.update": "Update installiert",
    "user.password_reset": "Passwort zurückgesetzt",
    "permissions.update": "Rechte geändert",
    "server.meta.update": "Serverangaben geändert",
    "server.logo.update": "Logo geändert",
    "server.password.view": "Passwort angezeigt",
    "console.open": "Konsole geöffnet",
    "files.list": "Dateien aufgelistet",
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
    "files.mkdir": "Ordner angelegt",
    "files.delete": "Gelöscht",
    "automation.settings": "Absturz-Neustart geändert",
    "server.new": "Neuer Server erkannt",
    "server.reviewed": "Neuen Server als erledigt markiert",
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
    "theme.update": "Theme geändert",
    "group.delete": "Rechtegruppe gelöscht",
    "user.invite": "Einladung erstellt",
    "user.invite_revoke": "Einladung zurückgezogen",
    "invite.accept": "Einladung angenommen",
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
    "backup.download": "Backup direkt heruntergeladen",
    "backup.file_download": "Gespeichertes Backup heruntergeladen",
    "backup.file_delete": "Gespeichertes Backup gelöscht",
    "backup.cancel": "Backup abgebrochen",
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


def query(limit: int = 100, offset: int = 0, action: str | None = None, username: str | None = None,
          server: str | None = None) -> tuple[list, int]:
    where, params = [], []
    if action:
        where.append("action = ?")
        params.append(action)
    if username:
        where.append("username = ? COLLATE NOCASE")
        params.append(username)
    if server:
        where.append("server = ?")
        params.append(server)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with db.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM audit_log {clause}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, limit, offset]
        ).fetchall()
    return rows, total
