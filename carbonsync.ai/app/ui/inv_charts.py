"""Charts of the target path, the breakdown, the GHG inventory and the alerts.

Same rules as ``charts``: colours follow the entity (each zone keeps its colour whatever the period or
filter), one y-axis, light grid, unified hover. Rendered with ``charts.show``.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from app.core.i18n import tr
from app.ui.charts import (
    AQUA,
    BLUE,
    GRIDLINE,
    MAGENTA,
    NEUTRAL,
    ORANGE,
    YELLOW,
    _base,
    _rgba,
)
from app.ui.format import month_short
from app.ui.theme import CRIT

# Zones keep their colour everywhere (fixed order of the validated palette)
ZONE_COLORS = {"P1": BLUE, "P2": ORANGE, "P3": AQUA, "UT": YELLOW, "LOG": MAGENTA}


def zone_color(zone: str) -> str:
    return ZONE_COLORS.get(zone, NEUTRAL)


def path_chart(path: pd.DataFrame, projected: dict | None = None, height: int = 330) -> go.Figure:
    """Actual annual emissions (bars) against the straight-line path (line); the current year's projection."""
    fig = go.Figure()
    done = path.dropna(subset=["actual"])
    fig.add_bar(x=done["year"], y=done["actual"], name=tr("Réel", "Actual"), marker_color=NEUTRAL,
                hovertemplate="%{y:,.0f} t")
    if projected:
        fig.add_bar(x=[projected["year"]], y=[projected["total"]], name=tr("Projection de l'année", "Year projection"),
                    marker_color=_rgba(BLUE, 0.55),
                    error_y={"type": "data", "symmetric": False, "array": [projected["hi"] - projected["total"]],
                             "arrayminus": [projected["total"] - projected["lo"]], "color": BLUE, "thickness": 1.5},
                    hovertemplate="%{y:,.0f} t")
    fig.add_scatter(x=path["year"], y=path["cap"], mode="lines+markers", name=tr("Trajectoire (plafond)", "Path (cap)"),
                    line={"color": BLUE, "width": 2}, marker={"size": 8, "color": BLUE}, hovertemplate="%{y:,.0f} t")
    fig = _base(fig, height=height, unit="tCO₂e")
    fig.update_xaxes(dtick=1)
    fig.update_layout(bargap=0.35)
    return fig


def weights_chart(weights: dict[int, float], height: int = 240) -> go.Figure:
    months = sorted(weights)
    fig = go.Figure(go.Bar(x=[month_short(m) for m in months], y=[weights[m] * 100 for m in months],
                           marker_color=BLUE, hovertemplate="%{y:.1f} %<extra></extra>"))
    fig = _base(fig, height=height, legend=False, unit=tr("% de l'année", "% of the year"))
    fig.add_hline(y=100 / 12, line={"color": NEUTRAL, "width": 1, "dash": "dot"})
    fig.update_layout(hovermode="closest")
    return fig


def zone_bars(zones: pd.DataFrame, label_col: str, height: int = 300) -> go.Figure:
    z = zones.iloc[::-1]
    fig = go.Figure(go.Bar(
        y=z[label_col], x=z["t"], orientation="h", marker_color=[zone_color(c) for c in z["zone"]],
        text=[f"{p:.0f}%" for p in z["share_pct"]], textposition="outside", cliponaxis=False,
        customdata=z["owner"], hovertemplate="%{y}: %{x:,.0f} tCO₂e · %{customdata}<extra></extra>"))
    fig = _base(fig, height=height, legend=False)
    fig.update_layout(hovermode="closest", bargap=0.4, margin={"l": 8, "r": 44, "t": 8, "b": 8})
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, title={"text": "tCO₂e"})
    fig.update_yaxes(showgrid=False)
    return fig


def items_bars(items: pd.DataFrame, item_col: str, zone_col: str, top: int = 12, height: int = 420) -> go.Figure:
    it = items.head(top).iloc[::-1]
    fig = go.Figure(go.Bar(
        y=it[item_col], x=it["t"], orientation="h", marker_color=[zone_color(c) for c in it["zone"]],
        customdata=it[zone_col], hovertemplate="%{y} (%{customdata}): %{x:,.0f} tCO₂e<extra></extra>"))
    fig = _base(fig, height=height, legend=False)
    fig.update_layout(hovermode="closest", bargap=0.35)
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, title={"text": "tCO₂e"})
    fig.update_yaxes(showgrid=False)
    return fig


