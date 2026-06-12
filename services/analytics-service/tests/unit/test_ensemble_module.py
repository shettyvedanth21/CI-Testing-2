import math
import pickle

import numpy as np
import pandas as pd
import pytest

from src.services.analytics.ensemble.anomaly_ensemble import AnomalyEnsemble
from src.services.analytics.ensemble.voting_engine import VotingEngine
from src.services.analytics.explainer.reasoning_engine import ReasoningEngine
from src.services.analytics.models.cusum_detector import CUSUMDetector
from src.services.analytics.models.degradation_tracker import DegradationTracker
from src.services.analytics.models.lstm_autoencoder import LSTMAnomalyAutoencoder
from src.services.analytics.models.lstm_classifier import LSTMFailureClassifier
from src.services.job_runner import _json_safe


def test_voting_engine_anomaly_matrix():
    engine = VotingEngine()

    def run_case(if_flag: bool, lstm_flag: bool, cusum_flag: bool):
        return engine.vote_anomaly(
            {
                "is_anomaly": np.array([if_flag]),
                "anomaly_score": np.array([0.9 if if_flag else 0.1]),
            },
            {
                "is_anomaly": np.array([lstm_flag]),
                "anomaly_score": np.array([0.9 if lstm_flag else 0.1]),
            },
            {
                "is_anomaly": np.array([cusum_flag]),
                "anomaly_score": np.array([0.9 if cusum_flag else 0.1]),
            },
        )

    r0 = run_case(False, False, False)
    assert r0["is_anomaly"] == [False]
    assert r0["vote_count"] == [0]
    assert r0["confidence"] == ["NORMAL"]

    r1 = run_case(True, False, False)
    assert r1["is_anomaly"] == [False]
    assert r1["vote_count"] == [1]
    assert r1["confidence"] == ["LOW"]

    r2 = run_case(True, True, False)
    assert r2["is_anomaly"] == [True]
    assert r2["vote_count"] == [2]
    assert r2["confidence"] == ["MEDIUM"]

    r3 = run_case(True, True, True)
    assert r3["is_anomaly"] == [True]
    assert r3["vote_count"] == [3]
    assert r3["confidence"] == ["HIGH"]


def test_voting_engine_failure_matrix():
    engine = VotingEngine()

    low = np.full(40, 0.1)
    high = np.full(40, 0.9)
    deg_off = {"trend_type": "stable", "hours_to_failure": None}
    deg_on = {"trend_type": "linear", "hours_to_failure": 10, "label": "HIGH"}

    r0 = engine.vote_failure(low, low, deg_off)
    assert r0["votes"] == 0 and r0["verdict"] == "NORMAL"

    r1 = engine.vote_failure(high, low, deg_off)
    assert r1["votes"] == 1 and r1["verdict"] == "WATCH"

    r2 = engine.vote_failure(high, high, deg_off)
    assert r2["votes"] == 2 and r2["verdict"] == "WARNING"

    r3 = engine.vote_failure(high, high, deg_on)
    assert r3["votes"] == 3 and r3["verdict"] == "CRITICAL"


def test_lstm_autoencoder_non_trained_fallback():
    model = LSTMAnomalyAutoencoder()
    short = np.random.randn(10, 30, 3).astype(np.float32)
    assert model.train(short) is False

    out = model.predict(short)
    assert out["is_trained"] is False
    assert out["is_anomaly"].shape == (10,)
    assert np.all(out["anomaly_score"] == 0)


def test_lstm_classifier_non_trained_fallback():
    model = LSTMFailureClassifier()
    short_seq = np.random.randn(10, 30, 4).astype(np.float32)
    short_labels = np.zeros(10, dtype=int)
    assert model.train(short_seq, short_labels) is False

    proba = model.predict_proba(short_seq)
    assert proba.shape == (10,)
    assert np.all(proba == 0)


def test_cusum_warmup_and_drift_detection():
    warmup_detector = CUSUMDetector(warmup=20)
    short_df = pd.DataFrame({"x": np.linspace(0, 1, 10)})
    short_out = warmup_detector.detect(short_df, ["x"])
    assert len(short_out["is_anomaly"]) == 10
    assert not short_out["is_anomaly"].any()

    n = 120
    stable = np.zeros(60)
    drift = np.linspace(0.0, 6.0, 60)
    df = pd.DataFrame({"x": np.concatenate([stable, drift])})
    drift_out = CUSUMDetector(k=0.2, h=3.0, warmup=20).detect(df, ["x"])
    assert len(drift_out["is_anomaly"]) == n
    assert drift_out["is_anomaly"][-1]
    assert "x" in drift_out["drift_params"]


