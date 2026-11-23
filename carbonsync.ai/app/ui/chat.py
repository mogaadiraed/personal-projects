"""Assistant chat rendering: messages, live tool activity, charts from tool results, sources."""
from __future__ import annotations

import json
from html import escape
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.core.i18n import lang, tr
from app.ui import charts as ch
from app.ui import components as ui
from app.ui.format import date_label, month_short, num, pct
from app.ui.theme import CRIT, GOOD, WARN

USER_AVATAR = ":material/person:"
BOT_AVATAR = ":material/eco:"


# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------
def _done_label(rec: dict[str, Any]) -> str:
    return rec["label"].rstrip("…").rstrip(".")


def tool_chips(records: list[dict[str, Any]]) -> None:
    if not records:
        return
    html = "".join(
        f'<span class="cs-chip {"accent" if r.get("ok") else "warn"}"><span class="dot"></span>'
        f'{escape(_done_label(r))} · {num(r.get("seconds") or 0, 2)} s</span>'
        for r in records
    )
    st.html(f'<div class="cs-chips cs-tools">{html}</div>')


def _cloud_line(turn: dict[str, Any]) -> str:
    """Expert mode: cost, cache, brief version and what was sent."""
    if turn.get("privacy") != "aggregated":
        return ""
    sent = sum(len(json.dumps(r.get("result"), ensure_ascii=False, default=str)) for r in turn["tools"] if r.get("ok"))
    cost = turn.get("cost_usd") or 0.0
    money = f"{num(cost, 4)} $" if lang() == "fr" else f"${num(cost, 4)}"
    served = turn.get("served_by")
    lines = [
        tr(f"**Coût estimé** {money} · {num(turn.get('cache_read') or 0)} jetons lus depuis le cache",
           f"**Estimated cost** {money} · {num(turn.get('cache_read') or 0)} tokens read from cache"),
        tr(f"**Envoyé au cloud** : question, fiche expert v{turn.get('brief') or '?'}, contexte et "
           f"{len(turn['tools'])} résultat(s) d'outils agrégés ({num(sent / 1000, 1)} ko)",
           f"**Sent to the cloud**: question, expert brief v{turn.get('brief') or '?'}, context and "
           f"{len(turn['tools'])} aggregated tool result(s) ({num(sent / 1000, 1)} kB)"),
    ]
    if served:
        lines.append(tr(f"**Repli serveur** : réponse produite par {served}", f"**Server fallback**: answered by {served}"))
    return "  \n" + "  \n".join(lines)


