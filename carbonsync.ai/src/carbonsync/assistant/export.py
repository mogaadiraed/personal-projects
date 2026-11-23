"""Export an assistant conversation, or only its last answer (the action plan), to Markdown or PDF.

The PDF reuses the page and table helpers of the executive brief (``export/brief.py``,
matplotlib only). Each answer keeps its sources: tools read, data date, forecast run, engine,
figures the check did not find in the data, and the cost for cloud models.
"""
from __future__ import annotations

import functools
import io
import re
from datetime import datetime
from typing import Any

from matplotlib.backends.backend_pdf import PdfPages

from carbonsync.export import brief

_INLINE = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"__(.+?)__"), r"\1"), (re.compile(r"`([^`]+)`"), r"\1"),
           (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"), (re.compile(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)"), r"\1"),
           (re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)"), r"\1")]
ROW_H = 0.0172   # table row height on the page (same as the executive brief)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _t(lang: str, fr: str, en: str) -> str:
    return fr if lang == "fr" else en


@functools.cache
def _glyphs() -> frozenset[int]:
    """Characters the PDF font can draw: emoji and rare symbols are dropped instead of showing boxes."""
    from matplotlib import font_manager, ft2font

    return frozenset(ft2font.FT2Font(font_manager.findfont("DejaVu Sans")).get_charmap())


def _plain(text: str) -> str:
    for rx, rep in _INLINE:
        text = rx.sub(rep, text)
    ok = _glyphs()
    text = "".join(ch for ch in text if ord(ch) in ok)
    return re.sub(r" {2,}", " ", text).replace("$", r"\$").strip()   # "$" would start matplotlib math text


def _turns(chat: Any, last_only: bool) -> list[dict[str, Any]]:
    turns = [t for t in chat.turns if t.get("answer") or t.get("error")]
    return turns[-1:] if last_only else turns


def _sources(turn: dict[str, Any], lang: str) -> list[str]:
    out = []
    names = [str(t.get("label") or t.get("name")).rstrip("…").rstrip(".") for t in turn.get("tools") or []
             if t.get("ok", True)]
    if names:
        out.append(_t(lang, "Outils lus : ", "Tools read: ") + ", ".join(dict.fromkeys(str(n) for n in names)))
    if turn.get("data_until"):
        out.append(_t(lang, f"Données jusqu'au {turn['data_until']}", f"Data until {turn['data_until']}")
                   + (_t(lang, f" · prévision {turn['forecast_run']}", f" · forecast {turn['forecast_run']}")
                      if turn.get("forecast_run") else ""))
    engine = turn.get("label") or turn.get("provider") or ""
    if turn.get("served_by"):
        engine += _t(lang, f" (réponse de {turn['served_by']})", f" (answered by {turn['served_by']})")
    if engine:
        out.append(_t(lang, "Moteur : ", "Engine: ") + engine
                   + (_t(lang, " · données agrégées", " · aggregated data") if turn.get("privacy") == "aggregated" else ""))
    if turn.get("cost_usd"):
        out.append(_t(lang, f"Coût estimé : {turn['cost_usd']:.4f} $".replace(".", ","),
                      f"Estimated cost: ${turn['cost_usd']:.4f}"))
    if turn.get("unverified"):
        out.append(_t(lang, "Chiffres non retrouvés dans les données lues : ", "Figures not found in the data read: ")
                   + ", ".join(turn["unverified"][:12]))
    return out


def title(chat: Any, lang: str, last_only: bool) -> str:
    turns = _turns(chat, last_only)
    plan = last_only and turns and any(t.get("name") == "analyse" for t in turns[-1].get("tools") or [])
    if plan:
        return _t(lang, "Plan d'action — assistant CarbonSync", "Action plan — CarbonSync assistant")
    if last_only:
        return _t(lang, "Réponse de l'assistant CarbonSync", "CarbonSync assistant answer")
    return _t(lang, "Conversation avec l'assistant CarbonSync", "Conversation with the CarbonSync assistant")


