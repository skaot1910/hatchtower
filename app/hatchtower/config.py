"""Konfiguration von Hatchtower.

Die Umgebungsdatei enthält nur, was die Installation selbst braucht (interne
Schlüssel, Datenordner). Alles andere - Panel-Adresse, API-Keys, Modus - wird in
der Oberfläche eingestellt und verschlüsselt in der Datenbank gespeichert.

Welches Pterodactyl angebunden ist und wie es bezeichnet wird, wird immer
ausdrücklich eingetragen und nie automatisch erraten.
"""
from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _req(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Umgebungsvariable {name} fehlt")
    return value


@dataclass
class Settings:
    # Aus der Umgebungsdatei (fest für die Installation)
    secret_key: str
    data_key: str
    data_dir: Path
    # Aus der Datenbank (in der Oberfläche änderbar)
    pterodactyl_url: str = ""
    app_key: str = ""
    client_key: str = ""
    manager_mode: str = "read-only"
    trusted_proxies: tuple = ()
    timezone: str = "Europe/Berlin"
    default_timezone: str = "Europe/Berlin"
    node_overrides: dict = field(default_factory=dict)
    poll_interval: int = 15
    sync_interval: int = 300
    session_idle_minutes: int = 30
    session_max_hours: int = 12

    @property
    def read_only(self) -> bool:
        return self.manager_mode != "read-write"

    @property
    def configured(self) -> bool:
        return bool(self.pterodactyl_url and self.app_key and self.client_key)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hatchtower.db"

    @property
    def logo_dir(self) -> Path:
        return self.data_dir / "logos"



# Schlüssel in der Tabelle app_settings
DB_KEYS = {
    "panel_url": "conn_panel_url",
    "app_key": "conn_app_key",          # verschlüsselt
    "client_key": "conn_client_key",    # verschlüsselt
    "mode": "conn_mode",
    "trusted_proxies": "conn_trusted_proxies",
    "timezone": "conn_timezone",
    "node_overrides": "conn_node_overrides",
}


def _valid_tz(name: str) -> str:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(name.strip())
        return name.strip()
    except Exception:
        return "Europe/Berlin"


def load_settings() -> Settings:
    return Settings(
        secret_key=_req("HATCHTOWER_SECRET_KEY"),
        data_key=_req("HATCHTOWER_DATA_KEY"),
        data_dir=Path(os.environ.get("HATCHTOWER_DATA_DIR", "").strip() or "/var/lib/hatchtower"),
        default_timezone=_valid_tz(os.environ.get("HATCHTOWER_TZ", "")),
    )


def parse_proxies(text: str) -> tuple:
    result = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            result.append(ipaddress.ip_address(part))
    return tuple(result)


def apply_db_values(settings: Settings, values: dict[str, str], decrypt) -> None:
    """Übernimmt die in der Oberfläche gespeicherten Werte in die laufenden Einstellungen."""
    settings.pterodactyl_url = (values.get(DB_KEYS["panel_url"]) or "").rstrip("/")
    settings.app_key = decrypt(values.get(DB_KEYS["app_key"])) or ""
    settings.client_key = decrypt(values.get(DB_KEYS["client_key"])) or ""
    settings.manager_mode = "read-write" if values.get(DB_KEYS["mode"]) == "read-write" else "read-only"
    try:
        settings.trusted_proxies = parse_proxies(values.get(DB_KEYS["trusted_proxies"]) or "")
    except ValueError:
        settings.trusted_proxies = ()
    settings.timezone = values.get(DB_KEYS["timezone"]) or settings.default_timezone
    try:
        overrides = json.loads(values.get(DB_KEYS["node_overrides"]) or "{}")
        settings.node_overrides = {str(k): str(v) for k, v in overrides.items() if v}
    except (ValueError, AttributeError):
        settings.node_overrides = {}
