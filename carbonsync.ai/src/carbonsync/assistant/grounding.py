"""Checks that the figures in an answer come from the data the assistant read.

Every number written by the model is looked up in the tool results of the conversation, the
system prompt (data date, cap…) and the user's own questions. A number counts as found when a
source value rounds to it at the precision it is written with (or is within 0.5 %). Numbers
that are not found were computed or invented by the model: the UI shows them as "to check".

A calculation written out in the answer ("3 155 − 3 096 = 59 t") makes its result count as
found when its operands are found and its arithmetic is right (the Expert brief asks for this).

Small whole numbers (≤ 31: counts, months, days, list items), plain years and unit constants
(100, 1000, 365) are not checked.
"""
from __future__ import annotations

import bisect
import re
from collections.abc import Iterable
from typing import Any

# grouped thousands (FR "1 088,5", EN "1,088.5"), or a plain number ("222,3", "0.64", "3222")
_NUM = re.compile(
    r"(?<![\w.,\d])[-−+]?(?:\d{1,3}(?:[   ]\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)(?![\d])")
_LIST_MARK = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_SPACES = re.compile(r"[   ]")
REL_TOL = 0.005


def _candidates(token: str) -> tuple[list[float], int, bool]:
    """Possible values of a written number, its decimals and whether it had a thousands separator."""
    t = token.lstrip("+-−")
    grouped = bool(_SPACES.search(t))
    t = _SPACES.sub("", t)
    if "," in t and "." in t:
        dec_sep = "," if t.rfind(",") > t.rfind(".") else "."
        whole, _, frac = t.rpartition(dec_sep)
        whole = whole.replace("," if dec_sep == "." else ".", "")
        return [float(f"{whole}.{frac}")], len(frac), True
    for sep in (",", "."):
        if sep in t:
            parts = t.split(sep)
            if len(parts) > 2:  # 1,055,716 or 1.055.716
                return [float("".join(parts))], 0, True
            whole, frac = parts
            if len(frac) == 3 and not grouped:  # 1,088 → 1088 (EN) or 1.088 (FR): accept both
                return [float(whole + frac), float(f"{whole}.{frac}")], 0, True
            return [float(f"{whole}.{frac}")], len(frac), grouped
    return [float(t)], 0, grouped


def numbers(text: str) -> list[tuple[str, list[float], int, bool]]:
    """(written token, candidate values, decimals, grouped) for every number in a text."""
    out = []
    for m in _NUM.finditer(text or ""):
        tok = m.group(0)
        try:
            vals, dec, grouped = _candidates(tok)
        except ValueError:
            continue
        out.append((tok.strip(), vals, dec, grouped))
    return out


def _walk(obj: Any) -> Iterable[float]:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, int | float):
        yield float(obj)
    elif isinstance(obj, str):
        for _, vals, _, _ in numbers(obj):
            yield from vals
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list | tuple):
        for v in obj:
            yield from _walk(v)


def source_values(*objs: Any) -> list[float]:
    """Sorted absolute values found in tool results / texts, with fractions also as percentages."""
    vals: set[float] = set()
    for obj in objs:
        for v in _walk(obj):
            a = abs(v)
            vals.add(a)
            if a <= 1:
                vals.add(a * 100)
    return sorted(vals)


def _found(value: float, decimals: int, pool: list[float]) -> bool:
    tol = max(0.5 * 10 ** -decimals, abs(value) * REL_TOL) + 1e-9
    i = bisect.bisect_left(pool, value - tol)
    return i < len(pool) and pool[i] <= value + tol


_UNSIGNED = r"\d{1,3}(?:[   ]\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?"
# "3 155 t − 3 096 t = 59 t", "59 / 3 096 = 1,9 %": a calculation written out in the answer
_CALC = re.compile(rf"(?<![\w.,\d])({_UNSIGNED})[^\d=\n()]{{0,14}}?([+\-−–×x*/÷])\s*({_UNSIGNED})[^\d=\n()]{{0,14}}?"
                   rf"=\s*[-−]?({_UNSIGNED})(?![\d])")
CALC_TOL = 0.01
_CONSTANTS = {100.0, 1000.0, 365.0, 366.0}   # %, kg → t, days per year


def _operand(value: float, decimals: int, pool: list[float]) -> bool:
    """An operand is fine when it comes from the sources, is a small count (≤ 31) or a unit constant."""
    return _found(value, decimals, pool) or (decimals == 0 and (value <= 31 or value in _CONSTANTS))


def derived(answer: str, pool: list[float]) -> list[float]:
    """Results of calculations shown in the answer whose operands come from the sources and whose
    arithmetic is right (so the reader can check them). Chained calculations are followed."""
    found: list[float] = []
    for m in _CALC.finditer(answer or ""):
        try:
            (a_vals, a_dec, _), op, (b_vals, b_dec, _), (c_vals, _, _) = (
                _candidates(m.group(1)), m.group(2), _candidates(m.group(3)), _candidates(m.group(4)))
        except ValueError:
            continue
        known = sorted(pool + found)
        a_ok = [a for a in a_vals if _operand(a, a_dec, known)]
        b_ok = [b for b in b_vals if _operand(b, b_dec, known)]
        for a in a_ok:
            for b in b_ok:
                if op == "+":
                    results = [a + b]
                elif op in "-−–":
                    results = [abs(a - b)]
                elif op in "×x*":
                    results = [a * b]
                else:
                    results = [a / b, a / b * 100] if b else []
                for c in c_vals:
                    if any(abs(c - r) <= max(abs(r) * CALC_TOL, 0.5 * 10 ** -2) for r in results):
                        found.append(c)
    return found


def unverified(answer: str, pool: list[float]) -> list[str]:
    """Numbers of the answer that no source supports (as written, without duplicates). A result
    of a calculation written out in the answer counts as supported when its arithmetic is right."""
    text = _LIST_MARK.sub(" ", answer or "")
    extra = derived(text, pool)
    if extra:
        pool = sorted(pool + extra)
    out: list[str] = []
    for tok, vals, dec, grouped in numbers(text):
        a = abs(vals[0])
        if dec == 0 and len(vals) == 1 and (a <= 31 or (not grouped and (1900 <= a <= 2100 or a in _CONSTANTS))):
            continue
        if any(_found(abs(v), dec, pool) for v in vals):
            continue
        if tok not in out:
            out.append(tok)
    return out


def check(answer: str, turns: list[dict[str, Any]], current: dict[str, Any], system: str) -> list[str]:
    """Figures of ``answer`` not found in the conversation's tool results, questions or system prompt."""
    results = [r.get("result") for t in [*turns, current] for r in t.get("tools", []) if r.get("ok")]
    questions = [t.get("question", "") for t in [*turns, current]]
    return unverified(answer, source_values(results, questions, system))
