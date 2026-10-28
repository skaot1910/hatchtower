"""SQLite-Datenbank mit versionierten Migrationen.

Der Zugriff laeuft ausschliesslich ueber dieses Modul, damit ein spaeterer
Wechsel auf PostgreSQL an einer Stelle passieren kann.
"""
from __future__ import annotations

import logging
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
    # 22: Mehrere Benachrichtigungs-Webhooks mit Titel, Beschreibung und optionaler Server-Auswahl
    #     (leer = alle Server). Der bisherige einzelne Webhook wird übernommen.
    #     Zeitpläne bekommen die Aktion „Update-Fenster“ (Start für eine Dauer, danach Stopp).
    """
    CREATE TABLE notify_webhooks (
        id          INTEGER PRIMARY KEY,
        title       TEXT NOT NULL,
        description TEXT,
        kind        TEXT NOT NULL DEFAULT 'discord',
        url_enc     TEXT NOT NULL,
        events      TEXT NOT NULL,
        servers     TEXT NOT NULL DEFAULT '',
        enabled     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL,
        updated_at  TEXT
    );
    INSERT INTO notify_webhooks (title, kind, url_enc, events, created_at)
        SELECT 'Webhook', COALESCE((SELECT value FROM app_settings WHERE key = 'notify_webhook_type'), 'discord'),
               u.value,
               COALESCE((SELECT value FROM app_settings WHERE key = 'notify_webhook_events'),
                        'crash,restarted,restart_failed,disk,ram,cpu,schedule_failed,panel_down,panel_up,new_server,backup_failed,backup_ok'),
               strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
        FROM app_settings u WHERE u.key = 'notify_webhook_url_enc' AND COALESCE(u.value, '') != '';
    DELETE FROM app_settings WHERE key IN ('notify_webhook_url_enc', 'notify_webhook_type', 'notify_webhook_events');

    CREATE TABLE schedules_new (
        id               INTEGER PRIMARY KEY,
        identifier       TEXT NOT NULL,
        name             TEXT NOT NULL,
        action           TEXT NOT NULL CHECK (action IN ('start', 'stop', 'restart', 'command', 'update')),
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
        created_at       TEXT NOT NULL,
        run_at           TEXT,
        duration_minutes INTEGER,
        window_until     TEXT
    );
    INSERT INTO schedules_new (id, identifier, name, action, command, weekdays, time, only_if_running, warn_minutes,
                               warn_command, enabled, last_run, last_warn, last_result, created_by, created_at, run_at)
        SELECT id, identifier, name, action, command, weekdays, time, only_if_running, warn_minutes,
               warn_command, enabled, last_run, last_warn, last_result, created_by, created_at, run_at FROM schedules;
    DROP TABLE schedules;
    ALTER TABLE schedules_new RENAME TO schedules;
    """,
    # Benutzer ohne E-Mail-Adresse (vom Owner festgelegt, auch wenn E-Mail sonst Pflicht ist)
    """
    ALTER TABLE users ADD COLUMN no_email INTEGER NOT NULL DEFAULT 0;
    """,
    # Ansicht pro Benutzer: 'simple' (nur das Wichtigste), 'full' oder NULL (Standard der Instanz)
    """
    ALTER TABLE users ADD COLUMN view_mode TEXT;
    INSERT OR IGNORE INTO app_settings (key, value) VALUES ('default_view', 'full');
    """,
    # Ansicht vom Owner festgelegt: der Benutzer kann nicht selbst umschalten
    """
    ALTER TABLE users ADD COLUMN view_locked INTEGER NOT NULL DEFAULT 0;
    """,
    # Backups: Ergebnis der Prüfung jeder Backup-Datei (alle Prüfsummen gelesen) und Wiederherstellungen
    """
    ALTER TABLE backup_runs ADD COLUMN verified_at TEXT;
    ALTER TABLE backup_runs ADD COLUMN verify_ok INTEGER;
    ALTER TABLE backup_runs ADD COLUMN verify_detail TEXT;
    CREATE TABLE backup_restores (
        id             INTEGER PRIMARY KEY,
        run_id         INTEGER REFERENCES backup_runs(id) ON DELETE SET NULL,
        identifier     TEXT NOT NULL,
        server_name    TEXT,
        job_name       TEXT,
        backup_at      TEXT,
        mode           TEXT NOT NULL CHECK (mode IN ('folder', 'overwrite')),
        dest           TEXT NOT NULL,
        status         TEXT NOT NULL,
        detail         TEXT,
        files          INTEGER,
        data_bytes     INTEGER,
        started_by     INTEGER,
        started_name   TEXT,
        power_restore  INTEGER NOT NULL DEFAULT 0,
        started_at     TEXT NOT NULL,
        finished_at    TEXT
    );
    CREATE INDEX backup_restores_run ON backup_restores(run_id);
    """,
    # 27: drop the community Discord that migration 12 used to preset on every install
    """
    DELETE FROM app_settings WHERE key = 'community_discord' AND value = 'https://discord.gg/uQDCrtjdKG';
    """,
    # 28: notification inbox (bell): entries with an audience, read state per user (inbox.py)
    """
    CREATE TABLE inbox (
        id          INTEGER PRIMARY KEY,
        created_at  TEXT NOT NULL,
        event       TEXT NOT NULL,
        audience    TEXT NOT NULL CHECK (audience IN ('owner', 'server', 'users')),
        identifier  TEXT,
        detail      TEXT,
        dedupe      TEXT
    );
    CREATE INDEX inbox_created ON inbox(created_at);
    CREATE INDEX inbox_dedupe ON inbox(dedupe, created_at);
    CREATE TABLE inbox_read (
        user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        entry_id  INTEGER NOT NULL REFERENCES inbox(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, entry_id)
    );
    ALTER TABLE users ADD COLUMN inbox_read_upto INTEGER NOT NULL DEFAULT 0;
    """,
    # 29: file search index per server (fileindex.py): one row per file or folder, plus crawl state
    """
    CREATE TABLE file_index (
        identifier   TEXT NOT NULL,
        dir          TEXT NOT NULL,
        name         TEXT NOT NULL,
        is_dir       INTEGER NOT NULL,
        size         INTEGER NOT NULL DEFAULT 0,
        modified_at  TEXT,
        gen          INTEGER NOT NULL,
        PRIMARY KEY (identifier, dir, name)
    ) WITHOUT ROWID;
    CREATE TABLE file_index_state (
        identifier   TEXT PRIMARY KEY,
        status       TEXT NOT NULL CHECK (status IN ('running', 'done', 'error')),
        gen          INTEGER NOT NULL,
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        dirs         INTEGER NOT NULL DEFAULT 0,
        entries      INTEGER NOT NULL DEFAULT 0,
        truncated    INTEGER NOT NULL DEFAULT 0,
        error        TEXT
    );
    """,
    # 30: the sidebar always shows the Hatchtower mark; the uploaded sidebar logo is no longer used
    """
    DELETE FROM app_settings WHERE key = 'brand_logo_header';
    """,
    # 31: is anyone on the server? verdict, deciding source and the evidence (presence.py)
    """
    ALTER TABLE server_state ADD COLUMN presence TEXT;
    ALTER TABLE server_state ADD COLUMN presence_source TEXT;
    ALTER TABLE server_state ADD COLUMN presence_players INTEGER;
    ALTER TABLE server_state ADD COLUMN presence_info TEXT;
    """,
    # 32: history of player detection changes (shown in the players tab, kept for 30 days)
    """
    CREATE TABLE presence_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier  TEXT NOT NULL,
        ts          INTEGER NOT NULL,
        presence    TEXT NOT NULL,
        source      TEXT,
        players     INTEGER,
        evidence    TEXT
    );
    CREATE INDEX presence_log_server ON presence_log (identifier, id);
    """,
    # 33: notice for owners when someone registers through an invitation link (like new servers);
    #     webhooks that report new servers also report new users
    """
    ALTER TABLE users ADD COLUMN review_pending INTEGER NOT NULL DEFAULT 0;
    UPDATE notify_webhooks SET events = events || ',new_user' WHERE ',' || events || ',' LIKE '%,new_server,%';
    """,
    # 34: finer backup permissions. Whoever saw backups keeps seeing the stored ones (backups.list); whoever could
    #     start backups keeps downloading stored backups and the direct ZIP download (both were part of backups.run)
    """
    INSERT OR IGNORE INTO perm_group_items (group_id, permission)
        SELECT group_id, 'backups.list' FROM perm_group_items WHERE permission = 'backups.view';
    INSERT OR IGNORE INTO user_server_permissions (user_id, identifier, permission)
        SELECT user_id, identifier, 'backups.list' FROM user_server_permissions WHERE permission = 'backups.view';
    INSERT OR IGNORE INTO perm_group_items (group_id, permission)
        SELECT group_id, k.key FROM perm_group_items, (SELECT 'backups.download' AS key UNION SELECT 'backups.direct') k
        WHERE permission = 'backups.run';
    INSERT OR IGNORE INTO user_server_permissions (user_id, identifier, permission)
        SELECT user_id, identifier, k.key FROM user_server_permissions,
               (SELECT 'backups.download' AS key UNION SELECT 'backups.direct') k
        WHERE permission = 'backups.run';
    """,
    # 35: save the game before a backup without stopping (console command, then wait), and the save-game job of a
    #     server: a direct-download job shown as "Download save game" on the server page
    """
    ALTER TABLE backup_jobs ADD COLUMN save_command TEXT;
    ALTER TABLE backup_jobs ADD COLUMN save_wait INTEGER;
    ALTER TABLE backup_jobs ADD COLUMN save_for TEXT;
    CREATE UNIQUE INDEX backup_jobs_save_for ON backup_jobs (save_for) WHERE save_for IS NOT NULL;
    """,
    # 36: outcome of the last delivery per webhook, so a silently broken webhook can be seen (support report)
    """
    ALTER TABLE notify_webhooks ADD COLUMN last_ok_at TEXT;
    ALTER TABLE notify_webhooks ADD COLUMN last_error_at TEXT;
    ALTER TABLE notify_webhooks ADD COLUMN last_error TEXT;
    """,
    # 37: webhooks that Discord no longer knows (deleted: HTTP 404, link invalid: HTTP 401) are switched off
    #     automatically instead of being retried forever; the HTTP code and time say why and when
    """
    ALTER TABLE notify_webhooks ADD COLUMN disabled_code INTEGER;
    ALTER TABLE notify_webhooks ADD COLUMN disabled_at TEXT;
    ALTER TABLE status_feeds ADD COLUMN disabled_code INTEGER;
    ALTER TABLE status_feeds ADD COLUMN disabled_at TEXT;
    """,
    # 38: panel backups whose file Wings no longer has (the panel still lists them): marked after the first failed
    #     download instead of offering a download that cannot work
    """
    CREATE TABLE panel_backup_missing (
        uuid        TEXT PRIMARY KEY,
        identifier  TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        detail      TEXT
    );
    """,
    # 39: donations the owner records by hand (a donation link reports nothing back); shown on the status page
    #     and the in-app donation page with the details chosen in the settings
    """
    CREATE TABLE donations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        donor        TEXT,
        amount_cents INTEGER,
        currency     TEXT NOT NULL DEFAULT 'EUR',
        donated_on   TEXT NOT NULL,
        note         TEXT,
        created_by   INTEGER,
        created_at   TEXT NOT NULL
    );
    """,
    # 40: "stay signed in" on the login page: such sessions last security.REMEMBER_DAYS without idle timeout
    """
    ALTER TABLE sessions ADD COLUMN remember INTEGER NOT NULL DEFAULT 0;
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
        if version > len(MIGRATIONS):
            # Datenbank einer neueren Version (z. B. nach einem Rückfall auf ältere Software): läuft meist,
            # aber neue Funktionen fehlen. Nicht still übergehen.
            logging.getLogger("hatchtower.db").warning(
                "Datenbank-Version %s ist neuer als diese Hatchtower-Version (%s)", version, len(MIGRATIONS))
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