def test_degradation_tracker_branches():
    tracker = DegradationTracker()

    insufficient = tracker.estimate_ttf(pd.Series(np.linspace(0.1, 0.2, 10)))
    assert insufficient["trend_type"] == "insufficient_data"

    critical_series = pd.Series(np.linspace(0.2, 0.9, 50))
    critical = tracker.estimate_ttf(critical_series)
    assert critical["trend_type"] == "critical"
    assert critical["hours_to_failure"] == 0.0

    linear_series = pd.Series(np.linspace(0.2, 0.8, 200))
    linear = tracker.estimate_ttf(linear_series)
    assert linear["trend_type"] == "linear"
    assert linear["hours_to_failure"] is not None
    assert linear["hours_to_failure"] >= 0

    x = np.arange(200, dtype=float)
    exp_series = pd.Series(0.1 * np.exp(0.01 * x))
    exponential = tracker.estimate_ttf(exp_series)
    assert exponential["trend_type"] in ("exponential", "linear")
    assert exponential["hours_to_failure"] is not None

    stable_series = pd.Series(np.linspace(0.4, 0.2, 120))
    stable = tracker.estimate_ttf(stable_series)
    assert stable["trend_type"] == "stable"
    assert stable["hours_to_failure"] is None


def test_reasoning_engine_outputs():
    engine = ReasoningEngine()

    failure = engine.generate_failure_reasoning(
        {
            "verdict": "WARNING",
            "confidence": "MEDIUM",
            "votes": 2,
            "models_voted": ["xgboost", "lstm_classifier"],
            "hours_to_failure": 18.0,
            "ttf_confidence_interval": [12.0, 36.0],
        },
        [("current_std_60m", 0.4), ("temp_trend_60m", 0.3)],
        {"trend_type": "linear", "trend_r2": 0.81},
    )
    assert isinstance(failure["summary"], str) and failure["summary"]
    assert isinstance(failure["evidence_text"], str)
    assert isinstance(failure["recommended_actions"], list) and failure["recommended_actions"]
    assert "xgboost" not in failure["evidence_text"].lower()
    assert "models agree" not in failure["evidence_text"].lower()

    anomaly = engine.generate_anomaly_reasoning({"confidence": "LOW"}, ["temperature"])
    assert isinstance(anomaly["summary"], str) and anomaly["summary"]
    assert anomaly["affected_parameters"] == ["temperature"]
    assert "models agree" not in anomaly["summary"].lower()


def test_json_safe_recurses_nested_nan_inf():
    payload = {
        "outer": {
            "score": float("nan"),
            "nested": [1.0, float("inf"), {"v": -float("inf")}],
        },
        "ok": 5.0,
    }
    safe = _json_safe(payload)

    assert safe["outer"]["score"] is None
    assert safe["outer"]["nested"][1] is None
    assert safe["outer"]["nested"][2]["v"] is None
    assert safe["ok"] == 5.0
    assert not math.isnan(safe["ok"])


@pytest.fixture
def anomaly_progress_df():
    timestamps = pd.date_range("2026-01-01", periods=80, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "temperature": np.linspace(20.0, 25.0, 80),
            "power": np.linspace(100.0, 130.0, 80),
        }
    )


def test_anomaly_ensemble_progress_labels_show_cached_branches(monkeypatch, anomaly_progress_df):
    events: list[tuple[str, str, str]] = []

    class _StubPipeline:
        def prepare_data(self, df, params):
            return df, df

        def train(self, train_df, model_name, params):
            raise AssertionError("cached isolation forest should not retrain")

        def predict(self, df, model, params):
            size = len(df)
            return {
                "is_anomaly": [False] * size,
                "anomaly_score": [0.1] * size,
                "point_timestamps": [ts.isoformat() for ts in df["timestamp"]],
                "anomaly_details": [],
                "columns_used": ["temperature", "power"],
                "data_completeness_pct": 100.0,
                "single_parameter_mode": False,
                "days_available": 1.0,
                "insufficient_data": False,
                "confidence": {},
            }

    class _StubLSTM:
        def load_bytes(self, payload):
            return True

        def train(self, sequences):
            raise AssertionError("cached temporal autoencoder should not retrain")

        def predict(self, sequences):
            n = len(sequences)
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n, dtype=float),
            }

    class _StubCUSUM:
        def __init__(self):
            self.k = 0.5
            self.h = 5.0
            self.warmup = 20

        def load_bytes(self, payload):
            return True

        def to_bytes(self):
            return b"cusum"

        def detect(self, df, numeric_cols):
            n = len(df)
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n, dtype=float),
                "drift_params": [],
                "is_trained": True,
            }

    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.AnomalyDetectionPipeline", _StubPipeline)
    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.LSTMAnomalyAutoencoder", _StubLSTM)
    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.CUSUMDetector", _StubCUSUM)

    result = AnomalyEnsemble().run(
        anomaly_progress_df,
        {
            "__artifacts": {
                "isolation_forest": {"artifact_payload": pickle.dumps({"cached": True}), "feature_schema_hash": AnomalyEnsemble._schema_hash(["if", "temperature", "power"])},
                "lstm_autoencoder": {"artifact_payload": b"cached-lstm", "feature_schema_hash": AnomalyEnsemble._schema_hash(["lstm_ae", "30", "temperature", "power"])},
                "cusum": {"artifact_payload": b"cached-cusum", "feature_schema_hash": AnomalyEnsemble._schema_hash(["cusum", "temperature", "power"])},
            }
        },
        progress_callback=lambda event_type, stage_key, label: events.append((event_type, stage_key, label)),
    )

    labels = [label for _, _, label in events]
    assert "Loading isolation forest" in labels
    assert "Loading temporal autoencoder" in labels
    assert "Running CUSUM drift detection" in labels
    assert "Combining ensemble signals" in labels
    assert result["artifact_updates"] == {}


