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
    # Hinweis für Admins, wenn das Recht mehr erlaubt, als der Name vermuten lässt
    note: str = ""


PERMISSIONS: tuple[Permission, ...] = (
    Permission("server.view", "Sehen", "Allgemein",
               "Server in der Übersicht sehen: Status, CPU, RAM, Speicher, Adresse. Ohne dieses Recht ist der Server unsichtbar."),
    Permission("server.password", "Passwort sehen", "Allgemein",
               "Das Beitritts-Passwort in Klarschrift sehen.", requires=("server.view",)),
    Permission("server.log", "Protokoll sehen", "Allgemein",
               "Das Protokoll des Servers sehen: wer ihn wann gestartet, gestoppt oder neu gestartet hat, Abstürze,"
               " Statuswechsel, Konsolenbefehle, Dateiänderungen, Zeitpläne und Backups. Ohne IP-Adressen.",
               requires=("server.view",)),
    Permission("server.players", "Spieler sehen", "Allgemein",
               "Den Reiter „Spieler“ sehen: ob gerade jemand auf dem Server ist, woran das erkannt wird, und den"
               " Verlauf. Die Namen der Spieler sind darin zu sehen.", requires=("server.view",)),
    Permission("server.manage", "Server verwalten", "Allgemein",
               "Logo, Ingame-Servername, Passwort und öffentliche Adresse im Manager pflegen.", requires=("server.view",)),
    Permission("power.start", "Start", "Steuerung", "Server starten.", write=True, requires=("server.view",)),
    Permission("power.stop", "Stop", "Steuerung", "Server stoppen.", write=True, requires=("server.view",)),
    Permission("power.restart", "Restart", "Steuerung", "Server neu starten.", write=True, requires=("server.view",)),
    Permission("console.read", "Konsole lesen", "Konsole", "Das Live-Log mitlesen.", requires=("server.view",)),
    Permission("console.command", "Befehle senden", "Konsole", "Befehle in die Konsole schreiben.",
               write=True, requires=("server.view", "console.read"),
               note="Je nach Spiel reichen Befehle weit, z. B. Spieler zu Admins machen oder die Welt verändern."),
    Permission("console.archive", "Konsolen-Archiv sehen", "Konsole",
               "Das mitgeschriebene Konsolen-Archiv durchsuchen und herunterladen.", requires=("server.view",)),
    Permission("sftp.view", "Dateien ansehen", "Dateien", "Ordner und Dateien auflisten.", requires=("server.view",)),
    Permission("sftp.download", "Herunterladen", "Dateien", "Dateien herunterladen.",
               requires=("server.view", "sftp.view"),
               note="Konfigurationsdateien können Passwörter und Zugangsschlüssel enthalten."),
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
    # Two kinds of backups: stored ones (on a backup target: drive in the container or network share) and the
    # direct download, which reads the current files from the live server and never stores anything
    Permission("backups.view", "Backups sehen", "Backups",
               "Die Seite Backups öffnen: Backup-Jobs mit diesem Server und laufende Vorgänge mit Fortschritt sehen.",
               requires=("server.view",)),
    Permission("backups.list", "Gespeicherte Backups sehen", "Backups",
               "Die auf dem Backup-Ziel (Laufwerk oder Netzlaufwerk) gespeicherten Backups sehen – die"
               " Wiederherstellungspunkte mit Datum, Größe und Prüfergebnis – und die bisherigen Wiederherstellungen."
               " Herunterladen oder zurückspielen geht damit noch nicht.",
               requires=("server.view", "backups.view")),
    Permission("backups.download", "Gespeicherte Backups herunterladen", "Backups",
               "Gespeicherte Backups als ZIP auf das eigene Gerät laden, z. B. um einen Spielstand offline"
               " weiterzuspielen. Startet, stoppt und ändert nichts.",
               requires=("server.view", "backups.view", "backups.list"),
               note="Gibt über das Backup Zugriff auf alle gesicherten Dateien – auch ohne die Rechte unter „Dateien“."),
    Permission("backups.run", "Backups starten", "Backups",
               "Backup-Jobs mit Ziel von Hand starten (es entsteht ein neues gespeichertes Backup), laufende abbrechen"
               " und gespeicherte Backups prüfen. Zusammen mit „Gespeicherte Backups herunterladen“ auch: ausführen"
               " und gleich herunterladen. Jobs, die den Server stoppen, nur mit den Steuerrechten dazu.",
               requires=("server.view", "backups.view", "backups.list")),
    Permission("backups.restore", "Gespeicherte Backups wiederherstellen", "Backups",
               "Ein gespeichertes Backup auf den Server zurückspielen – in einen eigenen Ordner oder an den"
               " ursprünglichen Ort, wobei gleichnamige Dateien ersetzt werden. Server vorher stoppen nur mit den"
               " Steuerrechten dazu.", write=True, requires=("server.view", "backups.view", "backups.list"),
               note="Ersetzt Dateien auf dem Server – auch ohne die Rechte unter „Dateien“."),
    Permission("backups.manage", "Backup-Jobs verwalten", "Backups",
               "Backup-Jobs und Direktdownload-Jobs anlegen, ändern und löschen, dabei Dateien und Ordner auswählen,"
               " und gespeicherte Backups löschen, dazu Backups des Panels, deren Datei auf dem Node fehlt. Jobs mit"
               " mehreren Servern nur, wenn das Recht für alle gilt;"
               " Stoppen vor dem Backup nur mit den Steuerrechten dazu.",
               requires=("server.view", "backups.view", "backups.list", "sftp.view")),
    Permission("backups.direct", "Direktdownload aus dem Livesystem", "Direktdownload",
               "Direktdownload-Jobs (Jobs ohne Backup-Ziel) für diesen Server ausführen: Hatchtower liest den"
               " aktuellen Stand der Dateien direkt vom laufenden Server und lädt ihn als ZIP auf das eigene Gerät."
               " Gespeichert wird dabei nichts. Den Job legt ein Owner oder wer Backup-Jobs verwalten darf an; Jobs,"
               " die den Server vorher stoppen, nur mit den Steuerrechten dazu.", requires=("server.view", "backups.view"),
               note="Gibt Zugriff auf alle Dateien, die der Job enthält – auch ohne die Rechte unter „Dateien“."),
    # Server administration in the panel: only with the optional admin key (write access)
    Permission("admin.view", "Panel-Einstellungen sehen", "Administration",
               "Name, Ressourcen, Ports und Startparameter des Servers so sehen, wie sie im Panel eingestellt sind.",
               requires=("server.view",), note="Startvariablen können Passwörter oder Zugangsschlüssel enthalten."),
    Permission("admin.details", "Name und Beschreibung", "Administration",
               "Name und Beschreibung des Servers im Panel ändern.", write=True, requires=("server.view", "admin.view")),
    Permission("admin.build", "Ressourcen und Ports", "Administration",
               "RAM, CPU, Speicher, die Limits für Datenbanken, Ports und Backups sowie die Ports des Servers im"
               " Panel ändern.", write=True, requires=("server.view", "admin.view"),
               note="Kann dem Server mehr Leistung geben, als der Node hat, und freie Ports des Nodes belegen."),
    Permission("admin.startup", "Startparameter", "Administration",
               "Startbefehl, Docker-Image und alle Startvariablen im Panel ändern, auch die, die das Panel für"
               " Benutzer sperrt.", write=True, requires=("server.view", "admin.view"),
               note="Startvariablen legen je nach Spiel Passwörter, Versionen oder Mods fest."),
    Permission("admin.reinstall", "Neu installieren", "Administration",
               "Den Server im Panel neu installieren lassen (Installationsskript des Eggs).", write=True,
               requires=("server.view", "admin.view"), note="Je nach Egg werden dabei Dateien des Servers überschrieben."),
)

