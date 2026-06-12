"""Shared feature engineering for ensemble models."""

from typing import List, Tuple

import numpy as np
import pandas as pd

try:
    from services.shared.telemetry_contract import is_analytics_business_feature
except ModuleNotFoundError:  # pragma: no cover - unit-test path fallback
    import sys
    from pathlib import Path

    services_root = Path(__file__).resolve().parents[5]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))
    from shared.telemetry_contract import is_analytics_business_feature

ROLLING_WINDOWS = [5, 15, 30, 60, 360]

EXCLUDE_COLS = {
    "timestamp",
    "_time",
    "device_id",
    "device",
    "host",
    "table",
    "_start",
    "_stop",
    "_field",
    "_measurement",
}


class FeatureEngineer:
    """Constructs deterministic feature sets shared by all failure models."""

    def get_numeric_cols(self, df: pd.DataFrame) -> List[str]:
        return [
            c
            for c in df.columns
            if c not in EXCLUDE_COLS
            and is_analytics_business_feature(c)
            and pd.api.types.is_numeric_dtype(df[c])
        ]

    def compute_all_features(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        numeric_cols = self.get_numeric_cols(df)
        frames = []
        feature_names: List[str] = []

        for col in numeric_cols:
            s = pd.to_numeric(df[col], errors="coerce")

            for w in ROLLING_WINDOWS:
                r = s.rolling(w, min_periods=max(1, w // 2))
                frames += [
                    r.mean().rename(f"{col}_mean_{w}m"),
                    r.std().rename(f"{col}_std_{w}m"),
                    r.max().rename(f"{col}_max_{w}m"),
                    r.min().rename(f"{col}_min_{w}m"),
                    s.diff(w).rename(f"{col}_roc_{w}m"),
                    s.diff(w).abs().rename(f"{col}_roc_abs_{w}m"),
                ]
                feature_names += [
                    f"{col}_mean_{w}m",
                    f"{col}_std_{w}m",
                    f"{col}_max_{w}m",
                    f"{col}_min_{w}m",
                    f"{col}_roc_{w}m",
                    f"{col}_roc_abs_{w}m",
                ]

            m60 = s.rolling(60, min_periods=5).mean()
            s60 = s.rolling(60, min_periods=5).std()
            cv = (s60 / (m60.abs() + 1e-9)).clip(0, 10)
            frames.append(cv.rename(f"{col}_cv_60m"))
            feature_names.append(f"{col}_cv_60m")

            p95 = s.quantile(0.95)
            p05 = s.quantile(0.05)
            frames.append((s > p95).astype(float).rename(f"{col}_above_p95"))
            frames.append((s < p05).astype(float).rename(f"{col}_below_p05"))
            feature_names += [f"{col}_above_p95", f"{col}_below_p05"]

            def _slope(x):
                if len(x) < 3:
                    return 0.0
                return float(np.polyfit(range(len(x)), x, 1)[0])

            trend = s.rolling(60, min_periods=10).apply(_slope, raw=True)
            frames.append(trend.rename(f"{col}_trend_60m"))
            feature_names.append(f"{col}_trend_60m")

        frames += [
            pd.Series(df.index.hour, index=df.index, name="hour_of_day"),
            pd.Series(df.index.dayofweek, index=df.index, name="day_of_week"),
            pd.Series(
                ((df.index.hour >= 22) | (df.index.hour < 6)).astype(float),
                index=df.index,
                name="is_night_shift",
            ),
        ]
        feature_names += ["hour_of_day", "day_of_week", "is_night_shift"]

        feature_df = pd.concat(frames, axis=1)
        feature_df = feature_df.ffill().fillna(0).replace([np.inf, -np.inf], 0)

        return feature_df, feature_names