def zone_monthly(alloc: pd.DataFrame, label_col: str, height: int = 330) -> go.Figure:
    """Stacked monthly emissions by zone (``alloc``: rows of ``breakdown.allocate(..., freq="M")``)."""
    g = alloc.groupby(["period", "zone", label_col])["t"].sum().reset_index()
    g["period"] = pd.to_datetime(g["period"])
    fig = go.Figure()
    for zone in [z for z in ZONE_COLORS if z in set(g["zone"])] + sorted(set(g["zone"]) - set(ZONE_COLORS)):
        part = g[g["zone"] == zone]
        fig.add_bar(x=part["period"], y=part["t"], name=str(part[label_col].iloc[0]), marker_color=zone_color(zone),
                    hovertemplate="%{y:,.0f}")
    fig = _base(fig, height=height, unit="tCO₂e")
    fig.update_layout(barmode="stack", bargap=0.22)
    fig.update_xaxes(tickformat="%b %y")
    return fig


def scope2_years(df: pd.DataFrame, height: int = 300) -> go.Figure:
    """Scope 2 by year, location-based and market-based side by side."""
    fig = go.Figure()
    fig.add_bar(x=df["year"], y=df["location_t"], name=tr("Méthode localisation", "Location-based"), marker_color=BLUE,
                hovertemplate="%{y:,.0f}")
    fig.add_bar(x=df["year"], y=df["market_t"], name=tr("Méthode marché", "Market-based"), marker_color=YELLOW,
                hovertemplate="%{y:,.0f}")
    fig = _base(fig, height=height, unit="tCO₂e")
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig.update_xaxes(dtick=1)
    return fig


def scope3_bars(by_cat: pd.DataFrame, label_col: str, height: int = 300) -> go.Figure:
    c = by_cat.iloc[::-1]
    fig = go.Figure(go.Bar(y=[f"{int(k)}. {v}" for k, v in zip(c["category"], c[label_col])], x=c["t"],
                           orientation="h", marker_color=AQUA, hovertemplate="%{y}: %{x:,.0f} tCO₂e<extra></extra>"))
    fig = _base(fig, height=height, legend=False)
    fig.update_layout(hovermode="closest", bargap=0.4)
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, title={"text": "tCO₂e"})
    fig.update_yaxes(showgrid=False)
    return fig


def anomaly_chart(recent: pd.DataFrame, band: pd.DataFrame, anomalies: pd.DataFrame, height: int = 340) -> go.Figure:
    """Actual days against the 90 % band of the forecast made before them; days outside marked."""
    fig = go.Figure()
    if not band.empty:
        fig.add_scatter(x=band["date"], y=band["hi90"], mode="lines", line={"width": 0}, showlegend=False,
                        hoverinfo="skip")
        fig.add_scatter(x=band["date"], y=band["lo90"], mode="lines", line={"width": 0}, fill="tonexty",
                        fillcolor=_rgba(BLUE, 0.16), name=tr("Bande 90 % de la prévision", "Forecast 90% band"),
                        hoverinfo="skip")
        fig.add_scatter(x=band["date"], y=band["yhat"], mode="lines", line={"color": BLUE, "width": 2, "dash": "dot"},
                        name=tr("Prévu", "Forecast"), hovertemplate="%{y:.2f}")
    fig.add_scatter(x=recent["date"], y=recent["co2e_total_t"], mode="lines", line={"color": NEUTRAL, "width": 2},
                    name=tr("Réel", "Actual"), hovertemplate="%{y:.2f}")
    if not anomalies.empty:
        fig.add_scatter(x=anomalies["date"], y=anomalies["actual"], mode="markers",
                        marker={"color": CRIT, "size": 10, "symbol": "circle", "line": {"width": 2, "color": "white"}},
                        name=tr("Hors bande", "Outside the band"), hovertemplate="%{y:.2f}")
    fig = _base(fig, height=height, unit=tr("tCO₂e / jour", "tCO₂e / day"))
    return fig
