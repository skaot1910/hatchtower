"""Impressum und Datenschutzerklärung.

Beide Seiten sind optional (Haken unter System → Rechtliches) und frei bearbeitbar.
Die Vorlagen passen zu dem, was Hatchtower tatsächlich speichert; ergänzt werden nur
Name, Anschrift und Kontakt. Hinweis: Die Vorlagen sind keine Rechtsberatung und ohne Gewähr
für Richtigkeit oder Vollständigkeit; sie sollen nur das Hinterlegen eigener Angaben ermöglichen.

Textformat: Leerzeile = neuer Absatz, "## " = Überschrift, "- " = Aufzählung.
{audit_tage} und {konsolen_tage} werden durch die eingestellten Aufbewahrungsfristen ersetzt.
"""
from __future__ import annotations

import html
import re

from markupsafe import Markup

from . import db, retention

PAGES = {"impressum": "legal_impressum", "datenschutz": "legal_privacy"}
URL = re.compile(r"(https?://[^\s<>\"]+)")
MAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")

TEMPLATES = {
    ("impressum", "de"): """## Angaben gemäß § 5 DDG

[Vorname Nachname]
[Straße Hausnummer]
[PLZ Ort]
[Land]

## Kontakt

E-Mail: [name@example.com]

## Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV

[Vorname Nachname], Anschrift wie oben

## Hinweis

Diese Seite ist ein privat betriebenes Verwaltungswerkzeug für Game-Server einer Community. Es werden keine Waren oder Dienstleistungen gegen Entgelt angeboten.""",
    ("impressum", "en"): """## Information pursuant to § 5 DDG (German Digital Services Act)

[First name Last name]
[Street and number]
[Postcode City]
[Country]

## Contact

Email: [name@example.com]

## Responsible for content pursuant to § 18 (2) MStV

[First name Last name], address as above

## Note

This site is a privately operated management tool for the game servers of a community. No goods or services are offered for payment.""",
    ("datenschutz", "de"): """## 1. Verantwortlicher

[Vorname Nachname]
[Straße Hausnummer], [PLZ Ort]
E-Mail: [name@example.com]

## 2. Allgemeines

Diese Seite ist ein Verwaltungswerkzeug (Hatchtower) für Game-Server. Sie verwendet keine Analyse- oder Tracking-Dienste, keine Werbung und lädt keine Inhalte von Drittanbietern (keine externen Schriften, Skripte oder Bilder).

## 3. Benutzerkonto

Für die Anmeldung speichern wir den Benutzernamen, das Passwort als nicht umkehrbaren Hash (Argon2id), die Rolle, die vergebenen Rechte, den Zeitpunkt der letzten Anmeldung sowie die gewählte Sprache und das Theme. Freiwillig kannst du eine E-Mail-Adresse hinterlegen, etwa für das Zurücksetzen des Passworts oder für Benachrichtigungen.

Rechtsgrundlage ist Art. 6 Abs. 1 lit. b DSGVO (Bereitstellung des Kontos). Die Daten werden gelöscht, sobald das Konto gelöscht wird.

## 4. Protokolle (Audit-Log) und Sicherheit

Zur Sicherheit des Systems protokollieren wir Anmeldungen und Aktionen (zum Beispiel Server starten, Dateien ändern) mit Zeitpunkt, Benutzername, IP-Adresse und Ergebnis. Fehlgeschlagene Anmeldeversuche werden vorübergehend gezählt, um Angriffe abzuwehren.

Rechtsgrundlage ist unser berechtigtes Interesse an einem sicheren Betrieb und der Nachvollziehbarkeit von Änderungen (Art. 6 Abs. 1 lit. f DSGVO). Protokolleinträge werden nach {audit_tage} Tagen automatisch gelöscht – auch dann, wenn ein Konto vorher gelöscht wurde.

## 5. Cookies

Wir setzen ausschließlich technisch notwendige Cookies (§ 25 Abs. 2 TDDDG), eine Einwilligung ist dafür nicht erforderlich:

- hatchtower_session: hält dich angemeldet (endet mit der Abmeldung, spätestens nach 12 Stunden)
- hatchtower_prelogin: Schutz des Anmeldeformulars (15 Minuten)
- hatchtower_lang: gewählte Sprache (1 Jahr)

## 6. Konsolen-Archiv

Die Konsolenausgabe der Game-Server wird zur Fehlersuche gespeichert. Sie kann Spielernamen, Chatnachrichten oder IP-Adressen von Spielern enthalten. Rechtsgrundlage ist unser berechtigtes Interesse an einem stabilen Betrieb der Server (Art. 6 Abs. 1 lit. f DSGVO). Die Einträge werden nach {konsolen_tage} Tagen automatisch gelöscht.

## 7. E-Mail-Benachrichtigungen

Wenn du Benachrichtigungen per E-Mail einschaltest, verwenden wir deine E-Mail-Adresse für den Versand. Die E-Mails werden über den Mailserver [Anbieter des Mailservers] verschickt. Du kannst die Benachrichtigungen jederzeit in deinem Konto ausschalten (Art. 6 Abs. 1 lit. a DSGVO).

## 8. Server und Reverse-Proxy

Die Seite wird auf eigener Hardware betrieben und ist über einen Reverse-Proxy erreichbar. Dabei fallen technisch bedingt Verbindungsdaten wie IP-Adresse und Zeitpunkt an.

## 9. Weitergabe

Eine Weitergabe an Dritte findet nicht statt, außer an den genannten Mailserver beim Versand von E-Mails.

## 10. Deine Rechte

Du hast das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch (Art. 21). Wende dich dafür an die oben genannte Kontaktadresse. Außerdem kannst du dich bei einer Datenschutz-Aufsichtsbehörde beschweren.

Stand: [Datum]""",
    ("datenschutz", "en"): """## 1. Controller

[First name Last name]
[Street and number], [Postcode City]
Email: [name@example.com]

## 2. General

This site is a management tool (Hatchtower) for game servers. It does not use analytics or tracking services, no advertising, and does not load content from third parties (no external fonts, scripts or images).

## 3. User account

For logging in we store your username, your password as an irreversible hash (Argon2id), your role, the permissions granted to you, the time of your last login, and your chosen language and theme. You may optionally store an email address, for example to reset your password or to receive notifications.

The legal basis is Art. 6 (1) (b) GDPR (providing the account). The data is deleted when the account is deleted.

## 4. Logs (audit log) and security

For the security of the system we log logins and actions (for example starting servers or changing files) with time, username, IP address and result. Failed login attempts are counted temporarily to fend off attacks.

The legal basis is our legitimate interest in secure operation and traceability of changes (Art. 6 (1) (f) GDPR). Log entries are deleted automatically after {audit_tage} days – even if an account was deleted earlier.

## 5. Cookies

We only use technically necessary cookies (§ 25 (2) TDDDG); no consent is required for them:

- hatchtower_session: keeps you logged in (ends when you log out, at the latest after 12 hours)
- hatchtower_prelogin: protects the login form (15 minutes)
- hatchtower_lang: chosen language (1 year)

## 6. Console archive

The console output of the game servers is stored for troubleshooting. It may contain player names, chat messages or IP addresses of players. The legal basis is our legitimate interest in stable operation of the servers (Art. 6 (1) (f) GDPR). Entries are deleted automatically after {konsolen_tage} days.

## 7. Email notifications

If you enable email notifications, we use your email address to send them. Emails are sent via the mail server [mail provider]. You can turn notifications off in your account at any time (Art. 6 (1) (a) GDPR).

## 8. Server and reverse proxy

The site runs on our own hardware and is reachable through a reverse proxy. For technical reasons, connection data such as IP address and time are processed.

## 9. Disclosure

Data is not passed on to third parties, except to the mail server mentioned above when sending emails.

## 10. Your rights

You have the right of access (Art. 15 GDPR), rectification (Art. 16), erasure (Art. 17), restriction of processing (Art. 18), data portability (Art. 20) and to object (Art. 21). Please contact the address above. You also have the right to lodge a complaint with a data protection supervisory authority.

Last updated: [date]""",
}


