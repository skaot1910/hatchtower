"""Command line on the server: setup code, owner account, password reset and the read-only self test."""
from __future__ import annotations

import asyncio
import dataclasses

import pytest
from conftest import PASSWORD

from hatchtower import cli, db, diagnose, main, security, setup
from hatchtower.i18n import Msg

NEW = "Neues-Passwort-4567"


def answers(monkeypatch, typed: list[str], passwords: list[str]) -> None:
    typed, passwords = list(typed), list(passwords)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed.pop(0))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": passwords.pop(0))


def user(name: str):
    with db.connect() as con:
        return con.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()


def test_einrichtungscode_nur_ohne_benutzer(world, capsys):
    assert cli.setup_token() == 1
    assert "gibt bereits Benutzer" in capsys.readouterr().out
    with db.connect() as con:
        con.execute("DELETE FROM users")
    assert cli.setup_token() == 0
    code = capsys.readouterr().out.strip().rsplit(" ", 1)[-1]
    assert setup.check_setup_token(code)


def test_owner_anlegen(world, monkeypatch, capsys):
    answers(monkeypatch, ["konsole"], [NEW, NEW])
    assert cli.create_owner() == 0
    row = user("konsole")
    assert row["role"] == "owner" and security.verify_password(row["password_hash"], NEW)
    assert world.audit("user.create")[-1]["username"] == "Konsole"


@pytest.mark.parametrize("name, passwords, message", [
    ("mit leerzeichen", [], "Ungültiger Benutzername"),
    ("olga", [], "gibt es schon"),
    ("frisch", ["kurz", "kurz"], "mindestens 15 Zeichen"),
    ("frisch", [NEW, NEW + "x"], "stimmen nicht überein"),
])
def test_owner_anlegen_abgelehnt(world, monkeypatch, capsys, name, passwords, message):
    answers(monkeypatch, [name], passwords)
    assert cli.create_owner() == 1
    assert message in capsys.readouterr().out
    assert name == "olga" or user(name) is None


def test_passwort_zuruecksetzen_entsperrt_und_meldet_ab(world, monkeypatch):
    uid = world.user("gesperrt", active=False)
    token, _ = security.create_session(uid, "127.0.0.1", "pytest")
    answers(monkeypatch, ["gesperrt"], [NEW, NEW])
    assert cli.reset_password() == 0
    row = user("gesperrt")
    assert row["is_active"] == 1 and security.verify_password(row["password_hash"], NEW)
    assert security.load_session(token, main.settings) is None
    assert not security.verify_password(row["password_hash"], PASSWORD)


def test_passwort_unbekannter_benutzer(world, monkeypatch, capsys):
    answers(monkeypatch, ["niemand"], [])
    assert cli.reset_password() == 1 and "gibt es nicht" in capsys.readouterr().out


def report(*checks) -> diagnose.Report:
    r = diagnose.Report(panel_type="pterodactyl", url="http://panel.test")
    for area, level, title in checks:
        r.add(area, level, Msg(title), detail=Msg("Detail zu {t}", t=title) if level != diagnose.OK else "",
              fix=["Abhilfe"] if level == diagnose.ERROR else None)
    return r


def selftest(world, monkeypatch, rep: diagnose.Report, **changes) -> int:
    async def fake_run(settings, cand, **kw):
        return rep
    monkeypatch.setattr(diagnose, "run", fake_run)
    settings = dataclasses.replace(main.settings, pterodactyl_url="http://panel.test", app_key="a", client_key="c",
                                   **changes)
    return asyncio.run(cli.selftest(settings))


def test_selbsttest_ohne_fehler(world, monkeypatch, capsys):
    area = diagnose.AREAS[0][0]
    assert selftest(world, monkeypatch, report((area, diagnose.OK, "Panel erreichbar")), manager_mode="read-only") == 0
    out = capsys.readouterr().out
    assert "✓ Panel erreichbar" in out and "Read-Only-Sperre greift" in out and "0 Fehler" in out
    assert db.get_setting("connection_last_check")


def test_selbsttest_mit_fehler(world, monkeypatch, capsys):
    area = diagnose.AREAS[0][0]
    rep = report((area, diagnose.ERROR, "Key ungültig"), (area, diagnose.WARNING, "Uhrzeit weicht ab"))
    assert selftest(world, monkeypatch, rep, manager_mode="read-write") == 1
    out = capsys.readouterr().out
    assert "✕ Key ungültig" in out and "Detail zu Key ungültig" in out and "→ Abhilfe" in out
    assert "! Uhrzeit weicht ab" in out and "Read-Only-Sperre" not in out


def test_selbsttest_ohne_einrichtung(world, capsys):
    settings = dataclasses.replace(main.settings, pterodactyl_url="", app_key="", client_key="")
    assert asyncio.run(cli.selftest(settings)) == 1
    assert "Noch nicht eingerichtet" in capsys.readouterr().out


def test_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["hatchtower", "version"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0 and main.__version__ in capsys.readouterr().out
