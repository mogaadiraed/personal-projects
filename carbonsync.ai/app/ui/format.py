"""Locale-aware number formatting (fr: 1 234,5 · en: 1,234.5)."""
from __future__ import annotations

import calendar

from app.core.i18n import lang

_MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_MONTHS_FR_LONG = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
                   "Septembre", "Octobre", "Novembre", "Décembre"]


def num(val: float | None, decimals: int = 1) -> str:
    if val is None:
        return "—"
    s = f"{val:,.{decimals}f}"
    if lang() == "fr":
        s = s.replace(",", " ").replace(".", ",")
    return s


def pct(val: float | None, decimals: int = 1, signed: bool = False) -> str:
    if val is None:
        return "—"
    sign = "+" if signed and val > 0 else ""
    body = num(val, decimals)
    return f"{sign}{body} %" if lang() == "fr" else f"{sign}{body}%"


def signed(val: float, decimals: int = 1) -> str:
    return ("+" if val > 0 else "") + num(val, decimals)


def month_short(m: int) -> str:
    return _MONTHS_FR[m - 1] if lang() == "fr" else calendar.month_abbr[m]


def month_long(m: int) -> str:
    return _MONTHS_FR_LONG[m - 1] if lang() == "fr" else calendar.month_name[m]


def date_label(ts) -> str:
    return f"{ts.day} {month_short(ts.month)} {ts.year}"
