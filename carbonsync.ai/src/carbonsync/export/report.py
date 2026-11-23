"""Excel audit workbook (CSRD / ESRS E1): 10 sheets, native Excel charts, conclusions included.

Sheets: Summary · Annual · Monthly · Activity · Forecast · Budget · Models · Data quality ·
Methodology · Log. Content comes from ``insights.build_insights`` so the workbook, the PDF brief
and the dashboard always tell the same story.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.series import SeriesLabel
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from carbonsync.export.insights import DEPT_NAMES, MODEL_NAMES, Fmt, build_insights
from carbonsync.obs import get_logger

log = get_logger(__name__)

NAVY = "1F3A5F"
COLORS = {"s1": "EB6834", "s2": "3987E5", "other": "1BAF7A", "fc": "3987E5", "band": "9DB9E8", "actual": "898781"}
STATUS_FILL = {"green": "C6EFCE", "amber": "FFEB9C", "red": "FFC7CE", "nodata": "F2F2F2"}

TITLE = Font(name="Calibri", size=16, bold=True, color=NAVY)
H2 = Font(name="Calibri", size=12, bold=True, color=NAVY)
SUB = Font(name="Calibri", size=10, italic=True, color="595959")
HEAD = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", start_color=NAVY, end_color=NAVY)
ALT_FILL = PatternFill("solid", start_color="F3F6FA", end_color="F3F6FA")
LINE = Side(style="thin", color="D9DEE5")
BOX = Border(left=LINE, right=LINE, top=LINE, bottom=LINE)
WRAP = Alignment(wrap_text=True, vertical="top")


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or get_project_root() / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _title(ws: Worksheet, title: str, subtitle: str = "") -> int:
    ws["A1"] = title
    ws["A1"].font = TITLE
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = SUB
    ws.sheet_view.showGridLines = False
    return 4


def _table(ws: Worksheet, row: int, headers: list[str], rows: list[list[Any]],
           formats: dict[int, str] | None = None, col: int = 1) -> int:
    """Write a styled table; returns the next free row (one blank line after)."""
    formats = formats or {}
    for j, h in enumerate(headers):
        c = ws.cell(row=row, column=col + j, value=h)
        c.font, c.fill, c.border = HEAD, HEAD_FILL, BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, values in enumerate(rows, start=1):
        for j, v in enumerate(values):
            if isinstance(v, float) and pd.isna(v):
                v = None
            if isinstance(v, pd.Timestamp):
                v = v.to_pydatetime()
            c = ws.cell(row=row + i, column=col + j, value=v)
            c.border = BOX
            if j in formats:
                c.number_format = formats[j]
            if i % 2 == 0:
                c.fill = ALT_FILL
    return row + len(rows) + 2


def _h2(ws: Worksheet, row: int, text: str) -> int:
    ws.cell(row=row, column=1, value=text).font = H2
    return row + 1


def _widths(ws: Worksheet, widths: dict[str, float] | None = None, cap: int = 60) -> None:
    for column in ws.columns:
        letter = get_column_letter(column[0].column)
        longest = max((len(str(c.value)) for c in column[3:] if c.value is not None), default=8)
        ws.column_dimensions[letter].width = min(max(longest + 2, 11), cap)
    for letter, w in (widths or {}).items():
        ws.column_dimensions[letter].width = w


def _color_series(chart, colors: list[str]) -> None:
    for s, color in zip(chart.series, colors):
        s.graphicalProperties.solidFill = color
        s.graphicalProperties.line.solidFill = color


# ---------------------------------------------------------------------------
def generate_excel_report(output_path: Path | None = None, config_path: Path | None = None,
                          lang: str | None = None, run_id: str | None = None,
                          insights: dict[str, Any] | None = None) -> Path:
    """Build the audit workbook. ``lang`` defaults to ``app.language`` in config.yaml."""
    root = get_project_root()
    cfg = load_config(config_path)
    lang = lang or cfg.get("app", {}).get("language", "fr")
    ins = insights or build_insights(lang, run_id=run_id, config_path=config_path)
    f = Fmt(lang)
    t = f.t

    if output_path is None:
        out_dir = root / "data" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"CarbonSync_Report_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb = Workbook()
    wb.remove(wb.active)
    per, tot, fc, bud, q = ins["period"], ins["totals"], ins["forecast"], ins["budget"], ins["quality"]
    stamp = ins["generated_at"].strftime("%Y-%m-%d %H:%M")
    subtitle = t(f"{ins['site']} · données du {f.date(per['first'])} au {f.date(per['last'])} · généré le {stamp}",
                 f"{ins['site']} · data {f.date(per['first'])} to {f.date(per['last'])} · generated {stamp}")
    if ins["synthetic"]:
        subtitle += t(" · DONNÉES SYNTHÉTIQUES", " · SYNTHETIC DATA")

    # 1 ── Summary ----------------------------------------------------------------
    ws = wb.create_sheet(t("Synthèse", "Summary"))
    r = _title(ws, t("CarbonSync AI – Rapport d'audit carbone", "CarbonSync AI – Carbon audit report"), subtitle)
    r = _h2(ws, r, t("Chiffres clés", "Key figures"))
    sh = tot["shares"]
    kpis = [
        [t("Période couverte", "Period covered"), f"{per['first']:%Y-%m-%d} → {per['last']:%Y-%m-%d}", "",
         t(f"{per['days']} jours", f"{per['days']} days")],
        [t("Émissions 12 derniers mois", "Emissions, last 12 months"), round(tot["last12"], 1), "tCO₂e",
         t("Variation sur un an", "Year-on-year change") + f" : {f.pct(tot['yoy_pct'], 1, True)}"],
        [t("Moyenne journalière (12 mois)", "Daily average (12 months)"), round(tot["daily_mean_12"], 2), "tCO₂e/j", ""],
        ["Scope 1 (12 m)", round(tot["s1_12"], 1), "tCO₂e", f.pct(sh["co2e_scope1_t"], 1)],
        [t("Scope 2 méthode localisation (12 m)", "Scope 2 location-based (12 m)"), round(tot["s2_12"], 1), "tCO₂e",
         f.pct(sh["co2e_scope2_t"], 1)],
        [t("Scope 2 méthode marché (12 m)", "Scope 2 market-based (12 m)"), round(ins["scope2"]["market_t"], 1), "tCO₂e",
         t(f"{f.pct(ins['scope2']['covered_pct'], 0)} couverts par contrat", f"{f.pct(ins['scope2']['covered_pct'], 0)} covered by contracts")],
        [t("Autres (12 m)", "Other (12 m)"), round(tot["other_12"], 1), "tCO₂e", f.pct(sh["co2e_other_t"], 1)],
        [t("Intensité carbone (12 m)", "Carbon intensity (12 m)"), round(tot["intensity_12"], 2), "kgCO₂e/unit", ""],
        [t("Émissions cumulées (tout l'historique)", "Cumulative emissions (full history)"), round(tot["all"], 1), "tCO₂e", ""],
        [t("Scope 3, présélection (12 m)", "Scope 3, screening (12 m)"), round(ins["scope3"]["total_t"], 1), "tCO₂e",
         t("hors budget ; estimations à confirmer", "outside the budget; estimates to confirm")],
    ]
    if fc.get("available"):
        kpis += [
            [t(f"Prévision {fc['days']} jours ({fc['model_name']})", f"{fc['days']}-day forecast ({fc['model_name']})"),
             round(fc["total"], 1), "tCO₂e", t("IC 90 %", "90% CI") + f" {f.num(fc['lo_total'])}–{f.num(fc['hi_total'])}"],
            [t("Pic prévu", "Forecast peak"), round(fc["peak"], 2), "tCO₂e/j", f"{fc['peak_date']:%Y-%m-%d}"],
        ]
    kpis += [
        [t(f"Budget {bud['year']}", f"{bud['year']} budget"), round(bud["cap"], 0), "tCO₂e",
         t(f"{f.num(bud['actual_ytd'])} t déjà émises", f"{f.num(bud['actual_ytd'])} t emitted")],
        [t("Objectif", "Target"), ins["targets"].get("target_emissions_t"), "tCO₂e",
         t(f"−{ins['targets']['reduction_pct']:g} % en {ins['targets']['target_year']} vs {ins['targets']['base_year']}",
           f"−{ins['targets']['reduction_pct']:g}% by {ins['targets']['target_year']} vs {ins['targets']['base_year']}")],
        [t("Alertes ouvertes", "Open alerts"), len((ins.get("alerts") or {}).get("alerts", [])), "",
         t("voir l'onglet Alertes", "see the Alerts sheet")],
        [t("Fichiers acceptés", "Files accepted"), f"{q['accepted']}/{q['scanned']}", "", f.pct(q["acceptance_pct"], 1)],
        [t("Jours imputés", "Imputed days"), per["imputed"], t("jours", "days"), f.pct(per["imputed_pct"], 1)],
    ]
    r = _table(ws, r, [t("Indicateur", "Indicator"), t("Valeur", "Value"), t("Unité", "Unit"), t("Note", "Note")], kpis,
               {1: "#,##0.0#"})
    r = _h2(ws, r, t("Conclusions", "Conclusions"))
    for k, text in enumerate(ins["conclusions"], 1):
        ws.cell(row=r, column=1, value=f"{k}.").alignment = WRAP
        c = ws.cell(row=r, column=2, value=text)
        c.alignment = WRAP
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.row_dimensions[r].height = 15 * (1 + len(text) // 95)
        r += 1
    r = _h2(ws, r + 1, t("Recommandations", "Recommendations"))
    for text in ins["recommendations"]:
        ws.cell(row=r, column=1, value="→")
        c = ws.cell(row=r, column=2, value=text)
        c.alignment = WRAP
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.row_dimensions[r].height = 15 * (1 + len(text) // 95)
        r += 1
    _widths(ws, {"A": 42, "B": 40, "C": 14, "D": 44})

    # 2 ── Annual -------------------------------------------------------------------
    ws = wb.create_sheet(t("Annuel", "Annual"))
    r = _title(ws, t("Émissions annuelles par scope", "Annual emissions by scope"), subtitle)
    a = ins["annual"]
    rows = [[int(x.year), x.s1, x.s2, x.other, x.total, x.yoy_pct / 100 if pd.notna(x.yoy_pct) else None,
             int(x.days), int(x.imputed), x.kwh, x.units, x.intensity_kg_unit,
             t("complète", "full") if x.complete else t("partielle", "partial")] for x in a.itertuples()]
    start = r
    r = _table(ws, r, [t("Année", "Year"), "Scope 1 (t)", "Scope 2 (t)", t("Autres (t)", "Other (t)"), "Total (t)",
                       t("Var. an.", "YoY"), t("Jours", "Days"), t("Imputés", "Imputed"), t("Électricité (kWh)", "Electricity (kWh)"),
                       t("Unités produites", "Units produced"), t("Intensité (kg/unité)", "Intensity (kg/unit)"), t("Année", "Year")],
               rows, {1: "#,##0.0", 2: "#,##0.0", 3: "#,##0.0", 4: "#,##0.0", 5: "+0.0%;-0.0%", 8: "#,##0", 9: "#,##0",
                      10: "0.00"})
    ch = BarChart()
    ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
    ch.title, ch.y_axis.title = t("Émissions annuelles (tCO₂e)", "Annual emissions (tCO₂e)"), "tCO₂e"
    ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=start, max_row=start + len(rows)), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=start + 1, max_row=start + len(rows)))
    _color_series(ch, [COLORS["s1"], COLORS["s2"], COLORS["other"]])
    ch.height, ch.width = 8, 22
    ws.add_chart(ch, f"A{r + 1}")
    _widths(ws)

    # 3 ── Monthly ------------------------------------------------------------------
    ws = wb.create_sheet(t("Mensuel", "Monthly"))
    r = _title(ws, t("Émissions mensuelles par scope", "Monthly emissions by scope"), subtitle)
    m = ins["monthly"]
    rows = [[x.month.strftime("%Y-%m"), x.s1, x.s2, x.other, x.total,
             x.yoy_pct / 100 if pd.notna(x.yoy_pct) else None, int(x.days), int(x.imputed),
             t("oui", "yes") if x.complete else t("non", "no")] for x in m.itertuples()]
    start = r
    r = _table(ws, r, [t("Mois", "Month"), "Scope 1 (t)", "Scope 2 (t)", t("Autres (t)", "Other (t)"), "Total (t)",
                       t("Var. vs N-1", "vs last year"), t("Jours", "Days"), t("Imputés", "Imputed"), t("Complet", "Complete")],
               rows, {1: "#,##0.0", 2: "#,##0.0", 3: "#,##0.0", 4: "#,##0.0", 5: "+0.0%;-0.0%"})
    ws.freeze_panes = ws.cell(row=start + 1, column=2)
    n = min(36, len(rows))
    ch = BarChart()
    ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
    ch.title = t(f"{n} derniers mois (tCO₂e)", f"Last {n} months (tCO₂e)")
    first_row = start + len(rows) - n + 1
    ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=first_row, max_row=start + len(rows)), titles_from_data=False)
    for s, name in zip(ch.series, ["Scope 1", "Scope 2", t("Autres", "Other")]):
        s.tx = SeriesLabel(v=name)
    ch.set_categories(Reference(ws, min_col=1, min_row=first_row, max_row=start + len(rows)))
    _color_series(ch, [COLORS["s1"], COLORS["s2"], COLORS["other"]])
    ch.height, ch.width = 9, 28
    ws.add_chart(ch, "K4")
    _widths(ws)

    # 4 ── Activity -----------------------------------------------------------------
    ws = wb.create_sheet(t("Activité", "Activity"))
    r = _title(ws, t("Données d'activité mensuelles (sources des émissions)", "Monthly activity data (emission sources)"), subtitle)
    rows = [[x.month.strftime("%Y-%m"), x.electricity_kwh, x.fuel_oil_l, x.diesel_l, x.waste_kg, x.water_m3,
             x.units_produced, x.intensity_kg_unit] for x in m.itertuples()]
    start = r
    _table(ws, r, [t("Mois", "Month"), t("Électricité (kWh)", "Electricity (kWh)"), t("Fioul (L)", "Fuel oil (L)"),
                   "Diesel (L)", t("Déchets (kg)", "Waste (kg)"), t("Eau (m³)", "Water (m³)"),
                   t("Unités produites", "Units produced"), t("Intensité (kgCO₂e/unité)", "Intensity (kgCO₂e/unit)")],
           rows, {k: "#,##0" for k in range(1, 7)} | {7: "0.00"})
    ws.freeze_panes = ws.cell(row=start + 1, column=2)
    _widths(ws)

    # 5 ── Forecast -----------------------------------------------------------------
    ws = wb.create_sheet(t("Prévision", "Forecast"))
    r = _title(ws, t("Prévision des émissions (intervalle de confiance 90 %)", "Emissions forecast (90% confidence interval)"), subtitle)
    if fc.get("available"):
        info = [
            [t("Modèle", "Model"), fc["model_name"]], [t("Identifiant", "Run id"), fc["run_id"]],
            [t("Entraîné jusqu'au", "Trained until"), f"{fc['trained_until']:%Y-%m-%d}"],
            [t("Historique utilisé", "History used"), t(f"{fc['history_days']} jours", f"{fc['history_days']} days")],
            [t("Horizon", "Horizon"), f"{fc['start']:%Y-%m-%d} → {fc['end']:%Y-%m-%d} ({fc['days']})"],
            [t("Total prévu", "Forecast total"), f"{f.num(fc['total'], 1)} t ({f.num(fc['lo_total'])}–{f.num(fc['hi_total'])})"],
            [t("Moyenne / jour", "Mean / day"), f"{f.num(fc['mean'], 2)} t ({f.pct(fc['change_pct'], 1, True)} "
                                                 + t("vs période précédente", "vs previous period") + ")"],
            [t("Largeur moyenne IC 90 %", "Mean 90% CI width"), f"{f.num(fc['width'], 2)} t"],
        ]
        r = _table(ws, r, [t("Élément", "Item"), t("Valeur", "Value")], info)
        r = _h2(ws, r, t("Par mois", "By month"))
        fm = fc["monthly"]
        r = _table(ws, r, [t("Mois", "Month"), t("Jours", "Days"), t("Prévu (t)", "Forecast (t)"), t("Bas 90 % (t)", "Low 90% (t)"),
                           t("Haut 90 % (t)", "High 90% (t)")],
                   [[x.month.strftime("%Y-%m"), int(x.days), x.yhat, x.lo90, x.hi90] for x in fm.itertuples()],
                   {2: "#,##0.0", 3: "#,##0.0", 4: "#,##0.0"})
        r = _h2(ws, r, t("Par jour", "Daily"))
        d = fc["daily"]
        start = r
        r = _table(ws, r, ["Date", t("Horizon (j)", "Horizon (d)"), t("Prévu (t)", "Forecast (t)"), t("Bas 90 %", "Low 90%"),
                           t("Haut 90 %", "High 90%")],
                   [[x.date.to_pydatetime(), int(x.horizon), x.yhat, x.lo90, x.hi90] for x in d.itertuples()],
                   {0: "yyyy-mm-dd", 2: "0.00", 3: "0.00", 4: "0.00"})
        lc = LineChart()
        lc.title, lc.y_axis.title = t("Prévision journalière (tCO₂e)", "Daily forecast (tCO₂e)"), "tCO₂e"
        lc.add_data(Reference(ws, min_col=3, max_col=5, min_row=start, max_row=start + len(d)), titles_from_data=True)
        lc.set_categories(Reference(ws, min_col=1, min_row=start + 1, max_row=start + len(d)))
        _color_series(lc, [COLORS["fc"], COLORS["band"], COLORS["band"]])
        for s in lc.series:
            s.smooth = False
            s.graphicalProperties.line.width = 15000
        lc.x_axis.number_format = "mmm yy"
        lc.height, lc.width = 9, 26
        ws.add_chart(lc, "H4")
    else:
        ws.cell(row=r, column=1, value=t("Aucune prévision enregistrée.", "No stored forecast."))
    _widths(ws, {"A": 26, "B": 34})

    # 6 ── Budget -------------------------------------------------------------------
    ws = wb.create_sheet("Budget")
    r = _title(ws, t(f"Budget carbone {bud['year']}", f"{bud['year']} carbon budget"), subtitle)
    r = _table(ws, r, [t("Élément", "Item"), t("Valeur", "Value")], [
        [t("Plafond annuel (t)", "Annual cap (t)"), bud["cap"]],
        [t("Déjà émis (t)", "Emitted so far (t)"), round(bud["actual_ytd"], 1)],
        [t("Projection sur les mois couverts (t)", "Projection over covered months (t)"), round(bud["projected_covered"], 1)],
        [t("Plafonds cumulés sur ces mois (t)", "Cumulative ceilings over those months (t)"), round(bud["ceiling_covered"], 1)],
    ], {1: "#,##0.0"})
    labels = {"green": t("Dans la cible", "On track"), "amber": t("À surveiller", "At risk"),
              "red": t("Dépassement", "Over budget"), "nodata": t("Non couvert", "Not covered")}
    bt = bud["table"]
    start = r
    r = _table(ws, r, [t("Mois", "Month"), t("Plafond (t)", "Ceiling (t)"), t("Réel (t)", "Actual (t)"), t("Prévu (t)", "Forecast (t)"),
                       t("Projection (t)", "Projected (t)"), t("Borne haute 90 % (t)", "Upper 90% (t)"), t("Écart (t)", "Gap (t)"),
                       t("Statut", "Status")],
               [[x.month.strftime("%Y-%m"), x.ceiling, x.actual, x.forecast, x.projected, x.upper,
                 x.projected - x.ceiling if x.covered else None, labels[x.status]] for x in bt.itertuples()],
               {k: "#,##0.0" for k in range(1, 6)} | {6: "+#,##0.0;-#,##0.0"})
    for k, x in enumerate(bt.itertuples(), start=1):
        ws.cell(row=start + k, column=8).fill = PatternFill("solid", start_color=STATUS_FILL[x.status], end_color=STATUS_FILL[x.status])
    ws.cell(row=r, column=1, value=t("Règle : vert si la borne haute 90 % reste sous le plafond, orange si seule la projection "
                                      "centrale y reste, rouge sinon.",
                                      "Rule: green if the 90% upper bound stays under the ceiling, amber if only the central "
                                      "projection does, red otherwise.")).font = SUB
    tg = ins["targets"]
    r = _h2(ws, r + 2, t("Trajectoire vers l'objectif", "Path to the target"))
    ws.cell(row=r, column=1, value=tg["describe"]).font = SUB
    r += 1
    path = tg["path"]
    r = _table(ws, r, [t("Année", "Year"), t("Plafond sur la trajectoire (t)", "Cap on the path (t)"), t("Réel (t)", "Actual (t)"),
                       t("Écart (t)", "Gap (t)"), t("Sur la trajectoire", "On the path")],
               [[int(x.year), x.cap, x.actual, x.gap,
                 "—" if x.on_track is None or pd.isna(x.on_track) else (t("oui", "yes") if x.on_track else t("non", "no"))]
                for x in path.itertuples()],
               {1: "#,##0.0", 2: "#,##0.0", 3: "+#,##0.0;-#,##0.0"})
    src_w = (t(f"saisonniers, moyenne {', '.join(map(str, tg['weights_years']))}",
               f"seasonal, average of {', '.join(map(str, tg['weights_years']))}")
             if tg["weights_source"] == "seasonal" else t("saisis à la main", "entered by hand"))
    r = _h2(ws, r, t(f"Poids mensuels ({src_w})", f"Monthly weights ({src_w})"))
    r = _table(ws, r, [t("Mois", "Month"), t("Poids", "Weight"), t(f"Plafond {bud['year']} (t)", f"{bud['year']} ceiling (t)")],
               [[f.month(pd.Timestamp(bud["year"], m, 1), year=False), w, w * bud["cap"]]
                for m, w in sorted(tg["monthly_weights"].items())],
               {1: "0.0%", 2: "#,##0.0"})
    _widths(ws, {"A": 40})

    # 6b ── GHG inventory (Scope 1, Scope 2 both methods, Scope 3 screening) ----------------
    s2, s3 = ins["scope2"], ins["scope3"]
    w0, w1 = ins["breakdown"]["window"]
    ws = wb.create_sheet(t("Inventaire GES", "GHG inventory"))
    r = _title(ws, t("Inventaire GES – Scopes 1, 2 et 3 (12 derniers mois)", "GHG inventory – Scopes 1, 2 and 3 (last 12 months)"),
               subtitle + f" · {w0:%Y-%m-%d} → {w1:%Y-%m-%d}")
    r = _table(ws, r, [t("Poste", "Line"), "tCO₂e", t("Méthode", "Method")], [
        ["Scope 1", tot["s1_12"], t("Fioul et gazole × facteurs", "Fuel oil and diesel × factors")],
        [t("Scope 2 – méthode localisation", "Scope 2 – location-based"), s2["location_t"],
         t(f"kWh réseau × {s2['grid_kgco2_per_kwh']:g} kgCO₂e/kWh (moyenne du réseau)",
           f"grid kWh × {s2['grid_kgco2_per_kwh']:g} kgCO₂e/kWh (grid average)")],
        [t("Scope 2 – méthode marché", "Scope 2 – market-based"), s2["market_t"],
         t(f"kWh sous contrat × facteur du contrat + reste × mix résiduel {s2['residual_mix_kgco2_per_kwh']:g}",
           f"contracted kWh × contract factor + rest × residual mix {s2['residual_mix_kgco2_per_kwh']:g}")],
        [t("Scope 3 – présélection", "Scope 3 – screening"), s3["total_t"],
         t("Voir le détail ci-dessous (calculé / estimé / exclu)", "See detail below (calculated / estimated / excluded)")],
    ], {1: "#,##0.0"})
    ws.cell(row=r - 1, column=1, value=s2["note"]).font = SUB
    r = _h2(ws, r, t("Scope 2 par année, deux méthodes", "Scope 2 by year, both methods"))
    r = _table(ws, r, [t("Année", "Year"), t("Localisation (t)", "Location-based (t)"), t("Marché (t)", "Market-based (t)")],
               [[int(x.year), x.location_t, x.market_t] for x in s2["annual"].itertuples()], {1: "#,##0.0", 2: "#,##0.0"})
    r = _h2(ws, r, t("Scope 3 – présélection des 15 catégories", "Scope 3 – screening of the 15 categories"))
    st_lbl = {"calculated": t("Calculé", "Calculated"), "estimated": t("Estimé", "Estimated"), "excluded": t("Exclu", "Excluded")}
    tb = s3["table"]
    r = _table(ws, r, [t("Cat.", "Cat."), t("Catégorie", "Category"), t("Poste", "Item"), t("Statut", "Status"), "tCO₂e",
                       t("Part", "Share"), t("Méthode", "Method"), t("Qualité", "Quality"), t("Source ou motif", "Source or reason")],
               [[int(x.category), getattr(x, f"category_{f.lang}"), getattr(x, f"item_{f.lang}"), st_lbl[x.status], x.t,
                 None if x.share_pct is None or pd.isna(x.share_pct) else x.share_pct / 100,
                 getattr(x, f"method_{f.lang}"), x.quality, x.source] for x in tb.itertuples()],
               {4: "#,##0.0", 5: "0.0%"})
    ws.cell(row=r - 1, column=1, value=t(
        "Les postes calculés (déchets, eau) sont déjà dans le total du budget (« autres »). Les postes estimés reposent sur "
        "des hypothèses de config.yaml (scope3) à remplacer par les achats réels et une enquête déplacements.",
        "Calculated lines (waste, water) are already in the budget total (\"other\"). Estimated lines rest on assumptions "
        "in config.yaml (scope3), to replace with actual purchases and a commuting survey.")).font = SUB
    _widths(ws, {"A": 34, "B": 34, "C": 34, "D": 12, "E": 12, "F": 9, "G": 50, "H": 9, "I": 60})

    # 6c ── Breakdown by zone and item ------------------------------------------------------
    bd = ins["breakdown"]
    ws = wb.create_sheet(t("Répartition", "Breakdown"))
    r = _title(ws, t("Répartition par zone et par poste (12 derniers mois)", "Breakdown by zone and item (last 12 months)"),
               subtitle + f" · {w0:%Y-%m-%d} → {w1:%Y-%m-%d}")
    r = _table(ws, r, [t("Zone", "Zone"), t("Responsable", "Owner"), "tCO₂e", t("Part", "Share")],
               [[getattr(x, f"zone_{f.lang}"), x.owner, x.t, x.share_pct / 100] for x in bd["zones"].itertuples()],
               {2: "#,##0.0", 3: "0.0%"})
    r = _table(ws, r, [t("Poste", "Item"), t("Zone", "Zone"), t("Source", "Source"), "Scope", "tCO₂e", t("Part", "Share")],
               [[getattr(x, f"item_{f.lang}"), getattr(x, f"zone_{f.lang}"), getattr(x, f"source_{f.lang}"),
                 {"scope1": "1", "scope2": "2", "other": t("autre", "other")}[x.scope], x.t, x.share_pct / 100]
                for x in bd["items"].itertuples()], {4: "#,##0.0", 5: "0.0%"})
    ws.cell(row=r - 1, column=1, value=t(
        "Clés de répartition estimées (config.yaml, breakdown) : chaque compteur du site est réparti entre les postes ; à "
        "remplacer par des sous-compteurs. Le total est égal au total du site.",
        "Estimated allocation keys (config.yaml, breakdown): each site meter is split between items; replace with "
        "sub-meters. The total equals the site total.")).font = SUB
    _widths(ws, {"A": 40, "B": 28, "C": 18})

    # 6d ── Alerts ----------------------------------------------------------------------------
    al = ins.get("alerts") or {}
    ws = wb.create_sheet(t("Alertes", "Alerts"))
    r = _title(ws, t("Alertes", "Alerts"),
               subtitle + (t(f" · vérifiées le {al['checked_at']}", f" · checked {al['checked_at']}") if al.get("checked_at") else ""))
    sev = {"critical": t("Critique", "Critical"), "warning": t("À surveiller", "Warning"), "info": t("Information", "Info")}
    rows = [[sev.get(a["severity"], a["severity"]), a[f"title_{f.lang}"], a[f"detail_{f.lang}"], str(a.get("first_seen", ""))[:10]]
            for a in al.get("alerts", [])]
    if rows:
        r = _table(ws, r, [t("Gravité", "Severity"), t("Alerte", "Alert"), t("Détail", "Detail"), t("Depuis", "Since")], rows)
    else:
        ws.cell(row=r, column=1, value=t("Aucune alerte ouverte.", "No open alert."))
    _widths(ws, {"A": 14, "B": 48, "C": 90, "D": 12})

    # 7 ── Models -------------------------------------------------------------------
    ws = wb.create_sheet(t("Modèles", "Models"))
    r = _title(ws, t("Modèles de prévision et précision", "Forecast models and accuracy"), subtitle)
    md = ins["models"]
    if not md.empty:
        r = _h2(ws, r, t("Backtest (moyenne des fenêtres)", "Backtest (mean over windows)"))
        r = _table(ws, r, [t("Modèle", "Model"), "MAE (t/j)", "MASE", t("Couverture 90 %", "90% coverage"),
                           t("Largeur IC (t)", "CI width (t)"), t("Fenêtres", "Windows")],
                   [[x.name, x.mae, x.mase, x.coverage, x.mean_width, int(x.windows)] for x in md.itertuples()],
                   {1: "0.000", 2: "0.000", 3: "0.0%", 4: "0.00"})
    meta = ins["backtest_meta"]
    rows = [[t("Protocole", "Protocol"), meta.get("protocol", "—")], [t("Note", "Note"), meta.get("note", "—")],
            [t("Routeur", "Router"), t(ins["router"]["fr"], ins["router"]["en"])],
            [t("Choix actuel", "Current choice"), t(f"{ins['router']['history_days']} jours → ", f"{ins['router']['history_days']} days → ")
             + MODEL_NAMES[ins["router"]["choice"]]]]
    r = _table(ws, r, [t("Élément", "Item"), t("Détail", "Detail")], rows)
    if ins["levers"]:
        r = _h2(ws, r, t("Effet des leviers (12 mois avant / après)", "Lever effect (12 months before / after)"))
        _table(ws, r, [t("Levier", "Lever"), t("Début", "Start"), t("Avant", "Before"), t("Après", "After"), t("Variation", "Change")],
               [[lv["label"], f"{lv['start']:%Y-%m-%d}", lv["before"], lv["after"], lv["change_pct"] / 100] for lv in ins["levers"]],
               {2: "#,##0.000", 3: "#,##0.000", 4: "+0.0%;-0.0%"})
    _widths(ws, {"B": 70})
    for row in ws.iter_rows():
        for c in row:
            if c.column == 2:
                c.alignment = WRAP

    # 8 ── Data quality -------------------------------------------------------------
    ws = wb.create_sheet(t("Qualité", "Data quality"))
    r = _title(ws, t("Qualité et couverture des données", "Data quality and coverage"), subtitle)
    r = _table(ws, r, [t("Indicateur", "Indicator"), t("Valeur", "Value")], [
        [t("Fichiers analysés", "Files scanned"), q["scanned"]], [t("Acceptés", "Accepted"), q["accepted"]],
        [t("En quarantaine", "Quarantined"), q["rejected"]], [t("Corrections automatiques", "Automatic repairs"), q["warnings"]],
        [t("Jours imputés", "Imputed days"), per["imputed"]], [t("Part imputée", "Imputed share"), per["imputed_pct"] / 100],
    ], {1: "#,##0.###"})
    ws.cell(row=r - 2, column=2).number_format = "0.0%"
    r = _h2(ws, r, t("Services", "Departments"))
    dept_rows = []
    for d, last in q["dept_last"].items():
        state = t("à jour", "up to date")
        if last is None:
            state = t("aucune donnée", "no data")
        elif d in q["pending"]:
            state = t("en avance (attend les autres services)", "ahead (waiting for others)")
        elif d in q["behind"]:
            state = t(f"en retard de {q['behind'][d]} j", f"{q['behind'][d]} d behind")
        dept_rows.append([t(*DEPT_NAMES[d]), f"{last:%Y-%m-%d}" if last is not None else "—", state])
    r = _table(ws, r, [t("Service", "Department"), t("Dernier jour reçu", "Last day received"), t("État", "State")], dept_rows)
    r = _h2(ws, r, t("Fichiers en quarantaine", "Quarantined files"))
    _table(ws, r, [t("Fichier", "File"), t("Service", "Department"), t("Motif", "Reason"), t("Correction suggérée", "Suggested fix")],
           [[x["file"], t(*DEPT_NAMES.get(x["department"], (x["department"],) * 2)), x["reason"], x["fix"]] for x in q["quarantine"]]
           or [[t("Aucun", "None"), "", "", ""]])
    _widths(ws, {"C": 70, "D": 60})
    for row in ws.iter_rows(min_row=r):
        for c in row:
            c.alignment = WRAP

    # 9 ── Methodology --------------------------------------------------------------
    ws = wb.create_sheet(t("Méthodologie", "Methodology"))
    r = _title(ws, t("Méthodologie et facteurs d'émission", "Methodology and emission factors"), subtitle)
    ef = ins["factors"]
    r = _table(ws, r, [t("Source", "Source"), t("Facteur", "Factor"), t("Unité", "Unit"), t("Scope", "Scope"), t("Référence", "Reference")], [
        [t("Électricité réseau", "Grid electricity"), ef.get("electricity_kgco2_per_kwh"), "kgCO₂e/kWh", "2",
         t("Réseau tunisien 2025 (Electricity Maps)", "Tunisian grid 2025 (Electricity Maps)")],
        [t("Fioul", "Fuel oil"), ef.get("fuel_oil_kgco2_per_l"), "kgCO₂e/L", "1", "ADEME / DEFRA 2024"],
        ["Diesel", ef.get("diesel_kgco2_per_l"), "kgCO₂e/L", "1", "ADEME / DEFRA 2024"],
        [t("Déchets", "Waste"), ef.get("waste_kgco2_per_kg"), "kgCO₂e/kg", t("autre", "other"), "ADEME"],
        [t("Eau", "Water"), ef.get("water_kgco2_per_m3"), "kgCO₂e/m³", t("autre", "other"), t("Facteur municipal", "Municipal factor")],
    ], {1: "0.000"})
    ws.cell(row=r - 1, column=1, value=t(f"Facteurs revus le {ins['factors_review']}", f"Factors reviewed {ins['factors_review']}")).font = SUB
    r = _h2(ws, r, t("Méthode", "Method"))
    steps = [
        t("Collecte : un CSV par service et par mois (énergie, production, logistique, déchets & eau).",
          "Collection: one CSV per department and month (energy, production, logistics, waste & water)."),
        t("Validation : schémas stricts, réparation automatique (séparateurs, formats français, unités, alias de colonnes) ; "
          "les fichiers non réparables sont mis en quarantaine avec un motif.",
          "Validation: strict schemas, automatic repair (delimiters, French formats, units, column aliases); "
          "files that cannot be repaired are quarantined with a reason."),
        t("Consolidation : une ligne par jour ; un mois n'est intégré que lorsque tous les services l'ont transmis ; "
          "les trous courts sont imputés et signalés.",
          "Consolidation: one row per day; a month is included only once every department has sent it; "
          "short gaps are imputed and flagged."),
        t("Calcul : émissions = activité × facteur, réparties en Scope 1 (combustibles), Scope 2 (électricité, méthode "
          "localisation) et autres (déchets, eau).",
          "Calculation: emissions = activity × factor, split into Scope 1 (fuels), Scope 2 (electricity, location-based) "
          "and other (waste, water)."),
        t("Scope 2 méthode marché : kWh couverts par des instruments contractuels (PPA, offre verte, certificats) × leur "
          "facteur, le reste × le mix résiduel (GHG Protocol Scope 2 Guidance ; ESRS E1-6 demande les deux chiffres).",
          "Market-based Scope 2: kWh covered by contractual instruments (PPA, green tariff, certificates) × their factor, "
          "the rest × the residual mix (GHG Protocol Scope 2 Guidance; ESRS E1-6 asks for both figures)."),
        t("Scope 3 : présélection des 15 catégories du GHG Protocol, chacune calculée, estimée (hypothèses sourcées) ou "
          "exclue avec motif.",
          "Scope 3: screening of the 15 GHG Protocol categories, each calculated, estimated (sourced assumptions) or "
          "excluded with a reason."),
        t("Répartition : chaque compteur du site est réparti entre zones (P1 coupe, P2 préparation, P3 assemblage, "
          "utilités, logistique) et postes par des clés de répartition.",
          "Breakdown: each site meter is split between zones (P1 cutting, P2 preparation, P3 assembly, utilities, "
          "logistics) and items with allocation keys."),
        t("Prévision : Prophet (tendance + saisonnalités + régresseurs), Chronos-2 (modèle de fondation, zéro-shot) et "
          "Ens-PC (moyenne des deux) ; intervalle de confiance 90 %.",
          "Forecast: Prophet (trend + seasonalities + regressors), Chronos-2 (foundation model, zero-shot) and "
          "Ens-PC (average of both); 90% confidence interval."),
        t("Budget : plafond de l'année lu sur la trajectoire linéaire de l'année de référence à l'objectif, réparti par mois "
          "selon le profil saisonnier des dernières années complètes ; statut d'après la projection et sa borne haute.",
          "Budget: the year's cap read on the straight-line path from the base year to the target, split by month with the "
          "seasonal profile of the last complete years; status from the projection and its upper bound."),
        t("Alertes : vérifiées à chaque exécution du pipeline (budget, fin d'année, jours hors bande 90 %, services en "
          "retard, quarantaine).",
          "Alerts: checked at each pipeline run (budget, year-end, days outside the 90% band, late departments, "
          "quarantine)."),
    ]
    for k, s in enumerate(steps, 1):
        ws.cell(row=r, column=1, value=f"{k}.")
        c = ws.cell(row=r, column=2, value=s)
        c.alignment = WRAP
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        ws.row_dimensions[r].height = 32
        r += 1
    _widths(ws, {"A": 22, "B": 14, "C": 14, "D": 10, "E": 46})

    # 10 ── Log ---------------------------------------------------------------------
    ws = wb.create_sheet(t("Journal", "Log"))
    r = _title(ws, t("Journal des traitements et dépôts", "Processing and upload log"), subtitle)
    r = _h2(ws, r, t("Exécutions du pipeline", "Pipeline runs"))
    r = _table(ws, r, [t("Début", "Started"), t("Origine", "Trigger"), t("Jours avant", "Days before"), t("Jours après", "Days after"),
                       t("Données jusqu'au", "Data through"), t("Modèle", "Model"), t("Durée (s)", "Time (s)")],
               [[h.get("started_at", "").replace("T", " "), h.get("trigger"), h.get("before", {}).get("days"),
                 h.get("after", {}).get("days"), h.get("after", {}).get("last"),
                 MODEL_NAMES.get(h.get("steps", {}).get("forecast", {}).get("model"), "—"), h.get("seconds")]
                for h in reversed(ins["history"])] or [["—"] * 7])
    r = _h2(ws, r, t("Fichiers déposés depuis le tableau de bord", "Files uploaded from the dashboard"))
    _table(ws, r, [t("Fichier", "File"), t("Service", "Department"), t("Lignes", "Rows"), t("Du", "From"), t("Au", "To"),
                   t("Déposé le", "Uploaded")],
           [[u["file"], t(*DEPT_NAMES.get(u["department"], (u["department"],) * 2)), u["rows"], u["first_date"], u["last_date"],
             u["uploaded_at"].replace("T", " ")] for u in ins["uploads"]] or [["—"] * 6])
    _widths(ws)

    wb.save(output_path)
    log.info("Excel report written: %s (%d sheets)", Path(output_path).name, len(wb.sheetnames))
    return Path(output_path)


if __name__ == "__main__":
    print(generate_excel_report())
