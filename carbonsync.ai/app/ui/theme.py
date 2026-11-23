"""Global CSS for CarbonSync AI.

Native widget colours come from ``.streamlit/config.toml`` (written from the Settings by
``app.ui.appearance``). This stylesheet adds the look chosen there:

* **Verre** (glass): soft palette glows behind the page (slowly drifting when the animated background is
  on), translucent cards and
  KPI tiles with a light edge, a frosted top bar and assistant panel (``backdrop-filter``);
* **Plat** (flat): plain surfaces with thin borders;
* density, corners, animations and page width;
* the custom HTML components (KPI tiles, chips, status banner, stepper, lists).

Cards are the bordered containers made by ``components.card`` (they carry a ``cs-card-head``
title) and the department tiles; CSS finds them with ``:has()``.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.ui import appearance as ap

# Fixed status colours (identical in both modes, always paired with a label)
GOOD = "#0ca30c"
WARN = "#fab219"
CRIT = "#d03b3b"
ACCENT = "#1baf7a"   # brand green (charts keep entity colours; the UI accent follows the palette)

CARD = ('[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] :is(.cs-card-head, .cs-dept))')

# Glass wallpaper: soft palette glows on two fixed layers behind the page (``isolation`` keeps them under the
# content). Animated, they drift slowly (transform only, so the GPU does it); the glow strength stays the one
# the contrast checks use, so text over them stays readable.
WALLPAPER = """
.stApp { isolation: isolate; }
.stApp::before, .stApp::after {
  content: ""; position: fixed; inset: -18%; z-index: -1; pointer-events: none;
}
.stApp::before {
  background: radial-gradient(38% 34% at 22% 14%, var(--cs-glow-1), transparent 70%),
              radial-gradient(34% 30% at 78% 88%, var(--cs-glow-3), transparent 70%);
}
.stApp::after {
  background: radial-gradient(36% 32% at 86% 12%, var(--cs-glow-2), transparent 70%),
              radial-gradient(30% 28% at 12% 82%, var(--cs-glow-2), transparent 72%);
}
"""
WALLPAPER_MOTION = """
.stApp::before, .stApp::after { will-change: transform; }
.stApp::before { animation: csDriftA 34s ease-in-out infinite alternate; }
.stApp::after { animation: csDriftB 42s ease-in-out infinite alternate; }
@keyframes csDriftA {
  0% { transform: translate3d(0, 0, 0) scale(1); }
  50% { transform: translate3d(7%, 6%, 0) scale(1.08); }
  100% { transform: translate3d(-5%, 9%, 0) scale(1.03); }
}
@keyframes csDriftB {
  0% { transform: translate3d(0, 0, 0) scale(1.04); }
  50% { transform: translate3d(-8%, 5%, 0) scale(1); }
  100% { transform: translate3d(-3%, -7%, 0) scale(1.1); }
}
"""


def _vars(look: dict[str, Any], kind: str) -> str:
    a, pal = look["appearance"], ap.palette(look)
    p, g = pal[kind], ap.GLASS[kind]
    glass = a["style"] == "glass"
    g1, g2 = pal["glow"][kind]
    dark = kind == "dark"
    ink = "255,255,255" if dark else "15,23,42"
    v = {
        "--cs-accent": p["primary"],
        "--cs-accent-soft": ap.rgba(p["primary"], 0.16 if dark else 0.11),
        "--cs-accent-line": ap.rgba(p["primary"], 0.45),
        "--cs-line": f"rgba({ink},{0.09 if dark else 0.10})",
        "--cs-fill": f"rgba({ink},{0.035 if dark else 0.03})",
        "--cs-fill-2": f"rgba({ink},{0.07 if dark else 0.06})",
        "--cs-bg": p["bg"],
        "--cs-panel": ap.rgba(p["bg"], 0.86) if glass else p["bg"],
        "--cs-panel-strip": ap.rgba(p["bg2"], 0.85) if glass else p["bg2"],
    }
    if glass:
        v.update({
            "--cs-surface": ap.rgba(p["card"], g["card"]),
            "--cs-header": ap.rgba(p["bg"], g["header"]),
            "--cs-edge": f"inset 0 1px 0 rgba(255,255,255,{0.06 if dark else 0.85})",
            "--cs-shadow": "0 10px 30px rgba(0,0,0,.28)" if dark else "0 8px 24px rgba(15,23,42,.07)",
            "--cs-page": p["bg"],
            # Wallpaper glows (drawn on two fixed layers behind the page, see WALLPAPER)
            "--cs-glow-1": ap.rgba(g1, g["glow"]),
            "--cs-glow-2": ap.rgba(g2, g["glow2"]),
            "--cs-glow-3": ap.rgba(g1, round(g["glow2"] * 0.7, 3)),
        })
    else:
        v.update({"--cs-surface": "transparent", "--cs-header": p["bg"], "--cs-edge": "0 0 0 transparent",
                  "--cs-shadow": "none", "--cs-page": p["bg"]})
    return ";".join(f"{k}:{val}" for k, val in v.items())


def css(look: dict[str, Any], kind: str | None) -> str:
    a, lay = look["appearance"], look["layout"]
    glass = a["style"] == "glass"
    compact = a["density"] == "compact"
    sharp = a["corners"] == "sharp"
    r, r_sm, r_xs = ("6px", "5px", "4px") if sharp else ("16px", "12px", "8px")
    pad = ".7rem .85rem" if compact else "1rem 1.1rem"
    gap = ".6rem" if compact else "1rem"
    width = "100%" if lay["page_width"] == "full" else "1360px"
    if kind in ("light", "dark"):
        root = f":root{{{_vars(look, kind)}}}"
    else:   # mode follows the OS until the browser reports it
        root = (f":root{{{_vars(look, 'dark')}}}"
                f"@media (prefers-color-scheme: light){{:root{{{_vars(look, 'light')}}}}}")
    blur = "backdrop-filter: blur(16px) saturate(140%); -webkit-backdrop-filter: blur(16px) saturate(140%);" if glass else ""
    motion = ("" if a["animations"] else
              "*, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }")
    rise = (f"""@keyframes csRise {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}
