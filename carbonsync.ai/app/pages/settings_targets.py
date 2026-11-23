"""Configuration tabs for editors: target and budget, inventory (Scope 2, Scope 3) and breakdown keys.

Each block saves only its own section of config.yaml (with the usual audit copy). Figures follow at once:
the plan, the inventory and the breakdown are computed from config.yaml on every page view.
"""
from __future__ import annotations

import copy
from datetime import date

import pandas as pd
import streamlit as st

from app.core.i18n import tr
from app.data.loader import load_emissions_daily
from app.ui import components as ui
from app.ui.format import month_long, num
from carbonsync import targets
from carbonsync.inventory import breakdown as bd
from carbonsync.inventory import scope2, scope3


# ---------------------------------------------------------------------------
# Target & budget
# ---------------------------------------------------------------------------
def target_tab(cfg: dict, save) -> None:
    df = load_emissions_daily()
    t = targets.target_settings(cfg)
    b = targets.budget_settings(cfg)
    tot = targets.annual_totals(df) if not df.empty else pd.DataFrame(columns=["year", "complete"])
    complete = [int(y) for y in tot.loc[tot["complete"], "year"]] if not tot.empty else []
    years = sorted(set(complete) | {t["base_year"]})

    left, right = st.columns(2, gap="medium")
    with left, ui.card(tr("Objectif de réduction", "Reduction target"),
                       tr("Périmètre du budget : Scope 1 + Scope 2 localisation + déchets et eau.",
                          "Budget perimeter: Scope 1 + Scope 2 location-based + waste and water."), stretch=True):
        base = st.selectbox(tr("Année de référence (année complète)", "Base year (complete year)"), years,
                            index=years.index(t["base_year"]), key="tg_base")
        c1, c2 = st.columns(2)
        target_year = c1.number_input(tr("Année cible", "Target year"), base + 1, 2100, max(t["target_year"], base + 1),
                                      1, key="tg_year")
        red = c2.number_input(tr("Réduction (%)", "Reduction (%)"), 0.0, 100.0, float(t["reduction_pct"]), 1.0,
                              key="tg_red")
        if base not in complete:
            st.warning(tr("Cette année n'est pas complète dans les données : le plafond fixe sera utilisé.",
                          "This year is not complete in the data: the fixed cap will be used."))
    with right, ui.card(tr("Plafond et poids mensuels", "Cap and monthly weights"), stretch=True):
        cap_src = st.segmented_control(tr("Plafond de chaque année", "Each year's cap"), list(targets.CAP_SOURCES),
                                       default=b["cap_source"], key="tg_cap_src", width="stretch",
                                       format_func={"path": tr("Lu sur la trajectoire", "Read on the path"),
                                                    "manual": tr("Fixe (saisi)", "Fixed (entered)")}.get) or b["cap_source"]
        manual_cap = st.number_input(tr("Plafond fixe (tCO₂e/an)", "Fixed cap (tCO₂e/yr)"), 100.0, 50000.0,
                                     float(b["annual_cap_tco2e"]), 50.0, key="tg_cap", disabled=cap_src != "manual")
        w_src = st.segmented_control(tr("Poids mensuels", "Monthly weights"), list(targets.WEIGHT_SOURCES),
                                     default=b["weights_source"], key="tg_w_src", width="stretch",
                                     format_func={"seasonal": tr("Profil saisonnier", "Seasonal profile"),
                                                  "manual": tr("Saisis à la main", "Entered by hand")}.get) or b["weights_source"]
        w_years = st.slider(tr("Années complètes pour le profil", "Complete years for the profile"), 1, 5,
                            int(b["weights_years"]), key="tg_w_years", disabled=w_src != "seasonal")

    manual_w = b["monthly_weights"]
    ok = True
    if w_src == "manual":
        with ui.card(tr("Poids saisis", "Entered weights"), tr("Total attendu : 1,00", "Expected total: 1.00")):
            weights = pd.DataFrame({"m": range(1, 13), tr("Mois", "Month"): [month_long(m) for m in range(1, 13)],
                                    tr("Poids", "Weight"): [manual_w.get(m, 1 / 12) for m in range(1, 13)]})
            edited = st.data_editor(weights, hide_index=True, width="stretch", disabled=["m", tr("Mois", "Month")],
                                    column_config={"m": None, tr("Poids", "Weight"): st.column_config.NumberColumn(
                                        min_value=0.0, max_value=1.0, step=0.001, format="%.3f")}, key="tg_weights")
            total = float(edited[tr("Poids", "Weight")].sum())
            ok = abs(total - 1.0) <= 0.01
            st.caption(tr(f"Total : {num(total, 3)}", f"Total: {num(total, 3)}")
                       + ("" if ok else tr(" — doit être ≈ 1,00", " — must be ≈ 1.00")))
            manual_w = {int(r["m"]): float(r[tr("Poids", "Weight")]) for _, r in edited.iterrows()}

    new = copy.deepcopy(cfg)
    new["targets"] = {**(cfg.get("targets") or {}), "base_year": int(base), "target_year": int(target_year),
                      "reduction_pct": float(red)}
    new["budget"] = {**(cfg.get("budget") or {}), "cap_source": cap_src, "weights_source": w_src,
                     "weights_years": int(w_years), "annual_cap_tco2e": float(manual_cap),
                     "annual_target_tco2e": float(manual_cap), "monthly_weights": manual_w}
    if not df.empty:
        p = targets.plan(new, df)
        year = (df["date"].max() + pd.Timedelta(days=1)).year
        st.info(p.describe(tr("fr", "en")) + " " + tr(f"Plafond {year} : {num(p.cap(year), 0)} t.",
                                                    f"{year} cap: {num(p.cap(year), 0)} t."), icon=":material/flag:")
    changed = (targets.target_settings(new) != targets.target_settings(cfg)
               or targets.budget_settings(new) != targets.budget_settings(cfg))
    if st.button(tr("Enregistrer l'objectif et le budget", "Save target and budget"), type="primary",
                 icon=":material/check:", disabled=not (ok and changed), key="tg_save"):
        save(new)
        st.rerun()


