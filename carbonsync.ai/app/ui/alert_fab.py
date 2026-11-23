"""Floating alert button, stacked above the AI button: red when a critical alert is open, amber for warnings.

A click opens a small panel with the open alerts (critical ones on a dark red background) and two shortcuts to
the Alerts page: every alert, or the critical ones only. Not shown on the Alerts page itself.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from app.core.i18n import lang, tr
from app.data.loader import load_alerts_state

ORDER = {"critical": 0, "warning": 1, "info": 2}
SHOWN = 6
CRIT_BG, CRIT_LINE, CRIT_EDGE = "#7f1d1d", "#991b1b", "#ef4444"   # white text on #7f1d1d: 9.9:1


def _css(side: str, bottom: int, tone: str, count: int) -> str:
    other = "left" if side == "right" else "right"
    offset = 33                                    # centred above the 60 px AI button (28 + 5)
    fill = {
        "crit": "background: radial-gradient(circle at 30% 25%, #f87171, #dc2626 52%, #7f1d1d); color: #fff;",
        "warn": "background: radial-gradient(circle at 30% 25%, #fde68a, #f59e0b 52%, #b45309); color: #1f1300;",
        "calm": "background: var(--cs-panel); color: var(--cs-accent); border: 1px solid var(--cs-line) !important;",
    }[tone]
    ring = {"crit": "rgba(220,38,38,.6)", "warn": "rgba(245,158,11,.55)"}.get(tone)
    badge = "" if not count else f"""
.st-key-cs_afab [data-testid="stPopoverButton"]::before {{
  content: "{count}"; position: absolute; top: -4px; {side}: -4px; min-width: 20px; height: 20px; padding: 0 5px;
  border-radius: 10px; background: #fff; color: {"#b91c1c" if tone == "crit" else "#7c2d12"}; font-size: .72rem;
  font-weight: 750; line-height: 20px; text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,.35); z-index: 1;
}}"""
    pulse = "" if not ring else f"""
.st-key-cs_afab [data-testid="stPopoverButton"]::after {{
  content: ""; position: absolute; inset: -5px; border-radius: 50%; border: 2px solid {ring};
  animation: csPulse 2.2s ease-out infinite; pointer-events: none;
}}
@media (prefers-reduced-motion: reduce) {{ .st-key-cs_afab [data-testid="stPopoverButton"]::after {{ display: none; }} }}"""
    return f"""<style>
.st-key-cs_afab {{ position: fixed; {side}: {offset}px; {other}: auto; bottom: {bottom}px; z-index: 999985;
  width: 50px !important; }}
.st-key-cs_afab [data-testid="stPopover"] {{ width: 50px; }}
.st-key-cs_afab [data-testid="stPopoverButton"] {{
  position: relative; width: 50px; height: 50px; min-height: 50px; padding: 0; border-radius: 50%; border: 0;
  {fill} box-shadow: 0 8px 22px rgba(0,0,0,.30), 0 2px 5px rgba(0,0,0,.25); transition: transform .15s ease;
  justify-content: center;
}}
.st-key-cs_afab [data-testid="stPopoverButton"]:hover, .st-key-cs_afab [data-testid="stPopoverButton"]:focus {{
  transform: scale(1.07); {fill}
}}
.st-key-cs_afab [data-testid="stPopoverButton"] > div {{ margin: 0; gap: 0; justify-content: center; }}
.st-key-cs_afab [data-testid="stPopoverButton"] > div > div[aria-hidden="true"],
.st-key-cs_afab [data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"] {{ display: none; }}
.st-key-cs_afab [data-testid="stPopoverButton"] [data-testid="stIconMaterial"] {{ font-size: 26px; color: inherit; }}
{badge}{pulse}
@keyframes csPulse {{ 0% {{ transform: scale(.92); opacity: .9; }} 100% {{ transform: scale(1.3); opacity: 0; }} }}
/* the AI panel uses the same corner: the alert button steps aside while it is open */
body:has(.st-key-cs_dock) .st-key-cs_afab {{ display: none; }}
@media (max-width: 640px) {{ .st-key-cs_afab {{ {side}: 21px; bottom: {bottom - 14 if bottom > 28 else 16}px; }} }}
/* panel */
.cs-ap-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 4px; }}
.cs-ap-title {{ font-weight: 700; font-size: 1rem; }}
.cs-ap-counts {{ font-size: .74rem; opacity: .75; white-space: nowrap; }}
.cs-ap-list {{ display: flex; flex-direction: column; gap: 6px; width: min(360px, 82vw); }}
.cs-ap-item {{ border-radius: 10px; padding: 8px 10px 8px 12px; border-left: 5px solid var(--cs-line);
  background: var(--cs-fill-2); }}