.cs-kpi, .cs-status, {CARD} {{ animation: csRise .32s ease-out both; }}
.cs-kpi {{ transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }}
.cs-kpi:hover {{ transform: translateY(-1px); border-color: var(--cs-accent-line); }}
@media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation: none !important; transition: none !important; }} }}"""
            if a["animations"] else "")
    wallpaper = (WALLPAPER + (WALLPAPER_MOTION if a["animations"] and a["animated_background"] else "")) if glass else ""
    return f"""
<style>
{root}
.stApp {{ background: var(--cs-page) !important; }}
{wallpaper}

/* ---------- Layout ---------- */
[data-testid="stMainBlockContainer"], .block-container {{
  max-width: {width} !important;
  padding: {"4.2rem 1.6rem 2.4rem" if compact else "4.6rem 2rem 3rem"} !important;
}}
@media (max-width: 640px) {{
  [data-testid="stMainBlockContainer"], .block-container {{ padding: 4.2rem 1rem 2rem !important; }}
}}
{"[data-testid='stVerticalBlock'] { gap: " + gap + " !important; }" if compact else ""}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none !important; }}
/* Top bar stays above the page; frosted in the glass style, so scrolled content never runs under the tab names */
header[data-testid="stHeader"] {{
  background: var(--cs-header) !important; {blur}
  border-bottom: 1px solid var(--cs-line);
  z-index: 999990;
}}
[data-testid="stTopNavLink"], [data-testid="stTopNavSection"], [data-testid="stTopNavDropdownLink"] {{ border-radius: {r_xs}; }}
[data-testid="stTopNavLink"][aria-current="page"], [data-testid="stTopNavDropdownLink"][aria-current="page"] {{
  background: var(--cs-accent-soft) !important; box-shadow: inset 0 -2px 0 var(--cs-accent);
}}
[data-testid="stTopNavLink"][aria-current="page"] *, [data-testid="stTopNavDropdownLink"][aria-current="page"] * {{ font-weight: 650; }}

h1, h2, h3 {{ letter-spacing: -0.015em; }}
[data-testid="stHeadingWithActionElements"] a {{ display: none !important; }} /* hide anchor icons */

/* ---------- Cards ---------- */
{CARD} {{
  background: var(--cs-surface) !important;
  border-color: var(--cs-line) !important;
  border-radius: {r} !important;
  box-shadow: var(--cs-edge), var(--cs-shadow);
  padding: {pad} !important;
}}
.cs-card-head {{ margin-bottom: 2px; }}
[data-testid="stExpander"] details {{ border-radius: {r_sm} !important; background: var(--cs-surface); }}

