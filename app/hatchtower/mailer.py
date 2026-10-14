"""E-Mail-Versand über SMTP (nur Standardbibliothek).

Die Zugangsdaten trägt der Owner unter System → E-Mail ein; das Passwort wird
verschlüsselt gespeichert. Ohne eingeschalteten Versand verschickt Hatchtower nichts.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

from . import APP_NAME, db, i18n

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
    username = db.get_setting("mail_username")
    return MailConfig(
        enabled=db.get_setting("mail_enabled") == "1",
        host=db.get_setting("mail_host"),
        port=port,
        security=security if security in SECURITY else "starttls",
        username=username,
        password=vault.decrypt(db.get_setting("mail_password_enc")) or "",
        # Ohne eigene Absenderadresse gilt die Login-Adresse (bei Gmail & Co. ohnehin der Absender)
        sender=db.get_setting("mail_from") or (username if valid_email(username) else ""),
        sender_name=db.get_setting("mail_from_name") or APP_NAME,
        base_url=db.get_setting("mail_base_url").rstrip("/"),
    )


def email_optional() -> bool:
    """Owner-Einstellung unter Benutzer: Benutzer dürfen auch ohne E-Mail-Adresse sein."""
    return db.get_setting("users_email_optional") == "1"


def email_required(cfg: MailConfig) -> bool:
    """Neue Benutzer brauchen eine E-Mail-Adresse – nur wenn E-Mail eingeschaltet ist."""
    return cfg.ready and not email_optional()


# == Aufbau der E-Mails =========================================================================
# Alle Mails haben dasselbe Gerüst: Kopf mit Logo (sonst Name), Überschrift, Anrede, Absätze,
# optional ein Knopf (darunter der Link zum Kopieren), Angaben als Tabelle, Hinweis, Gruß und
# Fußzeile. Verschickt wird HTML mit einer Text-Fassung für Mailprogramme ohne HTML.

LOGO_DIR: Path | None = None       # setzt main.py beim Start (settings.logo_dir)
TIMEZONE = "Europe/Berlin"         # setzt main.py; für Zeitangaben in den Mails
ACCENT = "#6d28d9"
TEXT = "#1f2937"
MUTED = "#6b7280"
LINE = "#e5e7eb"


@dataclass
class Mail:
    subject: str
    heading: str
    paragraphs: list[str]
    name: str | None = None                        # Anrede „Hallo {name},“; ohne Namen „Hallo,“
    button: tuple[str, str] | None = None          # (Beschriftung, Adresse)
    facts: list[tuple[str, str]] = field(default_factory=list)
    note: str | None = None                        # grau hinterlegter Hinweis
    reason: str | None = None                      # Fußzeile: warum diese Mail kommt
    base_url: str = ""
    unsubscribe: str | None = None                 # Abmeldelink (Benachrichtigungen), auch als List-Unsubscribe
    lang: str = field(default_factory=i18n.current)  # Sprache beim Erstellen; gilt auch für Anrede und Fußzeile


def brand_name() -> str:
    return (db.get_setting("brand_name") or "").strip() or APP_NAME


def now_text() -> str:
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).strftime(i18n.t("%d.%m.%Y, %H:%M Uhr"))


def logo_png() -> bytes | None:
    """Logo aus dem Erscheinungsbild als PNG (viele Mailprogramme zeigen kein WebP), höchstens 96 px hoch."""
    if LOGO_DIR is None:
        return None
    name = db.get_setting("brand_logo_header") or db.get_setting("brand_logo_login")
    if not name or "/" in name or "\\" in name:
        return None
    try:
        from PIL import Image
        with Image.open(LOGO_DIR / name) as img:
            img = img.convert("RGBA")
            img.thumbnail((480, 96))
            out = io.BytesIO()
            img.save(out, "PNG", optimize=True)
            return out.getvalue()
    except Exception as exc:          # fehlende oder kaputte Datei: Mail ohne Logo
        log.info("Logo für E-Mail nicht verwendbar: %s", exc)
        return None


def _greeting(mail: Mail) -> str:
    return i18n.t("Hallo {name},", name=mail.name) if mail.name else i18n.t("Hallo,")


def _footer_lines(mail: Mail) -> list[str]:
    brand = brand_name()
    lines = [i18n.t("Diese Nachricht wurde automatisch von {brand} verschickt.", brand=brand)]
    if mail.reason:
        lines.append(mail.reason)
    if brand != APP_NAME:
        lines.append(i18n.t("Bereitgestellt mit {app}", app=APP_NAME))
    return lines


def render_text(mail: Mail) -> str:
    out = [mail.heading, "=" * min(60, len(mail.heading)), "", _greeting(mail), ""]
    for p in mail.paragraphs:
        out += [p, ""]
    if mail.button:
        out += [f"{mail.button[0]}:", mail.button[1], ""]
    if mail.facts:
        width = max(len(k) for k, _ in mail.facts) + 1
        out += [f"{(k + ':').ljust(width)} {v}" for k, v in mail.facts] + [""]
    if mail.note:
        out += [mail.note, ""]
    out += [i18n.t("Viele Grüße"), brand_name(), "", "-- "] + _footer_lines(mail)
    if mail.unsubscribe:
        out.append(i18n.t("E-Mail-Benachrichtigungen abbestellen: {link}", link=mail.unsubscribe))
    if mail.base_url:
        out.append(mail.base_url)
    return "\n".join(out) + "\n"


def render_html(mail: Mail, logo_cid: str | None) -> str:
    e = html.escape
    brand = e(brand_name())
    para = f'style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{TEXT}"'
    if logo_cid:
        head = (f'<img src="cid:{logo_cid.strip("<>")}" alt="{brand}" height="48" style="display:block;height:48px;'
                f'width:auto;max-width:240px;border:0">')
    else:
        head = f'<span style="font-size:20px;font-weight:700;color:{ACCENT}">{brand}</span>'
    parts = [f"<p {para}>{e(_greeting(mail))}</p>"]
    parts += [f"<p {para}>{e(p).replace(chr(10), '<br>')}</p>" for p in mail.paragraphs]
    if mail.button:
        label, url = e(mail.button[0]), e(mail.button[1], quote=True)
        parts.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 18px"><tr>'
            f'<td style="border-radius:8px;background:{ACCENT}"><a href="{url}" style="display:inline-block;'
            f'padding:12px 24px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;'
            f'border-radius:8px">{label}</a></td></tr></table>'
            f'<p style="margin:0 0 20px;font-size:12px;line-height:1.5;color:{MUTED}">'
            f'{e(i18n.t("Falls der Knopf nicht funktioniert, kopiere diesen Link in deinen Browser:"))}<br>'
            f'<a href="{url}" style="color:{ACCENT};word-break:break-all">{e(mail.button[1])}</a></p>')
    if mail.facts:
        rows = []
        for i, (k, v) in enumerate(mail.facts):
            border = f"border-top:1px solid {LINE};" if i else ""
            rows.append(f'<tr><td style="{border}padding:9px 14px;font-size:13px;color:{MUTED};white-space:nowrap;'
                        f'vertical-align:top">{e(k)}</td><td style="{border}padding:9px 14px;font-size:13px;'
                        f'color:#111827">{e(v)}</td></tr>')
        parts.append(f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 20px;'
                     f'border:1px solid {LINE};border-radius:8px;border-collapse:separate;border-spacing:0;'
                     f'background:#f9fafb">{"".join(rows)}</table>')
    if mail.note:
        parts.append(f'<p style="margin:0 0 20px;padding:12px 14px;font-size:13px;line-height:1.5;color:#4b5563;'
                     f'background:#f3f4f6;border-radius:8px">{e(mail.note)}</p>')
    parts.append(f'<p {para}>{e(i18n.t("Viele Grüße"))}<br><strong>{brand}</strong></p>')
    footer = "<br>".join(e(line) for line in _footer_lines(mail))
    if mail.unsubscribe:
        footer += (f'<br><a href="{e(mail.unsubscribe, quote=True)}" style="color:{MUTED}">'
                   f'{e(i18n.t("E-Mail-Benachrichtigungen abbestellen"))}</a>')
    if mail.base_url:
        footer += f'<br><a href="{e(mail.base_url, quote=True)}" style="color:{MUTED}">{e(mail.base_url)}</a>'
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    return (
        f'<!doctype html><html lang="{i18n.current()}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(mail.subject)}</title></head>'
        f'<body style="margin:0;padding:0;background:#f3f4f6">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6">'
        f'<tr><td align="center" style="padding:28px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;'
        f'font-family:{font}">'
        f'<tr><td style="padding:0 4px 18px">{head}</td></tr>'
        f'<tr><td style="background:#ffffff;border-radius:12px;border-top:4px solid {ACCENT};padding:28px 28px 12px">'
        f'<h1 style="margin:0 0 18px;font-size:21px;line-height:1.3;color:#111827">{e(mail.heading)}</h1>'
        f'{"".join(parts)}</td></tr>'
        f'<tr><td style="padding:18px 8px 0;font-size:12px;line-height:1.6;color:{MUTED};text-align:center">'
        f'{footer}</td></tr></table></td></tr></table></body></html>')


def build_message(cfg: MailConfig, to: list[str], mail: Mail | str, subject: str = "") -> EmailMessage:
    """Fertige Nachricht: bei Mail HTML mit Text-Fassung und eingebettetem Logo, sonst nur Text."""
    msg = EmailMessage()
    domain = cfg.sender.split("@")[-1]
    msg["From"] = formataddr((cfg.sender_name, cfg.sender))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject if isinstance(mail, str) else mail.subject
    msg["Message-ID"] = make_msgid(domain=domain)
    if isinstance(mail, str):
        msg.set_content(mail)
        return msg
    mail.base_url = mail.base_url or cfg.base_url
    if mail.unsubscribe:                  # Abbestellen direkt im Mailprogramm (RFC 2369/8058)
        msg["List-Unsubscribe"] = f"<{mail.unsubscribe}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    previous = i18n.current()
    i18n.set_language(mail.lang)
    try:
        logo = logo_png()
        cid = make_msgid(domain=domain) if logo else None
        msg.set_content(render_text(mail))
        msg.add_alternative(render_html(mail, cid), subtype="html")
    finally:
        i18n.set_language(previous)
    if logo:
        msg.get_payload()[1].add_related(logo, "image", "png", cid=cid, filename="logo.png")
    return msg


def _send_blocking(cfg: MailConfig, to: list[str], subject: str, text: Mail | str) -> None:
    msg = build_message(cfg, to, text, subject)
    context = ssl.create_default_context()
    step = "connect"
    try:
        if cfg.security == "ssl":
            server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=20)
        with server:
            if cfg.security == "starttls":
                server.starttls(context=context)
            if cfg.username:
                step = "login"
                server.login(cfg.username, cfg.password)
            step = "send"
            server.send_message(msg)
    except (smtplib.SMTPException, OSError, UnicodeError) as exc:
        detail = f"{exc.__class__.__name__}: {str(exc)[:200]}"
        if step == "login" or isinstance(exc, smtplib.SMTPAuthenticationError):
            # Gmail & Co. trennen bei falschem Passwort teils einfach die Verbindung
            raise MailError(i18n.t("Anmeldung am SMTP-Server abgelehnt – Benutzername und Passwort prüfen. Gmail und"
                                   " Outlook brauchen ein App-Passwort (bei Gmail 16 Buchstaben). ({detail})",
                                   detail=detail)) from exc
        if step == "connect" and isinstance(exc, (smtplib.SMTPServerDisconnected, ssl.SSLError, TimeoutError)):
            raise MailError(i18n.t("Verbindung zum SMTP-Server fehlgeschlagen – passen Port und Verschlüsselung"
                                   " zusammen (465 = SSL/TLS, 587 = STARTTLS)? ({detail})", detail=detail)) from exc
        if isinstance(exc, smtplib.SMTPSenderRefused):
            raise MailError(i18n.t("Der SMTP-Server lehnt die Absenderadresse ab – sie muss zum Postfach gehören."
                                   " ({detail})", detail=detail)) from exc
        raise MailError(detail) from exc


async def send(cfg: MailConfig, to: list[str] | str, subject: str, text: Mail | str) -> None:
    """Verschickt eine Nachricht. Bei einer Mail gilt deren Betreff, `subject` nur für reinen Text."""
    recipients = [to] if isinstance(to, str) else [t for t in to if t]
    if not cfg.ready:
        raise MailError("E-Mail-Versand ist nicht eingerichtet")
    if not recipients:
        return
    await asyncio.to_thread(_send_blocking, cfg, recipients, subject, text)
