"""3-model anomaly ensemble orchestrator."""

import hashlib
import pickle
import time
from typing import Any, Callable, Dict, List

import numpy as np
import pandas as pd
import structlog
from sklearn.preprocessing import StandardScaler

from src.services.analytics.anomaly_detection import AnomalyDetectionPipeline
from src.services.analytics.ensemble.voting_engine import VotingEngine
from src.services.analytics.explainer.reasoning_engine import ReasoningEngine
from src.services.analytics.features.feature_engineer import FeatureEngineer
from src.services.analytics.features.sequence_builder import SequenceBuilder
from src.services.analytics.models.cusum_detector import CUSUMDetector
from src.services.analytics.models.lstm_autoencoder import LSTMAnomalyAutoencoder

SEQUENCE_LENGTH = 30
logger = structlog.get_logger()


class AnomalyEnsemble:
    """Runs IF + LSTM autoencoder + CUSUM and combines via voting."""

    @staticmethod
    def _emit_progress(
        progress_callback: Callable[[str, str, str], None] | None,
        event_type: str,
        stage_key: str,
        label: str,
    ) -> None:
        if progress_callback is None:
            return
        progress_callback(event_type, stage_key, label)

    @staticmethod
    def _parse_utc_timestamps(values: Any) -> pd.DatetimeIndex:
        """Parse mixed timestamp values deterministically without inference warnings."""
        if isinstance(values, pd.DatetimeIndex):
            return values.tz_convert("UTC") if values.tz is not None else values.tz_localize("UTC")
        series = pd.Series(values)
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = pd.to_datetime(series, utc=True, errors="coerce")
        else:
            parsed = pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")
        return pd.DatetimeIndex(parsed)

    def run(
        self,
        df: pd.DataFrame,
        parameters: Dict[str, Any] | None = None,
        progress_callback: Callable[[str, str, str], None] | None = None,
    ) -> Dict[str, Any]:
        params = parameters or {}
        preloaded_artifacts = params.get("__artifacts", {}) if isinstance(params, dict) else {}
        fe = FeatureEngineer()
        numeric_cols = fe.get_numeric_cols(df)
        artifact_updates: Dict[str, Dict[str, Any]] = {}

        if_pipeline = AnomalyDetectionPipeline()
        self._emit_progress(
            progress_callback,
            "start",
            "anomaly_feature_preparation",
            "Preparing anomaly features",
        )
        train_df, _ = if_pipeline.prepare_data(df, params)
        self._emit_progress(
            progress_callback,
            "complete",
            "anomaly_feature_preparation",
            "Preparing anomaly features",
        )
        if_schema_hash = self._schema_hash(["if"] + list(numeric_cols))
        if_model = None
        if_artifact = preloaded_artifacts.get("isolation_forest") if isinstance(preloaded_artifacts, dict) else None
        if isinstance(if_artifact, dict):
            if_payload = if_artifact.get("artifact_payload")
            if_hash = if_artifact.get("feature_schema_hash")
            if if_hash == if_schema_hash and if_payload:
                try:
                    self._emit_progress(
                        progress_callback,
                        "start",
                        "isolation_forest",
                        "Loading isolation forest",
                    )
                    if_model = pickle.loads(if_payload)
                    logger.info("using_cached_model", model_key="isolation_forest")
                except Exception:
                    if_model = None
        if if_model is None:
            self._emit_progress(
                progress_callback,
                "start",
                "isolation_forest",
                "Training isolation forest",
            )
            if_start = time.perf_counter()
            if_model = if_pipeline.train(train_df, "isolation_forest", params)
            artifact_updates["isolation_forest"] = {
                "feature_schema_hash": if_schema_hash,
                "artifact_payload": pickle.dumps(if_model, protocol=pickle.HIGHEST_PROTOCOL),
                "metrics": {
                    "training_duration_seconds": round(time.perf_counter() - if_start, 3),
                    "contamination": float(if_model.get("confidence", {}).get("contamination", 0.0)),
                },
            }
        self._emit_progress(
            progress_callback,
            "complete",
            "isolation_forest",
            "Isolation forest ready",
        )
        if_result = if_pipeline.predict(df, if_model, params)

        n_if = len(if_result.get("is_anomaly", []))
        if n_if == 0:
            return {
                "is_anomaly": [],
                "anomaly_score": [],
                "anomaly_details": [],
                "point_timestamps": [],
                "total_anomalies": 0,
                "anomaly_percentage": 0.0,
                "ensemble": {},
                "reasoning": {},
                "data_quality_flags": self._flags(df, False, False),
                "artifact_updates": artifact_updates,
            }

        lstm_result = {
            "is_anomaly": np.zeros(n_if, dtype=bool),
            "anomaly_score": np.zeros(n_if, dtype=float),
            "is_trained": False,
            "time_budget_capped": False,
        }

        if numeric_cols:
            self._emit_progress(
                progress_callback,
                "start",
                "lstm_sequence_preparation",
                "Preparing LSTM sequences",
            )
            base = self._to_timestamp_index(df)
            scaled = StandardScaler().fit_transform(base[numeric_cols].fillna(0))
            scaled_df = pd.DataFrame(scaled, index=base.index, columns=numeric_cols)

            seq_b = SequenceBuilder()
            sequences, ts = seq_b.build_sequences(scaled_df, SEQUENCE_LENGTH, numeric_cols)
            self._emit_progress(
                progress_callback,
                "complete",
                "lstm_sequence_preparation",
                "LSTM sequences prepared",
            )
            lstm_ae = LSTMAnomalyAutoencoder()
            lstm_schema_hash = self._schema_hash(["lstm_ae", str(SEQUENCE_LENGTH)] + list(numeric_cols))
            trained = False
            lstm_artifact = preloaded_artifacts.get("lstm_autoencoder") if isinstance(preloaded_artifacts, dict) else None
            if isinstance(lstm_artifact, dict):
                reg_hash = lstm_artifact.get("feature_schema_hash")
                reg_payload = lstm_artifact.get("artifact_payload")
                if reg_hash == lstm_schema_hash and reg_payload:
                    self._emit_progress(
                        progress_callback,
                        "start",
                        "lstm_model",
                        "Loading temporal autoencoder",
                    )
                    trained = lstm_ae.load_bytes(reg_payload)
                    if trained:
                        logger.info("using_cached_model", model_key="lstm_autoencoder")
            if not trained:
                self._emit_progress(
                    progress_callback,
                    "start",
                    "lstm_model",
                    "Training temporal autoencoder",
                )
                lstm_start = time.perf_counter()
                trained = lstm_ae.train(sequences)
                if trained:
                    artifact_updates["lstm_autoencoder"] = {
                        "feature_schema_hash": lstm_schema_hash,
                        "artifact_payload": lstm_ae.to_bytes(),
                        "metrics": {
                            "training_duration_seconds": round(time.perf_counter() - lstm_start, 3),
                            "sequence_length": SEQUENCE_LENGTH,
                            "trained_epochs": int(getattr(lstm_ae, "trained_epochs", 0) or 0),
                            "time_budget_capped": bool(getattr(lstm_ae, "training_timed_out", False)),
                            "stop_reason": getattr(lstm_ae, "training_stop_reason", None),
                        },
                    }
            self._emit_progress(
                progress_callback,
                "complete",
                "lstm_model",
                (
                    "Temporal autoencoder ready (time-capped)"
                    if trained and bool(getattr(lstm_ae, "training_timed_out", False))
                    else "Temporal autoencoder ready"
                )
                if trained
                else "Temporal autoencoder skipped",
            )
            self._emit_progress(
                progress_callback,
                "start",
                "lstm_scoring",
                "Scoring temporal autoencoder" if trained else "Skipping temporal autoencoder scoring",
            )
            lstm_raw = lstm_ae.predict(sequences)
            self._emit_progress(
                progress_callback,
                "complete",
                "lstm_scoring",
                "Temporal autoencoder scoring complete" if trained else "Temporal autoencoder scoring skipped",
            )

            if isinstance(if_result.get("point_timestamps"), list):
                target_ts = self._parse_utc_timestamps(if_result["point_timestamps"])
            else:
                target_ts = self._parse_utc_timestamps(base.index)

            ts_index_map = {pd.Timestamp(t).isoformat(): i for i, t in enumerate(ts)}
            is_anomaly = np.zeros(len(target_ts), dtype=bool)
            anomaly_score = np.zeros(len(target_ts), dtype=float)
            for i, t in enumerate(target_ts):
                if pd.isna(t):
                    continue
                idx = ts_index_map.get(pd.Timestamp(t).isoformat())
                if idx is None:
                    continue
                is_anomaly[i] = bool(lstm_raw["is_anomaly"][idx])
                anomaly_score[i] = float(lstm_raw["anomaly_score"][idx])

            lstm_result = {
                "is_anomaly": is_anomaly,
                "anomaly_score": anomaly_score,
                "is_trained": trained,
                "time_budget_capped": bool(getattr(lstm_ae, "training_timed_out", False)),
            }

        target_ts = if_result.get("point_timestamps", [])
        self._emit_progress(
            progress_callback,
            "start",
            "cusum_scoring",
            "Running CUSUM drift detection",
        )
        base_if_df = self._aligned_numeric_frame(df, numeric_cols, target_ts)
        cusum = CUSUMDetector()
        cusum_schema_hash = self._schema_hash(["cusum"] + list(numeric_cols))
        cusum_artifact = preloaded_artifacts.get("cusum") if isinstance(preloaded_artifacts, dict) else None
        cusum_loaded = False
        if isinstance(cusum_artifact, dict):
            reg_hash = cusum_artifact.get("feature_schema_hash")
            reg_payload = cusum_artifact.get("artifact_payload")
            if reg_hash == cusum_schema_hash and reg_payload:
                cusum_loaded = cusum.load_bytes(reg_payload)
                if cusum_loaded:
                    logger.info("using_cached_model", model_key="cusum")
        if not cusum_loaded:
            artifact_updates["cusum"] = {
                "feature_schema_hash": cusum_schema_hash,
                "artifact_payload": cusum.to_bytes(),
                "metrics": {
                    "training_duration_seconds": 0.0,
                    "k": cusum.k,
                    "h": cusum.h,
                    "warmup": cusum.warmup,
                },
            }
        cusum_result = cusum.detect(
            base_if_df,
            [c for c in numeric_cols if c in base_if_df.columns],
        )
        self._emit_progress(
            progress_callback,
            "complete",
            "cusum_scoring",
            "CUSUM drift detection complete",
        )

        self._emit_progress(
            progress_callback,
            "start",
            "ensemble_voting",
            "Combining ensemble signals",
        )
        vote = VotingEngine().vote_anomaly(if_result, lstm_result, cusum_result)

        affected = self._affected_parameters(if_result)
        reasoning = ReasoningEngine().generate_anomaly_reasoning(
            {
                "confidence": self._summary_confidence(vote.get("confidence", [])),
            },
            affected,
        )
        self._emit_progress(
            progress_callback,
            "complete",
            "ensemble_voting",
            "Anomaly ensemble signals combined",
        )

        vote_count = vote.get("vote_count", [])
        confidence = vote.get("confidence", [])
        combined_score = vote.get("combined_score", [])
        models_voted = vote.get("models_voted", [])

        anomaly_details = []
        if_details = if_result.get("anomaly_details", []) or []
        for i, detail in enumerate(if_details):
            vc = int(vote_count[i]) if i < len(vote_count) else 0
            conf = confidence[i] if i < len(confidence) else "NORMAL"
            mv = models_voted[i] if i < len(models_voted) else []
            enriched = dict(detail)
            enriched["vote_count"] = vc
            enriched["ensemble_confidence"] = conf
            enriched["models_voted"] = mv
            anomaly_details.append(enriched)

        summary_vc = int(max(vote_count)) if vote_count else 0
        summary_conf = self._summary_confidence(confidence)

        return {
            "anomaly_score": combined_score,
            "is_anomaly": vote.get("is_anomaly", []),
            "point_timestamps": if_result.get("point_timestamps", []),
            "anomaly_details": anomaly_details,
            "columns_used": if_result.get("columns_used", []),
            "total_anomalies": int(sum(1 for x in vote.get("is_anomaly", []) if x)),
            "anomaly_percentage": float((sum(1 for x in vote.get("is_anomaly", []) if x) / max(1, len(vote.get("is_anomaly", [])))) * 100),
            "data_completeness_pct": float(if_result.get("data_completeness_pct", 100.0)),
            "single_parameter_mode": bool(if_result.get("single_parameter_mode", False)),
            "days_available": float(if_result.get("days_available", 0.0)),
            "insufficient_data": bool(if_result.get("insufficient_data", False)),
            "confidence": if_result.get("confidence", {}),
            "ensemble": {
                "vote_count": summary_vc,
                "confidence": summary_conf,
                "models_voted": models_voted,
                "per_model": vote.get("per_model", {}),
                "timeline": {
                    "vote_count": vote_count,
                    "confidence": confidence,
                    "models_voted": models_voted,
                },
            },
            "reasoning": reasoning,
            "data_quality_flags": self._flags(
                df,
                bool(lstm_result.get("is_trained", False)),
                bool(lstm_result.get("time_budget_capped", False)),
            ),
            "cusum_drift_params": cusum_result.get("drift_params", []),
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
    def _aligned_numeric_frame(
        df: pd.DataFrame,
        numeric_cols: List[str],
        target_ts: List[str],
    ) -> pd.DataFrame:
        if not target_ts:
            return pd.DataFrame(index=pd.DatetimeIndex([]))
        base = df.copy()
        if "timestamp" not in base.columns and "_time" in base.columns:
            base = base.rename(columns={"_time": "timestamp"})
        base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True, errors="coerce")
        base = base.dropna(subset=["timestamp"]).sort_values("timestamp")
        if not numeric_cols:
            return pd.DataFrame(index=AnomalyEnsemble._parse_utc_timestamps(target_ts))
        clean = base[["timestamp"] + [c for c in numeric_cols if c in base.columns]].copy()
        clean = clean.set_index("timestamp").resample("1min").mean()
        clean = clean.ffill(limit=15).bfill(limit=15).fillna(0)
        idx = AnomalyEnsemble._parse_utc_timestamps(target_ts)
        return clean.reindex(idx, method="nearest").fillna(0)

    @staticmethod
    def _summary_confidence(confidence: List[str]) -> str:
        if not confidence:
            return "NORMAL"
        if "HIGH" in confidence:
            return "HIGH"
        if "MEDIUM" in confidence:
            return "MEDIUM"
        if "LOW" in confidence:
            return "LOW"
        return "NORMAL"

    @staticmethod
    def _affected_parameters(if_result: Dict[str, Any]) -> List[str]:
        details = if_result.get("anomaly_details", []) or []
        counts: Dict[str, int] = {}
        for d in details:
            for p in d.get("parameters", []):
                counts[p] = counts.get(p, 0) + 1
        return [p for p, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]]

    @staticmethod
    def _flags(
        df: pd.DataFrame,
        lstm_trained: bool,
        lstm_time_budget_capped: bool,
    ) -> List[Dict[str, str]]:
        flags: List[Dict[str, str]] = []
        if len(df) <= 1:
            days = 0.0
        else:
            idx_df = df.copy()
            if "timestamp" not in idx_df.columns and "_time" in idx_df.columns:
                idx_df = idx_df.rename(columns={"_time": "timestamp"})
            idx_df["timestamp"] = pd.to_datetime(idx_df["timestamp"], utc=True, errors="coerce")
            idx_df = idx_df.dropna(subset=["timestamp"]).sort_values("timestamp")
            days = (idx_df["timestamp"].iloc[-1] - idx_df["timestamp"].iloc[0]).total_seconds() / 86400 if len(idx_df) > 1 else 0.0

        if days < (1 / 24):
            flags.append(
                {
                    "type": "data_confidence",
                    "confidence_level": "Very Low",
                    "color": "red",
                    "message": "Demo only — less than 1 hour of data.",
                    "severity": "warning",
                }
            )
        elif days < 1:
            flags.append(
                {
                    "type": "data_confidence",
                    "confidence_level": "Low",
                    "color": "orange",
                    "message": f"Only {int(days * 24)}h of data — directional only.",
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
        elif lstm_time_budget_capped:
            flags.append(
                {
                    "type": "lstm_training_capped",
                    "message": "Temporal model training was time-capped to keep analytics responsive.",
                    "severity": "info",
                }
            )
        return flags
