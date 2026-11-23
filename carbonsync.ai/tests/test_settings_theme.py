"""Settings upgrade: palettes and readability, config.toml writer, settings model, export / import /
reset, theme CSS, chart restyle, the Settings page and the layout options of the app shell.

Nothing here writes the real config.yaml or .streamlit/config.toml: saves and theme sync are patched.
"""
from __future__ import annotations

import copy
from pathlib import Path

import plotly.graph_objects as go
import pytest
from streamlit.testing.v1 import AppTest

from app.data.loader import load_config
from app.ui import appearance as ap
from app.ui import charts as ch
from app.ui import theme

# ---------------------------------------------------------------------------
# Palettes: every one readable in both modes, on flat and frosted surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(ap.PALETTES))
def test_preset_palettes_pass_every_contrast_floor(key):
    c = ap.checks(ap.PALETTES[key])
    assert all(c[k] >= v for k, v in ap.MIN_CHECKS.items()), {k: round(c[k], 2) for k in c}


@pytest.mark.parametrize("accent", ["#ffff00", "#00ffff", "#ff00ff", "#000000", "#ffffff", "#808080", "#0000ff",
                                    "#ff0000", "#7fff00", "#123456"])
def test_any_custom_accent_is_fitted_to_stay_readable(accent):
    look = ap.settings({"appearance": {"palette": "custom", "custom_accent": accent}})
    c = ap.checks(ap.palette(look))
    assert all(c[k] >= v for k, v in ap.MIN_CHECKS.items()), {k: round(c[k], 2) for k in c}


def test_contrast_maths():
    assert ap.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert ap.contrast("#777777", "#777777") == pytest.approx(1.0)
    assert ap.mix("#ffffff", "#000000", 0.5) in ("#808080", "#7f7f7f")


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------


def test_settings_defaults_and_cleaning():
    look = ap.settings({})
    assert look == ap.DEFAULTS
    assert look["appearance"]["style"] == "glass" and look["appearance"]["palette"] == "emerald"
    bad = ap.settings({
        "appearance": {"palette": "neon", "style": "wood", "custom_accent": "green", "animations": 0, "text_size": "xl"},
        "layout": {"hidden_pages": ["overview", "assistant", "configuration", "forecast", "nope"],
                   "start_page": "forecast", "assistant_side": "top"},
    })
    assert bad["appearance"]["palette"] == "emerald" and bad["appearance"]["style"] == "glass"
    assert bad["appearance"]["custom_accent"] == "#1baf7a" and bad["appearance"]["animations"] is False
    assert bad["appearance"]["text_size"] == "m"
    # protected pages can never be hidden, unknown ones are dropped, a hidden start page falls back to the overview
    assert bad["layout"]["hidden_pages"] == ["forecast"]
    assert bad["layout"]["start_page"] == "overview"
    assert bad["layout"]["assistant_side"] == "right"


def test_menu_lists_every_page_once():
    assert len(ap.PAGE_PATHS) == len(set(ap.PAGE_PATHS)) == 18          # + Breakdown, Alerts, GHG inventory
    assert set(ap.PROTECTED_PAGES) <= set(ap.PAGE_PATHS)


# ---------------------------------------------------------------------------
# .streamlit/config.toml
# ---------------------------------------------------------------------------