# ---------------------------------------------------------------------------
# Inventory (Scope 2 market-based, Scope 3 screening) and breakdown keys
# ---------------------------------------------------------------------------
def _d(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    try:
        return pd.Timestamp(v).date()
    except (ValueError, TypeError):
        return None


def inventory_tab(cfg: dict, save) -> None:
    sub = st.segmented_control("inv_section", ["s2", "s3", "bd"], default="s2", key="inv_sub",
                               label_visibility="collapsed",
                               format_func={"s2": tr("Scope 2 méthode marché", "Market-based Scope 2"),
                                            "s3": tr("Scope 3 (hypothèses)", "Scope 3 (assumptions)"),
                                            "bd": tr("Répartition (clés)", "Breakdown (keys)")}.get) or "s2"
    {"s2": _scope2, "s3": _scope3, "bd": _breakdown}[sub](cfg, save)


def _scope2(cfg: dict, save) -> None:
    s = scope2.settings(cfg)
    with ui.card(tr("Mix résiduel", "Residual mix"),
                 tr("Facteur des kWh non couverts par un contrat (méthode marché).",
                    "Factor of the kWh no contract covers (market-based).")):
        c1, c2 = st.columns([1, 3])
        rm = c1.number_input("kgCO₂e/kWh", 0.0, 2.0, float(s["residual_mix_kgco2_per_kwh"]), 0.001, format="%.3f",
                             key="s2_rm")
        src = c2.text_area(tr("Source", "Source"), s["residual_mix_source"], height=80, key="s2_src")
    with ui.card(tr("Instruments contractuels", "Contractual instruments"),
                 tr("PPA, offre verte ou certificats : une ligne par contrat. Part des kWh achetés OU MWh par an.",
                    "PPA, green tariff or certificates: one row per contract. Share of the kWh bought OR MWh per year.")):
        rows = pd.DataFrame([{
            "name": i["name"], "kind": i["kind"], "start": _d(i["start"]), "end": _d(i["end"]),
            "share_pct": None if i["share_of_grid_kwh"] is None else i["share_of_grid_kwh"] * 100,
            "mwh_per_year": i["mwh_per_year"], "kgco2_per_kwh": i["kgco2_per_kwh"], "enabled": i["enabled"],
        } for i in s["instruments"]], columns=["name", "kind", "start", "end", "share_pct", "mwh_per_year",
                                               "kgco2_per_kwh", "enabled"])
        rows["start"] = pd.to_datetime(rows["start"])
        rows["end"] = pd.to_datetime(rows["end"])
        edited = st.data_editor(rows, num_rows="dynamic", hide_index=True, width="stretch", key="s2_inst", column_config={
            "name": st.column_config.TextColumn(tr("Nom", "Name"), required=True),
            "kind": st.column_config.SelectboxColumn(tr("Type", "Kind"), options=list(scope2.KINDS), required=True,
                                                     default="green_tariff"),
            "start": st.column_config.DateColumn(tr("Début", "Start")),
            "end": st.column_config.DateColumn(tr("Fin", "End")),
            "share_pct": st.column_config.NumberColumn(tr("Part des kWh (%)", "Share of kWh (%)"), min_value=0.0,
                                                       max_value=100.0, format="%.0f"),
            "mwh_per_year": st.column_config.NumberColumn(tr("MWh / an", "MWh / yr"), min_value=0.0, format="%.0f"),
            "kgco2_per_kwh": st.column_config.NumberColumn("kgCO₂e/kWh", min_value=0.0, max_value=2.0, format="%.3f",
                                                           default=0.0),
            "enabled": st.column_config.CheckboxColumn(tr("Actif", "On"), default=True),
        })
    instruments = []
    for _, r in edited.iterrows():
        if not str(r.get("name") or "").strip():
            continue
        share = r.get("share_pct")
        mwh = r.get("mwh_per_year")
        instruments.append({
            "name": str(r["name"]).strip(), "kind": r.get("kind") if r.get("kind") in scope2.KINDS else "certificates",
            "start": _d(r.get("start")).isoformat() if _d(r.get("start")) else None,
            "end": _d(r.get("end")).isoformat() if _d(r.get("end")) else None,
            "share_of_grid_kwh": None if share is None or pd.isna(share) else round(float(share) / 100, 4),
            "mwh_per_year": None if mwh is None or pd.isna(mwh) else float(mwh),
            "kgco2_per_kwh": 0.0 if pd.isna(r.get("kgco2_per_kwh")) else float(r.get("kgco2_per_kwh") or 0.0),
            "enabled": bool(r.get("enabled")) if not pd.isna(r.get("enabled")) else True,
        })
    new = copy.deepcopy(cfg)
    new["scope2"] = {**(cfg.get("scope2") or {}), "residual_mix_kgco2_per_kwh": float(rm),
                     "residual_mix_source": src.strip(), "instruments": instruments}
    df = load_emissions_daily()
    if not df.empty:
        last = df["date"].max()
        a = scope2.totals(df, cfg, last - pd.Timedelta(days=364), last)
        b = scope2.totals(df, new, last - pd.Timedelta(days=364), last)
        st.caption(tr(f"12 derniers mois, méthode marché : {num(a['market_t'], 0)} t actuellement → {num(b['market_t'], 0)} t "
                      f"avec ces réglages (localisation : {num(b['location_t'], 0)} t).",
                      f"Last 12 months, market-based: {num(a['market_t'], 0)} t now → {num(b['market_t'], 0)} t with "
                      f"these settings (location-based: {num(b['location_t'], 0)} t)."))
    if st.button(tr("Enregistrer le Scope 2", "Save Scope 2"), type="primary", icon=":material/check:",
                 disabled=new["scope2"] == cfg.get("scope2"), key="s2_save"):
        save(new)
        st.rerun()


def _scope3(cfg: dict, save) -> None:
    s = scope3.settings(cfg)
    with ui.card(tr("Matières achetées (catégorie 1)", "Purchased materials (category 1)"),
                 tr("Masse par unité produite × facteur ; source de chaque facteur.",
                    "Mass per unit produced × factor; source of each factor.")):
        mats = st.data_editor(pd.DataFrame(s["purchased_materials"], columns=["name_fr", "name_en", "kg_per_unit",
                                                                                "kgco2_per_kg", "source"]),
                              num_rows="dynamic", hide_index=True, width="stretch", key="s3_mats", column_config={
            "name_fr": st.column_config.TextColumn("Nom (FR)", required=True),
            "name_en": st.column_config.TextColumn("Name (EN)", required=True),
            "kg_per_unit": st.column_config.NumberColumn(tr("kg / unité", "kg / unit"), min_value=0.0, format="%.3f"),
            "kgco2_per_kg": st.column_config.NumberColumn("kgCO₂e/kg", min_value=0.0, format="%.2f"),
            "source": st.column_config.TextColumn("Source", width="large")})
    c = s["commuting"]
    fe = s["fuel_energy"]
    left, right = st.columns(2, gap="medium")
    with left, ui.card(tr("Trajets du personnel (catégorie 7)", "Staff commuting (category 7)"), stretch=True):
        c1, c2, c3 = st.columns(3)
        emp = c1.number_input(tr("Salariés", "Staff"), 0, 100000, int(c["employees"]), 10, key="s3_emp")
        km = c2.number_input(tr("km aller-retour", "Round trip km"), 0.0, 500.0, float(c["round_trip_km"]), 1.0, key="s3_km")
        att = c3.number_input(tr("Présence", "Attendance"), 0.0, 1.0, float(c.get("attendance", 1.0)), 0.05, key="s3_att")
        modes = st.data_editor(pd.DataFrame(c["modes"], columns=["mode_fr", "mode_en", "share", "kgco2_per_pkm"]),
                               num_rows="dynamic", hide_index=True, width="stretch", key="s3_modes", column_config={
            "mode_fr": "Mode (FR)", "mode_en": "Mode (EN)",
            "share": st.column_config.NumberColumn(tr("Part", "Share"), min_value=0.0, max_value=1.0, format="%.2f"),
            "kgco2_per_pkm": st.column_config.NumberColumn("kgCO₂e/km", min_value=0.0, format="%.3f")})
    with right, ui.card(tr("Énergie amont et transport (catégories 3, 4, 9)", "Upstream energy and transport (categories 3, 4, 9)"),
                        stretch=True):
        c1, c2 = st.columns(2)
        wtt = c1.number_input(tr("Amont combustibles (kgCO₂e/L)", "Fuel upstream (kgCO₂e/L)"), 0.0, 5.0,
                              float(fe["fuels_wtt_kgco2_per_l"]), 0.01, key="s3_wtt")
        up = c2.number_input(tr("Amont électricité (kgCO₂e/kWh)", "Electricity upstream (kgCO₂e/kWh)"), 0.0, 1.0,
                             float(fe["electricity_upstream_kgco2_per_kwh"]), 0.005, format="%.3f", key="s3_up")
        out_kg = st.number_input(tr("Produit expédié (kg / unité)", "Product shipped (kg / unit)"), 0.0, 100.0,
                                 float(s["transport_outbound"]["kg_per_unit"]), 0.1, key="s3_out")
        st.caption(tr("Distances et modes de transport : config.yaml → scope3 → transport_inbound / transport_outbound.",
                      "Transport distances and modes: config.yaml → scope3 → transport_inbound / transport_outbound."))
    with ui.card(tr("Catégories exclues", "Excluded categories"), tr("Motif de chaque exclusion.", "Reason for each exclusion.")):
        excl = st.data_editor(pd.DataFrame([{"category": k, "name": tr(*scope3.CATEGORIES[k]), "reason": v}
                                            for k, v in sorted(s["excluded"].items())]),
                              hide_index=True, width="stretch", disabled=["category", "name"], key="s3_excl",
                              column_config={"category": tr("Cat.", "Cat."), "name": tr("Catégorie", "Category"),
                                             "reason": st.column_config.TextColumn(tr("Motif", "Reason"), width="large")})
    new = copy.deepcopy(cfg)
    s3 = dict(cfg.get("scope3") or {})
    s3["purchased_materials"] = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in mats.to_dict("records")
                                 if str(r.get("name_fr") or r.get("name_en") or "").strip()]
    s3["commuting"] = {**s["commuting"], "employees": int(emp), "round_trip_km": float(km), "attendance": float(att),
                       "modes": [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in modes.to_dict("records")
                                 if str(r.get("mode_fr") or r.get("mode_en") or "").strip()]}
    s3["fuel_energy"] = {**s["fuel_energy"], "fuels_wtt_kgco2_per_l": float(wtt),
                         "electricity_upstream_kgco2_per_kwh": float(up)}
    s3["transport_outbound"] = {**s["transport_outbound"], "kg_per_unit": float(out_kg)}
    s3["transport_inbound"] = s["transport_inbound"]
    s3["excluded"] = {int(r["category"]): str(r["reason"] or "") for _, r in excl.iterrows()}
    new["scope3"] = s3
    share_total = sum(float(m.get("share") or 0) for m in s3["commuting"]["modes"])
    ok = abs(share_total - 1) <= 0.01 or not s3["commuting"]["modes"]
    if not ok:
        st.warning(tr(f"Parts des modes de transport : total {num(share_total, 2)}, doit être 1.",
                      f"Commuting mode shares total {num(share_total, 2)}, must be 1."))
    df = load_emissions_daily()
    if not df.empty and ok:
        last = df["date"].max()
        a = scope3.summary(df, cfg, last - pd.Timedelta(days=364), last)["total_t"]
        b = scope3.summary(df, new, last - pd.Timedelta(days=364), last)["total_t"]
        st.caption(tr(f"Scope 3 sur 12 mois : {num(a, 0)} t actuellement → {num(b, 0)} t avec ces hypothèses.",
                      f"Scope 3 over 12 months: {num(a, 0)} t now → {num(b, 0)} t with these assumptions."))
    if st.button(tr("Enregistrer les hypothèses Scope 3", "Save Scope 3 assumptions"), type="primary",
                 icon=":material/check:", disabled=not ok or new["scope3"] == cfg.get("scope3"), key="s3_save"):
        save(new)
        st.rerun()