def file_name(chat: Any, last_only: bool, ext: str) -> str:
    kind = "plan" if last_only else "conversation"
    return f"carbonsync_assistant_{kind}_{datetime.now():%Y%m%d_%H%M}.{ext}"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def to_markdown(chat: Any, lang: str = "fr", last_only: bool = False) -> str:
    turns = _turns(chat, last_only)
    lines = [f"# {title(chat, lang, last_only)}", "",
             _t(lang, f"Exporté le {datetime.now():%d/%m/%Y %H:%M} · {len(turns)} question(s)",
                f"Exported {datetime.now():%Y-%m-%d %H:%M} · {len(turns)} question(s)"), ""]
    for i, t in enumerate(turns, 1):
        lines += [f"## {i}. {t.get('question', '').strip()}", ""]
        if t.get("answer"):
            lines += [t["answer"].strip(), ""]
        if t.get("error"):
            lines += [f"> {_t(lang, 'Erreur', 'Error')} : {t['error']}", ""]
        src = _sources(t, lang)
        if src:
            lines += [f"*{_t(lang, 'Sources', 'Sources')}*", ""] + [f"- {s}" for s in src] + [""]
    lines.append(_t(lang, "---\nChiffres issus des données du tableau de bord CarbonSync AI ; l'assistant ne modifie "
                          "jamais les données.",
                    "---\nFigures from the CarbonSync AI dashboard data; the assistant never changes the data."))
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _table_block(doc: brief._Doc, rows: list[str]) -> None:
    cells = [[_plain(c) for c in r.strip().strip("|").split("|")] for r in rows if not _RULE.match(r)]
    if not cells:
        return
    width = max(len(r) for r in cells)
    cells = [r + [""] * (width - len(r)) for r in cells]
    header, body = cells[0], cells[1:] or [[""] * width]
    if width > 7:   # too wide for the page: keep it as text
        for r in cells:
            doc.para(" · ".join(c for c in r if c), size=7.6)
        return
    limit = max(10, 110 // width)
    body = [[c if len(c) <= limit else c[:limit - 1] + "…" for c in r] for r in body]
    header = [c if len(c) <= limit else c[:limit - 1] + "…" for c in header]
    longest = [max(len(r[k]) for r in [header, *body]) + 3 for k in range(width)]   # widths follow the text
    ax = doc.axes(ROW_H * (len(body) + 1))
    brief._table(ax, header, body, [n / sum(longest) for n in longest])


def _markdown_to_pdf(doc: brief._Doc, text: str) -> None:
    table: list[str] = []
    for raw in (text or "").splitlines() + [""]:
        line = raw.rstrip()
        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        if table:
            _table_block(doc, table)
            table = []
        if not line.strip():
            doc.y -= 0.004
            continue
        if line.lstrip().startswith("#"):
            doc.heading(_plain(line.lstrip("# ")), size=9.4)
        elif _RULE.match(line) or line.strip() in ("---", "***"):
            doc.y -= 0.006
        elif _BULLET.match(line):
            indent = len(line) - len(line.lstrip())
            doc.para(_plain(_BULLET.sub("", line)), bullet="–" if indent else "•", width=106 if indent else 110)
        elif line.lstrip().startswith(">"):
            doc.para(_plain(line.lstrip("> ")), color=brief.MUTED)
        else:
            doc.para(_plain(line))


def to_pdf(chat: Any, lang: str = "fr", last_only: bool = False) -> bytes:
    turns = _turns(chat, last_only)
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        doc = brief._Doc(pdf, title(chat, lang, last_only),
                         _t(lang, f"Exporté le {datetime.now():%d/%m/%Y %H:%M}", f"Exported {datetime.now():%Y-%m-%d %H:%M}"),
                         _t(lang, "CarbonSync AI · chiffres issus des données du tableau de bord · l'assistant ne "
                                  "modifie jamais les données",
                            "CarbonSync AI · figures from the dashboard data · the assistant never changes the data"))
        doc.new_page()
        if not turns:
            doc.para(_t(lang, "Aucune réponse dans cette conversation.", "No answer in this conversation."))
        for i, t in enumerate(turns, 1):
            doc.need(0.08)
            doc.heading(f"{i}. {_plain(t.get('question', ''))}"[:160])
            if t.get("answer"):
                _markdown_to_pdf(doc, t["answer"])
            if t.get("error"):
                doc.para(_plain(f"{_t(lang, 'Erreur', 'Error')} : {t['error']}"), color=brief.STATUS["red"])
            for s in _sources(t, lang):
                doc.para(_plain(s), size=6.8, color=brief.MUTED, bullet="·")
            doc.y -= 0.014
        doc.close_page()
    return buf.getvalue()
