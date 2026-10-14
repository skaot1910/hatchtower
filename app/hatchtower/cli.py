"""Kommandozeile von Hatchtower.

Aufruf auf dem Server (als root):
  hatchtower setup-token      Einrichtungscode für den Assistenten erzeugen
  hatchtower create-owner     Owner-Konto auf der Konsole anlegen
  hatchtower reset-password   Passwort eines Benutzers neu setzen
  hatchtower selftest         Verbindung zum Panel prüfen (nur lesend)
  hatchtower version          Installierte Version anzeigen
  hatchtower update <paket>   Auf eine neue Version aktualisieren (Shell-Befehl, siehe /usr/local/bin/hatchtower)
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from . import APP_NAME, __version__, audit, db, i18n, security, setup
from .i18n import t
from .config import ConfigError, apply_db_values, load_settings
from .panels import make_client
from .ptero import ReadOnlyViolation


def _ask_password(username: str) -> str | None:
    password = getpass.getpass(t("Passwort: "))
    problem = security.password_message(password, username)
    if problem:
        print(problem)
        return None
    if getpass.getpass(t("Passwort wiederholen: ")) != password:
        print(t("Die Passwörter stimmen nicht überein."))
        return None
    return password


def setup_token() -> int:
    if setup.users_exist():
        print(t("Es gibt bereits Benutzer. Der Einrichtungscode wird nur für das erste Konto gebraucht."))
        print(t("Passwort vergessen? Nutze: hatchtower reset-password"))
        return 1
    token = setup.new_setup_token()
    print(t("Einrichtungscode (24 Stunden gültig): {token}", token=token))
    return 0


def create_owner() -> int:
    username = input(t("Benutzername für den Owner: ")).strip()
    if not username or len(username) > 32 or not all(c.isalnum() or c in "-_." for c in username):
        print(t("Ungültiger Benutzername (1–32 Zeichen, Buchstaben, Zahlen, - _ .)."))
        return 1
    with db.connect() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            print(t("Diesen Benutzer gibt es schon."))
            return 1
    password = _ask_password(username)
    if password is None:
        return 1
    with db.connect() as con:
        con.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'owner', ?)",
                    (username, security.hash_password(password), db.utcnow()))
    setup.clear_setup_token()
    audit.record("user.create", username="Konsole", ip="lokal", detail={"user": username, "role": "owner"})
    print(t("Owner „{name}“ angelegt.", name=username))
    return 0


def reset_password() -> int:
    username = input(t("Benutzername: ")).strip()
    with db.connect() as con:
        row = con.execute("SELECT id, username FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        print(t("Diesen Benutzer gibt es nicht."))
        return 1
    password = _ask_password(row["username"])
    if password is None:
        return 1
    with db.connect() as con:
        con.execute("UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL, is_active = 1 "
                    "WHERE id = ?", (security.hash_password(password), row["id"]))
    security.delete_user_sessions(row["id"])
    audit.record("user.password_reset", username="Konsole", ip="lokal", detail={"user": row["username"]})
    print(t("Passwort für „{name}“ gesetzt. Das Konto ist entsperrt.", name=row["username"]))
    return 0


async def selftest(settings) -> int:
    """Gleiche Prüfung wie im Browser (diagnose.py), nur lesend. Rückgabe 1 nur bei echten Fehlern."""
    from . import diagnose
    if not settings.configured:
        print(t("Noch nicht eingerichtet: Panel-Adresse oder API-Keys fehlen. Bitte im Browser einrichten."))
        return 1
    print(t("{app} {version} – Selbsttest (nur lesend) gegen {url}", app=APP_NAME, version=__version__,
            url=f"{settings.panel.name} {settings.pterodactyl_url}"))
    report = await diagnose.run(settings, diagnose.candidate_from_settings(settings))
    db.set_setting("connection_last_check", json.dumps(diagnose.summary(report)), None)
    marks = {diagnose.ERROR: "✕", diagnose.WARNING: "!", diagnose.OPTIONAL: "○", diagnose.OK: "✓"}
    for key, label in diagnose.AREAS:
        checks = report.area(key)
        if key in report.skipped:
            print(f"\n{t(label)}\n  · " + t("Nicht geprüft – erst den Fehler oben beheben."))
            continue
        if not checks:
            continue
        print(f"\n{t(label)}")
        for c in checks:
            print(f"  {marks[c.level]} {t(c.title)}")
            if c.level in (diagnose.ERROR, diagnose.WARNING):
                if c.detail:
                    print(f"      {t(c.detail)}")
                for step in c.fix:
                    print(f"      → {t(step)}")
    print("\n  · " + t("Modus: {mode}", mode=t("nur lesen") if settings.read_only else t("lesen und steuern")))
    if settings.read_only:
        client = make_client(settings)
        try:
            client._guard("POST")
            print("  ✕ " + t("Read-Only-Sperre: POST wurde NICHT blockiert"))
            return 1
        except ReadOnlyViolation:
            print("  ✓ " + t("Read-Only-Sperre greift"))
        finally:
            await client.close()
    print("\n" + t("Ergebnis: {e} Fehler, {w} Warnung(en)", e=report.errors, w=report.warnings))
    return 1 if report.errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="hatchtower", description=APP_NAME)
    parser.add_argument("command", choices=["setup-token", "create-owner", "reset-password", "selftest", "version"])
    args = parser.parse_args()
    if args.command == "version":
        print(f"{APP_NAME} {__version__}")
        sys.exit(0)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(t("Konfigurationsfehler: {grund}", grund=exc))
        sys.exit(2)
    db.init(settings.db_path)
    i18n.use_default()
    if args.command == "setup-token":
        sys.exit(setup_token())
    if args.command == "create-owner":
        sys.exit(create_owner())
    if args.command == "reset-password":
        sys.exit(reset_password())
    vault = security.Vault(settings.data_key)
    with db.connect() as con:
        values = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM app_settings WHERE key LIKE 'conn_%'")}
    apply_db_values(settings, values, vault.decrypt)
    i18n.set_panel(settings.panel.name)
    sys.exit(asyncio.run(selftest(settings)))


if __name__ == "__main__":
    main()
