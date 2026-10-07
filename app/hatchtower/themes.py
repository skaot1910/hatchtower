"""Farbthemes. Die Farben selbst stehen in style.css, hier nur Namen und Symbole.

Umgeschaltet wird oben rechts in der Kopfzeile, gespeichert pro Benutzer.
"""
from __future__ import annotations

THEMES = [
    {"key": "nacht", "name": "Dunkel", "icon": "☾", "description": "Schwarz mit Lila – der Standard."},
    {"key": "mittel", "name": "Mittel", "icon": "◐", "description": "Grautöne mit gedecktem Petrol."},
    {"key": "hell", "name": "Hell", "icon": "☀", "description": "Hell und warm, ohne grelles Weiß."},
]

KEYS = {t["key"] for t in THEMES}
DEFAULT = "nacht"


def valid(key: str | None) -> str | None:
    return key if key in KEYS else None
