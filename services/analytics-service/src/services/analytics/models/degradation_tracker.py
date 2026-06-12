"""Physics-inspired degradation trend tracker for TTF estimation."""

import pickle
from typing import List

import numpy as np
import pandas as pd

FAILURE_THRESHOLD = 0.85
MIN_TREND_POINTS = 30


class DegradationTracker:
    """Estimates degradation trend and time-to-failure ranges."""

    def compute_degradation_score(self, df: pd.DataFrame, numeric_cols: List[str]) -> pd.Series:
        scores = []

        for col in numeric_cols:
            s = pd.to_numeric(df[col], errors="coerce")
            std = s.std() + 1e-9
            dev = ((s - s.median()).abs() / (3 * std)).clip(0, 1)
            scores.append(dev)

        if not scores:
            return pd.Series(np.zeros(len(df)), index=df.index)

        return pd.concat(scores, axis=1).mean(axis=1)

    def estimate_ttf(self, series: pd.Series) -> dict:
        if len(series) < MIN_TREND_POINTS:
            return self._insufficient()

        current = float(series.iloc[-1])
        if current >= FAILURE_THRESHOLD:
            return {
                "hours_to_failure": 0.0,
                "confidence_interval_hours": [0.0, 2.0],
                "trend_type": "critical",
                "trend_r2": 1.0,
                "degradation_rate_per_hour": None,
                "is_reliable": True,
                "label": "CRITICAL — failure imminent",
                "is_trained": True,
            }

        recent = series.iloc[-min(len(series), 1440) :]
        x = np.arange(len(recent), dtype=float)
        y = recent.values.astype(float)

        lin_coeffs = np.polyfit(x, y, 1)
        lin_r2 = self._r2(y, np.polyval(lin_coeffs, x))

        exp_r2, exp_coeffs = 0.0, None
        try:
            exp_coeffs = np.polyfit(x, np.log(np.clip(y, 1e-9, None)), 1)
            exp_r2 = self._r2(y, np.exp(np.polyval(exp_coeffs, x)))
        except Exception:
            pass

        use_exp = exp_coeffs is not None and exp_r2 > lin_r2 + 0.05 and exp_r2 > 0.4

        if use_exp and exp_coeffs[0] > 0:
            trend_type = "exponential"
            r2 = exp_r2
            remaining = np.log(FAILURE_THRESHOLD + 1e-9) - np.log(current + 1e-9)
            steps = remaining / exp_coeffs[0] if exp_coeffs[0] > 0 else None
        elif lin_r2 > 0.35 and lin_coeffs[0] > 0:
            trend_type = "linear"
            r2 = lin_r2
            remaining = FAILURE_THRESHOLD - current
            steps = remaining / (lin_coeffs[0] + 1e-9) if remaining > 0 else 0
        else:
            return self._stable()

        if steps is None or steps < 0:
            return self._stable()

        hours = float(np.clip(steps / 60.0, 0, 720))
        unc = 1.0 + (1.0 - r2) * 2.0
        ci_low = max(0.0, hours * (1.0 - 0.3 * unc))
        ci_high = min(720.0, hours * (1.0 + 0.5 * unc))

        return {
            "hours_to_failure": round(hours, 1),
            "confidence_interval_hours": [round(ci_low, 1), round(ci_high, 1)],
            "trend_type": trend_type,
            "trend_r2": round(r2, 3),
            "degradation_rate_per_hour": round(float(lin_coeffs[0] * 60), 5),
            "is_reliable": r2 >= 0.60,
            "label": self._label(hours),
            "is_trained": True,
        }

    def _label(self, h: float) -> str:
        if h <= 4:
            return "CRITICAL — within 4 hours"
        if h <= 24:
            return f"HIGH — within {int(h)} hours"
        if h <= 72:
            return f"ELEVATED — within {int(h / 24) + 1} days"
        if h <= 168:
            return f"MODERATE — within {int(h / 24)} days"
        return f"LOW — within {int(h / 24)} days"

    @staticmethod
    def _stable() -> dict:
        return {
            "hours_to_failure": None,
            "confidence_interval_hours": None,
            "trend_type": "stable",
            "trend_r2": 0.0,
            "degradation_rate_per_hour": 0.0,
            "is_reliable": True,
            "label": "STABLE",
            "is_trained": True,
        }

    @staticmethod
    def _insufficient() -> dict:
        return {
            "hours_to_failure": None,
            "confidence_interval_hours": None,
            "trend_type": "insufficient_data",
            "trend_r2": 0.0,
            "degradation_rate_per_hour": 0.0,
            "is_reliable": False,
            "label": "Insufficient data for trend analysis",
            "is_trained": True,
        }

    @staticmethod
    def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2) + 1e-9
        return float(np.clip(1 - ss_res / ss_tot, 0, 1))

    def to_bytes(self) -> bytes:
        payload = {
            "failure_threshold": FAILURE_THRESHOLD,
            "min_trend_points": MIN_TREND_POINTS,
        }
        return pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)

    def load_bytes(self, payload: bytes) -> bool:
        if not payload:
            return False
        try:
            pickle.loads(payload)
            return True
        except Exception:
            return False
