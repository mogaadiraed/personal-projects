"""
CarbonSync AI – Streamlit App & Redesign Unit Tests
=====================================================
Verifies cached data loaders, view callables, benchmark metadata (R1),
scenario sensitivity ranges (R6), conservative budget bound method (R3),
and strict zero-sidebar enforcement.
"""
from __future__ import annotations

import json

from app.core import budget as bud
from app.data.loader import (
    get_duckdb_path,
    get_project_root,
    load_backtest_summary,
    load_config,
    load_daily_raw_features,
    load_data_quality_report,
    load_emissions_daily,
    load_forecast_runs,
    load_latest_forecast,
    load_monthly_scope_summary,
    load_quarantine_details,
)
from app.pages import (
    budget,
    configuration,
    data_quality,
    forecast,
    methodology,
    overview,
    reports,
    scenarios,
)
from carbonsync.scenarios.whatif import simulate_scenario


def test_data_loader_config_and_paths():
    """Verifies that config, localization, and database paths load properly."""
    cfg = load_config()
    assert isinstance(cfg, dict)
    assert "app" in cfg
    assert "emission_factors" in cfg
    assert "budget" in cfg
    assert "display" in cfg
    assert "governance" in cfg

    root = get_project_root()
    assert (root / "config.yaml").exists()

    db_path = get_duckdb_path()
    assert db_path.exists(), f"DuckDB file not found at {db_path}"


def test_data_loader_emissions_and_forecasts():
    """Verifies data loaders fetch non-empty DataFrames from DuckDB."""
    df_daily = load_emissions_daily()
    assert not df_daily.empty
    # 8 original years (2922 days) plus whatever was uploaded since, with no missing day
    assert len(df_daily) >= 2922
    assert len(df_daily) == (df_daily["date"].max() - df_daily["date"].min()).days + 1
    assert "co2e_total_t" in df_daily.columns
    assert "co2e_scope1_t" in df_daily.columns
    assert "co2e_scope2_t" in df_daily.columns

    df_raw = load_daily_raw_features()
    assert not df_raw.empty
    assert "is_imputed" in df_raw.columns

    runs = load_forecast_runs()
    assert not runs.empty
    assert "run_id" in runs.columns

    df_fc, run_meta = load_latest_forecast()
    assert not df_fc.empty
    assert len(df_fc) == 183
    assert "yhat" in df_fc.columns
    assert "lo90" in df_fc.columns
    assert "hi90" in df_fc.columns
    assert isinstance(run_meta, dict)

    df_monthly = load_monthly_scope_summary()
    assert not df_monthly.empty
    assert "co2e_scope1_t" in df_monthly.columns
    assert "month_dt" in df_monthly.columns


def test_data_loader_backtest_and_quality():
    """Verifies backtest summary and quarantine data loaders."""
    bt = load_backtest_summary()
    assert isinstance(bt, list)
    assert len(bt) > 0
    assert "mae" in bt[0]
    assert "mase" in bt[0]

    dq = load_data_quality_report()
    assert isinstance(dq, dict)
    assert dq.get("total_files_scanned", 0) > 0
    assert dq.get("accepted_count", 0) > 0

    quarantine = load_quarantine_details()
    assert isinstance(quarantine, list)
    # 12 since French-format files (decimal commas) are repaired instead of rejected
    assert len(quarantine) == 12
    assert "errors" in quarantine[0]
    assert "file_name" in quarantine[0]


def test_view_and_page_render_callables():
    """Verifies that every page module exposes a render callable."""
    pages = [
        overview.render,
        forecast.render,
        budget.render,
        scenarios.render,
        data_quality.render,
        methodology.render,
        reports.render,
        configuration.render,
    ]
    for page_fn in pages:
        assert callable(page_fn)


