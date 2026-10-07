"""E-Mail-Versand über SMTP (nur Standardbibliothek).

Die Zugangsdaten trägt der Owner unter System → E-Mail ein; das Passwort wird
verschlüsselt gespeichert. Ohne eingeschalteten Versand verschickt Hatchtower nichts.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import APP_NAME, db

log = logging.getLogger("hatchtower.mail")

EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,}$")
SECURITY = ("starttls", "ssl", "none")


class MailError(RuntimeError):
    pass


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or "")) and len(value) <= 254


@dataclass
class MailConfig:
    enabled: bool
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    sender_name: str
    base_url: str

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.host and self.sender and self.base_url)


def load_config(vault) -> MailConfig:
    try:
        port = int(db.get_setting("mail_port", "587"))
    except ValueError:
        port = 587
    security = db.get_setting("mail_security", "starttls")
    return MailConfig(
        enabled=db.get_setting("mail_enabled") == "1",
        host=db.get_setting("mail_host"),
        port=port,
        security=security if security in SECURITY else "starttls",
        username=db.get_setting("mail_username"),
        password=vault.decrypt(db.get_setting("mail_password_enc")) or "",
        sender=db.get_setting("mail_from"),
        sender_name=db.get_setting("mail_from_name") or APP_NAME,
        base_url=db.get_setting("mail_base_url").rstrip("/"),
    )


def _send_blocking(cfg: MailConfig, to: list[str], subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.sender_name, cfg.sender))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=cfg.sender.split("@")[-1])
    msg.set_content(text)
    context = ssl.create_default_context()
    try:
        if cfg.security == "ssl":
            server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=20)
        with server:
            if cfg.security == "starttls":
                server.starttls(context=context)
            if cfg.username:
                server.login(cfg.username, cfg.password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"{exc.__class__.__name__}: {str(exc)[:200]}") from exc


async def send(cfg: MailConfig, to: list[str] | str, subject: str, text: str) -> None:
    recipients = [to] if isinstance(to, str) else [t for t in to if t]
    if not cfg.ready:
        raise MailError("E-Mail-Versand ist nicht eingerichtet")
    if not recipients:
        return
    await asyncio.to_thread(_send_blocking, cfg, recipients, subject, text)


def signature() -> str:
    return f"\n\n-- \n{APP_NAME}"
