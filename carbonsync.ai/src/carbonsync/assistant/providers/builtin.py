"""Built-in assistant: works with no model at all (nothing to download, nothing to start).

It behaves like a tiny model inside the same engine: first it picks one tool from keywords
in the question, then it writes a templated FR/EN answer from that tool's result. No free
conversation, but every figure is exact and sources and charts work the same way.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pandas as pd

from carbonsync.assistant.knowledge import fold
from carbonsync.assistant.providers.base import Delta, Provider, ToolCall, Turn
from carbonsync.export.insights import Fmt

_ANALYSE = ("analys", "analyz", "redui", "reduc", "baiss", "diminu", "minimi", "stabilis", "plan d'action",
            "action plan", "reduce", "lower", "cut ", "conseil", "advice", "tips", "astuce", "levier", "lever",
            "recommand", "recommend", "hotspot", "point chaud", "objectif", "target")
_WHY = ("pourquoi", "why ", "hausse", "augment", "increase", "rise", "cause", "explique la", "explain the")
_GREET = re.compile(r"^\s*(bonjour|bonsoir|salut|coucou|hello|hi|hey|merci|thanks|thank you)\b[\s!.,?]*$")
_HOWTO = re.compile(r"^\s*(comment\b|ou\b(?! en est)|how\b(?! much| many)|where\b|what does|what is|what's|qu.est|c.est quoi"
                    r"|a quoi|que fait|que signifie|explain|explique|aide|help)")
_GUIDE = ("page", "bouton", "button", "onglet", "tab ", "graphique", "chart", "upload", "deposer", "depot", "ajouter",
          "telecharg", "download", "configur", "reglage", "setting", "reinitialis", "reset", "attente", "waiting",
          "signifie", "meaning", "laboratoire", "model lab", "navigation", "menu")
_DATA = [  # (tool, keywords) — first best score wins
    ("get_alerts", ("alerte", "alert", "anomal", "notification", "avertissement", "critique", "critical",
                    "hors de la bande", "outside the band", "n'envoie plus", "stopped sending")),
    ("get_breakdown", ("zone", "repartition", "breakdown", "atelier", "workshop", "decoupe", "cutting", "preparation",
                       "assemblage", "assembly", "utilites", "utilities", "logistique", "logistics", "compresseur",
                       "compressor", "fours", "oven", "chariot", "forklift", " p1 ", " p2 ", " p3 ", "sous-categorie",
                       "subcategor", "par poste", "by item")),
    ("get_inventory", ("inventaire", "inventory", "localisation", "location-based", "scope 3", "scope3", "methode marche", "market-based", "market based",
                       "mix residuel", "residual mix", "ppa", "contrat", "contract", "cuivre", "copper", "pvc",
                       "trajets", "commuting", "domicile", "categorie", "category", "achats", "purchased")),
    ("get_budget", ("budget", "plafond", "ceiling", "cap ", "depass", "over budget", "statut", "status", "trajectoire",
                    "path", "annee de reference", "base year", "2030")),
    ("get_forecast", ("prevision", "prevoi", "forecast", "futur", "prochain", "next ", "a venir", "horizon")),
    ("get_data_quality", ("qualite", "quality", "quarantaine", "quarantine", "rejet", "reject", "impute", "fichier",
                          "file", "manquant", "missing")),
    ("get_model_performance", ("modele", "model", "backtest", "mase", "mae", "precision", "accuracy", "routeur",
                               "router", "prophet", "chronos", "ens-pc", "fiable", "reliable")),
    ("get_methodology", ("facteur", "factor", "methodolog", "formule", "formula", "calcul", "ghg", "esrs", "csrd")),
    ("get_pipeline_activity", ("pipeline", "historique", "history", "dernier depot", "last upload", "execution", "run ")),
    ("get_overview", ("resume", "summary", "situation", "etat", "overview", "kpi", "bilan", "total", "emission", "emit",
                      "emis", "carbon", "co2", "derniere annee", "last year", "12 mois", "12 months",
                      "scope", "ou en est", "how are we", "point")),
]


# What-if levers of the Scenarios page: keywords, (min, max, value when named without a number)
_LEVERS: dict[str, tuple[tuple[str, ...], tuple[float, float, float | None]]] = {
    "solar_pct": (("solaire", "solar", "photovolta", "panneaux", "autoconsommation", "self-consumption",
                   "self consumption"), (0, 40, 10.0)),
    "efficiency_pct": (("efficacite", "efficiency"), (0, 15, 5.0)),
    "production_pct": (("production", "volume"), (-20, 20, None)),   # only with an explicit percentage
}
_WHATIF = ("scenario", "simul", "et si", "what if", "hypoth", "suppos", " si ", " if ", "effet", "effect", "impact",
           "gagn", "save", "econom", "evit", "avoid")
_PCT = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:%|pour ?cent|percent)")
_DOWN = ("baisse", "diminu", "recul", "moins", "drop", "fall", "decreas", "lower", "less")


def _scenario_args(q: str) -> dict[str, float] | None:
    """Lever percentages of a what-if question ("15 % de solaire et 5 % d'efficacité"), else None."""
    spans = {lever: [(m.start(), m.end()) for kw in kws for m in re.finditer(re.escape(kw), q)]
             for lever, (kws, _lim) in _LEVERS.items()}
    if not any(spans.values()):
        return None
    args: dict[str, float] = {}
    for m in _PCT.finditer(q):
        # the closest lever word, before ("solaire à 15 %") or after ("15 % de solaire") the number
        near = [(min(m.start() - e if e <= m.start() else s - m.end() for s, e in sp), lever)
                for lever, sp in spans.items() if sp and lever not in args]   # each lever takes one number
        if not near:
            continue
        dist, lever = min(near)
        if dist <= 30:
            value = float(m.group(1).replace(",", "."))
            if lever == "production_pct" and not m.group(1)[0] in "+-" and any(w in q for w in _DOWN):
                value = -value
            args[lever] = value
    named = [lever for lever, sp in spans.items() if sp and _LEVERS[lever][1][2] is not None]
    if not args and not (named and any(w in q for w in _WHATIF)):
        return None
    for lever in named:
        args.setdefault(lever, _LEVERS[lever][1][2])
    return {k: max(_LEVERS[k][1][0], min(_LEVERS[k][1][1], v)) for k, v in args.items()}


def route(question: str) -> tuple[str, dict[str, Any]]:
    """Which tool answers this question, with its arguments."""
    return _route(question)[:2]


def route_confident(question: str) -> tuple[str, dict[str, Any]] | None:
    """The tool only when a keyword clearly points to it (not a greeting, not the catch-all guess).

    Used to give a local model the right data before its first call."""
    name, args, sure = _route(question)
    return (name, args) if sure else None


def _route(question: str) -> tuple[str, dict[str, Any], bool]:
    q = " " + fold(question) + " "
    if _GREET.match(q):
        return "", {}, False
    if not any(k in q for k in _GUIDE) and (levers := _scenario_args(q)):
        return "run_scenario", levers, True
    if any(k in q for k in _ANALYSE):
        return "analyse", {"months": _months(q)}, True
    # the overview is the catch-all: its generic words ("emission", "total"…) count less than specific ones
    scores = [(sum(k in q for k in kws) * (0.4 if name == "get_overview" else 1.0), name) for name, kws in _DATA]
    best, name = max(scores, key=lambda x: x[0])
    guide = any(k in q for k in _GUIDE)
    if guide or (_HOWTO.search(q.strip()) and not best and not any(k in q for k in _WHY)):
        return "explain_dashboard", {"question": question}, True
    if any(k in q for k in _WHY):
        return "analyse", {"months": _months(q)}, True
    years = sorted({int(y) for y in re.findall(r"\b(20\d\d)\b", q)})
    if years and (not best or name == "get_overview"):
        # "how much in 2025 (vs 2024)": calendar-year totals, with the year before when only one is named
        first = years[0] - 1 if len(years) == 1 else years[0]
        return "query_emissions", {"start": f"{first}-01-01", "end": f"{years[-1]}-12-31", "granularity": "year"}, True
    if best:
        args: dict[str, Any] = {}
        if name == "get_budget" and (y := re.search(r"\b(20\d\d)\b", q)) and y.group(1) != "2030":
            args["year"] = int(y.group(1))
        if name == "get_breakdown" and (z := re.search(r"\b(p[123])\b", q)):
            args["zone"] = z.group(1).upper()
        return name, args, True
    return "explain_dashboard", {"question": question}, False


def _months(q: str) -> int:
    if m := re.search(r"\b(\d{1,2})\s*(?:[a-z]+\s+){0,2}(mois|months?)\b", q):
        return max(1, min(12, int(m.group(1))))
    if "semestre" in q or "half" in q or "six" in q:
        return 6
    if "annee" in q or " an " in q or "year" in q or "12" in q:
        return 12
    return 3


class BuiltinProvider(Provider):
    key = "builtin"
    label = "Assistant intégré"
    uses_model = False

    def __init__(self, lang: str = "fr") -> None:
        super().__init__("")
        self.lang = lang
        self.label = "Assistant intégré" if lang == "fr" else "Built-in assistant"

    def stream(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
               think: bool = False, final: bool = False) -> Iterator[Delta | Turn]:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        results = [m for m in messages[last_user + 1:] if m["role"] == "tool"]
        f = Fmt(self.lang)
        if not results:
            name, args = route(messages[last_user]["content"])
            if not name:
                text = _welcome(f)
                yield Delta("text", text)
                yield Turn(text=text)
                return
            yield Turn(tool_calls=[ToolCall(f"call_builtin_{len(messages)}", name, args)], stop="tool_use")
            return
        parts = []
        for m in results:
            payload = json.loads(m["content"])
            if not payload.get("ok"):
                parts.append(f.t(f"Je n'ai pas pu lire ces données : {payload.get('error')}",
                                 f"I could not read that data: {payload.get('error')}"))
                continue
            parts.append(_ANSWERS.get(m["name"], _generic)(f, payload["result"]))
        text = "\n\n".join(parts)
        yield Delta("text", text)
        yield Turn(text=text)


# ---------------------------------------------------------------------------
# Templated answers
# ---------------------------------------------------------------------------
def _d(f: Fmt, iso: str | None) -> str:
    return f.date(pd.Timestamp(iso)) if iso else "—"


def _m(f: Fmt, iso: str) -> str:
    return f.month(pd.Timestamp(iso))


def _welcome(f: Fmt) -> str:
    return f.t("Bonjour ! Je réponds à partir des données du tableau de bord. Par exemple :\n\n"
               "- « Fais l'analyse des 3 derniers mois » (constats et plan d'action)\n"
               "- « Où en est le budget 2026 ? »\n- « Montre la prévision »\n"
               "- « Quels fichiers sont en quarantaine ? »\n- « Quelles alertes sont ouvertes ? »\n"
               "- « Répartition par zone » · « Scope 2 méthode marché et Scope 3 »\n"
               "- « Comment ajouter 3 mois de données ? »",
               "Hello! I answer from the dashboard's data. For example:\n\n"
               "- \"Run the analysis of the last 3 months\" (findings and action plan)\n"
               "- \"How is the 2026 budget?\"\n- \"Show the forecast\"\n"
               "- \"Which files are quarantined?\"\n- \"Which alerts are open?\"\n"
               "- \"Breakdown by zone\" · \"Market-based Scope 2 and Scope 3\"\n"
               "- \"How do I add 3 months of data?\"")


def _num0(f: Fmt, v: float | None) -> str:
    return "—" if not v else f.num(v)


def _list(items: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(items, 1))


def _analyse(f: Fmt, r: dict[str, Any]) -> str:
    hs = r["hotspots"]
    head = f.t(f"**Analyse carbone** · données au {_d(f, r['as_of'])} · période étudiée : {_d(f, hs['start'])} → "
               f"{_d(f, hs['end'])}",
               f"**Carbon analysis** · data to {_d(f, r['as_of'])} · period studied: {_d(f, hs['start'])} → "
               f"{_d(f, hs['end'])}")
    return (f"{head}\n\n**{f.t('Constats', 'Findings')}**\n\n{_list(r['findings'])}\n\n"
            f"**{f.t('Plan d’action', 'Action plan')}**\n\n{_list(r['action_plan'])}")


def _explain(f: Fmt, r: dict[str, Any]) -> str:
    hits = r["sections"]
    if not hits:
        return f.t("Je n'ai pas trouvé ce sujet dans le guide. Sujets disponibles : ",
                   "I could not find that in the guide. Available topics: ") + ", ".join(r["all_topics"]) + "."
    top = hits[0]
    out = f"**{top['title']}**\n\n{top['text']}"
    if len(hits) > 1:
        out += "\n\n_" + f.t("Voir aussi : ", "See also: ") + ", ".join(h["title"] for h in hits[1:]) + "_"
    return out


def _overview(f: Fmt, r: dict[str, Any]) -> str:
    l12 = r["last_12_months"]
    head = f.t(f"**Situation au {_d(f, r['period']['last'])}** · 12 derniers mois : {f.num(l12['total_t'])} tCO₂e "
               f"({f.pct(l12['change_pct'], 1, True)} sur un an)",
               f"**Status at {_d(f, r['period']['last'])}** · last 12 months: {f.num(l12['total_t'])} tCO₂e "
               f"({f.pct(l12['change_pct'], 1, True)} year on year)")
    extra = []
    t = r.get("target") or {}
    if t.get("annual_cap_t"):
        extra.append(f.t(f"Plafond {t['year']} : {f.num(t['annual_cap_t'])} t · {t.get('describe', '')}",
                         f"{t['year']} cap: {f.num(t['annual_cap_t'])} t · {t.get('describe', '')}"))
    s2 = r.get("scope2_last_12_months") or {}
    if s2:
        extra.append(f.t(f"Scope 2 (12 mois) : localisation {f.num(s2['location_t'])} t · marché {f.num(s2['market_t'])} t",
                         f"Scope 2 (12 months): location-based {f.num(s2['location_t'])} t · market-based "
                         f"{f.num(s2['market_t'])} t"))
    al = r.get("open_alerts") or {}
    if al.get("critical") or al.get("warning"):
        extra.append(f.t(f"Alertes ouvertes : {al['critical']} critique(s), {al['warning']} avertissement(s) (page Alertes)",
                         f"Open alerts: {al['critical']} critical, {al['warning']} warning(s) (Alerts page)"))
    more = ("\n\n" + "\n".join(f"- {x}" for x in extra)) if extra else ""
    return (f"{head}{more}\n\n{_list(r['conclusions'])}\n\n**{f.t('Recommandations', 'Recommendations')}**\n\n"
            f"{_list(r['recommendations'])}")


def _rows(table: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not table:
        return []
    return [dict(zip(table["columns"], row)) for row in table["rows"]]


def _inventory(f: Fmt, r: dict[str, Any]) -> str:
    s2, s3, per = r["scope2"], r["scope3"], r["period"]
    lang = "fr" if f.lang == "fr" else "en"
    head = f.t(f"**Inventaire GES** · {_d(f, per['start'])} → {_d(f, per['end'])}",
               f"**GHG inventory** · {_d(f, per['start'])} → {_d(f, per['end'])}")
    lines = [
        f.t(f"- Scope 1 : {f.num(r['scope1_t'])} tCO₂e", f"- Scope 1: {f.num(r['scope1_t'])} tCO₂e"),
        f.t(f"- Scope 2 localisation : {f.num(s2['location_t'])} t · marché : {f.num(s2['market_t'])} t "
            f"({f.pct(s2['covered_pct'], 0)} des kWh sous contrat)",
            f"- Scope 2 location-based: {f.num(s2['location_t'])} t · market-based: {f.num(s2['market_t'])} t "
            f"({f.pct(s2['covered_pct'], 0)} of kWh under contract)"),
        f.t(f"- Scope 3 (criblage) : {f.num(s3['total_t'])} t, dont {f.num(s3['estimated_t'])} t estimées",
            f"- Scope 3 (screening): {f.num(s3['total_t'])} t, of which {f.num(s3['estimated_t'])} t estimated"),
    ]
    cats = sorted(s3["by_category"], key=lambda c: -(c.get("t") or 0))[:4]
    if cats:
        lines.append(f.t("- Principales catégories : ", "- Main categories: ")
                     + ", ".join(f"{c['category']}. {c.get('category_' + lang)} {f.num(c['t'])} t" for c in cats))
    if s3["excluded_categories"]:
        lines.append(f.t("- Catégories exclues (avec motif, page Inventaire GES) : ",
                         "- Excluded categories (with reason, GHG inventory page): ")
                     + ", ".join(str(c) for c in s3["excluded_categories"]))
    return (f"{head}\n\n" + "\n".join(lines) + f"\n\n_{s2['note']} "
            + f.t("Le Scope 3 est une estimation à partir d'hypothèses sourcées.",
                  "Scope 3 is an estimate from sourced assumptions.") + "_")


def _breakdown(f: Fmt, r: dict[str, Any]) -> str:
    lang = "fr" if f.lang == "fr" else "en"
    per = r["period"]
    zones = _rows(r["zones"])
    head = f.t(f"**Répartition par zone** · {_d(f, per['start'])} → {_d(f, per['end'])} · total site "
               f"{f.num(r['site_total_t'])} tCO₂e",
               f"**Breakdown by zone** · {_d(f, per['start'])} → {_d(f, per['end'])} · site total "
               f"{f.num(r['site_total_t'])} tCO₂e")
    body = f.t("| Zone | Responsable | tCO₂e | Part |\n|---|---|---:|---:|", "| Zone | Owner | tCO₂e | Share |\n|---|---|---:|---:|")
    def zname(z: dict[str, Any]) -> str:
        name = str(z.get("zone_" + lang) or z["zone"])
        return name if name.startswith(z["zone"]) else f"{z['zone']} · {name}"

    body += "".join(f"\n| {zname(z)} | {z.get('owner') or '—'} | {f.num(z['t'])} | {f.pct(z['share_pct'], 0)} |"
                    for z in zones)
    items = _rows(r["items"])[:5]
    if items:
        body += "\n\n" + f.t("**Principaux postes** : ", "**Largest items**: ") + "; ".join(
            f"{i.get('item_' + lang)} ({i['zone']}) {f.num(i['t'])} t" for i in items)
    return (f"{head}\n\n{body}\n\n_" + f.t("Clés de répartition estimées (config.yaml → breakdown), à remplacer par "
                                             "des sous-compteurs.", "Estimated allocation keys (config.yaml → "
                                             "breakdown), to replace with sub-meters.") + "_")


_SEV = {"critical": "🔴", "warning": "🟠", "info": "🔵"}


def _alerts(f: Fmt, r: dict[str, Any]) -> str:
    c = r["counts"]
    when = (r.get("checked_at") or "—").replace("T", " ")[:16]
    if not r["alerts"]:
        return f.t(f"**Aucune alerte ouverte** (vérifié le {when}).", f"**No open alert** (checked {when}).")
    head = f.t(f"**{c['critical']} critique(s), {c['warning']} avertissement(s), {c['info']} info** · vérifié le {when}",
               f"**{c['critical']} critical, {c['warning']} warning(s), {c['info']} info** · checked {when}")
    lines = [f"- {_SEV.get(a['severity'], '•')} **{a['title']}**" + (f" — {a['detail']}" if a.get("detail") else "")
             for a in r["alerts"]]
    return f"{head}\n\n" + "\n".join(lines) + "\n\n_" + f.t("Détails et historique : page Alertes.",
                                                              "Details and history: Alerts page.") + "_"


_STATUS = {"green": ("🟢 dans la cible", "🟢 on track"), "amber": ("🟠 à surveiller", "🟠 at risk"),
           "red": ("🔴 dépassement", "🔴 over"), "nodata": ("— sans données", "— no data")}


def _budget(f: Fmt, r: dict[str, Any]) -> str:
    st = {k: v[0 if f.lang == "fr" else 1] for k, v in _STATUS.items()}
    lines = []
    if "full_year_estimate" in r:
        fy = r["full_year_estimate"]
        gap = fy["gap_t"]
        verdict = (f.t(f"dépassement de {f.num(gap)} t", f"{f.num(gap)} t over") if gap > 0
                   else f.t(f"marge de {f.num(-gap)} t", f"{f.num(-gap)} t of headroom"))
        lines.append(f.t(
            f"**Budget {r['year']}** : plafond {f.num(r['cap_t'])} t. Réalisé à date : {f.num(r['actual_ytd_t'])} t. "
            f"Estimation de l'année complète : **{f.num(fy['total_t'])} t** → {verdict} "
            f"(fourchette 90 % : {f.num(fy['total_lo_t'])} à {f.num(fy['total_hi_t'])} t).",
            f"**{r['year']} budget**: cap {f.num(r['cap_t'])} t. Emitted so far: {f.num(r['actual_ytd_t'])} t. "
            f"Full-year estimate: **{f.num(fy['total_t'])} t** → {verdict} "
            f"(90% range: {f.num(fy['total_lo_t'])} to {f.num(fy['total_hi_t'])} t)."))
        head = f.t("| Mois | Réel | Prévu | Estimé | Plafond | Statut |\n|---|---:|---:|---:|---:|---|",
                   "| Month | Actual | Forecast | Estimated | Ceiling | Status |\n|---|---:|---:|---:|---:|---|")
        rows = [f"| {_m(f, m['month'])} | {_num0(f, m['actual'])} | {_num0(f, m['forecast'])} | "
                f"{_num0(f, m['estimate'])} | {f.num(m['ceiling'])} | {_month_status(f, m, st)} |" for m in r["months"]]
        lines.append(head + "\n" + "\n".join(rows))
        lines.append(f.t("_Estimé : au-delà de l'horizon de prévision, même période l'an dernier × tendance récente._",
                         "_Estimated: beyond the forecast horizon, same period last year × recent trend._"))
    else:
        gap = r["gap_t"]
        lines.append(f.t(f"**Budget {r['year']}** (réel uniquement) : {f.num(r['total_t'])} t pour un plafond de "
                         f"{f.num(r['cap_t'])} t ({'dépassement' if gap > 0 else 'marge'} de {f.num(abs(gap))} t).",
                         f"**{r['year']} budget** (actuals only): {f.num(r['total_t'])} t against a "
                         f"{f.num(r['cap_t'])} t cap ({f.num(abs(gap))} t {'over' if gap > 0 else 'under'})."))
        head = f.t("| Mois | Réel | Plafond | Statut |\n|---|---:|---:|---|", "| Month | Actual | Ceiling | Status |\n|---|---:|---:|---|")
        rows = [f"| {_m(f, m['month'])} | {_num0(f, m['actual'])} | {f.num(m['ceiling'])} | {st.get(m['status'], m['status'])} |"
                for m in r["months"]]
        lines.append(head + "\n" + "\n".join(rows))
    return "\n\n".join(lines)


def _month_status(f: Fmt, m: dict[str, Any], st: dict[str, str]) -> str:
    if m.get("basis") == "estimate":
        return f.t("🔴 au-dessus (estimé)", "🔴 above (estimated)") if m["over_ceiling"] else \
            f.t("🟢 en dessous (estimé)", "🟢 below (estimated)")
    return st.get(m.get("dashboard_status", m.get("status")), "—")


def _forecast(f: Fmt, r: dict[str, Any]) -> str:
    run = r["run"]
    text = f.t(
        f"**Prévision {run['model_name']}** du {_d(f, r['start'])} au {_d(f, r['end'])} : **{f.num(r['total_t'])} t** "
        f"(fourchette 90 % : {f.num(r['lo90_total_t'])} à {f.num(r['hi90_total_t'])} t), soit "
        f"{f.num(r['daily_mean_t'], 2)} t/jour contre {f.num(r['recent_daily_mean_t'], 2)} sur la période récente. "
        f"Pic prévu le {_d(f, r['peak']['date'])} ({f.num(r['peak']['yhat_t'], 1)} t). "
        f"Entraînée sur {f.num(run['history_days'])} jours jusqu'au {_d(f, run['trained_until'])}.",
        f"**{run['model_name']} forecast** from {_d(f, r['start'])} to {_d(f, r['end'])}: **{f.num(r['total_t'])} t** "
        f"(90% range: {f.num(r['lo90_total_t'])} to {f.num(r['hi90_total_t'])} t), i.e. "
        f"{f.num(r['daily_mean_t'], 2)} t/day against {f.num(r['recent_daily_mean_t'], 2)} recently. "
        f"Peak forecast on {_d(f, r['peak']['date'])} ({f.num(r['peak']['yhat_t'], 1)} t). "
        f"Trained on {f.num(run['history_days'])} days up to {_d(f, run['trained_until'])}.")
    if run.get("stale"):
        text += f.t(" ⚠️ Elle n'intègre pas les derniers dépôts : relancez-la.", " ⚠️ It predates the latest uploads: rerun it.")
    tb = r["table"]
    if r["granularity"] == "month":
        c = tb["columns"]
        head = f.t("| Mois | Prévu | Bas 90 % | Haut 90 % |\n|---|---:|---:|---:|", "| Month | Forecast | Low 90% | High 90% |\n|---|---:|---:|---:|")
        rows = [f"| {_m(f, row[0] + '-01')} | {f.num(row[c.index('yhat_t')])} | {f.num(row[c.index('lo90_t')])} | "
                f"{f.num(row[c.index('hi90_t')])} |" for row in tb["rows"]]
        text += "\n\n" + head + "\n" + "\n".join(rows)
    return text


def _quality(f: Fmt, r: dict[str, Any]) -> str:
    text = f.t(
        f"**Qualité des données** : {f.num(r['files_accepted'])} fichiers acceptés sur {f.num(r['files_scanned'])} "
        f"({f.pct(r['acceptance_pct'], 1)}), **{r['files_quarantined']} en quarantaine**. "
        f"{f.num(r['imputed_days'])} jours imputés sur {f.num(r['days'])} ({f.pct(r['imputed_pct'], 1)}).",
        f"**Data quality**: {f.num(r['files_accepted'])} of {f.num(r['files_scanned'])} files accepted "
        f"({f.pct(r['acceptance_pct'], 1)}), **{r['files_quarantined']} quarantined**. "
        f"{f.num(r['imputed_days'])} imputed days out of {f.num(r['days'])} ({f.pct(r['imputed_pct'], 1)}).")
    last = r["department_last_day"]
    if last:
        text += "\n\n" + f.t("Dernier jour reçu : ", "Last day received: ") + ", ".join(
            f"{k} {_d(f, v)}" for k, v in last.items())
    if r["departments_behind_days"]:
        text += "\n\n" + f.t("En retard : ", "Behind: ") + ", ".join(
            f"{k} ({v} j)" if f.lang == "fr" else f"{k} ({v} d)" for k, v in r["departments_behind_days"].items())
    if r["quarantine"]:
        items = [f"`{q['file']}` — {q['reason']}" + (f" → {q['fix']}" if q.get("fix") else "") for q in r["quarantine"][:6]]
        text += "\n\n" + f.t("**Fichiers en quarantaine**", "**Quarantined files**") + "\n\n" + "\n".join(f"- {i}" for i in items)
        if r["quarantine_total"] > 6:
            text += "\n- " + f.t(f"… et {r['quarantine_total'] - 6} autres (page Qualité des données)",
                                 f"… and {r['quarantine_total'] - 6} more (Data quality page)")
    return text


def _models(f: Fmt, r: dict[str, Any]) -> str:
    rows = r["backtest"] or []
    rule_fr = (r.get("router") or {}).get("fr") or r["router_rule"]
    text = f.t(f"**Meilleur modèle en backtest : {r['best_model']}**. Règle du routeur : {rule_fr}. "
               f"Prévision active : {r['active_forecast']}.",
               f"**Best model in backtest: {r['best_model']}**. Router rule: {r['router_rule']}. "
               f"Active forecast: {r['active_forecast']}.")
    if rows:
        head = f.t("| Modèle | MAE (t/j) | MASE | Couverture 90 % |\n|---|---:|---:|---:|",
                   "| Model | MAE (t/day) | MASE | 90% coverage |\n|---|---:|---:|---:|")
        body = [f"| {m['name']} | {f.num(m['mae'], 2)} | {f.num(m['mase'], 2)} | {f.pct((m['coverage'] or 0) * 100, 0)} |"
                for m in rows]
        text += "\n\n" + head + "\n" + "\n".join(body)
        text += "\n\n" + f.t("_MASE < 1 : meilleur que la référence naïve saisonnière._",
                             "_MASE < 1: better than the seasonal naive baseline._")
    return text


def _methodology(f: Fmt, r: dict[str, Any]) -> str:
    names = {"electricity_kgco2_per_kwh": (f.t("Électricité réseau", "Grid electricity"), "kgCO₂e/kWh"),
             "fuel_oil_kgco2_per_l": (f.t("Fioul", "Fuel oil"), "kgCO₂e/L"),
             "diesel_kgco2_per_l": (f.t("Gazole", "Diesel"), "kgCO₂e/L"),
             "waste_kgco2_per_kg": (f.t("Déchets", "Waste"), "kgCO₂e/kg"),
             "water_kgco2_per_m3": (f.t("Eau", "Water"), "kgCO₂e/m³")}
    items = [f"- {names.get(k, (k, ''))[0]} : {f.num(v, 3)} {names.get(k, ('', ''))[1]}" for k, v in r["emission_factors"].items()]
    text = f.t(f"**Facteurs d'émission** (revus le {r['factors_review_date']})",
               f"**Emission factors** (reviewed {r['factors_review_date']})") + "\n\n" + "\n".join(items)
    text += "\n\n" + f.t("Émissions = activité × facteur. Scope 1 = fioul + gazole ; Scope 2 = électricité du réseau "
                         "(méthode localisation) ; autres = déchets + eau. Référentiels : GHG Protocol, CSRD / ESRS E1.",
                         "Emissions = activity × factor. Scope 1 = fuel oil + diesel; Scope 2 = grid electricity "
                         "(location-based); other = waste + water. Frameworks: GHG Protocol, CSRD / ESRS E1.")
    return text


def _pipeline(f: Fmt, r: dict[str, Any]) -> str:
    runs = r["pipeline_runs"]
    if not runs:
        return f.t("Aucune exécution du pipeline enregistrée.", "No pipeline run recorded yet.")
    items = []
    for x in runs[:5]:
        after = (x.get("after") or {}).get("last")
        items.append(f.t(f"{x['started_at'].replace('T', ' ')} · {x.get('trigger') or '—'} · "
                         f"{x.get('new_days') or 0} nouveaux jours · données jusqu'au {_d(f, after)} · "
                         f"{f.num(x.get('seconds') or 0, 1)} s",
                         f"{x['started_at'].replace('T', ' ')} · {x.get('trigger') or '—'} · "
                         f"{x.get('new_days') or 0} new days · data to {_d(f, after)} · {f.num(x.get('seconds') or 0, 1)} s"))
    return (f.t("**Dernières exécutions du pipeline**", "**Latest pipeline runs**") + "\n\n"
            + "\n".join(f"- {i}" for i in items)
            + "\n\n" + f.t(f"{r['uploads_total']} fichiers déposés au total.", f"{r['uploads_total']} files uploaded in total."))


def _emissions(f: Fmt, r: dict[str, Any]) -> str:
    cols, rows = r["table"]["columns"], r["table"]["rows"]
    if not rows:
        return f.t("Aucune donnée sur cette période.", "No data for this period.")
    ix = {c: i for i, c in enumerate(cols)}
    shown = [c for c in ("total_t", "scope1_t", "scope2_t", "other_t") if c in ix]
    names = {"total_t": ("Total (t)", "Total (t)"), "scope1_t": ("Scope 1", "Scope 1"), "scope2_t": ("Scope 2", "Scope 2"),
             "other_t": ("Autres", "Other")}
    head = ("| " + f.t("Période", "Period") + " | " + " | ".join(f.t(*names[c]) for c in shown) + " | "
            + f.t("Évolution", "Change") + " |\n|---|" + "---:|" * (len(shown) + 1))
    lines, prev = [], None
    for row in rows[-24:]:
        tot, full = row[ix["total_t"]], row[ix["complete"]] if "complete" in ix else True
        change = f.pct((tot / prev - 1) * 100, 1, True) if prev and full else "—"
        label = str(row[ix["period"]]) + ("" if full else f.t(" (partiel)", " (partial)"))
        lines.append(f"| {label} | " + " | ".join(f.num(row[ix[c]]) for c in shown) + f" | {change} |")
        prev = tot if full else None
    gran = {"day": ("jour", "day"), "week": ("semaine", "week"), "month": ("mois", "month"), "year": ("année", "year")}
    g = gran.get(r["granularity"], (r["granularity"], r["granularity"]))
    title = f.t(f"**Émissions par {g[0]}** ({_d(f, r['start'])} → {_d(f, r['end'])})",
                f"**Emissions by {g[1]}** ({_d(f, r['start'])} → {_d(f, r['end'])})")
    notes = [f"_{n}_" for n in r.get("notes", [])]
    if any(not row[ix["complete"]] for row in rows if "complete" in ix):
        notes.append(f.t("_Partiel : période pas encore complète dans les données, sans évolution calculée._",
                         "_Partial: period not complete in the data yet, so no change is computed._"))
    return "\n\n".join([title, head + "\n" + "\n".join(lines), *notes])


def _scenario(f: Fmt, r: dict[str, Any]) -> str:
    p, h, y = r["parameters"], r["forecast_horizon"], r["year_effect"]
    levers = [f.t(f"solaire {f.num(p['solar_share_pct'])} %", f"solar {f.num(p['solar_share_pct'])}%")
              if p["solar_share_pct"] else "",
              f.t(f"efficacité {f.num(p['efficiency_gain_pct'])} %", f"efficiency {f.num(p['efficiency_gain_pct'])}%")
              if p["efficiency_gain_pct"] else "",
              f"production {f.pct(p['production_load_pct'], 0, True)}" if p["production_load_pct"] else ""]
    shown = ", ".join(x for x in levers if x) or f.t("aucun levier", "no lever")
    delta = h["net_delta_tco2e"]
    signed = ("+" if delta > 0 else "") + f.num(delta, 1)
    lines = [f.t(f"**Scénario** : {shown}", f"**Scenario**: {shown}"), "",
             f.t(f"- **{h['horizon_days']} prochains jours** ({_d(f, h['start'])} → {_d(f, h['end'])}) : "
                 f"{f.num(h['total_baseline_tco2e'])} t → **{f.num(h['total_scenario_tco2e'])} t** "
                 f"({signed} t, {f.pct(h['pct_change'], 1, True)})",
                 f"- **Next {h['horizon_days']} days** ({_d(f, h['start'])} → {_d(f, h['end'])}): "
                 f"{f.num(h['total_baseline_tco2e'])} t → **{f.num(h['total_scenario_tco2e'])} t** "
                 f"({signed} t, {f.pct(h['pct_change'], 1, True)})")]
    if y:
        saved = y["saved_rest_of_year_t"]
        effect = (f.t(f"soit {f.num(saved, 1)} t évitées", f"i.e. {f.num(saved, 1)} t avoided") if saved >= 0 else
                  f.t(f"soit {f.num(-saved, 1)} t de plus", f"i.e. {f.num(-saved, 1)} t more"))
        lines.append(f.t(f"- **Année {y['year']}** : {f.num(y['projected_without_t'])} t prévues sans le scénario, "
                         f"**{f.num(y['projected_with_t'])} t** avec (plafond {f.num(y['cap_t'])} t), {effect} "
                         "sur le reste de l'année.",
                         f"- **Year {y['year']}**: {f.num(y['projected_without_t'])} t expected without the scenario, "
                         f"**{f.num(y['projected_with_t'])} t** with it (cap {f.num(y['cap_t'])} t), {effect} "
                         "over the rest of the year."))
        gap = y["gap_after_t"]
        if gap <= 0:
            lines.append(f.t(f"- ✅ L'année repasse sous le plafond (marge {f.num(-gap)} t).",
                             f"- ✅ The year ends under the cap ({f.num(-gap)} t of headroom)."))
        else:
            lines.append(f.t(f"- ⚠️ Il reste {f.num(gap)} t au-dessus du plafond : ajoutez un levier ou augmentez-le.",
                             f"- ⚠️ Still {f.num(gap)} t over the cap: add or raise a lever."))
    lines += ["", f.t("_Règles : le solaire et l'efficacité réduisent l'électricité du réseau (Scope 2) ; l'efficacité "
                      "réduit aussi les carburants de moitié ; la production pèse avec une élasticité de 0,75. "
                      "Autres valeurs : précisez-les (« 20 % de solaire et 5 % d'efficacité ») ou utilisez la page "
                      "Scénarios._",
                      "_Rules: solar and efficiency cut grid electricity (Scope 2); efficiency also cuts fuels by half; "
                      "production weighs with a 0.75 elasticity. Other values: name them (\"20% solar and 5% "
                      "efficiency\") or use the Scenarios page._")]
    return "\n".join(lines)


def _generic(f: Fmt, r: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(r, ensure_ascii=False, indent=1)[:3000] + "\n```"


_ANSWERS = {"analyse": _analyse, "explain_dashboard": _explain, "get_overview": _overview, "get_budget": _budget,
            "get_forecast": _forecast, "get_data_quality": _quality, "get_model_performance": _models,
            "get_methodology": _methodology, "get_pipeline_activity": _pipeline, "query_emissions": _emissions,
            "run_scenario": _scenario, "get_inventory": _inventory, "get_breakdown": _breakdown,
            "get_alerts": _alerts}
