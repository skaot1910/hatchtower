"""Passwörter, Sitzungen, CSRF, Anmeldeschutz und Client-IP."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from . import db
from .config import Settings, is_trusted_proxy

# OWASP-Empfehlung für Argon2id: 19 MiB, 2 Durchläufe, 1 Thread
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

# Gültiger Hash als Vergleichswert, damit unbekannte Benutzer gleich lange brauchen
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))

SESSION_COOKIE = "hatchtower_session"
PRELOGIN_COOKIE = "hatchtower_prelogin"
# "Stay signed in": such sessions skip the idle timeout and last this many days
REMEMBER_DAYS = 30


# -- Passwörter ----------------------------------------------------------

def password_problems(password: str, username: str = "") -> list[str]:
    problems = []
    if len(password) < 15:
        problems.append("mindestens 15 Zeichen")
    if not re.search(r"[a-z]", password):
        problems.append("einen Kleinbuchstaben")
    if not re.search(r"[A-Z]", password):
        problems.append("einen Großbuchstaben")
    if not re.search(r"\d", password):
        problems.append("eine Zahl")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("ein Sonderzeichen")
    return problems


def password_message(password: str, username: str = "") -> str | None:
    """Fertige, übersetzte Fehlermeldung oder None, wenn das Passwort passt."""
    from .i18n import t
    parts = []
    problems = password_problems(password, username)
    if problems:
        parts.append(t("Das Passwort braucht {list}.", list=", ".join(t(x) for x in problems)))
    if username and username.lower() in password.lower():
        parts.append(t("Das Passwort darf den Benutzernamen nicht enthalten."))
    return " ".join(parts) or None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# -- Verschlüsselung gespeicherter Serverpasswörter ------------------------

class Vault:
    def __init__(self, data_key: str):
        self._fernet = Fernet(data_key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None


# -- Sitzungen -------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, ip: str, user_agent: str, remember: bool = False) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = db.utcnow()
    with db.connect() as con:
        con.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen, ip, user_agent, remember)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_token_hash(token), user_id, csrf, now, now, ip, user_agent[:200], int(remember)),
        )
    return token, csrf


def load_session(token: str | None, settings: Settings, touch: bool = True):
    """Gibt (Session, Benutzer) zurück oder None, wenn abgelaufen/ungültig.

    touch=False prüft nur und zählt nicht als Aktivität (für offene Konsolen-Verbindungen)."""
    if not token:
        return None
    th = _token_hash(token)
    now = datetime.now(timezone.utc)
    with db.connect() as con:
        row = con.execute(
            "SELECT s.*, u.username, u.role, u.is_active, u.theme, u.language, u.view_mode, u.view_locked FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ?",
            (th,),
        ).fetchone()
        if row is None:
            return None
        created = datetime.fromisoformat(row["created_at"])
        last_seen = datetime.fromisoformat(row["last_seen"])
        if row["remember"]:
            timed_out = now - created > timedelta(days=REMEMBER_DAYS)
        else:
            timed_out = (now - last_seen > timedelta(minutes=settings.session_idle_minutes)
                         or now - created > timedelta(hours=settings.session_max_hours))
        expired = timed_out or not row["is_active"]
        if expired:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            return None
        if touch and (now - last_seen).total_seconds() > 30:
            con.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (db.utcnow(), th))
        return row


def delete_session(token: str | None) -> None:
    if token:
        with db.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def delete_user_sessions(user_id: int) -> None:
    """Meldet den Benutzer überall ab (Passwort geändert, Konto gesperrt). Offene Links zum Zurücksetzen des
    Passworts gelten dann ebenfalls nicht mehr, sonst ließe sich das neue Passwort damit gleich wieder ersetzen."""
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM tokens WHERE purpose = 'reset' AND user_id = ?", (user_id,))


def csrf_ok(expected: str | None, given: str | None) -> bool:
    return bool(expected and given and hmac.compare_digest(expected, given))


def same_host(old: str | None, new: str | None) -> bool:
    """Gleiches Ziel? Gespeicherte Passwörter und Keys dürfen nur an den Host gehen, für den sie eingegeben wurden."""
    def norm(host: str | None) -> str:
        return (host or "").strip().lower().rstrip(".")
    return bool(norm(old)) and norm(old) == norm(new)


# -- Prelogin-CSRF (signiert, ohne Sitzung) ------------------------------

def prelogin_token(secret_key: str) -> str:
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def prelogin_valid(secret_key: str, cookie: str | None, form: str | None) -> bool:
    if not cookie or not form or not hmac.compare_digest(cookie, form) or "." not in cookie:
        return False
    nonce, sig = cookie.rsplit(".", 1)
    expected = hmac.new(secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# -- Abmeldelink für E-Mail-Benachrichtigungen ----------------------------
# Ohne Anmeldung nutzbar (DSGVO, RFC 8058). Die Signatur hängt an der Adresse: Ändert sich
# die E-Mail-Adresse, gelten alte Links nicht mehr.

def unsubscribe_token(secret_key: str, user_id: int, email: str) -> str:
    sig = hmac.new(secret_key.encode(), f"unsubscribe:{user_id}:{email.lower()}".encode(), hashlib.sha256)
    return f"{user_id}.{sig.hexdigest()[:40]}"


def unsubscribe_user(secret_key: str, token: str, lookup) -> dict | None:
    """Benutzer zum Abmeldelink oder None. `lookup(user_id)` liefert die Zeile mit id und email."""
    uid, _sep, _sig = token.partition(".")
    if not uid.isdigit() or len(token) > 80:
        return None
    row = lookup(int(uid))
    if row is None or not row["email"]:
        return None
    return row if hmac.compare_digest(token, unsubscribe_token(secret_key, row["id"], row["email"])) else None


# -- Anmeldeschutz -------------------------------------------------------

class LoginThrottle:
    """Begrenzt Anmeldeversuche pro IP und sperrt Konten mit wachsender Dauer (beides im Speicher).

    Die Kontosperre gilt nur für die IP, von der die Fehlversuche kamen. So kann ein Fremder, der einen
    Benutzernamen kennt, das Konto nicht für alle anderen sperren. Gegen verteiltes Raten schützen die
    langen Passwörter (mindestens 15 Zeichen, Argon2id). Jede Sperre hängt am Passwort-Hash: Wird das Passwort
    geändert (auch über die Konsole), ist sie aufgehoben.

    IPs werden als Netz gezählt: IPv6 als /64, weil jeder Anschluss mindestens so ein Netz hat und sonst
    pro Versuch eine neue Adresse nehmen könnte. IPv4 bleibt einzeln.
    """

    MAX_ENTRIES = 10000        # danach werden alte Einträge verworfen
    ACCOUNT_FORGET = 86400     # Fehlversuche pro Konto und IP verfallen nach einem Tag ohne neuen Versuch

    def __init__(self, per_ip: int = 10, window: int = 900):
        self.per_ip = per_ip
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # (Benutzer-ID, IP) -> (Fehlversuche, gesperrt bis, letzter Versuch, Passwort-Hash), Zeiten aus time.monotonic()
        self._accounts: dict[tuple[int, str], tuple[int, float, float, str]] = {}

    @staticmethod
    def net(ip: str) -> str:
        """Schlüssel für die Zählung: IPv4 einzeln, IPv6 als /64-Netz."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if addr.version == 4:
            return str(addr)
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))

    def ip_blocked(self, ip: str) -> bool:
        ip = self.net(ip)
        hits = self._hits.get(ip)
        if not hits:
            return False
        cutoff = time.monotonic() - self.window
        while hits and hits[0] < cutoff:
            hits.popleft()
        if not hits:
            del self._hits[ip]
        return len(hits) >= self.per_ip

    def record_failure(self, ip: str) -> None:
        if len(self._hits) >= self.MAX_ENTRIES:
            cutoff = time.monotonic() - self.window
            for key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                del self._hits[key]
        self._hits[self.net(ip)].append(time.monotonic())

    def account_locked(self, user_id: int, ip: str, password_hash: str) -> bool:
        entry = self._accounts.get((user_id, self.net(ip)))
        return entry is not None and entry[3] == password_hash and entry[1] > time.monotonic()

    def account_failure(self, user_id: int, ip: str, password_hash: str) -> int:
        """Zählt einen Fehlversuch für Konto und IP; gibt die Sperrdauer in Minuten zurück (0 = keine)."""
        ip = self.net(ip)
        now = time.monotonic()
        if len(self._accounts) >= self.MAX_ENTRIES:
            for key in [k for k, v in self._accounts.items() if v[2] < now - self.ACCOUNT_FORGET]:
                del self._accounts[key]
        failed, _, last, known = self._accounts.get((user_id, ip), (0, 0.0, now, password_hash))
        if last < now - self.ACCOUNT_FORGET or known != password_hash:
            failed = 0
        failed += 1
        minutes = self.lock_minutes(failed)
        self._accounts[(user_id, ip)] = (failed, now + minutes * 60, now, password_hash)
        return minutes

    def account_success(self, user_id: int, ip: str) -> None:
        self._accounts.pop((user_id, self.net(ip)), None)

    @staticmethod
    def lock_minutes(failed_logins: int) -> int:
        if failed_logins < 5:
            return 0
        return min(2 ** (failed_logins - 5), 60)


# -- Client-IP -----------------------------------------------------------

def client_ip(peer: str | None, forwarded_for: str | None, settings: Settings) -> str:
    """Echte Client-IP. X-Forwarded-For zählt nur, wenn die Anfrage von einem eingetragenen Reverse-Proxy kommt."""
    peer = peer or "unbekannt"
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not is_trusted_proxy(peer_addr, settings.trusted_proxies) or not forwarded_for:
        return peer
    for candidate in reversed([p.strip() for p in forwarded_for.split(",") if p.strip()]):
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            return peer
        if not is_trusted_proxy(addr, settings.trusted_proxies):
            return candidate
    return peer
