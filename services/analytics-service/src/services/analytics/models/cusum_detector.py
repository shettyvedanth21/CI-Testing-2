"""CUSUM drift detector for sustained process shifts."""

import pickle
from typing import List

import numpy as np
import pandas as pd


class CUSUMDetector:
    """Two-sided CUSUM for no-training drift anomaly detection."""

    def __init__(self, k: float = 0.5, h: float = 5.0, warmup: int = 20):
        self.k = k
        self.h = h
        self.warmup = warmup

    def detect(self, df: pd.DataFrame, numeric_cols: List[str]) -> dict:
        n = len(df)
        if n < self.warmup or not numeric_cols:
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n),
                "drift_params": [],
                "is_trained": True,
            }

        all_scores = []
        drift_flags = []

        for col in numeric_cols:
            s = pd.to_numeric(df[col], errors="coerce").ffill().bfill().fillna(0).values.astype(float)
            mu = np.mean(s[: self.warmup])
            sigma = np.std(s[: self.warmup]) + 1e-9
            z = (s - mu) / sigma

            s_pos = np.zeros(n)
            s_neg = np.zeros(n)

            for i in range(1, n):
                s_pos[i] = max(0.0, s_pos[i - 1] + z[i] - self.k)
                s_neg[i] = max(0.0, s_neg[i - 1] - z[i] - self.k)

            combined = np.maximum(s_pos, s_neg)
            scores = np.clip(combined / self.h, 0, 1)
            all_scores.append(scores)

            if (combined > self.h).any():
                drift_flags.append(col)

        agg = np.max(all_scores, axis=0) if all_scores else np.zeros(n)

        return {
            "is_anomaly": agg > 0.5,
            "anomaly_score": np.clip(agg, 0, 1),
            "drift_params": drift_flags,
            "is_trained": True,
        }

    def to_bytes(self) -> bytes:
        payload = {"k": self.k, "h": self.h, "warmup": self.warmup}
        return pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)

    def load_bytes(self, payload: bytes) -> bool:
        if not payload:
            return False
        try:
            data = pickle.loads(payload)
            self.k = float(data.get("k", self.k))
            self.h = float(data.get("h", self.h))
            self.warmup = int(data.get("warmup", self.warmup))
            return True
        except Exception:
            return False
