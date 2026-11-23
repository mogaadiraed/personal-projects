# How CarbonSync AI fits together

A map of the code for anyone opening the project for the first time: what runs when, which file does what, and
where every piece of data is kept. The [full project report](CARBONSYNC_REPORT.md) explains each page and figure;
the [thesis](CarbonSync_AI_final_report.pdf) explains the method and the model benchmark.

## The short version

The project has two halves.

- **The engine** (`src/carbonsync/`) is a plain Python package with no Streamlit in it. Its centre is
  `pipeline.run_pipeline()`, which takes the department CSV files in `data/inbox/` all the way to a forecast, alerts
  and reports, in seven steps, and stores everything in one DuckDB file (`data/carbonsync.duckdb`).
- **The dashboard** (`app/`) is a Streamlit app that only reads what the engine produced (through cached loaders),
  and calls the engine when the user asks for something new: an upload, a new forecast, a scenario, a question to
  the assistant.

```
department CSVs ──► data/inbox/ ──► run_pipeline() ──► data/carbonsync.duckdb ──► dashboard pages
   (upload page,                    1 validate & repair      raw_* tables           (app/pages/*.py,
    a .zip, or                      2 load                   emissions_daily         read through
    copied by hand)                 3 daily table + CO2e     forecasts               app/data/loader.py)
                                    4 forecast (router)      backtest_results
                                    5 backtest refresh     ─► data/alerts.json ───► alert button, Alerts page
                                    6 alerts               ─► data/reports/   ───► Excel workbook, PDF brief
                                    7 reports
```

## 1. Start-up

| Step | File | What happens |
|---|---|---|
| Launcher | `start.bat` / `start.sh` | Uses `.venv` if present, runs `streamlit run streamlit_app.py --server.port 8501` and opens the browser. `stop.bat` stops everything (dashboard, background work, local models). |
| Entry point | `streamlit_app.py` | Puts the project root and `src/` on the Python path, then calls `app.main.main()`. |
| Shell | `app/main.py` → `main()` | Page config and theme, then `auth.gate()` (sign-in, or the "create the administrator" screen on first start), the menu built for the user's role (`PAGE_ROLES`), the selected page, the footer, the floating AI button and the round alert button. |
| Pages | `app/pages/*.py` | One file per page (Overview, Forecast, Budget, Scenarios, Data intake, Data quality, Inventory, Methodology, Model lab, Assistant, AI & API pages, Configuration…). |
| Loaders | `app/data/loader.py` | Read-only, cached access to DuckDB and the JSON files. The cache is cleared after each pipeline run, so every page shows the new data. |
| Look | `app/ui/` | Palettes, glass / flat style, light / dark mode, charts and formatting. Settings are saved in `config.yaml` and the theme part of `.streamlit/config.toml`. |

## 2. The pipeline, step by step

`src/carbonsync/pipeline.py` → `run_pipeline(forecast=True, report=…, trigger=…)`. Command line:
`python -m carbonsync.pipeline` (options `--no-forecast`, `--no-report`, `--no-backtest`, `--backtest`).

| # | Module and function | What it does |
|---|---|---|
| 1 | `ingest/validate.py` → `validate_all_inbox()` | Reads every `data/inbox/*.csv`: detects encoding, delimiter and decimal separator, maps column aliases, normalises dates, removes duplicate days, converts unit slips (MWh → kWh), checks the Pandera schemas and writes clean Parquet files to `data/processed/`. A file with a missing required column or more than 10 % missing values is copied to `data/quarantine/` with an `.errors.json` explaining why. |
| 2 | `ingest/load.py` → `load_processed_to_duckdb()` | Loads the clean files into `raw_energy`, `raw_production`, `raw_logistics`, `raw_waste_water`. |
| 3 | `features/daily.py` → `build_daily_features()` | Joins the four sources into one row per day (`emissions_daily`), fills gaps (flagged as imputed) and converts to tCO₂e with the factors in `config.yaml`: electricity 0.483 kg/kWh, fuel oil and diesel 2.68 kg/L, waste 0.5 kg/kg, water 0.3 kg/m³. |
| 4 | `models/router.py` → `choose_model()`, `models/ensemble.py` → `run_and_save_forecast()` | Picks the model: **Chronos-2** when at least 60 days of history exist, otherwise **Prophet** (also used when Chronos-2 cannot run). Forecasts 183 days with a 90 % interval and saves the run in DuckDB. |
| 5 | `backtest/rolling.py` → `refresh_backtest()` | Rolling-origin backtest (12 origins, 6 / 12 / 24-month history windows) of Chronos-2, Prophet, Ens-PC and two references (seasonal naive and an oracle); the scores feed the Methodology and Model lab pages. |
| 6 | `alerts.py` → `check()` | Budget, year-end cap, days outside the forecast band, departments not sending data, router drift, quarantine → `data/alerts.json`. |
| 7 | `export/report.py`, `export/brief.py` | The Excel audit workbook and the 3-page PDF brief → `data/reports/`. |

### Where the models run

