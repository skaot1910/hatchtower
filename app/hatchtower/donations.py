"""Donation button (e.g. PayPal) and a supporter list the owner keeps by hand.

A plain donation link tells Hatchtower nothing about who donated, so the owner records donations
themselves. What the public list shows (name, date, amount) is one setting for all entries;
without the name every entry reads "anonymous"."""
from __future__ import annotations

import re
from datetime import date

from . import db, i18n

FIELDS = {"name": "Name", "date": "Datum", "amount": "Betrag"}
DEFAULT_FIELDS = "name,date"
CURRENCIES = ("EUR", "USD", "CHF", "GBP")
SYMBOLS = {"EUR": "€", "USD": "$", "CHF": "CHF", "GBP": "£"}
PUBLIC_LIMIT = 20
URL_RE = re.compile(r"^https://[^\s/?#<>\"']+\.[^\s/?#<>\"']+(/[^\s<>\"']*)?$")
AMOUNT_RE = re.compile(r"^\d{1,7}([.,]\d{1,2})?$")


def clean_url(value: str) -> str | None:
    """Only https links (PayPal, paypal.me, …); None means invalid. Empty switches the button off."""
    value = value.strip()
    if not value:
        return ""
    return value if len(value) <= 300 and URL_RE.match(value) else None


def parse_amount(value: str) -> int | None:
    """'5', '5,50' or '5.5' -> cents. Raises ValueError for anything else."""
    value = value.strip()
    if not value:
        return None
    if not AMOUNT_RE.match(value):
        raise ValueError(value)
    whole, _, frac = value.replace(",", ".").partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def parse_day(value: str) -> str:
    """Date from <input type=date>; not in the future."""
    day = date.fromisoformat(value.strip())
    if day > date.today():
        raise ValueError(value)
    return day.isoformat()


def fmt_amount(cents: int | None, currency: str) -> str:
    if cents is None:
        return ""
    text = f"{cents // 100}" if cents % 100 == 0 else f"{cents / 100:.2f}"
    symbol = SYMBOLS.get(currency, currency)
    return f"{i18n.decimal(text)} {symbol}" if i18n.current() == "de" else (
        f"{symbol}{text}" if symbol in ("$", "£") else f"{text} {symbol}")


def fmt_day(value: str) -> str:
    day = date.fromisoformat(value)
    return day.strftime("%d.%m.%Y" if i18n.current() == "de" else "%d %b %Y")


def config() -> dict:
    url = clean_url(db.get_setting("donate_url")) or ""
    fields = [f for f in (db.get_setting("donate_fields", DEFAULT_FIELDS) or "").split(",") if f in FIELDS]
    app_set, status_set = db.get_setting("donate_in_app") == "1", db.get_setting("donate_on_status") == "1"
    # *_set: the checkbox as saved; in_app/on_status: actually shown (needs a link)
    return {"url": url, "text": db.get_setting("donate_text") or "", "app_set": app_set, "status_set": status_set,
            "in_app": bool(url) and app_set, "on_status": bool(url) and status_set,
            "history": db.get_setting("donate_history") == "1", "fields": fields}


def in_app() -> bool:
    """Heart in the sidebar (checked on every page, so only the two settings it needs)."""
    return db.get_setting("donate_in_app") == "1" and bool(clean_url(db.get_setting("donate_url")))


def entries(limit: int | None = None) -> list[dict]:
    query = "SELECT * FROM donations ORDER BY donated_on DESC, id DESC"
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(query + (f" LIMIT {int(limit)}" if limit else ""))]
    for r in rows:
        r["day_text"] = fmt_day(r["donated_on"])
        r["amount_text"] = fmt_amount(r["amount_cents"], r["currency"])
        r["amount_input"] = (i18n.decimal(f"{r['amount_cents'] / 100:.2f}") if r["amount_cents"] is not None else "")
    return rows


def public_entries(cfg: dict) -> list[dict]:
    """Only the chosen details leave the server; without "name" every donor is anonymous."""
    if not cfg["history"]:
        return []
    shown = set(cfg["fields"])
    result = []
    for r in entries(PUBLIC_LIMIT):
        item = {"name": (r["donor"] if "name" in shown else None) or i18n.t("Anonym")}
        if "date" in shown:
            item["day"] = r["day_text"]
        if "amount" in shown and r["amount_text"]:
            item["amount"] = r["amount_text"]
        result.append(item)
    return result


def totals() -> list[str]:
    """Sum per currency, for the owner only."""
    with db.connect() as con:
        rows = con.execute("SELECT currency, SUM(amount_cents) AS total FROM donations"
                           " WHERE amount_cents IS NOT NULL GROUP BY currency ORDER BY total DESC").fetchall()
    return [fmt_amount(r["total"], r["currency"]) for r in rows]
