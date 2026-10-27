"""Pre-releases: version order, choosing the offered release by the switch, no downgrade when switching off."""
from hatchtower import db, main, updates
from hatchtower.updates import version_key


def rel(version: str) -> dict:
    return {"version": version, "url": f"https://example.invalid/v{version}", "notes": f"Notes {version}",
            "published_at": None}


def check(stable: str | None, newest: str | None) -> updates.UpdateCheck:
    upd = updates.UpdateCheck()
    upd._stable = rel(stable) if stable else None
    upd._newest = rel(newest) if newest else None
    return upd


def test_reihenfolge():
    order = ["0.37.2", "0.38.0-pre.1", "0.38.0-pre.2", "0.38.0-pre.10", "0.38.0-rc.1", "0.38.0", "0.38.1"]
    assert sorted(reversed(order), key=version_key) == order
    assert version_key("v0.38.0") == version_key("0.38.0")
    assert updates.is_prerelease("0.38.0-rc.1") and not updates.is_prerelease("0.38.0")


def test_schalter_waehlt_angebot(monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.37.2")
    upd = check("0.37.2", "0.38.0-pre.1")
    assert not upd.available and upd.latest == "0.37.2"
    upd.prereleases = True
    assert upd.available and upd.latest == "0.38.0-pre.1" and upd.latest_is_prerelease
    assert upd.notes == "Notes 0.38.0-pre.1"


def test_kein_downgrade_aber_naechstes_release(monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.38.0-pre.2")
    upd = check("0.37.2", "0.38.0-pre.2")
    assert not upd.available                      # switch off: stays on the pre-release
    upd = check("0.38.0", "0.38.0")
    assert upd.available and upd.latest == "0.38.0"   # final version overtakes the pre-release


def test_route_speichert_einstellung(world, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.37.2")
    upd = check("0.37.2", "0.38.0-pre.1")
    monkeypatch.setattr(main.app.state, "updates", upd)
    o = world.client(world.ids["olga"])
    r = o.post("/admin/system/update-channel", data={"csrf": o.csrf, "prereleases": "1"})
    assert r.status_code == 303
    assert db.get_setting(updates.PRERELEASE_SETTING) == "1" and upd.available
    page = o.get("/admin/updates").text
    assert "0.38.0-pre.1" in page and "tag-pre" in page
    o.post("/admin/system/update-channel", data={"csrf": o.csrf})
    assert db.get_setting(updates.PRERELEASE_SETTING) == "0" and not upd.available
    assert [a["action"] for a in world.audit("system.update_channel")]


# -- Update aus der Oberfläche: hängengebliebener Zustand ---------------------------------------

def _state(tmp_path, monkeypatch, started, helper):
    from datetime import datetime, timedelta, timezone
    from hatchtower import updates
    monkeypatch.setattr(updates, "STATUS_DIR", tmp_path / "status")
    monkeypatch.setattr(updates, "_helper_running", lambda: helper)
    when = (datetime.now(timezone.utc) - timedelta(seconds=started)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    (tmp_path / "update.state").write_text(f"state=running\nfrom=0.41.1\nto=\nstarted={when}\nfinished=\n")
    return updates.read_state(tmp_path)["state"]


def test_update_ohne_helfer_gilt_als_unterbrochen(tmp_path, monkeypatch):
    assert _state(tmp_path, monkeypatch, 120, helper=False) == "interrupted"
    assert _state(tmp_path, monkeypatch, 120, helper=True) == "running"
    assert _state(tmp_path, monkeypatch, 3, helper=False) == "running"      # helper just starting
    assert _state(tmp_path, monkeypatch, 120, helper=None) == "running"     # processes not visible: trust the file
    assert _state(tmp_path, monkeypatch, 40 * 60, helper=None) == "interrupted"
    assert _state(tmp_path, monkeypatch, 40 * 60, helper=True) == "interrupted"


def test_nach_unterbrochenem_update_neu_anfragbar(tmp_path, monkeypatch):
    from hatchtower import updates
    _state(tmp_path, monkeypatch, 120, helper=False)
    assert updates.request_update(tmp_path, "latest")
    assert updates.read_state(tmp_path)["state"] == "requested"
