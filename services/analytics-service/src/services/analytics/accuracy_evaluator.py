"""Accuracy evaluator against labeled failure events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.models.database import AccuracyEvaluation, AnalyticsJob, FailureEventLabel


@dataclass
class EvalResult:
    sample_size: int
    labeled_events: int
    precision: float
    recall: float
    f1_score: float
    false_alert_rate: float
    avg_lead_hours: Optional[float]
    is_certified: bool
    notes: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "sample_size": self.sample_size,
            "labeled_events": self.labeled_events,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
            "false_alert_rate": round(self.false_alert_rate, 4),
            "avg_lead_hours": None if self.avg_lead_hours is None else round(self.avg_lead_hours, 2),
            "is_certified": self.is_certified,
            "notes": self.notes,
        }


class AccuracyEvaluator:
    """Computes failure prediction quality from completed jobs + labels."""

    POSITIVE_VERDICTS = {"CRITICAL", "WARNING", "WATCH"}

    @staticmethod
    async def evaluate_failure_predictions(
        session: AsyncSession,
        device_id: Optional[str] = None,
        lookback_days: int = 90,
        lead_window_hours: int = 24,
    ) -> EvalResult:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=max(1, int(lookback_days)))

        job_q = (
            select(
                AnalyticsJob.job_id,
                AnalyticsJob.device_id,
                AnalyticsJob.created_at,
                AnalyticsJob.completed_at,
                AnalyticsJob.results,
            )
            .where(AnalyticsJob.analysis_type == "prediction")
            .where(AnalyticsJob.status == "completed")
            .where(AnalyticsJob.completed_at >= start)
            .limit(10000)
        )
        label_q = (
            select(FailureEventLabel)
            .where(FailureEventLabel.event_time >= start)
            .limit(10000)
        )
        if device_id:
            job_q = job_q.where(AnalyticsJob.device_id == device_id)
            label_q = label_q.where(FailureEventLabel.device_id == device_id)

        jobs = list((await session.execute(job_q)).all())
        labels = list((await session.execute(label_q)).scalars().all())

        predictions = []
        for job_row in jobs:
            results = job_row.results or {}
            formatted = results.get("formatted", {}) if isinstance(results, dict) else {}
            attention_required = bool(formatted.get("attention_required")) if isinstance(formatted, dict) else False
            summary = formatted.get("summary", {}) if isinstance(formatted, dict) else {}
            risk_level = str((summary or {}).get("failure_risk") or "").strip().lower()
            positive = attention_required or risk_level in {"medium", "high", "critical"}
            if not positive and not risk_level:
                continue
            predictions.append(
                {
                    "job_id": job_row.job_id,
                    "device_id": job_row.device_id,
                    "time": job_row.completed_at or job_row.created_at or now,
                    "positive": positive,
                }
            )

        by_device: Dict[str, List[FailureEventLabel]] = {}
        for label in labels:
            by_device.setdefault(label.device_id, []).append(label)

        tp = fp = 0
        lead_hours: List[float] = []
        matched_labels: set[str] = set()
        window = timedelta(hours=max(1, int(lead_window_hours)))

        for pred in predictions:
            if not pred["positive"]:
                continue
            device_labels = by_device.get(pred["device_id"], [])
            match = None
            for ev in device_labels:
                if str(ev.id) in matched_labels:
                    continue
                if pred["time"] <= ev.event_time <= (pred["time"] + window):
                    match = ev
                    break
            if match:
                tp += 1
                matched_labels.add(str(match.id))
                lead = (match.event_time - pred["time"]).total_seconds() / 3600.0
                lead_hours.append(max(0.0, lead))
            else:
                fp += 1

        fn = max(0, len(labels) - len(matched_labels))
        precision = (tp / (tp + fp)) if (tp + fp) else 0.0
        recall = (tp / (tp + fn)) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        false_alert_rate = (fp / (tp + fp)) if (tp + fp) else 0.0
        avg_lead = (sum(lead_hours) / len(lead_hours)) if lead_hours else None

        settings = get_settings()
        enough_labels = len(labels) >= settings.accuracy_min_labeled_events
        is_certified = (
            enough_labels
            and precision >= settings.accuracy_certification_min_precision
            and recall >= settings.accuracy_certification_min_recall
        )
        notes = (
            "Certified against labeled events."
            if is_certified
            else f"Not certified: labels={len(labels)} (min {settings.accuracy_min_labeled_events}), "
            f"precision>={settings.accuracy_certification_min_precision}, "
            f"recall>={settings.accuracy_certification_min_recall}."
        )

        result = EvalResult(
            sample_size=len(predictions),
            labeled_events=len(labels),
            precision=precision,
            recall=recall,
            f1_score=f1,
            false_alert_rate=false_alert_rate,
            avg_lead_hours=avg_lead,
            is_certified=is_certified,
            notes=notes,
        )

        record = AccuracyEvaluation(
            id=str(uuid.uuid4()),
            analysis_type="prediction",
            scope_device_id=device_id,
            sample_size=result.sample_size,
            labeled_events=result.labeled_events,
            precision=result.precision,
            recall=result.recall,
            f1_score=result.f1_score,
            false_alert_rate=result.false_alert_rate,
            avg_lead_hours=result.avg_lead_hours,
            is_certified=1 if result.is_certified else 0,
            notes=result.notes,
        )
        session.add(record)
        await session.commit()

        return result
