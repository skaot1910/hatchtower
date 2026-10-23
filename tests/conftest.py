"""Testumgebung: Hatchtower mit Wegwerf-Datenbank und nachgebildetem Pterodactyl.

Es wird nichts im Netz angesprochen. Das nachgebildete Pterodactyl (FakePtero) erbt den
echten Read-Only-Schutz von PteroClient und merkt sich jede Anfrage, damit Tests prüfen
können, dass eine verweigerte Aktion Pterodactyl nie erreicht hat.
"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="hatchtower-test-"))
os.environ["HATCHTOWER_SECRET_KEY"] = "test-secret-key-" + "x" * 32
os.environ["HATCHTOWER_DATA_KEY"] = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
os.environ["HATCHTOWER_DATA_DIR"] = str(_TMP / "unused")

from fastapi.testclient import TestClient  # noqa: E402

from hatchtower import accounts, backups, db, main, permissions, security, setup  # noqa: E402
from hatchtower.ptero import READ_METHODS, AdminKeyMissing, PteroClient, PteroError, ReadOnlyViolation  # noqa: E402

PASSWORD = "Sehr-Langes-Passwort-123"
FEED_URL = "https://discord.com/api/webhooks/123456789012345678/" + "f" * 68
_HASH = security.hash_password(PASSWORD)   # einmal berechnen, Argon2 ist absichtlich langsam


# -- Nachgebildetes Pterodactyl --------------------------------------------------------------

# Answers of the admin API (admin key) for srv-a (id 1): one egg with one variable, port 11 used, port 12 free
_VAR = {"env_variable": "GAME", "name": "Spiel", "default_value": "demo", "rules": "required|string|max:20",
        "user_editable": True}
ADMIN_API = {
    "/api/application/servers/1": {"attributes": {
        "id": 1, "identifier": "srv-a", "name": "Server srv-a", "description": "", "external_id": None, "user": 1,
        "node": 1, "egg": 5, "nest": 1, "allocation": 11, "suspended": False,
        "limits": {"memory": 1024, "swap": 0, "disk": 2048, "io": 500, "cpu": 100, "threads": None, "oom_disabled": True},
        "feature_limits": {"databases": 0, "allocations": 1, "backups": 1},
        "container": {"startup_command": "./start.sh", "image": "img:1", "environment": {"GAME": "demo"}},
        "relationships": {
            "allocations": {"data": [{"attributes": {"id": 11, "ip": "10.0.0.1", "port": 25561, "assigned": True}}]},
            "variables": {"data": [{"attributes": {**_VAR, "server_value": "demo"}}]},
            "user": {"attributes": {"id": 1, "username": "admin"}}}}},
    "/api/application/users": {"data": [{"attributes": {"id": 1, "username": "admin", "email": "a@b.test"}}]},
    "/api/application/nodes": {"data": [{"attributes": {"id": 1, "name": "Node 1", "fqdn": "wings.test"}}]},
    "/api/application/nodes/1/allocations": {"data": [
        {"attributes": {"id": 11, "ip": "10.0.0.1", "port": 25561, "assigned": True}},
        {"attributes": {"id": 12, "ip": "10.0.0.1", "port": 25570, "assigned": False}}]},
    "/api/application/nests": {"data": [{"attributes": {"id": 1, "name": "Spiele"}}]},
    "/api/application/nests/1/eggs": {"data": [{"attributes": {"id": 5, "name": "Demo"}}]},
    "/api/application/nests/1/eggs/5": {"attributes": {
        "id": 5, "name": "Demo", "startup": "./start.sh", "docker_images": {"Standard": "img:1"},
        "relationships": {"variables": {"data": [{"attributes": _VAR}]}}}},
}


class FakePtero(PteroClient):
    def __init__(self, settings):
        self.settings = settings
        self._wings_hosts = {"wings.test"}
        self.calls: list[tuple] = []
        self.missing_dirs: set[str] = {"/archiv/alt", "/x", "/plugins/deaktiviert", "/neu"}

    async def close(self) -> None:
        pass

    @property
    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] not in READ_METHODS]

    async def _request(self, method, path, *, key, params=None, json_body=None, content=None, raw=False):
        self._guard(method)
        if key == "app" and method.upper() not in READ_METHODS:
            raise ReadOnlyViolation("Die Application-API wird nur lesend verwendet")
        if key == "admin" and not self.settings.admin_key:
            raise AdminKeyMissing("Kein Admin-Key")
        self.calls.append((method.upper(), path, params, json_body))
        if key == "admin" and method.upper() == "GET" and path in ADMIN_API:
            return ADMIN_API[path]
        if key == "admin" and method.upper() == "POST" and path == "/api/application/servers":
            return {"attributes": {"identifier": "srv-new"}}
        if path.endswith("/files/list"):
            if (params or {}).get("directory") in self.missing_dirs:      # like Wings for a folder that is not there
                raise PteroError("Pterodactyl antwortet mit HTTP 404", status=404)
            return {"data": [{"attributes": {"name": n, "is_file": True, "size": 6, "modified_at": None}}
                             for n in ("a.txt", "a.txt.bak")]}
        if path.endswith("/files/contents"):
            return b"hallo\n"
        if path.endswith("/files/download") or path.endswith("/files/upload"):
            return {"attributes": {"url": "https://wings.test/x?token=t"}}
        if path.endswith("/backups"):            # backups the panel made itself
            return {"data": [{"attributes": {"uuid": "pb-1", "name": "Panel-Sicherung", "bytes": 1234,
                                             "is_successful": True, "is_locked": False,
                                             "created_at": "2026-10-20T10:00:00+00:00",
                                             "completed_at": "2026-10-20T10:01:00+00:00"}}],
                    "meta": {"pagination": {"total_pages": 1}}}
        if "/backups/" in path and path.endswith("/download"):
            return {"attributes": {"url": "https://wings.test/backup?token=t"}}
        if path.endswith("/websocket"):
            return {"data": {"token": "t", "socket": "wss://wings.test/ws"}}
        if path.endswith("/resources"):
            return {"attributes": {"current_state": "running", "resources": {}}}
        if path.startswith("/api/application/"):
            return {"data": [], "meta": {"pagination": {"total_pages": 1}}}
        return None

    async def upload(self, identifier, directory, files):
        self._guard("POST")
        self.calls.append(("POST", f"upload:{identifier}", directory, [n for n, _ in files]))

    async def stream_download(self, url):
        self._guard("GET")
        self.calls.append(("GET", "wings-download", url, None))
        yield b"daten"

    async def wings_stream(self, url, range_header=None):
        """Like real Wings for backups: ignores Range and always sends the whole file."""
        self._guard("GET")
        self.calls.append(("GET", "wings-download", url, range_header))
        data = b"\x1f\x8b-daten-0123456789"

        async def body():
            yield data[:7]
            yield data[7:]
        return 200, {"content-length": str(len(data))}, body()


async def fake_console(client, identifier):
    yield {"type": "line", "text": "Konsole 1", "source": "console output"}


class FakeUpdates:
    available = False
    latest = None
    error = None
    url = None
    checked_at = None
    notes = ""
    published_at = None
    repo = "skaot1910/hatchtower"
    prereleases = False
    latest_is_prerelease = False

    def set_prereleases(self, on, user_id):
        self.prereleases = on

    def cooldown_left(self):
        return 0

    async def check_now(self):
        return True


# -- Testwelt ----------------------------------------------------------------------------------

SERVERS = ("srv-a", "srv-b", "srv-gone")
_TEMPLATE = _TMP / "template.db"


def _build_template() -> None:
    db.init(_TEMPLATE)
    with db.connect() as con:
        for i, ident in enumerate(SERVERS, start=1):
            con.execute("INSERT INTO servers (identifier, ptero_id, uuid, name, egg, primary_ip, primary_port,"
                        " present) VALUES (?, ?, ?, ?, 'Minecraft', '10.0.0.1', ?, ?)",
                        (ident, i, f"uuid-{ident}", f"Server {ident}", 25560 + i, 0 if ident == "srv-gone" else 1))
            con.execute("INSERT INTO server_state (identifier, state) VALUES (?, 'running')", (ident,))
        con.execute("INSERT INTO server_meta (identifier, password_enc, on_status) VALUES ('srv-a', ?, 1)",
                    (main.vault.encrypt("geheimes-beitrittspasswort"),))
    con = sqlite3.connect(_TEMPLATE)   # WAL auflösen, damit die Vorlage eine einzelne Datei ist
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()


_build_template()


@dataclass
class World:
    """Eine frische Instanz pro Test."""
    path: Path
    ptero: FakePtero
    ids: dict[str, int] = field(default_factory=dict)
    args: dict[str, str] = field(default_factory=dict)

    # Benutzer und Sitzungen

    def user(self, name: str, role: str = "user", grants: dict[str, set[str]] | None = None,
             active: bool = True) -> int:
        with db.connect() as con:
            uid = con.execute("INSERT INTO users (username, password_hash, role, is_active, created_at)"
                              " VALUES (?, ?, ?, ?, ?)", (name, _HASH, role, int(active), db.utcnow())).lastrowid
        if grants:
            self.grant(uid, grants)
        self.ids[name] = uid
        return uid

    def grant(self, uid: int, grants: dict[str, set[str]]) -> None:
        permissions.save_assignments(uid, {ident: {"mode": "custom", "group_id": None, "keys": set(keys)}
                                           for ident, keys in grants.items()})

    def client(self, uid: int | None = None, csrf: bool = True) -> TestClient:
        c = TestClient(main.app, follow_redirects=False, base_url="http://hatchtower.test")
        c.csrf = ""
        if uid is not None:
            token, c.csrf = security.create_session(uid, "127.0.0.1", "pytest")
            c.cookies.set(security.SESSION_COOKIE, token)
        if not csrf:
            c.csrf = ""
        return c

    def anonymous_with_prelogin(self) -> tuple[TestClient, str]:
        c = self.client()
        token = security.prelogin_token(main.settings.secret_key)
        c.cookies.set(security.PRELOGIN_COOKIE, token)
        return c, token

    # Beobachtung

    def snapshot(self) -> dict[str, list]:
        """Alle Daten außer Audit-Log und Sitzungen: Eine verweigerte Anfrage darf hier nichts ändern."""
        result = {}
        with db.connect() as con:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            for table in sorted(tables):
                if table in ("audit_log", "sessions") or table.startswith("sqlite_"):
                    continue
                result[table] = sorted(tuple(map(str, r)) for r in con.execute(f"SELECT * FROM {table}"))
        return result

    def audit(self, action: str | None = None) -> list[dict]:
        with db.connect() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY id")]
        return [r for r in rows if action is None or r["action"] == action]

    def read_only(self, on: bool = True) -> None:
        main.settings.manager_mode = "read-only" if on else "read-write"


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


@pytest.fixture
def world(monkeypatch, tmp_path) -> World:
    path = tmp_path / "hatchtower.db"
    shutil.copy(_TEMPLATE, path)
    db.init(path)
    from hatchtower import routes_backup
    routes_backup._panel_cache.clear()          # the panel's backup list is cached per server for a minute

    s = main.settings
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "panel_type", "pterodactyl")
    monkeypatch.setattr(s, "pterodactyl_url", "https://panel.test")
    monkeypatch.setattr(s, "app_key", "ptla_test")
    monkeypatch.setattr(s, "client_key", "ptlc_test")
    monkeypatch.setattr(s, "admin_key", "ptla_admin")
    monkeypatch.setattr(s, "manager_mode", "read-write")
    monkeypatch.setattr(s, "node_overrides", {})
    (tmp_path / "logos").mkdir()

    ptero = FakePtero(s)
    noop = lambda *a, **k: None  # noqa: E731
    main.app.state.ptero = ptero
    main.app.state.monitor = SimpleNamespace(
        healthy=True, last_error=None, last_ok=None, last_sync=None, client=ptero,
        query=SimpleNamespace(forget=noop), follow=noop, request_sync=noop,
        request_light_sync=noop, down_since=None, poll_interval=10, poll_delay=lambda: 10)
    main.app.state.automation = SimpleNamespace(client=ptero, note_power=noop, set_timezone=noop)
    main.app.state.logarchive = SimpleNamespace(client=ptero)
    main.app.state.updates = FakeUpdates()
    main.app.state.statusfeed = SimpleNamespace(last_ok={}, last_error={}, request_update=noop, remove=noop)
    main.app.state.backups = backups.BackupManager(main.app.state, main.vault, s)   # ohne Hintergrundaufgaben
    monkeypatch.setattr(main, "console_stream", fake_console)
    monkeypatch.setattr(main, "load_connection", noop)
    monkeypatch.setattr(main, "make_client", lambda settings: ptero)

    async def no_nodes(*a, **k):
        return []
    monkeypatch.setattr(setup, "node_checks", no_nodes)
    main._power_last.clear()
    main.throttle.__init__()
    main._untrusted_proxy[:] = [None, 0.0]
    main._untrusted_proxy[:] = [None, 0.0]

    w = World(path=path, ptero=ptero)
    w.user("olga", "owner")
    victim = w.user("victim", "user", {"srv-a": {"server.view"}})
    with db.connect() as con:
        gid = con.execute("INSERT INTO perm_groups (name, created_at, updated_at) VALUES ('Mods', ?, ?)",
                          (db.utcnow(), db.utcnow())).lastrowid
        con.execute("INSERT INTO perm_group_items (group_id, permission) VALUES (?, 'server.view')", (gid,))
        sid = con.execute("INSERT INTO schedules (identifier, name, action, weekdays, time, enabled, created_by,"
                          " created_at) VALUES ('srv-a', 'Neustart', 'restart', '1', '04:00', 1, 1, ?)",
                          (db.utcnow(),)).lastrowid
        aid = con.execute("INSERT INTO announcements (text, level, created_by, created_at) VALUES"
                          " ('Wartung', 'info', 1, ?)", (db.utcnow(),)).lastrowid
        # Backup: Ziel im Datenordner, Job „ganzer Server srv-a“, ein gespeichertes Backup
        tid = con.execute("INSERT INTO backup_targets (name, kind, path, created_at) VALUES ('Platte', 'local', ?, ?)",
                          (str(tmp_path / "bk"), db.utcnow())).lastrowid
        jid = con.execute("INSERT INTO backup_jobs (name, target_id, created_by, created_at) VALUES ('Welt', ?, 1, ?)",
                          (tid, db.utcnow())).lastrowid
        con.execute("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, 'srv-a', '/')", (jid,))
        # Direct-download job (no target): reads the live server, needs backups.direct
        djid = con.execute("INSERT INTO backup_jobs (name, target_id, created_by, created_at) VALUES ('Live', NULL, 1, ?)",
                           (db.utcnow(),)).lastrowid
        con.execute("INSERT INTO backup_job_items (job_id, identifier, path) VALUES (?, 'srv-a', '/world')", (djid,))
        rid = con.execute("INSERT INTO backup_runs (batch, job_id, job_name, identifier, server_name, target_id, kind,"
                          " file, size, status, queued_at, finished_at) VALUES ('b1', ?, 'Welt', 'srv-a', 'Server srv-a',"
                          " ?, 'target', 'Welt/Server_srv-a/alt.zip', 22, 'ok', ?, ?)",
                          (jid, tid, db.utcnow(), db.utcnow())).lastrowid
    with db.connect() as con:
        fid = con.execute("INSERT INTO status_feeds (name, url_enc, fields, created_at) VALUES ('Clan', ?, 'name,status', ?)",
                          (main.vault.encrypt(FEED_URL), db.utcnow())).lastrowid
        con.execute("INSERT INTO status_feed_servers (feed_id, identifier) VALUES (?, 'srv-a')", (fid,))
        eid = con.execute("INSERT INTO inbox (created_at, event, audience, identifier, detail) VALUES"
                          " (?, 'crash', 'server', 'srv-a', 'weg')", (db.utcnow(),)).lastrowid
        hid = con.execute("INSERT INTO notify_webhooks (title, url_enc, events, created_at) VALUES ('Alarm', ?, 'crash', ?)",
                          (main.vault.encrypt(FEED_URL), db.utcnow())).lastrowid
    (tmp_path / "bk" / "Welt" / "Server_srv-a").mkdir(parents=True)
    (tmp_path / "bk" / "Welt" / "Server_srv-a" / "alt.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    for key, value in {"brand_logo_login": "f" * 32 + ".webp", "mail_password_enc": main.vault.encrypt("smtp"),
                       "status_enabled": "1", "legal_impressum_enabled": "1",
                       "legal_impressum_text": "Hallo"}.items():
        db.set_setting(key, value, None)
    (tmp_path / "logos" / ("f" * 32 + ".webp")).write_bytes(b"RIFF")
    invite = accounts.create("invite", data={"role": "user"}, created_by=1)
    w.args = {
        "ident": "srv-a", "user_id": str(victim), "group_id": str(gid), "sched_id": str(sid), "ann_id": str(aid),
        "job_id": str(jid), "direct_job_id": str(djid), "uuid": "pb-1", "feed_id": str(fid), "hook_id": str(hid), "entry_id": str(eid), "run_id": str(rid), "target_id": str(tid),
        "token_hash": accounts._hash(invite), "kind": "login", "page": "impressum", "key": "hell", "mode": "simple", "code": "en",
        "filename": "f" * 32 + ".webp",
        "@invite": invite,
        "@reset": accounts.create("reset", user_id=victim),
        "@verify": accounts.create("verify", user_id=victim, email="v@example.com"),
        "@unsubscribe": security.unsubscribe_token(main.settings.secret_key, victim, "v@example.com"),
    }
    return w