.cs-ap-item .t {{ font-weight: 650; font-size: .86rem; line-height: 1.25; }}
.cs-ap-item .d {{ font-size: .76rem; opacity: .85; line-height: 1.3; margin-top: 2px; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
.cs-ap-item.critical {{ background: {CRIT_BG}; border-left-color: {CRIT_EDGE}; color: #fff; }}
.cs-ap-item.warning {{ background: rgba(250,178,25,.16); border-left-color: #f59e0b; }}
.cs-ap-more {{ font-size: .74rem; opacity: .7; }}
</style>"""


def _sorted_alerts() -> list[dict]:
    alerts = load_alerts_state().get("alerts", [])
    return sorted(alerts, key=lambda a: ORDER.get(a.get("severity"), 3))


def _go(alerts_page, severity: str) -> None:
    st.session_state["al_filter"] = severity
    st.switch_page(alerts_page)


def render(page_path: str, alerts_page=None, side: str = "right", stacked: bool = True) -> None:
    """Call once per run, after the page. ``stacked``: the AI button is shown below it on this page."""
    if page_path == "alerts":
        return
    alerts = _sorted_alerts()
    crit = sum(a.get("severity") == "critical" for a in alerts)
    warn = sum(a.get("severity") == "warning" for a in alerts)
    info = len(alerts) - crit - warn
    tone = "crit" if crit else "warn" if warn else "calm"
    st.html(_css(side, 102 if stacked else 28, tone, crit + warn))
    if crit:
        tip = tr(f"{crit} alerte(s) critique(s) : cliquer pour voir", f"{crit} critical alert(s): click to see")
    elif warn:
        tip = tr(f"{warn} alerte(s) à surveiller", f"{warn} warning(s)")
    else:
        tip = tr("Alertes : rien de critique", "Alerts: nothing critical")
    with st.container(key="cs_afab"), st.popover(
            " ", icon=":material/notifications_active:" if crit + warn else ":material/notifications:", help=tip,
            key="cs_afab_pop"):
        _panel(alerts, crit, warn, info, alerts_page)


def _panel(alerts: list[dict], crit: int, warn: int, info: int, alerts_page) -> None:
    L = lang()
    counts = tr(f"{crit} critique(s) · {warn} à surveiller · {info} info",
                f"{crit} critical · {warn} warning · {info} info")
    items = "".join(
        f'<div class="cs-ap-item {escape(a.get("severity", "info"))}"><div class="t">{escape(a.get("title_" + L, ""))}</div>'
        f'<div class="d">{escape(a.get("detail_" + L, ""))}</div></div>'
        for a in alerts[:SHOWN]
    ) or f'<div class="cs-ap-more">{escape(tr("Aucune alerte ouverte.", "No open alert."))}</div>'
    more = len(alerts) - SHOWN
    st.html(f'<div class="cs-ap-head"><span class="cs-ap-title">{escape(tr("Alertes", "Alerts"))}</span>'
            f'<span class="cs-ap-counts">{escape(counts)}</span></div><div class="cs-ap-list">{items}</div>'
            + (f'<div class="cs-ap-more">{escape(tr(f"+ {more} autre(s)", f"+ {more} more"))}</div>' if more > 0 else ""))
    if alerts_page is None:
        return
    if crit and st.button(tr("Voir les alertes critiques", "See the critical alerts"), icon=":material/error:",
                          type="primary", key="afab_crit", width="stretch"):
        _go(alerts_page, "critical")
    if st.button(tr("Ouvrir la page Alertes", "Open the Alerts page"), icon=":material/arrow_forward:",
                 key="afab_all", width="stretch"):
        _go(alerts_page, "all")