# Welches Recht ein Zeitplan mit dieser Aktion voraussetzt
# Rechte, die ein Zeitplan je Aktion braucht (Update-Fenster startet und stoppt)
AUTOMATION_NEEDS = {"start": ("power.start",), "stop": ("power.stop",), "restart": ("power.restart",),
                    "command": ("console.command",), "update": ("power.start", "power.stop")}

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
    view_mode: str | None = None
    view_locked: bool = False

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
        return need is not None and (self.is_owner or (self.can(identifier, "automation.manage")
                                                       and all(self.can(identifier, n) for n in need)))

    @property
    def can_backup(self) -> bool:
        """Darf die Seite Backups öffnen (Owner oder Recht für mindestens einen Server)."""
        return self.is_owner or any("backups.view" in keys for keys in self.grants.values())

    def can_any(self, key: str) -> bool:
        return self.is_owner or any(key in keys for keys in self.grants.values())


# Welche Steuerrechte ein Backup-Job braucht, der den Server vorher stoppt
BACKUP_STOP_NEEDS = {"none": (), "stop": ("power.stop",), "restart": ("power.stop", "power.start")}

GROUP_ORDER = ("Allgemein", "Steuerung", "Konsole", "Dateien", "Automationen", "Backups", "Direktdownload",
               "Administration")


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


