"""Colour themes. The colours live in style.css; this file only holds names and icon names (see _icons.html).

Switched in the sidebar and stored per user. There are exactly two: dark and light.
"""
from __future__ import annotations

THEMES = [
    {"key": "nacht", "name": "Dunkel", "icon": "moon", "description": "Obsidian und Anthrazit mit Bordeaux – der Standard."},
    {"key": "hell", "name": "Hell", "icon": "sun", "description": "Warmes Off-White mit Bordeaux."},
]

KEYS = {t["key"] for t in THEMES}
DEFAULT = "nacht"
# Themes that no longer exist, still stored for some users or as instance default
ALIASES = {"mittel": "nacht"}


def valid(key: str | None) -> str | None:
    key = ALIASES.get(key, key) if key else key
    return key if key in KEYS else None
