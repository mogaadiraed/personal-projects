"""PDF executive brief: key figures, charts, conclusions, budget, forecast, models, recommendations, then the
GHG inventory (Scope 2 both methods, Scope 3 screening), the path to the target, the breakdown and the alerts.

Built with matplotlib only (no extra dependency). Text and figures come from ``insights.build_insights``.
"""
from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch, Rectangle

from carbonsync.export.insights import DEPT_NAMES, Fmt, build_insights
from carbonsync.obs import get_logger

log = get_logger(__name__)

A4 = (8.27, 11.69)
NAVY, INK, MUTED, LINE = "#1F3A5F", "#1d2127", "#6b7078", "#d9dee5"
S1, S2, OTHER, FC, NEUTRAL = "#eb6834", "#3987e5", "#1baf7a", "#3987e5", "#898781"
STATUS = {"green": "#0ca30c", "amber": "#fab219", "red": "#d03b3b", "nodata": "#c9ccd1"}
LEFT, RIGHT = 0.07, 0.93

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": LINE, "axes.linewidth": 0.6,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.labelcolor": MUTED,
                     "xtick.major.size": 0, "ytick.major.size": 0})


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _clean(text: str) -> str:
    return text.replace(" ", " ")


class _Doc:
    """Tiny page/flow manager: header band on every page, flowing text that breaks pages."""

    def __init__(self, pdf: PdfPages, title: str, right: str, footer: str):
        self.pdf, self.title, self.right, self.footer = pdf, title, right, footer
        self.fig = None
        self.page = 0
        self.y = 0.0

    def new_page(self) -> None:
        if self.fig is not None:
            self.close_page()
        self.page += 1
        self.fig = plt.figure(figsize=A4)
        self.fig.patches.append(Rectangle((0, 0.955), 1, 0.045, transform=self.fig.transFigure, color=NAVY, zorder=0))
        self.fig.text(LEFT, 0.972, _clean(self.title), color="white", fontsize=12.5, weight="bold", va="center")
        self.fig.text(RIGHT, 0.972, _clean(self.right), color="white", fontsize=8, ha="right", va="center")
        self.fig.text(LEFT, 0.02, _clean(self.footer), color=MUTED, fontsize=6.5)
        self.fig.text(RIGHT, 0.02, str(self.page), color=MUTED, fontsize=6.5, ha="right")
        self.y = 0.935

    def close_page(self) -> None:
        self.pdf.savefig(self.fig)
        plt.close(self.fig)
        self.fig = None

    def need(self, h: float) -> None:
        if self.y - h < 0.05:
            self.new_page()

    def heading(self, text: str, size: float = 10.5) -> None:
        self.need(0.05)
        self.fig.text(LEFT, self.y, _clean(text), fontsize=size, weight="bold", color=NAVY, va="top")
        self.y -= 0.024

    def para(self, text: str, size: float = 8.2, width: int = 112, color: str = INK, bullet: str = "") -> None:
        lines = textwrap.wrap(_clean(text), width=width - (4 if bullet else 0))
        lh = size * 1.42 / 72 / A4[1]
        self.need(lh * len(lines) + 0.004)
        if bullet:
            self.fig.text(LEFT, self.y, bullet, fontsize=size, color=NAVY, va="top", weight="bold")
        for k, line in enumerate(lines):
            self.fig.text(LEFT + (0.028 if bullet else 0), self.y - k * lh, line, fontsize=size, color=color, va="top")
        self.y -= lh * len(lines) + 0.006

    def axes(self, h: float, x0: float = LEFT, w: float = RIGHT - LEFT, advance: bool = True):
        self.need(h + 0.02)
        ax = self.fig.add_axes([x0, self.y - h, w, h])
        if advance:
            self.y -= h + 0.035
        return ax


