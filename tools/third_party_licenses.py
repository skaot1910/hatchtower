"""Erzeugt THIRD_PARTY_LICENSES.txt aus den installierten Python-Paketen.

Aufruf (mit der Python-Umgebung von Hatchtower):
    /opt/hatchtower/venv/bin/python tools/third_party_licenses.py > THIRD_PARTY_LICENSES.txt

Für jedes Paket: Name, Version, Lizenz, Projektseite und der mitgelieferte Lizenztext.
"""
from __future__ import annotations

import sys
from importlib import metadata

SKIP = {"pip", "setuptools", "wheel", "hatchtower"}
LICENSE_HINTS = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")


def license_name(meta) -> str:
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    if classifiers:
        return ", ".join(classifiers)
    return (meta.get("License") or "unbekannt").split("\n")[0][:120]


def license_texts(dist) -> list[tuple[str, str]]:
    texts = []
    for f in dist.files or []:
        name = f.name.upper()
        if any(name.startswith(h) for h in LICENSE_HINTS):
            try:
                texts.append((str(f), f.locate().read_text(encoding="utf-8", errors="replace")))
            except (OSError, FileNotFoundError):
                continue
    return texts


def main() -> None:
    out = sys.stdout
    out.write("Hatchtower – Lizenzen der verwendeten Open-Source-Software\n")
    out.write("Third-party licenses of the open source software used by Hatchtower\n")
    out.write("=" * 78 + "\n\n")
    out.write("Pterodactyl Panel/Wings (MIT) wird nur über die API angebunden und ist nicht Teil von Hatchtower.\n\n")
    dists = sorted((d for d in metadata.distributions() if (d.metadata.get("Name") or "").lower() not in SKIP),
                   key=lambda d: d.metadata["Name"].lower())
    for d in dists:
        meta = d.metadata
        out.write("-" * 78 + "\n")
        out.write(f"{meta['Name']} {d.version}\nLizenz/License: {license_name(meta)}\n")
        home = meta.get("Home-page") or next((u.split(",", 1)[1].strip() for u in (meta.get_all("Project-URL") or [])), "")
        if home:
            out.write(f"{home}\n")
        for path, text in license_texts(d):
            out.write(f"\n[{path}]\n{text.strip()}\n")
        out.write("\n")


if __name__ == "__main__":
    main()
