"""Every key in locales/en.json ("server") must still appear somewhere in the code.

When a German text changes, the old key stays behind unnoticed. This test collects all
string constants (incl. f-string pieces) from the Python code via AST plus the templates
and app.js, with whitespace normalised, and reports keys that appear nowhere.
"""
import ast
import json
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app" / "hatchtower"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def unused_server_keys() -> list[str]:
    consts = set()
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                consts.add(_norm(node.value))
    blob = "\n".join(_norm(p.read_text(encoding="utf-8"))
                     for pattern in ("*.html", "*.js", "*.txt") for p in APP.rglob(pattern))
    catalog = json.loads((APP / "locales" / "en.json").read_text(encoding="utf-8"))["server"]
    return [k for k in catalog if _norm(k) not in consts and _norm(k) not in blob]


def test_no_unused_server_translations():
    unused = unused_server_keys()
    assert not unused, f"{len(unused)} unused keys in locales/en.json: {unused[:10]}"
