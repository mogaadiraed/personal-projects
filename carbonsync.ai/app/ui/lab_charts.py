"""Chart builders for the model lab and the data intake pages (same palette and base as ``charts``)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from app.core.i18n import tr
from app.ui.charts import (
    AQUA,
    MODEL_COLORS,
    MODEL_NAMES,
    NEUTRAL,
    ORANGE,
    VIOLET,
    _base,
    _rgba,
)
from app.ui.theme import CRIT

NAIVE = "seasonal_naive"
UNIT_DAY = ("tCO₂e / jour", "tCO₂e / day")


def _origin_marker(fig: go.Figure, origin: pd.Timestamp, text: str = "") -> None:
    fig.add_vline(x=origin, line={"color": NEUTRAL, "width": 1, "dash": "dot"})
    if text:
        fig.add_annotation(x=origin, y=1, yref="paper", text=text, showarrow=False, xanchor="left",
                           yanchor="top", xshift=6, font={"size": 11, "color": NEUTRAL})


# =============================================================================
# Model lab
# =============================================================================
def forecasts(train_tail: pd.DataFrame, actual: pd.DataFrame, runs: dict[str, pd.DataFrame],
              naive: pd.DataFrame | None, bands: bool, origin: pd.Timestamp, height: int = 440) -> go.Figure:
    """Training tail + observed horizon (neutral) and every model's forecast."""
    fig = go.Figure()
    obs = pd.concat([train_tail[["date", "co2e_total_t"]], actual[["date", "co2e_total_t"]]])
    fig.add_scatter(x=obs["date"], y=obs["co2e_total_t"], mode="lines", name=tr("Observé", "Observed"),
                    line={"color": NEUTRAL, "width": 1.4}, hovertemplate="%{y:.2f}")
    if naive is not None:
        fig.add_scatter(x=naive["date"], y=naive["yhat"], mode="lines", name=MODEL_NAMES[NAIVE],
                        line={"color": MODEL_COLORS[NAIVE], "width": 1.4, "dash": "dot"},
                        hovertemplate="%{y:.2f}", visible="legendonly")
    for model, df in runs.items():
        color = MODEL_COLORS.get(model, VIOLET)
        if bands:
            fig.add_scatter(x=df["date"], y=df["hi90"], mode="lines", line={"width": 0}, showlegend=False,
                            hoverinfo="skip", legendgroup=model)
            fig.add_scatter(x=df["date"], y=df["lo90"], mode="lines", line={"width": 0}, fill="tonexty",
                            fillcolor=_rgba(color, 0.13), showlegend=False, hoverinfo="skip", legendgroup=model)
        fig.add_scatter(x=df["date"], y=df["yhat"], mode="lines", name=MODEL_NAMES.get(model, model),
                        line={"color": color, "width": 2}, hovertemplate="%{y:.2f}", legendgroup=model)
    _origin_marker(fig, origin, tr("Fin d'entraînement", "End of training"))
    return _base(fig, height=height, unit=tr(*UNIT_DAY))


def error_by_week(actual: pd.DataFrame, runs: dict[str, pd.DataFrame], naive: pd.DataFrame | None,
                  height: int = 320) -> go.Figure:
    """Mean absolute error per forecast week: how fast each model degrades with distance."""
    fig = go.Figure()
    series = dict(runs)
    if naive is not None:
        series[NAIVE] = naive
    for model, df in series.items():
        m = actual[["date", "co2e_total_t"]].merge(df[["date", "yhat"]], on="date")
        if m.empty:
            continue
        m["week"] = (m["date"] - m["date"].min()).dt.days // 7 + 1
        w = m.assign(err=(m["yhat"] - m["co2e_total_t"]).abs()).groupby("week")["err"].mean()
        fig.add_scatter(x=w.index, y=w.values, mode="lines", name=MODEL_NAMES.get(model, model),
                        line={"color": MODEL_COLORS.get(model, VIOLET), "width": 2,
                              "dash": "dot" if model == NAIVE else None},
                        hovertemplate="%{y:.2f} t")
    fig = _base(fig, height=height, unit=tr("Erreur absolue moyenne (t/j)", "Mean absolute error (t/d)"))
    fig.update_xaxes(title={"text": tr("Semaine de prévision", "Forecast week"), "font": {"size": 11}})
    return fig


def prophet_trend(comp: pd.DataFrame, origin: pd.Timestamp, height: int = 250) -> go.Figure:
    color = MODEL_COLORS["prophet"]
    fit, fc = comp[comp["part"] == "fit"], comp[comp["part"] == "forecast"]
    fig = go.Figure()
    fig.add_scatter(x=fit["date"], y=fit["trend"], mode="lines", name=tr("Appris", "Learned"),
                    line={"color": color, "width": 2}, hovertemplate="%{y:.2f}")
    fig.add_scatter(x=fc["date"], y=fc["trend"], mode="lines", name=tr("Prolongé", "Extrapolated"),
                    line={"color": color, "width": 2, "dash": "dot"}, hovertemplate="%{y:.2f}")
    _origin_marker(fig, origin)
    return _base(fig, height=height, legend=False, unit=tr("t/j", "t/d"))


