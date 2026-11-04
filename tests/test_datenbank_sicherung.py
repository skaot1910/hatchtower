"""Daily database backup (systemd unit) and the backup before updates (install.sh).

Both run while Hatchtower is working: the service opens and closes connections all the time, and closing the last one
checkpoints the WAL under a short exclusive lock. Without a busy timeout the sqlite3 tool fails at once with
"database is locked" (seen on the test instance: an empty daily backup and an aborted update)."""
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 command line tool missing")


def unit_command(data: Path, backups: Path) -> str:
    text = (ROOT / "installer" / "hatchtower-backup.service").read_text()
    cmd = re.search(r"^ExecStart=/bin/sh -c '(.*)'$", text, re.M).group(1)
    cmd = cmd.replace("$$", "$").replace("%%", "%")          # systemd escapes
    return cmd.replace("/var/lib/hatchtower", str(data)).replace("/var/backups/hatchtower", str(backups))


def make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()


def hold_lock(path: Path, seconds: float) -> threading.Thread:
    """Another process keeps the database locked for a moment, like Hatchtower during a checkpoint."""
    ready = threading.Event()

    def run():
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA locking_mode=EXCLUSIVE")
        con.execute("BEGIN EXCLUSIVE")
        con.execute("INSERT INTO t VALUES (2)")
        con.execute("COMMIT")                                  # the exclusive lock stays until close
        ready.set()
        time.sleep(seconds)
        con.close()
    thread = threading.Thread(target=run)
    thread.start()
    ready.wait()
    return thread


def test_taegliches_backup_wartet_auf_die_sperre(tmp_path):
    data, backups = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    backups.mkdir()
    make_db(data / "hatchtower.db")
    lock = hold_lock(data / "hatchtower.db", 1.5)
    result = subprocess.run(["sh", "-c", unit_command(data, backups)], capture_output=True, text=True, timeout=60)
    lock.join()
    assert result.returncode == 0, result.stderr
    files = list(backups.iterdir())
    assert len(files) == 1 and re.fullmatch(r"hatchtower-\d{4}-\d{2}-\d{2}\.db", files[0].name)
    assert sqlite3.connect(files[0]).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_fehlgeschlagenes_backup_hinterlaesst_keine_leere_datei(tmp_path):
    data, backups = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    backups.mkdir()
    (data / "hatchtower.db").write_text("keine Datenbank")
    result = subprocess.run(["sh", "-c", unit_command(data, backups)], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert list(backups.iterdir()) == []


def test_backup_vor_dem_update_wartet_und_bricht_sauber_ab():
    script = (ROOT / "installer" / "install.sh").read_text()
    block = script[script.index('BK="$BACKUP_DIR/vor-update-'):script.index('chown -h hatchtower:hatchtower "$BK"')]
    assert re.search(r'sqlite3 -cmd "\.timeout \d{4,}" "\$DATA_DIR/hatchtower\.db" "\.backup', block)
    assert 'rm -f -- "$BK"' in block and "fail " in block
