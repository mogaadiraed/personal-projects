"""New pages (Alerts, Breakdown, GHG inventory), the target section of the budget, role gating and the login."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.ui import appearance as ap

ROOT = Path(__file__).resolve().parents[1]
EDITOR = {"username": "editor1", "name": "Ed Itor", "role": "editor"}
VIEWER = {"username": "viewer1", "name": "Vi Ewer", "role": "viewer"}


def _page(name: str):
    return f"from app.pages import {name}\n{name}.render()"


def _text(at) -> str:
    return "\n".join(str(e.value) for e in [*at.markdown, *at.caption, *at.info, *at.warning, *at.error])


@pytest.fixture
def saved(monkeypatch, tmp_path):
    from app.pages import configuration

    calls: list[dict] = []

    def fake_save(cfg):
        calls.append(copy.deepcopy(cfg))
        return tmp_path / "config_test.yaml"

    monkeypatch.setattr(configuration, "save_config", fake_save)
    monkeypatch.setattr(ap, "sync_theme", lambda look, root=None: False)
    return calls


@pytest.mark.parametrize("lang", ["fr", "en"])
@pytest.mark.parametrize("name", ["alerts", "breakdown", "inventory", "budget", "overview"])
def test_new_and_changed_pages_render(name, lang):
    at = AppTest.from_string(_page(name), default_timeout=120)
    at.session_state["lang"] = lang
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_budget_page_shows_the_target_path():
    at = AppTest.from_string(_page("budget"), default_timeout=120)
    at.session_state["lang"] = "en"
    at.run()
    text = _text(at)
    assert "cap" in text and "projected" in text.lower()
    assert len(at.get("plotly_chart")) >= 3                     # month, path, weights


def test_inventory_page_shows_both_scope2_methods():
    at = AppTest.from_string(_page("inventory"), default_timeout=120)
    at.session_state["lang"] = "en"
    at.run()
    assert "residual mix" in _text(at).lower()
    assert len(at.dataframe) >= 2


def test_viewer_cannot_run_a_forecast_or_generate_reports():
    at = AppTest.from_string(_page("forecast"), default_timeout=120)
    at.session_state["auth_user"] = VIEWER
    at.run()
    assert not at.exception and at.button(key="run_go").disabled
    at = AppTest.from_string(_page("reports"), default_timeout=120)
    at.session_state["auth_user"] = VIEWER
    at.run()
    assert at.button(key="gen_xlsx").disabled and at.button(key="gen_pdf").disabled
    at = AppTest.from_string(_page("alerts"), default_timeout=120)
    at.session_state["auth_user"] = VIEWER
    at.run()
    assert at.button(key="al_check").disabled


def test_editor_gets_the_editor_tabs_and_a_viewer_only_the_password_tab(saved):
    at = AppTest.from_string(_page("configuration"), default_timeout=120)
    at.session_state["auth_user"] = EDITOR
    at.session_state["lang"] = "en"
    at.run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert len(labels) == 5 and any("Target" in x for x in labels) and not any("Users" in x for x in labels)
    assert labels[-1].endswith("Password")
    at = AppTest.from_string(_page("configuration"), default_timeout=120)
    at.session_state["auth_user"] = VIEWER
    at.session_state["lang"] = "en"
    at.run()
    assert not at.exception and [t.label.split()[-1] for t in at.tabs] == ["Password"]


def test_target_tab_saves_a_new_reduction(saved):
    at = AppTest.from_string(_page("configuration"), default_timeout=120)
    at.session_state["auth_user"] = EDITOR
    at.run()
    assert at.button(key="tg_save").disabled                     # nothing changed yet
    at.number_input(key="tg_red").set_value(40.0).run()
    assert not at.button(key="tg_save").disabled
    at.button(key="tg_save").click().run()
    assert not at.exception
    assert saved and saved[-1]["targets"]["reduction_pct"] == 40.0
    from app.data.loader import load_config

    assert saved[-1]["emission_factors"] == load_config()["emission_factors"]   # nothing else touched
    assert "budget" in saved[-1] and saved[-1]["budget"]["cap_source"] in ("path", "manual")


def test_breakdown_keys_that_do_not_add_up_cannot_be_saved(saved):
    at = AppTest.from_string(_page("configuration"), default_timeout=120)
    at.session_state["auth_user"] = EDITOR
    at.run()
    at.segmented_control(key="inv_sub").set_value("bd").run()
    assert not at.exception
    assert at.button(key="bk_save").disabled                     # unchanged
    assert saved == []


def test_router_card_in_the_system_tab(saved):
    at = AppTest.from_string(_page("configuration"), default_timeout=120)
    at.run()                                                     # login off: administrator
    assert not at.exception
    assert at.selectbox(key="rt_model").value == "chronos" and at.button(key="rt_save").disabled
    at.number_input(key="rt_min").set_value(90).run()
    at.button(key="rt_save").click().run()
    assert saved[-1]["router"] == {"model": "chronos", "fallback": "prophet", "min_days": 90}


# ---------------------------------------------------------------------------
# Login through the whole app (streamlit_app.py)
# ---------------------------------------------------------------------------
@pytest.fixture
def auth_root(tmp_path, monkeypatch):
    import carbonsync.auth as core

    monkeypatch.delenv("CARBONSYNC_AUTH", raising=False)
    monkeypatch.setattr(core, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(ap, "sync_theme", lambda look, root=None: False)
    return tmp_path


def test_first_start_creates_the_administrator_then_viewers_see_less(auth_root):
    import carbonsync.auth as core
    from app.core import nav

    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120)
    at.run()
    assert not at.exception
    at.text_input(key="auth_setup_user").input("admin")
    at.text_input(key="auth_setup_pw1").input("admin-pass")
    at.text_input(key="auth_setup_pw2").input("admin-pass")
    at.button[0].click().run()
    assert not at.exception and at.session_state["auth_user"]["role"] == "admin"
    assert {"configuration", "intake", "model-lab", "ai-claude"} <= set(nav.PAGES)

    core.AuthStore(auth_root).create_user("viewer1", "viewer-pass", "viewer")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120)
    at.run()
    at.text_input(key="auth_user_in").input("viewer1")
    at.text_input(key="auth_pw_in").input("wrong-pass")
    at.button[0].click().run()
    assert at.error and "auth_user" not in at.session_state
    at.text_input(key="auth_user_in").input("viewer1")
    at.text_input(key="auth_pw_in").input("viewer-pass")
    at.button[0].click().run()
    assert not at.exception and at.session_state["auth_user"]["role"] == "viewer"
    assert not {"intake", "model-lab", "ai-claude"} & set(nav.PAGES)
    assert {"overview", "alerts", "breakdown", "inventory", "budget", "configuration"} <= set(nav.PAGES)
    assert "viewer-pass" not in (auth_root / "data" / "auth" / "users.json").read_text(encoding="utf-8")


def _html(at) -> str:
    return "\n".join(h.proto.body for h in at.get("html"))


def test_sign_in_screen_hides_the_menu_bar_and_presence_shows_the_team(auth_root):
    import carbonsync.auth as core

    store = core.AuthStore(auth_root)
    store.create_user("admin", "admin-pass", "admin", "Amira Ben Ali")
    store.create_user("viewer1", "viewer-pass", "viewer", "Sami")
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120)
    at.session_state["lang"] = "en"
    at.run()
    page = _html(at)
    assert not at.exception and 'header[data-testid="stHeader"]' in page and "cs-login-brand" in page
    at.text_input(key="auth_user_in").input("admin")
    at.text_input(key="auth_pw_in").input("admin-pass")
    at.button[0].click().run()
    page = _html(at)
    assert not at.exception and "cs-login-brand" not in page            # signed in: the menu bar is back
    assert "Amira Ben Ali" in page and "Sami" in page and page.count("cs-dot on") == 1   # admin online, viewer not
    assert "auth_new1" not in [t.key for t in at.text_input]              # no password form in the account menu


def test_floating_alert_button_lists_critical_alerts_and_opens_them(monkeypatch):
    from app.ui import alert_fab

    state = {"alerts": [
        {"id": "w", "severity": "warning", "title_en": "Late data", "detail_en": "d", "title_fr": "t", "detail_fr": "d"},
        {"id": "c", "severity": "critical", "title_en": "Year over cap", "detail_en": "d", "title_fr": "t", "detail_fr": "d"},
    ]}
    monkeypatch.setattr(alert_fab, "load_alerts_state", lambda: state)
    at = AppTest.from_string("from app.ui import alert_fab\nalert_fab.render('overview', None)", default_timeout=60)
    at.session_state["lang"] = "en"
    at.run()
    page = _html(at)
    assert not at.exception and "radial-gradient(circle at 30% 25%, #f87171" in page       # red button
    assert page.index("Year over cap") < page.index("Late data")                            # critical first
    assert 'class="cs-ap-item critical"' in page and "bottom: 102px" in page               # above the AI button
    at = AppTest.from_string("from app.ui import alert_fab\nalert_fab.render('alerts', None)", default_timeout=60)
    at.run()
    assert "st-key-cs_afab" not in _html(at)                                                # not on the Alerts page


def test_alerts_page_filters_and_paints_critical_alerts():
    at = AppTest.from_string(_page("alerts"), default_timeout=120)
    at.session_state["lang"] = "en"
    at.session_state["al_filter"] = "critical"          # as set by the floating button
    at.run()
    assert not at.exception
    page = _html(at)
    assert "st-key-cs_al_critical_" in page and "#7f1d1d" in page
    from app.data.loader import load_alerts_state

    shown = [a for a in load_alerts_state().get("alerts", []) if a["severity"] == "critical"]
    titles = "\n".join(m.value for m in at.markdown)
    assert all(a["title_en"] in titles for a in shown)
    assert not any(a["title_en"] in titles for a in load_alerts_state().get("alerts", []) if a["severity"] != "critical")


def test_text_kpis_wrap_instead_of_overflowing():
    from app.ui import components as ui

    assert ui._is_text("Assembly lines and test benches") and ui._is_text("Utilities and buildings")
    assert not ui._is_text("3,170") and not ui._is_text("Chronos-2") and not ui._is_text("2026-03-31")
