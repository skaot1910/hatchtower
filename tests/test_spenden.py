"""Donations: link check, amounts, and that the public list only shows the chosen details."""
import pytest

from hatchtower import db, donations


def test_link_nur_https():
    assert donations.clean_url("") == ""
    assert donations.clean_url(" https://paypal.me/meinname ") == "https://paypal.me/meinname"
    assert donations.clean_url("https://www.paypal.com/donate/?hosted_button_id=ABC") is not None
    for bad in ("http://paypal.me/x", "javascript:alert(1)", "https://", "https://pay pal.me", "paypal.me/x"):
        assert donations.clean_url(bad) is None, bad


def test_betraege():
    assert donations.parse_amount("") is None
    assert donations.parse_amount("5") == 500
    assert donations.parse_amount("7,5") == 750
    assert donations.parse_amount("12.34") == 1234
    for bad in ("-3", "abc", "1,234", "5€"):
        with pytest.raises(ValueError):
            donations.parse_amount(bad)


def test_oeffentliche_liste_zeigt_nur_gewaehlte_angaben(world):
    with db.connect() as con:
        con.execute("INSERT INTO donations (donor, amount_cents, currency, donated_on, note, created_at)"
                    " VALUES (NULL, 1000, 'EUR', '2026-10-05', 'intern', ?)", (db.utcnow(),))
    db.set_setting("donate_history", "1", None)
    db.set_setting("donate_fields", "name,date", None)
    rows = donations.public_entries(donations.config())
    assert rows[0] == {"name": "Anonym", "day": "05.10.2026"}         # neuester zuerst, ohne Namen anonym
    assert rows[1] == {"name": "Mia", "day": "01.10.2026"}
    db.set_setting("donate_fields", "amount", None)
    rows = donations.public_entries(donations.config())
    assert rows[1] == {"name": "Anonym", "amount": "5 €"}            # Name ausgeblendet: alle anonym
    db.set_setting("donate_history", "0", None)
    assert donations.public_entries(donations.config()) == []


def test_statusseite_mit_spendenbox(world):
    anon = world.client()
    assert "donate-box" not in anon.get("/status").text
    db.set_setting("donate_on_status", "1", None)
    db.set_setting("donate_history", "1", None)
    db.set_setting("donate_fields", "date", None)
    page = anon.get("/status").text
    assert "donate-box" in page and "hosted_button_id=X" in page
    assert "Mia" not in page and "01 Oct 2026" in page and "Anonymous" in page
    db.set_setting("donate_url", "", None)                             # ohne Link kein Button
    assert "donate-box" not in anon.get("/status").text


def test_herz_und_spendenseite_nur_wenn_eingeschaltet(world):
    user = world.client(world.ids["victim"])
    assert 'href="/donate"' in user.get("/").text
    assert user.get("/donate").status_code == 200
    db.set_setting("donate_in_app", "0", None)
    assert 'href="/donate"' not in user.get("/").text
    assert user.get("/donate").status_code == 404


def test_owner_traegt_spende_ein(world):
    owner = world.client(world.ids["olga"])
    r = owner.post("/admin/donations/entry", data={"csrf": owner.csrf, "donor": " Max ", "amount": "2,50",
                                                    "currency": "USD", "day": "2026-10-20", "note": "danke"})
    assert r.status_code == 303
    with db.connect() as con:
        row = con.execute("SELECT * FROM donations WHERE donor = 'Max'").fetchone()
    assert row["amount_cents"] == 250 and row["currency"] == "USD" and row["note"] == "danke"
    r = owner.post("/admin/donations/entry", data={"csrf": owner.csrf, "day": "2999-01-01"})
    assert "Datum" in r.headers["location"]
    r = owner.post("/admin/donations", data={"csrf": owner.csrf, "url": "http://unsicher.example"})
    assert db.get_setting("donate_url").startswith("https://www.paypal.com")     # unverändert
    assert "/admin/donations" in owner.get("/admin/donations").text