def test_theme_toml_keeps_other_sections_and_writes_both_modes(tmp_path):
    (tmp_path / ".streamlit").mkdir()
    old = ('[server]\nheadless = true\nport = 9999\n\n# a comment\n[theme]\nbase = "light"\nprimaryColor = "#000000"\n'
           '\n[theme.dark]\nprimaryColor = "#111111"\n\n[client]\ntoolbarMode = "viewer"\n')
    (tmp_path / ".streamlit" / "config.toml").write_text(old, encoding="utf-8")
    look = ap.settings({"appearance": {"palette": "ocean", "text_size": "l", "corners": "sharp"}})
    assert ap.sync_theme(look, root=tmp_path) is True
    text = (tmp_path / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "port = 9999" in text and 'toolbarMode = "viewer"' in text      # kept
    assert '"#000000"' not in text and '"#111111"' not in text              # old theme gone
    pal = ap.PALETTES["ocean"]
    assert f'primaryColor = "{pal["dark"]["primary"]}"' in text and f'primaryColor = "{pal["light"]["primary"]}"' in text
    assert "baseFontSize = 16" in text and 'baseRadius = "4px"' in text and 'base = "dark"' in text
    headers = [line for line in text.splitlines() if line.startswith("[")]
    assert headers.count("[theme]") == headers.count("[theme.dark]") == headers.count("[theme.light]") == 1
    assert text.startswith("# Theme sections generated")
    import tomllib

    parsed = tomllib.loads(text)
    assert parsed["theme"]["chartCategoricalColors"][0] == ap.CHART_COLORS[0]
    assert ap.sync_theme(look, root=tmp_path) is False                      # unchanged: not rewritten
    auto = ap.settings({"appearance": {"mode": "auto"}})
    assert "base =" not in ap.theme_toml(auto, text)


def test_theme_toml_without_a_file_has_the_default_server_sections(tmp_path):
    assert ap.sync_theme(ap.settings({}), root=tmp_path) is True
    text = (tmp_path / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'address = "localhost"' in text and "gatherUsageStats = false" in text


# ---------------------------------------------------------------------------
# Export / import / reset
# ---------------------------------------------------------------------------


def test_export_import_round_trip_and_reset():
    cfg = copy.deepcopy(load_config())
    cfg["appearance"] = {"palette": "indigo", "style": "flat"}
    cfg["layout"] = {"hidden_pages": ["model-lab"]}
    exported = ap.export_settings(cfg)
    assert exported[ap.SETTINGS_FILE_TAG] == 1
    assert exported["appearance"]["palette"] == "indigo" and exported["layout"]["hidden_pages"] == ["model-lab"]
    assert "emission_factors" not in exported and "ollama" not in exported   # no data, no AI settings

    target = ap.reset_look(cfg)
    assert target["appearance"] == ap.DEFAULTS["appearance"] and target["budget"] == cfg["budget"]
    new, sections = ap.apply_import(target, exported)
    assert {"appearance", "layout", "reports", "app", "budget"} <= set(sections)
    assert ap.settings(new)["appearance"]["palette"] == "indigo"
    assert new["emission_factors"] == cfg["emission_factors"]


def test_import_rejects_other_files_and_bad_values():
    with pytest.raises(ValueError):
        ap.apply_import({}, {"hello": 1})
    with pytest.raises(ValueError):
        ap.apply_import({}, [1, 2])
    new, _ = ap.apply_import({"budget": {"annual_cap_tco2e": 2000}}, {
        ap.SETTINGS_FILE_TAG: 1,
        "budget": {"annual_cap_tco2e": 10, "monthly_weights": {str(m): 0.5 for m in range(1, 13)}},
        "app": {"language": "de", "title": "  Site A  "},
        "appearance": {"palette": "neon"},
    })
    assert new["budget"]["annual_cap_tco2e"] == 2000 and "monthly_weights" not in new["budget"]
    assert new["app"] == {"title": "Site A"}
    assert new["appearance"]["palette"] == "emerald"


# ---------------------------------------------------------------------------
# Theme CSS
# ---------------------------------------------------------------------------


def test_css_follows_style_density_corners_width_and_animations():
    glass = theme.css(ap.settings({}), "dark")
    flat = theme.css(ap.settings({"appearance": {"style": "flat", "density": "compact", "corners": "sharp",
                                                 "animations": False}, "layout": {"page_width": "full"}}), "light")
    assert "radial-gradient" in glass and "backdrop-filter" in glass and "csRise" in glass
    assert "radial-gradient" not in flat and "backdrop-filter" not in flat
    assert "animation: none !important" in flat and "max-width: 100% !important" in flat
    assert "max-width: 1360px !important" in glass
    pal = ap.PALETTES["emerald"]
    assert f"--cs-accent:{pal['dark']['primary']}" in glass and f"--cs-accent:{pal['light']['primary']}" in flat
    assert "csDriftA" in glass and ".stApp::before" in glass                    # animated wallpaper by default
    assert ".stApp::before" not in flat                                          # flat: plain page
    still = theme.css(ap.settings({"appearance": {"animated_background": False}}), "dark")
    assert ".stApp::before" in still and "csDriftA" not in still                 # glows kept, not moving
    no_motion = theme.css(ap.settings({"appearance": {"animations": False}}), "dark")
    assert "csDriftA" not in no_motion
    both = theme.css(ap.settings({}), None)        # mode not known yet: follows the OS
    assert "prefers-color-scheme: light" in both


def test_logo_and_favicon_use_the_accent():
    look = ap.settings({"appearance": {"palette": "coral"}})
    pal = ap.PALETTES["coral"]
    assert f'fill="{pal["light"]["primary"]}"' in ap.logo_svg(look, "light")
    assert f'fill="{pal["dark"]["primary"]}"' in ap.logo_svg(look, "dark")
    assert pal["dark"]["primary"] in ap.favicon_svg(look)
    assert Path(ap.logo_path(look, "dark")).exists() and Path(ap.favicon_path(look)).exists()


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def test_chart_base_and_dark_steps():
    fig = go.Figure()
    fig.add_scatter(x=[1, 2], y=[1, 2], mode="lines", line={"color": ch.BLUE, "width": 1.2})
    fig.add_scatter(x=[1, 2], y=[1, 2], mode="lines", line={"width": 0}, fill="tonexty", fillcolor=ch._rgba(ch.ORANGE, 0.2))
    fig.add_annotation(x=1, y=1, text="a", font={"size": 9})
    fig = ch._base(fig, unit="t")
    assert fig.data[0].line.width == 2 and fig.data[1].line.width == 0     # bands stay hidden
    assert fig.layout.font.size == ap.CHART_FONT_PX["m"] and fig.layout.annotations[0].font.size >= 12
    assert fig.layout.xaxis.showspikes is True
    dark = ch._restep(fig)
    assert dark.data[0].line.color == ch.DARK_STEPS[ch.BLUE]
    assert dark.data[1].fillcolor.startswith("rgba(217,89,38,")              # #d95926
    assert ch.MODEL_COLORS["oracle"] == ch.MAGENTA and ch.SCOPE_COLORS == {"s1": ch.ORANGE, "s2": ch.BLUE, "other": ch.AQUA}


# ---------------------------------------------------------------------------
# Settings page (saves recorded in memory)
# ---------------------------------------------------------------------------


def _settings_page() -> None:
    from app.pages import configuration

    configuration.render()


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


def test_settings_page_has_eight_tabs_and_applies_a_palette(saved):
    at = AppTest.from_function(_settings_page, default_timeout=60)
    at.run()
    assert not at.exception
    assert len(at.tabs) == 8                                     # administrator (login off in tests)
    assert at.button(key="ap_apply").disabled                  # nothing changed yet
    at.segmented_control(key="ap_palette").set_value("ocean").run()
    assert not at.toggle(key="ap_wall").disabled
    at.segmented_control(key="ap_style").set_value("flat").run()
    assert at.toggle(key="ap_wall").disabled                     # only for the glass effect
    assert not at.button(key="ap_apply").disabled
    at.button(key="ap_apply").click().run()
    assert not at.exception
    assert saved and saved[-1]["appearance"]["palette"] == "ocean" and saved[-1]["appearance"]["style"] == "flat"
    assert saved[-1]["emission_factors"] == load_config()["emission_factors"]   # nothing else touched


def test_settings_page_layout_save_keeps_protected_pages(saved):
    already = set(ap.settings(load_config())["layout"]["hidden_pages"])   # whatever the user hid
    at = AppTest.from_function(_settings_page, default_timeout=60)
    at.run()
    pills = at.pills(key="lay_pages")
    assert "overview" not in pills.options and "configuration" not in pills.options
    pills.set_value([p for p in pills.value if p != "model-lab"]).run()
    at.toggle(key="lay_footer").set_value(False).run()
    at.button(key="lay_save").click().run()
    assert not at.exception
    lay = saved[-1]["layout"]
    assert lay["hidden_pages"] == sorted(already | {"model-lab"}) and lay["footer"] is False


def test_settings_page_reset_needs_confirmation(saved):
    at = AppTest.from_function(_settings_page, default_timeout=60)
    at.run()
    assert at.button(key="cfg_reset").disabled
    at.checkbox(key="cfg_reset_ok").check().run()
    at.button(key="cfg_reset").click().run()
    assert not at.exception
    assert saved[-1]["appearance"] == ap.DEFAULTS["appearance"]


# ---------------------------------------------------------------------------
# App shell: hidden pages, start page
# ---------------------------------------------------------------------------


def _shell_nav() -> None:
    import streamlit as st

    from app import main as m
    from app.ui import appearance

    look = appearance.settings({"layout": {"hidden_pages": ["model-lab", "intake"], "start_page": "budget"}})
    sections, by_path = m._navigation(look)
    st.session_state["_paths"] = sorted(by_path)
    st.session_state["_default"] = [p for p, page in by_path.items() if page._default]
    st.session_state["_sections"] = len(sections)


def test_shell_navigation_hides_pages_and_moves_the_start_page():
    at = AppTest.from_function(_shell_nav, default_timeout=60)
    at.run()
    assert not at.exception
    paths = at.session_state["_paths"]
    assert "model-lab" not in paths and "intake" not in paths and "overview" in paths and "assistant" in paths
    assert at.session_state["_default"] == ["budget"]
    assert at.session_state["_sections"] == 8            # the Models section is empty, so it is gone
