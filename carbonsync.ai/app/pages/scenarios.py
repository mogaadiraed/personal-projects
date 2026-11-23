"""Scenarios – what-if simulation of decarbonisation levers on the active forecast."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.i18n import tr
from app.data.loader import load_active_forecast, load_config, load_emissions_daily
from app.ui import charts
from app.ui import components as ui
from app.ui.format import date_label, num, pct, signed
from carbonsync.scenarios.whatif import get_historical_scope_shares, simulate_scenario

PRESETS = {
    "solar": (25.0, 0.0, 0.0),
    "efficiency": (0.0, 10.0, 0.0),
    "combined": (20.0, 8.0, 0.0),
    "reset": (0.0, 0.0, 0.0),
}


@st.cache_data(show_spinner=False, ttl=300)
def _scope_shares() -> dict[str, float]:
    return get_historical_scope_shares()


def _apply_preset(name: str) -> None:
    s, e, p = PRESETS[name]
    st.session_state.update(sc_solar=s, sc_eff=e, sc_prod=p)


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    fc, _ = load_active_forecast()
    if fc.empty:
        ui.empty_state(tr("Aucune prévision de référence.", "No baseline forecast available."))
        return

    for k, v in (("sc_solar", 15.0), ("sc_eff", 5.0), ("sc_prod", 0.0)):
        st.session_state.setdefault(k, v)
    st.session_state.setdefault("sc_saved", [])

    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    ui.page_header(
        tr("Planification", "Plan"),
        tr("Scénarios", "Scenarios"),
        tr("Testez l'effet des leviers sur les 6 prochains mois.", "Test how levers change the next 6 months."),
        ui.data_chips(date_label(df["date"].max()), demo),
    )

    # ---- Levers ------------------------------------------------------------
    with ui.card(tr("Leviers", "Levers")):
        b = st.columns(4)
        labels = {
            "solar": tr("Solaire 25 %", "Solar 25%"),
            "efficiency": tr("Efficacité 10 %", "Efficiency 10%"),
            "combined": tr("Programme combiné", "Combined program"),
            "reset": tr("Réinitialiser", "Reset"),
        }
        for col, name in zip(b, PRESETS):
            col.button(labels[name], on_click=_apply_preset, args=(name,), width="stretch", key=f"preset_{name}",
                       type="tertiary" if name == "reset" else "secondary")
        l1, l2, l3 = st.columns(3)
        solar = l1.slider(tr("Autoconsommation solaire", "Solar self-consumption"), 0.0, 40.0, step=1.0,
                          format="%.0f%%", key="sc_solar")
        eff = l2.slider(tr("Efficacité énergétique", "Energy efficiency"), 0.0, 15.0, step=0.5,
                        format="%.1f%%", key="sc_eff")
        prod = l3.slider(tr("Volume de production", "Production volume"), -20.0, 20.0, step=1.0,
                         format="%+.0f%%", key="sc_prod")

    shares = _scope_shares()

    def total(s: float, e: float, p: float) -> float:
        return simulate_scenario(fc, s, e, p, scope_shares=shares)["summary"]["total_scenario_tco2e"]

    res = simulate_scenario(fc, solar, eff, prod, scope_shares=shares)
    summ = res["summary"]
    base, scen, delta = summ["total_baseline_tco2e"], summ["total_scenario_tco2e"], summ["net_delta_tco2e"]

    # Sequential attribution: each lever's increment given the ones before it
    t_solar = total(solar, 0, 0)
    t_solar_eff = total(solar, eff, 0)
    steps = [
        (tr("Solaire", "Solar"), t_solar - base),
        (tr("Efficacité", "Efficiency"), t_solar_eff - t_solar),
        (tr("Production", "Production"), scen - t_solar_eff),
    ]

    ui.kpis(
        [
            ui.Kpi(tr("Référence 183 j", "Baseline 183 d"), num(base, 0), "tCO₂e"),
            ui.Kpi(tr("Scénario", "Scenario"), num(scen, 0), "tCO₂e", pct(summ["pct_change"], 1, True),
                   "good" if delta <= 0 else "bad"),
            ui.Kpi(tr("Émissions évitées", "Avoided emissions"), num(-delta, 0), "tCO₂e",
                   note=tr(f"± 8 % : {num(abs(delta) * 0.92, 0)}–{num(abs(delta) * 1.08, 0)} t",
                           f"± 8%: {num(abs(delta) * 0.92, 0)}–{num(abs(delta) * 1.08, 0)} t")),
            ui.Kpi(tr("Par jour", "Per day"), num(-delta / max(1, summ["horizon_days"]), 2), tr("t/j", "t/d")),
        ]
    )

    left, right = st.columns([1, 1.4])
    with left, ui.card(tr("Contribution des leviers", "Lever contribution")):
        charts.show(charts.waterfall(base, steps, scen), key="sc_wf")
    with right, ui.card(tr("Référence vs scénario", "Baseline vs scenario")):
        charts.show(charts.scenario_trajectory(res["scenario_df"]), key="sc_traj")

    # ---- Saved scenarios ---------------------------------------------------
    with ui.card(tr("Scénarios enregistrés", "Saved scenarios"), tr("3 au maximum", "Up to 3")):
        c1, c2, c3 = st.columns([2.5, 1, 1], vertical_alignment="bottom")
        name = c1.text_input(tr("Nom", "Name"), value=f"S{solar:.0f} · E{eff:.0f} · P{prod:+.0f}", key="sc_name")
        from app.core import auth

        if c2.button(tr("Enregistrer", "Save"), icon=":material/bookmark_add:", width="stretch",
                     disabled=not auth.can("scenarios_save"),
                     help=None if auth.can("scenarios_save") else auth.need("scenarios_save")):
            saved = st.session_state["sc_saved"][-2:]
            saved.append({"name": name, "solar": solar, "eff": eff, "prod": prod, "total": scen, "delta": delta})
            st.session_state["sc_saved"] = saved
        if c3.button(tr("Vider", "Clear"), width="stretch", disabled=not st.session_state["sc_saved"]):
            st.session_state["sc_saved"] = []
        if st.session_state["sc_saved"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            tr("Nom", "Name"): s["name"],
                            tr("Solaire", "Solar"): f"{s['solar']:.0f}%",
                            tr("Efficacité", "Efficiency"): f"{s['eff']:.1f}%",
                            "Production": f"{s['prod']:+.0f}%",
                            "Total (t)": num(s["total"], 0),
                            "Δ (t)": signed(s["delta"], 0),
                        }
                        for s in st.session_state["sc_saved"]
                    ]
                ),
                hide_index=True, width="stretch",
            )

    st.caption(
        tr(
            f"Estimation directionnelle fondée sur les parts de scope historiques "
            f"(multiplicateur {res['multipliers']['net_composite_multiplier']:.3f}) — pas une garantie d'abattement.",
            f"Directional estimate from historical scope shares "
            f"(multiplier {res['multipliers']['net_composite_multiplier']:.3f}) — not a guaranteed abatement.",
        )
    )
