"""Unterstützte Game-Server-Panels.

Hatchtower hängt an genau einem Panel. Pterodactyl und Pelican (ein Fork von Pterodactyl) haben dieselbe
API und laufen über PteroClient, Calagopus hat eine eigene API und einen eigenen Adapter.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT = "pterodactyl"


@dataclass(frozen=True)
class Panel:
    key: str
    name: str
    website: str
    app_prefix: str          # Präfix des Application-Keys ("" = keine Prüfung)
    client_prefix: str       # Präfix des Client-Keys ("" = keine Prüfung)
    single_key: bool         # nur ein API-Key (mit Admin- und Server-Rechten)
    tested: str              # mit diesen Versionen getestet


PANELS: dict[str, Panel] = {
    "pterodactyl": Panel("pterodactyl", "Pterodactyl", "https://pterodactyl.io", "ptla_", "ptlc_", False, "Panel 1.15.1 · Wings 1.13.3"),
    "pelican": Panel("pelican", "Pelican", "https://pelican.dev", "papp_", "pacc_", False, "Panel/Wings 1.0.0-rc.1"),
    "calagopus": Panel("calagopus", "Calagopus", "https://calagopus.com", "", "", True, "Panel/Wings 1.2.5"),
}


def get(key: str | None) -> Panel:
    return PANELS.get(key or "", PANELS[DEFAULT])


def valid(key: str | None) -> str:
    return key if key in PANELS else DEFAULT


def make_client(settings):
    """Passenden API-Adapter für das eingestellte Panel erzeugen."""
    if settings.panel_type == "calagopus":
        from .calagopus import CalagopusClient
        return CalagopusClient(settings)
    from .ptero import PteroClient
    return PteroClient(settings)