def template(page: str, lang: str) -> str:
    return TEMPLATES.get((page, lang)) or TEMPLATES[(page, "de")]


def enabled(page: str) -> bool:
    return db.get_setting(PAGES[page] + "_enabled") == "1"


def text(page: str) -> str:
    return db.get_setting(PAGES[page] + "_text")


def links() -> list[tuple[str, str]]:
    """Für Fußzeile und Login-Seite: (Pfad, deutscher Titel)."""
    result = []
    if enabled("impressum") and text("impressum").strip():
        result.append(("/impressum", "Impressum"))
    if enabled("datenschutz") and text("datenschutz").strip():
        result.append(("/datenschutz", "Datenschutz"))
    return result


def _inline(line: str) -> str:
    escaped = html.escape(line)
    escaped = URL.sub(lambda m: f'<a href="{m.group(1)}" rel="noopener noreferrer">{m.group(1)}</a>', escaped)
    return MAIL.sub(lambda m: f'<a href="mailto:{m.group(1)}">{m.group(1)}</a>', escaped)


def to_html(source: str) -> Markup:
    source = source.replace("{audit_tage}", str(retention.audit_days()))
    keep = retention.console_days()
    source = source.replace("{konsolen_tage}", str(keep) if keep else "0")
    out: list[str] = []
    for block in re.split(r"\n\s*\n", source.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        first = lines[0].strip()
        if first.startswith("### "):
            out.append(f"<h3>{_inline(first[4:])}</h3>")
            lines = lines[1:]
        elif first.startswith("## ") or first.startswith("# "):
            out.append(f"<h2>{_inline(first.lstrip('#').strip())}</h2>")
            lines = lines[1:]
        if not lines:
            continue
        if all(l.strip().startswith("- ") for l in lines if l.strip()):
            items = "".join(f"<li>{_inline(l.strip()[2:])}</li>" for l in lines if l.strip())
            out.append(f"<ul>{items}</ul>")
        else:
            out.append("<p>" + "<br>".join(_inline(l) for l in lines) + "</p>")
    return Markup("\n".join(out))
