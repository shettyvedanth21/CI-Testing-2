import json

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.services.result_formatter import ResultFormatter


def test_anomaly_formatter_excludes_internal_model_details_from_customer_payload():
    formatter = ResultFormatter()

    formatted = formatter.format_anomaly_results(
        device_id="DEV-1",
        job_id="job-1",
        anomaly_details=[
            {
                "timestamp": "2026-04-20T10:00:00Z",
                "severity": "high",
                "parameters": ["temperature", "power"],
                "context": "Sustained heat rise under normal load.",
            }
        ],
        total_points=120,
        sensitivity="medium",
        lookback_days=7,
        metadata={"data_points_analyzed": 120, "days_available": 7},
        ensemble={
            "confidence": "HIGH",
            "vote_count": 3,
            "models_voted": [["isolation_forest", "lstm_autoencoder", "cusum"]],
            "per_model": {
                "isolation_forest": {"score": [0.92], "is_anomaly": [True]},
                "lstm_autoencoder": {"score": [0.87], "is_anomaly": [True]},
                "cusum": {"score": [0.88], "is_anomaly": [True]},
            },
        },
        reasoning={
            "summary": "A confirmed abnormal operating pattern was detected.",
            "affected_parameters": ["temperature", "power"],
            "recommended_action": "Inspect the cooling path and load profile.",
            "confidence": "HIGH",
        },
    )

    payload = json.dumps(formatted).lower()

    assert "ensemble" not in formatted
    assert "confidence_summary" in formatted
    assert formatted["confidence_summary"]["title"] == "Analysis Confidence"
    assert "model_used" not in formatted["metadata"]
    assert "isolation_forest" not in payload
    assert "lstm_autoencoder" not in payload
    assert "cusum" not in payload
    assert "hybrid_ensemble_v2" not in payload


def test_failure_formatter_excludes_internal_model_details_and_keeps_customer_confidence_summary():
    formatter = ResultFormatter()

    formatted = formatter.format_failure_prediction_results(
        device_id="DEV-9",
        job_id="job-9",
        failure_probability_pct=72.5,
        risk_breakdown={"safe_pct": 25, "warning_pct": 40, "critical_pct": 35},
        risk_factors=[
            {
                "parameter": "vibration",
                "contribution_pct": 41,
                "trend": "increasing",
                "context": "Rising vibration over the recent baseline.",
                "reasoning": "Elevated oscillation trend.",
                "current_value": 8.0,
                "baseline_value": 5.0,
            }
        ],
        model_confidence="HIGH",
        days_available=10,
        metadata={"data_points_analyzed": 14400},
        ensemble={
            "verdict": "WARNING",
            "votes": 2,
            "confidence": "HIGH",
            "models_voted": ["xgboost", "lstm_classifier"],
            "per_model": {
                "xgboost": {"probability_pct": 79},
                "lstm_classifier": {"probability_pct": 71},
                "degrade_tracker": {"trend_type": "linear"},
            },
        },
        reasoning={
            "summary": "Elevated failure risk detected within 18 hours.",
            "evidence_text": "Evidence strength is moderate because several analytics signals point to emerging risk.",
            "top_risk_factors": ["Vibration"],
            "recommended_actions": ["Inspect bearings and schedule maintenance."],
            "confidence": "HIGH",
        },
    )

    payload = json.dumps(formatted).lower()

    assert "ensemble" not in formatted
    assert formatted["attention_required"] is True
    assert formatted["confidence_summary"]["title"] == "Prediction Confidence"
    assert formatted["confidence_summary"]["evidence_strength"] in {"Strong", "Moderate"}
    assert formatted["reasoning"]["evidence_text"]
    assert "xgboost" not in payload
    assert "lstm_classifier" not in payload
    assert "degrade_tracker" not in payload
