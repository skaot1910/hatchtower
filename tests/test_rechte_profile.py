"""Rechte aus Sicht typischer Benutzerprofile und Randfälle, die die Matrix pro Route nicht abdeckt.

- Ein Benutzer, der nur neu starten darf, kommt an nichts anderes heran – über keine Route.
- Geplante Aufgaben (Zeitpläne, Backup-Jobs) folgen denselben Regeln, wenn es den Ersteller nicht mehr gibt.
- Wer eine Rechtegruppe löscht, sieht vorher, dass Benutzer mit „Alle Server“ ihren Zugriff verlieren.
- Benutzer sehen nur Benachrichtigungen, die sie auch bekommen können.
"""
from __future__ import annotations

import pytest

from hatchtower import automation, backups, db, notify, permissions
from routes import ROUTES
from test_rechte_matrix import assert_denied, assert_ok, ids, send

RESTART_ONLY = {"srv-a": {"server.view", "power.restart"}}
# Was ein Benutzer mit „Sehen“ und „Restart“ erreichen darf – alles andere muss 403 liefern
ALLOWED = {"GET /servers/{ident}", "GET /api/servers/{ident}/metrics", "POST /api/servers/{ident}/favorite",
           "POST /api/servers/{ident}/power [restart]"}
GUARDED = [r for r in ROUTES if r.access in ("server", "kill", "automation", "backup", "owner") and r.id not in ALLOWED]


@pytest.mark.parametrize("route", GUARDED, ids=ids(GUARDED))
def test_nur_neustart_kommt_an_nichts_anderes(world, route):
    uid = world.user("neustarter", grants=RESTART_ONLY)
    before = world.snapshot()
    response = send(world, world.client(uid), route)
    assert_denied(world, response, before, uid)


def test_nur_neustart_darf_neu_starten_und_sieht_keine_geheimnisse(world):
    uid = world.user("neustarter", grants=RESTART_ONLY)
    c = world.client(uid)
    page = c.get("/servers/srv-a")
    assert page.status_code == 200
    html = page.text
    assert "geheimes-beitrittspasswort" not in html
    for link in ("/servers/srv-a/files", "/servers/srv-a/settings", "/admin/users", "/admin/backups",
                 "/admin/automation", "/admin/connection", 'data-tab-link="konsole"'):
        assert link not in html, link
    assert "geheimes-beitrittspasswort" not in c.get("/api/overview").text
    assert "geheimes-beitrittspasswort" not in c.get("/").text
    restart = next(r for r in ROUTES if r.id == "POST /api/servers/{ident}/power [restart]")
    assert_ok(send(world, c, restart))
    assert [w[1] for w in world.ptero.writes] == ["/api/client/servers/srv-a/power"]
    # dasselbe Recht gilt nicht für einen anderen Server
    world.ptero.calls.clear()
    before = world.snapshot()
    assert_denied(world, send(world, c, restart, ident="srv-b"), before, uid)


# == Geplante Aufgaben gelöschter oder fremder Ersteller =========================================

def schedule(created_by):
    return {"created_by": created_by, "identifier": "srv-a", "action": "restart", "warn_command": None}


def job(created_by):
    return {"created_by": created_by, "stop_mode": "none", "warn_command": None}


@pytest.mark.parametrize("check", [lambda c: automation.creator_allows(schedule(c)),
                                   lambda c: backups.creator_allows(job(c), "srv-a")],
                         ids=["zeitplan", "backup-job"])
def test_ersteller_regeln_gelten_gleich_fuer_zeitplaene_und_backups(world, check):
    planer = world.user("planer", grants={"srv-a": {"automation.manage", "power.restart", "backups.manage"}})
    assert check(None), "ältere Einträge ohne Ersteller laufen weiter"
    assert check(world.ids["olga"]), "Aufgaben von Ownern laufen immer"
    assert check(planer)
    assert not check(world.ids["victim"]), "ohne die nötigen Rechte läuft nichts"
    assert not check(987654), "gibt es den Ersteller nicht mehr, läuft nichts"
    with db.connect() as con:
        con.execute("UPDATE users SET is_active = 0 WHERE id = ?", (planer,))
    assert not check(planer), "gesperrte Ersteller: nichts läuft"


# == Rechtegruppe mit „Alle Server, auch künftige“ löschen ==========================================

def test_gruppe_loeschen_warnt_auch_vor_alle_server(world):
    gid = int(world.args["group_id"])
    uid = world.user("alle", "user")
    permissions.save_default_group(uid, gid)
    assert "server.view" in permissions.load_grants(uid)["srv-b"]
    group = next(g for g in permissions.load_groups() if g["id"] == gid)
    assert (group["usage"], group["default_users"]) == (0, 1)
    o = world.client(world.ids["olga"])
    html = o.get("/admin/groups").text
    assert "„Alle Server“ für 1 Benutzer" in html
    assert "verlieren damit den Zugriff auf alle Server" in html
    assert_ok(o.post(f"/admin/groups/{gid}/delete", data={"csrf": o.csrf}))
    assert permissions.load_grants(uid) == {}
    assert world.audit("group.delete")[-1]["detail"].find("alle_server_entfernt") >= 0


# == Benachrichtigungen ===========================================================================

def test_benutzer_sehen_nur_empfangbare_ereignisse():
    assert set(notify.events_for(True)) == set(notify.EVENTS)
    assert not set(notify.events_for(False)) & notify.PANEL_EVENTS
    assert "crash" in notify.events_for(False)


def test_benachrichtigungen_speichern_behaelt_verborgene_auswahl(world):
    uid = world.ids["victim"]
    with db.connect() as con:
        con.execute("UPDATE users SET notify_events = 'crash,backup_ok' WHERE id = ?", (uid,))
    c = world.client(uid)
    # ein Benutzer kann Owner-Meldungen auch per Hand nicht dazuschalten …
    assert_ok(c.post("/account/notify", data={"csrf": c.csrf, "notify_email": "1", "event__disk": "1",
                                              "event__panel_down": "1"}))
    with db.connect() as con:
        stored = con.execute("SELECT notify_events FROM users WHERE id = ?", (uid,)).fetchone()[0]
    # … und verliert die bisherige (für ihn unsichtbare) Auswahl nicht, falls er später Owner wird
    assert stored.split(",") == ["disk", "backup_ok"]
