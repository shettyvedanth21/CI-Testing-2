from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

TelemetryCoverageLevel = Literal[
    "full_coverage",
    "partial_coverage",
    "insufficient_coverage",
    "no_coverage",
]


@dataclass(frozen=True)
class TelemetryCoverageResult:
    level: TelemetryCoverageLevel
    coverage_pct: float
    selected_window_days: float | None = None
    covered_days: float | None = None
    selected_window_hours: float | None = None
    covered_duration_hours: float | None = None
    warnings: list[str] = field(default_factory=list)
    minimum_requirements: dict[str, Any] = field(default_factory=dict)
    usable_devices: list[str] = field(default_factory=list)
    skipped_devices: list[dict[str, Any]] = field(default_factory=list)
    usable_for_business_decisions: bool = False
    artifact_generation_allowed: bool = False
    terminal_status: Literal["business_complete", "business_blocked"] = "business_blocked"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "coverage_pct": round(float(self.coverage_pct), 2),
            "selected_window_days": self.selected_window_days,
            "covered_days": self.covered_days,
            "selected_window_hours": self.selected_window_hours,
            "covered_duration_hours": self.covered_duration_hours,
            "warnings": list(self.warnings),
            "minimum_requirements": dict(self.minimum_requirements),
            "usable_devices": list(self.usable_devices),
            "skipped_devices": list(self.skipped_devices),
            "usable_for_business_decisions": bool(self.usable_for_business_decisions),
            "artifact_generation_allowed": bool(self.artifact_generation_allowed),
            "terminal_status": self.terminal_status,
            "message": self.message,
        }


def _window_days(start: datetime | date | None, end: datetime | date | None) -> float | None:
    if start is None or end is None:
        return None
    if isinstance(start, datetime) and isinstance(end, datetime):
        seconds = max((end - start).total_seconds(), 0.0)
        return seconds / 86400.0 if seconds > 0 else 0.0
    return float(max((end - start).days + 1, 0))  # inclusive report-style dates


def _classify_pct(
    coverage_pct: float,
    *,
    has_any_data: bool,
    has_usable_result: bool,
    full_threshold_pct: float,
    minimum_usable_pct: float,
) -> TelemetryCoverageLevel:
    if not has_any_data:
        return "no_coverage"
    if coverage_pct <= 0:
        return "insufficient_coverage" if not has_usable_result else "no_coverage"
    if not has_usable_result or coverage_pct < minimum_usable_pct:
        return "insufficient_coverage"
    if coverage_pct >= full_threshold_pct:
        return "full_coverage"
    return "partial_coverage"


def build_window_coverage_result(
    *,
    selected_window_start: datetime | date | None,
    selected_window_end: datetime | date | None,
    covered_duration_hours: float,
    has_any_data: bool | None = None,
    warnings: list[str] | None = None,
    minimum_usable_pct: float = 1.0,
    full_threshold_pct: float = 95.0,
    has_usable_result: bool = True,
    artifact_generation_allowed: bool | None = None,
) -> TelemetryCoverageResult:
    selected_days = _window_days(selected_window_start, selected_window_end)
    selected_hours = selected_days * 24.0 if selected_days is not None else None
    coverage_pct = 0.0
    if selected_hours and selected_hours > 0:
        coverage_pct = max(0.0, min(100.0, (covered_duration_hours / selected_hours) * 100.0))
    elif covered_duration_hours > 0:
        coverage_pct = 100.0

    level = _classify_pct(
        coverage_pct,
        has_any_data=(covered_duration_hours > 0) if has_any_data is None else bool(has_any_data),
        has_usable_result=has_usable_result,
        full_threshold_pct=full_threshold_pct,
        minimum_usable_pct=minimum_usable_pct,
    )
    usable = level in {"full_coverage", "partial_coverage"}
    artifact_allowed = usable if artifact_generation_allowed is None else bool(artifact_generation_allowed)
    messages = {
        "full_coverage": "Telemetry coverage is sufficient for the selected window.",
        "partial_coverage": "Telemetry coverage is partial; results are usable with coverage warnings.",
        "insufficient_coverage": "Telemetry coverage is insufficient for a trustworthy result.",
        "no_coverage": "No telemetry was available for the selected window.",
    }
    return TelemetryCoverageResult(
        level=level,
        coverage_pct=coverage_pct,
        selected_window_days=round(selected_days, 4) if selected_days is not None else None,
        covered_days=round(covered_duration_hours / 24.0, 4),
        selected_window_hours=round(selected_hours, 4) if selected_hours is not None else None,
        covered_duration_hours=round(max(0.0, covered_duration_hours), 4),
        warnings=list(warnings or []),
        minimum_requirements={
            "minimum_usable_coverage_pct": minimum_usable_pct,
            "full_coverage_threshold_pct": full_threshold_pct,
        },
        usable_for_business_decisions=usable,
        artifact_generation_allowed=artifact_allowed,
        terminal_status="business_complete" if usable else "business_blocked",
        message=messages[level],
    )


def build_device_coverage_result(
    *,
    selected_device_ids: list[str],
    usable_device_ids: list[str],
    has_any_data: bool | None = None,
    skipped_devices: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    minimum_usable_pct: float = 1.0,
    full_threshold_pct: float = 100.0,
    has_usable_result: bool | None = None,
    artifact_generation_allowed: bool | None = None,
) -> TelemetryCoverageResult:
    selected_count = len(selected_device_ids)
    usable_count = len(usable_device_ids)
    coverage_pct = (usable_count / selected_count * 100.0) if selected_count > 0 else 0.0
    usable_result = usable_count > 0 if has_usable_result is None else bool(has_usable_result)
    level = _classify_pct(
        coverage_pct,
        has_any_data=(usable_count > 0) if has_any_data is None else bool(has_any_data),
        has_usable_result=usable_result,
        full_threshold_pct=full_threshold_pct,
        minimum_usable_pct=minimum_usable_pct,
    )
    usable = level in {"full_coverage", "partial_coverage"}
    artifact_allowed = usable if artifact_generation_allowed is None else bool(artifact_generation_allowed)
    messages = {
        "full_coverage": "All selected devices have usable telemetry coverage.",
        "partial_coverage": "Some selected devices have usable telemetry coverage.",
        "insufficient_coverage": "Selected device coverage is insufficient for a trustworthy result.",
        "no_coverage": "No selected devices had usable telemetry coverage.",
    }
    return TelemetryCoverageResult(
        level=level,
        coverage_pct=coverage_pct,
        warnings=list(warnings or []),
        minimum_requirements={
            "minimum_usable_coverage_pct": minimum_usable_pct,
            "full_coverage_threshold_pct": full_threshold_pct,
            "selected_device_count": selected_count,
            "minimum_usable_device_count": 1 if selected_count else 0,
        },
        usable_devices=list(usable_device_ids),
        skipped_devices=list(skipped_devices or []),
        usable_for_business_decisions=usable,
        artifact_generation_allowed=artifact_allowed,
        terminal_status="business_complete" if usable else "business_blocked",
        message=messages[level],
    )