def prophet_weekly(comp: pd.DataFrame, height: int = 250) -> go.Figure:
    days = [tr(a, b) for a, b in zip(["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
                                     ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])]
    dow = comp["date"].dt.dayofweek
    effect = comp.groupby(dow)["weekly"].mean().reindex(range(7)).fillna(0)
    if "extra_regressors_additive" in comp:  # weekend / shutdown regressors act on the same cycle
        effect = effect + comp.groupby(dow)["extra_regressors_additive"].mean().reindex(range(7)).fillna(0)
    effect = effect - effect.mean()
    fig = go.Figure(go.Bar(x=days, y=effect.values, marker={"color": MODEL_COLORS["prophet"]},
                           hovertemplate="%{y:+.2f} t<extra></extra>"))
    fig = _base(fig, height=height, legend=False, unit=tr("Écart vs moyenne (t)", "vs average (t)"))
    fig.update_layout(hovermode="closest")
    return fig


def prophet_yearly(comp: pd.DataFrame, height: int = 250) -> go.Figure:
    fit = comp[comp["part"] == "fit"].sort_values("date")
    year = fit[fit["date"] > fit["date"].max() - pd.Timedelta(days=365)]
    fig = go.Figure()
    fig.add_scatter(x=year["date"], y=year["yearly"], mode="lines", line={"color": MODEL_COLORS["prophet"], "width": 2},
                    hovertemplate="%{y:+.2f} t", name=tr("Saisonnalité annuelle", "Yearly seasonality"))
    fig = _base(fig, height=height, legend=False, unit=tr("Écart vs tendance (t)", "vs trend (t)"))
    fig.update_xaxes(tickformat="%b")
    return fig


def chronos_fan(context: pd.DataFrame, fc: pd.DataFrame, height: int = 320) -> go.Figure:
    color = MODEL_COLORS["chronos"]
    fig = go.Figure()
    fig.add_scatter(x=context["date"], y=context["co2e_total_t"], mode="lines", name=tr("Contexte lu", "Context read"),
                    line={"color": NEUTRAL, "width": 1.3}, hovertemplate="%{y:.2f}")
    fig.add_scatter(x=fc["date"], y=fc["hi90"], mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=fc["date"], y=fc["lo90"], mode="lines", line={"width": 0}, fill="tonexty",
                    fillcolor=_rgba(color, 0.18), name=tr("Quantiles 5–95 %", "5–95% quantiles"), hoverinfo="skip")
    fig.add_scatter(x=fc["date"], y=fc["yhat"], mode="lines", name=tr("Médiane", "Median"),
                    line={"color": color, "width": 2}, hovertemplate="%{y:.2f}")
    return _base(fig, height=height, unit=tr(*UNIT_DAY))


def ensemble_parts(prophet: pd.DataFrame, chronos: pd.DataFrame, ens: pd.DataFrame, height: int = 320) -> go.Figure:
    fig = go.Figure()
    for model, df, width in (("prophet", prophet, 1.3), ("chronos", chronos, 1.3), ("ens_pc", ens, 2.4)):
        fig.add_scatter(x=df["date"], y=df["yhat"], mode="lines", name=MODEL_NAMES[model],
                        line={"color": MODEL_COLORS[model], "width": width}, hovertemplate="%{y:.2f}")
    return _base(fig, height=height, unit=tr(*UNIT_DAY))


# =============================================================================
# Data intake: department × month coverage
# =============================================================================
DEPT_LABELS = {
    "energie": ("Énergie", "Energy"),
    "production": ("Production", "Production"),
    "logistique": ("Logistique", "Logistics"),
    "dechets_eau": ("Déchets & eau", "Waste & water"),
}
COVERAGE_STATES = {0: ("Absent", "Missing"), 1: ("Partiel", "Partial"), 2: ("Complet", "Complete"),
                   3: ("Rejeté", "Rejected")}


def dept_coverage(status: pd.DataFrame, rejected: set[tuple[str, pd.Timestamp]], months: pd.DatetimeIndex,
                  height: int = 205) -> go.Figure:
    """Department × month grid. z: 0 missing · 1 partial · 2 complete · 3 rejected (nothing accepted)."""
    depts = list(DEPT_LABELS)
    z, text = [], []
    for d in depts:
        sub = status[status["department"] == d].set_index("month")["days"] if not status.empty else pd.Series(dtype=int)
        row, trow = [], []
        for m in months:
            days, full = int(sub.get(m, 0)), m.days_in_month
            state = 2 if days >= full - 1 else 1 if days > 0 else 3 if (d, m) in rejected else 0
            row.append(state)
            trow.append(f"{tr(*DEPT_LABELS[d])} · {m:%Y-%m} · {tr(*COVERAGE_STATES[state])} · {days}/{full} {tr('j', 'd')}")
        z.append(row)
        text.append(trow)
    gray = "rgba(128,128,128,0.18)"
    fig = go.Figure(go.Heatmap(
        z=z, x=[m.strftime("%Y-%m") for m in months], y=[tr(*DEPT_LABELS[d]) for d in depts], zmin=0, zmax=3,
        colorscale=[[0, gray], [0.249, gray], [0.25, ORANGE], [0.499, ORANGE],
                    [0.5, _rgba(AQUA, 0.85)], [0.749, _rgba(AQUA, 0.85)], [0.75, CRIT], [1, CRIT]],
        showscale=False, xgap=3, ygap=3, customdata=text, hovertemplate="%{customdata}<extra></extra>",
    ))
    fig = _base(fig, height=height, legend=False)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    fig.update_xaxes(tickangle=0, nticks=12)
    fig.update_layout(hovermode="closest")
    return fig
