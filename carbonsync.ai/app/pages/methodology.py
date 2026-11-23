"""Methodology – backtest evidence for the forecasting engine."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.i18n import tr
from app.data.loader import load_backtest_meta, load_backtest_summary, load_config
from app.ui import charts
from app.ui import components as ui
from app.ui.format import num, pct


def render() -> None:
    cfg = load_config()
    bt = pd.DataFrame(load_backtest_summary())
    meta = load_backtest_meta()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"

    ui.page_header(
        tr("Preuves", "Evidence"),
        tr("Méthodologie", "Methodology"),
        tr("Backtest glissant : 6, 12 et 24 mois d'historique, horizon 183 jours.",
           "Rolling-origin backtest: 6, 12 and 24 months of history, 183-day horizon."),
        [(f"Seed {meta.get('seed', '—')}", ""), (f"Commit {meta.get('commit', '—')}", "")]
        + ([(tr("Données synthétiques", "Synthetic data"), "warn")] if demo else []),
    )
    if bt.empty:
        ui.empty_state(tr("Aucun backtest. Lancez python -m carbonsync.backtest.rolling.",
                          "No backtest yet. Run python -m carbonsync.backtest.rolling."))
        return

    agg = bt.groupby("model_name")[["mae", "mase", "coverage", "mean_width"]].mean()
    real = agg.drop(index=[m for m in ("oracle",) if m in agg.index])
    best = real["mase"].idxmin()
    best_cov = real["coverage"].idxmax()
    oracle_mae = float(agg.loc["oracle", "mae"]) if "oracle" in agg.index else None

    ui.kpis(
        [
            ui.Kpi(tr("Meilleur modèle", "Best model"), charts.MODEL_NAMES.get(best, best),
                   note=f"MASE {num(real.loc[best, 'mase'], 2)}"),
            ui.Kpi(tr("Erreur moyenne (MAE)", "Mean error (MAE)"), num(real.loc[best, "mae"], 2), tr("t/j", "t/d"),
                   note=tr(f"Plancher oracle {num(oracle_mae, 2)}", f"Oracle floor {num(oracle_mae, 2)}") if oracle_mae else ""),
            ui.Kpi(tr("Meilleure couverture 90 %", "Best 90% coverage"), pct(real.loc[best_cov, "coverage"] * 100, 0),
                   note=charts.MODEL_NAMES.get(best_cov, best_cov)),
            ui.Kpi(tr("Évaluations", "Evaluations"), str(len(bt)), tr("lignes", "rows"),
                   note=tr(f"{bt['model_name'].nunique()} modèles", f"{bt['model_name'].nunique()} models")),
        ]
    )

    t1, t2, t3, t4 = st.tabs([tr("Précision", "Accuracy"), tr("Calibration", "Calibration"), "MASE", tr("Tableau", "Table")])
    with t1, ui.card(tr("MAE par fenêtre d'historique", "MAE by history window"), tr("Plus bas = meilleur", "Lower is better")):
        charts.show(
            charts.metric_by_window(bt, "mae", ref=oracle_mae, ref_label=tr("Plancher oracle", "Oracle floor")),
            key="m_mae",
        )
    with t2, ui.card(tr("Couverture empirique de l'intervalle 90 %", "Empirical coverage of the 90% interval"),
                     tr("Cible : 90 %", "Target: 90%")):
        charts.show(charts.metric_by_window(bt, "coverage", ref=90, ref_label=tr("Cible 90 %", "90% target"), pct=True), key="m_cov")
    with t3, ui.card(tr("Erreur relative à la référence naïve", "Error relative to naive baseline"),
                     tr("< 1 = meilleur que la référence", "< 1 beats the baseline")):
        charts.show(charts.metric_by_window(bt, "mase", ref=1.0, ref_label=tr("Référence", "Baseline")), key="m_mase")
    with t4:
        st.dataframe(
            pd.DataFrame(
                {
                    tr("Modèle", "Model"): bt["model_name"].map(lambda m: charts.MODEL_NAMES.get(m, m)),
                    tr("Historique", "History"): bt["history_months"].astype(str) + tr(" mois", " mo"),
                    "MAE": bt["mae"].round(3),
                    "MASE": bt["mase"].round(3),
                    tr("Couverture", "Coverage"): (bt["coverage"] * 100).round(1),
                    tr("Largeur (t)", "Width (t)"): bt["mean_width"].round(2),
                }
            ).sort_values([tr("Modèle", "Model"), tr("Historique", "History")]),
            hide_index=True, width="stretch",
        )

    c1, c2 = st.columns(2)
    with c1.expander(tr("Comment fonctionne le moteur", "How the engine works")):
        st.markdown(
            tr(
                "- **Routeur** : Chronos-2 dès 60 jours d'historique (meilleure MAE et couverture la plus proche "
                "de 90 % dans le backtest), sinon Prophet ; Prophet aussi si Chronos-2 échoue.\n"
                "- **Chronos-2** : modèle de fondation, zero-shot (pas d'entraînement sur le site).\n"
                "- **Ens-PC** : moyenne de Prophet (saisonnalités, calendrier) et Chronos-2 ; disponible au choix.\n"
                "- **Backtest** : recalculé après chaque dépôt (fenêtres glissantes), l'écart du routeur est surveillé par les alertes.\n"
                "- **Intervalles** : conformal split, par tranches d'horizon 1–30 / 31–90 / 91–183 j.",
                "- **Router**: Chronos-2 from 60 days of history (best MAE and coverage closest to 90% in the "
                "backtest), otherwise Prophet; Prophet also if Chronos-2 fails.\n"
                "- **Chronos-2**: zero-shot foundation model (no training on the site).\n"
                "- **Ens-PC**: average of Prophet (seasonality, calendar) and Chronos-2; available on demand.\n"
                "- **Backtest**: refreshed after each upload (rolling windows); the alerts watch the router's gap.\n"
                "- **Intervals**: split-conformal, by horizon bucket 1–30 / 31–90 / 91–183 d.",
            )
        )
    with c2.expander(tr("Limites", "Limitations")):
        st.markdown(
            tr(
                "- Évaluation sur données synthétiques ; à confirmer en parallèle sur données réelles.\n"
                "- Le plancher oracle n'existe pas en production.\n"
                "- Les intervalles d'Ens-PC et de Prophet couvrent moins que la cible de 90 % ; Chronos-2 en est le plus proche.",
                "- Evaluated on synthetic data; confirm with a shadow run on real data.\n"
                "- The oracle floor does not exist in production.\n"
                "- Ens-PC and Prophet intervals cover less than the 90% target; Chronos-2 is the closest.",
            )
        )