def sources(turn: dict[str, Any], key: str) -> None:
    with st.expander(tr("Sources et détails", "Sources and details"), icon=":material/fact_check:"):
        local = turn.get("privacy") == "full"
        st.markdown(
            tr(f"**Moteur** : {turn['label']} · {'local, rien n’a quitté ce PC' if local else 'cloud, données agrégées uniquement'}",
               f"**Engine**: {turn['label']} · {'local, nothing left this PC' if local else 'cloud, aggregated data only'}")
            + "  \n"
            + tr(f"**Données** au {date_label(pd.Timestamp(turn['data_until']))} · prévision `{turn.get('forecast_run') or '—'}`",
                 f"**Data** to {date_label(pd.Timestamp(turn['data_until']))} · forecast `{turn.get('forecast_run') or '—'}`")
            + "  \n"
            + tr(f"**Durée** {num(turn['seconds'], 1)} s · {turn['rounds']} appel(s) au modèle · "
                 f"{num(turn['input_tokens'])} jetons lus / {num(turn['output_tokens'])} écrits",
                 f"**Time** {num(turn['seconds'], 1)} s · {turn['rounds']} model call(s) · "
                 f"{num(turn['input_tokens'])} tokens read / {num(turn['output_tokens'])} written")
            + _cloud_line(turn)
        )
        if turn["tools"]:
            rows = [{tr("Outil", "Tool"): r["name"], tr("Paramètres", "Arguments"): json.dumps(r.get("args") or {}, ensure_ascii=False),
                     tr("Statut", "Status"): "✓" if r.get("ok") else f"✗ {r.get('error') or ''}",
                     tr("Durée (s)", "Time (s)"): round(r.get("seconds") or 0, 3)} for r in turn["tools"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", key=f"{key}_src")
        else:
            st.caption(tr("Aucun outil utilisé pour cette réponse.", "No tool used for this answer."))


# ---------------------------------------------------------------------------
# Charts built from tool results (only data the answer is based on)
# ---------------------------------------------------------------------------
def _mlabel(value: Any, year: bool = False) -> str:
    ts = pd.Timestamp(str(value)[:7] + "-01") if len(str(value)) <= 7 else pd.Timestamp(value)
    return f"{month_short(ts.month)} {str(ts.year)[2:]}" if year else month_short(ts.month)


def _month_labels(values: list[str]) -> list[str]:
    years = {str(v)[:4] for v in values}
    return [_mlabel(v, year=len(years) > 1) for v in values]


def _analyse_card(res: dict[str, Any], key: str) -> None:
    p, red = res["projection"], res.get("reductions")
    gap = p["gap_t"]
    items = [
        ui.Kpi(tr(f"Projection {p['year']}", f"{p['year']} projection"), num(p["total_t"], 0), "tCO₂e",
               tr(f"plafond {num(p['cap'], 0)}", f"cap {num(p['cap'], 0)}")),
        ui.Kpi(tr("Écart au plafond", "Gap to cap"), ("+" if gap > 0 else "") + num(gap, 0), "t",
               tr("dépassement", "over") if gap > 0 else tr("marge", "headroom"), "bad" if gap > 0 else "good"),
    ]
    if red:
        rest = next((w for w in red["windows"] if w["window"] == "rest_of_year"), red["windows"][-1])
        el = rest["if_only_electricity"]
        items += [
            ui.Kpi(tr("Effort sur chaque source", "Cut on every source"), pct(-red["uniform_cut_pct"], 1), "",
                   tr("jusqu'au 31 déc.", "to 31 Dec")),
            ui.Kpi(tr("Ou électricité seule", "Or electricity alone"), "−" + num(el["cut_kwh_per_day"], 0),
                   tr("kWh/jour", "kWh/day"), pct(-el["pct_of_current_level"], 1)),
        ]
    ui.kpis(items)
    months = pd.DataFrame(p["months"])
    months["label"] = [_mlabel(m) for m in months["month"]]
    names = {"actual": tr("Réel", "Actual"), "forecast": tr("Prévu", "Forecast"),
             "estimate": tr("Estimé (au-delà de la prévision)", "Estimated (beyond forecast)")}
    colors = {"actual": ch.NEUTRAL, "forecast": ch._rgba(ch.BLUE, 0.8), "estimate": ch._rgba(ch.BLUE, 0.35)}
    fig = go.Figure()
    for basis in ("actual", "forecast", "estimate"):
        part = months[months["basis"] == basis]
        if not part.empty:
            fig.add_bar(x=part["label"], y=part["projected"], name=names[basis], marker_color=colors[basis],
                        hovertemplate="%{y:,.0f} t")
    fig.add_scatter(x=months["label"], y=months["ceiling"], mode="markers", name=tr("Plafond du mois", "Month ceiling"),
                    marker={"symbol": "line-ew", "size": 24,
                            "line": {"width": 2.5, "color": [CRIT if o else GOOD for o in months["over_t"] > 0]}},
                    hovertemplate="%{y:,.0f} t")
    fig = ch._base(fig, height=280, unit="tCO₂e")
    fig.update_layout(barmode="overlay")
    ch.show(fig, key=f"{key}_an")


def _budget_chart(res: dict[str, Any], key: str) -> None:
    df = pd.DataFrame(res["months"])
    df["label"] = [_mlabel(m) for m in df["month"]]
    status_col = {"green": GOOD, "amber": WARN, "red": CRIT, "nodata": ch.NEUTRAL}
    fig = go.Figure()
    fig.add_bar(x=df["label"], y=df["actual"], name=tr("Réel", "Actual"), marker_color=ch.NEUTRAL, hovertemplate="%{y:,.0f} t")
    if "forecast" in df:
        fig.add_bar(x=df["label"], y=df["forecast"], name=tr("Prévu", "Forecast"), marker_color=ch._rgba(ch.BLUE, 0.75),
                    hovertemplate="%{y:,.0f} t")
    if "estimate" in df and df["estimate"].notna().any():
        fig.add_bar(x=df["label"], y=df["estimate"], name=tr("Estimé (au-delà de la prévision)", "Estimated (beyond forecast)"),
                    marker_color=ch._rgba(ch.BLUE, 0.35), hovertemplate="%{y:,.0f} t")
    if "over_ceiling" in df:
        colors = [CRIT if o else GOOD for o in df["over_ceiling"]]
    else:
        colors = [status_col.get(s, ch.NEUTRAL) for s in df["status"]]
    fig.add_scatter(x=df["label"], y=df["ceiling"], mode="markers", name=tr("Plafond", "Ceiling"),
                    marker={"symbol": "line-ew", "size": 24, "line": {"width": 2.5, "color": colors}},
                    hovertemplate="%{y:,.0f} t")
    fig = ch._base(fig, height=280, unit="tCO₂e")
    fig.update_layout(barmode="stack")
    ch.show(fig, key=f"{key}_bud")


def _table_frame(tb: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(tb["rows"], columns=tb["columns"])


def _forecast_chart(res: dict[str, Any], key: str) -> None:
    df = _table_frame(res["table"])
    x = _month_labels(df["period"].tolist()) if res["granularity"] == "month" else df["period"].str.replace("week of ", "")
    fig = go.Figure()
    fig.add_scatter(x=x, y=df["hi90_t"], mode="lines", line={"width": 0}, showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=x, y=df["lo90_t"], mode="lines", line={"width": 0}, fill="tonexty",
                    fillcolor=ch._rgba(ch.BLUE, 0.16), name=tr("Intervalle 90 %", "90% interval"), hovertemplate="%{y:,.1f}")
    fig.add_scatter(x=x, y=df["yhat_t"], mode="lines+markers" if len(df) < 40 else "lines", name=tr("Prévision", "Forecast"),
                    line={"color": ch.BLUE, "width": 2}, marker={"size": 8}, hovertemplate="%{y:,.1f}")
    ch.show(ch._base(fig, height=260, unit="tCO₂e"), key=f"{key}_fc")


_SCOPE_METRICS = {"scope1_t": ("Scope 1", ch.ORANGE), "scope2_t": ("Scope 2", ch.BLUE),
                  "other_t": (tr("Autres", "Other"), ch.AQUA)}


def _emissions_chart(res: dict[str, Any], key: str) -> None:
    df = _table_frame(res["table"])
    if df.empty:
        return
    g = res["granularity"]
    x = _month_labels(df["period"].tolist()) if g == "month" else df["period"].astype(str).str.replace("week of ", "")
    fig = go.Figure()
    scopes = [m for m in _SCOPE_METRICS if m in df]
    if scopes and len(df) <= 60:
        for m in scopes:
            name, color = _SCOPE_METRICS[m]
            fig.add_bar(x=x, y=df[m], name=name, marker_color=color, hovertemplate="%{y:,.1f} t")
        fig.update_layout(barmode="stack")
        unit = "tCO₂e"
    else:
        metric = "total_t" if "total_t" in df else next((c for c in df.columns if c not in ("period", "days", "complete")), None)
        if metric is None:
            return
        fig.add_scatter(x=x, y=df[metric], mode="lines", name=metric, line={"color": ch.NEUTRAL, "width": 1.6},
                        hovertemplate="%{y:,.2f}")
        unit = "tCO₂e" if metric.endswith("_t") else metric
    ch.show(ch._base(fig, height=260, unit=unit, legend=len(fig.data) > 1), key=f"{key}_em")


def _compare_chart(res: dict[str, Any], key: str) -> None:
    dec = res.get("decomposition")
    if not dec:
        return
    names = {"electricity": tr("Électricité", "Electricity"), "fuel_oil": tr("Fioul", "Fuel oil"),
             "diesel": tr("Gazole", "Diesel"), "waste": tr("Déchets", "Waste"), "water": tr("Eau", "Water")}
    src = dec["by_source"]
    y = [names.get(s["key"], s["key"]) for s in src]
    fig = go.Figure()
    fig.add_bar(y=y, x=[s["volume_t_per_day"] for s in src], orientation="h", name=tr("Effet volume", "Volume effect"),
                marker_color=ch.ORANGE, hovertemplate="%{x:+.3f} t/j")
    fig.add_bar(y=y, x=[s["intensity_t_per_day"] for s in src], orientation="h",
                name=tr("Effet intensité", "Intensity effect"), marker_color=ch.BLUE, hovertemplate="%{x:+.3f} t/j")
    fig = ch._base(fig, height=240, unit=None)
    fig.update_layout(barmode="relative", hovermode="y unified")
    fig.update_xaxes(title={"text": tr("tCO₂e / jour", "tCO₂e / day"), "font": {"size": 11}}, showgrid=True,
                     gridcolor=ch.GRIDLINE, zeroline=True, zerolinecolor="rgba(128,128,128,.5)")
    fig.update_yaxes(showgrid=False)
    ch.show(fig, key=f"{key}_cmp")


_CHARTS = {"analyse": _analyse_card, "get_budget": _budget_chart, "get_forecast": _forecast_chart,
           "query_emissions": _emissions_chart, "compare_periods": _compare_chart}


def result_visuals(turn: dict[str, Any], key: str, limit: int = 2) -> None:
    """Charts for the tools behind the answer: last call of each kind, at most ``limit``."""
    latest: dict[str, dict[str, Any]] = {}
    for r in turn["tools"]:
        if r.get("ok") and r["name"] in _CHARTS and r.get("result"):
            latest[r["name"]] = r
    if "analyse" in latest:  # the analysis card already shows the budget months
        latest.pop("get_budget", None)
    for i, r in enumerate(list(latest.values())[-limit:]):
        try:
            _CHARTS[r["name"]](r["result"], f"{key}_{i}")
        except (KeyError, TypeError, ValueError) as exc:  # a chart must never break the chat
            st.caption(tr(f"Graphique indisponible ({type(exc).__name__}).", f"Chart unavailable ({type(exc).__name__})."))


# ---------------------------------------------------------------------------
# Whole turns
# ---------------------------------------------------------------------------
def render_turn(turn: dict[str, Any], key: str) -> None:
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(turn["question"])
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        tool_chips(turn["tools"])
        if turn.get("notice"):
            st.caption("⚠️ " + turn["notice"] + " " + tr("Réponse de l'assistant intégré.", "Answered by the built-in assistant."))
        if turn.get("thinking"):
            with st.expander(tr("Réflexion du modèle", "Model reasoning"), icon=":material/psychology:"):
                st.caption(turn["thinking"])
        if turn.get("answer"):
            st.markdown(turn["answer"])
        unverified_note(turn)
        result_visuals(turn, key)
        if turn.get("error"):
            st.error(turn["error"], icon=":material/error:")
        sources(turn, key)


def unverified_note(turn: dict[str, Any]) -> None:
    """Figures of the answer that are not in the data the assistant read (computed or invented)."""
    figs = turn.get("unverified") or []
    if not figs:
        return
    shown = " · ".join(figs[:8]) + (" …" if len(figs) > 8 else "")
    st.caption(":material/warning: " + tr(
        f"{len(figs)} chiffre(s) non retrouvé(s) dans les données consultées (calculés ou supposés par le modèle) : "
        f"{shown}. À vérifier, ou demandez l'analyse pour des chiffres exacts.",
        f"{len(figs)} figure(s) not found in the data read (computed or assumed by the model): {shown}. "
        "Check them, or ask for the analysis for exact figures."))


def stream_turn(events, key: str) -> dict[str, Any] | None:
    """Render a turn live from engine events; returns the finished turn record."""
    done: dict[str, Any] | None = None
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        status = st.status(tr("Lecture de la question…", "Reading the question…"), expanded=False)
        notice_ph = st.empty()
        text_ph = st.empty()
        text, thinking, n_tools, rounds = "", "", 0, 0
        for ev in events:
            if ev.kind == "model":
                rounds += 1
                if rounds > 1:
                    status.update(label=tr("Rédaction de la réponse…", "Writing the answer…"))
            elif ev.kind == "thinking":
                thinking += ev.text
                status.update(label=tr("Réflexion…", "Thinking…"))
            elif ev.kind == "tool_start":
                status.update(label=ev.record["label"])
            elif ev.kind == "tool_end":
                n_tools += 1
                mark = "✓" if ev.record.get("ok") else "✗"
                status.write(f"{mark} {_done_label(ev.record)} · {num(ev.record.get('seconds') or 0, 2)} s")
            elif ev.kind == "text":
                text += ev.text
                text_ph.markdown(text + " ▌")
            elif ev.kind == "notice":
                notice_ph.caption("⚠️ " + ev.text + " " + tr("Réponse de l'assistant intégré.",
                                                              "Answered by the built-in assistant."))
            elif ev.kind == "done":
                done = ev.turn
        if done is None:
            status.update(label=tr("Interrompu", "Interrupted"), state="error")
            return None
        secs = num(done["seconds"], 1)
        label = (tr(f"{n_tools} outil(s) · {secs} s", f"{n_tools} tool(s) · {secs} s") if n_tools
                 else tr(f"Réponse · {secs} s", f"Answered · {secs} s"))
        status.update(
            label=label,
            state="error" if done.get("error") and not done.get("answer") else "complete", expanded=False)
        if done.get("answer"):
            text_ph.markdown(done["answer"])
        else:
            text_ph.empty()
        unverified_note(done)
        if thinking:
            with st.expander(tr("Réflexion du modèle", "Model reasoning"), icon=":material/psychology:"):
                st.caption(thinking)
        result_visuals(done, key)
        if done.get("error"):
            st.error(done["error"], icon=":material/error:")
        sources(done, key)
    return done


def welcome(suggestions: list[tuple[str, str, str]], on_pick_key: str) -> None:
    """Empty-chat screen: what the assistant can do and suggested questions."""
    title = tr("Posez une question sur vos émissions", "Ask about your emissions")
    sub = tr("Chaque chiffre vient des données du tableau de bord, avec ses sources. L’assistant ne modifie jamais "
             "les données.", "Every figure comes from the dashboard data, with its sources. The assistant never "
             "changes the data.")
    st.html(f'<div class="cs-assist-hello"><div class="cs-section">{escape(title)}</div>'
            f'<div class="cs-section-sub">{escape(sub)}</div></div>')
    cols = st.columns(3)
    for i, (fr, en, icon) in enumerate(suggestions):
        label = fr if lang() == "fr" else en
        if cols[i % 3].button(label, icon=f":material/{icon}:", key=f"sugg_{i}", width="stretch"):
            st.session_state[on_pick_key] = label
            st.rerun()