def _breakdown(cfg: dict, save) -> None:
    s = bd.settings(cfg)
    with ui.card(tr("Zones", "Zones"), tr("Code, noms et responsable de chaque zone.", "Code, names and owner of each zone.")):
        zones = st.data_editor(pd.DataFrame([{"code": k, "fr": v.get("fr", k), "en": v.get("en", k), "owner": v.get("owner", "")}
                                             for k, v in s["zones"].items()]),
                               num_rows="dynamic", hide_index=True, width="stretch", key="bk_zones_ed", column_config={
            "code": st.column_config.TextColumn(tr("Code", "Code"), required=True, max_chars=8),
            "fr": "Nom (FR)", "en": "Name (EN)", "owner": tr("Responsable", "Owner")})
    zone_map = {str(r["code"]).strip(): {"fr": r["fr"] or r["code"], "en": r["en"] or r["code"], "owner": r["owner"] or ""}
                for _, r in zones.iterrows() if str(r.get("code") or "").strip()}
    src = st.selectbox(tr("Compteur à répartir", "Meter to split"), list(bd.SOURCES), key="bk_src",
                       format_func=lambda k: tr(bd.SOURCES[k]["fr"], bd.SOURCES[k]["en"]))
    with ui.card(tr("Clés de répartition", "Allocation keys"), tr("Les parts d'un compteur doivent totaliser 1.",
                                                                   "The shares of a meter must total 1.")):
        keys = st.data_editor(pd.DataFrame(s["keys"].get(src, []), columns=["fr", "en", "zone", "share"]),
                              num_rows="dynamic", hide_index=True, width="stretch", key=f"bk_keys_{src}", column_config={
            "fr": st.column_config.TextColumn("Poste (FR)", required=True),
            "en": st.column_config.TextColumn("Item (EN)", required=True),
            "zone": st.column_config.SelectboxColumn(tr("Zone", "Zone"), options=list(zone_map), required=True),
            "share": st.column_config.NumberColumn(tr("Part", "Share"), min_value=0.0, max_value=1.0, format="%.3f")})
        total = float(pd.to_numeric(keys["share"], errors="coerce").fillna(0).sum())
        st.caption(tr(f"Total : {num(total, 3)}", f"Total: {num(total, 3)}"))
    new = copy.deepcopy(cfg)
    b = dict(cfg.get("breakdown") or {})
    b["zones"] = zone_map
    b["keys"] = {**s["keys"], src: [{"fr": r["fr"], "en": r["en"], "zone": r["zone"], "share": float(r["share"] or 0)}
                                    for _, r in keys.iterrows() if str(r.get("fr") or r.get("en") or "").strip()]}
    new["breakdown"] = b
    errors = bd.validate(bd.settings(new))
    for e in errors:
        st.warning(e)
    if st.button(tr("Enregistrer la répartition", "Save breakdown"), type="primary", icon=":material/check:",
                 disabled=bool(errors) or new["breakdown"] == cfg.get("breakdown"), key="bk_save"):
        save(new)
        st.rerun()