/* ---------- Tabs: pill on the active tab ---------- */
[data-testid="stTabs"] [role="tablist"] {{ gap: 4px; }}
[data-testid="stTab"] {{ padding: 6px 12px !important; margin: 0 !important; border-radius: {r_xs} {r_xs} 0 0; font-weight: 550; }}
[data-testid="stTab"]:hover {{ background: var(--cs-fill-2); }}
[data-testid="stTab"][aria-selected="true"] {{ background: var(--cs-accent-soft); }}
[data-testid="stTab"][aria-selected="true"] * {{ font-weight: 650; }}

/* Metric fallback (native st.metric) */
[data-testid="stMetricValue"] {{ font-weight: 650; letter-spacing: -0.02em; }}

/* ---------- Page header ---------- */
.cs-head {{ margin: 0 0 {".7rem" if compact else "1.1rem"} 0; }}
.cs-eyebrow {{
  font-size: .72rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--cs-accent); margin-bottom: .25rem;
}}
.cs-title {{ font-size: 1.65rem; font-weight: 650; letter-spacing: -0.02em; line-height: 1.2; margin: 0; }}
.cs-sub {{ font-size: .92rem; opacity: .68; margin-top: .3rem; }}
.cs-chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: .7rem; }}
.cs-chip {{
  display: inline-flex; align-items: center; gap: 6px;
  font-size: .75rem; font-weight: 550; padding: 3px 10px; border-radius: {"4px" if sharp else "999px"};
  border: 1px solid var(--cs-line); background: var(--cs-fill);
}}
.cs-chip .dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: .6; }}
.cs-chip.accent .dot {{ background: var(--cs-accent); opacity: 1; }}
.cs-chip.warn .dot {{ background: {WARN}; opacity: 1; }}

