"""3-model failure ensemble orchestrator."""

import hashlib
import time
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import structlog
from sklearn.preprocessing import StandardScaler

from src.services.analytics.ensemble.voting_engine import VotingEngine
from src.services.analytics.explainer.reasoning_engine import ReasoningEngine
from src.services.analytics.failure_prediction import FailurePredictionPipeline
from src.services.analytics.features.feature_engineer import FeatureEngineer
from src.services.analytics.model_cache import ModelCache
from src.services.analytics.features.sequence_builder import SequenceBuilder
from src.services.analytics.models.degradation_tracker import DegradationTracker
from src.services.analytics.models.lstm_classifier import LSTMFailureClassifier
from src.services.analytics.models.xgboost_classifier import XGBoostFailureClassifier

SEQUENCE_LENGTH = 30
logger = structlog.get_logger()


class FailureEnsemble:
    """Runs XGBoost + LSTM + degradation trend and combines via voting."""

    def run(self, df: pd.DataFrame, parameters: Dict[str, Any] | None = None) -> Dict[str, Any]:
        params = parameters or {}
        preloaded_artifacts = params.get("__artifacts", {}) if isinstance(params, dict) else {}
        base = self._to_timestamp_index(df)

        fe = FeatureEngineer()
        numeric_cols = fe.get_numeric_cols(base.reset_index())
        feature_df, feature_names = fe.compute_all_features(base)
        x = feature_df.values

        fp_pipeline = FailurePredictionPipeline()
        y = fp_pipeline._generate_labels(base.reset_index(), numeric_cols).to_numpy(dtype=int)

        xgb = XGBoostFailureClassifier()
        schema_hash = hashlib.sha256("|".join(feature_names).encode("utf-8")).hexdigest()
        device_id = str((df.get("device_id").iloc[0] if "device_id" in df.columns and len(df) else "unknown"))
        cache = ModelCache()
        xgb_trained = False
        artifact_updates: Dict[str, Dict[str, Any]] = {}
        registry_artifact = preloaded_artifacts.get("xgboost") if isinstance(preloaded_artifacts, dict) else None
        if isinstance(registry_artifact, dict):
            reg_hash = registry_artifact.get("feature_schema_hash")
            reg_payload = registry_artifact.get("artifact_payload")
            if reg_hash == schema_hash and reg_payload:
                xgb_trained = xgb.load_bytes(reg_payload)
                if xgb_trained:
                    logger.info("using_cached_model", model_key="xgboost")
        if not xgb_trained:
            xgb_start = time.perf_counter()
            xgb_trained = xgb.train(x, y, feature_names)
            if xgb_trained:
                xgb_payload = xgb.to_bytes()
                cache.save(
                    device_id=device_id,
                    analysis_type="prediction",
                    model_key="xgboost",
                    schema_hash=schema_hash,
                    payload=xgb_payload,
                )
                artifact_updates["xgboost"] = {
                    "feature_schema_hash": schema_hash,
                    "artifact_payload": xgb_payload,
                    "metrics": {
                        "training_duration_seconds": round(time.perf_counter() - xgb_start, 3),
                        "samples": int(len(x)),
                        "positive_labels": int(y.sum()) if len(y) else 0,
                    },
                }
        xgb_proba = xgb.predict_proba(x)

        lstm_proba = np.zeros(len(base), dtype=float)
        lstm_trained = False

        if numeric_cols:
            scaled = StandardScaler().fit_transform(base[numeric_cols].fillna(0))
            scaled_df = pd.DataFrame(scaled, index=base.index, columns=numeric_cols)

            seq_b = SequenceBuilder()
            sequences, _ = seq_b.build_sequences(scaled_df, SEQUENCE_LENGTH, numeric_cols)
            seq_labels = y[SEQUENCE_LENGTH:]

            lstm_clf = LSTMFailureClassifier()
            lstm_schema_hash = self._schema_hash(["lstm_classifier", str(SEQUENCE_LENGTH)] + list(numeric_cols))
            lstm_artifact = preloaded_artifacts.get("lstm_classifier") if isinstance(preloaded_artifacts, dict) else None
            if isinstance(lstm_artifact, dict):
                reg_hash = lstm_artifact.get("feature_schema_hash")
                reg_payload = lstm_artifact.get("artifact_payload")
                if reg_hash == lstm_schema_hash and reg_payload:
                    lstm_trained = lstm_clf.load_bytes(reg_payload)
                    if lstm_trained:
                        logger.info("using_cached_model", model_key="lstm_classifier")
            if not lstm_trained:
                lstm_start = time.perf_counter()
                lstm_trained = lstm_clf.train(sequences, seq_labels)
                if lstm_trained:
                    artifact_updates["lstm_classifier"] = {
                        "feature_schema_hash": lstm_schema_hash,
                        "artifact_payload": lstm_clf.to_bytes(),
                        "metrics": {
                            "training_duration_seconds": round(time.perf_counter() - lstm_start, 3),
                            "sequence_length": SEQUENCE_LENGTH,
                            "positive_labels": int(seq_labels.sum()) if len(seq_labels) else 0,
                        },
                    }
            lstm_seq = lstm_clf.predict_proba(sequences)
            pad_len = len(base) - len(sequences)
            if pad_len > 0:
                lstm_proba = np.concatenate([np.zeros(pad_len, dtype=float), lstm_seq])
            else:
                lstm_proba = lstm_seq

        deg_tracker = DegradationTracker()
        deg_schema_hash = self._schema_hash(["degradation_tracker"] + list(numeric_cols))
        deg_artifact = preloaded_artifacts.get("degradation_tracker") if isinstance(preloaded_artifacts, dict) else None
        deg_loaded = False
        if isinstance(deg_artifact, dict):
            reg_hash = deg_artifact.get("feature_schema_hash")
            reg_payload = deg_artifact.get("artifact_payload")
            if reg_hash == deg_schema_hash and reg_payload:
                deg_loaded = deg_tracker.load_bytes(reg_payload)
                if deg_loaded:
                    logger.info("using_cached_model", model_key="degradation_tracker")
        if not deg_loaded:
            artifact_updates["degradation_tracker"] = {
                "feature_schema_hash": deg_schema_hash,
                "artifact_payload": deg_tracker.to_bytes(),
                "metrics": {
                    "training_duration_seconds": 0.0,
                },
            }
        deg_score = deg_tracker.compute_degradation_score(base, numeric_cols)
        deg_result = deg_tracker.estimate_ttf(deg_score)

        verdict = VotingEngine().vote_failure(
            xgb_proba,
            lstm_proba,
            deg_result,
            xgb_trained=bool(xgb_trained),
            lstm_trained=bool(lstm_trained),
        )

        risk_features = xgb.get_top_features(x[-100:])
        risk_factors = self._risk_factor_rows(risk_features, feature_df)
        reasoning = ReasoningEngine().generate_failure_reasoning(verdict, risk_features, deg_result)

        failure_probability = (0.5 * xgb_proba + 0.5 * lstm_proba).clip(0, 1)
        predicted_failure = failure_probability >= 0.5

        recent_n = max(20, int(len(failure_probability) * 0.20)) if len(failure_probability) else 20
        recent_n = min(recent_n, len(failure_probability)) if len(failure_probability) else 0
        failure_probability_pct = float(np.percentile(failure_probability[-recent_n:], 90) * 100) if recent_n else 0.0

        safe_pct = float(np.mean(failure_probability < 0.30) * 100) if len(failure_probability) else 0.0
        warning_pct = float(np.mean((failure_probability >= 0.30) & (failure_probability < 0.70)) * 100) if len(failure_probability) else 0.0
        critical_pct = float(np.mean(failure_probability >= 0.70) * 100) if len(failure_probability) else 0.0

        ttf_hours = deg_result.get("hours_to_failure")
        if ttf_hours is None:
            ttf_series = [None for _ in failure_probability]
        else:
            ttf_series = [round(float(max(0.0, ttf_hours)), 1) for _ in failure_probability]

        days_available = self._days_available(base)

        return {
            "failure_probability": failure_probability.tolist(),
            "predicted_failure": predicted_failure.tolist(),
            "time_to_failure_hours": ttf_series,
            "point_timestamps": [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in base.index],
            "failure_probability_pct": round(failure_probability_pct, 1),
            "risk_breakdown": {
                "safe_pct": round(safe_pct, 1),
                "warning_pct": round(warning_pct, 1),
                "critical_pct": round(critical_pct, 1),
            },
            "risk_factors": risk_factors,
            "model_confidence": verdict.get("confidence", "Low"),
            "days_available": round(days_available, 1),
            "insufficient_data_for_prediction": len(base) < 100,
            "data_completeness_pct": self._data_completeness(base),
            "confidence": {
                "level": verdict.get("confidence", "Low"),
            },
            "ensemble": verdict,
            "reasoning": reasoning,
            "time_to_failure": {
                "hours": deg_result.get("hours_to_failure"),
                "label": deg_result.get("label"),
                "confidence_interval": deg_result.get("confidence_interval_hours"),
                "trend_type": deg_result.get("trend_type"),
                "trend_r2": deg_result.get("trend_r2"),
                "is_reliable": deg_result.get("is_reliable", False),
            },
            "degradation_series": [float(v) for v in deg_score.iloc[-1440:].tolist()],
            "data_quality_flags": self._flags(days_available, lstm_trained),
            "artifact_updates": artifact_updates,
        }

    @staticmethod
    def _schema_hash(parts: List[str]) -> str:
        joined = "|".join(parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_timestamp_index(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "timestamp" not in out.columns and "_time" in out.columns:
            out = out.rename(columns={"_time": "timestamp"})
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
        out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
        return out.set_index("timestamp")

    @staticmethod
    def _risk_factor_rows(risk_features: List[Tuple[str, float]], feature_df: pd.DataFrame) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if feature_df.empty:
            return rows

        split = max(1, len(feature_df) // 2)

        for name, importance in risk_features[:5]:
            if name not in feature_df.columns:
                continue
            series = pd.to_numeric(feature_df[name], errors="coerce").fillna(0)
            om = float(series.iloc[:split].mean())
            rm = float(series.iloc[split:].mean()) if len(series.iloc[split:]) else om
            rs = float(series.iloc[split:].std()) if len(series.iloc[split:]) else 0.0
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
            rows.append(
                {
                    "parameter": name,
                    "contribution_pct": round(float(importance) * 100, 1),
                    "trend": trend,
                    "context": f"{name} changed {abs(pct_ch):.1f}% in recent readings",
                    "reasoning": f"Feature importance and trend indicate {trend} behavior.",
                    "current_value": round(rm, 3),
                    "baseline_value": round(om, 3),
                }
            )
        return rows

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
        if len(df.index) <= 1:
            return 0.0
        try:
            return float((df.index.max() - df.index.min()).total_seconds() / 86400)
        except Exception:
            return 0.0

    @staticmethod
    def _flags(days: float, lstm_trained: bool) -> List[Dict[str, str]]:
        flags: List[Dict[str, str]] = []

        if days < (1 / 24):
            level, color, msg = "Very Low", "red", "Demo only — less than 1 hour of data."
        elif days < 1:
            level, color, msg = "Low", "orange", f"{int(days * 24)}h of data — directional only."
        elif days < 7:
            level, color, msg = "Moderate", "yellow", f"{int(max(days, 1))} days — accuracy improving."
        elif days < 30:
            level, color, msg = "Good", "blue", f"{int(days)} days — reliable for warnings."
        elif days < 90:
            level, color, msg = "High", "green", f"{int(days)} days — production grade."
        else:
            level, color, msg = "Very High", "green", f"{int(days)} days — robust baseline."

        flags.append(
            {
                "type": "data_confidence",
                "confidence_level": level,
                "color": color,
                "message": msg,
                "severity": "info",
            }
        )

        if not lstm_trained:
            flags.append(
                {
                    "type": "lstm_not_trained",
                    "message": "Temporal model skipped — need 50+ sequences.",
                    "severity": "info",
                }
            )

        return flags
