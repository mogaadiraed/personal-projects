"""Plotly chart builders.

Figures are rendered with ``theme="streamlit"`` so axes, fonts and hover labels
follow the active light/dark palette automatically. Only series colors are set
here; they come from one validated categorical palette and are assigned by
entity (Scope 1 is always orange, Scope 2 always blue, …), never by rank, whatever
the interface palette chosen in the Settings.

The constants are the light-mode steps; :func:`show` swaps them for the dark-mode
steps of the same hues when the page is dark (both sets checked with the
colour-blind validator against the flat and frosted card surfaces).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from app.core.i18n import tr
from app.ui import appearance as ap
from app.ui.format import month_short, num
from app.ui.theme import CRIT, GOOD, WARN

# --- Series colors (entity -> fixed slot of the validated order) ------------
BLUE = "#2a78d6"     # slot 1 · Scope 2 · forecast
ORANGE = "#eb6834"   # slot 2 · Scope 1
AQUA = "#1baf7a"     # slot 3 · Other / Scope 3 · scenario
YELLOW = "#eda100"   # slot 4
MAGENTA = "#e87ba4"  # slot 5
VIOLET = "#6250d6"   # slot 7 (fallback only)
NEUTRAL = "#898781"  # observed history, reference lines
GRIDLINE = "rgba(128,128,128,0.16)"
# Same hues, stepped for the dark surface
DARK_STEPS = {BLUE: "#3987e5", ORANGE: "#d95926", AQUA: "#199e70", YELLOW: "#c98500",
              MAGENTA: "#d55181", VIOLET: "#9085e9", "#008300": "#0ca30c", "#e34948": "#e66767"}

SCOPE_COLORS = {"s1": ORANGE, "s2": BLUE, "other": AQUA}
MODEL_COLORS = {
    "ens_pc": BLUE,
    "chronos": AQUA,
    "prophet": ORANGE,
    "seasonal_naive": YELLOW,
    "oracle": MAGENTA,
}
MODEL_NAMES = {
    "ens_pc": "Ens-PC",
    "chronos": "Chronos-2",
    "prophet": "Prophet",
    "seasonal_naive": "Seasonal naive",
    "oracle": "Oracle",
}

PLOT_CONFIG: dict[str, Any] = {
    "displaylogo": False,
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
    "responsive": True,
}


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def _font() -> float:
    try:
        return ap.chart_font(ap.current())
    except Exception:  # noqa: BLE001 – charts built outside the app (tests, exports)
        return ap.CHART_FONT_PX["m"]


def _base(fig: go.Figure, height: int = 360, legend: bool = True, unit: str | None = None) -> go.Figure:
    """Shared look: readable text (follows the text-size setting), 2 px lines, legend on top,
    recessive grid, crosshair + unified tooltip, axis title with the unit."""
    fs = _font()
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 40 if legend else 12, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "size": fs},
        showlegend=legend,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0, "title": None,
                "bgcolor": "rgba(0,0,0,0)", "font": {"size": fs}, "itemsizing": "constant"},
        hovermode="x unified",
        hoverlabel={"font": {"size": fs}},
        barcornerradius=4,
        bargap=0.28,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, ticks="", tickfont={"size": fs},
                     showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1, spikedash="dot",
                     spikecolor="rgba(128,128,128,0.55)")
    fig.update_yaxes(
        showgrid=True, gridcolor=GRIDLINE, gridwidth=1, zeroline=False, showline=False, tickfont={"size": fs},
        ticks="", title={"text": unit, "font": {"size": fs - 1}} if unit else None,
    )
    for trace in fig.data:   # data lines at least 2 px (bands drawn with width 0 stay hidden)
        line = getattr(trace, "line", None)
        mode = str(getattr(trace, "mode", "") or "")
        if line is not None and "lines" in mode and line.width and 0 < line.width < 2:
            line.width = 2
    for a in fig.layout.annotations or ():
        if a.font is not None and a.font.size is not None and a.font.size < fs - 1:
            a.font.size = fs - 1
    return fig


def _restep(fig: go.Figure) -> go.Figure:
    """Dark page: every series colour swapped for the dark step of the same hue."""
    s = fig.to_json()
    for light, dark in DARK_STEPS.items():
        s = s.replace(light, dark)
        rgb_l = ",".join(str(int(light[i:i + 2], 16)) for i in (1, 3, 5))
        rgb_d = ",".join(str(int(dark[i:i + 2], 16)) for i in (1, 3, 5))
        s = s.replace(f"rgba({rgb_l},", f"rgba({rgb_d},")
    return pio.from_json(s, skip_invalid=True)


def show(fig: go.Figure, key: str | None = None) -> None:
    try:
        dark = ap.active_mode(ap.current()) == "dark"
    except Exception:  # noqa: BLE001
        dark = False
    st.plotly_chart(_restep(fig) if dark else fig, theme="streamlit", config=PLOT_CONFIG, width="stretch", key=key)


# =============================================================================
# Trajectory: history + forecast with 90 % band
# =============================================================================
def trajectory(
    hist: pd.DataFrame,
    fc: pd.DataFrame,
    *,
    weekly: bool = False,
    milestones: list[tuple[str, str]] | None = None,
    height: int = 400,
    scroll: bool = False,
) -> go.Figure:
    """``scroll=True``: full daily history, opens on the last year + forecast; drag or use the
    range slider below the chart to move to any date."""
    h = hist[["date", "co2e_total_t"]].copy()
    f = fc[["date", "yhat", "lo90", "hi90"]].copy() if not fc.empty else pd.DataFrame(columns=["date", "yhat", "lo90", "hi90"])
    unit = tr("tCO₂e / jour", "tCO₂e / day")
    if weekly:
        h = h.resample("W-MON", on="date").sum().reset_index()
        if not f.empty:
            f = f.resample("W-MON", on="date").sum().reset_index()
        unit = tr("tCO₂e / semaine", "tCO₂e / week")

    fig = go.Figure()
    fig.add_scatter(
        x=h["date"], y=h["co2e_total_t"], mode="lines", name=tr("Observé", "Observed"),
        line={"color": NEUTRAL, "width": 1.6}, hovertemplate="%{y:.1f}",
    )
    if not f.empty:
        fig.add_scatter(x=f["date"], y=f["hi90"], mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip")
        fig.add_scatter(
            x=f["date"], y=f["lo90"], mode="lines", line={"width": 0}, fill="tonexty",
            fillcolor=_rgba(BLUE, 0.16), name=tr("Intervalle 90 %", "90% interval"), hoverinfo="skip",
        )
        fig.add_scatter(
            x=f["date"], y=f["yhat"], mode="lines", name=tr("Prévision", "Forecast"),
            line={"color": BLUE, "width": 2}, hovertemplate="%{y:.1f}",
        )
        origin = f["date"].min()
        fig.add_vline(x=origin, line={"color": NEUTRAL, "width": 1, "dash": "dot"})
        fig.add_annotation(
            x=origin, y=1, yref="paper", text=tr("Début prévision", "Forecast start"), showarrow=False,
            xanchor="left", yanchor="top", xshift=6, font={"size": 11, "color": NEUTRAL},
        )

    x_min = h["date"].min()
    for date_str, label in milestones or []:
        d = pd.Timestamp(date_str)
        if d >= x_min:
            fig.add_vline(x=d, line={"color": NEUTRAL, "width": 1, "dash": "dash"})
            fig.add_annotation(
                x=d, y=1, yref="paper", text=label, showarrow=False, xanchor="left",
                yanchor="top", xshift=6, font={"size": 11, "color": NEUTRAL},
            )
    fig = _base(fig, height=height, unit=unit)
    if scroll and not h.empty:
        end = f["date"].max() if not f.empty else h["date"].max()
        # Scale on normal days; rare anomaly spikes (meter glitches) would flatten 8 years of curve.
        top = max(float(h["co2e_total_t"].quantile(0.995)), float(f["hi90"].max()) if not f.empty else 0.0) * 1.12
        spikes = h[h["co2e_total_t"] > top]
        if not spikes.empty:
            fig.add_trace(go.Scatter(
                x=spikes["date"], y=[top * 0.97] * len(spikes), mode="markers", cliponaxis=False,
                name=tr("Pic hors échelle", "Off-scale spike"),
                marker={"symbol": "triangle-up", "size": 10, "color": CRIT, "line": {"width": 0}},
                customdata=spikes["co2e_total_t"],
                hovertemplate=tr("Pic hors échelle : %{customdata:.1f} t", "Off-scale spike: %{customdata:.1f} t")
                + "<extra></extra>",
            ))
        fig.update_xaxes(
            range=[h["date"].max() - pd.Timedelta(days=365), end],
            rangeslider={"visible": True, "thickness": 0.09, "bgcolor": "rgba(128,128,128,0.07)",
                         "bordercolor": GRIDLINE, "borderwidth": 1},
            rangeselector={
                "buttons": [
                    {"count": 6, "label": tr("6 m", "6 m"), "step": "month", "stepmode": "backward"},
                    {"count": 1, "label": tr("1 an", "1 y"), "step": "year", "stepmode": "backward"},
                    {"count": 3, "label": tr("3 ans", "3 y"), "step": "year", "stepmode": "backward"},
                    {"step": "all", "label": tr("Tout", "All")},
                ],
                "bgcolor": "rgba(128,128,128,0.10)", "activecolor": _rgba(AQUA, 0.35),
                "x": 0.5, "xanchor": "center", "y": 1.02, "yanchor": "bottom", "font": {"size": 11},
            },
        )
        fig.update_yaxes(fixedrange=True, range=[0, top])
        fig.update_layout(dragmode="pan", margin={"l": 8, "r": 8, "t": 44, "b": 8})
    return fig


def model_overlay(runs: dict[str, pd.DataFrame], hist_tail: pd.DataFrame, show_bands: bool, height: int = 420) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=hist_tail["date"], y=hist_tail["co2e_total_t"], mode="lines", name=tr("Observé", "Observed"),
        line={"color": NEUTRAL, "width": 1.6}, hovertemplate="%{y:.1f}",
    )
    for model, df in runs.items():
        color = MODEL_COLORS.get(model, VIOLET)
        name = MODEL_NAMES.get(model, model)
        if show_bands:
            fig.add_scatter(x=df["date"], y=df["hi90"], mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip")
            fig.add_scatter(
                x=df["date"], y=df["lo90"], mode="lines", line={"width": 0}, fill="tonexty",
                fillcolor=_rgba(color, 0.12), showlegend=False, hoverinfo="skip",
            )
        fig.add_scatter(
            x=df["date"], y=df["yhat"], mode="lines", name=name, line={"color": color, "width": 2},
            hovertemplate="%{y:.2f}",
        )
    return _base(fig, height=height, unit=tr("tCO₂e / jour", "tCO₂e / day"))


# =============================================================================
# Scopes
# =============================================================================
def scope_bars(s1: float, s2: float, other: float, height: int = 230) -> go.Figure:
    total = s1 + s2 + other
    rows = sorted(
        [
            (tr("Scope 2 · Électricité", "Scope 2 · Electricity"), s2, SCOPE_COLORS["s2"]),
            (tr("Scope 1 · Combustibles", "Scope 1 · Fuels"), s1, SCOPE_COLORS["s1"]),
            (tr("Autre · Déchets & eau", "Other · Waste & water"), other, SCOPE_COLORS["other"]),
        ],
        key=lambda r: r[1],
    )
    fig = go.Figure(
        go.Bar(
            y=[r[0] for r in rows], x=[r[1] for r in rows], orientation="h",
            marker={"color": [r[2] for r in rows]},
            text=[f"{r[1] / total * 100:.0f}%" if total else "" for r in rows],
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}: %{x:,.0f} tCO₂e<extra></extra>",
        )
    )
    fig = _base(fig, height=height, legend=False)
    fig.update_layout(hovermode="closest", bargap=0.45, margin={"l": 8, "r": 40, "t": 8, "b": 8})
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, showticklabels=False)
    fig.update_yaxes(showgrid=False)
    return fig


def monthly_scopes(df_monthly: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = go.Figure()
    x = df_monthly["month_dt"]
    for col, key, name in [
        ("co2e_scope2_t", "s2", tr("Scope 2", "Scope 2")),
        ("co2e_scope1_t", "s1", tr("Scope 1", "Scope 1")),
        ("co2e_other_t", "other", tr("Autre", "Other")),
    ]:
        fig.add_bar(x=x, y=df_monthly[col], name=name, marker_color=SCOPE_COLORS[key], hovertemplate="%{y:,.0f}")
    fig = _base(fig, height=height, unit="tCO₂e")
    fig.update_layout(barmode="stack", bargap=0.22)
    fig.update_xaxes(tickformat="%b %y")
    return fig


# =============================================================================
# Budget
# =============================================================================
def burndown(mb, height: int = 360) -> go.Figure:
    days = list(range(1, mb.days_in_month + 1))
    fig = go.Figure()
    fig.add_scatter(x=days, y=mb.cum_upper, mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip")
    fig.add_scatter(
        x=days, y=mb.cum_projected, mode="lines", fill="tonexty", fillcolor=_rgba(BLUE, 0.14),
        line={"color": BLUE, "width": 2, "dash": "dot"}, name=tr("Projection", "Projection"),
        hovertemplate="%{y:.1f}",
    )
    fig.add_scatter(
        x=days, y=mb.cum_actual, mode="lines", line={"color": NEUTRAL, "width": 2.2},
        name=tr("Réel cumulé", "Cumulative actual"), hovertemplate="%{y:.1f}",
    )
    fig.add_hline(y=mb.ceiling, line={"color": CRIT, "width": 1.5, "dash": "dash"})
    fig.add_annotation(
        x=1, xref="paper", y=mb.ceiling, text=tr("Plafond", "Ceiling"), showarrow=False,
        xanchor="right", yanchor="bottom", font={"size": 11, "color": CRIT},
    )
    fig = _base(fig, height=height, unit="tCO₂e")
    fig.update_xaxes(title={"text": tr("Jour du mois", "Day of month"), "font": {"size": 11}}, dtick=5)
    return fig


def budget_year(df: pd.DataFrame, height: int = 320) -> go.Figure:
    labels = [month_short(m) for m in df["month"]]
    status_col = {"green": GOOD, "amber": WARN, "red": CRIT, "nodata": NEUTRAL}
    fig = go.Figure()
    fig.add_bar(x=labels, y=df["actual"], name=tr("Réel", "Actual"), marker_color=NEUTRAL, hovertemplate="%{y:,.0f}")
    fig.add_bar(
        x=labels, y=df["forecast"], name=tr("Prévu", "Forecast"), marker_color=_rgba(BLUE, 0.75),
        hovertemplate="%{y:,.0f}",
    )
    fig.add_scatter(
        x=labels, y=df["ceiling"], mode="markers", name=tr("Plafond", "Ceiling"),
        marker={"symbol": "line-ew", "size": 26, "line": {"width": 2.5, "color": [status_col[s] for s in df["status"]]}},
        hovertemplate="%{y:,.0f}",
    )
    fig = _base(fig, height=height, unit="tCO₂e")
    fig.update_layout(barmode="stack")
    return fig


# =============================================================================
# Scenarios
# =============================================================================
def scenario_trajectory(df_sim: pd.DataFrame, height: int = 360) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=df_sim["date"], y=df_sim["yhat"], mode="lines", name=tr("Référence", "Baseline"),
        line={"color": NEUTRAL, "width": 1.6}, hovertemplate="%{y:.2f}",
    )
    fig.add_scatter(
        x=df_sim["date"], y=df_sim["scenario_yhat"], mode="lines", name=tr("Scénario", "Scenario"),
        line={"color": AQUA, "width": 2}, fill="tonexty", fillcolor=_rgba(AQUA, 0.12), hovertemplate="%{y:.2f}",
    )
    return _base(fig, height=height, unit=tr("tCO₂e / jour", "tCO₂e / day"))


def waterfall(baseline: float, steps: list[tuple[str, float]], final: float, height: int = 360) -> go.Figure:
    fig = go.Figure(
        go.Waterfall(
            x=[tr("Référence", "Baseline")] + [s[0] for s in steps] + [tr("Scénario", "Scenario")],
            y=[baseline] + [s[1] for s in steps] + [final],
            measure=["absolute"] + ["relative"] * len(steps) + ["total"],
            text=[num(baseline, 0)] + [("+" if s[1] > 0 else "") + num(s[1], 0) for s in steps] + [num(final, 0)],
            textposition="outside",
            cliponaxis=False,
            connector={"line": {"color": GRIDLINE, "width": 1}},
            decreasing={"marker": {"color": AQUA}},
            increasing={"marker": {"color": ORANGE}},
            totals={"marker": {"color": NEUTRAL}},
            hovertemplate="%{x}: %{y:,.1f} tCO₂e<extra></extra>",
        )
    )
    fig = _base(fig, height=height, legend=False, unit="tCO₂e")
    fig.update_layout(hovermode="closest", margin={"l": 8, "r": 8, "t": 24, "b": 8})
    lo = min(final, baseline) * 0.85
    fig.update_yaxes(range=[lo, max(final, baseline) * 1.06])
    return fig


# =============================================================================
# Data quality
# =============================================================================
def coverage_heatmap(df_daily: pd.DataFrame, height: int = 280) -> go.Figure:
    d = df_daily[["date", "is_imputed"]].copy()
    d["year"] = d["date"].dt.year
    d["doy"] = d["date"].dt.dayofyear
    d["state"] = d["is_imputed"].fillna(0).astype(int)  # 0 observed · 1 imputed
    grid = d.pivot_table(index="year", columns="doy", values="state", aggfunc="max")
    grid = grid.reindex(columns=range(1, 367))
    dates = d.pivot_table(index="year", columns="doy", values="date", aggfunc="first").reindex(columns=range(1, 367))
    z = grid.fillna(-1).values  # -1 = no data
    text = dates.map(lambda v: v.strftime("%Y-%m-%d") if pd.notna(v) else "").values
    state_text = [[{-1: tr("Absent", "Missing"), 0: tr("Observé", "Observed"), 1: tr("Imputé", "Imputed")}[int(v)] for v in row] for row in z]

    fig = go.Figure(
        go.Heatmap(
            z=z, x=list(range(1, 367)), y=[str(y) for y in grid.index],
            zmin=-1, zmax=1,
            colorscale=[[0, "rgba(128,128,128,0.18)"], [0.333, "rgba(128,128,128,0.18)"],
                        [0.334, _rgba(AQUA, 0.85)], [0.666, _rgba(AQUA, 0.85)],
                        [0.667, ORANGE], [1, ORANGE]],
            showscale=False, xgap=1, ygap=3,
            customdata=[[f"{t} · {s}" for t, s in zip(r1, r2)] for r1, r2 in zip(text, state_text)],
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    fig = _base(fig, height=height, legend=False)
    month_starts = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
    fig.update_xaxes(tickvals=month_starts, ticktext=[month_short(m) for m in range(1, 13)])
    fig.update_yaxes(showgrid=False, autorange="reversed", tickmode="array", tickvals=[str(y) for y in grid.index])
    fig.update_layout(hovermode="closest")
    return fig


def rejection_bars(quarantine: list[dict], height: int = 300) -> go.Figure:
    counts: dict[str, int] = {}
    for q in quarantine:
        for err in q.get("errors", []) or ["?"]:
            key = str(err).split(":")[0].strip()[:48]
            counts[key] = counts.get(key, 0) + 1
    items = sorted(counts.items(), key=lambda kv: kv[1])[-8:]
    fig = go.Figure(
        go.Bar(
            y=[k for k, _ in items], x=[v for _, v in items], orientation="h", marker_color=ORANGE,
            text=[v for _, v in items], textposition="outside", cliponaxis=False,
            hovertemplate="%{y}: %{x}<extra></extra>",
        )
    )
    fig = _base(fig, height=height, legend=False)
    fig.update_layout(hovermode="closest", margin={"l": 8, "r": 30, "t": 8, "b": 8})
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, showticklabels=False)
    fig.update_yaxes(showgrid=False)
    return fig


# =============================================================================
# Methodology
# =============================================================================
_MODEL_ORDER = ["oracle", "chronos", "ens_pc", "seasonal_naive", "prophet"]


def _ordered_models(bt: pd.DataFrame) -> list[str]:
    present = list(bt["model_name"].unique())
    return [m for m in _MODEL_ORDER if m in present] + [m for m in present if m not in _MODEL_ORDER]


def metric_by_window(bt: pd.DataFrame, metric: str, *, ref: float | None = None, ref_label: str = "",
                     pct: bool = False, height: int = 320) -> go.Figure:
    fig = go.Figure()
    for m in _ordered_models(bt):
        sub = bt[bt["model_name"] == m].sort_values("history_months")
        y = sub[metric] * (100 if pct else 1)
        fig.add_bar(
            x=[f"{h} {tr('mois', 'mo')}" for h in sub["history_months"]], y=y,
            name=MODEL_NAMES.get(m, m), marker_color=MODEL_COLORS.get(m, VIOLET),
            hovertemplate="%{y:.1f}%" if pct else "%{y:.3f}",
        )
    if ref is not None:
        fig.add_hline(y=ref, line={"color": CRIT, "width": 1.5, "dash": "dash"})
        fig.add_annotation(x=0, xref="paper", y=ref, text=ref_label, showarrow=False, xanchor="left",
                           yanchor="bottom", font={"size": 11, "color": CRIT})
    fig = _base(fig, height=height)
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
    if pct:
        fig.update_yaxes(ticksuffix="%", range=[0, 100])
    return fig
