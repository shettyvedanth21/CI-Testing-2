"""Failure Prediction Pipeline - premium robust implementation."""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import structlog
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from src.services.analytics.base import BasePipeline
from src.services.analytics.confidence import get_confidence
try:
    from services.shared.telemetry_contract import is_analytics_business_feature
except ModuleNotFoundError:  # pragma: no cover - unit-test path fallback
    from shared.telemetry_contract import is_analytics_business_feature

logger = structlog.get_logger()

MIN_POINTS = 100


class FailurePredictionPipeline(BasePipeline):
    def prepare_data(
        self,
        df: pd.DataFrame,
        parameters: Optional[Dict[str, Any]],
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        dfn = self._norm_ts(df)
        cols = self._numeric_cols(dfn)
        if not cols:
            raise ValueError("No numeric columns found in dataset")

        clean = dfn[["timestamp"] + cols].copy().sort_values("timestamp")
        clean = clean.set_index("timestamp").resample("1min").mean().reset_index()
        clean[cols] = clean[cols].ffill(limit=15).bfill(limit=15)
        clean[cols] = self._sanitize_numeric(clean[cols])
        labels = self._generate_labels(clean[["timestamp"] + cols].copy(), cols)
        clean["failure_label"] = labels.astype(int)
        clean["failure"] = clean["failure_label"]  # backward compatibility alias

        split = max(int(len(clean) * 0.8), MIN_POINTS)
        split = min(split, len(clean))
        return clean.iloc[:split], clean.iloc[split:]

    def train(
        self,
        train_df: pd.DataFrame,
        model_name: str,
        parameters: Optional[Dict[str, Any]],
    ) -> Any:
        params = parameters or {}
        confidence = get_confidence(len(train_df), str(params.get("sensitivity", "medium")))
        cols = self._numeric_cols(train_df)
        data = self._sanitize_numeric(train_df[cols].copy())

        features = self._build_features(data, cols)

        labels = self._generate_labels(
            train_df[["timestamp"] + cols].copy(),
            cols,
        )

        scaler = StandardScaler()
        X = scaler.fit_transform(features.fillna(0))
        X = np.clip(X, -10.0, 10.0)

        model = RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 200)),
            max_depth=int(params.get("max_depth", 8)),
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X, labels.values)

        return {
            "model": model,
            "scaler": scaler,
            "columns": cols,
            "feature_cols": cols,  # backward compatibility for older tests/consumers
            "feature_names": features.columns.tolist(),
            "train_data": data,
            "confidence": confidence.to_dict(),
        }

    def _generate_labels(
        self,
        df: pd.DataFrame,
        numeric_cols: List[str],
    ) -> pd.Series:
        """
        Generate synthetic failure labels from multi-parameter stress indicators.

        This method is intentionally reusable by the ensemble orchestrator
        to keep label semantics consistent across models.
        """
        if not numeric_cols:
            return pd.Series(0, index=df.index, dtype=int)

        data = self._sanitize_numeric(df[numeric_cols].copy())

        band_viol = pd.DataFrame(index=data.index)
        for col in numeric_cols:
            p10, p90 = data[col].quantile(0.10), data[col].quantile(0.90)
            band_viol[col] = ((data[col] < p10) | (data[col] > p90)).astype(int)
        multi = (band_viol.sum(axis=1) >= 2).astype(int)

        roc_stress = pd.Series(0, index=data.index)
        for col in numeric_cols:
            roc = data[col].diff().abs().fillna(0)
            roc_stress = roc_stress | (roc > roc.quantile(0.95)).astype(int)

        labels = ((multi | roc_stress) > 0).astype(int)
        if labels.sum() < 5:
            labels.iloc[-min(10, len(labels)) :] = 1
        return labels

    def predict(
        self,
        test_df: pd.DataFrame,
        model: Any,
        parameters: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        df = self._norm_ts(test_df)
        cols = [c for c in model.get("columns", []) if c in df.columns]
        if not cols:
            raise ValueError("No model columns available in input dataframe")

        clean = df[["timestamp"] + cols].copy().sort_values("timestamp")
        clean = clean.set_index("timestamp").resample("1min").mean().reset_index()
        clean[cols] = clean[cols].ffill(limit=15).bfill(limit=15)
        data = self._sanitize_numeric(clean[cols].copy())
        feats = self._build_features(data, cols)

        scaler = model["scaler"]
        rf = model["model"]

        X = scaler.transform(feats.fillna(0))
        X = np.clip(X, -10.0, 10.0)

        all_proba = np.clip(rf.predict_proba(X)[:, 1], 0.0, 1.0)

        recent_n = max(int(len(X) * 0.20), 20)
        recent_n = min(recent_n, len(X)) if len(X) else 0
        fp_pct = float(np.percentile(all_proba[-recent_n:], 90) * 100) if recent_n else 0.0

        safe_pct = float(np.mean(all_proba < 0.30) * 100) if len(all_proba) else 0.0
        warning_pct = float(np.mean((all_proba >= 0.30) & (all_proba < 0.70)) * 100) if len(all_proba) else 0.0
        critical_pct = float(np.mean(all_proba >= 0.70) * 100) if len(all_proba) else 0.0

        importances = rf.feature_importances_
        feat_names = feats.columns.tolist()
        param_imp = {col: 0.0 for col in cols}
        for fname, imp in zip(feat_names, importances):
            for col in cols:
                if fname.startswith(col + "_") or fname == col:
                    param_imp[col] += float(imp)
                    break
        total_imp = sum(param_imp.values()) + 1e-9
        param_pct = {k: round(v / total_imp * 100, 1) for k, v in param_imp.items()}

        split = max(1, len(data) // 2)
        risk_factors = []
        for col in sorted(param_pct, key=param_pct.get, reverse=True):
            om = float(data[col].iloc[:split].mean())
            rm = float(data[col].iloc[split:].mean()) if len(data.iloc[split:]) else om
            rs = float(data[col].iloc[split:].std()) if len(data.iloc[split:]) else 0.0
            cv = rs / (abs(rm) + 1e-9)
            if cv > 0.15:
                trend = "erratic"
            elif rm > om * 1.05:
                trend = "increasing"
            elif rm < om * 0.95:
                trend = "decreasing"
            else:
                trend = "stable"
            pct_ch = (rm - om) / (abs(om) + 1e-9) * 100

            risk_factors.append(
                {
                    "parameter": col,
                    "contribution_pct": param_pct[col],
                    "trend": trend,
                    "context": (
                        f"{col} {'increased' if trend == 'increasing' else 'changed'} "
                        f"{abs(pct_ch):.1f}% in recent readings "
                        f"(current: {rm:.2f}, baseline: {om:.2f})"
                    ),
                    "reasoning": self._reasoning(col, trend),
                    "current_value": round(rm, 3),
                    "baseline_value": round(om, 3),
                }
            )

        days = self._days_available(clean)
        confidence = get_confidence(len(clean), str((parameters or {}).get("sensitivity", "medium")))

        return {
            "failure_probability": all_proba.tolist(),
            "predicted_failure": (all_proba >= 0.5).tolist(),
            "time_to_failure_hours": [round((1.0 - p) * 720, 1) for p in all_proba],
            "point_timestamps": [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in clean["timestamp"]],
            "failure_probability_pct": round(fp_pct, 1),
            "risk_breakdown": {
                "safe_pct": round(safe_pct, 1),
                "warning_pct": round(warning_pct, 1),
                "critical_pct": round(critical_pct, 1),
            },
            "risk_factors": risk_factors,
            "model_confidence": confidence.level,
            "days_available": round(days, 1),
            "insufficient_data_for_prediction": len(clean) < MIN_POINTS,
            "data_completeness_pct": self._data_completeness(clean),
            "confidence": confidence.to_dict(),
        }

    def evaluate(
        self,
        test_df: pd.DataFrame,
        results: Dict[str, Any],
        parameters: Optional[Dict[str, Any]],
    ) -> Dict[str, float]:
        return {
            "failure_probability_pct": float(results.get("failure_probability_pct", 0.0)),
            "model_confidence": str(results.get("model_confidence", "Low")),
        }

    @staticmethod
    def _build_features(data: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
        out = pd.DataFrame(index=data.index)
        for col in cols:
            for w in [10, 30, 360]:
                out[f"{col}_mean_{w}"] = data[col].rolling(w, min_periods=1).mean()
                out[f"{col}_std_{w}"] = data[col].rolling(w, min_periods=1).std().fillna(0)
            out[f"{col}_roc"] = data[col].diff().fillna(0)
            p10, p90 = data[col].quantile(0.10), data[col].quantile(0.90)
            out[f"{col}_above_p90"] = (data[col] > p90).astype(int)
            out[f"{col}_below_p10"] = (data[col] < p10).astype(int)
        out["multi_param_violation"] = sum(out[f"{c}_above_p90"] + out[f"{c}_below_p10"] for c in cols)
        return out

    @staticmethod
    def _sanitize_numeric(data: pd.DataFrame) -> pd.DataFrame:
        out = data.copy()
        for col in out.columns:
            s = pd.to_numeric(out[col], errors="coerce")
            p01 = s.quantile(0.01) if s.notna().any() else 0.0
            p99 = s.quantile(0.99) if s.notna().any() else 0.0
            med = s.median() if s.notna().any() else 0.0
            s = s.replace([np.inf], p99).replace([-np.inf], p01)
            out[col] = s.fillna(med)
        return out

    @staticmethod
    def _days_available(df: pd.DataFrame) -> float:
        try:
            return float((df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400)
        except Exception:
            return 0.0

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
    def _reasoning(param: str, trend: str) -> str:
        p = param.lower()
        if "temp" in p and trend == "increasing":
            return "Rising temperature indicates cooling degradation or increased friction"
        if "vibr" in p and trend == "increasing":
            return "Progressive vibration increase is a common bearing failure precursor"
        if "press" in p and trend == "decreasing":
            return "Declining pressure may indicate seal or valve degradation"
        if "current" in p and trend == "erratic":
            return "Erratic current draw suggests mechanical resistance or electrical issue"
        if "power" in p and trend == "increasing":
            return "Increasing power consumption may indicate inefficiency or overload"
        if trend == "erratic":
            return f"Erratic {param} pattern indicates instability"
        if trend == "increasing":
            return f"Sustained increase in {param} is a stress indicator"
        if trend == "decreasing":
            return f"Declining {param} may indicate degradation"
        return f"{param} is within normal operating range"

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
            "failure",
            "failure_label",
            "predicted_failure",
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
