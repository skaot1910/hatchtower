"""SQLite-Datenbank mit versionierten Migrationen.

Der Zugriff laeuft ausschliesslich ueber dieses Modul, damit ein spaeterer
Wechsel auf PostgreSQL an einer Stelle passieren kann.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_db_path: Path | None = None

MIGRATIONS: list[str] = [
    # 1: Grundschema
    """
    CREATE TABLE users (
        id             INTEGER PRIMARY KEY,
        username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash  TEXT NOT NULL,
        role           TEXT NOT NULL CHECK (role IN ('owner', 'user')),
        is_active      INTEGER NOT NULL DEFAULT 1,
        created_at     TEXT NOT NULL,
        last_login_at  TEXT,
        failed_logins  INTEGER NOT NULL DEFAULT 0,
        locked_until   TEXT
    );

    CREATE TABLE sessions (
        token_hash  TEXT PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf        TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        last_seen   TEXT NOT NULL,
        ip          TEXT,
        user_agent  TEXT
    );

    -- Spiegel der Pterodactyl-Daten (nur lesend synchronisiert)
    CREATE TABLE servers (
        identifier     TEXT PRIMARY KEY,
        ptero_id       INTEGER NOT NULL,
        uuid           TEXT NOT NULL,
        name           TEXT NOT NULL,
        node_id        INTEGER,
        egg            TEXT,
        allocations    TEXT NOT NULL DEFAULT '[]',
        primary_ip     TEXT,
        primary_port   INTEGER,
        limit_memory   INTEGER NOT NULL DEFAULT 0,
        limit_disk     INTEGER NOT NULL DEFAULT 0,
        limit_cpu      INTEGER NOT NULL DEFAULT 0,
        suspended      INTEGER NOT NULL DEFAULT 0,
        present        INTEGER NOT NULL DEFAULT 1,
        startup_name   TEXT,
        startup_password_enc TEXT,
        synced_at      TEXT
    );

    -- Eigene Angaben im Manager (Pterodactyl wird dadurch nicht veraendert)
    CREATE TABLE server_meta (
        identifier           TEXT PRIMARY KEY,
        logo_file            TEXT,
        ingame_name          TEXT,
        password_enc         TEXT,
        public_address       TEXT,
        query_port           INTEGER,
        updated_at           TEXT,
        updated_by           INTEGER
    );

    -- Vom Monitoring beobachteter Zustand
    CREATE TABLE server_state (
        identifier       TEXT PRIMARY KEY,
        state            TEXT,
        last_started_at  TEXT,
        last_stopped_at  TEXT,
        cpu              REAL,
        memory_bytes     INTEGER,
        disk_bytes       INTEGER,
        uptime_ms        INTEGER,
        players          INTEGER,
        max_players      INTEGER,
        query_name       TEXT,
        query_port       INTEGER,
        updated_at       TEXT
    );

    CREATE TABLE user_server_permissions (
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        identifier  TEXT NOT NULL,
        permission  TEXT NOT NULL,
        PRIMARY KEY (user_id, identifier, permission)
    );

    CREATE TABLE audit_log (
        id        INTEGER PRIMARY KEY,
        ts        TEXT NOT NULL,
        user_id   INTEGER,
        username  TEXT,
        ip        TEXT,
        action    TEXT NOT NULL,
        server    TEXT,
        detail    TEXT,
        result    TEXT NOT NULL
    );
    CREATE INDEX audit_ts ON audit_log(ts);

    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log ist nur anhaengbar'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log ist nur anhaengbar'); END;
    """,
    # 2: Automationen
    """
    CREATE TABLE automation_settings (
        identifier     TEXT PRIMARY KEY,
        crash_restart  INTEGER NOT NULL DEFAULT 0,
        updated_at     TEXT,
        updated_by     INTEGER
    );

    CREATE TABLE schedules (
        id               INTEGER PRIMARY KEY,
        identifier       TEXT NOT NULL,
        name             TEXT NOT NULL,
        action           TEXT NOT NULL CHECK (action IN ('start', 'stop', 'restart', 'command')),
        command          TEXT,
        weekdays         TEXT NOT NULL,
        time             TEXT NOT NULL,
        only_if_running  INTEGER NOT NULL DEFAULT 1,
        warn_minutes     INTEGER,
        warn_command     TEXT,
        enabled          INTEGER NOT NULL DEFAULT 1,
        last_run         TEXT,
        last_warn        TEXT,
        last_result      TEXT,
        created_by       INTEGER,
        created_at       TEXT NOT NULL
    );

    CREATE TABLE crash_events (
        id           INTEGER PRIMARY KEY,
        identifier   TEXT NOT NULL,
        detected_at  TEXT NOT NULL,
        outcome      TEXT NOT NULL
    );
    """,
    # 3: Allgemeine Einstellungen (im Manager änderbar)
    """
    CREATE TABLE app_settings (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_at  TEXT,
        updated_by  INTEGER
    );
    INSERT INTO app_settings (key, value) VALUES ('public_host', '');
    """,
    # 4: Eigene Rechtegruppen
    """
    CREATE TABLE perm_groups (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description  TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT
    );

    CREATE TABLE perm_group_items (
        group_id    INTEGER NOT NULL REFERENCES perm_groups(id) ON DELETE CASCADE,
        permission  TEXT NOT NULL,
        PRIMARY KEY (group_id, permission)
    );

    -- Pro Benutzer und Server: entweder eine Gruppe oder individuelle Rechte
    CREATE TABLE user_server_groups (
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        identifier  TEXT NOT NULL,
        group_id    INTEGER NOT NULL REFERENCES perm_groups(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, identifier)
    );
    """,
    # 5: Verlauf von CPU, RAM und Netzwerk (72 Stunden)
    """
    CREATE TABLE metrics (
        identifier  TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        cpu         REAL,
        mem         INTEGER,
        rx          INTEGER,
        tx          INTEGER
    );
    CREATE INDEX metrics_ident_ts ON metrics(identifier, ts);
    """,
    # 6: Erscheinungsbild (Name und Logos des Managers)
    """
    INSERT INTO app_settings (key, value) VALUES ('brand_name', '');
    INSERT INTO app_settings (key, value) VALUES ('brand_show_name', '1');
    """,
    # 7: Themes pro Benutzer
    """
    ALTER TABLE users ADD COLUMN theme TEXT;
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('default_theme', 'nacht');
    """,
    # 8: Umbenennung zu Hatchtower: alter Standardname wird zum leeren eigenen Namen,
    #    Bezeichnung und öffentliche URL entfallen
    """
    UPDATE app_settings SET value = '' WHERE key = 'brand_name' AND value IN ('Crest Control', 'Hatchtower');
    DELETE FROM app_settings WHERE key IN ('conn_target_label', 'conn_public_url');
    """,
    # 9: Sprache pro Benutzer und Standardsprache
    """
    ALTER TABLE users ADD COLUMN language TEXT;
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('default_language', 'de');
    """,
    # 10: Aufbewahrung, E-Mail, Einladungen, Benachrichtigungen, Statusseite, Ankündigungen,
    #     einmalige Zeitpläne, Log-Archiv, Verfügbarkeit, Tags und Favoriten
    """
    -- Audit-Log: Einträge bleiben unveränderlich und lassen sich frühestens nach einem Tag löschen
    -- (für die einstellbare Aufbewahrungsdauer)
    DROP TRIGGER audit_no_delete;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
    WHEN OLD.ts > strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-1 day')
    BEGIN SELECT RAISE(ABORT, 'audit_log ist nur anhaengbar'); END;

    ALTER TABLE users ADD COLUMN email TEXT;
    ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN notify_email INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN notify_events TEXT;

    -- Einmal-Links: Einladung, Passwort zurücksetzen, E-Mail bestätigen
    CREATE TABLE tokens (
        token_hash  TEXT PRIMARY KEY,
        purpose     TEXT NOT NULL CHECK (purpose IN ('invite', 'reset', 'verify')),
        user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
        email       TEXT,
        data        TEXT,
        created_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        created_by  INTEGER
    );

    -- Sperrfrist für Benachrichtigungen (damit nichts im Minutentakt meldet)
    CREATE TABLE notify_log (
        key      TEXT PRIMARY KEY,
        sent_at  INTEGER NOT NULL
    );

    ALTER TABLE server_meta ADD COLUMN tags TEXT;
    ALTER TABLE server_meta ADD COLUMN on_status INTEGER NOT NULL DEFAULT 0;

    CREATE TABLE favorites (
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        identifier  TEXT NOT NULL,
        PRIMARY KEY (user_id, identifier)
    );

    CREATE TABLE announcements (
        id          INTEGER PRIMARY KEY,
        text        TEXT NOT NULL,
        level       TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warn')),
        starts_at   TEXT,
        ends_at     TEXT,
        public      INTEGER NOT NULL DEFAULT 0,
        created_by  INTEGER,
        created_at  TEXT NOT NULL
    );

    ALTER TABLE schedules ADD COLUMN run_at TEXT;

    -- Statuswechsel für die Verfügbarkeit (90 Tage)
    CREATE TABLE state_changes (
        identifier  TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        state       TEXT NOT NULL
    );
    CREATE INDEX state_changes_ident_ts ON state_changes(identifier, ts);

    -- Mitgeschriebene Konsole
    CREATE TABLE console_lines (
        id          INTEGER PRIMARY KEY,
        identifier  TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        level       TEXT,
        line        TEXT NOT NULL
    );
    CREATE INDEX console_lines_ident_ts ON console_lines(identifier, ts);

    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('audit_retention_days', '30');
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('console_retention_days', '3');
    """,
    # 11: Nur noch drei Themes (Dunkel, Mittel, Hell): Polar wird Hell, die übrigen Dunkel
    """
    UPDATE users SET theme = 'hell' WHERE theme = 'polar';
    UPDATE users SET theme = NULL WHERE theme IS NOT NULL AND theme NOT IN ('nacht', 'mittel', 'hell');
    UPDATE app_settings SET value = 'hell' WHERE key = 'default_theme' AND value = 'polar';
    UPDATE app_settings SET value = 'nacht' WHERE key = 'default_theme' AND value NOT IN ('nacht', 'mittel', 'hell');
    """,
    # 12: Spielerzahl im Verlauf (für die Statusseite) und Link zum Community-Discord
    """
    ALTER TABLE metrics ADD COLUMN players INTEGER;
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('community_discord', 'https://discord.gg/uQDCrtjdKG');
    """,
    # 13: neue Meldungen (CPU dauerhaft hoch, Pterodactyl ausgefallen/wieder da) beim Webhook mit einschalten,
    #     wenn dort schon eine Auswahl gespeichert ist; Spielerverlauf von der Statusseite entfernt
    """
    UPDATE app_settings SET value = value || ',cpu,panel_down,panel_up'
        WHERE key = 'notify_webhook_events' AND value != '';
    UPDATE app_settings SET value = replace(replace(value, ',players', ''), 'players,', '')
        WHERE key = 'status_fields';
    """,
    # 14: „Alle Server, auch künftige“ pro Benutzer; neue Server warten auf Durchsicht durch einen Owner
    """
    CREATE TABLE user_default_access (
        user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        group_id    INTEGER NOT NULL REFERENCES perm_groups(id) ON DELETE CASCADE,
        updated_at  TEXT
    );
    ALTER TABLE servers ADD COLUMN review_pending INTEGER NOT NULL DEFAULT 0;
    UPDATE app_settings SET value = value || ',new_server'
        WHERE key = 'notify_webhook_events' AND value != '';
    """,
    # 15: angezeigtes Spiel pro Server überschreibbar (sonst Egg-Name aus Pterodactyl)
    """
    ALTER TABLE server_meta ADD COLUMN game_name TEXT;
    """,
    # 16: Einladungen mit Beschreibung und mehreren Nutzungen (0 = unbegrenzt), Notiz pro Benutzer,
    #     eigenes Recht fürs Konsolen-Archiv (bekommt, wer bisher die Konsole lesen durfte)
    """
    ALTER TABLE tokens ADD COLUMN note TEXT;
    ALTER TABLE tokens ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE tokens ADD COLUMN uses INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN note TEXT;
    INSERT OR IGNORE INTO perm_group_items (group_id, permission)
        SELECT group_id, 'console.archive' FROM perm_group_items WHERE permission = 'console.read';
    INSERT OR IGNORE INTO user_server_permissions (user_id, identifier, permission)
        SELECT user_id, identifier, 'console.archive' FROM user_server_permissions WHERE permission = 'console.read';
    """,
    # 17: Einladungslink zusätzlich verschlüsselt speichern, damit Owner ihn später noch einmal kopieren können
    """
    ALTER TABLE tokens ADD COLUMN token_enc TEXT;
    """,
    # 18: Backups – Ziele (Ordner im Hatchtower-Container oder SMB-Freigabe), Jobs mit Servern und Pfaden,
    #     Läufe (ein Eintrag pro Server und Lauf); neue Meldungen beim Webhook mit einschalten
    """
    CREATE TABLE backup_targets (
        id                INTEGER PRIMARY KEY,
        name              TEXT NOT NULL,
        kind              TEXT NOT NULL DEFAULT 'local' CHECK (kind IN ('local', 'smb')),
        path              TEXT NOT NULL DEFAULT '',
        smb_host          TEXT,
        smb_share         TEXT,
        smb_user          TEXT,
        smb_password_enc  TEXT,
        created_at        TEXT NOT NULL,
        updated_at        TEXT
    );

    -- target_id NULL = Direktdownload (nur manuell)
    CREATE TABLE backup_jobs (
        id            INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        target_id     INTEGER REFERENCES backup_targets(id),
        all_servers   INTEGER NOT NULL DEFAULT 0,
        excludes      TEXT,
        weekdays      TEXT NOT NULL DEFAULT '',
        time          TEXT,
        stop_mode     TEXT NOT NULL DEFAULT 'none' CHECK (stop_mode IN ('none', 'restart', 'stop')),
        warn_minutes  INTEGER,
        warn_command  TEXT,
        keep_count    INTEGER,
        keep_days     INTEGER,
        compress      INTEGER NOT NULL DEFAULT 0,
        notify        TEXT NOT NULL DEFAULT 'errors' CHECK (notify IN ('never', 'errors', 'always')),
        enabled       INTEGER NOT NULL DEFAULT 1,
        last_run      TEXT,
        last_result   TEXT,
        created_by    INTEGER,
        created_at    TEXT NOT NULL,
        updated_at    TEXT
    );

    -- path '/' = ganzer Server
    CREATE TABLE backup_job_items (
        job_id      INTEGER NOT NULL REFERENCES backup_jobs(id) ON DELETE CASCADE,
        identifier  TEXT NOT NULL,
        path        TEXT NOT NULL,
        PRIMARY KEY (job_id, identifier, path)
    );

    CREATE TABLE backup_runs (
        id             INTEGER PRIMARY KEY,
        batch          TEXT NOT NULL,
        job_id         INTEGER REFERENCES backup_jobs(id) ON DELETE SET NULL,
        job_name       TEXT NOT NULL,
        identifier     TEXT NOT NULL,
        server_name    TEXT,
        target_id      INTEGER,
        kind           TEXT NOT NULL DEFAULT 'target' CHECK (kind IN ('target', 'download')),
        file           TEXT,
        size           INTEGER,
        files          INTEGER,
        data_bytes     INTEGER,
        status         TEXT NOT NULL,
        detail         TEXT,
        trigger        TEXT,
        started_by     INTEGER,
        started_name   TEXT,
        power_restore  INTEGER NOT NULL DEFAULT 0,
        queued_at      TEXT NOT NULL,
        started_at     TEXT,
        finished_at    TEXT
    );
    CREATE INDEX backup_runs_job ON backup_runs(job_id, identifier);

    UPDATE app_settings SET value = value || ',backup_failed,backup_ok'
        WHERE key = 'notify_webhook_events' AND value != '';
    """,
    # 19: Mehrere Discord-Kanäle mit eigener Server-Auswahl (unabhängig von der Statusseite).
    #     Ein bisher eingerichteter Webhook wird übernommen, mit den Servern, die er bisher gezeigt hat.
    """
    CREATE TABLE status_feeds (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        url_enc     TEXT NOT NULL,
        enabled     INTEGER NOT NULL DEFAULT 1,
        title       TEXT,
        fields      TEXT NOT NULL DEFAULT 'name,status,address',
        msg_ids     TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        updated_at  TEXT
    );
    CREATE TABLE status_feed_servers (
        feed_id     INTEGER NOT NULL REFERENCES status_feeds(id) ON DELETE CASCADE,
        identifier  TEXT NOT NULL,
        PRIMARY KEY (feed_id, identifier)
    );
    INSERT INTO status_feeds (name, url_enc, enabled, fields, msg_ids, created_at)
        SELECT 'Discord', u.value,
               COALESCE((SELECT value FROM app_settings WHERE key = 'status_discord_enabled'), '0') = '1',
               COALESCE((SELECT value FROM app_settings WHERE key = 'status_discord_fields'), 'name,status,address'),
               COALESCE((SELECT value FROM app_settings WHERE key = 'status_discord_msg'), ''),
               strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
        FROM app_settings u WHERE u.key = 'status_discord_url_enc' AND COALESCE(u.value, '') != '';
    INSERT INTO status_feed_servers (feed_id, identifier)
        SELECT f.id, m.identifier FROM status_feeds f, server_meta m WHERE m.on_status = 1;
    DELETE FROM app_settings WHERE key IN ('status_discord_url_enc', 'status_discord_enabled', 'status_discord_fields',
                                           'status_discord_msg');
    """,
    # 20: Ergebnis der letzten Prüfung eines Backup-Ziels (Test, Speichern oder Backup-Lauf)
    """
    ALTER TABLE backup_targets ADD COLUMN checked_at TEXT;
    ALTER TABLE backup_targets ADD COLUMN check_ok INTEGER;
    ALTER TABLE backup_targets ADD COLUMN check_free INTEGER;
    ALTER TABLE backup_targets ADD COLUMN check_error TEXT;
    """,
    # 21: Protokoll pro Server liest das Audit-Log nach Server
    """
    CREATE INDEX audit_server ON audit_log(server, id);
    """,
]

RETENTION_CHOICES = (1, 3, 7, 14, 30, 60, 90)


def get_setting(key: str, default: str = "") -> str:
    with connect() as con:
        row = con.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str, user_id: int | None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at,"
            " updated_by = excluded.updated_by",
            (key, value, utcnow(), user_id),
        )


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(path: Path) -> None:
    global _db_path
    _db_path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        for index, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            con.executescript(f"BEGIN; {sql}; PRAGMA user_version = {index}; COMMIT;")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    if _db_path is None:
        raise RuntimeError("Datenbank nicht initialisiert")
    con = sqlite3.connect(_db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
