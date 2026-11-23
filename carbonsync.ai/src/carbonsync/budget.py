"""Monthly carbon-budget evaluation shared by the pages, the reports, the assistant and the alerts.

A month is evaluated from observed days (``emissions_daily``) plus the stored
forecast for the days not yet observed. Its ceiling is the year's cap times the
month's weight, both from the budget plan (``carbonsync.targets``): the cap is
read on the path to the reduction target (or a fixed cap), the weights follow the
site's seasonal pattern (or are entered by hand). The status rule is:

* green – the conservative upper bound (sum of daily hi90) stays under the ceiling
* amber – the median projection fits, but the upper bound does not
* red   – the median projection exceeds the ceiling
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import pandas as pd

from carbonsync import targets


@dataclass
class MonthBudget:
    year: int
    month: int
    ceiling: float
    weight: float
    spent: float
    days_observed: int
    days_in_month: int
    projected: float
    upper: float
    status: str  # "green" | "amber" | "red"
    days_covered: int = 0  # days with an actual or a forecast value
    cum_actual: list[float | None] = field(default_factory=list)
    cum_projected: list[float | None] = field(default_factory=list)
    cum_upper: list[float | None] = field(default_factory=list)

    @property
    def gap(self) -> float:
        """Projected month-end minus ceiling (negative = headroom)."""
        return self.projected - self.ceiling


def plan(cfg: dict, df_daily: pd.DataFrame | None) -> targets.BudgetPlan:
    return targets.plan(cfg, df_daily)


def annual_cap(cfg: dict, df_daily: pd.DataFrame | None = None, year: int | None = None) -> float:
    """Cap of ``year`` (default: the year being managed). Without data, the fixed cap of the settings."""
    if df_daily is None or df_daily.empty:
        return float(targets.budget_settings(cfg)["annual_cap_tco2e"])
    if year is None:
        year = current_period(df_daily)[0]
    return targets.plan(cfg, df_daily).cap(int(year))


def month_weight(cfg: dict, month: int, df_daily: pd.DataFrame | None = None) -> float:
    if df_daily is None or df_daily.empty:
        return float(targets.budget_settings(cfg)["monthly_weights"].get(int(month), 1.0 / 12.0))
    return targets.plan(cfg, df_daily).weight(month)


def current_period(df_daily: pd.DataFrame) -> tuple[int, int]:
    """The month being managed: the last observed month if incomplete, else the next one."""
    last = df_daily["date"].max()
    if last.day == calendar.monthrange(last.year, last.month)[1]:
        nxt = last + pd.Timedelta(days=1)
        return nxt.year, nxt.month
    return last.year, last.month


def evaluate_month(
    df_daily: pd.DataFrame, df_fc: pd.DataFrame, cfg: dict, year: int, month: int,
    budget_plan: targets.BudgetPlan | None = None,
) -> MonthBudget:
    bp = budget_plan or targets.plan(cfg, df_daily)
    weight = bp.weight(month)
    ceiling = bp.cap(year) * weight
    n_days = calendar.monthrange(year, month)[1]

    obs = df_daily[(df_daily["date"].dt.year == year) & (df_daily["date"].dt.month == month)]
    obs_by_day = dict(zip(obs["date"].dt.day, obs["co2e_total_t"].astype(float)))

    fc_by_day: dict[int, tuple[float, float]] = {}
    if not df_fc.empty:
        fc = df_fc[(df_fc["date"].dt.year == year) & (df_fc["date"].dt.month == month)]
        fc_by_day = {
            int(d): (float(y), float(h))
            for d, y, h in zip(fc["date"].dt.day, fc["yhat"], fc["hi90"])
        }

    # Run-rate fallback for days with neither actuals nor forecast
    rate = (sum(obs_by_day.values()) / len(obs_by_day)) if obs_by_day else 0.0
    if not obs_by_day and fc_by_day:
        rate = sum(v[0] for v in fc_by_day.values()) / len(fc_by_day)

    cum_a: list[float | None] = []
    cum_p: list[float | None] = []
    cum_u: list[float | None] = []
    spent = proj = up = 0.0
    last_obs_day = max(obs_by_day) if obs_by_day else 0

    for d in range(1, n_days + 1):
        if d in obs_by_day:
            spent += obs_by_day[d]
            proj += obs_by_day[d]
            up += obs_by_day[d]
            cum_a.append(spent)
        else:
            y, h = fc_by_day.get(d, (rate, rate * 1.15))
            proj += y
            up += h
            cum_a.append(None)
        # projected / upper lines start where the actuals stop
        if d >= last_obs_day:
            cum_p.append(proj)
            cum_u.append(up)
        else:
            cum_p.append(None)
            cum_u.append(None)

    if up <= ceiling:
        status = "green"
    elif proj <= ceiling:
        status = "amber"
    else:
        status = "red"

    covered = len(set(obs_by_day) | set(fc_by_day))
    return MonthBudget(
        days_covered=covered,
        year=year,
        month=month,
        ceiling=ceiling,
        weight=weight,
        spent=spent,
        days_observed=len(obs_by_day),
        days_in_month=n_days,
        projected=proj,
        upper=up,
        status=status,
        cum_actual=cum_a,
        cum_projected=cum_p,
        cum_upper=cum_u,
    )


def year_summary(df_daily: pd.DataFrame, df_fc: pd.DataFrame, cfg: dict, year: int) -> pd.DataFrame:
    """Per-month actual / forecast / ceiling for a calendar year."""
    rows = []
    bp = targets.plan(cfg, df_daily)
    for m in range(1, 13):
        mb = evaluate_month(df_daily, df_fc, cfg, year, m, bp)
        rows.append(
            {
                "month": m,
                "actual": mb.spent,
                "forecast": mb.projected - mb.spent,
                "ceiling": mb.ceiling,
                "status": mb.status if mb.days_covered else "nodata",
                "covered": mb.days_covered > 0,
            }
        )
    return pd.DataFrame(rows)
