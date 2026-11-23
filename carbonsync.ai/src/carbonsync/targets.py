"""Reduction target, the yearly path to it, and the carbon budget calculated from that path.

* **Target** (``targets`` in config.yaml): a base year (a complete calendar year of data), a target year and a
  reduction in %, on the budget perimeter: Scope 1 + Scope 2 (location-based) + waste & water, i.e. the
  daily ``co2e_total_t`` the forecast and the budget use.
* **Path**: a straight line from the base-year emissions to the target; one cap per year. Before the base
  year the cap is the base-year level; after the target year it stays at the target level.
* **Budget** (``budget``):
  - ``cap_source: path`` takes each year's cap from the path; ``manual`` keeps ``annual_cap_tco2e``.
  - ``weights_source: seasonal`` splits a year's cap by month in proportion to the average monthly share of
    the last ``weights_years`` complete years (August shutdown, winter peak…); ``manual`` keeps
    ``monthly_weights``.

Everything here is pure pandas on the daily emissions table, so the pages, the reports, the assistant and the
alerts all get the same caps.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

TARGET_DEFAULTS: dict[str, Any] = {"base_year": 2019, "target_year": 2030, "reduction_pct": 30.0}
BUDGET_DEFAULTS: dict[str, Any] = {"cap_source": "path", "weights_source": "seasonal", "weights_years": 3,
                                   "annual_cap_tco2e": 3000.0}
CAP_SOURCES = ("path", "manual")
WEIGHT_SOURCES = ("seasonal", "manual")


def target_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("targets", {}) or {})
    out = dict(TARGET_DEFAULTS)
    for k in ("base_year", "target_year"):
        if raw.get(k) is not None:
            out[k] = int(raw[k])
    if raw.get("reduction_pct") is not None:
        out["reduction_pct"] = min(100.0, max(0.0, float(raw["reduction_pct"])))
    if out["target_year"] <= out["base_year"]:
        out["target_year"] = out["base_year"] + 1
    return out


def budget_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("budget", {}) or {})
    out = dict(BUDGET_DEFAULTS)
    if raw.get("cap_source") in CAP_SOURCES:
        out["cap_source"] = raw["cap_source"]
    elif raw and "cap_source" not in raw:
        out["cap_source"] = "manual"       # an older config.yaml keeps its fixed cap until a target is set
    if raw.get("weights_source") in WEIGHT_SOURCES:
        out["weights_source"] = raw["weights_source"]
    elif raw and "weights_source" not in raw:
        out["weights_source"] = "manual"
    if raw.get("weights_years") is not None:
        out["weights_years"] = max(1, int(raw["weights_years"]))
    cap = raw.get("annual_cap_tco2e", raw.get("annual_target_tco2e"))
    if cap is not None:
        out["annual_cap_tco2e"] = float(cap)
    out["monthly_weights"] = _manual_weights(raw.get("monthly_weights"))
    return out


def _manual_weights(raw: Any) -> dict[int, float]:
    w = {m: 1.0 / 12.0 for m in range(1, 13)}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                m = int(k)
            except (TypeError, ValueError):
                continue
            if 1 <= m <= 12 and v is not None:
                w[m] = float(v)
    return w


def annual_totals(df: pd.DataFrame, col: str = "co2e_total_t") -> pd.DataFrame:
    """Calendar-year totals with the number of days present and whether the year is complete."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["year", "total", "days", "complete"])
    d = pd.DataFrame({"date": pd.to_datetime(df["date"]), "v": pd.to_numeric(df[col], errors="coerce")})
    g = d.groupby(d["date"].dt.year).agg(total=("v", "sum"), days=("v", "size")).reset_index(names="year")
    g["complete"] = g.apply(lambda r: r["days"] >= (366 if calendar.isleap(int(r["year"])) else 365), axis=1)
    g["year"] = g["year"].astype(int)
    return g


def seasonal_weights(df: pd.DataFrame, years: int = 3, col: str = "co2e_total_t") -> tuple[dict[int, float], list[int]]:
    """Average share of each month in the year's total over the last ``years`` complete years (sums to 1)."""
    tot = annual_totals(df, col)
    full = [int(y) for y in tot.loc[tot["complete"], "year"]][-years:]
    if not full:
        return {m: 1.0 / 12.0 for m in range(1, 13)}, []
    d = pd.DataFrame({"date": pd.to_datetime(df["date"]), "v": pd.to_numeric(df[col], errors="coerce")})
    d = d[d["date"].dt.year.isin(full)]
    m = d.groupby([d["date"].dt.year, d["date"].dt.month])["v"].sum()
    shares = m / m.groupby(level=0).transform("sum")
    avg = shares.groupby(level=1).mean()
    total = float(avg.sum()) or 1.0
    return {int(k): float(v) / total for k, v in avg.items()}, full


