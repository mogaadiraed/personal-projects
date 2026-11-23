# CarbonSync dashboard guide (assistant)

The assistant's knowledge base. Each `##` section is one topic; the `keywords` line helps search.

## CarbonSync at a glance
<!-- keywords: application, overview, navigation, menu, tabs, language, theme, dark, light, start, launch, sign in, login -->
CarbonSync AI tracks the CO₂ emissions of an industrial site (Scope 1, Scope 2 location- and market-based, a Scope 3 screening), forecasts the next 6 months, splits the emissions by plant zone and compares the trajectory with a reduction target (−30% by 2030 vs 2019 by default) and its yearly carbon budget. Data arrives as CSV files per department (energy, production, logistics, waste & water).

The top bar groups the pages: **Monitor** (Overview, Forecast, Breakdown), **Alerts**, **Assistant**, **Models** (Model lab), **Plan** (Carbon budget, Scenarios), **Data** (Data intake, Data quality), **Evidence** (GHG inventory, Methodology, Reports), **Settings** (Configuration). The bar stays fixed when you scroll. The pages shown depend on your role (viewer, editor, administrator).

At the top right of each page: the **FR / EN** switch and your **account** button: your team with who is **online or offline**, the **latest data files** received (department, rows, period, who sent them) and the last pipeline run, then **Log out**. The password is changed in Settings → Configuration → **Password**. At the bottom right, the round **alert button** sits above the AI button: **red** when a critical alert is open, amber for warnings, with the count. The look (palette, glass effect, light / dark mode, text size) is set in Settings → Configuration → Appearance.

Start: double-click `start.bat`; the browser opens by itself and the window shows the activity (pipeline, forecasts, assistant). Stop: `stop.bat` stops the dashboard, its background work and the local models, and closes Ollama (`stop.bat keep-ollama` leaves Ollama open). By hand: `streamlit run streamlit_app.py`.

## Overview
<!-- keywords: overview, home, kpi, last 12 months, trajectory, all, full history, scroll, scope split, monthly, month budget, market-based, cap, open alerts -->
Home page. First row: emissions over the **last 12 months** (and year-on-year change), **this month's budget** (status vs ceiling), the **next 30 days** forecast, the **Scope 2 share**. Second row: **Scope 2 location-based** and **market-based** (12 months, share of kWh under contract), **this year's cap** read on the target path, and the **open alerts**.

**Emissions trajectory**: history + forecast with the 90% interval. Buttons 90 d / 180 d / 365 d / **All**. "All" shows the 3,000+ days: the chart opens on the last year and you drag it (or the bar below it) to go back to any date; in-chart shortcuts 6 m / 1 y / 3 y / All. Off-scale anomaly spikes are marked ▲ (hover = real value). The on-site solar and efficiency programme milestones are shown.

**Scope split** (last 12 months) and **Monthly emissions** (last 24 months, stacked by scope).

## Forecast
<!-- keywords: forecast, horizon, 183 days, interval, peak, model, compare, new forecast, run, backtest, mase -->
Active forecast over **183 days** with a 90% confidence interval. KPIs: horizon total, daily mean, forecast peak, mean interval width.

**Forecast and recent history** chart (day or week step), **10 highest-emitting days**, **Latest run of each model** (pick models to compare, show intervals), **Backtest accuracy** (MASE < 1 beats the naive baseline).

**Run a new forecast** (button at the bottom): model Auto (router) / Ens-PC / Chronos-2 / Prophet, training history 180 / 365 / 730 / 1,095 days. Chronos-2 takes 1–2 min on CPU. The new forecast becomes the active one.

## Model lab
<!-- keywords: model lab, experiment, train, test, horizon, 3 months, 6 months, 12 months, training period, backtest, future, prophet, chronos, ens-pc, compare -->
To test each model on its own. **Experiment** settings: models (Prophet, Chronos-2, Ens-PC), **horizon** 3 / 6 / 12 months, **training period** 6 / 12 / 24 months or the whole history, **mode** "Backtest on the past" (the forecast is scored against real data from a cut-off date) or "Forecast the future". Button **Run experiment**.

