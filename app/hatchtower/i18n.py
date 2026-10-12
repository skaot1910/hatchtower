"""Mehrsprachigkeit.

Die deutschen Texte im Code sind der Ausgangspunkt. Für jede weitere Sprache gibt es
eine Datei locales/<sprache>.json, die deutsche Texte auf die Übersetzung abbildet.
Fehlt eine Übersetzung, wird der deutsche Text angezeigt.

Platzhalter wie {name} werden nach der Übersetzung eingesetzt:
    t("Gruppe „{name}“ gespeichert", name=gruppe)
{panel} steht für den Namen des angebundenen Panels (Pterodactyl, Pelican oder Calagopus)
und wird automatisch eingesetzt.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path

LANGUAGES = {"de": "Deutsch", "en": "English"}
DEFAULT = "de"

_current: ContextVar[str] = ContextVar("hatchtower_lang", default=DEFAULT)
_catalogs: dict[str, dict[str, str]] = {}
_js_catalogs: dict[str, dict[str, str]] = {}
_panel_name = "Pterodactyl"


def _load() -> None:
    for code in LANGUAGES:
        if code == DEFAULT:
            continue
        path = Path(__file__).parent / "locales" / f"{code}.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        _catalogs[code] = data.get("server", {})
        _js_catalogs[code] = data.get("browser", {})


_load()


def valid(code: str | None) -> str | None:
    return code if code in LANGUAGES else None


def set_language(code: str | None) -> None:
    _current.set(valid(code) or DEFAULT)


def use_default() -> None:
    """Für Hintergrundaufgaben ohne Benutzer: die eingestellte Standardsprache."""
    from . import db
    try:
        set_language(db.get_setting("default_language"))
    except Exception:
        set_language(DEFAULT)


def set_panel(name: str) -> None:
    """Name des angebundenen Panels für den Platzhalter {panel}."""
    global _panel_name
    _panel_name = name


def panel_name() -> str:
    return _panel_name


def current() -> str:
    return _current.get()


class Msg(str):
    """Deutscher Text mit Platzhaltern, der erst bei der Anzeige übersetzt wird.

    Für Meldungen, die im Hintergrund entstehen und später in der Sprache des Betrachters
    erscheinen: t(Msg("GitHub antwortet mit HTTP {code}", code=404)).
    """
    def __new__(cls, text: str, **values):
        obj = super().__new__(cls, text.format(**values) if values else text)
        obj.key, obj.values = text, values
        return obj


def t(text: str, **values) -> str:
    """Übersetzt einen deutschen Text in die aktuelle Sprache.

    Gleiche deutsche Wörter mit unterschiedlicher Bedeutung bekommen einen Kontext
    vor einem senkrechten Strich, z. B. t("Rolle|Benutzer"); angezeigt wird nur der Teil danach.
    """
    if not text:
        return text
    if isinstance(text, Msg):
        return t(text.key, **text.values, **values)
    lang = _current.get()
    found = _catalogs.get(lang, {}).get(text) if lang != DEFAULT else None
    if found is None:
        context, sep, rest = text.partition("|")
        found = rest if sep and context.isalpha() else text
    if "{panel}" in found and "panel" not in values:
        found = found.replace("{panel}", _panel_name)
    return found.format(**values) if values else found


def browser_catalog() -> dict[str, str]:
    """Übersetzungen für Texte, die im Browser (app.js) entstehen."""
    return _js_catalogs.get(_current.get(), {})


def from_accept_language(header: str | None) -> str | None:
    """Bevorzugte Sprache aus dem Accept-Language-Header des Browsers."""
    for part in (header or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if valid(code):
            return code
    return None


# -- Zahlen und Datum -----------------------------------------------------------

def decimal(value: str) -> str:
    """'1.5' -> '1,5' auf Deutsch, unverändert auf Englisch."""
    return value.replace(".", ",") if _current.get() == "de" else value


def date_format(seconds: bool = False) -> str:
    if _current.get() == "de":
        return "%d.%m.%Y, %H:%M:%S" if seconds else "%d.%m.%Y, %H:%M"
    return "%d %b %Y, %H:%M:%S" if seconds else "%d %b %Y, %H:%M"


def locale_tag() -> str:
    """Für Intl im Browser."""
    return "de-DE" if _current.get() == "de" else "en-GB"