| File | Model |
|---|---|
| `models/chronos_model.py` | `get_chronos_pipeline()` loads `amazon/chronos-2` on the CPU once per process (offline from the Hugging Face cache, or downloaded on first use, about 460 MB) and `predict_df()` returns the 5 %, 50 % and 95 % quantiles. |
| `models/prophet_model.py` | Prophet with weekly seasonality (yearly once a year of history exists) and regressors (weekend, planned shutdown, solar and efficiency periods); a Fourier + Ridge fallback if Prophet fails. |
| `models/ensemble.py` | Ens-PC (an average of Prophet and Chronos-2) and the code that saves any run. |
| `models/lab.py` | The Model lab experiments (any model, any window). |

## 3. New data: the upload loop

`app/pages/data_intake.py` (menu **Data → Data intake**, editors and admins) accepts CSVs one by one, a `.zip`, or a
whole folder. `ingest/intake.py` does the work: `expand_archive()` unpacks a zip, `inspect_upload()` recognises the
department from the file-name prefix (`energie_`, `production_`, `logistique_`, `dechets_eau_`) or from the columns,
and `save_uploads()` writes the files to `data/inbox/` with a manifest so the upload can be undone. The page then calls
`run_pipeline(trigger="upload")` and streams its log live. A month reported by only some departments waits until all of them have sent it. Try it with `samples/2026-Q2.zip` (April–June 2026).

## 4. The AI assistant

All in `src/carbonsync/assistant/`.

| File | Role |
|---|---|
| `tools.py` | The 16 tools a model can call: `get_overview`, `query_emissions`, `compare_periods`, `get_forecast`, `list_forecast_runs`, `get_budget`, `get_data_quality`, `get_pipeline_activity`, `get_model_performance`, `get_methodology`, `get_inventory`, `get_breakdown`, `get_alerts`, `explain_dashboard`, `run_scenario`, `analyse`. They read DuckDB; the model never sees the database itself. |
| `analyst.py` | The built-in **Analyse** engine: a full analysis and action plan computed in Python, no AI model needed. |
| `engine.py` | `ask()`: the tool loop between the model and the tools, the expert rules for cloud models, the conversation turns. |
| `grounding.py` | The figure check: every number in an answer is compared with the tool results. |
| `providers/` | One adapter per engine: built-in, Ollama local, Ollama Cloud, OpenRouter (OpenAI-compatible), Claude. |
| `vault.py` | API keys in the operating system's credential store via `keyring` (service `CarbonSync AI`; Windows Credential Manager on Windows). An environment variable such as `ANTHROPIC_API_KEY` or `OLLAMA_API_KEY` is used when no key is stored. Keys are never written to a file. |
| `usage.py` | Cost per day and the daily cap (5 USD by default). |
| `knowledge.py`, `prompts.py` | Searches the page guide (`docs/assistant/guide_*.md`) and holds the system prompts. |
| `store.py`, `export.py` | Conversations in `data/assistant/chats/` (git-ignored) and their export to Markdown or PDF. |

**Privacy rule for cloud models.** A provider declares `privacy="full"` (local) or `"aggregated"` (cloud). With
`aggregated`, the tools only answer in whole months: daily granularity is raised to months, periods are widened to
whole months, and the highest day and anomaly days are left out. Daily values stay on the PC. Secrets are masked in
every log line (`obs.py`).

## 5. Where everything is kept

| Path | Content | In git |
|---|---|---|
| `config.yaml` | Emission factors, target and budget, model and router settings, appearance | yes |
| `.streamlit/config.toml` | Server settings; theme part rewritten from Settings → Appearance | yes |
| `data/inbox/` | Raw department CSVs (synthetic, with realistic defects) | yes |
| `data/truth/` | The clean ground truth the generator started from | yes |
| `data/processed/`, `data/quarantine/` | Repaired files, rejected files and their reasons | no (rebuilt) |
| `data/carbonsync.duckdb` | Every table the pages read | no (rebuilt) |
| `data/reports/`, `data/alerts.json` | Excel and PDF reports, current alerts | no (rebuilt) |
| `data/auth/users.json` | Accounts (PBKDF2 password hashes), sessions | no (created on first start) |
| `data/assistant/` | Conversations, usage, assistant settings | no (yours only) |
| Credential Manager | API keys | never in the project |

## 6. Rebuilding the synthetic data

`src/carbonsync/synth/generator.py` writes eight years (2018–2025) of daily data for a fictional wiring-harness plant
(seed 42, with a solar installation in 2021 and an efficiency programme in 2023), split into monthly department
files. `synth/messify.py` then adds realistic defects to about 10 % of the files (French Excel format, date variants, encodings, duplicate rows, renamed or missing columns, MWh instead of kWh, negative values, late corrections) so the
validation step has something to do. The first quarter of 2026 in `data/inbox/` is the content of
`samples/2026-Q1.zip`. `scripts/make_sample_uploads.py` regenerates the sample uploads.

## 7. Tests

`tests/` holds about 330 pytest tests (pipeline, models, budget, inventory, alerts, login, assistant tools and
privacy, every page rendered with Streamlit's AppTest). Run them after building the data:
`python -m pytest -o pythonpath="src ." tests/ -q`.
