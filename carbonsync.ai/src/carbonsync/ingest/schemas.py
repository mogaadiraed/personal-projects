"""
CarbonSync AI – Pandera Ingestion Schemas
==========================================
Declarative schemas for each department CSV:
  - EnergySchema (electricity_kwh, fuel_oil_l)
  - ProductionSchema (units_produced, operating_hours, shutdown_flag)
  - LogisticsSchema (trips, diesel_l)
  - WasteWaterSchema (waste_kg, water_m3)
"""

from __future__ import annotations

import pandera.pandas as pa

# 1. Energy Schema
EnergySchema = pa.DataFrameSchema(
    {
        "date": pa.Column("datetime64[ns]", nullable=False),
        "electricity_kwh": pa.Column(float, pa.Check.in_range(0.0, 250_000.0), nullable=True),
        "fuel_oil_l": pa.Column(float, pa.Check.in_range(0.0, 50_000.0), nullable=True),
    },
    strict=True,
    coerce=True,
)

# 2. Production Schema
ProductionSchema = pa.DataFrameSchema(
    {
        "date": pa.Column("datetime64[ns]", nullable=False),
        "units_produced": pa.Column(float, pa.Check.in_range(0.0, 50_000.0), nullable=True),
        "operating_hours": pa.Column(float, pa.Check.in_range(0.0, 24.5), nullable=True),
        "shutdown_flag": pa.Column(int, pa.Check.isin([0, 1]), nullable=False),
    },
    strict=True,
    coerce=True,
)

# 3. Logistics Schema
LogisticsSchema = pa.DataFrameSchema(
    {
        "date": pa.Column("datetime64[ns]", nullable=False),
        "trips": pa.Column(float, pa.Check.in_range(0.0, 500.0), nullable=True),
        "diesel_l": pa.Column(float, pa.Check.in_range(0.0, 10_000.0), nullable=True),
    },
    strict=True,
    coerce=True,
)

# 4. Waste & Water Schema
WasteWaterSchema = pa.DataFrameSchema(
    {
        "date": pa.Column("datetime64[ns]", nullable=False),
        "waste_kg": pa.Column(float, pa.Check.in_range(0.0, 25_000.0), nullable=True),
        "water_m3": pa.Column(float, pa.Check.in_range(0.0, 5_000.0), nullable=True),
    },
    strict=True,
    coerce=True,
)

# Mapping from department name / prefix to schema
SCHEMAS = {
    "energie": EnergySchema,
    "production": ProductionSchema,
    "logistique": LogisticsSchema,
    "dechets_eau": WasteWaterSchema,
}

EXPECTED_COLUMNS = {
    "energie": ["date", "electricity_kwh", "fuel_oil_l"],
    "production": ["date", "units_produced", "operating_hours", "shutdown_flag"],
    "logistique": ["date", "trips", "diesel_l"],
    "dechets_eau": ["date", "waste_kg", "water_m3"],
}
