"""Confidence module for premium ML analytics."""

from dataclasses import asdict, dataclass
from typing import Dict


@dataclass(frozen=True)
class ConfidenceResult:
    level: str
    badge_color: str
    contamination: float
    zscore_multiplier: float
    banner_text: str
    banner_style: str
    minutes_available: float
    days_available: float

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _human_data_window(points: int) -> str:
    if points < 60:
        return f"{max(1, points)} minute" + ("" if max(1, points) == 1 else "s")
    if points < 1440:
        hours = max(1, round(points / 60))
        return f"{hours} hour" + ("" if hours == 1 else "s")
    days = max(1, round(points / 1440))
    return f"{days} day" + ("" if days == 1 else "s")


def get_confidence(data_points: int, sensitivity: str = "medium") -> ConfidenceResult:
    """
    1 point == 1 minute after resampling.
    """
    points = max(0, int(data_points))
    mins = float(points)
    hours = mins / 60.0
    days = mins / 1440.0
    window = _human_data_window(points)

    if points < 10:
        return ConfidenceResult(
            level="Low",
            badge_color="#DC2626",
            contamination=0.01,
            zscore_multiplier=1.5,
            banner_text=(
                f"Low confidence: only {window} of data. "
                "Results are indicative; collect more telemetry."
            ),
            banner_style="red",
            minutes_available=mins,
            days_available=days,
        )

    if points < 360:
        return ConfidenceResult(
            level="Low",
            badge_color="#DC2626",
            contamination=0.01,
            zscore_multiplier=1.3,
            banner_text=(
                f"Low confidence: only {window} of data. "
                "Re-run after 6 hours for stronger reliability."
            ),
            banner_style="red",
            minutes_available=mins,
            days_available=days,
        )

    if points < 10080:
        return ConfidenceResult(
            level="Moderate",
            badge_color="#D97706",
            contamination=0.02,
            zscore_multiplier=1.1 if points < 1440 else 1.0,
            banner_text=(
                f"Moderate confidence: {window} of data. "
                "Re-run after 7 days for high confidence."
            ),
            banner_style="amber",
            minutes_available=mins,
            days_available=days,
        )

    if points < 43200:
        return ConfidenceResult(
            level="High",
            badge_color="#059669",
            contamination=0.03,
            zscore_multiplier=1.0,
            banner_text=(
                f"High confidence: {window} of data. "
                "Results are reliable for maintenance decisions."
            ),
            banner_style="green",
            minutes_available=mins,
            days_available=days,
        )

    return ConfidenceResult(
        level="Very High",
        badge_color="#4F46E5",
        contamination=0.05,
        zscore_multiplier=1.0,
        banner_text=(
            f"Very high confidence: {window} of data. "
            "Long-cycle behavior captured for maximum reliability."
        ),
        banner_style="indigo",
        minutes_available=mins,
        days_available=days,
    )
