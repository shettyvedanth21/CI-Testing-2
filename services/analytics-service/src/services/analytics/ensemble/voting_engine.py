"""Shared voting logic for anomaly and failure ensembles."""

import numpy as np


class VotingEngine:
    """Computes model-agreement verdicts and normalized confidence."""

    def vote_anomaly(self, if_r: dict, lstm_r: dict, cusum_r: dict) -> dict:
        n = len(if_r["is_anomaly"])

        v = np.stack(
            [
                np.asarray(if_r["is_anomaly"]).astype(int),
                np.asarray(lstm_r["is_anomaly"]).astype(int),
                np.asarray(cusum_r["is_anomaly"]).astype(int),
            ],
            axis=1,
        )
        vote_count = v.sum(axis=1)

        scores = np.stack(
            [
                np.asarray(if_r["anomaly_score"], dtype=float),
                np.asarray(lstm_r["anomaly_score"], dtype=float),
                np.asarray(cusum_r["anomaly_score"], dtype=float),
            ],
            axis=1,
        )
        combined = scores @ np.array([0.40, 0.40, 0.20])

        conf = np.where(
            vote_count == 3,
            "HIGH",
            np.where(vote_count == 2, "MEDIUM", np.where(vote_count == 1, "LOW", "NORMAL")),
        )

        models_voted = []
        for i in range(n):
            mv = []
            if v[i, 0]:
                mv.append("isolation_forest")
            if v[i, 1]:
                mv.append("lstm_autoencoder")
            if v[i, 2]:
                mv.append("cusum")
            models_voted.append(mv)

        flagged = (vote_count >= 2).astype(bool)

        return {
            "is_anomaly": flagged.tolist(),
            "vote_count": vote_count.tolist(),
            "confidence": conf.tolist(),
            "combined_score": combined.tolist(),
            "models_voted": models_voted,
            "per_model": {
                "isolation_forest": {
                    "is_anomaly": np.asarray(if_r["is_anomaly"]).astype(bool).tolist(),
                    "score": np.asarray(if_r["anomaly_score"], dtype=float).tolist(),
                    "is_trained": bool(if_r.get("is_trained", True)),
                },
                "lstm_autoencoder": {
                    "is_anomaly": np.asarray(lstm_r["is_anomaly"]).astype(bool).tolist(),
                    "score": np.asarray(lstm_r["anomaly_score"], dtype=float).tolist(),
                    "is_trained": bool(lstm_r.get("is_trained", False)),
                },
                "cusum": {
                    "is_anomaly": np.asarray(cusum_r["is_anomaly"]).astype(bool).tolist(),
                    "score": np.asarray(cusum_r["anomaly_score"], dtype=float).tolist(),
                    "drift_params": cusum_r.get("drift_params", []),
                    "is_trained": True,
                },
            },
        }

    def vote_failure(
        self,
        xgb_proba: np.ndarray,
        lstm_proba: np.ndarray,
        degradation: dict,
        threshold: float = 0.50,
        xgb_trained: bool = True,
        lstm_trained: bool = True,
    ) -> dict:
        n_recent = max(20, int(len(xgb_proba) * 0.20)) if len(xgb_proba) else 20

        xgb_sum = float(np.percentile(xgb_proba[-n_recent:], 90)) if len(xgb_proba) else 0.0
        lstm_sum = float(np.percentile(lstm_proba[-n_recent:], 90)) if len(lstm_proba) else 0.0
        deg_vote = degradation.get("trend_type") in ("linear", "exponential") and (degradation.get("hours_to_failure") or 9999) < 168

        xv = xgb_sum >= threshold
        lv = lstm_sum >= threshold
        votes = int(xv) + int(lv) + int(deg_vote)

        voted = []
        if xv:
            voted.append("xgboost")
        if lv:
            voted.append("lstm_classifier")
        if deg_vote:
            voted.append("degradation_tracker")

        verdict_map = {3: "CRITICAL", 2: "WARNING", 1: "WATCH", 0: "NORMAL"}
        conf_map = {3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "LOW"}

        combined = round(0.40 * xgb_sum + 0.40 * lstm_sum + 0.20 * (1.0 if deg_vote else 0.0), 4)

        return {
            "verdict": verdict_map[votes],
            "confidence": conf_map[votes],
            "votes": votes,
            "models_voted": voted,
            "combined_probability": combined,
            "hours_to_failure": degradation.get("hours_to_failure"),
            "ttf_label": degradation.get("label"),
            "ttf_confidence_interval": degradation.get("confidence_interval_hours"),
            "ttf_reliable": degradation.get("is_reliable", False),
            "per_model": {
                "xgboost": {
                    "probability_pct": round(xgb_sum * 100, 1),
                    "voted_high": xv,
                    "is_trained": bool(xgb_trained),
                },
                "lstm_classifier": {
                    "probability_pct": round(lstm_sum * 100, 1),
                    "voted_high": lv,
                    "is_trained": bool(lstm_trained),
                },
                "degradation_tracker": {
                    "trend_type": degradation.get("trend_type"),
                    "trend_r2": degradation.get("trend_r2"),
                    "voted_high": deg_vote,
                    "hours_to_failure": degradation.get("hours_to_failure"),
                    "is_reliable": degradation.get("is_reliable", False),
                    "is_trained": True,
                },
            },
        }
