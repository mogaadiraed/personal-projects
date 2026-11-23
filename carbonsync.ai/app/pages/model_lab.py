"""Model lab – train Prophet, Chronos-2 and Ens-PC on any window, watch them run, score them on real data."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from app.core.i18n import tr
from app.data.loader import clear_caches, load_config, load_emissions_daily
from app.ui import charts, lab_charts
from app.ui import components as ui
from app.ui.format import date_label, num, pct

HORIZONS = {3: 91, 6: 183, 12: 365}           # months -> days
TRAINING = {6: 182, 12: 365, 24: 730, 0: None}  # months -> days (0 = whole history)
MODELS = ["prophet", "chronos", "ens_pc"]


def _months(m: int) -> str:
    return tr(f"{m} mois", f"{m} mo")


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    if df.empty:
        ui.empty_state(tr("Aucune donnée d'émissions.", "No emissions data."))
        return
    df = df.sort_values("date").reset_index(drop=True)
    first, last = df["date"].min(), df["date"].max()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"

    ui.page_header(
        tr("Modèles", "Models"),
        tr("Laboratoire modèles", "Model lab"),
        tr("Entraînez chaque modèle sur la période de votre choix, suivez son exécution, comparez-le au réel.",
           "Train each model on any window, watch it run, score it against real data."),
        ui.data_chips(date_label(last), demo),
    )

    params = _controls(df, first, last, cfg)
    if params["run"]:
        _run(df, params)

    exp = st.session_state.get("lab_exp")
    if exp is None:
        ui.empty_state(tr("Choisissez les modèles et la période, puis lancez l'expérience.",
                          "Pick the models and the period, then run the experiment."))
        return
    _results(exp)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
def _controls(df: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp, cfg: dict) -> dict:
    with ui.card(tr("Expérience", "Experiment")):
        c1, c2, c3 = st.columns([1.35, 1, 1.25])
        with c1:
            models = st.pills(
                tr("Modèles", "Models"), MODELS, default=MODELS, selection_mode="multi",
                format_func=charts.MODEL_NAMES.get, key="lab_models",
            ) or []
        with c2:
            h_months = st.segmented_control(
                tr("Horizon de prévision", "Forecast horizon"), list(HORIZONS), default=6,
                format_func=_months, key="lab_h",
            ) or 6
        with c3:
            t_months = st.segmented_control(
                tr("Période d'entraînement", "Training period"), list(TRAINING), default=24,
                format_func=lambda m: tr("Tout", "All") if m == 0 else _months(m), key="lab_t",
            )
            t_months = 24 if t_months is None else t_months

        horizon, train_days = HORIZONS[h_months], TRAINING[t_months]
        c4, c5, c6 = st.columns([1.35, 1, 1.25], vertical_alignment="bottom")
        with c4:
            mode = st.segmented_control(
                tr("Mode", "Mode"), ["backtest", "future"], default="backtest",
                format_func=lambda v: tr("Tester sur le passé", "Backtest on the past") if v == "backtest"
                else tr("Prévoir le futur", "Forecast the future"),
                key="lab_mode",
            ) or "backtest"
        with c5:
            if mode == "backtest":
                default = max(first + pd.Timedelta(days=60), last - pd.Timedelta(days=horizon))
                origin = pd.Timestamp(st.date_input(
                    tr("Entraîner jusqu'au", "Train until"), value=default.date(),
                    min_value=(first + pd.Timedelta(days=30)).date(), max_value=(last - pd.Timedelta(days=1)).date(),
                    format="YYYY-MM-DD",
                    help=tr("La prévision commence le lendemain et est comparée aux données réelles.",
                            "The forecast starts the next day and is compared with the real data."),
                ))
            else:
                origin = last
                st.text_input(tr("Entraîner jusqu'au", "Train until"), f"{last:%Y-%m-%d}", disabled=True)
        with c6:
            run = st.button(tr("Lancer l'expérience", "Run experiment"), type="primary",
                            icon=":material/play_arrow:", width="stretch", disabled=not models, key="lab_run")

        hist = df[df["date"] <= origin]
        train = hist if train_days is None else hist.tail(train_days)
        end = origin + pd.Timedelta(days=horizon)
        n_score = int(((df["date"] > origin) & (df["date"] <= end)).sum())
        from carbonsync.models.router import MODEL_LABELS, choose_model

        pick = MODEL_LABELS[choose_model(len(train), cfg)]
        chips = [
            (tr(f"Entraînement {train['date'].min():%Y-%m-%d} → {origin:%Y-%m-%d} · {len(train)} j",
                f"Training {train['date'].min():%Y-%m-%d} → {origin:%Y-%m-%d} · {len(train)} d"), ""),
            (tr(f"Prévision → {end:%Y-%m-%d} · {horizon} j", f"Forecast → {end:%Y-%m-%d} · {horizon} d"), ""),
            (tr(f"{n_score} jours réels pour noter", f"{n_score} real days to score against") if n_score
             else tr("Futur pur : rien à noter", "Pure future: nothing to score"), "accent" if n_score else "warn"),
            (tr(f"Le routeur choisirait {pick}", f"Router would pick {pick}"), ""),
        ]
        ui.chip_row(chips)
        if len(train) < 365 and {"prophet", "ens_pc"} & set(models):
            st.caption(tr("Moins de 365 jours : Prophet n'apprend pas la saisonnalité annuelle.",
                          "Under 365 days: Prophet does not learn yearly seasonality."))
        from carbonsync.models import chronos_model

        if {"chronos", "ens_pc"} & set(models) and chronos_model._CHRONOS_PIPELINE is None:
            st.caption(tr("Chronos-2 charge ses poids au premier lancement (~30 s), ensuite moins d'une seconde.",
                          "Chronos-2 loads its weights on first use (~30 s), then runs in under a second."))

    return {"run": run, "models": models, "origin": origin, "train_days": train_days, "horizon": horizon,
            "h_months": h_months, "t_months": t_months, "mode": mode}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _run(df: pd.DataFrame, p: dict) -> None:
    from carbonsync.models.lab import run_experiment

    try:
        with ui.live_log(tr("Expérience en cours…", "Running experiment…"),
                         tr("Expérience terminée", "Experiment complete")) as lines:
            exp = run_experiment(df, p["models"], p["origin"], p["train_days"], p["horizon"])
    except Exception as exc:  # noqa: BLE001 - shown to the user
        st.error(f"{type(exc).__name__}: {exc}")
        return
    exp.params = p  # type: ignore[attr-defined]
    st.session_state["lab_exp"] = exp
    st.session_state["lab_log"] = list(lines)
    history = st.session_state.setdefault("lab_history", [])
    stamp = datetime.now().strftime("%H:%M:%S")
    for key, r in exp.runs.items():
        m = r.metrics
        history.insert(0, {
            tr("Heure", "Time"): stamp,
            tr("Modèle", "Model"): charts.MODEL_NAMES[key],
            tr("Entraînement", "Training"): tr("Tout", "All") if p["t_months"] == 0 else _months(p["t_months"]),
            tr("Jusqu'au", "Until"): f"{p['origin']:%Y-%m-%d}",
            "Horizon": _months(p["h_months"]),
            "MAE": round(m["mae"], 3) if m else None,
            "MASE": round(m["mase"], 3) if m else None,
            tr("Couv. 90 %", "90% cov."): round(m["coverage"] * 100, 1) if m else None,
            tr("Écart total %", "Total error %"): round(m["total_error_pct"], 1) if m else None,
            tr("Durée (s)", "Time (s)"): round(r.fit_seconds + r.predict_seconds, 2),
        })


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def _results(exp) -> None:
    p = getattr(exp, "params", {})
    scored = exp.evaluated_days > 0
    ui.chip_row([
        (tr("Résultats", "Results"), "accent"),
        (", ".join(charts.MODEL_NAMES[k] for k in exp.runs), ""),
        (tr(f"Entraînement {len(exp.train)} j → {exp.origin:%Y-%m-%d}", f"Training {len(exp.train)} d → {exp.origin:%Y-%m-%d}"), ""),
        (tr(f"Horizon {exp.horizon_days} j", f"Horizon {exp.horizon_days} d"), ""),
        (tr(f"Noté sur {exp.evaluated_days} j", f"Scored on {exp.evaluated_days} d") if scored
         else tr("Futur : pas encore de réel", "Future: no actuals yet"), "" if scored else "warn"),
    ])

    best = min(exp.runs, key=lambda k: exp.runs[k].metrics["mae"]) if scored else None
    tiles = []
    for key, r in exp.runs.items():
        secs = r.fit_seconds + r.predict_seconds
        if scored:
            m = r.metrics
            tiles.append(ui.Kpi(
                f"{charts.MODEL_NAMES[key]} · MAE", num(m["mae"], 2), tr("t/j", "t/d"),
                tr("Meilleur", "Best") if key == best else "", "good" if key == best else "",
                f"MASE {num(m['mase'], 2)} · {tr('couv.', 'cov.')} {pct(m['coverage'] * 100, 0)} · {num(secs, 1)} s",
            ))
        else:
            tiles.append(ui.Kpi(
                f"{charts.MODEL_NAMES[key]} · {tr('total prévu', 'forecast total')}", num(r.forecast["yhat"].sum(), 0),
                "tCO₂e", note=tr(f"{num(r.forecast['yhat'].mean(), 2)} t/j · {num(secs, 1)} s",
                                 f"{num(r.forecast['yhat'].mean(), 2)} t/d · {num(secs, 1)} s"),
            ))
    if scored and exp.naive and exp.naive.metrics:
        tiles.append(ui.Kpi(tr("Référence naïve · MAE", "Naive baseline · MAE"), num(exp.naive.metrics["mae"], 2),
                            tr("t/j", "t/d"), note=tr("Même jour, semaine précédente", "Same weekday, last week")))
    ui.kpis(tiles)

    names = [tr("Prévision vs réel", "Forecast vs actual"), tr("Erreur par semaine", "Error by week"),
             tr("Fonctionnement", "How it works"), tr("Scores", "Scores"), tr("Historique", "History")]
    t_fc, t_err, t_how, t_score, t_hist = st.tabs(names)

    runs = {k: r.forecast for k, r in exp.runs.items()}
    with t_fc, ui.card(tr("Prévisions et données réelles", "Forecasts and real data"),
                       tr("Référence naïve masquée : cliquez sur la légende.", "Naive baseline hidden: click the legend.")):
        bands = st.toggle(tr("Intervalles 90 %", "90% intervals"), value=len(runs) == 1, key="lab_bands")
        tail = exp.train.tail(max(90, exp.horizon_days // 2))
        charts.show(lab_charts.forecasts(tail, exp.actual, runs, exp.naive.forecast if exp.naive else None,
                                         bands, exp.origin), key="lab_fc")

    with t_err, ui.card(tr("Erreur absolue moyenne par semaine de prévision", "Mean absolute error per forecast week"),
                        tr("Plus la courbe est basse, mieux c'est.", "Lower is better.")):
        if scored:
            charts.show(lab_charts.error_by_week(exp.actual, runs, exp.naive.forecast if exp.naive else None), key="lab_err")
        else:
            ui.empty_state(tr("Horizon dans le futur : choisissez « Tester sur le passé » pour mesurer l'erreur.",
                              "Horizon is in the future: choose “Backtest on the past” to measure error."))

    with t_how:
        _how_it_works(exp)

    with t_score, ui.card(tr("Tableau des scores", "Scoreboard")):
        if scored:
            rows = []
            for key, r in [*exp.runs.items(), ("seasonal_naive", exp.naive)]:
                m = r.metrics
                rows.append({
                    tr("Modèle", "Model"): charts.MODEL_NAMES[key],
                    "MAE (t/j)": round(m["mae"], 3), "MASE": round(m["mase"], 3),
                    tr("Biais (t/j)", "Bias (t/d)"): round(m["bias"], 3),
                    tr("Écart total %", "Total error %"): round(m["total_error_pct"], 2),
                    tr("Couv. 90 %", "90% coverage"): round(m["coverage"] * 100, 1),
                    tr("Largeur 90 % (t)", "90% width (t)"): round(m["mean_width"], 2),
                    tr("Ajustement (s)", "Fit (s)"): round(r.fit_seconds, 2),
                    tr("Prévision (s)", "Predict (s)"): round(r.predict_seconds, 2),
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption(tr("MASE < 1 : meilleur que la référence naïve hebdomadaire · couverture idéale ≈ 90 %.",
                          "MASE < 1 beats the weekly naive baseline · ideal coverage ≈ 90%."))
        else:
            ui.empty_state(tr("Aucune donnée réelle sur l'horizon.", "No real data over the horizon."))
        if p.get("mode") == "future":
            _save_buttons(exp)

    with t_hist, ui.card(tr("Expériences de la session", "Experiments this session")):
        hist = st.session_state.get("lab_history", [])
        if hist:
            st.dataframe(pd.DataFrame(hist), hide_index=True, width="stretch")
            if st.button(tr("Effacer", "Clear"), key="lab_hist_clear", icon=":material/delete:"):
                st.session_state["lab_history"] = []
                st.rerun()
        log_lines = st.session_state.get("lab_log")
        if log_lines:
            with st.expander(tr("Journal de la dernière exécution", "Last run log")):
                st.code("\n".join(log_lines), language=None)


def _how_it_works(exp) -> None:
    prophet = exp.internal.get("prophet")
    chronos = exp.internal.get("chronos")
    ens = exp.internal.get("ens_pc")

    if prophet is not None:
        comp = prophet.details.get("components")
        with ui.card("Prophet", tr("Décompose la série : tendance + cycle hebdomadaire + saisonnalité annuelle + "
                                   "régresseurs (week-ends, arrêts, solaire, efficacité). Ajusté avec Stan.",
                                   "Decomposes the series: trend + weekly cycle + yearly seasonality + regressors "
                                   "(weekends, shutdowns, solar, efficiency). Fitted with Stan.")):
            ui.chip_row([(tr("Moteur", "Engine") + f" : {prophet.details.get('backend', '—')}", ""),
                         (tr(f"Ajustement {num(prophet.fit_seconds, 2)} s", f"Fit {num(prophet.fit_seconds, 2)} s"), "")])
            if comp is not None:
                a, b, c = st.columns(3)
                with a:
                    st.caption(tr("Tendance (pointillé : prolongée)", "Trend (dotted: extrapolated)"))
                    charts.show(lab_charts.prophet_trend(comp, exp.origin), key="lab_p_trend")
                with b:
                    st.caption(tr("Effet du jour de la semaine", "Day-of-week effect"))
                    charts.show(lab_charts.prophet_weekly(comp), key="lab_p_week")
                with c:
                    st.caption(tr("Saisonnalité annuelle", "Yearly seasonality"))
                    if "yearly" in comp:
                        charts.show(lab_charts.prophet_yearly(comp), key="lab_p_year")
                    else:
                        ui.empty_state(tr("Désactivée : moins de 365 jours d'entraînement.",
                                          "Disabled: fewer than 365 training days."))

    if chronos is not None:
        ctx = exp.train.tail(min(len(exp.train), 365))
        with ui.card("Chronos-2", tr("Modèle de fondation pré-entraîné : aucun ajustement, il lit l'historique comme "
                                     "contexte et produit directement des quantiles.",
                                     "Pretrained foundation model: no fitting, it reads the history as context and "
                                     "outputs quantiles directly.")):
            ui.chip_row([(tr(f"Contexte {chronos.details.get('context_days', len(exp.train))} j",
                             f"Context {chronos.details.get('context_days', len(exp.train))} d"), ""),
                         (tr(f"Inférence {num(chronos.predict_seconds, 2)} s", f"Inference {num(chronos.predict_seconds, 2)} s"), "")])
            charts.show(lab_charts.chronos_fan(ctx, chronos.forecast), key="lab_c_fan")

    if ens is not None and prophet is not None and chronos is not None:
        with ui.card("Ens-PC", tr("Moyenne jour par jour de Prophet et Chronos-2 (prévision et intervalles).",
                                  "Day-by-day average of Prophet and Chronos-2 (forecast and intervals).")):
            charts.show(lab_charts.ensemble_parts(prophet.forecast, chronos.forecast, ens.forecast), key="lab_e_parts")


def _save_buttons(exp) -> None:
    st.divider()
    st.caption(tr("Enregistrer une de ces prévisions comme prévision active de toutes les pages :",
                  "Save one of these forecasts as the active forecast on every page:"))
    cols = st.columns(len(exp.runs))
    for col, key in zip(cols, exp.runs):
        if col.button(charts.MODEL_NAMES[key], key=f"lab_save_{key}", icon=":material/save:", width="stretch"):
            from carbonsync.models.ensemble import run_and_save_forecast

            try:
                with ui.live_log(tr("Enregistrement…", "Saving…"), tr("Prévision enregistrée", "Forecast saved")):
                    res = run_and_save_forecast(history_days=len(exp.train), model_name=key,
                                                horizon_days=exp.horizon_days)
            except Exception as exc:  # noqa: BLE001
                st.error(f"{type(exc).__name__}: {exc}")
                return
            clear_caches()
            st.session_state["active_run_id"] = res["run_id"]
            st.toast(tr("Prévision active mise à jour.", "Active forecast updated."), icon=":material/check:")