def test_whatif_integration_with_duckdb_forecast():
    """Tests what-if simulation using real forecast loaded from DuckDB."""
    df_fc, _ = load_latest_forecast()
    assert not df_fc.empty

    result = simulate_scenario(
        baseline_df=df_fc,
        solar_share_pct=25.0,
        efficiency_gain_pct=10.0,
        production_load_pct=-5.0,
    )

    assert result["summary"]["total_baseline_tco2e"] > 0
    assert result["summary"]["total_scenario_tco2e"] < result["summary"]["total_baseline_tco2e"]
    assert result["summary"]["net_delta_tco2e"] < 0
    assert result["summary"]["pct_change"] < 0
    assert len(result["scenario_df"]) == len(df_fc)


def test_benchmark_has_metadata():
    """R1 requirement: backtest_summary.json must contain a valid meta block."""
    root = get_project_root()
    summary_path = root / "data" / "backtest_summary.json"
    assert summary_path.exists()

    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("meta", {})
    assert {"commit", "seed", "protocol"} <= meta.keys()
    assert meta["seed"] == 42
    assert "rolling origin" in meta["protocol"]


def test_scenario_has_sensitivity_range():
    """R6 requirement: scenario simulations provide transparent guardrails."""
    df_fc, _ = load_latest_forecast()
    result = simulate_scenario(
        baseline_df=df_fc,
        solar_share_pct=15.0,
        efficiency_gain_pct=5.0,
        production_load_pct=0.0,
    )
    assert "guardrail_notice" in result
    assert "multipliers" in result
    assert "net_composite_multiplier" in result["multipliers"]
    # Directional abatement should be non-zero
    assert result["summary"]["net_delta_tco2e"] < 0


def test_budget_bounds_conservative_method():
    """R3 requirement: verifies conservative cumulative bound summation."""
    df_fc, _ = load_latest_forecast()
    assert not df_fc.empty
    # The upper bound sum must be strictly greater than the median forecast sum
    median_sum = df_fc["yhat"].sum()
    upper_sum = df_fc["hi90"].sum()
    assert upper_sum > median_sum


def test_no_sidebar_calls_in_codebase():
    """Strict redesign requirement: st.sidebar must never be invoked in app/."""
    root = get_project_root()
    app_dir = root / "app"

    violations = []
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        # Check for active st.sidebar calls (ignoring comments/strings)
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "st.sidebar" in stripped:
                violations.append(f"{py_file.name}:{idx} -> {stripped}")

    assert len(violations) == 0, f"Found st.sidebar calls: {violations}"


def test_entrypoint_exists_and_imports():
    """The single entrypoint must exist and app.main must expose main()."""
    from app.main import main

    assert (get_project_root() / "streamlit_app.py").exists()
    assert callable(main)


def test_windows_launchers_are_ascii_with_crlf():
    """start.bat / stop.bat / stop_dashboard.ps1: cmd.exe misreads labels in LF-only files, and
    Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page."""
    root = get_project_root()
    for name in ("start.bat", "stop.bat", "scripts/stop_dashboard.ps1"):
        data = (root / name).read_bytes()
        data.decode("ascii")
        assert data.count(b"\n") == data.count(b"\r\n") > 0, name
    assert "stop_dashboard.ps1" in (root / "stop.bat").read_text(encoding="ascii")


def test_budget_evaluation_status_rules():
    """Month evaluation: projected = spent + remaining forecast; status follows the bounds."""
    df = load_emissions_daily()
    fc, _ = load_latest_forecast()
    cfg = load_config()

    y, m = bud.current_period(df)
    mb = bud.evaluate_month(df, fc, cfg, y, m)
    assert mb.days_in_month == len(mb.cum_projected)
    assert mb.upper >= mb.projected >= mb.spent
    expected = "green" if mb.upper <= mb.ceiling else ("amber" if mb.projected <= mb.ceiling else "red")
    assert mb.status == expected

    # A fully observed month has no forecast component
    last = df["date"].max()
    full = bud.evaluate_month(df, fc, cfg, last.year, last.month)
    observed = df[(df["date"].dt.year == last.year) & (df["date"].dt.month == last.month)]["co2e_total_t"].sum()
    assert abs(full.spent - observed) < 1e-6
    assert abs(full.projected - full.spent) < 1e-6