Results: forecasts vs actuals, mean absolute error by forecast week, **score table** (MAE, MASE, 90% coverage, bias) with the seasonal naive baseline, **session experiments** list (Clear button), **latest run log**, and the "how it works" view (Prophet components: trend, seasonalities).

Experiments never change the active forecast.

## Carbon budget
<!-- keywords: budget, ceiling, cap, monthly, annual, over budget, status, green, amber, red, consumed, projection, end of month, landing, target, path, trajectory, base year, 2030, -30%, reduction, seasonal weights, on track -->
Actuals + forecast against the **monthly ceiling**. Pick the **year** and **month**. KPIs: month ceiling, consumed, end-of-month projection, 90% upper bound. **Cumulative consumption this month** chart and **Year** view (estimated landing vs the annual cap).

**Target and path**: a base year (2019 by default, 3,397 tCO₂e), a target year (2030) and a reduction (−30%, i.e. 2,378 tCO₂e). The yearly path is a straight line from the base to the target; **each year's cap is read on that path** (2026: 2,749 tCO₂e). The chart shows the path, the actual years and this year's projection; the table says for each year whether it was on track. A **fixed cap** can be chosen instead (Configuration → Target & budget).

**Monthly weights**: by default the site's **seasonal profile** (share of each month in the last 3 complete years: August is lower because of the summer closure); they can also be entered by hand (total 1.00). Month ceiling = year cap × month weight.

Month status: **green** if the 90% upper bound stays under the ceiling, **amber** if only the central projection does, **red** otherwise. Months beyond the forecast horizon are not estimated on this page. The perimeter of the budget is Scope 1 + Scope 2 location-based + waste and water.

## Alerts
<!-- keywords: alerts, alert, bell, badge, notification, warning, critical, amber, red, anomaly, outside the band, 90% band, stale, no data, department, router drift, quarantine, check now, email -->
The round **alert button** at the bottom right (above the AI button) shows the number of open alerts; it turns **red** when one is critical. A click lists them (critical ones on dark red) with **See the critical alerts** and **Open the Alerts page**. On the Alerts page, critical alerts have a **dark red** background, warnings an amber one, and a filter shows All / Critical / Warnings / Info. The pipeline checks them after every run; editors can also press **Check now**.

