"""Spiel-Logos: jedes Bild wird neu als WebP gerechnet, damit kein Fremdinhalt mitkommt."""
from __future__ import annotations

import io
import secrets
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .i18n import t

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SIDE = 256
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
# Wenige MB können ein riesiges Bild beschreiben; entpackt (RGBA) bräuchte es Hunderte MB Arbeitsspeicher
MAX_PIXELS_SIDE = 4096


class LogoError(ValueError):
    pass


def store_logo(data: bytes, logo_dir: Path, box: tuple[int, int] = (MAX_SIDE, MAX_SIDE)) -> str:
    """Speichert ein Bild als WebP, verkleinert auf höchstens box (Seitenverhältnis bleibt)."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise LogoError(t("Das Bild ist größer als 2 MB."))
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise LogoError(t("Erlaubt sind PNG, JPG und WebP."))
            if max(probe.size) > MAX_PIXELS_SIDE:
                raise LogoError(t("Das Bild ist zu groß (höchstens {n} × {n} Pixel).", n=MAX_PIXELS_SIDE))
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGBA")
            img.thumbnail(box)
            filename = f"{secrets.token_hex(16)}.webp"
            logo_dir.mkdir(parents=True, exist_ok=True)
            img.save(logo_dir / filename, "WEBP", quality=90)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise LogoError(t("Die Datei ist kein gültiges Bild.")) from exc
    return filename


def delete_logo(filename: str | None, logo_dir: Path) -> None:
    if filename and len(filename) == 37 and filename.endswith(".webp"):
        (logo_dir / filename).unlink(missing_ok=True)
