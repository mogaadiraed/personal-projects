"""Appearance and layout settings: palettes, glass / flat style, text size, density, corners,
animations, page layout, and the files they produce.

* Stored in ``config.yaml`` (``appearance``, ``layout``, ``reports`` sections) with the usual
  audit copies.
* Native widget colours come from ``.streamlit/config.toml``. Streamlit watches that file and
  sends the theme again at every run, so :func:`sync_theme` rewrites its theme sections and a
  new palette applies live, without a restart. Its other sections (server, browser, client) are
  kept as they are.
* Every palette is fitted so text stays readable (contrast checks below, also run in the tests):
  body text ≥ 7:1, the accent as text ≥ 4.5:1 on the page, white on the accent (primary buttons)
  ≥ 3:1 in dark mode and ≥ 4.5:1 in light mode, the same on the frosted cards.
"""
from __future__ import annotations

import colorsys
import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

from app.data.loader import get_project_root, load_config

# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, dict[str, Any]] = {
    "appearance": {"palette": "emerald", "custom_accent": "#1baf7a", "style": "glass", "mode": "dark",
                   "text_size": "m", "density": "comfortable", "corners": "rounded", "animations": True,
                   "animated_background": True},
    "layout": {"start_page": "overview", "hidden_pages": [], "assistant_button": True, "assistant_side": "right",
               "page_width": "standard", "footer": True},
    "reports": {"language": "ui"},
}
CHOICES: dict[str, tuple[str, ...]] = {
    "style": ("glass", "flat"), "mode": ("auto", "light", "dark"), "text_size": ("s", "m", "l"),
    "density": ("comfortable", "compact"), "corners": ("rounded", "sharp"), "assistant_side": ("right", "left"),
    "page_width": ("standard", "full"), "language": ("ui", "fr", "en"),
}
# Top menu: (section fr, section en, [(url path, title fr, title en, material icon)])
MENU: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    ("Pilotage", "Monitor", [("overview", "Vue d'ensemble", "Overview", "space_dashboard"),
                             ("forecast", "Prévisions", "Forecast", "insights"),
                             ("breakdown", "Répartition", "Breakdown", "account_tree")]),
    ("Alertes", "Alerts", [("alerts", "Alertes", "Alerts", "notifications")]),
    ("Assistant", "Assistant", [("assistant", "Assistant", "Assistant", "smart_toy")]),
    ("IA & API", "AI & API", [("ai-overview", "Vue d'ensemble IA", "AI overview", "hub"),
                              ("ai-ollama", "Ollama", "Ollama", "cloud"),
                              ("ai-openrouter", "OpenRouter", "OpenRouter", "alt_route"),
                              ("ai-claude", "Claude", "Claude", "psychology")]),
    ("Modèles", "Models", [("model-lab", "Laboratoire modèles", "Model lab", "model_training")]),
    ("Planification", "Plan", [("budget", "Budget carbone", "Carbon budget", "track_changes"),
                               ("scenarios", "Scénarios", "Scenarios", "tune")]),
    ("Données", "Data", [("intake", "Dépôt de données", "Data intake", "upload_file"),
                         ("data", "Qualité des données", "Data quality", "fact_check")]),
    ("Preuves", "Evidence", [("inventory", "Inventaire GES", "GHG inventory", "inventory_2"),
                             ("methodology", "Méthodologie", "Methodology", "science"),
                             ("reports", "Rapports", "Reports", "description")]),
    ("Réglages", "Settings", [("configuration", "Configuration", "Configuration", "settings")]),
]
PAGE_PATHS = [p[0] for _s, _e, pages in MENU for p in pages]
# Pages that can never be hidden from the menu
PROTECTED_PAGES = ("overview", "assistant", "configuration", "alerts")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def settings(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``appearance``, ``layout`` and ``reports`` from the config, completed with defaults and checked."""
    out = copy.deepcopy(DEFAULTS)
    for section, values in out.items():
        given = cfg.get(section) or {}
        for k, default in values.items():
            v = given.get(k, default)
            if k in CHOICES and v not in CHOICES[k]:
                v = default
            elif isinstance(default, bool):
                v = bool(v)
            elif k == "palette" and v not in PALETTES and v != "custom":
                v = default
            elif k == "custom_accent":
                v = v.lower() if isinstance(v, str) and _HEX.match(v) else default
            elif k == "hidden_pages":
                v = sorted({p for p in (v if isinstance(v, list) else []) if p in PAGE_PATHS and p not in PROTECTED_PAGES})
            elif k == "start_page" and v not in PAGE_PATHS:
                v = default
            values[k] = v
    if out["layout"]["start_page"] in out["layout"]["hidden_pages"]:
        out["layout"]["start_page"] = "overview"
    return out


def current() -> dict[str, dict[str, Any]]:
    """Settings of the running app (from the cached config.yaml)."""
    return settings(load_config())


def active_mode(look: dict[str, Any]) -> str | None:
    """"light" or "dark" as shown in the browser, else the saved mode, else None (follows the OS)."""
    try:
        theme = st.context.theme
        kind = theme.get("type") if isinstance(theme, dict) else getattr(theme, "type", None)
    except Exception:  # noqa: BLE001 – outside a script run
        kind = None
    if kind in ("light", "dark"):
        return kind
    mode = look["appearance"]["mode"]
    return mode if mode in ("light", "dark") else None


# ---------------------------------------------------------------------------
# Colour maths (WCAG 2 relative luminance and contrast)
# ---------------------------------------------------------------------------
def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def luminance(hex_color: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (round(c * 255) for c in _rgb(hex_color))
    return f"rgba({r},{g},{b},{alpha})"


def mix(top: str, bottom: str, alpha: float) -> str:
    """``top`` painted at ``alpha`` over ``bottom`` (what a translucent layer looks like)."""
    t, b = _rgb(top), _rgb(bottom)
    return _hex(tuple(alpha * x + (1 - alpha) * y for x, y in zip(t, b, strict=True)))  # type: ignore[arg-type]


def _hls(hex_color: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*_rgb(hex_color))


def _from_hls(h: float, light: float, s: float) -> str:
    return _hex(colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, light)), max(0.0, min(1.0, s))))


def fit_luminance(hex_color: str, lo: float, hi: float) -> str:
    """Same hue and saturation, lightness moved until the luminance sits in [lo, hi]."""
    h, _light, s = _hls(hex_color)
    if lo <= luminance(hex_color) <= hi:
        return hex_color.lower()
    target = (lo + hi) / 2
    a, b = 0.0, 1.0
    for _ in range(40):
        mid = (a + b) / 2
        if luminance(_from_hls(h, mid, s)) < target:
            a = mid
        else:
            b = mid
    return _from_hls(h, (a + b) / 2, s)


# Luminance bands for the accent (see the module docstring)
DARK_ACCENT = (0.24, 0.29)     # ≥ 4.5:1 on a near-black page and on the frosted cards, white label ≥ 3:1
LIGHT_ACCENT = (0.09, 0.14)    # ≥ 4.5:1 on an off-white page and on the frosted cards, white label ≥ 4.5:1
TEXT_DARK, TEXT_LIGHT = "#eceeef", "#0b0d10"

# Glass layers: how strong the tinted glows behind the cards are, and how opaque the cards stay
GLASS = {
    "dark": {"glow": 0.14, "glow2": 0.09, "card": 0.66, "header": 0.80},
    "light": {"glow": 0.14, "glow2": 0.10, "card": 0.62, "header": 0.82},
}


def _palette(accent: str, glow2: str) -> dict[str, Any]:
    """Full light + dark palette from one accent colour (hue-tinted neutrals)."""
    h, _l, s = _hls(accent)
    tint = min(s, 0.55)
    return {
        "dark": {"primary": fit_luminance(accent, *DARK_ACCENT), "bg": _from_hls(h, 0.045, tint * 0.45),
                 "bg2": _from_hls(h, 0.085, tint * 0.30), "border": _from_hls(h, 0.16, tint * 0.18),
                 "text": TEXT_DARK, "card": _from_hls(h, 0.10, tint * 0.30)},
        "light": {"primary": fit_luminance(accent, *LIGHT_ACCENT), "bg": _from_hls(h, 0.968, tint * 0.30),
                  "bg2": _from_hls(h, 0.925, tint * 0.22), "border": _from_hls(h, 0.875, tint * 0.16),
                  "text": TEXT_LIGHT, "card": "#ffffff"},
        # Colours of the two soft glows behind the frosted cards (dark mode: the fitted steps, never brighter)
        "glow": {"dark": (fit_luminance(accent, *DARK_ACCENT), fit_luminance(glow2, *DARK_ACCENT)),
                 "light": (accent.lower(), glow2.lower())},
    }


_PRESETS: dict[str, tuple[tuple[str, str], str, str]] = {
    # key: ((fr, en), accent, second glow colour)
    "emerald": (("Émeraude", "Emerald"), "#1baf7a", "#3987e5"),
    "ocean": (("Océan", "Ocean"), "#1593c9", "#19b5a5"),
    "indigo": (("Indigo", "Indigo"), "#6366f1", "#a855f7"),
    "amber": (("Ambre", "Amber"), "#e09a12", "#e8603c"),
    "coral": (("Corail", "Coral"), "#ee6a4c", "#e0518a"),
    "graphite": (("Graphite", "Graphite"), "#7d8799", "#5f7fa8"),
}
PALETTES: dict[str, dict[str, Any]] = {k: {"name": n, **_palette(a, g)} for k, (n, a, g) in _PRESETS.items()}


def palette(look: dict[str, Any]) -> dict[str, Any]:
    a = look["appearance"]
    if a["palette"] == "custom":
        h, light, s = _hls(a["custom_accent"])
        return {"name": ("Personnalisée", "Custom"), **_palette(a["custom_accent"], _from_hls(h + 0.11, light, s))}
    return PALETTES[a["palette"]]


def glass_surface(pal: dict[str, Any], kind: str) -> str:
    """Worst case behind text on a frosted card: the card over the page where both glows overlap
    (the animated wallpaper moves them, so they can meet)."""
    g, p = GLASS[kind], pal[kind]
    g1, g2 = pal["glow"][kind]
    page = mix(g2, mix(g1, p["bg"], g["glow"]), g["glow2"])
    return mix(p["card"], page, g["card"])


def checks(pal: dict[str, Any]) -> dict[str, float]:
    """The contrast ratios the readability rules are about (both modes, flat and glass)."""
    d, li = pal["dark"], pal["light"]
    gd, gl = glass_surface(pal, "dark"), glass_surface(pal, "light")
    return {
        "dark_text": min(contrast(d["text"], d["bg2"]), contrast(d["text"], gd)),
        "light_text": min(contrast(li["text"], li["bg2"]), contrast(li["text"], gl)),
        "dark_accent": min(contrast(d["primary"], d["bg"]), contrast(d["primary"], gd)),
        "light_accent": min(contrast(li["primary"], li["bg"]), contrast(li["primary"], gl)),
        "dark_button": contrast("#ffffff", d["primary"]), "light_button": contrast("#ffffff", li["primary"]),
    }


MIN_CHECKS = {"dark_text": 7.0, "light_text": 7.0, "dark_accent": 4.5, "light_accent": 4.5, "dark_button": 3.0,
              "light_button": 4.5}


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------
FONT_PX = {"s": 13, "m": 14, "l": 16}
CHART_FONT_PX = {"s": 12, "m": 13, "l": 14.5}
RADIUS = {"rounded": ("10px", "8px"), "sharp": ("4px", "4px")}   # widgets, buttons


def chart_font(look: dict[str, Any]) -> float:
    return CHART_FONT_PX[look["appearance"]["text_size"]]


# ---------------------------------------------------------------------------
# .streamlit/config.toml
# ---------------------------------------------------------------------------
_TOML_NOTE = ("# Theme sections generated by CarbonSync AI from config.yaml (Settings → Appearance).\n"
              "# Change the look there: the [theme] sections below are rewritten when it changes.\n")
_TOML_DEFAULT = """[server]
headless = true
port = 8501
address = "localhost"

[browser]
gatherUsageStats = false

[client]
toolbarMode = "viewer"
"""
# Chart series: the validated categorical order (light steps; dark steps in app/ui/charts.py)
CHART_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
_SECTION = re.compile(r"^\s*\[([^\]]+)\]")


def _kept_sections(text: str) -> str:
    """Everything but the [theme*] sections and the comment lines (regenerated)."""
    out, keep = [], True
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            keep = not m.group(1).strip().startswith("theme")
        if keep and not line.lstrip().startswith("#"):
            out.append(line)
    kept = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return (kept + "\n") if kept else _TOML_DEFAULT


def theme_toml(look: dict[str, Any], existing: str | None = None) -> str:
    a, pal = look["appearance"], palette(look)
    base_radius, button_radius = RADIUS[a["corners"]]
    lines = [_TOML_NOTE, _kept_sections(existing) if existing else _TOML_DEFAULT, "[theme]"]
    if a["mode"] != "auto":
        lines.append(f'base = "{a["mode"]}"')
    lines += [
        'font = "Inter:https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"',
        f"baseFontSize = {FONT_PX[a['text_size']]}",
        f'baseRadius = "{base_radius}"',
        f'buttonRadius = "{button_radius}"',
        "showWidgetBorder = true",
        "linkUnderline = false",
        "chartCategoricalColors = [" + ", ".join(f'"{c}"' for c in CHART_COLORS) + "]",
    ]
    for mode in ("dark", "light"):
        p = pal[mode]
        lines += ["", f"[theme.{mode}]", f'primaryColor = "{p["primary"]}"', f'backgroundColor = "{p["bg"]}"',
                  f'secondaryBackgroundColor = "{p["bg2"]}"', f'textColor = "{p["text"]}"',
                  f'linkColor = "{p["primary"]}"', f'borderColor = "{p["border"]}"',
                  f'dataframeBorderColor = "{p["border"]}"']
    return "\n".join(lines) + "\n"


def toml_path(root: Path | None = None) -> Path:
    return (root or get_project_root()) / ".streamlit" / "config.toml"


def sync_theme(look: dict[str, Any], root: Path | None = None) -> bool:
    """Writes ``.streamlit/config.toml`` when the appearance changed. True if it was rewritten."""
    path = toml_path(root)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        text = theme_toml(look, existing)
        if existing == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Logo and favicon (in the accent colour of the mode shown)
# ---------------------------------------------------------------------------
_LEAF = ("M25.5 11.5c-7.5 0-13 4.6-13 11.4 0 1.6.4 3 1 4.1 1.8-4.4 5.4-7.6 9.8-9.2-3.6 2.2-6.2 5.5-7.4 9.6 "
         "1.1.6 2.4.9 3.9.9 6.2 0 9.7-6.3 5.7-16.8z")


def _mark(top: str, bottom: str, y: int) -> str:
    return (f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient></defs>'
            f'<rect x="2" y="{y}" width="32" height="32" rx="9" fill="url(#g)"/>'
            f'<g transform="translate(0 {y - 4})"><path d="{_LEAF}" fill="#ffffff"/></g>')


def _mark_colors(look: dict[str, Any]) -> tuple[str, str]:
    """Gradient of the mark: lighter top-left, the dark-mode accent bottom-right (white leaf ≥ 3:1)."""
    accent = palette(look)["dark"]["primary"]
    h, light, s = _hls(accent)
    return _from_hls(h, min(0.72, light + 0.14), s), accent


def logo_svg(look: dict[str, Any], kind: str | None, title: str = "CarbonSync") -> str:
    pal = palette(look)
    kind = kind or "dark"
    word = pal[kind]["primary"]                       # ≥ 4.5:1 on that mode's page
    suffix = "#5f6670" if kind == "light" else "#9aa0a8"
    top, bottom = _mark_colors(look)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 40" width="200" height="40">'
            + _mark(top, bottom, 4)
            + '<text x="44" y="27" font-family="Inter, \'Segoe UI\', system-ui, sans-serif" font-size="19" '
              f'font-weight="700" letter-spacing="-0.4" fill="{word}">{title}'
              f'<tspan font-weight="500" fill="{suffix}"> AI</tspan></text></svg>')


def favicon_svg(look: dict[str, Any]) -> str:
    top, bottom = _mark_colors(look)
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" width="36" height="36">' + _mark(top, bottom, 2) + "</svg>"


@lru_cache(maxsize=32)
def _write(name: str, text: str) -> str:
    d = get_project_root() / "data" / "ui"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return str(path)


def _name(prefix: str, svg: str) -> str:
    import hashlib

    return f"{prefix}_{hashlib.sha1(svg.encode()).hexdigest()[:10]}.svg"


def logo_path(look: dict[str, Any], kind: str | None) -> str:
    svg = logo_svg(look, kind)
    return _write(_name("logo", svg), svg)


def favicon_path(look: dict[str, Any]) -> str:
    svg = favicon_svg(look)
    return _write(_name("icon", svg), svg)


# ---------------------------------------------------------------------------
# Export / import / reset of the settings (Settings → System)
# ---------------------------------------------------------------------------
SETTINGS_FILE_TAG = "carbonsync_settings"
_APP_KEYS = ("title", "language", "data_mode")


def export_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """What a person can change in the Settings, as one JSON-ready dict (no data, no keys)."""
    look = settings(cfg)
    app = cfg.get("app", {}) or {}
    bud = cfg.get("budget", {}) or {}
    return {
        SETTINGS_FILE_TAG: 1,
        **look,
        "app": {k: app[k] for k in _APP_KEYS if k in app},
        "budget": {k: bud[k] for k in ("annual_cap_tco2e", "monthly_weights") if k in bud},
    }


def apply_import(cfg: dict[str, Any], data: Any) -> tuple[dict[str, Any], list[str]]:
    """New config with the sections of an exported file. Raises ValueError on a file that is not one."""
    if not isinstance(data, dict) or data.get(SETTINGS_FILE_TAG) != 1:
        raise ValueError("not a CarbonSync settings file")
    new = copy.deepcopy(cfg)
    done: list[str] = []
    look = settings({k: data.get(k) for k in DEFAULTS})
    for section in DEFAULTS:
        if isinstance(data.get(section), dict):
            new[section] = look[section]
            done.append(section)
    app = data.get("app")
    if isinstance(app, dict):
        target = new.setdefault("app", {})
        if isinstance(app.get("title"), str) and app["title"].strip():
            target["title"] = app["title"].strip()[:60]
        if app.get("language") in ("fr", "en"):
            target["language"] = app["language"]
        if app.get("data_mode") in ("synthetic", "production"):
            target["data_mode"] = app["data_mode"]
        done.append("app")
    bud = data.get("budget")
    if isinstance(bud, dict):
        target = new.setdefault("budget", {})
        cap = bud.get("annual_cap_tco2e")
        if isinstance(cap, (int, float)) and 500 <= cap <= 20000:
            target["annual_cap_tco2e"] = target["annual_target_tco2e"] = float(cap)
        weights = bud.get("monthly_weights")
        if isinstance(weights, dict) and len(weights) == 12:
            try:
                w = {int(m): float(v) for m, v in weights.items()}
            except (TypeError, ValueError):
                w = {}
            if sorted(w) == list(range(1, 13)) and all(v >= 0 for v in w.values()) and abs(sum(w.values()) - 1) <= 0.01:
                target["monthly_weights"] = w
        done.append("budget")
    if not done:
        raise ValueError("no settings in the file")
    return new, done


def reset_look(cfg: dict[str, Any]) -> dict[str, Any]:
    """Appearance, layout and reports back to the defaults (budget, data and AI settings unchanged)."""
    new = copy.deepcopy(cfg)
    for section, values in DEFAULTS.items():
        new[section] = copy.deepcopy(values)
    return new
