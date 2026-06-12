"""Plain-language reasoning for anomaly and failure outputs."""

from typing import List, Optional, Tuple

TRANSLATIONS = {
    "_mean_": "average level of",
    "_std_": "variability in",
    "_max_": "peak value of",
    "_roc_": "rate of change in",
    "_cv_": "instability in",
    "_above_p95": "frequent high readings in",
    "_below_p05": "frequent low readings in",
    "_trend_": "worsening trend in",
    "current": "electrical current",
    "voltage": "voltage",
    "temp": "temperature",
    "vibration": "vibration",
    "power": "power consumption",
    "pressure": "pressure",
}


class ReasoningEngine:
    """Builds concise explanations suitable for operations users."""

    def generate_failure_reasoning(
        self,
        verdict: dict,
        risk_factors: List[Tuple[str, float]],
        degradation: dict,
    ) -> dict:
        risk = verdict.get("verdict", "NORMAL")
        conf = verdict.get("confidence", "LOW")
        votes = int(verdict.get("votes", 0))
        hours = verdict.get("hours_to_failure")
        ci = verdict.get("ttf_confidence_interval")

        time_desc = self._time_desc(hours)
        ci_text = self._ci_text(ci)

        if risk == "CRITICAL":
            summary = f"Failure is likely {time_desc}{ci_text}."
        elif risk == "WARNING":
            suffix = f" {time_desc}" if time_desc else ""
            summary = f"Elevated failure risk detected{suffix}."
        elif risk == "WATCH":
            summary = "Early signs of degradation were detected. Monitor closely."
        else:
            summary = "Current telemetry indicates stable machine behavior."

        evidence_map = {
            3: "Evidence strength is high because multiple analytics signals indicate the same risk pattern.",
            2: "Evidence strength is moderate because several analytics signals point to emerging risk.",
            1: "Evidence strength is developing because an early warning pattern was detected in the telemetry.",
            0: "Evidence strength is limited because current telemetry does not show a sustained risk pattern.",
        }

        trend = degradation.get("trend_type", "stable")
        r2 = float(degradation.get("trend_r2", 0) or 0)
        if trend == "exponential":
            trend_text = f"Degradation accelerating (exponential trend, R²={r2:.2f})."
        elif trend == "linear":
            trend_text = f"Degradation progressing steadily (linear, R²={r2:.2f})."
        elif trend == "critical":
            trend_text = "Machine at critical degradation threshold."
        else:
            trend_text = "No clear degradation trend detected."

        plain_factors = [self._translate(n) for n, _ in (risk_factors or [])[:3]]

        if risk == "CRITICAL":
            actions = [
                "Stop machine if safe",
                "Dispatch maintenance immediately",
                "Do not restart without full inspection",
            ]
        elif risk == "WARNING":
            actions = [
                "Schedule maintenance within 24 hours",
                "Reduce machine load if possible",
                "Monitor continuously until maintenance",
            ]
        elif risk == "WATCH":
            actions = [
                "Add to next scheduled maintenance",
                "Increase monitoring frequency",
                "Document current readings",
            ]
        else:
            actions = ["Continue normal operation"]

        return {
            "summary": summary,
            "evidence_text": evidence_map.get(votes, ""),
            "trend_text": trend_text,
            "top_risk_factors": plain_factors,
            "recommended_actions": actions,
            "confidence": conf,
        }

    def generate_anomaly_reasoning(self, vote_result: dict, affected_params: List[str]) -> dict:
        conf = vote_result.get("confidence", "NORMAL")

        if conf == "HIGH":
            summary = "A confirmed abnormal operating pattern was detected."
            action = "Inspect machine immediately."
        elif conf == "MEDIUM":
            summary = "A likely abnormal operating pattern was detected."
            action = "Schedule inspection within 24 hours."
        elif conf == "LOW":
            summary = "A small deviation from normal behavior was detected."
            action = "Continue monitoring. Log observation."
        else:
            summary = "Operating conditions appear normal."
            action = "No action required."

        return {
            "summary": summary,
            "affected_parameters": affected_params[:5],
            "recommended_action": action,
            "confidence": conf,
        }

    @staticmethod
    def _time_desc(hours: Optional[float]) -> Optional[str]:
        if hours is None:
            return None
        if hours <= 4:
            return "within the next few hours"
        if hours <= 24:
            return f"within {int(hours)} hours"
        if hours <= 72:
            return f"within {int(hours / 24) + 1} days"
        return f"within {int(hours / 24)} days"

    @staticmethod
    def _ci_text(ci: Optional[list]) -> str:
        if not ci:
            return ""
        if ci[1] > 24:
            return f" (range: {int(ci[0] / 24)}–{int(ci[1] / 24)} days)"
        return f" (range: {int(ci[0])}–{int(ci[1])} hours)"

    @staticmethod
    def _translate(feature_name: str) -> str:
        name = feature_name.lower()
        for k, v in TRANSLATIONS.items():
            name = name.replace(k.lower(), f" {v} ")
        return name.strip().replace("  ", " ").capitalize()