def _kpis(doc: _Doc, items: list[tuple[str, str, str]]) -> None:
    h, gap = 0.072, 0.012
    w = (RIGHT - LEFT - gap * (len(items) - 1)) / len(items)
    for k, (label, value, note) in enumerate(items):
        x = LEFT + k * (w + gap)
        doc.fig.patches.append(FancyBboxPatch((x, doc.y - h), w, h, boxstyle="round,pad=0,rounding_size=0.008",
                                              transform=doc.fig.transFigure, facecolor="#f3f6fa", edgecolor=LINE, lw=0.6))
        doc.fig.text(x + 0.012, doc.y - 0.014, _clean(label), fontsize=7, color=MUTED, va="top")
        doc.fig.text(x + 0.012, doc.y - 0.033, _clean(value), fontsize=13.5, weight="bold", color=INK, va="top")
        doc.fig.text(x + 0.012, doc.y - 0.058, _clean(note), fontsize=6.6, color=MUTED, va="top")
    doc.y -= h + 0.03


def _style(ax, title: str = "") -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color=LINE, lw=0.5)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(_clean(title), loc="left", fontsize=8.8, color=INK, weight="bold", pad=6)


def _table(ax, header: list[str], rows: list[list[str]], col_w: list[float], status_col: int | None = None,
           status: list[str] | None = None, title: str = "", left_cols: tuple[int, ...] = (0,)) -> None:
    ax.axis("off")
    if title:
        ax.set_title(_clean(title), loc="left", fontsize=8.8, color=INK, weight="bold", pad=4)
    tb = ax.table(cellText=[[_clean(c) for c in r] for r in rows], colLabels=[_clean(h) for h in header],
                  colWidths=col_w, loc="upper left", cellLoc="right", colLoc="right")
    tb.auto_set_font_size(False)
    tb.set_fontsize(6.9)
    tb.scale(1, 1.18)
    for (r, c), cell in tb.get_celld().items():
        cell.set_edgecolor(LINE)
        cell.set_linewidth(0.4)
        if c in left_cols:
            cell.set_text_props(ha="left")
            cell._loc = "left"
        if r == 0:
            cell.set_facecolor(NAVY)
            cell.set_text_props(color="white", weight="bold")
        elif r % 2 == 0:
            cell.set_facecolor("#f3f6fa")
        if r > 0 and status_col is not None and status and c == status_col:
            cell.set_facecolor(STATUS[status[r - 1]])
            cell.set_text_props(color="white" if status[r - 1] in ("green", "red") else INK, weight="bold")


