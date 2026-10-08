"""Kommandozeile von Hatchtower.

Aufruf auf dem Server (als root):
  hatchtower setup-token      Einrichtungscode für den Assistenten erzeugen
  hatchtower create-owner     Owner-Konto auf der Konsole anlegen
  hatchtower reset-password   Passwort eines Benutzers neu setzen
  hatchtower selftest         Verbindung zu Pterodactyl prüfen (nur lesend)
  hatchtower version          Installierte Version anzeigen
  hatchtower update <paket>   Auf eine neue Version aktualisieren (Shell-Befehl, siehe /usr/local/bin/hatchtower)
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from . import APP_NAME, __version__, audit, db, i18n, security, setup
from .i18n import t
from .config import ConfigError, apply_db_values, load_settings
from .ptero import PteroClient, PteroError, ReadOnlyViolation


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
    if not settings.configured:
        print(t("Noch nicht eingerichtet: Panel-Adresse oder API-Keys fehlen. Bitte im Browser einrichten."))
        return 1
    client = PteroClient(settings)
    failed = 0
    print(t("{app} {version} – Selbsttest (nur lesend) gegen {url}", app=APP_NAME, version=__version__, url=settings.pterodactyl_url))
    try:
        try:
            nodes = await client.list_nodes()
            print("  ✓ " + t("Application-API: {n} Node(s)", n=len(nodes)))
        except PteroError as exc:
            failed += 1
            nodes = []
            print(t("  ✕ Application-API: ") + str(exc))
        try:
            servers = await client.list_servers()
            print("  ✓ " + t("Serverliste: {n} Server", n=len(servers)))
        except PteroError as exc:
            servers = []
            failed += 1
            print("  ✕ " + t("Serverliste: {grund}", grund=exc))
        if servers:
            try:
                res = await client.resources(servers[0].identifier)
                print("  ✓ " + t("Client-API: Status von „{name}“ = {state}", name=servers[0].name, state=res["state"]))
            except PteroError as exc:
                failed += 1
                print(t("  ✕ Client-API: ") + str(exc))
        for check in await setup.node_checks(client, settings.node_overrides) if nodes else []:
            ok = check["daemon_ok"] and check["sftp_ok"]
            failed += 0 if ok else 1
            reach = {True: t("erreichbar"), False: t("NICHT erreichbar")}
            print(f"  {'✓' if ok else '✕'} Wings {check['name']}: Daemon {check['host']}:{check['daemon_port']} "
                  f"{reach[check['daemon_ok']]}, SFTP {check['sftp_port']} {reach[check['sftp_ok']]}")
        print("  · " + t("Modus: {mode}", mode=t("nur lesen") if settings.read_only else t("lesen und steuern")))
        if settings.read_only:
            try:
                client._guard("POST")
                failed += 1
                print("  ✕ " + t("Read-Only-Sperre: POST wurde NICHT blockiert"))
            except ReadOnlyViolation:
                print("  ✓ " + t("Read-Only-Sperre greift"))
    finally:
        await client.close()
    return 1 if failed else 0


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
    sys.exit(asyncio.run(selftest(settings)))


if __name__ == "__main__":
    main()
