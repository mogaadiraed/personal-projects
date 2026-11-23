"""Reusable UI building blocks: page header, KPI tiles, status banner, cards."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape

import pandas as pd
import streamlit as st

from app.core.i18n import lang, tr
from app.ui.theme import CRIT, GOOD, WARN
from carbonsync.obs import capture


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
def page_header(eyebrow: str, title: str, subtitle: str = "", chips: list[tuple[str, str]] | None = None) -> None:
    """Title block with a language switch on the right.

    chips: list of (text, tone) where tone is "", "accent" or "warn".
    """
    left, right = st.columns([4.6, 2.4], vertical_alignment="top")
    with left:
        chip_html = "".join(
            f'<span class="cs-chip {tone}"><span class="dot"></span>{escape(text)}</span>'
            for text, tone in (chips or [])
        )
        st.html(
            f'<div class="cs-head">'
            f'<div class="cs-eyebrow">{escape(eyebrow)}</div>'
            f'<h1 class="cs-title">{escape(title)}</h1>'
            + (f'<div class="cs-sub">{escape(subtitle)}</div>' if subtitle else "")
            + (f'<div class="cs-chips">{chip_html}</div>' if chip_html else "")
            + "</div>"
        )
    with right:
        c_lang, c_user = st.columns([1, 1.25], gap="small", vertical_alignment="center")
        with c_lang:
            _language_switch()
        with c_user:
            from app.core import auth

            auth.account_menu()


def _language_switch() -> None:
    current = lang()
    choice = st.segmented_control(
        "Language",
        options=["fr", "en"],
        default=current,
        format_func=str.upper,
        label_visibility="collapsed",
        key=f"lang_switch_{current}",
    )
    if choice and choice != current:
        st.session_state["lang"] = choice
        st.rerun()


def data_chips(data_through: str, demo: bool, engine: str | None = None) -> list[tuple[str, str]]:
    chips = [(tr(f"Données au {data_through}", f"Data through {data_through}"), "")]
    if engine:
        chips.append((tr(f"Modèle {engine}", f"Model {engine}"), "accent"))
    if demo:
        chips.append((tr("Données synthétiques", "Synthetic data"), "warn"))
    return chips


# ---------------------------------------------------------------------------
# KPI tiles
# ---------------------------------------------------------------------------
@dataclass
class Kpi:
    label: str
    value: str
    unit: str = ""
    delta: str = ""
    tone: str = ""  # "good" | "bad" | ""
    note: str = ""
    kind: str = ""  # "" | "crit": the whole tile in dark red (an open critical alert)


def _is_text(value: str) -> bool:
    """A name (zone, item, model…) rather than a figure: shown smaller and allowed to wrap."""
    return len(value) > 10 and sum(ch.isalpha() for ch in value) > len(value) / 2


def kpis(items: list[Kpi]) -> None:
    tiles = []
    for k in items:
        foot = ""
        if k.delta or k.note:
            foot = (
                '<div class="cs-kpi-foot">'
                + (f'<span class="cs-delta {k.tone}">{escape(k.delta)}</span>' if k.delta else "")
                + (f'<span class="cs-kpi-note">{escape(k.note)}</span>' if k.note else "")
                + "</div>"
            )
        unit = f'<span class="cs-kpi-unit">{escape(k.unit)}</span>' if k.unit else ""
        value_cls = "cs-kpi-value text" if _is_text(k.value) else "cs-kpi-value"
        tiles.append(
            f'<div class="cs-kpi {escape(k.kind)}"><div class="cs-kpi-label">{escape(k.label)}</div>'
            f'<div class="{value_cls}" title="{escape(k.value)}">{escape(k.value)}{unit}</div>{foot}</div>'
        )
    st.html(f'<div class="cs-kpis">{"".join(tiles)}</div>')


# ---------------------------------------------------------------------------
# Status banner
# ---------------------------------------------------------------------------
STATUS_LABELS = {
    "green": ("Dans la cible", "On track"),
    "amber": ("À surveiller", "At risk"),
    "red": ("Dépassement", "Over budget"),
}
STATUS_COLORS = {"green": GOOD, "amber": WARN, "red": CRIT}


def status_label(level: str) -> str:
    fr, en = STATUS_LABELS[level]
    return tr(fr, en)


def status_banner(level: str, message: str, label: str | None = None) -> None:
    color = STATUS_COLORS[level]
    ink = "light-ink" if level != "amber" else ""
    st.html(
        f'<div class="cs-status" style="--c:{color}">'
        f'<span class="cs-status-badge {ink}">● {escape(label or status_label(level))}</span>'
        f'<span class="cs-status-text">{escape(message)}</span></div>'
    )


# ---------------------------------------------------------------------------
# Cards & sections
# ---------------------------------------------------------------------------
def card(title: str, subtitle: str = "", stretch: bool = False, align: str = "top"):
    """Bordered container with a compact title. Use as a context manager.

    ``stretch``: as tall as the row it sits in, so cards side by side end level.
    ``align="distribute"``: the last element (a button) sits at the bottom of a stretched card."""
    box = st.container(border=True, height="stretch" if stretch else "content", vertical_alignment=align)
    box.html(
        f'<div class="cs-card-head"><div class="cs-section">{escape(title)}</div>'
        + (f'<div class="cs-section-sub">{escape(subtitle)}</div>' if subtitle else "") + "</div>"
    )
    return box


def section(title: str, subtitle: str = "") -> None:
    """Section label outside a card."""
    st.html(f'<div class="cs-section">{escape(title)}</div>'
            + (f'<div class="cs-section-sub">{escape(subtitle)}</div>' if subtitle else ""))


def steps(items: list[tuple[str, str]]) -> None:
    """Horizontal pipeline stepper. items: (title, subtitle)."""
    html = "".join(
        f'<div class="cs-step"><div class="cs-step-n">{i:02d}</div>'
        f'<div class="cs-step-t">{escape(t)}</div><div class="cs-step-s">{escape(s)}</div></div>'
        for i, (t, s) in enumerate(items, 1)
    )
    st.html(f'<div class="cs-steps">{html}</div>')


def empty_state(message: str) -> None:
    st.info(message, icon=":material/info:")


def footer(factors_date: str) -> None:
    st.html(
        '<div class="cs-foot">'
        "<span>CarbonSync AI · v2.1</span>"
        f"<span>{escape(tr('Facteurs revus le', 'Factors reviewed'))} {escape(factors_date)}</span>"
        f"<span>{escape(tr('GHG Protocol · CSRD / ESRS E1', 'GHG Protocol · CSRD / ESRS E1'))}</span>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Live activity log (same lines as the terminal)
# ---------------------------------------------------------------------------
@contextmanager
def live_log(label: str, done_label: str, max_lines: int = 60) -> Iterator[list[str]]:
    """``st.status`` box that streams every ``carbonsync.*`` log line while the block runs.

    The status title follows the current step ("3/5 Rebuild daily table…").
    """
    status = st.status(label, expanded=True)
    with status:
        box = st.empty()
    lines: list[str] = []

    def on_record(record: logging.LogRecord, line: str) -> None:
        mark = "! " if record.levelno >= logging.WARNING else "  "
        lines.append(mark + line)
        shown = lines[-max_lines:]
        box.code("\n".join(shown), language=None, height=min(22 * len(shown) + 40, 420))
        msg = record.getMessage()
        if msg.endswith("…"):
            status.update(label=msg)

    try:
        with capture(on_record):
            yield lines
    except Exception as exc:
        status.update(label=f"{type(exc).__name__}: {exc}", state="error", expanded=True)
        raise
    status.update(label=done_label, state="complete", expanded=False)


def job_watch() -> None:
    """While a background pipeline runs: its live log, refreshed every second. The page reloads when it ends."""
    from app.core import jobs

    if not jobs.running():
        return

    @st.fragment(run_every=1.0)
    def _watch() -> None:
        job = jobs.snapshot()
        if not job.get("running"):
            st.rerun(scope="app")          # the run is over: every figure on the page is re-read
        status = st.status(job.get("step") or job.get("label") or tr("Pipeline en cours…", "Pipeline running…"),
                           expanded=True, state="running")
        lines = job.get("lines", [])[-60:]
        if lines:
            status.code("\n".join(lines), language=None, height=min(22 * len(lines) + 40, 420))
        status.caption(tr("Vous pouvez changer de page : le calcul continue et les pages se mettent à jour à la fin.",
                          "You can switch pages: the run carries on and every page updates when it ends."))

    _watch()


def forecast_freshness(run: dict, key: str) -> None:
    """Warns when the forecast shown was not made from the latest data (its first days would overlap real
    data), with a button that reruns the pipeline in the background. Shows the live run while one is going."""
    from app.core import auth, jobs
    from app.ui.format import date_label

    if jobs.running():
        job_watch()
        return
    if not run or not run.get("stale") or run.get("data_through") is None:
        return
    end, last = pd.Timestamp(run["end_date"]), pd.Timestamp(run["data_through"])
    if run.get("has_current"):     # an older run picked by hand on the Forecast page
        status_banner("amber", tr(
            f"Prévision plus ancienne, choisie sur la page Prévisions : calculée sur les données jusqu'au "
            f"{date_label(end)}. La prévision à jour part du {date_label(last + pd.Timedelta(days=1))}.",
            f"Older forecast, picked on the Forecast page: made from data up to {date_label(end)}. "
            f"The current forecast starts on {date_label(last + pd.Timedelta(days=1))}.",
        ), tr("Prévision ancienne", "Older forecast"))
        return
    status_banner("amber", tr(
        f"La prévision a été calculée sur les données jusqu'au {date_label(end)}, mais les données vont "
        f"maintenant jusqu'au {date_label(last)} : elle n'a pas été recalculée depuis. "
        "Mettez-la à jour pour prévoir à partir du jour suivant.",
        f"The forecast was made from data up to {date_label(end)}, but the data now run to {date_label(last)}: "
        "it has not been recomputed since. Update it to forecast from the next day.",
    ), tr("Prévision à mettre à jour", "Forecast out of date"))
    if auth.can("pipeline"):
        if st.button(tr("Mettre à jour la prévision", "Update the forecast"), type="primary",
                     icon=":material/refresh:", key=f"fresh_{key}"):
            jobs.start_pipeline("manual", tr("Mise à jour de la prévision…", "Updating the forecast…"))
            st.rerun()
    else:
        st.caption(tr("Un éditeur ou un administrateur peut la mettre à jour.",
                      "An editor or an administrator can update it."))


def chip_row(chips: list[tuple[str, str]]) -> None:
    """Inline chips (same style as the header). chips: (text, tone) with tone '' | 'accent' | 'warn'."""
    html = "".join(f'<span class="cs-chip {t}"><span class="dot"></span>{escape(x)}</span>' for x, t in chips)
    st.html(f'<div class="cs-chips" style="margin:0 0 .6rem 0">{html}</div>')


def dept_tile(name: str, last: str, state: str, note: str) -> None:
    """Department status tile. state: 'ok' | 'ahead' | 'behind' | 'none'."""
    color = {"ok": GOOD, "ahead": WARN, "behind": CRIT, "none": "rgba(128,128,128,.5)"}[state]
    st.html(
        f'<div class="cs-dept" style="--c:{color}"><div class="cs-dept-name">{escape(name)}</div>'
        f'<div class="cs-dept-last">{escape(last)}</div>'
        f'<div class="cs-dept-note"><span class="cs-dept-dot"></span>{escape(note)}</div></div>'
    )
