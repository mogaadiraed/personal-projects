"""
Unit Tests for CarbonSync What-If Simulator & Excel Reporting
=============================================================
"""

import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from carbonsync.export.report import generate_excel_report
from carbonsync.scenarios.whatif import (
    simulate_scenario,
)


@pytest.fixture(scope="module")
def project_root():
    return Path(__file__).resolve().parents[1]


def test_whatif_simulator_directional_logic():
    """Checks that solar & efficiency reduce emissions while production increases them."""
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    base_df = pd.DataFrame(
        {
            "date": dates,
            "horizon": list(range(1, 31)),
            "yhat": [10.0] * 30,
            "lo90": [8.0] * 30,
            "hi90": [12.0] * 30,
        }
    )

    # Solar lever (20%) -> emissions must decrease
    res_solar = simulate_scenario(base_df, solar_share_pct=20.0)
    assert res_solar["summary"]["net_delta_tco2e"] < 0, "Solar share must decrease emissions"
    assert res_solar["summary"]["pct_change"] < 0

    # Production load (+15%) -> emissions must increase
    res_prod = simulate_scenario(base_df, production_load_pct=15.0)
    assert res_prod["summary"]["net_delta_tco2e"] > 0, "Production increase must increase emissions"
    assert res_prod["summary"]["pct_change"] > 0

    # Combined levers
    res_comb = simulate_scenario(
        base_df,
        solar_share_pct=25.0,
        efficiency_gain_pct=10.0,
        production_load_pct=-5.0,
    )
    assert res_comb["summary"]["net_delta_tco2e"] < 0
    assert "scenario_yhat" in res_comb["scenario_df"].columns
    assert "delta_tco2e" in res_comb["scenario_df"].columns


def test_whatif_ground_truth_recovery_tolerance():
    """Verifies that simulator multipliers recover known synthetic effects within +-3% tolerance (Section 10.4)."""
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    base_df = pd.DataFrame(
        {
            "date": dates,
            "horizon": list(range(1, 11)),
            "yhat": [10.0] * 10,
            "lo90": [8.0] * 10,
            "hi90": [12.0] * 10,
        }
    )

    # Synthetic solar contract was designed at 15% electricity reduction
    # Scope 2 share is ~65% of emissions -> Expected total plant reduction: 15% * 0.65 = 9.75%
    shares = {"scope1_share": 0.25, "scope2_share": 0.65, "other_share": 0.10}
    res = simulate_scenario(base_df, solar_share_pct=15.0, scope_shares=shares)

    expected_plant_reduction_pct = 15.0 * 0.65  # 9.75%
    observed_reduction_pct = abs(res["summary"]["pct_change"])

    assert abs(observed_reduction_pct - expected_plant_reduction_pct) <= 3.0, (
        f"Expected recovery around {expected_plant_reduction_pct}%, observed {observed_reduction_pct}%"
    )


def test_excel_report_structure(tmp_path):
    """The audit workbook has its 13 sheets, charts, conclusions and cited factor sources (EN and FR)."""
    res_path = generate_excel_report(output_path=tmp_path / "report_en.xlsx", lang="en")
    assert res_path.exists(), "Report file must exist"
    wb = openpyxl.load_workbook(res_path)

    expected = ["Summary", "Annual", "Monthly", "Activity", "Forecast", "Budget", "GHG inventory", "Breakdown",
                "Alerts", "Models", "Data quality", "Methodology", "Log"]
    assert wb.sheetnames == expected
    for s in expected:
        assert wb[s].max_row >= 4, f"Sheet '{s}' has insufficient data rows"
    assert len(wb["Annual"]._charts) == 1 and len(wb["Monthly"]._charts) == 1

    summary_text = " ".join(str(c.value) for row in wb["Summary"].iter_rows() for c in row if c.value)
    assert "Conclusions" in summary_text and "Recommendations" in summary_text
    methodology_text = " ".join(str(c.value) for row in wb["Methodology"].iter_rows() for c in row if c.value)
    assert "Tunisian grid" in methodology_text and "kgCO₂e/kWh" in methodology_text
    inventory_text = " ".join(str(c.value) for row in wb["GHG inventory"].iter_rows() for c in row if c.value)
    assert "market-based" in inventory_text.lower() and "excluded" in inventory_text.lower()
    budget_text = " ".join(str(c.value) for row in wb["Budget"].iter_rows() for c in row if c.value)
    assert "2030" in budget_text

    fr = openpyxl.load_workbook(generate_excel_report(output_path=tmp_path / "report_fr.xlsx", lang="fr"))
    assert fr.sheetnames[0] == "Synthèse" and len(fr.sheetnames) == 13 and "Inventaire GES" in fr.sheetnames


def test_pdf_brief_and_insights(tmp_path):
    from carbonsync.export.brief import generate_pdf_brief
    from carbonsync.export.insights import build_insights

    ins = build_insights("en")
    assert len(ins["conclusions"]) >= 6 and ins["recommendations"]
    assert ins["annual"]["total"].sum() == pytest.approx(ins["totals"]["all"], rel=1e-9)
    assert len(ins["budget"]["table"]) == 12
    pdf = generate_pdf_brief(output_path=tmp_path / "brief.pdf", lang="en", insights=ins)
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page\b(?!s)", data)) == 3, "brief must be exactly 3 pages"