Checks: **budget** (a month whose projection passes its ceiling: amber within 10%, red above), **year-end** (full-year projection above the year's cap on the path), **anomalies** (actual days outside the forecast's 90% band over the last 90 days), **data** (a department that has sent nothing for 45 days), **router** (the router's model clearly worse than the best one in the backtest), **quarantine** (files to fix). Each card says what happened and links to the page to open. Resolved alerts move to the history. The chart shows the actual days against the 90% band.

Thresholds: Configuration → Reports & alerts. Email sending is optional and not configured.

## Breakdown by zone
<!-- keywords: breakdown, zone, plant, workshop, p1, p2, p3, cutting, preparation, assembly, utilities, logistics, item, subcategory, compressors, ovens, forklifts, allocation keys, sub-meters, owner -->
Emissions by **plant zone**: **P1 Cutting**, **P2 Preparation**, **P3 Assembly**, **UT Utilities and buildings**, **LOG Logistics**, and by **item** (cutting machines, compressors, ovens, assembly lines, air conditioning, boiler, forklifts, delivery trucks…). Pick the period; KPIs, bars by zone and by item, monthly view by zone (24 months) and the detail table with each zone's owner.

Method: each site meter (electricity, fuel oil, diesel, waste, water) is split with the **allocation keys** of config.yaml (breakdown); the zones always add up to the site total. The keys are estimates until sub-meters exist; they are edited in Configuration → Inventory & breakdown.

## GHG inventory
<!-- keywords: inventory, ghg inventory, esrs e1-6, scope 2 market-based, location-based, residual mix, ppa, green tariff, certificates, contract, scope 3, screening, category, purchased goods, copper, pvc, commuting, transport, calculated, estimated, excluded -->
The full GHG inventory over a period: **Scope 1**, **Scope 2 location-based** and **market-based**, **Scope 3**, and the totals for both Scope 2 methods (ESRS E1-6 table). Scope 2 by year and the table of contractual instruments.

**Market-based Scope 2**: the kWh covered by a contract (PPA, green tariff, certificates) take the contract's factor; the rest takes the residual mix. No contract is recorded yet and Tunisia has no published residual mix, so the grid average is used as a proxy and both methods give the same figure.

**Scope 3 screening**: each of the 15 categories is marked **calculated** (site data, e.g. waste, water), **estimated** (sourced assumptions: purchased copper and PVC per unit produced, upstream fuel and electricity, inbound and outbound transport, staff commuting) or **excluded** (with the reason). Orders of magnitude to set priorities, to replace with actual purchases and a commuting survey. Assumptions: Configuration → Inventory & breakdown.

## Login and roles
<!-- keywords: login, sign in, log out, password, account, user, role, viewer, editor, administrator, admin, first start, locked, unlock, forgot password, session, cookie, permissions -->
On the first start the dashboard asks to create the **administrator** account. Afterwards everyone signs in with a user name and password.

Roles: **viewer** (reads every monitoring page, budget, scenarios, reports, assistant; changes only their own password in Configuration → Password), **editor** (also uploads data, runs forecasts and the model lab, generates reports, checks alerts, edits target, inventory, breakdown, factors and reports settings), **administrator** (also appearance, layout, system, users, AI & API pages and resetting the data). Buttons a role cannot use are greyed out with a tooltip.

Security: passwords are stored only as PBKDF2-SHA256 hashes (600,000 iterations) in data/auth/users.json (never committed); a session lasts 12 hours; 5 wrong passwords lock the account for 15 minutes. Change your own password: Configuration → **Password** (your other sessions are then closed). Forgotten password: an administrator resets it (Configuration → Users or `python -m carbonsync.auth passwd <user>`). The login can be switched off with `auth: enabled: false` in config.yaml (everyone is then an administrator).

## Scenarios
<!-- keywords: scenario, what-if, lever, solar, self-consumption, efficiency, production, simulation, avoided emissions, save -->
Tests how levers change the next 6 months (active forecast as baseline). Sliders: **solar self-consumption** 0–40%, **energy efficiency** 0–15%, **production volume** −20 to +20%.

Results: 183-day baseline, scenario, **avoided emissions**, per day, contribution of each lever, baseline vs scenario chart, **saved scenarios**.

Simulator rules: solar and efficiency cut electricity (Scope 2); efficiency also cuts fuels by half; production has a 0.75 elasticity on emissions.

## Data intake
<!-- keywords: upload, add, csv, new month, quarter, zip, folder, department, pipeline, rerun, reset, original data, csv template, samples -->
To add data. One tile per department shows the last day received and its state. You can drop:

- one CSV **in a department's zone** (energy, production, logistics, waste & water);
- **everything at once**: several CSVs, a **.zip** archive or a **folder**; the department is detected from the file name or its columns.

After an upload the **whole pipeline** reruns: validation → loading → daily table → forecast (router: Chronos-2) → backtest refresh → alerts check → Excel and PDF reports. Every page updates. A month is included only once **all 4 departments** have sent it; until then it stays "waiting".

Other actions: **CSV template** (download one per department), **Rerun without a new file**, "Generate the Excel report" switch, **Pipeline history**, **Coverage by department** (last 18 months), **Back to the original data** (removes uploaded files and restores the originals, with confirmation).

Ready-made sample files: the project's `samples/` folder (`2026-Q1.zip` already uploaded, `2026-Q2.zip` = April–June 2026).

## Data quality
<!-- keywords: quality, quarantine, rejected, accepted, repaired, imputed, imputation, confidence, schema, columns, error, file -->
What came in, what was fixed, what was rejected. KPIs: **files accepted**, **quarantined**, **imputed days**, **confidence score** (/100).

**Rejection reasons**, **quarantine log** (filter by department) with each file's reason and **suggested fix**, and **expected schemas** (required columns per department).

Automatic repairs: delimiters, French formats (decimal comma, dd/mm/yyyy dates), units, column aliases. A file that cannot be repaired is quarantined: fix it and upload it again in Data intake.

## Methodology
<!-- keywords: methodology, backtest, accuracy, mae, mase, coverage, window, best model, evaluation -->
**Rolling backtest** results: 6, 12 and 24 months of history, 183-day horizon. KPIs: best model, mean error (MAE), best 90% coverage, number of evaluations. Charts: MAE by history window, empirical coverage of the 90% interval, error relative to the naive baseline.

## Reports
<!-- keywords: report, excel, pdf, summary, download, generate, audit, csrd, esrs, conclusions, recommendations, export, csv -->
Automatic conclusions and recommendations, then two documents:

- **Full audit workbook** (Excel, 10 sheets: summary, annual, monthly, activity, forecast, budget, models, quality, methodology, log);
- **Summary report** (PDF, 2 pages: key figures, charts, conclusions, budget, forecast, models, recommendations).

Press **Generate** then **Download**; earlier reports stay available ("Generated reports"). Downloadable datasets: daily emissions, monthly summary, annual summary, active forecast (CSV), backtest (JSON). **ESRS E1-6 readiness** table.

## Configuration
<!-- keywords: settings, configuration, parameters, appearance, theme, palette, colour, color, glass, flat, animated background, wallpaper, dark, light, text size, density, corners, animations, layout, menu, hide a page, start page, assistant button, footer, width, cap, annual budget, monthly weights, factors, reports, site name, report language, default language, data mode, synthetic, production, export, import, defaults, target, base year, residual mix, instruments, ppa, scope 3 assumptions, allocation keys, zones, users, accounts, roles, router, alerts thresholds -->
Up to eight tabs, depending on your role (editors: Target & budget, Inventory & breakdown, Factors, Reports & alerts; administrators: all):
- **Appearance** (admin): palette (Emerald, Ocean, Indigo, Amber, Coral, Graphite, or **Custom** with any accent colour), **Glass** effect or **Flat**, Auto / Light / Dark mode, text size S / M / L, density, corners, animations, **animated background**. Contrast is checked and every palette is adjusted to stay readable. **Apply** changes every page without a restart. Chart colours stay fixed (Scope 1 orange, Scope 2 blue…).
- **Layout** (admin): pages shown in the menu (Overview, Alerts, Assistant and Configuration are always shown), start page, page width, floating assistant button, footer.
- **Target & budget**: base year, target year, reduction %, each year's cap (read on the path or fixed), monthly weights (seasonal profile or entered by hand). The resulting cap of the year is shown before saving.
- **Inventory & breakdown**: market-based Scope 2 (residual mix and its source, contractual instruments: PPA, green tariff, certificates, with dates and share of kWh or MWh/year); Scope 3 assumptions (materials per unit and factors, commuting, upstream energy, transport, reasons of excluded categories); breakdown zones and allocation keys (the shares of each meter must total 1).
- **Factors**: emission factors with unit, scope and source, review date and its age. Read-only (changed in config.yaml, then the pipeline is rerun).
- **Reports & alerts**: site name, language of the Excel and PDF reports; alert thresholds (no data for N days, anomaly window, router margin). Email sending is not configured.
- **Users** (admin): create accounts (viewer, editor, administrator), change a role, reset a password, unlock, delete.
- **System** (admin): default language, data mode, engine figures, **router and backtest** (model Chronos-2, fallback Prophet, minimum history 60 days, backtest after each upload), **Export / import** the settings, **Reset to defaults**, change history.

Every save creates a timestamped audit copy of the configuration.

## AI & API
<!-- keywords: api key, key, ai & api, provider, ollama, ollama cloud, openrouter, free, free models, nemotron, gemma, ling, free limit, quota, fallback, data collection, gpu, free the gpu, video memory, stop models, start, unload, deepseek, kimi, glm, mistral, claude, anthropic, expert, expert mode, cloud, credential manager, keyring, cost, spending, spending cap, tokens, model, top 5, quick test, balance, credit, opus, sonnet, haiku, effort, test connection, delete key, data sent, privacy, expert brief, local model, context -->
Page group **AI & API**: the assistant's engines. Each cloud provider has its own page, with its API key. Keys are kept in the **Windows Credential Manager**, never in config.yaml, logs, exports or conversations, and are only shown masked (sk-ant-…a1b2, sk-or-…a1b2, or …a1b2 for a key without a prefix). Paste a key in the hidden field, then **Save the key**; **Test the connection** checks it without using tokens; **Delete the key** removes it.

**AI overview**: one tile per provider (local Ollama, Ollama Cloud, Claude, OpenRouter; OpenAI, Gemini, Mistral and "OpenAI-compatible" coming soon) with its status, model and today's spend; the **daily spending cap**, shared by every cloud provider (0 = no cap), and the last 30 days; the **data sent to the cloud**; the **expert brief** (docs/assistant/expert_brief.md), read by every cloud model at the start of every conversation. Once the cap is reached, cloud models pause until the next day.

**Ollama**: the **Ollama Cloud** key (created at ollama.com/settings/keys; an OLLAMA_API_KEY variable is used when no key is stored). The test reads the account balance. The **5 best cloud models** for the assistant, with their prices: DeepSeek V4 Pro (recommended), Kimi K3.1 Pro, GLM 5.3, Mistral Large 4, DeepSeek V4.1 Flash (fast, low cost); **Choose** makes one the assistant's model. **Other model**: any other Ollama cloud model. **Quick test**: a one-word question to the chosen model, to check it answers (cost: a fraction of a cent). Ollama Cloud is paid per token (a small free amount each month on some models, then credits or a plan). Below, **local Ollama**: models installed on this PC, GPU share, default local model and context size (8k, 16k recommended, 32k). **Stop local models** removes every model from memory to **free the GPU** (games, rendering, other software); the Ollama server stays open without using the GPU, and the assistant no longer loads the model by itself. **Start** loads the default model right away (about 10 s), and opens Ollama if it was closed; the first question with a local model loads it too. A local model leaves memory by itself 20 min after the last question.

**OpenRouter**: **free models** from several publishers with one key (created at openrouter.ai/settings/keys, starts with sk-or-v1-; or the OPENROUTER_API_KEY variable). The test reads the key and **today's free quota** without a model request. The **5 best free models** for the assistant, all with tools and reasoning: NVIDIA Nemotron 3 Ultra (recommended), Google Gemma 4 31B, NVIDIA Nemotron 3.5 Super VL, inclusionAI Ling 3.2 Flash, Google Gemma 4 26B A4B (fast); **Choose** makes one the assistant's model. **Other free model**: any other free model with tools; **Refresh the list** reads OpenRouter's public list again. **Free limits**: 20 requests per minute, 50 per day (1,000 per day once 10 credits have been bought); a question takes 2 to 4 requests. **Automatic fallback**: if the chosen model is busy, OpenRouter moves to another free model (no cost; the Sources panel shows which model answered). **Deny data collection**: only uses providers that do not keep requests (some free models become unavailable). OpenRouter does not keep requests by default, but the provider of a free model may log them; only aggregated data is sent.

**Claude**: the Anthropic key (Claude console, platform.claude.com → API Keys; or the ANTHROPIC_API_KEY variable), the model (Opus 5.5, Sonnet 5.5, Haiku 5.5) and the **reasoning effort** (low, medium recommended, high). Automatic fallback on Opus and Sonnet: a request declined for policy reasons is retried on another Claude model.

**Data sent to the cloud**: the question and the Expert conversation, the expert brief, the context (dates, cap, factors, active forecast) and tool results aggregated by month or coarser (periods widened to whole months). Never: daily rows, CSV files, results read with a local model, your identity, your keys.

## The data pipeline
<!-- keywords: pipeline, steps, loop, validation, loading, duckdb, daily table, processing, log, terminal, backtest refresh, alerts check -->
Steps: 1) **validation** of the CSVs (schemas, repairs, quarantine); 2) **loading** into DuckDB (raw tables per department); 3) **daily table**: one row per day, missing days imputed and flagged, a month enters only when every department has sent it; 4) **forecast** with the router (Chronos-2); 5) **backtest refresh** when new data arrived (rolling windows, skipped when already up to date); 6) **alerts check**; 7) **reports** Excel + PDF. Each run is logged (pipeline history) and visible in the command window.

