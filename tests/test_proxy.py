"""Betrieb im Internet: Reverse-Proxy, echte Besucher-IPs und Anmeldesperre pro Konto und IP."""
import pytest
from fastapi.testclient import TestClient

from hatchtower import db, main, security
from hatchtower.config import format_proxies, parse_proxies

PASSWORD = "Sehr-Langes-Passwort-123"


def visitor(ip: str) -> tuple[TestClient, str]:
    c = TestClient(main.app, follow_redirects=False, base_url="http://hatchtower.test", client=(ip, 40000))
    token = security.prelogin_token(main.settings.secret_key)
    c.cookies.set(security.PRELOGIN_COOKIE, token)
    return c, token


def login(ip: str, password: str, headers: dict | None = None):
    c, token = visitor(ip)
    return c.post("/login", data={"username": "olga", "password": password, "prelogin": token}, headers=headers or {})


def test_fremde_fehlversuche_sperren_den_besitzer_nicht(world):
    for _ in range(6):
        assert login("203.0.113.5", "falsch").status_code == 400
    # Der Angreifer ist für dieses Konto gesperrt, auch mit dem richtigen Passwort ...
    assert login("203.0.113.5", PASSWORD).status_code == 400
    assert world.audit("login")[-1]["result"] == "blocked"
    # ... der Besitzer von seiner eigenen IP nicht
    assert login("198.51.100.7", PASSWORD).status_code == 303


def test_passwortwechsel_hebt_sperre_auf(world):
    for _ in range(5):
        login("203.0.113.5", "falsch")
    assert login("203.0.113.5", PASSWORD).status_code == 400
    new = "Ganz-Neues-Passwort-4567"
    with db.connect() as con:   # wie „hatchtower reset-password“ in einem anderen Prozess
        con.execute("UPDATE users SET password_hash = ? WHERE username = 'olga'", (security.hash_password(new),))
    assert login("203.0.113.5", new).status_code == 303


def test_proxy_netz_liefert_echte_ip_und_https(world, monkeypatch):
    monkeypatch.setattr(main.settings, "trusted_proxies", parse_proxies("10.0.0.0/8"))
    r = login("10.1.2.3", PASSWORD, {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"})
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert security.SESSION_COOKIE in cookie and "secure" in cookie.lower()
    assert "strict-transport-security" in r.headers
    assert world.audit("login")[-1]["ip"] == "203.0.113.9"


def test_proxy_header_von_fremden_zaehlen_nicht(world, monkeypatch):
    monkeypatch.setattr(main.settings, "trusted_proxies", parse_proxies("10.0.0.0/8"))
    r = login("192.0.2.1", PASSWORD, {"X-Forwarded-For": "10.9.9.9", "X-Forwarded-Proto": "https"})
    assert r.status_code == 303
    assert "secure" not in r.headers["set-cookie"].lower()
    assert world.audit("login")[-1]["ip"] == "192.0.2.1"


def test_proxy_liste_pruefen():
    proxies = parse_proxies("127.0.0.1, 172.16.0.0/12; ::1")
    assert format_proxies(proxies) == "127.0.0.1, 172.16.0.0/12, ::1"
    for bad in ("0.0.0.0/0", "10.0.0.0/7", "::/0", "kein-proxy"):
        with pytest.raises(ValueError):
            parse_proxies(bad)


def test_hinweis_auf_nicht_eingetragenen_proxy(world, monkeypatch):
    owner, _ = visitor("127.0.0.1")
    token, _ = security.create_session(world.ids["olga"], "127.0.0.1", "pytest")
    owner.cookies.set(security.SESSION_COOKIE, token)
    hint = "nicht eingetragen ist"
    assert hint not in owner.get("/").text
    owner.get("/", headers={"X-Forwarded-For": "203.0.113.9"})
    assert hint in owner.get("/").text
    # Normale Benutzer sehen den Hinweis nicht
    c = world.client(world.ids["victim"])
    assert hint not in c.get("/").text
    monkeypatch.setattr(main.settings, "trusted_proxies", parse_proxies("127.0.0.1"))
    assert hint not in owner.get("/").text
