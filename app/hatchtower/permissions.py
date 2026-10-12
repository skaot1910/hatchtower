"""Rechtemodell von Hatchtower.

Grundsatz: Alles ist verboten, was nicht ausdruecklich erlaubt ist.
Rechte werden pro Benutzer und pro Server vergeben. Owner duerfen alles.
Schreibrechte wirken nur, wenn MANAGER_MODE=read-write gesetzt ist.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import db
from .i18n import t


@dataclass(frozen=True)
class Permission:
    key: str
    label: str
    group: str
    description: str
    write: bool = False
    requires: tuple[str, ...] = ()


PERMISSIONS: tuple[Permission, ...] = (
    Permission("server.view", "Sehen", "Allgemein",
               "Server in der Übersicht sehen: Status, CPU, RAM, Speicher, Spieler, IP. Ohne dieses Recht ist der Server unsichtbar."),
    Permission("server.password", "Passwort sehen", "Allgemein",
               "Das Beitritts-Passwort in Klarschrift sehen.", requires=("server.view",)),
    Permission("server.log", "Protokoll sehen", "Allgemein",
               "Das Protokoll des Servers sehen: wer ihn wann gestartet, gestoppt oder neu gestartet hat, Abstürze,"
               " Statuswechsel, Konsolenbefehle, Dateiänderungen, Zeitpläne und Backups. Ohne IP-Adressen.",
               requires=("server.view",)),
    Permission("server.manage", "Server verwalten", "Allgemein",
               "Logo, Ingame-Servername, Passwort und öffentliche Adresse im Manager pflegen.", requires=("server.view",)),
    Permission("power.start", "Start", "Steuerung", "Server starten.", write=True, requires=("server.view",)),
    Permission("power.stop", "Stop", "Steuerung", "Server stoppen.", write=True, requires=("server.view",)),
    Permission("power.restart", "Restart", "Steuerung", "Server neu starten.", write=True, requires=("server.view",)),
    Permission("console.read", "Konsole lesen", "Konsole", "Das Live-Log mitlesen.", requires=("server.view",)),
    Permission("console.command", "Befehle senden", "Konsole", "Befehle in die Konsole schreiben.",
               write=True, requires=("server.view", "console.read")),
    Permission("console.archive", "Konsolen-Archiv sehen", "Konsole",
               "Das mitgeschriebene Konsolen-Archiv durchsuchen und herunterladen.", requires=("server.view",)),
    Permission("sftp.view", "Dateien ansehen", "Dateien", "Ordner und Dateien auflisten.", requires=("server.view",)),
    Permission("sftp.download", "Herunterladen", "Dateien", "Dateien herunterladen.",
               requires=("server.view", "sftp.view")),
    Permission("sftp.upload", "Hochladen", "Dateien", "Dateien hochladen.", write=True,
               requires=("server.view", "sftp.view")),
    Permission("sftp.edit", "Bearbeiten", "Dateien", "Dateien bearbeiten.", write=True,
               requires=("server.view", "sftp.view", "sftp.download")),
    Permission("sftp.rename", "Umbenennen", "Dateien", "Dateien und Ordner umbenennen.", write=True,
               requires=("server.view", "sftp.view")),
    Permission("sftp.create_directory", "Ordner anlegen", "Dateien", "Neue Ordner anlegen.", write=True,
               requires=("server.view", "sftp.view")),
    Permission("sftp.delete", "Löschen", "Dateien", "Dateien und Ordner löschen.", write=True,
               requires=("server.view", "sftp.view")),
    Permission("automation.manage", "Automationen verwalten", "Automationen",
               "Zeitpläne für diesen Server anlegen, pausieren und löschen und den Neustart nach Absturz ein- oder"
               " ausschalten. Als Aktion stehen nur die Steuerrechte zur Verfügung, die der Benutzer selbst hat.",
               write=True, requires=("server.view",)),
    Permission("backups.view", "Backups sehen", "Backups",
               "Backup-Jobs mit diesem Server, laufende Backups mit Fortschritt und gespeicherte Backups sehen.",
               requires=("server.view",)),
    Permission("backups.run", "Backups starten und herunterladen", "Backups",
               "Backup-Jobs von Hand starten, laufende abbrechen und gespeicherte Backups herunterladen. Ein"
               " Backup enthält die Dateien des Servers. Jobs, die den Server stoppen, nur mit den Steuerrechten dazu.",
               requires=("server.view", "backups.view")),
    Permission("backups.manage", "Backup-Jobs verwalten", "Backups",
               "Backup-Jobs anlegen, ändern und löschen und im Datei-Explorer Dateien und Ordner hinzufügen."
               " Jobs mit mehreren Servern nur, wenn das Recht für alle gilt; Stoppen vor dem Backup nur mit"
               " den Steuerrechten dazu.", requires=("server.view", "backups.view", "sftp.view")),
)

# Welches Recht ein Zeitplan mit dieser Aktion voraussetzt
AUTOMATION_NEEDS = {"start": "power.start", "stop": "power.stop", "restart": "power.restart", "command": "console.command"}

BY_KEY = {p.key: p for p in PERMISSIONS}
ALL_KEYS = tuple(BY_KEY)

def with_dependencies(keys: set[str]) -> set[str]:
    """Ergaenzt automatisch alle Rechte, die fuer die gewaehlten Rechte noetig sind."""
    result = {k for k in keys if k in BY_KEY}
    changed = True
    while changed:
        changed = False
        for key in list(result):
            for dep in BY_KEY[key].requires:
                if dep not in result:
                    result.add(dep)
                    changed = True
    return result


@dataclass
class UserContext:
    id: int
    username: str
    role: str
    read_only: bool
    grants: dict[str, set[str]]
    theme: str | None = None
    language: str | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def can(self, identifier: str, key: str) -> bool:
        perm = BY_KEY.get(key)
        if perm is None:
            return False
        if perm.write and self.read_only:
            return False
        if self.is_owner:
            return True
        return key in self.grants.get(identifier, set())

    def visible(self, identifier: str) -> bool:
        return self.can(identifier, "server.view")

    @property
    def can_automate(self) -> bool:
        """Darf die Seite Automationen öffnen (Owner oder Recht für mindestens einen Server)."""
        return self.is_owner or any("automation.manage" in keys for keys in self.grants.values())

    def automation_servers(self, known) -> set[str]:
        return {i for i in known if self.is_owner or self.can(i, "automation.manage")}

    def schedule_allowed(self, identifier: str, action: str) -> bool:
        need = AUTOMATION_NEEDS.get(action)
        return need is not None and (self.is_owner or (self.can(identifier, "automation.manage") and self.can(identifier, need)))

    @property
    def can_backup(self) -> bool:
        """Darf die Seite Backups öffnen (Owner oder Recht für mindestens einen Server)."""
        return self.is_owner or any("backups.view" in keys for keys in self.grants.values())

    def can_any(self, key: str) -> bool:
        return self.is_owner or any(key in keys for keys in self.grants.values())


# Welche Steuerrechte ein Backup-Job braucht, der den Server vorher stoppt
BACKUP_STOP_NEEDS = {"none": (), "stop": ("power.stop",), "restart": ("power.stop", "power.start")}

GROUP_ORDER = ("Allgemein", "Steuerung", "Konsole", "Dateien", "Automationen", "Backups")


def grouped_permissions() -> list[tuple[str, list[Permission]]]:
    return [(g, [p for p in PERMISSIONS if p.group == g]) for g in GROUP_ORDER]


# Ausdrücklich „kein Zugriff“ auf einen Server – Ausnahme von „Alle Server, auch künftige“
BLOCKED = "-"


def load_grants(user_id: int) -> dict[str, set[str]]:
    """Wirksame Rechte pro Server: eigene Einstellung pro Server (Gruppe, individuell oder
    ausdrücklich kein Zugriff) geht vor; alle übrigen Server bekommen „Alle Server“, falls gesetzt."""
    grants: dict[str, set[str]] = {}
    explicit: set[str] = set()
    with db.connect() as con:
        for row in con.execute(
            "SELECT identifier, permission FROM user_server_permissions WHERE user_id = ?", (user_id,)
        ):
            explicit.add(row["identifier"])
            if row["permission"] != BLOCKED:
                grants.setdefault(row["identifier"], set()).add(row["permission"])
        for row in con.execute(
            "SELECT g.identifier, i.permission FROM user_server_groups g"
            " JOIN perm_group_items i ON i.group_id = g.group_id WHERE g.user_id = ?", (user_id,)
        ):
            explicit.add(row["identifier"])
            grants.setdefault(row["identifier"], set()).add(row["permission"])
        default = {r["permission"] for r in con.execute(
            "SELECT i.permission FROM user_default_access d JOIN perm_group_items i ON i.group_id = d.group_id"
            " WHERE d.user_id = ?", (user_id,))}
        if default:
            for row in con.execute("SELECT identifier FROM servers WHERE present = 1"):
                if row["identifier"] not in explicit:
                    grants[row["identifier"]] = set(default)
    return {ident: with_dependencies(keys) for ident, keys in grants.items()}


def load_default_group(user_id: int) -> int | None:
    with db.connect() as con:
        row = con.execute("SELECT group_id FROM user_default_access WHERE user_id = ?", (user_id,)).fetchone()
    return row["group_id"] if row else None


def save_default_group(user_id: int, group_id: int | None) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM user_default_access WHERE user_id = ?", (user_id,))
        if group_id is not None:
            con.execute("INSERT INTO user_default_access (user_id, group_id, updated_at) VALUES (?, ?, ?)",
                        (user_id, group_id, db.utcnow()))


def users_with_default_access() -> list[dict]:
    """Benutzer, die über „Alle Server“ automatisch Zugriff auf neue Server bekommen."""
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT u.id, u.username, g.name AS group_name FROM user_default_access d"
            " JOIN users u ON u.id = d.user_id JOIN perm_groups g ON g.id = d.group_id"
            " WHERE u.role != 'owner' ORDER BY u.username COLLATE NOCASE")]


def load_assignments(user_id: int) -> dict[str, dict]:
    """Pro Server: {'mode': 'group'|'custom'|'none', 'group_id': …, 'keys': {…}}.
    'none' heißt: ausdrücklich kein Zugriff. Server ohne Eintrag folgen „Alle Server“."""
    result: dict[str, dict] = {}
    with db.connect() as con:
        for row in con.execute(
            "SELECT identifier, permission FROM user_server_permissions WHERE user_id = ?", (user_id,)
        ):
            if row["permission"] == BLOCKED:
                result[row["identifier"]] = {"mode": "none", "group_id": None, "keys": set()}
                continue
            result.setdefault(row["identifier"], {"mode": "custom", "group_id": None, "keys": set()})["keys"].add(
                row["permission"])
        for row in con.execute("SELECT identifier, group_id FROM user_server_groups WHERE user_id = ?", (user_id,)):
            result[row["identifier"]] = {"mode": "group", "group_id": row["group_id"], "keys": set()}
    return result


def save_assignments(user_id: int, assignments: dict[str, dict]) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM user_server_permissions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM user_server_groups WHERE user_id = ?", (user_id,))
        for ident, a in assignments.items():
            if a["mode"] == "none":
                con.execute("INSERT INTO user_server_permissions (user_id, identifier, permission) VALUES (?, ?, ?)",
                            (user_id, ident, BLOCKED))
            elif a["mode"] == "group":
                con.execute("INSERT INTO user_server_groups (user_id, identifier, group_id) VALUES (?, ?, ?)",
                            (user_id, ident, a["group_id"]))
            elif a["mode"] == "custom":
                con.executemany(
                    "INSERT INTO user_server_permissions (user_id, identifier, permission) VALUES (?, ?, ?)",
                    [(user_id, ident, k) for k in sorted(with_dependencies(a["keys"]))],
                )


def load_groups() -> list[dict]:
    with db.connect() as con:
        groups = con.execute("SELECT * FROM perm_groups ORDER BY name COLLATE NOCASE").fetchall()
        items = con.execute("SELECT group_id, permission FROM perm_group_items").fetchall()
        usage = {r["group_id"]: r["n"] for r in con.execute(
            "SELECT group_id, COUNT(*) AS n FROM user_server_groups GROUP BY group_id")}
    keys: dict[int, set[str]] = {}
    for it in items:
        keys.setdefault(it["group_id"], set()).add(it["permission"])
    return [{"id": g["id"], "name": g["name"], "description": g["description"] or "",
             "keys": [k for k in ALL_KEYS if k in keys.get(g["id"], set())], "usage": usage.get(g["id"], 0)}
            for g in groups]


def save_group(group_id: int | None, name: str, description: str, keys: set[str]) -> int:
    now = db.utcnow()
    with db.connect() as con:
        if group_id is None:
            group_id = con.execute(
                "INSERT INTO perm_groups (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, description, now, now)).lastrowid
        else:
            con.execute("UPDATE perm_groups SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                        (name, description, now, group_id))
            con.execute("DELETE FROM perm_group_items WHERE group_id = ?", (group_id,))
        con.executemany("INSERT INTO perm_group_items (group_id, permission) VALUES (?, ?)",
                        [(group_id, k) for k in sorted(with_dependencies(keys))])
    return group_id


def describe_access(assignments: dict[str, dict], groups: dict[int, str], server_names: dict[str, str],
                    default_group: int | None = None) -> list[dict]:
    """Kurzübersicht pro Server für die Benutzerliste."""
    rows = []
    if default_group is not None:
        rows.append({"server": t("Alle Server"), "label": groups.get(default_group, "?"), "kind": "all"})
    for ident, name in sorted(server_names.items(), key=lambda kv: kv[1].lower()):
        a = assignments.get(ident)
        if not a:
            continue
        if a["mode"] == "none":
            if default_group is not None:
                rows.append({"server": name, "label": t("ausgenommen"), "kind": "none"})
        elif a["mode"] == "group":
            rows.append({"server": name, "label": groups.get(a["group_id"], "?"), "kind": "group"})
        elif "server.view" in with_dependencies(a["keys"]):
            rows.append({"server": name, "label": t("individuell"), "kind": "custom"})
    return rows