def test_anomaly_ensemble_progress_labels_show_training_branches(monkeypatch, anomaly_progress_df):
    events: list[tuple[str, str, str]] = []

    class _StubPipeline:
        def prepare_data(self, df, params):
            return df, df

        def train(self, train_df, model_name, params):
            return {"confidence": {"contamination": 0.02}}

        def predict(self, df, model, params):
            size = len(df)
            return {
                "is_anomaly": [False] * size,
                "anomaly_score": [0.1] * size,
                "point_timestamps": [ts.isoformat() for ts in df["timestamp"]],
                "anomaly_details": [],
                "columns_used": ["temperature", "power"],
                "data_completeness_pct": 100.0,
                "single_parameter_mode": False,
                "days_available": 1.0,
                "insufficient_data": False,
                "confidence": {},
            }

    class _StubLSTM:
        def __init__(self):
            self._trained = False

        def load_bytes(self, payload):
            return False

        def train(self, sequences):
            self._trained = True
            return True

        def predict(self, sequences):
            n = len(sequences)
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n, dtype=float),
            }

        def to_bytes(self):
            return b"trained-lstm"

    class _StubCUSUM:
        def __init__(self):
            self.k = 0.5
            self.h = 5.0
            self.warmup = 20

        def load_bytes(self, payload):
            return False

        def to_bytes(self):
            return b"cusum"

        def detect(self, df, numeric_cols):
            n = len(df)
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n, dtype=float),
                "drift_params": [],
                "is_trained": True,
            }

    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.AnomalyDetectionPipeline", _StubPipeline)
    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.LSTMAnomalyAutoencoder", _StubLSTM)
    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.CUSUMDetector", _StubCUSUM)

    result = AnomalyEnsemble().run(
        anomaly_progress_df,
        {},
        progress_callback=lambda event_type, stage_key, label: events.append((event_type, stage_key, label)),
    )

    labels = [label for _, _, label in events]
    assert "Training isolation forest" in labels
    assert "Training temporal autoencoder" in labels
    assert "Scoring temporal autoencoder" in labels
    assert "Running CUSUM drift detection" in labels
    assert result["artifact_updates"]["isolation_forest"]["artifact_payload"]
    assert result["artifact_updates"]["lstm_autoencoder"]["artifact_payload"]


def test_anomaly_ensemble_reports_time_capped_temporal_model(monkeypatch, anomaly_progress_df):
    class _StubPipeline:
        def prepare_data(self, df, params):
            return df, df

        def train(self, train_df, model_name, params):
            return {"confidence": {"contamination": 0.02}}

        def predict(self, df, model, params):
            size = len(df)
            return {
                "is_anomaly": [False] * size,
                "anomaly_score": [0.1] * size,
                "point_timestamps": [ts.isoformat() for ts in df["timestamp"]],
                "anomaly_details": [],
                "columns_used": ["temperature", "power"],
                "data_completeness_pct": 100.0,
                "single_parameter_mode": False,
                "days_available": 1.0,
                "insufficient_data": False,
                "confidence": {},
            }

    class _StubLSTM:
        def __init__(self):
            self.training_timed_out = True
            self.training_stop_reason = "time_budget_exceeded"
            self.trained_epochs = 2

        def load_bytes(self, payload):
            return False

        def train(self, sequences):
            return True

        def predict(self, sequences):
            n = len(sequences)
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n, dtype=float),
            }

        def to_bytes(self):
            return b"trained-lstm"

    class _StubCUSUM:
        def __init__(self):
            self.k = 0.5
            self.h = 5.0
            self.warmup = 20

        def load_bytes(self, payload):
            return False

        def to_bytes(self):
            return b"cusum"

        def detect(self, df, numeric_cols):
            n = len(df)
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n, dtype=float),
                "drift_params": [],
                "is_trained": True,
            }

    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.AnomalyDetectionPipeline", _StubPipeline)
    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.LSTMAnomalyAutoencoder", _StubLSTM)
    monkeypatch.setattr("src.services.analytics.ensemble.anomaly_ensemble.CUSUMDetector", _StubCUSUM)

    result = AnomalyEnsemble().run(anomaly_progress_df, {})

    flags = result.get("data_quality_flags", [])
    assert any(flag.get("type") == "lstm_training_capped" for flag in flags)
    assert result["artifact_updates"]["lstm_autoencoder"]["metrics"]["time_budget_capped"] is True
    assert result["artifact_updates"]["lstm_autoencoder"]["metrics"]["stop_reason"] == "time_budget_exceeded"
