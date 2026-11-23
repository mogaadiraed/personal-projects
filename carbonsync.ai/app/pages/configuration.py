"""Settings – appearance, layout, target & budget, inventory & breakdown, factors, reports & alerts, users, system,
password.

Every save writes config.yaml with a timestamped audit copy (data/config_history/). The appearance
also rewrites the theme of .streamlit/config.toml, which Streamlit applies live. The tabs follow the
signed-in role: viewers get the Password tab; editors also get the target, inventory, factors and reports tabs;
administrators get all.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import date

import pandas as pd
import streamlit as st

from app.core import auth
from app.core.i18n import tr
from app.data.loader import get_project_root, load_config, save_config
from app.ui import appearance as ap
from app.ui import charts as ch
from app.ui import components as ui
from app.ui.format import date_label, num

_WIDGET_PREFIXES = ("ap_", "lay_", "rep_", "cfg_", "tg_", "s2_", "s3_", "bk_", "al_", "rt_")

_PREVIEW_CSS = """<style>
.cs-pv-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.cs-pv { border-radius: 12px; padding: 12px; border: 1px solid rgba(128,128,128,.25); }
.cs-pv-mode { font-size: .7rem; font-weight: 600; opacity: .7; margin-bottom: 8px; }
.cs-pv-card { padding: 10px 12px; }
.cs-pv-bars { display: flex; align-items: flex-end; gap: 4px; height: 42px; margin: 8px 0 10px; }
.cs-pv-bars span { flex: 1; border-radius: 3px 3px 0 0; }
.cs-pv-btn { display: inline-block; color: #fff; font-size: .75rem; font-weight: 600; padding: 4px 12px; }
.cs-pv-chip { display: inline-block; font-size: .68rem; padding: 1px 8px; border-radius: 999px; margin-left: 6px;
              border: 1px solid rgba(128,128,128,.35); }
@media (max-width: 640px) { .cs-pv-row { grid-template-columns: 1fr; } }
</style>"""


def _save(cfg: dict, *, theme: bool = False, reset_widgets: bool = False) -> None:
    backup = save_config(cfg)
    st.cache_data.clear()
    if theme and ap.sync_theme(ap.settings(cfg)):
        time.sleep(0.7)   # Streamlit's watcher reloads .streamlit/config.toml before the rerun
    if reset_widgets:
        for k in [k for k in st.session_state if isinstance(k, str) and k.startswith(_WIDGET_PREFIXES)]:
            del st.session_state[k]
    st.toast(tr(f"Enregistré · sauvegarde {backup.name}", f"Saved · backup {backup.name}"), icon=":material/check:")


def _seg(label: str, options: dict[str, str], current: str, key: str) -> str:
    choice = st.segmented_control(label, list(options), default=current, format_func=options.get, key=key, width="stretch")
    return choice or current


def render() -> None:
    cfg = copy.deepcopy(load_config())  # never mutate the cached object
    look = ap.settings(cfg)
    ui.page_header(
        tr("Réglages", "Settings"),
        "Configuration",
        tr("Chaque enregistrement crée une copie d'audit horodatée.", "Every save creates a timestamped audit copy."),
    )
    from app.pages import settings_targets, settings_users

    every = [
        ("settings_appearance", ":material/palette: " + tr("Apparence", "Appearance"), lambda: _appearance(cfg, look)),
        ("settings_layout", ":material/dashboard_customize: " + tr("Mise en page", "Layout"), lambda: _layout(cfg, look)),
        ("settings_targets", ":material/flag: " + tr("Objectif et budget", "Target & budget"),
         lambda: settings_targets.target_tab(cfg, _save)),
        ("settings_inventory", ":material/inventory_2: " + tr("Inventaire et répartition", "Inventory & breakdown"),
         lambda: settings_targets.inventory_tab(cfg, _save)),
        ("settings_targets", ":material/science: " + tr("Facteurs", "Factors"), lambda: _factors(cfg)),
        ("settings_reports", ":material/description: " + tr("Rapports et alertes", "Reports & alerts"),
         lambda: _reports(cfg, look)),
        ("users", ":material/group: " + tr("Utilisateurs", "Users"), lambda: settings_users.users_tab(cfg)),
        ("settings_system", ":material/tune: " + tr("Système", "System"), lambda: _system(cfg)),
        ("account", ":material/key: " + tr("Mot de passe", "Password"), lambda: settings_users.password_tab(cfg)),
    ]
    local = bool(auth.current().get("local"))           # login off: no account, no password
    allowed = [(label, fn) for action, label, fn in every if auth.can(action) and not (action == "account" and local)]
    if not allowed:
        ui.empty_state(tr("Aucun réglage pour votre rôle.", "No settings for your role."))
        return
    tabs = st.tabs([label for label, _fn in allowed])
    for tab, (_label, fn) in zip(tabs, allowed):
        with tab:
            fn()


# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------
def _appearance(cfg: dict, look: dict) -> None:
    a = look["appearance"]
    left, right = st.columns([1.15, 1], gap="medium")
    with left, ui.card(tr("Style", "Style"), tr("Appliqué à toutes les pages, sans redémarrage.",
                                                "Applied to every page, no restart."), stretch=True):
        names = {k: tr(*p["name"]) for k, p in ap.PALETTES.items()} | {"custom": tr("Personnalisée", "Custom")}
        palette = _seg(tr("Palette", "Palette"), names, a["palette"], "ap_palette")
        accent = a["custom_accent"]
        if palette == "custom":
            accent = st.color_picker(tr("Couleur d'accent", "Accent colour"), a["custom_accent"], key="ap_accent")
        c1, c2 = st.columns(2, gap="small")
        with c1:
            style = _seg(tr("Effet", "Effect"), {"glass": tr("Verre", "Glass"), "flat": tr("Plat", "Flat")},
                         a["style"], "ap_style")
            text = _seg(tr("Taille du texte", "Text size"), {"s": "S", "m": "M", "l": "L"}, a["text_size"], "ap_text")
            corners = _seg(tr("Angles", "Corners"), {"rounded": tr("Arrondis", "Rounded"), "sharp": tr("Nets", "Sharp")},
                           a["corners"], "ap_corners")
        with c2:
            mode = _seg(tr("Mode", "Mode"), {"auto": "Auto", "light": tr("Clair", "Light"), "dark": tr("Sombre", "Dark")},
                        a["mode"], "ap_mode")
            density = _seg(tr("Densité", "Density"), {"comfortable": tr("Confortable", "Comfortable"),
                                                      "compact": tr("Compacte", "Compact")}, a["density"], "ap_density")
            st.html('<div style="height:.35rem"></div>')
            anim = st.toggle(tr("Animations", "Animations"), a["animations"], key="ap_anim")
            wall = st.toggle(tr("Fond animé", "Animated background"), a["animated_background"], key="ap_wall",
                             disabled=style != "glass" or not anim,
                             help=tr("Effet Verre : les reflets de couleur du fond bougent lentement.",
                                     "Glass effect: the colour glows behind the page drift slowly."))
    draft = ap.settings({**cfg, "appearance": {"palette": palette, "custom_accent": accent, "style": style, "mode": mode,
                                               "text_size": text, "density": density, "corners": corners,
                                               "animations": anim, "animated_background": wall}})
    with right, ui.card(tr("Aperçu", "Preview"), tr("Mode sombre et mode clair.", "Dark and light mode."), stretch=True):
        st.html(_PREVIEW_CSS + _preview(draft))
        c = ap.checks(ap.palette(draft))
        ok = all(c[k] >= v for k, v in ap.MIN_CHECKS.items())
        text_c = min(c["dark_text"], c["light_text"])
        accent_c = min(c["dark_accent"], c["light_accent"])
        button_c = min(c["dark_button"], c["light_button"])
        ui.chip_row([(tr(f"Texte {num(text_c, 0)}:1", f"Text {num(text_c, 0)}:1"), "accent" if ok else "warn"),
                     (f"Accent {num(accent_c, 1)}:1", "accent" if ok else "warn"),
                     (tr(f"Boutons {num(button_c, 1)}:1", f"Buttons {num(button_c, 1)}:1"), "accent" if ok else "warn")])
        st.caption(tr("Contrastes vérifiés : lisible dans les deux modes. Les couleurs des graphiques restent "
                      "fixes (Scope 1 orange, Scope 2 bleu…).",
                      "Contrast checked: readable in both modes. Chart colours stay fixed "
                      "(Scope 1 orange, Scope 2 blue…)."))
    changed = draft["appearance"] != look["appearance"]
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button(tr("Appliquer", "Apply"), type="primary", icon=":material/check:", disabled=not changed,
                 width="stretch", key="ap_apply"):
        cfg["appearance"] = draft["appearance"]
        _save(cfg, theme=True)
        st.rerun()
    if b2.button(tr("Par défaut", "Defaults"), icon=":material/restart_alt:", width="stretch", key="ap_reset",
                 disabled=look["appearance"] == ap.DEFAULTS["appearance"]):
        cfg["appearance"] = copy.deepcopy(ap.DEFAULTS["appearance"])
        _save(cfg, theme=True, reset_widgets=True)
        st.rerun()


def _preview(look: dict) -> str:
    pal, a = ap.palette(look), look["appearance"]
    glass, sharp = a["style"] == "glass", a["corners"] == "sharp"
    radius, btn = ("5px", "4px") if sharp else ("12px", "8px")
    series = [ch.BLUE, ch.ORANGE, ch.AQUA, ch.YELLOW, ch.MAGENTA]
    panels = []
    for kind, label in (("dark", tr("Sombre", "Dark")), ("light", tr("Clair", "Light"))):
        p, g = pal[kind], ap.GLASS[kind]
        g1, g2 = pal["glow"][kind]
        bg = (f"radial-gradient(260px 160px at 0% 0%, {ap.rgba(g1, min(0.4, g['glow'] * 1.6))}, transparent 72%),"
              f"radial-gradient(240px 160px at 100% 0%, {ap.rgba(g2, min(0.3, g['glow2'] * 1.6))}, transparent 72%),"
              f"{p['bg']}") if glass else p["bg"]
        card = ap.rgba(p["card"], g["card"]) if glass else p["bg"]
        line = "rgba(255,255,255,.10)" if kind == "dark" else "rgba(15,23,42,.12)"
        shadow = ("0 6px 18px rgba(0,0,0,.30)" if kind == "dark" else "0 6px 16px rgba(15,23,42,.08)") if glass else "none"
        colors = [ch.DARK_STEPS.get(c, c) for c in series] if kind == "dark" else series
        bars = "".join(f'<span style="height:{h}%;background:{c}"></span>'
                       for h, c in zip((58, 82, 64, 94, 72), colors, strict=True))
        panels.append(
            f'<div class="cs-pv" style="background:{bg};color:{p["text"]}">'
            f'<div class="cs-pv-mode">{label}</div>'
            f'<div class="cs-pv-card" style="background:{card};border:1px solid {line};border-radius:{radius};box-shadow:{shadow}">'
            f'<div style="color:{p["primary"]};font-size:.66rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase">'
            f'{tr("Émissions", "Emissions")}</div>'
            f'<div style="font-size:1.2rem;font-weight:650;letter-spacing:-.02em">1 284 '
            f'<span style="font-size:.72rem;font-weight:500;opacity:.65">tCO₂e</span>'
            f'<span class="cs-pv-chip">{tr("12 mois", "12 months")}</span></div>'
            f'<div class="cs-pv-bars">{bars}</div>'
            f'<span class="cs-pv-btn" style="background:{p["primary"]};border-radius:{btn}">{tr("Action", "Action")}</span>'
            "</div></div>")
    return '<div class="cs-pv-row">' + "".join(panels) + "</div>"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def _page_labels() -> dict[str, str]:
    return {path: tr(fr, en) for _s, _e, pages in ap.MENU for path, fr, en, _i in pages}


def _layout(cfg: dict, look: dict) -> None:
    lay = look["layout"]
    labels = _page_labels()
    hideable = [p for p in ap.PAGE_PATHS if p not in ap.PROTECTED_PAGES]
    left, right = st.columns(2, gap="medium")
    with left, ui.card(tr("Menu", "Menu"), tr("Vue d'ensemble, Assistant et Configuration restent toujours affichées.",
                                              "Overview, Assistant and Configuration are always shown."), stretch=True):
        shown = st.pills(tr("Pages affichées", "Pages shown"), hideable, selection_mode="multi",
                         default=[p for p in hideable if p not in lay["hidden_pages"]], format_func=labels.get,
                         key="lay_pages")
        visible = [p for p in ap.PAGE_PATHS if p in ap.PROTECTED_PAGES or p in shown]
        start = lay["start_page"] if lay["start_page"] in visible else "overview"
        start = st.selectbox(tr("Page d'accueil", "Start page"), visible, index=visible.index(start),
                             format_func=labels.get, key="lay_start")
    with right, ui.card(tr("Affichage", "Display"), tr("Largeur, pied de page et assistant flottant.",
                                                       "Width, footer and floating assistant."), stretch=True):
        width = _seg(tr("Largeur des pages", "Page width"), {"standard": "Standard", "full": tr("Pleine largeur", "Full width")},
                     lay["page_width"], "lay_width")
        fab = st.toggle(tr("Bouton assistant flottant", "Floating assistant button"), lay["assistant_button"], key="lay_fab")
        side = _seg(tr("Position du bouton", "Button position"), {"right": tr("À droite", "Right"), "left": tr("À gauche", "Left")},
                    lay["assistant_side"], "lay_side")
        footer = st.toggle(tr("Pied de page", "Footer"), lay["footer"], key="lay_footer")
    new = {"start_page": start, "hidden_pages": sorted(set(hideable) - set(shown)), "assistant_button": fab,
           "assistant_side": side, "page_width": width, "footer": footer}
    if st.button(tr("Enregistrer la mise en page", "Save layout"), type="primary", icon=":material/check:",
                 disabled=ap.settings({"layout": new})["layout"] == lay, key="lay_save"):
        cfg["layout"] = ap.settings({"layout": new})["layout"]
        _save(cfg)
        st.rerun()


# ---------------------------------------------------------------------------
# Emission factors (read-only: changing them changes every past figure)
# ---------------------------------------------------------------------------
def _factors(cfg: dict) -> None:
    ef = cfg.get("emission_factors", {})
    review = str(cfg.get("governance", {}).get("factors_review_date", ""))
    try:
        reviewed = date.fromisoformat(review)
        age = (date.today() - reviewed).days
    except ValueError:
        reviewed, age = None, None
    rows = [
        (tr("Électricité réseau", "Grid electricity"), "kWh", "Scope 2", ef.get("electricity_kgco2_per_kwh"), "Electricity Maps 2025 · STEG"),
        (tr("Fioul lourd", "Heavy fuel oil"), "L", "Scope 1", ef.get("fuel_oil_kgco2_per_l"), "ADEME / IPCC"),
        (tr("Gazole logistique", "Logistics diesel"), "L", "Scope 1", ef.get("diesel_kgco2_per_l"), "Base Carbone ADEME"),
        (tr("Déchets solides", "Solid waste"), "kg", tr("Autre", "Other"), ef.get("waste_kgco2_per_kg"), tr("Forfait conventionnel", "Conventional default")),
        (tr("Eau de procédé", "Process water"), "m³", tr("Autre", "Other"), ef.get("water_kgco2_per_m3"), "SONEDE"),
    ]
    due = age is not None and age > 365
    ui.kpis([
        ui.Kpi(tr("Dernière revue", "Last review"), date_label(reviewed) if reviewed else "—"),
        ui.Kpi(tr("Ancienneté", "Age"), str(age) if age is not None else "—", tr("jours", "days"),
               delta=(tr("À revoir", "Review due") if due else tr("À jour", "Up to date")) if age is not None else "",
               tone="bad" if due else "good"),
        ui.Kpi(tr("Facteurs", "Factors"), str(len(rows)), tr("flux", "activities"), note="GHG Protocol"),
    ])
    with ui.card(tr("Facteurs d'émission", "Emission factors"),
                 tr("Lecture seule : les modifier change tous les chiffres passés. À changer dans config.yaml, puis "
                    "relancer le pipeline.", "Read-only: changing them changes every past figure. Change them in "
                    "config.yaml, then rerun the pipeline.")):
        st.dataframe(
            pd.DataFrame(rows, columns=[tr("Flux", "Activity"), tr("Unité", "Unit"), "Scope", "kgCO₂e / " + tr("unité", "unit"), "Source"]),
            hide_index=True, width="stretch",
        )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _reports(cfg: dict, look: dict) -> None:
    app_cfg = cfg.setdefault("app", {})
    left, right = st.columns(2, gap="medium")
    with left, ui.card(tr("Nom du site", "Site name"), tr("Couverture des rapports et titre de l'onglet du navigateur.",
                                                          "Report cover and browser tab title."), stretch=True):
        title = st.text_input(tr("Nom du site", "Site name"), app_cfg.get("title", "CarbonSync AI"), max_chars=60,
                              key="rep_title", label_visibility="collapsed").strip()
    with right, ui.card(tr("Langue des rapports", "Report language"), tr("Classeur Excel et rapport PDF.",
                                                                         "Excel workbook and PDF report."), stretch=True):
        rlang = _seg(tr("Langue", "Language"), {"ui": tr("Interface", "Interface"), "fr": "Français", "en": "English"},
                     look["reports"]["language"], "rep_lang")
    changed = (title and title != app_cfg.get("title")) or rlang != look["reports"]["language"]
    if st.button(tr("Enregistrer", "Save"), type="primary", icon=":material/check:", disabled=not changed, key="rep_save"):
        if title:
            app_cfg["title"] = title
        cfg["reports"] = {**look["reports"], "language": rlang}
        _save(cfg)
        st.rerun()

    from app.pages import settings_targets

    settings_targets.alerts_block(copy.deepcopy(load_config()), _save)


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
def _system(cfg: dict) -> None:
    app_cfg = cfg.setdefault("app", {})
    c1, c2 = st.columns(2, gap="medium")
    with c1, ui.card(tr("Langue par défaut", "Default language"), tr("À l'ouverture du tableau de bord.",
                                                                     "When the dashboard opens."), stretch=True):
        new_lang = st.radio("lang", ["fr", "en"], index=0 if app_cfg.get("language", "fr") == "fr" else 1,
                            format_func=lambda x: "Français" if x == "fr" else "English",
                            horizontal=True, label_visibility="collapsed", key="cfg_lang")
    with c2, ui.card(tr("Mode de données", "Data mode"), tr("Données de démonstration ou réelles.",
                                                            "Demo or real data."), stretch=True):
        new_mode = st.radio("mode", ["synthetic", "production"],
                            index=0 if app_cfg.get("data_mode", "synthetic") == "synthetic" else 1,
                            format_func=lambda x: tr("Synthétique", "Synthetic") if x == "synthetic" else tr("Production", "Production"),
                            horizontal=True, label_visibility="collapsed", key="cfg_mode")
    if st.button(tr("Enregistrer les préférences", "Save preferences"), type="primary", icon=":material/check:",
                 disabled=(new_lang, new_mode) == (app_cfg.get("language", "fr"), app_cfg.get("data_mode", "synthetic")),
                 key="cfg_prefs_save"):
        app_cfg["language"], app_cfg["data_mode"] = new_lang, new_mode
        st.session_state["lang"] = new_lang
        _save(cfg)
        st.rerun()

    from carbonsync.models.router import MODEL_LABELS, MODELS, router_settings

    fcfg, rs = cfg.get("forecast", {}), router_settings(cfg)
    ui.section(tr("Moteur de prévision", "Forecast engine"),
               tr("Le routeur choisit le modèle du pipeline et du mode « Auto ».",
                  "The router picks the model of the pipeline and of the 'Auto' mode."))
    ui.kpis([
        ui.Kpi(tr("Modèle du routeur", "Router model"), MODEL_LABELS[rs["model"]],
               note=tr(f"dès {rs['min_days']} j, sinon {MODEL_LABELS[rs['fallback']]}",
                       f"from {rs['min_days']} d, otherwise {MODEL_LABELS[rs['fallback']]}")),
        ui.Kpi(tr("Horizon", "Horizon"), str(fcfg.get("prediction_length", 183)), tr("jours", "days")),
        ui.Kpi(tr("Confiance", "Confidence"), f"{float(fcfg.get('confidence_level', 0.9)) * 100:.0f}%"),
        ui.Kpi(tr("Modèle de fondation", "Foundation model"), str(fcfg.get("chronos_model", "—")).split("/")[-1],
               note=str(fcfg.get("chronos_device", "cpu")).upper()),
    ])

    with ui.card(tr("Routeur et backtest", "Router and backtest"),
                 tr("Chronos-2 est le plus précis et le mieux calibré dans le backtest ; Prophet sert de repli.",
                    "Chronos-2 is the most accurate and best calibrated in the backtest; Prophet is the fallback.")):
        c1, c2, c3, c4 = st.columns(4)
        r_model = c1.selectbox(tr("Modèle", "Model"), list(MODELS), index=list(MODELS).index(rs["model"]),
                               format_func=MODEL_LABELS.get, key="rt_model")
        r_fb = c2.selectbox(tr("Repli", "Fallback"), list(MODELS), index=list(MODELS).index(rs["fallback"]),
                            format_func=MODEL_LABELS.get, key="rt_fallback")
        r_min = c3.number_input(tr("Historique minimal (jours)", "Minimum history (days)"), 0, 3650, rs["min_days"],
                                key="rt_min")
        bt = cfg.get("backtest", {})
        r_bt = c4.toggle(tr("Backtest après chaque dépôt", "Backtest after each upload"),
                         bool(bt.get("refresh_after_upload", True)), key="rt_bt")
        new_router = {"model": r_model, "fallback": r_fb, "min_days": int(r_min)}
        same = new_router == {k: rs[k] for k in new_router} and r_bt == bool(bt.get("refresh_after_upload", True))
        if st.button(tr("Enregistrer le routeur", "Save router"), icon=":material/check:", key="rt_save", disabled=same):
            new = copy.deepcopy(cfg)
            new["router"] = new_router
            new["backtest"] = {**bt, "refresh_after_upload": bool(r_bt)}
            _save(new)
            st.rerun()

    c1, c2 = st.columns(2, gap="medium")
    with c1, ui.card(tr("Exporter / importer", "Export / import"),
                     tr("Apparence, mise en page, rapports, budget et préférences (JSON, sans données ni clés).",
                        "Appearance, layout, reports, budget and preferences (JSON, no data, no keys)."), stretch=True):
        st.download_button(tr("Exporter les réglages", "Export settings"),
                           json.dumps(ap.export_settings(cfg), ensure_ascii=False, indent=2),
                           file_name="carbonsync_settings.json", mime="application/json", icon=":material/download:",
                           key="cfg_export", on_click="ignore")
        n = st.session_state.setdefault("_cfg_import_n", 0)
        up = st.file_uploader(tr("Importer", "Import"), type=["json"], key=f"cfg_import_{n}", label_visibility="collapsed")
        if up is not None:
            try:
                new_cfg, sections = ap.apply_import(cfg, json.loads(up.getvalue().decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(tr("Fichier non reconnu : ce n'est pas un export de réglages CarbonSync.",
                            "File not recognised: not a CarbonSync settings export.") + f" ({exc})")
            else:
                st.caption(tr("Sections : ", "Sections: ") + ", ".join(sections))
                if st.button(tr("Appliquer l'import", "Apply import"), type="primary", icon=":material/upload:",
                             key="cfg_import_apply"):
                    st.session_state["_cfg_import_n"] = n + 1
                    if new_cfg.get("app", {}).get("language") in ("fr", "en"):
                        st.session_state["lang"] = new_cfg["app"]["language"]
                    _save(new_cfg, theme=True, reset_widgets=True)
                    st.rerun()
    with c2, ui.card(tr("Valeurs par défaut", "Defaults"),
                     tr("Apparence, mise en page et rapports reviennent aux valeurs d'origine. Budget, données et IA "
                        "inchangés.", "Appearance, layout and reports go back to the original values. Budget, data "
                        "and AI unchanged."), stretch=True, align="distribute"):
        confirm = st.checkbox(tr("Je confirme", "I confirm"), key="cfg_reset_ok")
        if st.button(tr("Rétablir les valeurs par défaut", "Reset to defaults"), icon=":material/restart_alt:",
                     disabled=not confirm, key="cfg_reset"):
            _save(ap.reset_look(cfg), theme=True, reset_widgets=True)
            st.rerun()

    history = sorted((get_project_root() / "data" / "config_history").glob("config_*.yaml"), reverse=True)
    if history:
        with st.expander(tr(f"Historique des modifications ({len(history)})", f"Change history ({len(history)})")):
            st.dataframe(pd.DataFrame({tr("Copie d'audit", "Audit copy"): [p.name for p in history[:20]]}),
                         hide_index=True, width="stretch")