# ---------------------------------------------------------------------------
# Alerts thresholds (shown in the Reports & alerts tab)
# ---------------------------------------------------------------------------
def alerts_block(cfg: dict, save) -> None:
    from carbonsync import alerts

    s = alerts.settings(cfg)
    with ui.card(tr("Alertes", "Alerts"), tr("Vérifiées après chaque exécution du pipeline.", "Checked after each pipeline run.")):
        c1, c2, c3 = st.columns(3)
        stale = c1.number_input(tr("Aucune donnée depuis (jours)", "No data for (days)"), 1, 365, s["stale_after_days"],
                                key="al_stale")
        window = c2.number_input(tr("Fenêtre d'anomalies (jours)", "Anomaly window (days)"), 7, 365,
                                 s["anomaly_window_days"], key="al_window")
        margin = c3.number_input(tr("Écart MASE routeur", "Router MASE margin"), 0.0, 1.0, float(s["router_margin_mase"]),
                                 0.01, key="al_margin")
        st.caption(tr("Envoi par e-mail : non configuré (pas de serveur de messagerie). Les alertes s'affichent dans "
                      "la barre du haut et la page Alertes.",
                      "Email sending: not configured (no mail server). Alerts show in the top bar and on the Alerts page."))
    new = copy.deepcopy(cfg)
    new["alerts"] = {**(cfg.get("alerts") or {}), "stale_after_days": int(stale), "anomaly_window_days": int(window),
                     "router_margin_mase": float(margin)}
    if st.button(tr("Enregistrer les seuils", "Save thresholds"), icon=":material/check:",
                 disabled=new["alerts"] == cfg.get("alerts"), key="al_save"):
        save(new)
        st.rerun()
