# CarbonSync AI — Full Project Report

**Carbon monitoring, forecasting and reduction planning for an industrial site**

| | |
|---|---|
| **Date** | 21 November 2026 |
| **Version** | Dashboard v2.1, after session 10 (seven critical changes) and its review fixes |
| **Code state** | Public snapshot of the dashboard as of 21 November 2026 (the development history is not published) |
| **Tests** | 334 automated tests pass; ruff (lint) clean |
| **Data** | Synthetic: 8 generated years (2018–2025) plus Q1 2026 uploaded from the dashboard; data through 2026-03-31 |
| **Site modelled** | A Tunisian wiring-harness plant (cutting, preparation, assembly), Tunisian grid electricity |

This report describes the whole product: what it does, the latest changes and why they were made, the figures it
currently shows, the data pipeline, the forecasting models, the carbon target and budget, the greenhouse-gas
inventory, the alerts and the action plan they call for, the login and roles, every page, the AI assistant, the
settings, where everything is stored, how to run it and how it is tested.

---

## Contents

1. [Summary](#1-summary)
2. [Where things stand](#2-where-things-stand)
3. [Latest changes (21 November 2026) and why](#3-latest-changes-21-november-2026-and-why)
4. [How it all fits together](#4-how-it-all-fits-together)
5. [Tools and technologies](#5-tools-and-technologies)
6. [The data pipeline](#6-the-data-pipeline)
7. [Forecasting models and their scores](#7-forecasting-models-and-their-scores)
8. [Reduction target and carbon budget](#8-reduction-target-and-carbon-budget)
9. [Greenhouse-gas inventory and breakdown by zone](#9-greenhouse-gas-inventory-and-breakdown-by-zone)
10. [Alerts and the action plan](#10-alerts-and-the-action-plan)
11. [Login, roles and the team](#11-login-roles-and-the-team)
12. [The dashboard, page by page](#12-the-dashboard-page-by-page)
13. [How the pages work together](#13-how-the-pages-work-together)
14. [The AI assistant](#14-the-ai-assistant)
15. [Settings and look](#15-settings-and-look)
16. [Where data, settings and keys are kept](#16-where-data-settings-and-keys-are-kept)
17. [Folder layout](#17-folder-layout)
18. [Running it](#18-running-it)
19. [Tests and checks](#19-tests-and-checks)
20. [Project timeline](#20-project-timeline)
21. [Known limits and next steps](#21-known-limits-and-next-steps)

---

## 1. Summary

CarbonSync AI is a local web dashboard (Streamlit) for the CO₂ emissions of an industrial site. It:

- takes the monthly CSV exports of four departments (energy, production, logistics, waste & water), repairs what can
  be repaired and quarantines the rest with the reason and a suggested fix;
- turns them into one daily table of emissions by scope (Scope 1, Scope 2, other) in a DuckDB database;
- forecasts the next **183 days** with a 90 % interval (Chronos-2 by default; Prophet and their average Ens-PC on
  demand) and re-runs the model backtest after each upload;
- compares actuals and forecast with a **reduction target** (−30 % by 2030 vs 2019): each year's cap is read on the
  straight-line path (2026: 2,749 tCO₂e) and split by month with the site's seasonal profile;
- keeps a **greenhouse-gas inventory**: Scope 2 location-based and market-based (contracts, residual mix), and a
  Scope 3 screening of the 15 categories (calculated / estimated / excluded with the reason);
- splits the emissions by **plant zone** (P1 cutting, P2 preparation, P3 assembly, utilities and buildings,
  logistics) and by item, each zone with an owner;
- raises **alerts** (budget, year-end, days outside the 90 % band, missing data, model drift, quarantine) through a
  floating alert button and an Alerts page, with critical alerts on a dark red background;
- has a **login with three roles** (viewer, editor, administrator), a team panel showing who is online and the latest
  data files received, and a password tab for every user;
- simulates decarbonisation levers (on-site solar, energy efficiency, production volume);
- produces a 13-sheet Excel audit workbook (CSRD / ESRS E1) and a 3-page PDF brief;
- has an **AI assistant** that answers questions about the data and the dashboard. Every figure it quotes comes from
  16 read-only tools; it runs on a local model on the graphics card, on cloud models with the user's own API keys,
  or with no model at all.

Everything runs on this PC. In local mode nothing leaves it; cloud models only receive monthly (or coarser) totals.

## 2. Where things stand

Figures read from the dashboard's own tools on 2026-11-21, after the pipeline run of session 10 (synthetic data).

| | |
|---|---|
| **History** | 2018-01-01 → 2026-03-31, 3,012 days; 681 days imputed (22.6 %) |
| **Last 12 months** (Apr 2025 – Mar 2026) | **3,170 tCO₂e**, +2.0 % vs the 12 months before; Scope 2 63.9 %, Scope 1 33.1 %, other 3.0 % |
| **Intensity** | 8.81 kg CO₂e per unit produced (9.42 the year before) |
| **Target** | −30 % by 2030 vs 2019 (3,397 → 2,378 tCO₂e); 2025 was 3,155 t against a path cap of 2,841 t (**313 t above the path**) |
| **2026 projection** | **3,230 t** (90 % range 2,745–3,491 t) vs a cap of **2,749 t**: **481 t over** |
| **Inventory, 12 months** | Scope 1 1,048 t · Scope 2 2,025 t (location-based = market-based: no contract yet) · other 96 t · Scope 3 ≈ 2,111 t (screening, outside the budget) |
| **Largest zone** | Utilities and buildings 1,239 t (39 %), then Logistics 680 t, P3 553 t, P1 396 t, P2 301 t |
| **Open alerts** | **2 critical** (April budget, year-end over the cap), 2 warnings (3 unusual days in January, no data since 2026-03-31), 1 info (12 quarantined files) |
| **Active forecast** | Chronos-2, 183 days (2026-04-01 → 2026-09-30), 1,618 t (90 % range 1,292–1,794 t) |
| **Stored** | 9 forecast runs, 177 backtest evaluations (12 origins × 3 history windows × 5 models) |
| **Dashboard** | 18 pages in 9 groups, French / English, login with 3 roles |
| **Assistant** | 16 read-only tools; 5 engines (built-in, local Ollama, Ollama Cloud, OpenRouter, Claude) |
| **Next data** | April–October 2026 not uploaded yet; `samples/2026-Q2.zip` (April–June) is ready |

## 3. Latest changes (21 November 2026) and why

Session 10 delivered seven critical changes, first as a reversible test on a separate branch. After a review the user
asked for six fixes, then accepted the result: it was merged into the main code.

### 3.1 The seven critical changes

| # | Change | What was built | Why |
|---|---|---|---|
| 1 | **Chronos-2 by default, backtest after each upload** | A forecast router (`models/router.py`): Chronos-2 from 60 days of history, otherwise Prophet, and Prophet whenever Chronos-2 cannot run. A new pipeline step recomputes the backtest when new days arrive (12 origins). A router-drift alert | On today's data Chronos-2 is as accurate as Ens-PC (MASE 0.889 vs 0.885) and is the only model whose 90 % interval holds 90 % of real days; it needs one model instead of two and no training. The old backtest was computed on data that has since changed |
| 2 | **Market-based Scope 2** | `inventory/scope2.py`: contracts (PPA, green tariff, certificates) with dates and factors, the rest at the residual mix. Both figures on Overview, GHG inventory, Excel, PDF and the assistant | ESRS E1-6 and the GHG Protocol Scope 2 Guidance require both methods; a green-electricity contract only shows in the market-based figure |
| 3 | **Scope 3 screening** | `inventory/scope3.py`: the 15 categories, each calculated, estimated (sourced assumptions) or excluded with a reason | ESRS E1-6 asks for the significant Scope 3 categories; the screening shows Scope 3 is about two-thirds of Scopes 1 + 2 and where to measure first |
| 4 | **Target −30 % by 2030, caps from the path** | `targets.py`: base year 2019, straight-line path, each year's cap read on it; monthly ceilings from the seasonal profile | A fixed 3,000 t cap was not tied to any commitment; flat monthly weights made the August shutdown look good and winter months red for seasonal reasons only |
| 5 | **Alerts as a pipeline step** | `alerts.py` (6 rules), pipeline step 6, alert button, Alerts page, Excel sheet, PDF, assistant tool | Problems were only visible to someone who opened the right page; the checks now run after every upload |
| 6 | **Breakdown by zone and item** | `inventory/breakdown.py`: allocation keys per meter in `config.yaml`, a Breakdown page, a `get_breakdown` tool | A reduction lever needs an owner; site meters alone cannot say which zone or machine emits |
| 7 | **Login with roles** | `auth.py`: accounts, hashed passwords, sessions, lockout, viewer / editor / admin, a Users tab and terminal commands | Targets, contracts and keys are now editable; readers must not change them by mistake, and API keys are for administrators only |

### 3.2 Fixes after the user's review

| Asked | Done |
|---|---|
| On Breakdown, "Largest zone" and "Largest item" text (for example "Assembly lines and test benches") went past the box | Key-figure tiles whose value is a name get a smaller font and wrap on up to two lines inside the tile; every value is clipped with an ellipsis rather than overflowing, on every page |
| An alert button like the floating AI button, above it, red when an alert is critical | A round floating button above the AI button: red with a pulse when a critical alert is open, amber for warnings, neutral otherwise, with the count. A click lists the open alerts and offers **See the critical alerts** and **Open the Alerts page**. The bell in the page header was removed |
| Critical alerts on a dark red background | On the Alerts page, critical alerts are on dark red with white text, warnings on amber; the "Critical" tile turns dark red; a filter shows All / Critical / Warnings / Info |
| The user icon should show the team online / offline and the latest data files, not the password form | The account panel shows the team with who is online, the 5 latest data files (with who sent them) and the last pipeline run, then Log out |
| Password change in its own Settings tab | Configuration → **Password**, for every signed-in user, viewers included; a change closes the user's other sessions |
| A better sign-in screen; no menu bar when nobody is signed in | A new sign-in and first-start screen (brand panel, form card, language switch); the menu bar, sidebar and toolbar are hidden until someone signs in |

### 3.3 Problems found and fixed while testing

| Problem | Fix |
|---|---|
| The backtest refresh dropped the code version it was computed with | The commit is recorded again (with `-dirty` when the code had uncommitted changes); recomputed after the commit |
| A report conclusion said "Ens-PC is the most accurate" while the router uses Chronos-2, 0.004 apart | The conclusion now says they are level and why Chronos-2 is used; a clear gap would say "review the router" |
| A 3-origin backtest ranked two close models wrongly (0.85 vs 0.92) | 12 origins (gap 0.004) |
| French alert texts showed "3.4 t" | French decimals ("3,4 t") |
| A zone label repeated its code ("P3 · P3 Assemblage") | The code is not repeated when the name already starts with it |
| The login crashed in tests when the cookie was not text | Type checks on the session token |

## 4. How it all fits together

```
 Department CSVs (energy, production, logistics, waste & water)
   |  Data intake page (files, a folder or a .zip), or data/inbox/
   v
 PIPELINE  src/carbonsync/pipeline.py   (after each upload, or by hand)
   1 validate & repair .... ingest/validate.py, ingest/schemas.py
   2 load ................. ingest/load.py
   3 daily table .......... features/daily.py  (activity x emission factor)
   4 forecast (router) .... models/router.py, models/ensemble.py
   5 backtest refresh ..... backtest/rolling.py
   6 alerts ............... alerts.py
   7 reports .............. export/insights.py, report.py, brief.py
   |
   +--> data/quarantine/         rejected files + reason + suggested fix
   +--> data/carbonsync.duckdb   raw_*, daily, emissions_daily, forecast_runs,
   |                             forecasts, backtest_results
   +--> data/alerts.json         open alerts, first-seen dates, history
   +--> data/reports/            Excel workbook (13 sheets), PDF brief (3 pages)

 READERS (same data, same figures)
   Login gate ........... app/core/auth.py, in front of every page
   Dashboard pages ...... app/data/loader.py (cached), 18 pages in 9 groups
   Reports .............. export/insights.py: one set of figures for page, Excel, PDF
   AI assistant ......... assistant/context.py snapshot -> 16 read-only tools -> engine

 config.yaml is read by every step: emission factors, target, budget, router,
 Scope 2 / 3, breakdown keys, alerts, login, appearance, layout.
```

## 5. Tools and technologies

### 5.1 In the product

| Area | Tool | Version | Used for |
|---|---|---|---|
| Language | Python | 3.12.7 | Everything |
| Web app | Streamlit | 1.67.0 | Pages, top navigation, fragments (floating panels, presence beat), live theme reload |
| Charts | Plotly | 7.1.0 | Every interactive chart |
| Data | pandas, NumPy | 3.0.6 | Tables, aggregation, the Analyse engine |
| Database | DuckDB | 2.0.0 | One-file analytical database (`data/carbonsync.duckdb`) |
| Validation | pandera | 0.35.0 | One schema per department CSV |
| Forecasting | Chronos-2 (`chronos-forecasting`, PyTorch) | 2.3.2, torch 2.15.0 | Amazon's time-series foundation model (`amazon/chronos-2`), zero-shot, on CPU |
| Forecasting | Prophet | 1.5.0 | Trend + weekly / yearly seasonality + regressors |
| Forecasting | scikit-learn | — | Ridge regression inside the Prophet wrapper |
| Excel | openpyxl | 3.1.5 | 13-sheet audit workbook with native Excel charts |
| PDF | matplotlib | 3.11.2 | 3-page executive brief, conversation export |
| Login | `hashlib`, `secrets` (standard library) | — | PBKDF2-SHA256 password hashes (600,000 rounds), session tokens |
| Config | PyYAML | — | `config.yaml` |
| Local AI | Ollama + `ollama` client | 0.40.16 / 0.6.3 | Local models on the GPU; also the Ollama Cloud protocol |
| Local models | Qwen 3.5 9B, Qwen 3.5 4B, Qwen 3 1.7B | — | The assistant's local engines ([§ 14.6](#146-the-local-models-where-they-came-from-and-where-they-are)) |
| Cloud AI | `anthropic` | 2.11.0 | Claude (Expert mode) |
| Cloud AI | `openai` | 3.57.0 | OpenRouter, through its OpenAI-compatible API |
| Secrets | `keyring` | 25.7.0 | API keys in the Windows Credential Manager |
| Quality | pytest, ruff | 9.2.0, 0.17.4 | 334 tests, lint |

`xgboost` and `mapie` are still listed in `requirements.txt` but no code uses them. The requirements use `>=`
bounds, not pinned versions.

### 5.2 To build and check it

- **git**: 33 commits over 10 work sessions (the development history is not published; this repository starts from
  a clean snapshot).
- **Claude Code** wrote the code with the user, one plan and one progress note per session.
- **Streamlit AppTest** runs every page, and the whole app with the login on and off, in tests and render scripts
  (no browser on this PC; visual checks are the user's).
- **WCAG contrast maths** (in `app/ui/appearance.py` and its tests) and a colour-blindness palette validator for the
  chart colours.
- **winget** installed Ollama.

Hardware for the local models: NVIDIA RTX 4060 Laptop GPU, 8 GB.

## 6. The data pipeline

### 6.1 Where the history comes from (one-off)

The 2018–2025 history is synthetic, generated once:

- `synth/generator.py` writes 8 years of daily data for the 4 departments (seed 42). It includes events the models
  and the assistant can find: on-site solar from 2021-06-01 (−15 % grid electricity), an efficiency programme from
  2023-03-01 (−8 %), a 14-day August shutdown, about 6 random breakdowns a year, +3 % growth a year, weekends at
  35 % activity, seasonal cooling load, noise, 1.5 % missing values and 0.3 % outliers.
- `synth/messify.py` then damages the CSVs the way real exports are damaged: French Excel format (`;` delimiter,
  decimal comma), day-first dates, Latin-1 or UTF-8 BOM encodings, duplicates, unit slips. 10 % of the files are
  messy and 2 % unusable.
- `data/truth/` keeps the clean signal, used by the backtest's "oracle" floor.

Q1 2026 was then uploaded by the user through the Data intake page, like real data.

### 6.2 The seven steps (each upload, or `python -m carbonsync.pipeline`)

| # | Step | Module | What it does | Output |
|---|---|---|---|---|
| 1 | **Validate & repair** | `ingest/validate.py`, `ingest/schemas.py` | Detects encoding, delimiter and decimal separator; strips blank rows and comment headers; maps column aliases (`Electricité (kWh)` → `electricity_kwh`); reads ISO and dd/mm/yyyy dates; removes duplicate dates; converts MWh slips to kWh; turns negative values into gaps; rejects files with missing required columns or more than 10 % missing values; checks the pandera schema | `data/processed/<department>/*.parquet`, or `data/quarantine/<file>` + `.errors.json` (reason and suggested fix) |
| 2 | **Load** | `ingest/load.py` | Loads the Parquet files into DuckDB with audit columns | `raw_energy`, `raw_production`, `raw_logistics`, `raw_waste_water` |
| 3 | **Daily table + emissions** | `features/daily.py` | One row per day with calendar features. Gaps of up to 3 days are interpolated and flagged `is_imputed`; longer gaps stay empty. A month enters only once **all four departments** have sent it. Emissions = activity × factor | `daily`, `emissions_daily` |
| 4 | **Forecast** | `models/router.py`, `models/ensemble.py` | Router: Chronos-2 from 60 days of history, otherwise Prophet (Prophet also if Chronos-2 fails). 183 days, 90 % interval. The new run becomes the active forecast | `forecast_runs`, `forecasts` |
| 5 | **Backtest refresh** | `backtest/rolling.py` | When new days arrived: 12 rolling origins × 6 / 12 / 24-month histories, 183-day horizon (16–18 s). Skipped when the backtest already covers the last day | `backtest_results`, `data/backtest_summary.json` |
| 6 | **Alerts** | `alerts.py` | Budget, year-end, unusual days, late departments, router drift, quarantine; keeps first-seen dates and the history | `data/alerts.json` |
| 7 | **Reports** | `export/insights.py`, `report.py`, `brief.py` | 13-sheet Excel workbook and 3-page PDF brief, from the same figures | `data/reports/` |

On the real data a run with new days takes about 30 s: the Chronos-2 forecast about 9 s (mostly loading its
weights; the forecast itself takes 0.3 s) and the 12-origin backtest 16–18 s. A run with no new days takes about 7 s.

Emission factors (`config.yaml`, reviewed 2026-01-01):

| Scope | Activity | Factor |
|---|---|---|
| Scope 1 (direct combustion) | Heavy fuel oil, logistics diesel | 2.68 kgCO₂e/L |
| Scope 2 (purchased electricity) | Tunisian grid, location-based | 0.483 kgCO₂e/kWh |
| Other | Solid waste / process water | 0.5 kgCO₂e/kg / 0.3 kgCO₂e/m³ |

Every run is logged: in the terminal (`obs.py`, timed steps), in `data/pipeline_history.jsonl` (what triggered it,
steps, new days, forecast made) and, for uploads, in `data/uploads_manifest.json` (file, department, rows, period,
time and, since the review fixes, **who uploaded it**). When an upload replaces an inbox file, the original is kept in
`data/uploads_backup/`, so **Back to the original data** can restore the dataset exactly.

### 6.3 Evidence steps (run by hand)

- `python -m carbonsync.backtest.rolling`: the rolling-origin backtest (the same as pipeline step 5), against a
  seasonal-naive baseline and the oracle floor. Results go to the `backtest_results` table and
  `data/backtest_summary.json`, with the code commit that computed them.
- `models/conformal.py`: split-conformal 90 % band from the backtest residuals, smoothed by horizon bucket
  (days 1–30, 31–90, 91–183).

## 7. Forecasting models and their scores

- **Chronos-2**: Amazon's time-series foundation model, used zero-shot (no training on this site). **Default.**
- **Prophet**: trend + weekly and yearly seasonality + regressors (weekend, shutdowns…). The fallback.
- **Ens-PC**: the average of Prophet and Chronos-2; available on demand.
- **Seasonal naive**: repeats last week; the yardstick.
- **Router** (production rule, `models/router.py`, `config.yaml → router`): Chronos-2 from 60 days of history,
  otherwise Prophet; Prophet also if Chronos-2 fails. An old configuration with only `min_days_for_ensemble` keeps
  its former rule (Prophet below 365 days, Ens-PC above). The rule is set in Configuration → System.

Backtest recomputed on the current data (`data/backtest_summary.json`: data through 2026-03-31, 12 origins from
2019-12-22 to 2025-09-21, history windows of 6 / 12 / 24 months, 183-day horizon, 177 evaluations):

| Model | MAE (t/day) | MASE (6 / 12 / 24 months) | MASE mean | 90 % coverage |
|---|---|---|---|---|
| Ens-PC | 1.058 | 1.004 / 0.907 / 0.745 | **0.885** | 93.9 % (too wide) |
| **Chronos-2** | **1.056** | 0.998 / 0.926 / 0.743 | **0.889** | **89.8 %** (on target) |
| Prophet | 1.274 | 1.180 / 1.065 / 0.910 | 1.052 | 92.0 % |
| Seasonal naive | 1.422 | 1.178 / 1.192 / 1.166 | 1.179 | 89.2 % |
| Oracle (floor, knows the true signal) | 0.163 | — | 0.137 | 95.6 % |

How to read it: MASE below 1 beats the naive baseline. Chronos-2 and Ens-PC are level on accuracy (0.004 apart,
inside the router alert's 0.05 margin); Chronos-2 is the only one whose 90 % interval holds 90 % of real days (a
wider interval makes the budget's amber status appear too often), and it needs one model instead of two, so it is
the default. Prophet alone is clearly behind. Earlier figures in this report (Chronos-2 MASE 0.62–0.67, Prophet
1.28–1.54) came from a backtest of 2026-11-06 on data that was regenerated on 2026-11-19; they no longer apply. The
backtest is now recomputed after each upload, and an alert is raised if the router's model falls more than 0.05 MASE
behind the best one.

## 8. Reduction target and carbon budget

### 8.1 The target and the path

The target applies to the **budget perimeter**: Scope 1 + Scope 2 location-based + waste and water (the daily total
that the forecast and the budget use). It is set in `config.yaml → targets` and Configuration → Target & budget.

- **Base year 2019**: 3,397.4 tCO₂e (a complete calendar year of data).
- **Target 2030**: −30 %, i.e. 2,378.2 tCO₂e.
- **Path**: a straight line from the base year to the target; each year's cap is read on it (about 92.7 t lower each
  year). Before the base year the cap is the base level; after 2030 it stays at the target level.

| Year | Path cap (t) | Actual (t) | Gap to the path (t) |
|---|---|---|---|
| 2019 | 3,397.4 | 3,397.4 | 0 (base year) |
| 2020 | 3,304.8 | 3,505.9 | +201.2 |
| 2021 | 3,212.1 | 3,344.6 | +132.5 |
| 2022 | 3,119.4 | 3,192.9 | +73.5 |
| 2023 | 3,026.8 | 3,026.7 | −0.1 (on the path) |
| 2024 | 2,934.1 | 3,095.8 | +161.7 |
| 2025 | 2,841.5 | 3,154.6 | **+313.2** |
| 2026 | 2,748.8 | 826.2 (Jan–Mar); projected 3,230 | **+481 projected** |
| 2027 | 2,656.2 | — | — |
| 2028 | 2,563.5 | — | — |
| 2029 | 2,470.8 | — | — |
| 2030 | 2,378.2 | — | — |

2023 was on the path (the efficiency programme of March 2023); since then emissions have risen again while the path
keeps going down.

### 8.2 Monthly ceilings

Each year's cap is split by month in proportion to the **seasonal profile**: the mean share of each month in the last
3 complete years (2023–2025). This removes the seasonal bias of flat weights (the August shutdown, the summer cooling
load), so a red month means "more than usual for this month".

| Month | Weight | 2026 ceiling (t) | 2026 actual / forecast / estimate (t) | Status |
|---|---|---|---|---|
| January | 8.79 % | 242 | 262 actual | Over |
| February | 8.23 % | 226 | 272 actual | Over |
| March | 8.90 % | 245 | 293 actual | Over |
| April | 8.72 % | 240 | 281 forecast | Over |
| May | 9.19 % | 253 | 285 forecast | Over |
| June | 8.58 % | 236 | 287 forecast | Over |
| July | 8.68 % | 239 | 295 forecast | Over |
| August | 6.18 % | 170 | 199 forecast | Over |
| September | 8.22 % | 226 | 270 forecast | Over |
| October | 8.39 % | 231 | 281 estimate | Over |
| November | 8.42 % | 231 | 264 estimate | Over |
| December | 7.70 % | 212 | 241 estimate | Over |

October–December lie beyond the 183-day forecast: they are estimated as the same days of 2025 × the recent trend
(1.019, clipped to 0.8–1.2). Year total: 826 t actual + 1,618 t forecast + 785 t estimated = 3,230 t.

A fixed annual cap (`cap_source: manual`) and hand-set monthly weights (`weights_source: manual`) remain available.

### 8.3 How a month's status is decided

- **Green**: the 90 % upper bound of the month's projection stays under the ceiling.
- **Amber**: only the central projection stays under the ceiling.
- **Red**: the central projection is over the ceiling.

The month's projection is actual days + forecast days (+ a seasonal estimate beyond the forecast horizon for the
year view). The evaluation lives in `src/carbonsync/budget.py` and is shared by the Budget page, Overview, the alerts,
the reports and the assistant, so they always agree.

## 9. Greenhouse-gas inventory and breakdown by zone

### 9.1 Scope 2 by both methods

- **Location-based**: every kWh bought from the grid × the grid average (0.483 kgCO₂e/kWh). This is the figure in the
  budget.
- **Market-based**: the kWh covered by contractual instruments (PPA with certificates, green tariff, energy attribute
  certificates) × the instrument's factor, plus the rest × the **residual mix**.
- Tunisia publishes no residual mix, so the grid average is used as the proxy (the GHG Protocol's fallback), and the
  source text says so.
- No contract is recorded yet (an example green tariff, 30 % from 2027, is in `config.yaml`, disabled), so both
  methods give **2,025 t** over the last 12 months.
- The on-site solar panels (2021) are in neither figure: their electricity never goes through the grid meter, so the
  meter's kWh already fall by the solar share.

### 9.2 Scope 3 screening

The 15 GHG Protocol categories, each with a status: **calculated** from metered site data, **estimated** from drivers
the dashboard already has (units produced, litres, kWh, working days) times sourced assumptions in
`config.yaml → scope3`, or **excluded** with the reason. Scope 3 stays **outside the budget perimeter**, so a changed
assumption cannot move the budget.

| Category | Item and assumption | Status | tCO₂e (12 months) |
|---|---|---|---|
| 1. Purchased goods | Water (supply and treatment) | Calculated | 7.0 |
| 1. Purchased goods | Copper (wire, terminals): 0.5 kg per harness × 4.0 kgCO₂e/kg | Estimated | 719.4 |
| 1. Purchased goods | PVC (insulation, sleeves): 0.3 kg per harness × 2.08 kgCO₂e/kg | Estimated | 224.5 |
| 3. Fuel and energy | Upstream of fuels (well-to-tank, 0.61 kgCO₂e/L) | Estimated | 238.6 |
| 3. Fuel and energy | Grid losses and upstream of electricity (0.07 kgCO₂e/kWh) | Estimated | 293.5 |
| 4. Upstream transport | Purchased materials, about 3,000 km by sea then road | Estimated | 25.9 |
| 5. Waste | All waste streams (already in the budget) | Calculated | 89.2 |
| 7. Employee commuting | Staff commuting by bus, car and other modes | Estimated | 483.5 |
| 9. Downstream transport | Harnesses delivered to customers | Estimated | 29.1 |
| 2, 6, 8, 10–15 | Excluded with the reason (for example 8: leased buildings and equipment are already in Scopes 1 and 2) | Excluded | — |
| **Total** | 96 t calculated, 2,014 t estimated | | **≈ 2,111** |

The largest categories to measure first: category 1 (copper and PVC, 944 t) and commuting (484 t).

### 9.3 Breakdown by zone and item

The departments send one meter per energy carrier (all the site's kWh, all its fuel oil…). Until sub-meters exist,
**allocation keys** (`config.yaml → breakdown`) split each meter between items, and each item belongs to a zone with
an owner. Each meter's keys must total 100 %, so the zones always add up to the site total.

| Zone | Owner | tCO₂e (12 months) | Share |
|---|---|---|---|
| Utilities and buildings | Maintenance | 1,239.0 | 39.1 % |
| Logistics | Logistics manager | 680.1 | 21.5 % |
| P3 Assembly | P3 manager | 553.4 | 17.5 % |
| P1 Cutting | P1 manager | 395.8 | 12.5 % |
| P2 Preparation | P2 manager | 301.4 | 9.5 % |

The ten largest items:

| Item | Zone | Source | tCO₂e | Share |
|---|---|---|---|---|
| Assembly lines and test benches | P3 | Electricity | 526.6 | 16.6 % |
| Delivery trucks | Logistics | Diesel | 449.3 | 14.2 % |
| Air conditioning and ventilation | Utilities | Electricity | 364.6 | 11.5 % |
| Cutting machines | P1 | Electricity | 364.6 | 11.5 % |
| Boiler (heating, hot water) | Utilities | Heavy fuel oil | 291.9 | 9.2 % |
| Crimping and preparation | P2 | Electricity | 283.6 | 8.9 % |
| Compressed air | Utilities | Electricity | 243.0 | 7.7 % |
| Lighting and offices | Utilities | Electricity | 162.0 | 5.1 % |
| Generators | Utilities | Heavy fuel oil | 157.2 | 5.0 % |
| Forklifts and site shuttles | Logistics | Diesel | 149.8 | 4.7 % |

The keys are estimates; the page, the PDF and the assistant say so.

## 10. Alerts and the action plan

### 10.1 The rules

`src/carbonsync/alerts.py` runs as pipeline step 6 after every upload, and on demand with **Check now** (editors).

| Rule | Severity | Trigger |
|---|---|---|
| Month budget | warning (amber) / critical (red) | The managed month's projection, or its 90 % upper bound, is over the monthly ceiling |
| Year-end | critical | Actual + forecast + seasonal estimate for the year is over the year's cap on the path |
| Unusual days | warning | Days of the last 90 outside the 90 % band of a forecast made **before** them (no look-ahead) |
| Late department / stale data | warning | A department behind the others, or no new day for 45 days |
| Router drift | info | Another model beats the router's model by more than 0.05 MASE in the latest backtest |
| Quarantine | info | Files waiting to be fixed |

Each alert keeps the date it was first seen; alerts that disappear go to a history with the date they were resolved.
Thresholds are in `config.yaml → alerts` and Configuration → Reports & alerts. Email is not configured (no mail
server); the alert button and the Alerts page are the channels.

### 10.2 Where alerts are shown

- **Alert button** (`app/ui/alert_fab.py`): a round 50 px button at the bottom right, stacked above the AI button. It
  is **red with a pulse** when a critical alert is open, **amber** for warnings only, neutral otherwise, with the count
  of critical + warning alerts. A click opens a panel listing up to 6 open alerts (critical first, on dark red;
  warnings on amber) with **See the critical alerts** (opens the Alerts page filtered on critical) and **Open the
  Alerts page**. It is hidden on the Alerts page itself and while the AI panel is open.
- **Alerts page**: three tiles (Critical, Warnings, Info; the Critical tile turns dark red when it is not zero),
  **Check now**, a filter (All / Critical / Warnings / Info), the alert cards (critical on dark red with white text,
  warnings on amber, info neutral) with a link to the page that explains each one, the chart of the forecast band
  with the days outside it, the resolved alerts and the thresholds.
- Also: Overview's second row of tiles, the Excel **Alerts** sheet, PDF page 3 and the assistant's `get_alerts` tool.

### 10.3 The current alerts

| Severity | Alert | Detail |
|---|---|---|
| Critical | Budget April 2026: projection over the ceiling | 281 t forecast against a 240 t ceiling (+17 %) |
| Critical | Year-end 2026 over the cap | 3,230 t projected against 2,749 t (+481 t); even the low end of the 90 % range (2,745 t) only just meets the cap |
| Warning | 3 days outside the 90 % forecast band | 2026-01-02 to 2026-01-20, all below; the largest on 2026-01-20 (3.4 t vs 11.0 t forecast) |
| Warning | No new data for a long time | Last day received 2026-03-31 (235 days ago); monthly exports are expected (threshold 45 days) |
| Info | 12 files in quarantine | To fix and upload again (Data quality page) |

**What the two critical alerts mean.** Both are forecasts: the data stops on 31 March 2026, and April to October have
not been uploaded yet. The cause is not an unusual month: the site still emits at its 2025 level (3,170 t over the
last 12 months, +2 %), while the cap now falls every year towards −30 % in 2030. 2025 was already 313 t above the path.

### 10.4 Action plan

**Step 1 — bring the data up to date.** Upload the April–October exports (`samples/2026-Q2.zip` covers April–June for
testing). The forecast for those months is replaced by actual figures, the backtest and alerts are recomputed, and
the stale-data warning clears. April is a closed month: its real figure stays on record, and the budget alert moves on
to the month after the latest data.

**Step 2 — be realistic about 2026 and aim at 2027.** About six weeks of 2026 remain and the gap is 481 t, roughly
15 % of the year, so the 2026 cap is out of reach. What it would have taken from April (scenario tool, same simulator
as the Scenarios page):

| Lever, applied April–December 2026 | Saved (t) | 2026 projection (t) | vs the 2,749 t cap |
|---|---|---|---|
| −5 % energy use (efficiency) | 100 | 3,130 | 381 over |
| −10 % energy use | 199 | 3,030 | 282 over |
| +10 points of on-site solar | 165 | 3,065 | 316 over |
| +10 points of solar, −10 % energy use | 348 | 2,882 | 133 over |
| +15 points of solar, −10 % energy use | 422 | 2,808 | 59 over |
| +20 points of solar, −10 % energy use | 496 | 2,733 | **met** |
| −10 % production (for reference only) | 180 | 3,049 | 300 over |

The real target is now **2027: a cap of 2,656 t**. At today's pace (about 3,230 t a year) that means removing about
**575 t a year (18 %)**.

**Step 3 — actions by owner** (tonnes from the Breakdown page, last 12 months; savings are typical industry ranges,
not measured on this site):

| Item (owner) | t / year | Action | Typical saving |
|---|---|---|---|
| Compressed air (Maintenance) | 243 | Leak survey and repair, pressure lowered by 1 bar, compressors off at weekends | 20–30 %, about 50–70 t |
| Air conditioning and ventilation (Maintenance) | 365 | Set-points, schedules that follow the shifts, filter and coil cleaning | 10–15 %, about 35–55 t |
| Lighting and offices (Maintenance) | 162 | LED lighting and presence sensors | about 50 %, about 80 t |
| Assembly lines and test benches (P3), cutting machines (P1), crimping (P2) | 527 / 365 / 284 | Switch off idle lines and test benches between shifts and at weekends (standby loads) | 5–10 %, about 60–120 t |
| Delivery trucks (Logistics) | 449 | Full loads, route planning, eco-driving training | 5–10 %, about 20–45 t |
| Boiler on heavy fuel oil (Maintenance) | 292 | Burner tuning, pipe insulation; later a heat pump | about 5 % now, about 15 t |
| Generators (Maintenance) | 157 | Find out why they run (tests, outages, peaks) and cut the runs that are not needed | to measure |

- These quick wins add up to about **250–400 t a year**.
- **More on-site solar** is the structural lever: each extra 10 points of solar share saves about 200 t a year (10 % of the
  2,025 t of Scope 2).
  Quick wins plus about 15 more points of solar cover the 2027 gap.
- Fit **sub-meters** on compressed air and air conditioning first, to replace estimated keys with measurements.
- Size each plan on the **Scenarios** page; after each monthly upload, check on Carbon budget that the year-end
  projection goes down.

**What will not clear these alerts**

- **Lowering the target** in Target & budget would remove the alerts but only hide the problem.
- **A green-electricity contract** (PPA, green tariff, certificates) lowers market-based Scope 2 but not this budget,
  which counts electricity at the grid average (location-based). Counting contracts in the budget would be a policy
  choice (target-setting frameworks accept either method) and would need a new setting.
- **For reference**: the SBTi 1.5 °C rate for Scopes 1 and 2 (4.2 % of the base a year) would mean about −46 % by 2030
  from 2019, a steeper path than −30 %.

## 11. Login, roles and the team

### 11.1 Signing in

- **First start**: no account exists, so the dashboard shows **Create the administrator** (user name, display name,
  password, confirmation). This account then creates the team.
- **Sign-in screen**: a brand panel in the palette colour (CarbonSync AI, "Your site's carbon, under control", four
  key points) and a form card ("Secure area — Welcome back": user name, password, **Sign in**), a French / English
  switch and a help line on forgotten passwords and the lock rule. Until someone signs in, the **menu bar, sidebar and
  toolbar are hidden**: no page, not even its name, is visible.
- **Session**: a random 256-bit token is kept in a browser cookie for **12 hours**; only its SHA-256 is stored.
  **Log out** deletes it. If the browser blocks the cookie, the login still works but a page refresh asks for the
  password again.
- **Lock**: 5 wrong passwords in a row lock the account for **15 minutes**; an administrator can unlock it.
- **Switching it off**: `auth: enabled: false` in `config.yaml` (or `CARBONSYNC_AUTH=off`, used by the tests): no
  login, everyone is an administrator.

### 11.2 Roles

| Role | Can |
|---|---|
| **Viewer** | Monitor, Alerts, Assistant, Plan, Data quality and Evidence pages; ask the assistant; download existing reports; change their own password (Configuration shows only the **Password** tab). Data intake, Model lab and the AI & API pages are left out of the menu; action buttons are greyed out with the role needed |
| **Editor** | + Data intake, Model lab, run forecasts, generate reports, save scenarios, check alerts; Configuration tabs Target & budget, Inventory & breakdown, Factors (read-only), Reports & alerts, Password |
| **Administrator** | + AI & API pages (keys), Users, Appearance, Layout, System, reset the data |

A page a role cannot open is left out of its menu, so typing its address does not open it either.

### 11.3 The account panel and the team

The account button (top right of every page) opens a panel with:

- **You**: initials, name, role and user name.
- **Team**: every account, online first. **Online** means a page of the dashboard was open in the last 3 minutes:
  each open page sends a presence beat every minute (written at most every 50 seconds). Otherwise the panel shows
  when the person was last seen ("12 min ago", "3 h ago", a date, or "never signed in"). Logging out makes a person
  offline at once.
- **Latest data files**: the 5 most recent uploads with the file name, department, number of rows, period covered,
  when, and **who sent it** (recorded for every upload since the review fixes).
- **Last pipeline run**: when, what triggered it, and the data date.
- **Log out**. The password is changed in Configuration → **Password**.

### 11.4 Passwords and accounts

- Passwords: at least 8 characters, no space at the start or end. Stored only as a **PBKDF2-HMAC-SHA256 hash, 600,000
  rounds, 16-byte random salt** (OWASP 2023 level), in `data/auth/users.json`, which git ignores.
- User names: 3–32 characters, lowercase letters, digits, dot, dash or underscore.
- **Configuration → Password** (everyone): current password, new password, confirmation. A change closes the user's
  other open sessions.
- **Configuration → Users** (administrators): accounts per role, add a user, change a role, unlock, reset a password
  (which also closes that user's sessions), delete (not yourself; the last administrator cannot be deleted or lose
  the role).
- Terminal, for an administrator on the PC (for example a forgotten administrator password):
  `python -m carbonsync.auth list | add <name> --role <role> | passwd <name> | role <name> <role> | unlock <name> |
  delete <name>`.

## 12. The dashboard, page by page

The top bar groups **18 pages in 9 groups**: Monitor (Overview, Forecast, Breakdown), Alerts, Assistant, AI & API
(AI overview, Ollama, OpenRouter, Claude), Models (Model lab), Plan (Carbon budget, Scenarios), Data (Data intake,
Data quality), Evidence (GHG inventory, Methodology, Reports) and Settings (Configuration). It stays fixed when you
scroll and is hidden on the sign-in screen.

On every page: the **French / English** switch and the **account button** at the top right; at the bottom right, the
round **AI button** and, above it, the round **alert button**; an optional footer. Overview, Alerts, Assistant and
Configuration cannot be hidden; any other page can be hidden in Configuration → Layout (the user has hidden
Methodology). Key-figure tiles keep their text inside the box: a value that is a name is shown smaller and wraps on
two lines.

### Monitor

**Overview** (`app/pages/overview.py`): the one-screen summary.
- First row: emissions over the last 12 months (and the year-on-year change), this month's budget status, the next 30
  days of forecast, the Scope 2 share.
- Second row: Scope 2 **location-based** and **market-based** (with the share under contract), this year's cap on the
  −30 % path, and the open alerts (critical + warnings).
- **Emissions trajectory**: history + forecast with the 90 % interval. Buttons 90 d / 180 d / 365 d / **All**. "All"
  shows all 3,000+ days, opens on the last year, and can be dragged back to any date. Off-scale anomaly spikes are
  drawn as triangle markers (hover shows the real value). The on-site solar and efficiency milestones are marked.
- **Scope split** (last 12 months) and **Monthly emissions** (last 24 months, stacked by scope).

**Forecast** (`app/pages/forecast.py`): the active forecast in detail.
- Tiles: horizon total, daily mean, forecast peak, mean interval width.
- Forecast and recent history (day or week step), the 10 highest-emitting days, the latest run of each model side by
  side (with or without intervals), backtest accuracy.
- **Run a new forecast** (editors): model Auto (router: Chronos-2) / Ens-PC / Chronos-2 / Prophet, training history of
  180, 365, 730 or 1,095 days. Chronos-2 takes about 9 s on CPU (mostly loading its weights). The new run becomes the
  active forecast.

**Breakdown** (`breakdown.py`, `inventory/breakdown.py`): emissions by plant zone and by item ([§ 9.3](#93-breakdown-by-zone-and-item)).
- Tiles: total allocated, largest zone (with its share and owner), largest item, items tracked.
- Zone bars, the 10 largest items (coloured by zone), zones month by month, and the full table (zone, item, source,
  key, tonnes). A note says the keys are estimates. Editors can open the key editor from the page.

### Alerts

**Alerts** (`app/pages/alerts.py`, `src/carbonsync/alerts.py`): see [§ 10](#10-alerts-and-the-action-plan).

### Assistant

**Assistant** (`app/pages/assistant.py`): chat with the data. Engine picker, Reasoning switch, Analysis (1 / 3 / 6 /
12 months), History, New chat, Export, suggestion chips, live status. Full description in [§ 14](#14-the-ai-assistant).

### AI & API (administrators)

The assistant's engines. Each cloud provider has its own page with its API key. A key is pasted into a hidden field,
then saved to the Windows Credential Manager. It is only ever shown masked (`sk-ant-…a1b2`).

**AI overview** (`ai_overview.py`):
- one tile per provider: status, model, today's spend;
- the **daily spending cap**, shared by all cloud providers ($5 by default, 0 = no cap), and the last 30 days of use;
- what is sent to the cloud, and the **expert brief** every cloud model reads first.

OpenAI, Gemini, Mistral and "OpenAI-compatible" have tiles marked "coming soon".

**Ollama** (`ai_ollama.py`):
- *Ollama Cloud*: the key (with a connection test that reads the account balance) and the 5 recommended cloud models
  with their prices: DeepSeek V4 Pro (recommended), Kimi K3.1 Pro, GLM 5.3, Mistral Large 4, DeepSeek V4.1 Flash. Any
  other cloud model can be picked too. A **quick test** sends a one-word question.
- *Local Ollama*: installed models, models in memory, GPU share, default model, context size (8k / 16k recommended /
  32k).
- **Stop local models** unloads them to free the GPU for other software. **Start** loads the default model again
  (about 10 s) and opens Ollama if it was closed.

**OpenRouter** (`ai_openrouter.py`):
- The key, with a test that reads today's free quota. The 5 recommended **free** models, all with tools and reasoning:
  NVIDIA Nemotron 3 Ultra (recommended), Google Gemma 4 31B, NVIDIA Nemotron 3.5 Super VL, inclusionAI Ling 3.2 Flash,
  Google Gemma 4 26B A4B. Any other free model can be picked from the live list.
- Free limits: 20 requests a minute, 50 a day (1,000 once 10 credits are bought); one question takes 2–4 requests.
- **Automatic fallback** to another free model when the chosen one is busy.
- **Deny data collection**: only uses providers that keep no requests.

**Claude** (`ai_claude.py`): the Anthropic key, the model (Opus 5.5, Sonnet 5.5, Haiku 5.5), reasoning effort
(low / medium recommended / high), connection test, usage.

### Models (editors)

**Model lab** (`model_lab.py`, `models/lab.py`): tests each model on its own.
- Choose models (Prophet, Chronos-2, Ens-PC), horizon (3 / 6 / 12 months), training period (6 / 12 / 24 months or all
  of it), and mode:
  - **Backtest on the past**: the forecast is scored against real data from a cut-off date;
  - **Forecast the future**.
- Results: forecasts vs actuals and the error by forecast week; a score table (MAE, MASE, coverage, bias) with the
  naive baseline; the session's experiments and the latest run log; a "how it works" view (Prophet's trend and
  seasonalities).
- Experiments never change the active forecast.

### Plan

**Carbon budget** (`budget.py`, `src/carbonsync/budget.py`, `targets.py`): actuals + forecast against the monthly
ceiling, for a chosen year and month ([§ 8](#8-reduction-target-and-carbon-budget)).
- Tiles: ceiling, consumed, end-of-month projection, 90 % upper bound.
- Cumulative consumption this month, and the year view (estimated landing vs the annual cap).
- Status green / amber / red with a banner.
- **Path to the target**: actual years against the path from the base year to 2030, and the gap each year.

**Scenarios** (`scenarios.py`, `scenarios/whatif.py`): sliders for solar self-consumption (0–40 %), energy efficiency
(0–15 %) and production volume (−20 to +20 %), applied to the active forecast.
- Results: baseline, scenario, avoided emissions (total and per day), each lever's contribution, a chart, and saved
  scenarios (editors).
- Rules: solar and efficiency cut electricity; efficiency also cuts fuels by half; production has a 0.75 elasticity
  on emissions.

### Data

**Data intake** (`data_intake.py`, `ingest/intake.py`, editors): adds data.
- One tile per department shows its last day received and its state.
- Drop one CSV in a department's zone, or **everything at once** (several CSVs, a folder or a .zip). The department
  is found from the file name or the columns. Each upload records who sent it.
- The whole pipeline then reruns with a live log. A month waits until all four departments have sent it.
- Also: CSV templates, **Rerun without a new file**, a "Generate the Excel report" switch, pipeline history, coverage
  by department (last 18 months), **Back to the original data** (administrators, with confirmation).

**Data quality** (`data_quality.py`): what came in, what was fixed, what was rejected.
- Tiles: files accepted, quarantined, imputed days, confidence score (/100).
- Rejection reasons, the quarantine log (each file's reason and **suggested fix**), and the expected schemas.

### Evidence

**GHG inventory** (`inventory.py`, `inventory/scope2.py`, `inventory/scope3.py`): the inventory as ESRS E1-6 asks
for it ([§ 9](#9-greenhouse-gas-inventory-and-breakdown-by-zone)): Scope 2 by both methods year by year, the Scope 3
screening (category bars and a table with method and source), and an ESRS E1 checklist (E1-4 target, E1-5 energy,
E1-6 Scopes 1 / 2 / 3). Editors can open the inventory settings from the page.

**Methodology** (`methodology.py`): the rolling-backtest evidence.
- Tiles: best model, mean error (MAE), best 90 % coverage, number of evaluations.
- Charts: MAE by history window, interval coverage, error relative to the baseline.

**Reports** (`reports.py`): automatic conclusions and recommendations, then two documents (**Generate**, editors;
then **Download**; earlier reports stay listed):
- **Excel audit workbook**, 13 sheets: Summary, Annual, Monthly, Activity, Forecast, Budget, GHG inventory,
  Breakdown, Alerts, Models, Data quality, Methodology, Log;
- **PDF brief**, 3 pages:
  - page 1: key figures, monthly emissions with the forecast, the conclusions;
  - page 2: recommendations, charts, the forecast by month, backtest accuracy and the year's budget table;
  - page 3: both Scope 2 figures, Scope 3 and the target, the path to −30 %, the breakdown by zone, the Scope 3
    screening table, the open alerts, data and method.

Also CSV downloads (daily, monthly and annual emissions, active forecast), the backtest (JSON) and an **ESRS E1
readiness** table (covered / partial), with rows for Scope 3 and the target (E1-4).

### Settings

**Configuration** (`configuration.py`, `settings_targets.py`, `settings_users.py`): up to nine tabs depending on the
role ([§ 15](#15-settings-and-look)).

## 13. How the pages work together

| When you… | What happens, and where it shows |
|---|---|
| **Upload files** (Data intake) | The pipeline reruns: validate → load → daily table → forecast (router) → backtest refresh → alerts → reports. The page caches follow the DuckDB file, so **every page** shows the new data on the next view. Rejected files appear in **Data quality** with a suggested fix; you fix them and upload them again. The upload and its sender appear in the account panel |
| **Run a new forecast** (Forecast) | It becomes the **active forecast**, used by Overview, Carbon budget, Scenarios, Reports, the alerts and the assistant |
| **Run an experiment** (Model lab) | Nothing else changes; experiments are kept apart from the active forecast |
| **Move the Scenarios sliders** | Uses `scenarios/whatif.py`. The assistant's `run_scenario` tool and the levers in its Analysis use the same simulator, so they give the same answers |
| **Generate a report** (Reports) | `export/insights.py` computes the figures, conclusions and recommendations once. The Reports page, the Excel workbook and the PDF show those same figures |
| **Change the target, cap or weights** (Target & budget) | `config.yaml` is saved (with an audit copy). The Budget page, Overview, the alerts, the reports and the assistant's budget figures follow |
| **Record a contract, change Scope 3 or breakdown keys** (Inventory & breakdown) | The GHG inventory, Breakdown, Overview's second row, the reports and the `get_inventory` / `get_breakdown` tools follow. Keys that do not total 100 % cannot be saved |
| **New data or a new forecast** | The alerts are checked again; the alert button, the Alerts page, the PDF and `get_alerts` show the result |
| **Change the look** (Appearance) | `config.yaml` + the theme in `.streamlit/config.toml`. Streamlit applies it live; the CSS, charts, logo, favicon and sign-in screen follow |
| **Hide a page / move the start page** (Layout) | The top bar is rebuilt from the menu minus hidden pages |
| **Change a role** (Users) | It applies at the person's next page view: menu, tabs and buttons follow |
| **Save an API key / pick a model** (AI & API) | The key goes to the Credential Manager and the model to `data/assistant/settings.json`. The Assistant's engine picker and the floating button offer it. Usage goes to `usage.json` and counts towards the daily cap |
| **Ask the floating AI button** | Same conversation and engine as the Assistant tab. The open page goes with the question, so "explain this page" works anywhere |
| **Ask the assistant anything** | It reads a snapshot of the same database and configuration the pages use. The snapshot refreshes after an upload, a new forecast or a settings save, and `explain_dashboard` searches the page guide (`docs/assistant/guide_*.md`) |

## 14. The AI assistant

### 14.1 What it is for, and its one rule

The assistant answers questions about the site's emissions and about the dashboard, and writes quantified analyses
and action plans, in French or English.

Its rule: **the model explains; it never computes.** Every figure comes from 16 read-only tools that query the same
data as the pages. A deterministic Analyse engine does the analytical work (hotspots, causes, projection, required
reductions, levers). A figure check then compares every number in the answer with what the tools returned, and lists
anything not found as "to check". The assistant cannot change data or settings.

### 14.2 How a question is answered

```
 question (Assistant tab or floating AI button, + the open page)
   |
   +- prefetch: when the built-in router is sure of the tool (budget, forecast,
   |            quality, analysis, years...), that tool runs first
   v
 engine.ask(): tool loop, up to 6 rounds (the last without tools), streamed live
   model -> tool calls -> tools.execute() (validated, read-only, privacy filter)
         -> results -> model ...
   v
 answer -> figure check (grounding.py) -> screen: text, tool chips, charts built
           from the tool results, "figures to check", Sources panel (engine,
           data date, forecast used, time, tokens, cost)
   v
 saved in data/assistant/chats/<id>.json
```

If the model fails (Ollama not running, model missing, no key, cap reached, API down), the **built-in assistant**
answers instead from the same tools. The user always gets exact figures.

### 14.3 The 16 tools (`src/carbonsync/assistant/tools.py`)

| Tool | What it returns |
|---|---|
| `get_overview` | Data period, last-12-month totals by scope and change, intensity, peaks, active forecast, budget status, target and path cap, both Scope 2 figures, Scope 3, open alerts, router, best model, report conclusions |
| `query_emissions` | Emissions and activity by day, week, month or year over a date range (max 400 rows; coarser automatically) |
| `compare_periods` | A period vs a reference (default: a year earlier), every scope and activity, plus an LMDI split into volume and intensity effects |
| `get_forecast` | The active (or a given) forecast with its 90 % interval, by day, week or month |
| `list_forecast_runs` | Stored forecast runs, newest first, and which one is active |
| `get_budget` | Monthly ceilings vs actual and forecast (green / amber / red) and a full-year estimate vs the cap, with a summary sentence |
| `get_data_quality` | Files accepted and quarantined (reason, suggested fix), last day per department, waiting months, imputed days |
| `get_pipeline_activity` | Recent pipeline runs and uploaded files |
| `get_model_performance` | Backtest scores (MAE, MASE, coverage), best model, router rule |
| `get_inventory` | Scope 1, Scope 2 location- and market-based (coverage, residual mix), Scope 3 screening by category with its status, totals for both methods |
| `get_breakdown` | Emissions by plant zone and item from the allocation keys; optional zone code |
| `get_alerts` | Open alerts, counts, rules and thresholds; in cloud mode the day-level anomaly values stay local |
| `get_methodology` | Emission factors, formulas, frameworks, cap and weights, forecast settings, data rules |
| `explain_dashboard` | Searches the page guide: what a page, chart, button or setting does and how to do something |
| `run_scenario` | What-if on the active forecast (same simulator as Scenarios) and its effect on the year vs the cap |
| `analyse` | The full analysis (§ 14.4): findings and an action plan |

Each tool has a provider-neutral JSON schema, French / English status labels for the live activity line, argument
validation, and output cleaned to plain JSON with no file paths. The built-in engine also answers inventory, zone
and alert questions directly from these tools.

### 14.4 The Analyse engine (`assistant/analyst.py`)

Six pure-pandas steps; the model only puts them into words:

1. **Hotspots**: the last N months vs the same months last year, vs the 12-month average, and the seasonal rank
   against every earlier year.
2. **Drivers**: the activity behind it (kWh, litres, units, trips) and intensities.
3. **Decompose** (LMDI-I): splits each source's change into a volume effect and an intensity effect.
4. **Project the year**: actual + forecast + a labelled seasonal estimate beyond the horizon, against the cap, with
   the 90 % range.
5. **Reductions**: the gap as concrete targets (kWh/day, L/day, % vs now) for a short window and for the rest of the
   year, with a feasibility label.
6. **Levers**: what-if levers ranked by how much of the gap they close, and the minimum single-lever effort.

It also runs in a terminal: `python -m carbonsync.assistant --lang en --months 3`.

### 14.5 The engines

| Engine | Runs on | Data it sees | Cost | Key |
|---|---|---|---|---|
| **Built-in** | This PC, no model | Everything (local) | Free | None |
| **Local Ollama**: `qwen3.5:9b` (default), `qwen3.5:4b` (faster), `qwen3:1.7b` | This PC, GPU | Everything; nothing leaves the PC | Free | None |
| **Ollama Cloud**: DeepSeek V4 Pro, Kimi K3.1 Pro, GLM 5.3, Mistral Large 4, DeepSeek V4.1 Flash, others | ollama.com | **Monthly totals only** | Per token | Ollama key |
| **OpenRouter**: Nemotron 3 Ultra, Gemma 4 31B, Nemotron 3.5 Super VL, Ling 3.2 Flash, Gemma 4 26B A4B, other free models | openrouter.ai | **Monthly totals only** | Free models ($0) | OpenRouter key |
| **Claude**: Opus 5.5, Sonnet 5.5, Haiku 5.5 | Anthropic API | **Monthly totals only** | Per token | Anthropic key |
| OpenAI, Gemini, Mistral, custom OpenAI-compatible | — | — | — | Coming soon (registry rows only) |

How each provider is built:
- **Common base**: one provider-neutral message format (`providers/base.py`). One registry (`providers/registry.py`)
  feeds the pages, the key vault, the usage meter and the engine picker, so adding an OpenAI-compatible provider is
  one row in a table.
- **Ollama, local and cloud** (`ollama_provider.py`, `ollama_cloud.py`): the same protocol and streaming. Cloud just
  adds another host and a key.
- **OpenRouter** (`openai_compat.py`, `openrouter.py`): the official `openai` SDK with another address. Tools are sent
  on every round and the last round uses `tool_choice: "none"`; a cut answer never runs its tool calls; fallback goes
  to free models only; no attribution headers are sent, so the project is never named to OpenRouter.
- **Claude** (`anthropic_provider.py`): streaming, adaptive thinking with an explicit effort. The tools and the expert
  brief are cached (cache reads cost 5–10 % of normal input). Claude's reasoning is replayed unchanged and the history
  is kept append-only. On Opus and Sonnet, a request declined for policy reasons is retried on another Claude model.

### 14.6 The local models: where they came from and where they are

- **Ollama** (the local model server) was installed with `winget install Ollama.Ollama` on 2026-11-20 (version
  0.40.16), in `%LOCALAPPDATA%\Programs\Ollama\`.
- **The models were downloaded by Ollama from its official model library** (ollama.com/library, registry
  `registry.ollama.ai`), with `ollama pull`:

| Model | Size | Pulled | Evaluation (§ 14.12) |
|---|---|---|---|
| `qwen3.5:9b` (default) | 6.6 GB | 2026-11-20 | 97.6 / 100, 12.2 s per question |
| `qwen3.5:4b` | 3.3 GB | 2026-11-20 | 95.3 / 100, 5.8 s |
| `qwen3:1.7b` | 1.4 GB | already on this PC since 2026-08-02 (not pulled for this project) | 89.0 / 100, 5.1 s |

- **Where they are stored**: Ollama's default folder, `%USERPROFILE%\.ollama\models` (`OLLAMA_MODELS` is not set),
  10.5 GB in total. The project never stores model files itself, and the dashboard never downloads or deletes models;
  use the Ollama app or `ollama pull` / `ollama rm`.
- **GPU use**: about 5.6–6.3 GB of video memory for the 9B while loaded. A model leaves memory by itself 20 minutes
  after the last question. `stop.bat` unloads every model and closes Ollama. **Stop** (AI & API → Ollama) frees the
  GPU at once; the assistant then does not load the model again by itself until **Start**.

### 14.7 Expert mode and the expert brief

Any cloud model runs in **Expert mode**. Every cloud conversation starts by reading the **expert brief**
(`docs/assistant/expert_brief.md`): the role (a senior industrial decarbonisation consultant fluent in GHG Protocol,
ISO 14064-1, CSRD / ESRS E1, ISO 50001 and LMDI), the site and its data, the metrics, the tools and the working and
answering rules (quantified, prioritised, honest about uncertainty; calculations written out).

It contains no changing figures: dates, cap, factors and the active forecast come in a separate context block, so
the brief can be cached. Any new provider gets it through `prompts.system_prompt(mode="expert")`.

Switching between local and Expert mode starts a new conversation, so a cloud model never sees results read in local
mode.

### 14.8 Privacy and security

- **API keys** live only in the **Windows Credential Manager** (`keyring`, encrypted by Windows, tied to the Windows
  account). They never go in `config.yaml`, git, logs, exports or saved conversations, and are shown masked only.
  The logs blank anything that looks like a key, and every stored key is also blanked by its value. An environment
  variable (`ANTHROPIC_API_KEY`, `OLLAMA_API_KEY`, `OPENROUTER_API_KEY`) is used when no key is stored.
- **Cloud data floor**: in Expert mode, tool results are never finer than a month. Periods are widened to whole
  calendar months, granularity is at least monthly, the single highest day is left out, and day-level anomaly values
  stay local.
- **Sent to the cloud**: the question and the Expert conversation, the brief, the context (dates, cap, factors,
  active forecast) and monthly-or-coarser tool results.
- **Never sent**: daily rows, CSV files, results read with a local model, the user's identity, the keys, attribution
  headers.
- Local mode sends nothing anywhere.

### 14.9 Cost control

- `data/assistant/usage.json` records tokens and estimated cost per day, provider and model.
- One **daily spending cap** ($5 by default, 0 = none) covers every cloud provider. Once it is reached, cloud models
  pause until the next day and the built-in assistant answers instead.
- Each answer's Sources panel shows its estimated cost.
- The meter uses list prices (checked 2026-11-20; DeepSeek at its peak price, to stay safe). Each provider's own
  console has the real bill.

### 14.10 The figure check (`assistant/grounding.py`)

Every number in an answer is looked up in the conversation's tool results, the system prompt and the user's own
questions. It counts as found when a source value rounds to it at the precision it is written with, or is within
0.5 %.

- A written-out calculation ("3,155 − 3,096 = 59 t") counts as found when its operands are found and its arithmetic
  is right.
- Small whole numbers (up to 31), plain years and unit constants are not checked.
- Anything else is listed under the answer as "figures to check".

### 14.11 The assistant in the interface

- **Assistant tab** (`app/pages/assistant.py`, `app/ui/chat.py`): engine picker; **Reasoning** (the model thinks
  first: more accurate, slower); **Analysis** over 1, 3, 6 or 12 months; History; New chat; suggestion chips; live
  status of each tool. Answers show tool chips, charts built from the
  results (analysis card, budget, forecast, emissions, LMDI), the figures to check and a Sources panel.
- **Floating AI button** (`app/ui/assistant_dock.py`): a round button at the bottom right of every page (except the
  Assistant tab) opens a panel over the page. It uses the same conversation and engine as the tab and knows the open
  page: the first suggestion explains it, the others are about it. It runs as a fragment, so chatting does not reload
  the page underneath; it shows the last 4 questions and has **Open in the Assistant tab**. Side (right / left) and
  on / off are in Configuration → Layout. The alert button sits just above it and steps aside while the panel is open.
- **Export** (`assistant/export.py`): the whole conversation, or only the last answer (for example the action plan),
  as Markdown or PDF. Each answer keeps its sources.
- **History** (`assistant/store.py`): one JSON file per conversation in `data/assistant/chats/`, on this PC only.

### 14.12 Evaluation of the local models (`assistant/evaluation.py`)

30 questions (15 topics × French / English) with known answers. The expected values are recomputed from the tools
at each run, so the set keeps working when new data arrives.

Score = right tool 25 % + expected figures 45 % + no unsourced figure 20 % + right language 10 %.

Run on this PC (RTX 4060 8 GB, 16k context):

| Model | Score | Right tool | Expected figures | No unsourced figure | Language | s / question |
|---|---|---|---|---|---|---|
| **qwen3.5:9b (recommended)** | **97.6** | 100 % | 98.3 % | 91.7 % | 100 % | 12.2 |
| qwen3.5:4b | 95.3 | 100 % | 93.3 % | 91.7 % | 100 % | 5.8 |
| qwen3:1.7b | 89.0 | 90 % | 83.3 % | 95.0 % | 100 % | 5.1 |

- The 9B is the default.
- The 4B is twice as fast, but it got the Q1 2026 vs Q1 2025 comparison wrong in both languages. A faster model only
  wins if it stays within 3 points on the score **and** on the figures.
- The evaluation runs from the terminal: `python -m carbonsync.assistant.evaluation --models …`.

### 14.13 What testing with real models changed

| Seen with the real model | Fix |
|---|---|
| The 9B skipped the analysis and invented "−120 kWh/day, realistic"; it also invented a page | **Prefetch**: confident questions get their tool before the first model call |
| "Oct–Dec forecast = 0" in a budget answer | `get_budget` returns a `summary` sentence (actual + forecast + estimate = total vs cap) |
| "Even the low end exceeds the cap" when it did not | The summary states the certainty ("likely but not certain") |
| The model did its own year-on-year arithmetic | `query_emissions` returns the changes; rule: keep the tools' figures, compute none |
| "2025 vs 2024" answered with rolling 12 months | Questions naming years go to calendar-year totals |
| The projection differed by analysis period (3,222 vs 3,221 t) | A fixed 3-month trend window |
| The evaluation accepted 811 t for 826 t | Tolerance tightened to 1 % at 3 significant figures; all models re-run |

### 14.14 How it was built

| Phase | Session | Built |
|---|---|---|
| 1 — Foundations | 02 | Analyse engine, 13 tools with the privacy mode, French / English guide and search, terminal entry point |
| 2 — Local assistant | 03 | Ollama + Qwen 3.5, streaming tool loop, prefetch, figure check, built-in fallback, charts, Sources, history |
| 3 — Expert mode | 04 | Expert brief, Claude provider, key vault, usage meter and cap, whole-month privacy floor, calculation-aware figure check |
| AI providers | 05–07 | Registry and AI & API pages, Ollama Cloud (top 5), OpenRouter (top 5 free, fallback, data-collection filter), GPU Stop / Start, "coming soon" providers |
| 4 — Polish | 08 | Floating button, Markdown / PDF export, local-model evaluation, page-aware Expert questions |
| Follow-up | 09 | Built-in what-if route (solar / efficiency / production → `run_scenario`) |
| Critical changes | 10 | 3 new tools (`get_inventory`, `get_breakdown`, `get_alerts`; 16 in all), richer `get_overview`, built-in answers on inventory, zones and alerts, page guide 23 sections in each language |

Live tests:
- **Done**: the local models on this PC (all three); the user's OpenRouter key, tested live with Nemotron 3 Ultra
  ($0).
- **Not yet live**: Ollama Cloud and Claude are built and tested offline with mocked APIs; no key has been added for
  them yet.

## 15. Settings and look

**Configuration** has up to nine tabs, depending on the role; every save writes `config.yaml` and a timestamped copy
in `data/config_history/`.

| Tab | Who | Contains |
|---|---|---|
| **Appearance** | Admin | Palette (Emerald, Ocean, Indigo, Amber, Coral, Graphite, or **Custom** with any accent colour); **Glass** (gradient background, translucent cards, frosted top bar) or **Flat**; Auto / Light / Dark; text size S / M / L; Comfortable / Compact; Rounded / Sharp corners; animations; **animated background**. A live preview shows both modes with the contrast figures. **Apply** changes every page without a restart |
| **Layout** | Admin | Pages shown in the menu, start page, page width (Standard / Full), floating AI button (on / off, right / left), footer |
| **Target & budget** | Editor | Base year, target year and reduction (−30 % by 2030); cap source (**path** or fixed cap); monthly weights (**seasonal** from chosen years, or manual totalling 1.00); a preview of the plan before saving |
| **Inventory & breakdown** | Editor | Scope 2: residual mix and its source, contract instruments (type, share or MWh, factor, dates) with the market-based figure before / after. Scope 3: materials per harness, staff and commuting, transport modes (shares must total 1), upstream factors, exclusions with reasons. Breakdown: zones and the allocation keys per meter (each must total 100 %) |
| **Factors** | Editor | Emission factors with unit, scope, source, last review date and its age ("Review due" after a year). Read-only: they are changed in `config.yaml`, then the pipeline is rerun |
| **Reports & alerts** | Editor | Site name (report cover, browser tab), report language; alert thresholds (stale-data days, anomaly window, router margin). Email: not configured |
| **Users** | Admin | Accounts per role, add a user, change a role, unlock, reset a password, delete ([§ 11.4](#114-passwords-and-accounts)) |
| **System** | Admin | Default language, data mode, engine figures, **router and backtest** card (model, fallback, minimum days, backtest after each upload), **export / import** the settings (JSON, no data, no keys), **reset to defaults**, change history |
| **Password** | Everyone signed in | Change your own password (current, new, confirm); your other sessions are closed. The rules and your last sign-in are shown beside the form |

How the look works:
- `app/ui/appearance.py` derives each palette from one accent colour and **fits it to pass WCAG contrast** in both
  modes, on flat and frosted surfaces, with both background glows stacked: text at least 7:1, accent at least 4.5:1,
  buttons at least 3:1 / 4.5:1.
- `.streamlit/config.toml`'s theme sections are rewritten from the settings, and Streamlit applies them live.
- `app/ui/theme.py` generates the CSS. The logo, favicon and the sign-in brand panel are drawn in the accent colour.
- Chart colours are fixed by entity (Scope 1 orange, Scope 2 blue…), whatever the palette, with separate dark-mode
  steps checked for colour blindness.
- Alert colours are fixed: critical dark red (#7f1d1d, white text at 9.9:1), warnings amber.
- Key-figure tiles: figures keep the large size; names are shown smaller and wrap on two lines inside the tile.
- The user's own look is **Amber, Glass, light mode** (animated background on).

## 16. Where data, settings and keys are kept

| Path | Holds | In git |
|---|---|---|
| `config.yaml` | Factors, scopes, target, budget, router, forecast and backtest settings, Scope 2 / 3, breakdown keys, alerts, login switch, appearance, layout, reports | Yes |
| `.streamlit/config.toml` | Server settings + the theme (regenerated from `config.yaml`) | Yes |
| `data/carbonsync.duckdb` | Every table (raw, daily, emissions, forecasts, backtest) | No (`data/` is ignored) |
| `data/inbox/`, `processed/`, `quarantine/`, `truth/` | Incoming CSVs, validated Parquet, rejected files + reasons, ground truth | No |
| `data/uploads_manifest.json`, `uploads_backup/` | Uploads made from the dashboard (with who sent them), and the originals they replaced | No |
| `data/pipeline_history.jsonl` | Every pipeline run | No |
| `data/data_quality_report.json`, `backtest_summary.json` | Quality report, backtest scores (with the code commit that computed them) | Yes (evidence files) |
| `data/alerts.json` | Open alerts, first-seen dates, history | No |
| `data/auth/users.json` | Accounts (password hashes only), sessions (token hashes, expiry, last seen) | No — never commit |
| `data/reports/` | Generated Excel and PDF reports | No |
| `data/assistant/chats/` | Saved conversations | No |
| `data/assistant/settings.json`, `usage.json`, `eval/` | AI settings (no keys), token use and cost, evaluation results | No |
| `data/config_history/` | An audit copy of the configuration at every save | No |
| `data/ui/` | Logo and favicon in the current accent colour | No |
| Windows Credential Manager | The cloud API keys | Never on disk in the project |
| `%USERPROFILE%\.ollama\models` | The local models (Ollama's own folder) | Outside the project |

## 17. Folder layout

```
carbonsync-dashboard/
  streamlit_app.py       entry point (streamlit run streamlit_app.py)
  start.bat, start.sh    start, open the browser, show the activity
  stop.bat               stop the dashboard, background work, local models, Ollama
  config.yaml            all settings
  requirements.txt, README.md
  app/                   the dashboard
    main.py              navigation shell, theme, floating buttons, presence beat
    core/                login gate and roles, navigation, budget re-export, FR / EN
    data/                cached DuckDB / JSON loaders
    pages/               one file per page (18) + settings tabs (targets, users)
    ui/                  appearance, theme CSS, components, charts, chat,
                         floating AI button, alert button
  src/carbonsync/        the engine
    synth/               synthetic history + messifier
    ingest/              validation, schemas, loading, upload intake
    features/            daily table and emissions
    models/              Prophet, Chronos-2, Ens-PC, router, conformal band, lab
    backtest/            rolling backtest and metrics
    scenarios/           what-if simulator
    inventory/           Scope 2 (both methods), Scope 3 screening, breakdown
    export/              insights, Excel workbook, PDF brief
    assistant/           engine, tools, analyst, figure check, prompts, guide,
                         providers, key vault, usage and cap, chats, evaluation
    targets.py           base year, reduction path, seasonal weights
    budget.py            monthly and yearly budget evaluation on the plan
    alerts.py            alert rules and data/alerts.json
    auth.py              accounts, hashes, sessions, presence, roles, CLI
    pipeline.py          one-call refresh (7 steps)
    obs.py               logging
  scripts/               make_sample_uploads.py, stop_dashboard.ps1
  samples/               2026-Q1.zip, 2026-Q2.zip, April test files
  tests/                 334 tests
  docs/
    CARBONSYNC_REPORT.md this report
    assistant/           page guide FR / EN and the expert brief
  data/                  raw synthetic CSVs (inbox/) and their ground truth (truth/); the rest is generated
```

## 18. Running it

```
pip install -r requirements.txt
start.bat                  start, open the browser, keep the window open
start.bat nobrowser        same, without opening the browser
stop.bat                   stop everything: dashboard, background work,
                           local models, Ollama
stop.bat keep-ollama       same, but leave Ollama open (models still unloaded)
streamlit run streamlit_app.py   by hand

python -m carbonsync.pipeline    same loop as Data intake
                                 (--no-forecast, --no-report, --backtest)
python -m carbonsync.auth add <name> --role admin    create an account
                                 (also list, passwd, role, unlock, delete)
python -m carbonsync.backtest.rolling                recompute the backtest
python -m carbonsync.assistant --lang en             the analysis in a terminal
python -m carbonsync.assistant.evaluation --models qwen3.5:9b qwen3.5:4b
python scripts/make_sample_uploads.py                rebuild the sample uploads

python -m pytest -o pythonpath="src ." tests/ -q
ruff check app src tests scripts
```

On the first start the browser asks to create the administrator account. For the local assistant:
`winget install Ollama.Ollama`, then `ollama pull qwen3.5:9b` (and `qwen3.5:4b` for a faster one).

## 19. Tests and checks

| Test file | Tests | Covers |
|---|---|---|
| `test_synth.py` | 4 | Synthetic generator and messifier |
| `test_ingest.py` | 5 | Validation, repairs, quarantine, loading |
| `test_features.py` | 4 | Daily table, imputation, emissions |
| `test_models.py` | 5 | Router (Chronos-2 default, Prophet fallback, legacy rule), Prophet, conformal band, stored forecasts |
| `test_backtest.py` | 4 | Rolling backtest and metrics |
| `test_scenarios_and_export.py` | 4 | What-if, Excel workbook (13 sheets), PDF (3 pages) |
| `test_intake_lab.py` | 14 | Date parsing, department detection, archives, uploads and undo, who uploaded, model lab |
| `test_app.py` | 12 | Data loaders, page entry points, what-if on the stored forecast, budget, launcher, backtest provenance |
| `test_assistant.py` | 30 | Tools, privacy floor, Analyse engine, guide search |
| `test_assistant_engine.py` | 38 | Tool loop, prefetch, fallback, figure check, built-in answers |
| `test_assistant_local_gpu.py` | 8 | Ollama provider, GPU Stop / Start |
| `test_assistant_cloud.py` | 29 | Claude provider, key vault, usage and cap, expert brief |
| `test_assistant_ollama_cloud.py` | 22 | Ollama Cloud adapter, prices, keys without prefix |
| `test_assistant_openrouter.py` | 22 | OpenAI-compatible adapter, OpenRouter, fallback, quota |
| `test_assistant_phase4.py` | 35 | Floating button, export, evaluation |
| `test_settings_theme.py` | 30 | Settings, palettes, contrast, theme file, layout |
| `test_targets.py` | 9 | Base year, path caps, seasonal weights, manual fallbacks |
| `test_inventory.py` | 12 | Scope 2 both methods and contract periods, Scope 3 statuses and exclusions, breakdown keys and totals |
| `test_alerts.py` | 9 | Each alert rule, first-seen dates, history, French decimals |
| `test_auth.py` | 16 | Hashes, roles, lockout, sessions, presence online / offline, password change closing other sessions, terminal commands |
| `test_pages_critical.py` | 22 | New pages French / English, viewer and editor access, settings saves, the full login flow, sign-in screen without the menu bar, team panel, alert button, critical filter and colours, text tiles |
| **Total** | **334** | All pass; ruff clean |

Other checks:
- The 7-step pipeline was run on the real data (Chronos-2 forecast, 12-origin backtest, alerts, reports).
- Every page was rendered in French and English with no error; the whole app was run with the login off and on
  (first start, sign-in, viewer access, password change, team presence).
- The 3 PDF pages were rendered to images and inspected; chart colours were run through the palette validator.
- Tests and render scripts never write the real `config.yaml` or `.streamlit/config.toml`. No test reads this PC's
  evaluation results.
- Not checked in a real browser (none on this PC): the visual look of the sign-in screen, the alert panel position
  and the team panel width are for the user to confirm.

## 20. Project timeline

| Date | Work |
|---|---|
| 2026-11-06 | Foundations: synthetic data and messifier, ingestion and validation, daily table and emissions, Prophet / Chronos-2 / Ens-PC + router + conformal band, rolling backtest, what-if and Excel, first multi-page dashboard |
| 2026-11-07 | Dark mode, launcher and entry-point fixes |
| 2026-11-19 | **Session 01**: folder reorganisation and redesign, model lab, data intake loop, sample uploads, full-history view, enriched Excel / PDF reports, sticky top bar |
| 2026-11-20 | **Session 02**: assistant phase 1 (Analyse engine, 13 tools, guide) |
| 2026-11-20 | **Session 03**: assistant phase 2 (Assistant tab, local models) |
| 2026-11-20 | **Sessions 04–07**: Expert mode, Claude, key vault, cap; AI & API pages, Ollama Cloud, OpenRouter, GPU controls |
| 2026-11-20 | **Sessions 08–09**: floating button, export, evaluation; settings, glass theme, palettes, layout, chart restyle, animated background |
| 2026-11-20 | Folder clean-up, first version of this report, `start.bat` / `stop.bat` |
| 2026-11-21 | **Session 10**: the seven critical changes on a test branch (Chronos-2 router + backtest after each upload, market-based Scope 2, Scope 3 screening, −30 % 2030 target and path budget, alerts, breakdown by zone, login with roles) |
| 2026-11-21 | Review fixes: KPI text, floating alert button, critical alerts on dark red, team panel, Password tab, sign-in screen |
| 2026-11-21 | Version accepted: merged into `master`, test backup removed |
| 2026-11-21 | This report brought up to date (alerts analysis and action plan, login and team, target tables) |

## 21. Known limits and next steps

### 21.1 Known limits

- **Synthetic data.** The reports say so. Replace it with real exports before sharing any figure.
- **Off the path.** 2025 was 313 t above the −30 % path and 2026 is projected 481 t over its cap: every 2026 month so
  far is red. This is the target doing its job, not a fault ([§ 10.4](#104-action-plan)).
- **Data gap.** No data after 2026-03-31; April–October 2026 are forecasts until uploaded.
- **Market-based Scope 2 = location-based** until a contract is recorded, and Tunisia has no published residual mix
  (grid average used as a proxy).
- **Scope 3 is a screening**: copper, PVC, transport, commuting and upstream energy are estimates from sourced
  assumptions.
- **Breakdown keys are estimates** until sub-meters exist.
- **Alerts by email** are not configured (no mail server); the alert button and the Alerts page show them.
- **Login**: the session cookie is set from a small same-origin script; with cookies blocked, a refresh asks for the
  password again. "Online" means a page was open in the last 3 minutes. Accounts live in `data/auth/users.json`
  (git-ignored), so they must be recreated on another PC.
- **Imputed days**: 22.6 % of history days are imputed; Data quality lists them.
- **Not tested live**: Ollama Cloud and Claude are waiting for keys; the visuals were checked with AppTest only.
- **Maintenance**: Streamlit does not hot-reload page modules; restart after code changes.

### 21.2 Next steps, in order

1. **Create the administrator account** at the next start, then the team accounts (Configuration → Users).
2. **Upload April–October 2026**; the stale-data warning clears and the budget alerts use real months.
3. **Launch the quick wins** of [§ 10.4](#104-action-plan) (compressed air, air conditioning, lighting, idle
   machines, trucks, boiler) and size them on the Scenarios page.
4. **Plan the on-site solar extension** (about 200 t a year per 10 points of solar share) to close the 2027 gap.
5. **Fit sub-meters** on compressed air and air conditioning first; replace the estimated keys with readings.
6. **Record real electricity contracts** and ask the supplier for a residual-mix factor.
7. **Replace the Scope 3 assumptions** with purchase records (copper and PVC tonnage) and a commuting survey.
8. **Review the ambition** (−30 % vs the SBTi 1.5 °C rate of about −46 % by 2030).
9. **Email alerts** later, with the mail password in the Credential Manager like the API keys.
10. **Check the visuals in a browser** (sign-in screen, alert panel, team panel) and report anything to adjust.