/* ---------- KPI tiles ---------- */
.cs-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: {"8px" if compact else "12px"}; margin: 0 0 {".7rem" if compact else "1rem"} 0; }}
.cs-kpi {{
  border: 1px solid var(--cs-line); border-radius: {r_sm if sharp else "14px"};
  padding: {"11px 14px 10px" if compact else "16px 18px 14px"};
  background: {"var(--cs-surface)" if glass else "var(--cs-fill)"}; box-shadow: var(--cs-edge), var(--cs-shadow);
}}
.cs-kpi-label {{ font-size: .78rem; font-weight: 550; opacity: .68; margin-bottom: 6px; }}
.cs-kpi {{ min-width: 0; overflow: hidden; }}
.cs-kpi-value {{ font-size: {"1.45rem" if compact else "1.7rem"}; font-weight: 650; letter-spacing: -0.025em; line-height: 1.15;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
/* A name instead of a figure (largest zone, largest item…): smaller, up to two lines, never past the tile */
.cs-kpi-value.text {{ font-size: {"1.02rem" if compact else "1.12rem"}; letter-spacing: -0.01em; line-height: 1.28;
  white-space: normal; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.cs-kpi-unit {{ font-size: .85rem; font-weight: 500; opacity: .6; margin-left: 4px; }}
.cs-kpi-foot {{ display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; margin-top: 8px; font-size: .78rem; min-width: 0; }}
.cs-kpi-note {{ opacity: .62; min-width: 0; overflow-wrap: anywhere; }}
/* An open critical alert: the whole tile in dark red */
.cs-kpi.crit {{ background: #7f1d1d !important; border-color: #991b1b !important; color: #fff; }}
.cs-kpi.crit .cs-kpi-label, .cs-kpi.crit .cs-kpi-note {{ opacity: .9; }}
.cs-kpi.crit .cs-delta {{ color: #fff !important; background: rgba(255,255,255,.18) !important; }}
.cs-delta {{ font-weight: 600; padding: 1px 7px; border-radius: 6px; background: var(--cs-fill-2); }}
.cs-delta.good {{ color: {GOOD}; background: rgba(12,163,12,.12); }}
.cs-delta.bad  {{ color: {CRIT}; background: rgba(208,59,59,.12); }}

/* ---------- Status banner ---------- */
.cs-status {{
  display: flex; align-items: center; gap: 14px; padding: {"10px 14px" if compact else "14px 18px"}; margin: 0 0 1rem 0;
  border: 1px solid var(--cs-line); border-radius: {r_sm if sharp else "14px"};
  background: {"var(--cs-surface)" if glass else "var(--cs-fill)"}; box-shadow: var(--cs-edge), var(--cs-shadow);
  border-left: 4px solid var(--c);
}}
.cs-status-badge {{
  font-size: .72rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  padding: 4px 10px; border-radius: 6px; color: #0b0d10; background: var(--c); white-space: nowrap;
}}
.cs-status-badge.light-ink {{ color: #ffffff; }}
.cs-status-text {{ font-size: .9rem; opacity: .88; }}

/* ---------- Section label ---------- */
.cs-section {{ font-size: .95rem; font-weight: 600; margin: 0 0 2px 0; }}
.cs-section-sub {{ font-size: .78rem; opacity: .62; margin: 0 0 8px 0; }}

/* ---------- Pipeline stepper ---------- */
.cs-steps {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 0 0 1rem 0; }}
.cs-step {{ border: 1px solid var(--cs-line); border-radius: {r_sm}; padding: 10px 12px; background: {"var(--cs-surface)" if glass else "var(--cs-fill)"}; }}
.cs-step-n {{ font-size: .7rem; font-weight: 700; color: var(--cs-accent); letter-spacing: .06em; }}
.cs-step-t {{ font-size: .85rem; font-weight: 600; margin-top: 2px; }}
.cs-step-s {{ font-size: .75rem; opacity: .62; }}

/* ---------- Department tile ---------- */
.cs-dept {{ padding: 2px 2px 6px; }}
.cs-dept-name {{ font-size: .72rem; font-weight: 600; letter-spacing: .07em; text-transform: uppercase; opacity: .65; }}
.cs-dept-last {{ font-size: 1.35rem; font-weight: 650; letter-spacing: -0.02em; margin-top: 2px; }}
.cs-dept-note {{ display: flex; align-items: center; gap: 7px; font-size: .78rem; opacity: .85; margin-top: 4px; }}
.cs-dept-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--c); flex: none; }}

/* Live log: compact monospace */
[data-testid="stExpanderDetails"] pre {{ font-size: .74rem !important; line-height: 1.45 !important; }}

/* ---------- Report lists ---------- */
.cs-list {{ margin: 0; padding-left: 1.3rem; font-size: .88rem; line-height: 1.5; }}
.cs-list li {{ margin: 0 0 .55rem 0; padding-left: .2rem; }}
.cs-list li::marker {{ color: var(--cs-accent); font-weight: 700; }}
.cs-list.arrow {{ list-style: none; padding-left: 0; }}
.cs-list.arrow li::before {{ content: "→"; color: var(--cs-accent); font-weight: 700; margin-right: .5rem; }}

/* ---------- Assistant ---------- */
.cs-chips.cs-tools {{ margin: 0 0 .45rem 0; }}
.cs-chips.cs-tools .cs-chip {{ font-size: .7rem; padding: 2px 9px; opacity: .85; }}
.cs-assist-hello {{ margin: .6rem 0 .8rem 0; }}
[data-testid="stChatMessage"] table {{ font-size: .84rem; }}
[data-testid="stChatMessage"] h1, [data-testid="stChatMessage"] h2, [data-testid="stChatMessage"] h3,
[data-testid="stChatMessage"] h4 {{ font-size: 1.02rem; font-weight: 650; margin: .9rem 0 .35rem 0; padding: 0; }}
[data-testid="stChatMessage"] hr {{ margin: .7rem 0; }}
[data-testid="stChatMessage"] .cs-kpis {{ margin: .4rem 0 .2rem 0; }}

/* ---------- Settings preview ---------- */
.cs-swatches {{ display: flex; gap: 6px; margin: 2px 0 10px; }}
.cs-swatch {{ flex: 1; height: 30px; border-radius: {r_xs}; border: 1px solid var(--cs-line); }}

/* ---------- Footer ---------- */
.cs-foot {{
  margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--cs-line);
  font-size: .75rem; opacity: .6; display: flex; flex-wrap: wrap; gap: 6px 18px;
}}
{rise}
{motion}
</style>
"""


def inject(look: dict[str, Any] | None = None) -> None:
    """Inject the global stylesheet (call once per run, before rendering a page)."""
    look = look or ap.current()
    st.html(css(look, ap.active_mode(look)))
