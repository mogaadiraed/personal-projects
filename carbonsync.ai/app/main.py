"""CarbonSync AI – application shell: page config, theme, login, top navigation, footer, floating assistant.

The look and the layout come from the Settings (``appearance`` / ``layout`` in config.yaml):
palette, glass or flat style, start page, pages shown in the menu, floating button, footer.
The menu also follows the signed-in user's role (``PAGE_ROLES``) and shows the open alerts count.
"""
from __future__ import annotations

import uuid

import streamlit as st

from app.core import auth
from app.core import nav as navreg
from app.core.i18n import lang, tr
from app.data.loader import load_alerts_state, load_config
from app.pages import (
    ai_claude,
    ai_ollama,
    ai_openrouter,
    ai_overview,
    alerts,
    assistant,
    breakdown,
    budget,
    configuration,
    data_intake,
    data_quality,
    forecast,
    inventory,
    methodology,
    model_lab,
    overview,
    reports,
    scenarios,
)
from app.ui import alert_fab, appearance, assistant_dock, theme
from app.ui import components as ui
from carbonsync.obs import get_logger, setup_console_logging

log = get_logger("carbonsync.app")
_BANNER_SHOWN = False

RENDER = {
    "overview": overview.render, "forecast": forecast.render, "assistant": assistant.render,
    "ai-overview": ai_overview.render, "ai-ollama": ai_ollama.render, "ai-openrouter": ai_openrouter.render,
    "ai-claude": ai_claude.render, "model-lab": model_lab.render, "budget": budget.render,
    "scenarios": scenarios.render, "intake": data_intake.render, "data": data_quality.render,
    "methodology": methodology.render, "reports": reports.render, "configuration": configuration.render,
    "breakdown": breakdown.render, "alerts": alerts.render, "inventory": inventory.render,
}
# Minimum role to see a page (others: every signed-in user)
PAGE_ROLES = {"ai-overview": "admin", "ai-ollama": "admin", "ai-openrouter": "admin", "ai-claude": "admin",
              "model-lab": "editor", "intake": "editor"}   # Configuration: everyone (viewers get the Password tab)


def _startup_banner(cfg: dict) -> None:
    """Printed once per server process in the terminal that runs ``streamlit run``."""
    global _BANNER_SHOWN
    if _BANNER_SHOWN:
        return
    _BANNER_SHOWN = True
    from app.data.loader import load_active_forecast, load_emissions_daily

    df = load_emissions_daily()
    port = st.get_option("server.port")
    log.info("━━━━ CarbonSync AI dashboard · http://localhost:%s ━━━━", port)
    if not df.empty:
        from carbonsync.models.router import router_rule

        log.info("Data: %d days · %s → %s · router: %s", len(df), df["date"].min().date(),
                 df["date"].max().date(), router_rule(cfg)["en"])
        _, run = load_active_forecast()
        if run:
            log.info("Active forecast: %s (%s, trained on %s days%s)", run.get("run_id"), run.get("model_name"),
                     run.get("history_days"), ", OUTDATED – rerun the pipeline" if run.get("stale") else "")
    log.info("Activity below: page views, database reads (cache misses), model runs, pipeline steps")


def alert_count() -> int:
    """Open critical and warning alerts (the top-bar badge)."""
    state = load_alerts_state()
    return sum(1 for a in state.get("alerts", []) if a.get("severity") in ("critical", "warning"))


def _navigation(look: dict, role: str = "admin", alerts_open: int = 0) -> tuple[dict, dict[str, st.Page]]:
    """Menu sections with the pages shown (Overview, Assistant and Alerts always are, Settings for editors)."""
    from carbonsync.auth import at_least

    hidden = set(look["layout"]["hidden_pages"]) | {p for p, r in PAGE_ROLES.items() if not at_least(role, r)}
    shown = [p for _s, _e, pages in appearance.MENU for p in pages if p[0] not in hidden]
    start = look["layout"]["start_page"]
    if start not in {p[0] for p in shown}:
        start = "overview"
    by_path: dict[str, st.Page] = {}
    sections: dict[str, list[st.Page]] = {}
    for sec_fr, sec_en, pages in appearance.MENU:
        for path, fr, en, icon in pages:
            if path in hidden:
                continue
            title = tr(fr, en) + (f" · {alerts_open}" if path == "alerts" and alerts_open else "")
            by_path[path] = st.Page(RENDER[path], title=title, icon=f":material/{icon}:", url_path=path,
                                    default=path == start)
            sections.setdefault(tr(sec_fr, sec_en), []).append(by_path[path])
    return sections, by_path


def main() -> None:
    from carbonsync import db

    db.shared_process()                  # the pages read while a background pipeline writes (app/core/jobs.py)
    cfg = load_config()
    look = appearance.settings(cfg)
    appearance.sync_theme(look)          # .streamlit/config.toml follows the Settings (applied live)
    kind = appearance.active_mode(look)
    try:
        icon = appearance.favicon_path(look)
    except OSError:
        icon = ":material/eco:"
    st.set_page_config(
        page_title=cfg.get("app", {}).get("title", "CarbonSync AI"),
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    setup_console_logging()
    theme.inject(look)
    lang()  # initialise session language
    _startup_banner(cfg)
    try:
        st.logo(appearance.logo_path(look, kind), size="large")
    except OSError:
        pass

    user = auth.gate(cfg)
    auth.heartbeat()                     # presence for the team list (every minute while the page is open)
    sections, by_path = _navigation(look, user["role"], alert_count())
    navreg.PAGES.clear()
    navreg.PAGES.update(by_path)
    nav = st.navigation(sections, position="top")
    path = getattr(nav, "_url_path", None) or nav.url_path or "overview"   # the start page has url_path ""
    sid = st.session_state.setdefault("_sid", uuid.uuid4().hex[:6])
    if st.session_state.get("_last_page") != path:
        st.session_state["_last_page"] = path
        log.info("Session %s → page %s", sid, nav.title)
    nav.run()

    if look["layout"]["footer"]:
        ui.footer(str(cfg.get("governance", {}).get("factors_review_date", "—")))
    side = look["layout"]["assistant_side"]
    ai_button = bool(look["layout"]["assistant_button"]) and path != "assistant"
    if ai_button:                          # floating AI button (not on the Assistant tab)
        assistant_dock.render(path, nav.title, by_path.get("assistant"), side=side)
    alert_fab.render(path, by_path.get("alerts"), side=side, stacked=ai_button)   # above the AI button
