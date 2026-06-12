"""Anomaly Detection Pipeline - premium robust implementation."""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from src.services.analytics.base import BasePipeline
from src.services.analytics.confidence import get_confidence
try:
    from services.shared.telemetry_contract import is_analytics_business_feature
except ModuleNotFoundError:  # pragma: no cover - unit-test path fallback
    from shared.telemetry_contract import is_analytics_business_feature

logger = structlog.get_logger()

MIN_POINTS = 50


class AnomalyDetectionPipeline(BasePipeline):
    def prepare_data(
        self,
        df: pd.DataFrame,
        parameters: Optional[Dict[str, Any]],
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        params = parameters or {}
        clean, cols = self._prepare_clean_df(df)

        if len(clean) < MIN_POINTS:
            # still return a split; downstream can label insufficient data
            split = max(1, int(len(clean) * 0.8))
            return clean.iloc[:split], clean.iloc[split:]

        split = max(int(len(clean) * 0.8), MIN_POINTS)
        split = min(split, len(clean))

        # Attach metadata into params-compatible output path through attributes
        self._last_prepare_metadata = {
            "columns": cols,
            "data_completeness_pct": self._data_completeness(clean),
            "single_parameter_mode": len(cols) == 1,
            "lookback_days": int(params.get("lookback_days", 7)),
        }

        return clean.iloc[:split], clean.iloc[split:]

    def train(
        self,
        train_df: pd.DataFrame,
        model_name: str,
        parameters: Optional[Dict[str, Any]],
    ) -> Any:
        params = parameters or {}
        cols = self._numeric_cols(train_df)
        if not cols:
            raise ValueError("No numeric columns found in dataset")

        confidence = get_confidence(len(train_df), str(params.get("sensitivity", "medium")))
        contamination = confidence.contamination
        sensitivity = str(params.get("sensitivity", "medium")).lower()
        if sensitivity == "low":
            contamination = min(contamination, 0.02)
        elif sensitivity == "high":
            contamination = min(0.06, contamination + 0.01)

        data = train_df[cols].copy()
        data = self._sanitize_numeric(data)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(data.values)
        X_scaled = np.clip(X_scaled, -10.0, 10.0)

        # Single parameter mode: still run IF but keep fallback metadata
        model = IsolationForest(
            contamination=contamination,
            n_estimators=200,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_scaled)

        return {
            "model": model,
            "scaler": scaler,
            "columns": cols,
            "feature_cols": cols,  # backward compatibility for older tests/consumers
            "single_parameter_mode": len(cols) == 1,
            "confidence": confidence.to_dict(),
        }

    def predict(
        self,
        test_df: pd.DataFrame,
        model: Any,
        parameters: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        params = parameters or {}
        clean, _ = self._prepare_clean_df(test_df)
        df = clean
        cols = [c for c in model.get("columns", []) if c in clean.columns]
        if not cols:
            raise ValueError("No model columns available in input dataframe")

        data = self._sanitize_numeric(clean[cols].copy())

        scaler = model["scaler"]
        clf = model["model"]
        X_scaled = scaler.transform(data.values)
        X_scaled = np.clip(X_scaled, -10.0, 10.0)

        raw_scores = clf.decision_function(X_scaled)
        predictions = clf.predict(X_scaled)

        # convert to anomaly score in [0, 1]
        inv = -raw_scores
        smin, smax = float(np.min(inv)), float(np.max(inv))
        norm = (inv - smin) / (smax - smin + 1e-9)
        norm = np.clip(norm, 0.0, 1.0)

        is_anomaly = predictions == -1
        confidence = get_confidence(len(df), str(params.get("sensitivity", "medium")))

        z = np.abs((data - data.mean()) / (data.std() + 1e-9)).fillna(0.0)

        anomaly_details: List[Dict[str, Any]] = []
        for idx, (flag, score) in enumerate(zip(is_anomaly.tolist(), norm.tolist())):
            if not flag:
                continue

            if score >= 0.8:
                sev = "high"
            elif score >= 0.5:
                sev = "medium"
            else:
                sev = "low"

            row_z = z.iloc[idx]
            triggered = row_z[row_z > 2.0].sort_values(ascending=False)
            triggered_params = triggered.index.tolist() or [cols[int(np.argmax(row_z.values))]]

            top = triggered_params[0]
            current = float(data.iloc[idx][top])
            mean = float(data[top].mean())
            std = float(data[top].std() + 1e-9)
            lo = mean - 2 * std
            hi = mean + 2 * std
            if current >= mean:
                context = f"{top} spike to {current:.2f} (normal: {lo:.2f}-{hi:.2f})"
            else:
                context = f"{top} dropped to {current:.2f} (normal: {lo:.2f}-{hi:.2f})"

            ts = df["timestamp"].iloc[idx]
            anomaly_details.append(
                {
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "severity": sev,
                    "parameters": triggered_params,
                    "context": context,
                    "raw_score": float(raw_scores[idx]),
                    "isolation_score": float(score),
                }
            )

        return {
            "anomaly_score": norm.tolist(),
            "is_anomaly": is_anomaly.tolist(),
            "point_timestamps": [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in df["timestamp"]],
            "anomaly_details": anomaly_details,
            "columns_used": cols,
            "total_anomalies": int(np.sum(is_anomaly)),
            "anomaly_percentage": float((np.sum(is_anomaly) / len(df) * 100) if len(df) else 0.0),
            "data_completeness_pct": self._data_completeness(df),
            "single_parameter_mode": bool(model.get("single_parameter_mode", False)),
            "days_available": self._days_available(df),
            "insufficient_data": len(df) < MIN_POINTS,
            "confidence": confidence.to_dict(),
        }

    def evaluate(
        self,
        test_df: pd.DataFrame,
        results: Dict[str, Any],
        parameters: Optional[Dict[str, Any]],
    ) -> Dict[str, float]:
        total = len(results.get("is_anomaly", []))
        n = int(np.sum(results.get("is_anomaly", []))) if total else 0
        scores = np.asarray(results.get("anomaly_score", []), dtype=float)
        return {
            "total_points": float(total),
            "anomalies_detected": float(n),
            "anomaly_rate_pct": float((n / total * 100) if total else 0.0),
            "mean_anomaly_score": float(np.mean(scores)) if scores.size else 0.0,
            "max_anomaly_score": float(np.max(scores)) if scores.size else 0.0,
        }

    @staticmethod
    def _prepare_clean_df(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        dfn = AnomalyDetectionPipeline._norm_ts(df)
        cols = AnomalyDetectionPipeline._numeric_cols(dfn)
        if not cols:
            raise ValueError("No numeric columns found in dataset")

        clean = dfn[["timestamp"] + cols].copy().sort_values("timestamp")

        # outlier clip ±5 sigma
        for col in cols:
            mean = clean[col].mean()
            std = clean[col].std()
            if pd.notna(std) and float(std) > 0:
                lo = mean - 5 * std
                hi = mean + 5 * std
                clean[col] = clean[col].clip(lower=lo, upper=hi)

        # resample 1min
        clean = clean.set_index("timestamp").resample("1min").mean().reset_index()

        # fill short gaps + median fallback
        clean[cols] = clean[cols].ffill(limit=15).bfill(limit=15)
        clean[cols] = AnomalyDetectionPipeline._sanitize_numeric(clean[cols])

        return clean, cols

    @staticmethod
    def _sanitize_numeric(data: pd.DataFrame) -> pd.DataFrame:
        out = data.copy()
        for col in out.columns:
            series = pd.to_numeric(out[col], errors="coerce")
            p01 = series.quantile(0.01) if series.notna().any() else 0.0
            p99 = series.quantile(0.99) if series.notna().any() else 0.0
            med = series.median() if series.notna().any() else 0.0
            series = series.replace([np.inf], p99).replace([-np.inf], p01)
            series = series.fillna(med)
            out[col] = series
        return out

    @staticmethod
    def _data_completeness(df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        numeric = df.select_dtypes(include=[np.number])
        if numeric.empty:
            return 0.0
        non_null = float(numeric.notna().sum().sum())
        total = float(numeric.shape[0] * numeric.shape[1])
        return round((non_null / total * 100) if total > 0 else 0.0, 2)

    @staticmethod
    def _days_available(df: pd.DataFrame) -> float:
        try:
            return round(float((df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400), 1)
        except Exception:
            return 0.0

    @staticmethod
    def _norm_ts(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "timestamp" not in out.columns and "_time" in out.columns:
            out = out.rename(columns={"_time": "timestamp"})
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
        return out.dropna(subset=["timestamp"])

    @staticmethod
    def _numeric_cols(df: pd.DataFrame) -> List[str]:
        exclude = {
            "timestamp",
            "_time",
            "device_id",
            "schema_version",
            "enrichment_status",
            "table",
            "hour",
            "minute",
            "second",
            "day",
            "month",
            "year",
            "day_of_week",
            "day_of_year",
            "week",
            "week_of_year",
            "quarter",
            "is_weekend",
            "index",
            "unnamed: 0",
        }
        return [
            c
            for c in df.columns
            if c not in exclude and is_analytics_business_feature(c) and pd.api.types.is_numeric_dtype(df[c])
        ]