# ---------------------------------------------------------------------------
def generate_pdf_brief(output_path: Path | None = None, lang: str | None = None, run_id: str | None = None,
                       insights: dict[str, Any] | None = None, config_path: Path | None = None) -> Path:
    root = get_project_root()
    cfg = yaml.safe_load((config_path or root / "config.yaml").read_text(encoding="utf-8"))
    lang = lang or cfg.get("app", {}).get("language", "fr")
    ins = insights or build_insights(lang, run_id=run_id, config_path=config_path)
    f = Fmt(lang)
    t = f.t
    if output_path is None:
        out_dir = root / "data" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"CarbonSync_Brief_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.pdf"

    per, tot, fc, bud, q = ins["period"], ins["totals"], ins["forecast"], ins["budget"], ins["quality"]
    footer = t(f"CarbonSync AI · facteurs revus le {ins['factors_review']} · GHG Protocol · CSRD / ESRS E1",
               f"CarbonSync AI · factors reviewed {ins['factors_review']} · GHG Protocol · CSRD / ESRS E1")
    if ins["synthetic"]:
        footer += t(" · données synthétiques", " · synthetic data")

    with PdfPages(output_path) as pdf:
        doc = _Doc(pdf, t("Rapport de synthèse carbone", "Carbon summary report"),
                   f"{ins['site']} · {ins['generated_at']:%Y-%m-%d}", footer)
        doc.new_page()

        # ---- Page 1: headline -----------------------------------------------------
        doc.para(t(f"Données du {f.date(per['first'])} au {f.date(per['last'])} · {f.num(per['days'])} jours · "
                   f"prévision {fc.get('model_name', '—')}",
                   f"Data from {f.date(per['first'])} to {f.date(per['last'])} · {f.num(per['days'])} days · "
                   f"{fc.get('model_name', '—')} forecast"), size=8.5, color=MUTED)
        doc.y -= 0.004
        items = [
            (t("Émissions 12 derniers mois", "Emissions, last 12 months"), f"{f.num(tot['last12'])} t",
             t("vs 12 mois précédents : ", "vs previous 12 months: ") + f.pct(tot["yoy_pct"], 1, True)),
            (t("Moyenne journalière", "Daily average"), f"{f.num(tot['daily_mean_12'], 2)} t",
             t(f"intensité {f.num(tot['intensity_12'], 1)} kg/unité", f"intensity {f.num(tot['intensity_12'], 1)} kg/unit")),
            (t("Part Scope 2", "Scope 2 share"), f.pct(tot["shares"]["co2e_scope2_t"], 0),
             t(f"Scope 1 {f.pct(tot['shares']['co2e_scope1_t'], 0)} · autres {f.pct(tot['shares']['co2e_other_t'], 0)}",
               f"Scope 1 {f.pct(tot['shares']['co2e_scope1_t'], 0)} · other {f.pct(tot['shares']['co2e_other_t'], 0)}")),
        ]
        if fc.get("available"):
            items.append((t(f"Prévision {fc['days']} jours", f"{fc['days']}-day forecast"), f"{f.num(fc['total'])} t",
                          t(f"IC 90 % {f.num(fc['lo_total'])}–{f.num(fc['hi_total'])} t",
                            f"90% CI {f.num(fc['lo_total'])}–{f.num(fc['hi_total'])} t")))
        _kpis(doc, items)

        # Monthly history + forecast
        m = ins["monthly"].tail(36)
        ax = doc.axes(0.19)
        x = list(m["month"])
        ax.bar(x, m["s2"], width=22, color=S2, label="Scope 2")
        ax.bar(x, m["s1"], width=22, bottom=m["s2"], color=S1, label="Scope 1")
        ax.bar(x, m["other"], width=22, bottom=m["s2"] + m["s1"], color=OTHER, label=t("Autres", "Other"))
        if fc.get("available"):
            fm = fc["monthly"]
            full = fm[fm["days"] >= 25]
            ax.bar(full["month"], full["yhat"], width=22, color=FC, alpha=0.28, edgecolor=FC, lw=0.8,
                   label=t("Prévision", "Forecast"))
            ax.errorbar(full["month"], full["yhat"], yerr=[full["yhat"] - full["lo90"], full["hi90"] - full["yhat"]],
                        fmt="none", ecolor=FC, elinewidth=0.8, capsize=2, label=t("IC 90 %", "90% CI"))
        _style(ax, t("Émissions mensuelles (tCO₂e) — 36 derniers mois et prévision",
                     "Monthly emissions (tCO₂e) — last 36 months and forecast"))
        ax.legend(ncol=5, fontsize=6.8, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.08))
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m/%y"))
        doc.y -= 0.02

        doc.heading(t("Conclusions", "Conclusions"))
        for k, c in enumerate(ins["conclusions"], 1):
            doc.para(c, bullet=f"{k}.", size=7.7, width=120)

        # ---- Page 2: recommendations and details -----------------------------------------
        doc.new_page()
        doc.heading(t("Recommandations", "Recommendations"))
        for r in ins["recommendations"]:
            doc.para(r, bullet="→", size=7.7, width=120)
        top = doc.y - 0.03
        axl = doc.fig.add_axes([LEFT, top - 0.17, 0.40, 0.17])
        a = ins["annual"]
        yrs = a["year"].astype(str) + a["complete"].map({True: "", False: "*"})
        axl.bar(yrs, a["s2"], color=S2, width=0.62)
        axl.bar(yrs, a["s1"], bottom=a["s2"], color=S1, width=0.62)
        axl.bar(yrs, a["other"], bottom=a["s2"] + a["s1"], color=OTHER, width=0.62)
        _style(axl, t("Émissions annuelles (tCO₂e)", "Annual emissions (tCO₂e)"))
        axl.tick_params(axis="x", labelsize=6.5)
        if (~a["complete"]).any():
            axl.text(0, -0.16, t("* année partielle", "* partial year"), transform=axl.transAxes, fontsize=6.3, color=MUTED)

        axr = doc.fig.add_axes([0.55, top - 0.17, RIGHT - 0.55, 0.17])
        b = bud["table"]
        lbl = [f.month(mm, False) for mm in b["month"]]
        axr.bar(lbl, b["actual"], color=NEUTRAL, width=0.62, label=t("Réel", "Actual"))
        axr.bar(lbl, b["forecast"], bottom=b["actual"], color=FC, alpha=0.55, width=0.62, label=t("Prévu", "Forecast"))
        axr.scatter(lbl, b["ceiling"], marker="_", s=180, color="#d03b3b", lw=1.6, label=t("Plafond", "Ceiling"), zorder=3)
        _style(axr, t(f"Budget {bud['year']} par mois (tCO₂e)", f"{bud['year']} budget by month (tCO₂e)"))
        axr.tick_params(axis="x", labelsize=6.3)
        axr.legend(fontsize=6.3, frameon=False, ncol=3, loc="upper left", bbox_to_anchor=(0, -0.1))
        doc.y = top - 0.245

        # Tables: forecast by month + model accuracy
        row_h = 0.0172
        n_rows = max(len(fc["monthly"]) if fc.get("available") else 0, len(ins["models"])) + 1
        h = row_h * n_rows
        if fc.get("available"):
            fm = fc["monthly"]
            rows = [[f.month(x.month), str(int(x.days)), f.num(x.yhat, 1), f.num(x.lo90, 1), f.num(x.hi90, 1)]
                    for x in fm.itertuples()]
            ax = doc.fig.add_axes([LEFT, doc.y - h, 0.40, h])
            _table(ax, [t("Mois", "Month"), t("Jours", "Days"), t("Prévu", "Forecast"), t("Bas 90 %", "Low 90%"),
                        t("Haut 90 %", "High 90%")], rows, [0.26, 0.14, 0.2, 0.2, 0.2],
                   title=t(f"Prévision par mois · {fc['model_name']} (t)", f"Forecast by month · {fc['model_name']} (t)"))
        md = ins["models"]
        if not md.empty:
            rows = [[x.name, f.num(x.mae, 2), f.num(x.mase, 2), f.pct(x.coverage * 100, 0)] for x in md.itertuples()]
            ax = doc.fig.add_axes([0.55, doc.y - h, RIGHT - 0.55, h])
            _table(ax, [t("Modèle", "Model"), "MAE", "MASE", t("Couv. 90 %", "90% cov.")], rows, [0.34, 0.2, 0.2, 0.26],
                   title=t("Précision en backtest", "Backtest accuracy"))
        doc.y -= h + 0.045

        # Budget table
        rows, status = [], []
        labels = {"green": t("Cible", "On track"), "amber": t("Risque", "At risk"), "red": t("Dépass.", "Over"),
                  "nodata": "—"}
        for x in b.itertuples():
            rows.append([f.month(x.month), f.num(x.ceiling), f.num(x.actual), f.num(x.forecast),
                         f.num(x.projected) if x.covered else "—", labels[x.status]])
            status.append(x.status)
        ax = doc.axes(row_h * (len(rows) + 1))
        _table(ax, [t("Mois", "Month"), t("Plafond", "Ceiling"), t("Réel", "Actual"), t("Prévu", "Forecast"),
                    t("Projection", "Projected"), t("Statut", "Status")], rows, [0.2, 0.16, 0.16, 0.16, 0.16, 0.16],
               status_col=5, status=status, title=t(f"Budget {bud['year']} (tCO₂e)", f"{bud['year']} budget (tCO₂e)"))

        # ---- Page 3: inventory, path, breakdown, alerts ---------------------------------
        doc.new_page()
        s2, s3, tg, bd = ins["scope2"], ins["scope3"], ins["targets"], ins["breakdown"]
        w0, w1 = bd["window"]
        doc.para(t(f"Inventaire GES sur 12 mois ({f.date(w0)} – {f.date(w1)})",
                   f"GHG inventory over 12 months ({f.date(w0)} – {f.date(w1)})"), size=8.5, color=MUTED)
        items = [
            (t("Scope 2 localisation", "Scope 2 location-based"), f"{f.num(s2['location_t'])} t",
             t("moyenne du réseau", "grid average")),
            (t("Scope 2 marché", "Scope 2 market-based"), f"{f.num(s2['market_t'])} t",
             t(f"{f.pct(s2['covered_pct'], 0)} sous contrat", f"{f.pct(s2['covered_pct'], 0)} under contract")),
            (t("Scope 3 (présélection)", "Scope 3 (screening)"), f"{f.num(s3['total_t'])} t",
             t("hors budget, estimations", "outside budget, estimates")),
        ]
        if tg.get("target_emissions_t") is not None:
            items.append((t(f"Objectif {tg['target_year']}", f"{tg['target_year']} target"),
                          f"{f.num(tg['target_emissions_t'])} t",
                          t(f"−{tg['reduction_pct']:g} % vs {tg['base_year']}", f"−{tg['reduction_pct']:g}% vs {tg['base_year']}")))
        _kpis(doc, items)

        top = doc.y - 0.01
        axl = doc.fig.add_axes([LEFT, top - 0.16, 0.40, 0.16])
        path = tg["path"]
        axl.plot(path["year"], path["cap"], color=NAVY, lw=1.6, label=t("Trajectoire (plafond)", "Path (cap)"))
        done = path.dropna(subset=["actual"])
        axl.bar(done["year"], done["actual"], color=NEUTRAL, width=0.6, label=t("Réel", "Actual"))
        _style(axl, t(f"Trajectoire vers −{tg['reduction_pct']:g} % en {tg['target_year']} (tCO₂e)",
                      f"Path to −{tg['reduction_pct']:g}% by {tg['target_year']} (tCO₂e)"))
        axl.set_ylim(0, max(float(path["cap"].max()), float(done["actual"].max()) if not done.empty else 0) * 1.12)
        axl.tick_params(axis="x", labelsize=6.3)
        axl.legend(fontsize=6.3, frameon=False, ncol=2, loc="upper left", bbox_to_anchor=(0, -0.08))

        axr = doc.fig.add_axes([0.62, top - 0.16, RIGHT - 0.62, 0.16])
        z = bd["zones"].iloc[::-1]
        axr.barh([_clean(v) for v in z[f"zone_{f.lang}"]], z["t"], color=S2, height=0.6)
        _style(axr, t("Répartition par zone (tCO₂e, 12 mois)", "Breakdown by zone (tCO₂e, 12 months)"))
        axr.grid(axis="x", color=LINE, lw=0.5)
        axr.grid(axis="y", visible=False)
        axr.tick_params(axis="y", labelsize=6.5)
        doc.y = top - 0.235

        tb = s3["table"]
        st_lbl = {"calculated": t("Calculé", "Calculated"), "estimated": t("Estimé", "Estimated"), "excluded": t("Exclu", "Excluded")}
        kept = tb[tb["status"] != "excluded"]
        rows = [[f"{int(x.category)}. {getattr(x, 'category_' + f.lang)}"[:46], getattr(x, "item_" + f.lang)[:40],
                 st_lbl[x.status], f.num(x.t, 1)] for x in kept.itertuples()]
        excl = ", ".join(str(c) for c in s3["excluded_categories"])
        if excl:
            rows.append([t(f"Catégories {excl}", f"Categories {excl}"), t("motifs dans le classeur Excel", "reasons in the Excel workbook"),
                         st_lbl["excluded"], "—"])
        ax = doc.axes(0.0155 * (len(rows) + 1))
        _table(ax, [t("Catégorie Scope 3", "Scope 3 category"), t("Poste", "Item"), t("Statut", "Status"), "tCO₂e"], rows,
               [0.42, 0.34, 0.12, 0.12], title=t("Scope 3 – présélection des 15 catégories", "Scope 3 – screening of the 15 categories"),
               left_cols=(0, 1))
        doc.para(t("Calculé : données du site (déchets, eau, déjà dans le budget). Estimé : hypothèses sourcées de config.yaml "
                   "(matières par unité, distances, trajets du personnel) à remplacer par les achats et une enquête. Exclu : "
                   "motif dans le classeur Excel.",
                   "Calculated: site data (waste, water, already in the budget). Estimated: sourced assumptions in "
                   "config.yaml (materials per unit, distances, staff commuting) to replace with purchases and a survey. "
                   "Excluded: reason in the Excel workbook."), size=7, color=MUTED, width=128)

        al = (ins.get("alerts") or {}).get("alerts", [])
        doc.heading(t(f"Alertes ({len(al)})", f"Alerts ({len(al)})"), size=9.5)
        sev = {"critical": t("CRITIQUE", "CRITICAL"), "warning": t("À SURVEILLER", "WARNING"), "info": "INFO"}
        for a in al[:8]:
            doc.para(f"{sev.get(a['severity'], a['severity'])} · {a['title_' + f.lang]} — {a['detail_' + f.lang]}",
                     size=7.2, bullet="!", width=128)
        if not al:
            doc.para(t("Aucune alerte ouverte.", "No open alert."), size=7.2)

        doc.heading(t("Données et méthode", "Data and method"), size=9)
        dept = ", ".join(f"{t(*DEPT_NAMES[k])} {f.date(v)}" for k, v in q["dept_last"].items() if v is not None)
        doc.para(t(f"{q['accepted']}/{q['scanned']} fichiers acceptés ({f.pct(q['acceptance_pct'], 1)}), {q['rejected']} en "
                   f"quarantaine, {q['warnings']} corrections automatiques, {f.pct(per['imputed_pct'], 1)} de jours imputés. "
                   f"Dernier jour reçu par service — {dept}. Fichiers déposés depuis le tableau de bord : {len(ins['uploads'])}.",
                   f"{q['accepted']}/{q['scanned']} files accepted ({f.pct(q['acceptance_pct'], 1)}), {q['rejected']} "
                   f"quarantined, {q['warnings']} automatic repairs, {f.pct(per['imputed_pct'], 1)} imputed days. "
                   f"Last day received per department — {dept}. Files uploaded from the dashboard: {len(ins['uploads'])}."),
                 size=7, color=MUTED, width=128)
        doc.para(t("Émissions = activité × facteur d'émission (Scope 1 : fioul et diesel ; Scope 2 : électricité réseau, "
                   "méthode localisation, et méthode marché avec contrats et mix résiduel ; autres : déchets et eau). "
                   "Budget : plafond lu sur la trajectoire linéaire vers l'objectif, réparti selon le profil saisonnier. Prévision : Prophet, Chronos-2 et Ens-PC (moyenne des deux), choisie par le "
                   f"routeur ({ins['router']['fr']}). Le détail complet "
                   "figure dans le classeur Excel d'audit.",
                   "Emissions = activity × emission factor (Scope 1: fuel oil and diesel; Scope 2: grid electricity, "
                   "location-based, and market-based with contracts and residual mix; other: waste and water). Budget: cap "
                   "read on the straight-line path to the target, split with the seasonal profile. Forecast: Prophet, Chronos-2 and Ens-PC (average of both), chosen by the router "
                   f"({ins['router']['en']}). Full detail is in the "
                   "Excel audit workbook."), size=7, color=MUTED, width=128)
        doc.close_page()

    log.info("PDF brief written: %s", Path(output_path).name)
    return Path(output_path)


if __name__ == "__main__":
    print(generate_pdf_brief())
