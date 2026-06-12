from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = ROOT.parent
sys.path = [p for p in sys.path if p not in {str(ROOT), str(SERVICES_ROOT)}]
sys.path.insert(0, str(SERVICES_ROOT))
sys.path.insert(0, str(ROOT))

from src.services.analytics.feature_engineering import FeatureEngineer


def test_feature_engineer_uses_business_power_and_pf_magnitude():
    df = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
                "power": -1200.0,
                "current": -5.0,
                "voltage": 230.0,
                "power_factor": -0.9,
                "polarity_mode": "inverted",
                "energy_flow_mode": "consumption_only",
            }
        ]
    )

    engineered = FeatureEngineer().engineer_features(df, ["power", "current", "voltage", "power_factor"])

    assert float(engineered.loc[0, "power"]) == 1200.0
    assert float(engineered.loc[0, "current"]) == 5.0
    assert float(engineered.loc[0, "power_factor"]) == 0.9


def test_feature_engineer_excludes_phase_diagnostics_from_business_features():
    df = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
                "power": 1200.0,
                "current": 10.0,
                "voltage": 230.0,
                "power_factor": 0.9,
                "current_l1": 999.0,
                "current_l2": 0.0,
                "current_l3": -999.0,
                "voltage_l1": 500.0,
                "voltage_l2": 0.0,
                "voltage_l3": -100.0,
            }
        ]
    )

    engineered = FeatureEngineer().engineer_features(
        df,
        ["power", "current", "voltage", "current_l1", "current_l2", "voltage_l1"],
    )

    assert "current_rolling_mean" in engineered.columns
    assert "voltage_rolling_mean" in engineered.columns
    assert "current_l1_rolling_mean" not in engineered.columns
    assert "current_l2_rolling_mean" not in engineered.columns
    assert "voltage_l1_rolling_mean" not in engineered.columns
    assert float(engineered.loc[0, "current"]) == 10.0
    assert float(engineered.loc[0, "voltage"]) == 230.0
