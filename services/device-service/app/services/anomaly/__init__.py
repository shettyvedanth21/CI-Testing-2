"""Machine anomaly detection package."""

from .aggregator import aggregate_daily_counts, aggregate_weekly_counts
from .baseline_learner import learn_anomaly_baseline
from .detector import detect_anomalies, merge_events
from .helpers import (
    build_anomaly_baseline_dict,
    build_anomaly_event_dict,
    build_daily_count_dict,
    build_weekly_count_dict,
)
from .service import (
    aggregate_daily_counts_for_device,
    aggregate_weekly_counts_for_device,
    cleanup_old_anomaly_rows,
    detect_device_anomalies,
    load_active_anomaly_baselines_for_device,
    load_recent_anomaly_events_for_device,
    persist_anomaly_baselines,
    persist_anomaly_event,
    persist_daily_count,
    persist_weekly_count,
    refresh_anomaly_baselines_for_device,
    update_anomaly_event,
)
from .types import (
    AnomalyBaselineInput,
    AnomalyCandidate,
    AnomalyFieldBaseline,
    DailyCountResult,
    WeeklyCountResult,
    DEFAULT_TIME_WINDOW,
    SUPPORTED_FIELDS,
)

__all__ = [
    "AnomalyBaselineInput",
    "AnomalyCandidate",
    "AnomalyFieldBaseline",
    "DailyCountResult",
    "WeeklyCountResult",
    "DEFAULT_TIME_WINDOW",
    "SUPPORTED_FIELDS",
    "aggregate_daily_counts",
    "aggregate_daily_counts_for_device",
    "aggregate_weekly_counts",
    "aggregate_weekly_counts_for_device",
    "build_anomaly_baseline_dict",
    "build_anomaly_event_dict",
    "build_daily_count_dict",
    "build_weekly_count_dict",
    "cleanup_old_anomaly_rows",
    "detect_anomalies",
    "detect_device_anomalies",
    "learn_anomaly_baseline",
    "load_active_anomaly_baselines_for_device",
    "load_recent_anomaly_events_for_device",
    "merge_events",
    "persist_anomaly_baselines",
    "persist_anomaly_event",
    "persist_daily_count",
    "persist_weekly_count",
    "refresh_anomaly_baselines_for_device",
    "update_anomaly_event",
]
