"""User ids are never reused: rebuilding users with AUTOINCREMENT must keep every dependent row."""
from __future__ import annotations

import sqlite3

from hatchtower import db

V = 40      # schema version before the rebuild


def _old_db(path):
    con = sqlite3.connect(path)
    for i, sql in enumerate(db.MIGRATIONS[:V], start=1):
        con.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")
    return con


def _fill(con, table, user_id):
    """One row in a table that references users, dummy values for every required column."""
    cols, values = [], []
    info = con.execute(f"PRAGMA table_info({table})").fetchall()
    rowid_alias = sum(1 for c in info if c[5]) == 1
    for _, name, kind, notnull, default, pk in info:
        if name == "purpose":
            cols.append(name), values.append("invite")
        elif name == "user_id":
            cols.append(name), values.append(user_id)
        elif (notnull or pk) and default is None and not (pk and rowid_alias and kind == "INTEGER"):
            cols.append(name), values.append(1 if "INT" in kind else f"{table}-{name}")
    con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)


def test_neuaufbau_behaelt_alles_und_ids_kommen_nicht_wieder(tmp_path):
    path = tmp_path / "alt.db"
    con = _old_db(path)
    for uid in (1, 2, 3):
        con.execute("INSERT INTO users (id, username, password_hash, role, created_at, theme)"
                    " VALUES (?, ?, 'h', 'user', 'jetzt', 'dark')", (uid, f"u{uid}"))
    con.execute("INSERT INTO audit_log (ts, user_id, username, action, result) VALUES ('t', 5, 'weg', 'x', 'ok')")
    children = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"
                                          " AND sql LIKE '%REFERENCES users%'")]
    assert len(children) >= 7
    for table in children:
        _fill(con, table, 2)
    con.commit()
    before = {t: con.execute(f"SELECT * FROM {t}").fetchall() for t in children + ["users"]}
    con.close()

    db.init(path)
    with db.connect() as con:
        for table, rows in before.items():
            assert [tuple(r) for r in con.execute(f"SELECT * FROM {table}")] == rows, table
        assert [r for r in con.execute("PRAGMA foreign_key_check") if r[2] == "users"] == []
        assert "AUTOINCREMENT" in con.execute("SELECT sql FROM sqlite_master WHERE name = 'users'").fetchone()[0]
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND tbl_name = 'console_lines'"
                           " AND sql LIKE '%(ts)%'").fetchone()
        new = con.execute("INSERT INTO users (username, password_hash, role, created_at)"
                          " VALUES ('neu', 'h', 'user', 'jetzt')").lastrowid
        assert new == 6                                      # above the deleted user 5 from the audit log
        con.execute("DELETE FROM users WHERE id = ?", (new,))
        assert con.execute("INSERT INTO users (username, password_hash, role, created_at)"
                           " VALUES ('neu2', 'h', 'user', 'jetzt')").lastrowid == 7
        # deleting a user still cascades
        con.execute("DELETE FROM users WHERE id = 2")
        assert all(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0 for t in children)