@dataclass
class BudgetPlan:
    cap_source: str
    weights_source: str
    weights: dict[int, float]
    weights_years: list[int]
    base_year: int
    target_year: int
    reduction_pct: float
    base_emissions: float | None
    manual_cap: float
    actuals: dict[int, float] = field(default_factory=dict)
    complete_years: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def target_emissions(self) -> float | None:
        return None if self.base_emissions is None else self.base_emissions * (1 - self.reduction_pct / 100)

    @property
    def uses_path(self) -> bool:
        return self.cap_source == "path" and self.base_emissions is not None

    def path_value(self, year: int) -> float | None:
        """Emissions allowed in ``year`` on the straight line from the base year to the target."""
        if self.base_emissions is None:
            return None
        if year <= self.base_year:
            return self.base_emissions
        if year >= self.target_year:
            return self.target_emissions
        frac = (year - self.base_year) / (self.target_year - self.base_year)
        return self.base_emissions * (1 - self.reduction_pct / 100 * frac)

    def cap(self, year: int) -> float:
        """Annual cap of ``year``: from the path, or the fixed cap."""
        if self.uses_path:
            return float(self.path_value(int(year)))
        return float(self.manual_cap)

    def weight(self, month: int) -> float:
        return float(self.weights.get(int(month), 1.0 / 12.0))

    def month_cap(self, year: int, month: int) -> float:
        return self.cap(year) * self.weight(month)

    def path_table(self, until: int | None = None) -> pd.DataFrame:
        """One row per year from the base year to the target year (or ``until``): cap, actual, gap, on track."""
        last = max(self.target_year, until or self.target_year)
        rows = []
        for y in range(self.base_year, last + 1):
            cap = self.path_value(y) if self.base_emissions is not None else self.manual_cap
            actual = self.actuals.get(y) if y in self.complete_years else None
            rows.append({"year": y, "cap": cap, "actual": actual,
                         "gap": (actual - cap) if actual is not None and cap is not None else None,
                         "on_track": (actual <= cap) if actual is not None and cap is not None else None})
        return pd.DataFrame(rows)

    def describe(self, lang: str = "fr") -> str:
        """One sentence for pages, reports and the assistant."""
        fr = lang == "fr"
        if not self.uses_path:
            cap = f"{self.manual_cap:,.0f}".replace(",", " " if fr else ",")
            return (f"Plafond annuel fixe de {cap} tCO₂e (saisi à la main)." if fr
                    else f"Fixed annual cap of {cap} tCO₂e (entered by hand).")
        b = f"{self.base_emissions:,.0f}".replace(",", " " if fr else ",")
        t = f"{self.target_emissions:,.0f}".replace(",", " " if fr else ",")
        if fr:
            return (f"Objectif −{self.reduction_pct:g} % en {self.target_year} par rapport à {self.base_year} "
                    f"({b} → {t} tCO₂e), trajectoire linéaire : le plafond de chaque année est lu sur cette droite.")
        return (f"Target −{self.reduction_pct:g}% by {self.target_year} vs {self.base_year} ({b} → {t} tCO₂e), "
                f"straight-line path: each year's cap is read on that line.")

    def as_dict(self, year: int | None = None) -> dict[str, Any]:
        out = {"cap_source": self.cap_source, "weights_source": self.weights_source,
               "base_year": self.base_year, "target_year": self.target_year, "reduction_pct": self.reduction_pct,
               "base_emissions_t": None if self.base_emissions is None else round(self.base_emissions, 1),
               "target_emissions_t": None if self.target_emissions is None else round(self.target_emissions, 1),
               "monthly_weights": {m: round(w, 4) for m, w in self.weights.items()},
               "weights_years": self.weights_years, "warnings": self.warnings}
        if year is not None:
            out["year"] = int(year)
            out["annual_cap_t"] = round(self.cap(year), 1)
        return out


def plan(cfg: dict[str, Any] | None, df: pd.DataFrame | None) -> BudgetPlan:
    """The budget plan for the current settings and data."""
    t = target_settings(cfg)
    b = budget_settings(cfg)
    tot = annual_totals(df) if df is not None else annual_totals(pd.DataFrame())
    complete = [int(y) for y in tot.loc[tot["complete"], "year"]] if not tot.empty else []
    actuals = {int(r["year"]): float(r["total"]) for _, r in tot.iterrows()} if not tot.empty else {}
    warnings: list[str] = []

    base = actuals.get(t["base_year"]) if t["base_year"] in complete else None
    if b["cap_source"] == "path" and base is None:
        warnings.append(f"base year {t['base_year']} is not a complete year of data: the fixed cap is used")

    if b["weights_source"] == "seasonal" and df is not None and not df.empty:
        weights, used = seasonal_weights(df, b["weights_years"])
        if not used:
            warnings.append("no complete year of data: monthly weights are equal")
    else:
        weights, used = dict(b["monthly_weights"]), []
        total = sum(weights.values()) or 1.0
        if abs(total - 1.0) > 0.02:
            warnings.append(f"manual monthly weights total {total:.3f}, not 1")

    return BudgetPlan(cap_source=b["cap_source"], weights_source=b["weights_source"], weights=weights,
                      weights_years=used, base_year=t["base_year"], target_year=t["target_year"],
                      reduction_pct=t["reduction_pct"], base_emissions=base, manual_cap=b["annual_cap_tco2e"],
                      actuals=actuals, complete_years=complete, warnings=warnings)