Command line: `python -m carbonsync.pipeline` (options `--no-forecast`, `--no-report`, `--no-backtest`, `--backtest` to force it). Alerts only: `python -m carbonsync.alerts`.

## Forecast models and router
<!-- keywords: model, prophet, chronos, ens-pc, ensemble, router, rule, 60 days, default model, naive, foundation, interval, mae, mase, coverage, bias, backtest after upload -->
- **Chronos-2**: a time-series foundation model (Amazon), used without specific training (zero-shot). **Default model.**
- **Prophet**: trend + weekly and yearly seasonality + regressors; the fallback.
- **Ens-PC**: average of Prophet and Chronos-2; available on demand (Forecast → Run a new forecast).
- **Seasonal naive baseline**: repeats the last week; the yardstick.

**Router** (production rule): **Chronos-2 from 60 days of history**, otherwise Prophet; Prophet also if Chronos-2 fails. Why: in the backtest Chronos-2 has the lowest error and the 90% coverage closest to 90%. The backtest is recomputed after each upload, and an alert is raised if the router's model becomes clearly worse than the best one. Settings: Configuration → System → Router and backtest.

Metrics: **MAE** (mean absolute error, t/day), **MASE** (MAE divided by the naive baseline's; < 1 = better), **90% coverage** (share of real days inside the interval; ideal ≈ 90%), **bias** (average over- or under-forecast).

## Scopes and emission factors
<!-- keywords: scope 1, scope 2, scope 3, other, factor, emission, kgco2, electricity, fuel oil, diesel, waste, water, ghg protocol, calculation, location-based, market-based, residual mix -->
Emissions = activity × emission factor.

- **Scope 1** (direct combustion): heavy fuel oil 2.68 kgCO₂e/L, logistics diesel 2.68 kgCO₂e/L.
- **Scope 2** (purchased electricity), two methods: **location-based** = kWh bought × Tunisian grid 0.483 kgCO₂e/kWh; **market-based** = kWh covered by a contract × the contract's factor + the other kWh × the residual mix. The on-site solar is behind the meter: it already lowers the kWh bought, in both methods.
- **Other** (in the budget): solid waste 0.5 kgCO₂e/kg, process water 0.3 kgCO₂e/m³. In the GHG inventory they are Scope 3 categories 5 and 1.
- **Scope 3**: screening estimates (see GHG inventory).

Frameworks: GHG Protocol (incl. Scope 2 Guidance), CSRD / ESRS E1. Factors and their review date are in Configuration → Factors and in Methodology.

## Imputation and quarantine
<!-- keywords: imputed, imputation, missing, gap, quarantine, rejected, waiting, incomplete month -->
An **imputed day** is a missing day estimated from neighbouring days; it is flagged (is_imputed column) and counted in Data quality. A **quarantined** file could not be repaired and is excluded until fixed. A **waiting** month has been received from at least one department but not all four; it enters the calculations only once complete.

## Frequently asked questions
<!-- keywords: how, faq, add three months, new quarter, download report, change cap, new forecast, reset, why, help, add a user, forgot password, change target -->
- **Add 3 months of data**: Data → Data intake → "Upload everything at once" → drop the .zip (e.g. `samples/2026-Q2.zip`). The pipeline reruns by itself (forecast, backtest, alerts, reports).
- **Go back to the original data** (administrator): Data intake → "Back to the original data" → Confirm.
- **Recompute the forecast**: Forecast → "Run a new forecast".
- **Change the target or the cap**: Settings → Configuration → Target & budget → "Save target and budget".
- **Record a green electricity contract**: Configuration → Inventory & breakdown → Market-based Scope 2 → add a row → "Save Scope 2".
- **Add a user / reset a password**: Configuration → Users (administrator), or `python -m carbonsync.auth add <name> --role editor`.
- **Change the colours or switch to light mode**: Settings → Configuration → Appearance → "Apply".
- **Hide a page from the menu**: Settings → Configuration → Layout → "Save layout".
- **Download a report**: Evidence → Reports → Generate → Download.
- **Test a model on another period**: Models → Model lab.
- **See the full history**: Overview → Trajectory → All.
- **Why a month is "waiting"**: one or more departments have not sent it yet.

## The assistant
<!-- keywords: assistant, chatbot, ai, expert, analyse, analysis, local, cloud, api key, privacy, sources, ollama, qwen, engine, reasoning, history, figure not found, check, export, export the conversation, action plan, markdown, pdf -->
**Assistant** page (top bar, after Monitor). The assistant answers from the dashboard's data through read-only tools: it never changes data or settings. Every answer shows the tools used, charts built from their results and a **Sources and details** panel (engine, data date, forecast used, time, tokens).

**Engine**: a local model through Ollama on the graphics card (Qwen 3.5 9B recommended, Qwen 3.5 4B faster; nothing leaves the PC), or the **built-in assistant** (no model: guided questions, exact figures), which also takes over when Ollama is not running. The first message loads the model into memory (about 10 s).

**Reasoning**: the model thinks before answering (more accurate, slower). **Analysis**: quantified diagnosis over 1, 3, 6 or 12 months (hotspots, LMDI explanation, annual projection vs cap, required reductions, levers) and an action plan. **History**: reopen or delete a conversation (saved on this PC). **New chat**: start over. **Export**: download the whole conversation, or only the last answer (for example the action plan), as **Markdown** or **PDF**, with each answer's sources (tools read, data date, forecast, engine, figures to check).

**Figures not found**: every number in the answer is compared with the data read; those that are not in it (computed or assumed by the model) are listed under the answer, to be checked.

**Expert mode**: a cloud model with your own API key (DeepSeek, Kimi, GLM or Mistral through Ollama Cloud, OpenRouter's free models such as Nemotron or Gemma, or Anthropic's Claude), chosen in **Engine** once the key is stored in AI & API → Ollama, OpenRouter or Claude. It first reads the expert brief (role of industrial decarbonisation analyst, site, data, metrics, tools, rules) and only receives **aggregated data**: monthly or coarser totals, never daily rows or files. Switching between local and Expert mode starts a new conversation. The Sources panel shows the estimated cost of each answer. If the key is missing, today's spending cap is reached or the API does not answer, the built-in assistant answers instead.

## The floating assistant
<!-- keywords: round button, floating button, bubble, icon, green circle, bottom right, every page, without leaving the page, open the assistant, close, panel, shortcut, this page, explain this page, assistant tab -->
A **round green button** (AI icon) at the bottom right of every page opens the assistant in a panel over the page: ask a question without leaving the page. The panel knows the open page: the first suggestion **explains this page**, the next ones are about it (budget, forecast, quarantine…), and the page goes with the question sent to the model. It is **the same conversation and engine** as the Assistant tab: a question asked here shows up there. At the top of the panel: new chat and **Open in the Assistant tab** (history, reasoning, analysis). **Engine and export**: change engine, export to Markdown or PDF. The panel shows the last 4 questions; the button (cross) closes it. It does not appear on the Assistant tab itself.