def creator_allows(created_by: int | None, identifier: str, need: set[str]) -> bool:
    """Geplante Aufgaben eines Benutzers (Zeitpläne, Backup-Jobs) laufen nur, solange er die nötigen
    Rechte für diesen Server noch hat. Ohne Ersteller (ältere Einträge) und von Ownern: erlaubt.
    Gibt es den Ersteller nicht mehr, läuft nichts."""
    if not created_by:
        return True
    with db.connect() as con:
        creator = con.execute("SELECT role, is_active FROM users WHERE id = ?", (created_by,)).fetchone()
    if creator is None:
        return False
    if creator["role"] == "owner":
        return True
    return bool(creator["is_active"]) and need <= load_grants(created_by).get(identifier, set())


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
        # „Alle Server, auch künftige“ zählt extra: Löschen der Gruppe nimmt diesen Benutzern jeden Zugriff
        default_users = {r["group_id"]: r["n"] for r in con.execute(
            "SELECT group_id, COUNT(*) AS n FROM user_default_access GROUP BY group_id")}
    keys: dict[int, set[str]] = {}
    for it in items:
        keys.setdefault(it["group_id"], set()).add(it["permission"])
    return [{"id": g["id"], "name": g["name"], "description": g["description"] or "",
             "keys": [k for k in ALL_KEYS if k in keys.get(g["id"], set())], "usage": usage.get(g["id"], 0),
             "default_users": default_users.get(g["id"], 0)}
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


# Vorschläge für den Anfang: von wenig zu viel, nur so viele Schreibrechte wie nötig
STARTER_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Gruppe|Zuschauer", "Sieht, ob der Server läuft, dazu Adresse und Passwort",
     ("server.view", "server.password")),
    ("Gruppe|Spieler", "Darf den Server außerdem starten und neu starten, aber nicht stoppen",
     ("server.view", "server.password", "power.start", "power.restart")),
    ("Gruppe|Moderator", "Steuert den Server, liest die Konsole, sendet Befehle und sieht das Protokoll",
     ("server.view", "server.password", "server.log", "server.players", "power.start", "power.stop", "power.restart",
      "console.read", "console.command", "console.archive")),
)


def starter_groups_missing(groups: list[dict]) -> bool:
    names = {g["name"].casefold() for g in groups}
    return any(t(key).casefold() not in names for key, _, _ in STARTER_GROUPS)


def create_starter_groups() -> list[str]:
    """Legt die Vorschlagsgruppen an, deren Name es noch nicht gibt. Gibt die neuen Namen zurück."""
    with db.connect() as con:
        existing = {r["name"].casefold() for r in con.execute("SELECT name FROM perm_groups")}
    created = []
    for key, description, keys in STARTER_GROUPS:
        name = t(key)
        if name.casefold() not in existing:
            save_group(None, name, t(description), set(keys))
            created.append(name)
    return created


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