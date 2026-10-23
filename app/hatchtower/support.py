"""Support report: one plain text file with the details that help to find the cause of a problem, to send to whoever
helps.

What it contains: version and system, panel connection and the full connection check, Wings nodes, servers,
users and permissions (numbers instead of names), automation, backups, notifications, settings, recent problems
from the audit log, update state and the last log lines.

What it never contains: API keys, passwords, tokens, webhook and invitation links, join passwords of servers,
e-mail addresses, IP addresses from the audit log. Secret settings are left out by name, and at the very end every
known secret value (decrypted from the database) is searched in the finished text and replaced by ***, so it
cannot slip in through a log line or an error message either.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit

from . import APP_NAME, __version__, db, diagnose, logbuffer, updates, wings
from .i18n import t

SECRET_NAME = re.compile(r"key|pass|secret|token|_enc|username|cookie|check$", re.IGNORECASE)
PERSONAL = {"mail_from_name"}           # not needed to find a problem, may be a real name
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")
MIN_SECRET = 6            # shorter values are not searched (they would match ordinary words and numbers)
PACKAGES = ("fastapi", "starlette", "uvicorn", "httpx", "websockets", "jinja2", "cryptography")


class Text:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def section(self, title: str) -> None:
        self.lines += ["", "=" * 78, title, "=" * 78]

    def kv(self, key: str, value) -> None:
        self.lines.append(f"{key + ':':<28} {value}")

    def add(self, line: str = "") -> None:
        self.lines.append(line)


def _size(num: int) -> str:
    return f"{num / 1024 ** 2:.1f} MiB" if num >= 1024 ** 2 else f"{num / 1024:.0f} KiB"


def _when(value: str | None) -> str:
    return value or "–"


def _read(path: str, limit: int = 4000) -> str:
    try:
        return Path(path).read_text(errors="replace")[:limit]
    except OSError:
        return ""


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# -- sections ------------------------------------------------------------------------------------------

def overview(out: Text, settings, web: bool) -> None:
    out.section("1. Overview")
    out.kv("Created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    out.kv("Hatchtower", __version__)
    out.kv("Created from", "web interface" if web else "command line (hatchtower support)")
    out.kv("Panel", f"{settings.panel.name} ({settings.panel_type})")
    out.kv("Mode", "read only" if settings.read_only else "read and control")
    out.kv("Time zone", settings.timezone)
    out.kv("Default language", db.get_setting("default_language") or "–")
    out.kv("Listens on", f"{os.environ.get('HATCHTOWER_BIND', '?')}:{os.environ.get('HATCHTOWER_PORT', '?')}")
    out.kv("Python", sys.version.split()[0])
    os_name = next((line.split("=", 1)[1].strip('"') for line in _read("/etc/os-release").splitlines()
                    if line.startswith("PRETTY_NAME=")), platform.system())
    out.kv("System", f"{os_name}, kernel {platform.release()}, {platform.machine()}")
    out.kv("Virtualisation", _run(["systemd-detect-virt"]) or "unknown")
    if web:
        up = time.time() - logbuffer.STARTED
        out.kv("Process running for", f"{int(up // 3600)} h {int(up % 3600 // 60)} min")
    with db.connect() as con:
        out.kv("Database schema", con.execute("PRAGMA user_version").fetchone()[0])
    try:
        out.kv("Database size", f"{settings.db_path.stat().st_size / 1024 ** 2:.1f} MiB")
        usage = shutil.disk_usage(settings.data_dir)
        out.kv("Data disk", f"{usage.free / 1024 ** 3:.1f} GiB free of {usage.total / 1024 ** 3:.1f} GiB")
    except OSError as exc:
        out.kv("Data disk", f"not readable ({exc.__class__.__name__})")
    mem = {line.split(":")[0]: line.split()[1] for line in _read("/proc/meminfo").splitlines() if ":" in line}
    if "MemTotal" in mem:
        out.kv("RAM", f"{int(mem.get('MemAvailable', 0)) // 1024} MiB available of {int(mem['MemTotal']) // 1024} MiB")
    out.kv("Packages", ", ".join(f"{p} {_version(p)}" for p in PACKAGES))


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "?"


def connection(out: Text, settings, state) -> None:
    out.section("2. Connection to the panel")
    out.kv("Panel address", settings.pterodactyl_url or "– (not set up)")
    out.kv("Keys present", ", ".join(k for k, v in (("application", settings.app_key), ("client", settings.client_key),
                                                    ("admin", settings.admin_key)) if v) or "none")
    out.kv("Other Wings addresses", json.dumps(settings.node_overrides) if settings.node_overrides else "–")
    out.kv("Trusted proxies", ", ".join(map(str, settings.trusted_proxies)) or "–")
    origins = {f"{netloc} (panel {panel})": origin for (panel, netloc), origin in wings._origins.items()}
    out.kv("Console origin in use", json.dumps(origins) if origins else "– (no console opened since start)")
    if state is not None:
        monitor = getattr(state, "monitor", None)
        if monitor is not None:
            out.kv("Status poll every", f"{monitor.poll_interval} s")
            out.kv("Last successful poll", _when(getattr(monitor, "last_ok", None)))
            error = getattr(monitor, "last_error", None)
            out.kv("Last poll error", t(error) if error else "–")
            out.kv("Panel unreachable since", _when(getattr(monitor, "down_since", None)))
        rate = getattr(getattr(state, "ptero", None), "rate", None)
        if rate:
            out.kv("Panel rate limit", f"{rate[1]} of {rate[0]} requests left ({int(time.monotonic() - rate[2])} s ago)")


async def connection_check(out: Text, settings) -> list[dict]:
    out.section("3. Connection check (same as System → Panel → Check connection)")
    if not settings.configured:
        out.add("Not set up yet: panel address or API keys missing.")
        return []
    report = await diagnose.run(settings, diagnose.candidate_from_settings(settings))
    marks = {diagnose.ERROR: "ERROR", diagnose.WARNING: "WARN ", diagnose.OPTIONAL: "OPT  ", diagnose.OK: "ok   "}
    for key, label in diagnose.AREAS:
        if key in report.skipped:
            out.add(f"[{t(label)}] not checked (an error above comes first)")
            continue
        for c in report.area(key):
            out.add(f"[{t(label)}] {marks[c.level]} {t(c.title)}")
            if c.level in (diagnose.ERROR, diagnose.WARNING):
                if c.detail:
                    out.add(f"        {t(c.detail)}")
                for step in c.fix:
                    out.add(f"        -> {t(step)}")
    out.add(f"Result: {report.errors} error(s), {report.warnings} warning(s)")
    return report.nodes or []


def nodes_section(out: Text, nodes: list[dict]) -> None:
    out.section("4. Wings nodes")
    if not nodes:
        out.add("– (none found, or the connection check could not list them)")
    for n in nodes:
        out.add(f"- {n.get('name') or n.get('fqdn')}: {n.get('scheme', '?')}://{n.get('host')}:{n.get('daemon_port')}"
                f" · panel address {n.get('fqdn')}"
                f"{' · other address ' + n['override'] if n.get('override') else ''}"
                f" · reachable: {'yes' if n.get('connected') else 'no'} ({n.get('http') or '–'})"
                f"{' · maintenance' if n.get('maintenance') else ''}")


def servers_section(out: Text) -> None:
    out.section("5. Servers")
    with db.connect() as con:
        rows = con.execute("SELECT s.identifier, s.name, s.egg, s.node_id, s.limit_memory, s.limit_disk, s.limit_cpu,"
                           " s.suspended, s.present, s.synced_at, st.state, st.updated_at, st.presence,"
                           " st.presence_source FROM servers s LEFT JOIN server_state st ON st.identifier = s.identifier"
                           " ORDER BY s.name COLLATE NOCASE").fetchall()
        crash = {r["identifier"] for r in con.execute("SELECT identifier FROM automation_settings WHERE crash_restart = 1")}
    present = [r for r in rows if r["present"]]
    removed = [r for r in rows if not r["present"]]
    out.add(f"{len(present)} server(s) in the panel:")
    for r in present:
        out.add(f"- {r['identifier']} {r['name']} [{r['egg'] or '?'}] node {r['node_id']}"
                f" · {r['state'] or '?'} (as of {_when(r['updated_at'])})"
                f" · RAM {r['limit_memory'] or 0} MiB, disk {r['limit_disk'] or 0} MiB, CPU {r['limit_cpu'] or 0} %"
                f" · players: {r['presence'] or '–'} via {r['presence_source'] or '–'}"
                f"{' · restart after crash' if r['identifier'] in crash else ''}{' · SUSPENDED' if r['suspended'] else ''}")
    if removed:
        out.add("")
        out.add(f"{len(removed)} server(s) deleted in the panel. Hatchtower keeps them so that their history stays"
                " (backups, server log, console archive); they are not polled any more:")
        for r in removed:
            out.add(f"- {r['identifier']} {r['name']} [{r['egg'] or '?'}] · last seen in the panel {_when(r['synced_at'])}")


def users_section(out: Text) -> None:
    out.section("6. Users and permissions (numbers instead of names)")
    from .permissions import load_grants
    with db.connect() as con:
        users = con.execute("SELECT id, role, is_active, last_login_at, locked_until, language, view_mode,"
                            " email IS NOT NULL AND email != '' AS has_mail FROM users ORDER BY id").fetchall()
        groups = con.execute("SELECT g.id, COUNT(i.permission) AS n FROM perm_groups g"
                             " LEFT JOIN perm_group_items i ON i.group_id = g.id GROUP BY g.id").fetchall()
        default = {r["user_id"]: r["group_id"] for r in con.execute("SELECT user_id, group_id FROM user_default_access")}
    out.add(f"{len(users)} accounts, {sum(u['role'] == 'owner' for u in users)} owner(s); "
            f"{len(groups)} permission group(s): " + ", ".join(f"#{g['id']} ({g['n']} rights)" for g in groups))
    for u in users:
        line = (f"- user #{u['id']} {u['role']}{'' if u['is_active'] else ' (disabled)'}"
                f"{' (locked)' if u['locked_until'] else ''}{'' if u['last_login_at'] else ' · never logged in'}"
                f" · e-mail {'yes' if u['has_mail'] else 'no'} · view {u['view_mode'] or 'default'}")
        if u["role"] != "owner":
            grants = load_grants(u["id"])
            keys = sorted(set().union(*grants.values())) if grants else []
            line += (f" · {len(grants)} server(s){' + all servers via group #' + str(default[u['id']]) if u['id'] in default else ''}"
                     f" · rights: {', '.join(keys) or 'none'}")
        out.add(line)


def automation_section(out: Text) -> None:
    out.section("7. Automation")
    with db.connect() as con:
        schedules = con.execute("SELECT identifier, action, weekdays, time, run_at, enabled, only_if_running,"
                                " last_run, last_result FROM schedules ORDER BY identifier, time").fetchall()
        present = {r[0] for r in con.execute("SELECT identifier FROM servers WHERE present = 1")}
        crashes = con.execute("SELECT identifier, detected_at, outcome FROM crash_events ORDER BY detected_at DESC LIMIT 10").fetchall()
    out.add(f"{len(schedules)} schedule(s)")
    for s in schedules:
        out.add(f"- {s['identifier']} {s['action']} {s['run_at'] or (s['weekdays'] + ' ' + s['time'])}"
                f"{'' if s['enabled'] else ' (paused)'}{' only if running' if s['only_if_running'] else ''}"
                f"{'' if s['identifier'] in present else ' · SERVER DELETED IN THE PANEL'}"
                f" · last {_when(s['last_run'])}: {s['last_result'] or '–'}")
    out.add("Last crashes:" if crashes else "No crashes recorded.")
    for c in crashes:
        out.add(f"- {c['detected_at']} {c['identifier']}: {t(c['outcome'])}")


def backups_section(out: Text, state) -> None:
    out.section("8. Backups")
    with db.connect() as con:
        targets = con.execute("SELECT id, kind, path, smb_host, smb_share, check_ok, check_free, check_error, checked_at"
                              " FROM backup_targets").fetchall()
        jobs = con.execute("SELECT id, target_id, all_servers, stop_mode, warn_command IS NOT NULL AS warn,"
                           " save_command, save_for, weekdays, time, enabled, compress, keep_count, keep_days,"
                           " last_result FROM backup_jobs ORDER BY id").fetchall()
        items = {r["job_id"]: r["n"] for r in con.execute("SELECT job_id, COUNT(*) AS n FROM backup_job_items GROUP BY job_id")}
        runs = con.execute("SELECT id, job_id, identifier, kind, trigger, status, size, files, queued_at, finished_at,"
                           " data_bytes, verify_ok, detail FROM backup_runs ORDER BY id DESC LIMIT 20").fetchall()
    for x in targets:
        where = x["path"] if x["kind"] == "local" else f"//{x['smb_host']}/{x['smb_share']}/{x['path'] or ''}"
        free = f"{x['check_free'] / 1024 ** 3:.1f} GiB free" if x["check_free"] else "free space unknown"
        out.add(f"- target #{x['id']} {x['kind']} {where} · last check {_when(x['checked_at'])}: "
                f"{'ok' if x['check_ok'] else x['check_error'] or 'not checked'} · {free}")
    for j in jobs:
        out.add(f"- job #{j['id']} {'target #' + str(j['target_id']) if j['target_id'] else 'direct download'}"
                f"{' · save game of ' + j['save_for'] if j['save_for'] else ''}"
                f" · {'all servers' if j['all_servers'] else str(items.get(j['id'], 0)) + ' path(s)'}"
                f" · stop {j['stop_mode']}{' with warning' if j['warn'] else ''}"
                f"{' · save command' if j['save_command'] else ''}"
                f" · schedule {(j['weekdays'] + ' ' + j['time']) if j['weekdays'] else 'by hand'}"
                f"{' (paused)' if j['weekdays'] and not j['enabled'] else ''}"
                f" · keep {j['keep_count'] or '∞'} / {j['keep_days'] or '∞'} d · last: {j['last_result'] or '–'}")
    out.add("Last runs:" if runs else "No runs yet.")
    for r in runs:
        detail = (r["detail"] or "").replace("\n", " ")[:300]
        size = r["size"] or r["data_bytes"] or 0
        out.add(f"- run #{r['id']} job #{r['job_id']} {r['identifier']} {r['kind']}/{r['trigger'] or 'manual'}"
                f" {r['status']} · {r['files'] or 0} files, {_size(size)}"
                f" · verified {'–' if r['verify_ok'] is None else ('ok' if r['verify_ok'] else 'FAILED')}"
                f" · {_when(r['finished_at'] or r['queued_at'])}{' · ' + detail if detail else ''}")
    manager = getattr(state, "backups", None) if state is not None else None
    if manager is not None:
        active = manager.active_views(lambda i: True)
        out.add(f"Running now: {len(active)}" + "".join(f"\n- {a['kind']} {a['ident']}: {a['phase']}" for a in active))


def notifications_section(out: Text, vault) -> None:
    out.section("9. Notifications and status page")
    with db.connect() as con:
        hooks = con.execute("SELECT * FROM notify_webhooks ORDER BY id").fetchall()
        feeds = con.execute("SELECT id, enabled, fields FROM status_feeds ORDER BY id").fetchall()
        recent = con.execute("SELECT key, sent_at FROM notify_log ORDER BY sent_at DESC LIMIT 15").fetchall()
        mail_users = con.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND email IS NOT NULL AND email != ''"
                                 " AND notify_email = 1").fetchone()[0]
    out.add(f"{len(hooks)} webhook(s):" if hooks else "No webhooks.")
    for h in hooks:
        host = urlsplit(vault.decrypt(h["url_enc"]) or "").hostname or "?"
        out.add(f"- webhook #{h['id']} \"{h['title']}\" {h['kind'] or '?'} to {host}{'' if h['enabled'] else ' · OFF'}")
        out.add(f"    events: {h['events'] or '–'}")
        out.add(f"    servers: {h['servers'] or 'all'} · last sent ok: {_when(h['last_ok_at'])}"
                f" · last error: {_when(h['last_error_at'])}{' – ' + h['last_error'] if h['last_error'] else ''}")
    out.kv("E-mail", "on" if db.get_setting("mail_enabled") == "1" else "off")
    out.kv("Mail server", f"{db.get_setting('mail_host') or '–'}:{db.get_setting('mail_port') or '–'}"
                          f" ({db.get_setting('mail_security') or '–'})")
    out.kv("Users getting e-mails", mail_users)
    out.kv("Last mail sent ok", _when(db.get_setting("mail_last_ok") or None))
    out.kv("Last mail error", db.get_setting("mail_last_error") or "–")
    out.kv("Status page", "on" if db.get_setting("status_enabled") == "1" else "off")
    out.kv("Status feeds", ", ".join(f"#{f['id']}{'' if f['enabled'] else ' (off)'}" for f in feeds) or "–")
    out.add("Last notifications raised (event:server, before the per-event cooldown):" if recent else
            "No notification raised yet.")
    for r in recent:
        key = r["key"][:-5] if r["key"].endswith(":None") else r["key"]
        out.add(f"- {datetime.fromtimestamp(r['sent_at'], timezone.utc).isoformat(timespec='seconds')} {key}")


def settings_section(out: Text) -> None:
    out.section("10. Settings (secret ones only as set/empty)")
    with db.connect() as con:
        rows = con.execute("SELECT key, value, updated_at FROM app_settings ORDER BY key").fetchall()
    for r in rows:
        value = r["value"] or ""
        if SECRET_NAME.search(r["key"]) or r["key"] in PERSONAL:
            value = "(set)" if value else "(empty)"
        elif len(value) > 200:
            value = value[:200] + " …"
        out.kv(r["key"], EMAIL.sub(lambda m: "***@" + m.group(0).split("@", 1)[1], value))


def audit_section(out: Text) -> None:
    out.section("11. Recent problems from the audit log (no IP addresses, users as numbers)")
    with db.connect() as con:
        rows = con.execute("SELECT ts, user_id, username, action, server, detail, result FROM audit_log"
                           " WHERE result != 'ok' ORDER BY id DESC LIMIT 1000").fetchall()
        accounts = {r["username"].casefold(): r["id"] for r in con.execute("SELECT id, username FROM users")}
        counts = con.execute("SELECT result, COUNT(*) AS n FROM audit_log WHERE ts >= ? GROUP BY result",
                             (datetime.fromtimestamp(time.time() - 86400, timezone.utc).isoformat(timespec="seconds"),)
                             ).fetchall()
    out.add("Last 24 h: " + (", ".join(f"{c['n']} {c['result']}" for c in counts) or "nothing"))
    # the same problem many times (e.g. every night) shows once, with how often and since when
    groups: dict[tuple, list] = {}
    for r in rows:
        detail = (r["detail"] or "").replace("\n", " ")[:200]
        key = (r["result"], r["action"], _who(r, accounts), r["server"] or "", detail)
        if key in groups:
            groups[key][1] += 1
            groups[key][2] = r["ts"]
        else:
            groups[key] = [r["ts"], 1, r["ts"]]
    for (result, action, who, server, detail), (last, n, first) in list(groups.items())[:40]:
        times = f"{last}" + (f" (×{n} since {first})" if n > 1 else "")
        out.add(f"- {times} {result} {action} · {who}{' · ' + server if server else ''}{' · ' + detail if detail else ''}")


def _who(row, accounts: dict[str, int]) -> str:
    """Users as numbers. Entries without a user id are internal actors (System, Automation, Backup) or a name typed
    at the login: an existing account becomes its number, anything else typed there is left out."""
    if row["user_id"]:
        return f"user #{row['user_id']}"
    name = row["username"] or ""
    if name.casefold() in accounts:
        return f"user #{accounts[name.casefold()]}"
    return "(unknown name)" if row["action"].startswith(("login", "auth", "password")) else (name or "–")


BUFFER_STAMP = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\dZ ")
JOURNAL_STAMP = re.compile(r"\S+ \S+ [^:\s]+\[\d+\]: |\S+ \S+ [^:\s]+: ")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def updates_section(out: Text, settings, state) -> None:
    out.section("12. Updates")
    out.kv("Installed", __version__)
    out.kv("Pre-releases", "on" if db.get_setting("update_prereleases") == "1" else "off")
    checker = getattr(state, "updates", None) if state is not None else None
    if checker is not None:
        out.kv("Newest offered", f"{checker.latest or '–'}{' (update available)' if checker.available else ''}")
        out.kv("Last check", f"{_when(checker.checked_at)}{' – ' + t(checker.error) if checker.error else ''}")
    info = updates.read_state(settings.data_dir)
    for key in ("state", "from", "to", "started", "finished"):
        if info.get(key):
            out.kv(f"Last web update {key}", info[key])
    log = _read(str(updates._files(settings.data_dir)[2]), 200_000).splitlines()[-40:]
    if log:
        out.add("Last lines of the log of the last update started in the web interface:")
        out.lines += ["  " + ANSI.sub("", line) for line in log]


def log_section(out: Text, web: bool) -> None:
    out.section("13. Log")
    if not web:
        out.add("See the system journal at the end of this file.")
        return
    lines = collapse(logbuffer.lines(logbuffer.MAX_LINES), BUFFER_STAMP)[-400:]
    out.add("Log of this process since its last start (repeated messages once, with count):")
    out.lines += lines or ["– (nothing logged yet)"]


# -- secrets -----------------------------------------------------------------------------------------

def secrets(settings, vault) -> list[str]:
    """Every secret value Hatchtower knows, decrypted, longest first."""
    found = {settings.app_key, settings.client_key, settings.admin_key, settings.secret_key, settings.data_key}
    encrypted = []
    with db.connect() as con:
        encrypted += [r[0] for r in con.execute("SELECT value FROM app_settings WHERE value LIKE 'gAAAA%'")]
        for table, column in (("servers", "startup_password_enc"), ("server_meta", "password_enc"),
                              ("backup_targets", "smb_password_enc"), ("notify_webhooks", "url_enc"),
                              ("status_feeds", "url_enc"), ("tokens", "token_enc")):
            try:
                encrypted += [r[0] for r in con.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")]
            except Exception:
                continue
    for value in encrypted:
        plain = vault.decrypt(value)
        if plain:
            found.add(plain)
            # webhook and invitation links: the secret part on its own as well
            found.update(part for part in re.split(r"[/?&=#]", plain) if len(part) >= 16)
    return sorted((s for s in found if s and len(s) >= MIN_SECRET), key=len, reverse=True)


def redact(text: str, known: list[str]) -> str:
    for value in known:
        text = text.replace(value, "***")
    return text


# -- addresses -----------------------------------------------------------------------------------------
# Domains and IP addresses are replaced by placeholders unless the user chooses to send them. The same address
# always gets the same placeholder, and private stays distinguishable from public: whoever reads the report can
# still tell whether two addresses are the same, which was the key to problems like the console origin.

TLDS = ("com|net|org|de|at|ch|eu|io|gg|me|info|xyz|dev|cloud|host|online|site|live|fr|nl|be|pl|uk|co|tv|cc|us|ru|"
        "se|no|dk|fi|it|es|pt|cz|biz|pro|club|space|tech|network|server|games|lan|local|home|internal|arpa")
HOST = re.compile(rf"\b(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{TLDS})\b", re.IGNORECASE)
# not part of a longer dotted number (a version like 1.2.3.4.5), but a full stop after it is fine
IPV4 = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d|\.\d)")
IPV6 = re.compile(r"(?<![\w:])(?=[0-9a-f:]*::|(?:[0-9a-f]{1,4}:){7})[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![\w:])",
                  re.IGNORECASE)
# well-known services: naming them reveals nothing about the user's own setup
KEEP_HOSTS = {"discord.com", "discordapp.com", "github.com", "api.github.com", "gmail.com", "smtp.gmail.com",
              "googlemail.com", "outlook.com", "smtp.office365.com", "office365.com", "gmx.net", "mail.gmx.net",
              "web.de", "smtp.web.de", "example.com", "example.org", "example.net"}


def anonymize(text: str) -> str:
    import ipaddress
    names: dict[str, str] = {}

    def label(kind: str, value: str) -> str:
        key = value.lower()
        if key not in names:
            names[key] = f"<{kind}-{sum(1 for v in names.values() if v.startswith('<' + kind)) + 1}>"
        return names[key]

    def ip(m: re.Match) -> str:
        try:
            addr = ipaddress.ip_address(m.group(0))
        except ValueError:                 # looked like an address, is none (e.g. a time like 12:30:00)
            return m.group(0)
        if addr.is_loopback or addr.is_unspecified:
            return m.group(0)
        return label("private-ip" if addr.is_private else "public-ip", m.group(0))

    def host(m: re.Match) -> str:
        value = m.group(0)
        if value.lower() in KEEP_HOSTS or value.lower().endswith(".example.com"):
            return value
        return label("host", value)
    return HOST.sub(host, IPV6.sub(ip, IPV4.sub(ip, text)))


def collapse(lines: list[str], prefix: re.Pattern | None = None) -> list[str]:
    """Identical messages once, with how often and when first and last (a message every minute would otherwise
    push everything else out of the report). `prefix` matches the time stamp part that differs."""
    seen: dict[str, list] = {}
    order: list[str] = []
    for line in lines:
        m = prefix.match(line) if prefix else None
        stamp, message = (m.group(0), line[m.end():]) if m else ("", line)
        if message in seen:
            seen[message][1] += 1
            seen[message][2] = stamp
        else:
            seen[message] = [stamp, 1, stamp]
            order.append(message)
    out = []
    for message in order:
        first, n, last = seen[message]
        out.append(f"{first}{message}" + (f"   [×{n}, last {last.strip()}]" if n > 1 else ""))
    return out


# -- the whole report --------------------------------------------------------------------------------

async def build(settings, vault, state=None, *, addresses: bool = False, finish: bool = True) -> str:
    """The report as text. `state` is app.state in the web interface (adds live status and the in-memory log);
    from the command line it is None. Without `addresses`, domains and IP addresses become placeholders.
    finish=False leaves redaction of addresses to the caller (the command line adds the journal first)."""
    web = state is not None
    out = Text()
    out.add(f"{APP_NAME} support report – {__version__}")
    out.add("Contains no keys, passwords, tokens, links with access codes, e-mail addresses or user names.")
    out.add("Addresses: " + ("included as they are." if addresses else
                             "replaced by placeholders – the same address always gets the same placeholder."))
    nodes: list[dict] = []
    parts = [
        lambda: overview(out, settings, web),
        lambda: connection(out, settings, state),
        None,                                   # connection check (async)
        lambda: nodes_section(out, nodes),
        lambda: servers_section(out),
        lambda: users_section(out),
        lambda: automation_section(out),
        lambda: backups_section(out, state),
        lambda: notifications_section(out, vault),
        lambda: settings_section(out),
        lambda: audit_section(out),
        lambda: updates_section(out, settings, state),
        lambda: log_section(out, web),
    ]
    for part in parts:
        try:
            if part is None:
                nodes = await connection_check(out, settings)
            else:
                part()
        except Exception as exc:              # one broken part must not cost the rest of the report
            out.add(f"!! This part could not be collected: {exc.__class__.__name__}: {exc}")
    out.add("")
    text = redact("\n".join(out.lines) + "\n", secrets(settings, vault))
    return text if addresses or not finish else anonymize(text)


def filename(now: datetime | None = None) -> str:
    return f"hatchtower-support-{(now or datetime.now()).strftime('%Y-%m-%d_%H-%M')}.txt"
