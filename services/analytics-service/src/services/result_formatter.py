"""Result formatter for dashboard-ready analytics payloads."""

from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, List

from src.services.analytics.confidence import get_confidence


class ResultFormatter:
    """Converts raw pipeline output to UI-ready structured payloads."""

    def format_anomaly_results(
        self,
        device_id: str,
        job_id: str,
        anomaly_details: List[Dict[str, Any]],
        total_points: int,
        sensitivity: str,
        lookback_days: int,
        metadata: Dict[str, Any] | None = None,
        ensemble: Dict[str, Any] | None = None,
        reasoning: Dict[str, Any] | None = None,
        data_quality_flags: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        total_anomalies = len(anomaly_details)
        anomaly_rate = round((total_anomalies / total_points * 100) if total_points > 0 else 0.0, 2)

        weights = {"high": 3, "medium": 2, "low": 1}
        raw_score = sum(weights.get(a.get("severity", "low"), 1) for a in anomaly_details)
        max_possible = max(total_points * 3, 1)
        anomaly_score = min(round(raw_score / max_possible * 100, 1), 100.0)
        health_score = round(max(0.0, min(100.0, 100.0 - (anomaly_score * 0.60))), 1)

        if anomaly_rate < 1:
            health_impact = "Normal"
        elif anomaly_rate < 3:
            health_impact = "Low"
        elif anomaly_rate < 7:
            health_impact = "Moderate"
        else:
            health_impact = "Critical"

        param_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"total": 0, "low": 0, "medium": 0, "high": 0}
        )
        for anomaly in anomaly_details:
            sev = anomaly.get("severity", "low")
            for param in anomaly.get("parameters", []):
                param_counts[param]["total"] += 1
                if sev in param_counts[param]:
                    param_counts[param][sev] += 1

        parameter_breakdown = sorted(
            [
                {
                    "parameter": param,
                    "anomaly_count": counts["total"],
                    "anomaly_pct": round((counts["total"] / total_points * 100) if total_points else 0.0, 2),
                    "severity_distribution": {
                        "low": counts["low"],
                        "medium": counts["medium"],
                        "high": counts["high"],
                    },
                }
                for param, counts in param_counts.items()
            ],
            key=lambda x: -x["anomaly_count"],
        )

        most_affected = parameter_breakdown[0]["parameter"] if parameter_breakdown else "N/A"

        daily: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"count": 0, "high_count": 0, "medium_count": 0, "low_count": 0}
        )
        for anomaly in anomaly_details:
            ts = str(anomaly.get("timestamp", ""))
            sev = anomaly.get("severity", "low")
            if len(ts) < 10:
                continue
            day = ts[:10]
            daily[day]["count"] += 1
            if sev in ("low", "medium", "high"):
                daily[day][f"{sev}_count"] += 1

        anomalies_over_time = [
            {"date": day, **counts} for day, counts in sorted(daily.items())
        ]

        anomaly_list = []
        for anomaly in anomaly_details[:100]:
            params = anomaly.get("parameters", [])
            sev = anomaly.get("severity", "low")
            anomaly_list.append(
                {
                    "timestamp": str(anomaly.get("timestamp", "")),
                    "severity": sev,
                    "parameters": params,
                    "context": anomaly.get("context", ""),
                    "reasoning": f"{len(params)} parameter(s) outside normal range",
                    "recommended_action": self._anomaly_action(params, sev),
                }
            )

        recommendations = self._anomaly_recommendations(anomaly_rate, parameter_breakdown)

        points_for_conf = int((metadata or {}).get("data_points_analyzed", total_points))
        days_analyzed = self._resolve_days_available(
            provided_days=(metadata or {}).get("days_available"),
            data_points=points_for_conf,
        )
        confidence = self._confidence_from_days(days_analyzed, sensitivity)
        normalized_quality_flags = self._normalize_data_confidence_flags(
            data_quality_flags or [],
            confidence,
        )
        gauge_color = "green" if anomaly_rate < 3.0 else "amber" if anomaly_rate < 7.0 else "red"
        ensemble_data = ensemble or {}
        timeline_conf = (ensemble_data.get("timeline") or {}).get("confidence") or []
        confidence_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NORMAL": 0}
        if timeline_conf:
            summary_confidence = max(timeline_conf, key=lambda c: confidence_order.get(str(c), 0))
        else:
            summary_confidence = str(ensemble_data.get("confidence") or "NORMAL")

        confidence_summary = self._build_confidence_summary(
            title="Analysis Confidence",
            level=confidence["level"],
            evidence_strength=self._evidence_strength(confidence["level"], summary_confidence),
            summary=(reasoning or {}).get("summary"),
            interpretation=(
                f"Health impact is {health_impact.lower()} based on {total_points} analyzed data points "
                f"across {round(days_analyzed, 1)} day(s)."
            ),
            recommended_action=(reasoning or {}).get("recommended_action")
            or (recommendations[0]["action"] if recommendations else "Continue monitoring."),
            factors=list((reasoning or {}).get("affected_parameters") or [row["parameter"] for row in parameter_breakdown[:3]]),
        )

        return {
            "analysis_type": "anomaly_detection",
            "device_id": device_id,
            "job_id": job_id,
            "days_available": round(days_analyzed, 1),
            "hours_available": round(days_analyzed * 24, 1),
            "confidence_badge": confidence,
            "health_score": health_score,
            "confidence_summary": confidence_summary,
            "confidence": {
                "level": confidence["level"],
                "badge_color": confidence["badge_color"],
                "banner_text": confidence["banner_text"],
                "banner_style": confidence["banner_style"],
                "days_available": round(days_analyzed, 1),
            },
            "summary": {
                "total_anomalies": total_anomalies,
                "anomaly_rate_pct": anomaly_rate,
                "anomaly_score": anomaly_score,
                "health_impact": health_impact,
                "most_affected_parameter": most_affected,
                "data_points_analyzed": total_points,
                "days_analyzed": round(days_analyzed, 1),
                "model_confidence": confidence["level"],
                "sensitivity": sensitivity,
            },
            "anomaly_rate_gauge": {
                "value": anomaly_rate,
                "max": 10,
                "color": gauge_color,
            },
            "parameter_breakdown": parameter_breakdown,
            "anomalies_over_time": anomalies_over_time,
            "anomaly_list": anomaly_list,
            "recommendations": recommendations,
            "metadata": {
                "data_completeness_pct": float((metadata or {}).get("data_completeness_pct", 100.0)),
                "parameters_analyzed": len(parameter_breakdown),
                "fallback_mode": bool((metadata or {}).get("fallback_mode", False)),
            },
            "execution_metadata": {
                "data_window": {
                    "requested_range": (metadata or {}).get("requested_range"),
                    "dataset_range": (metadata or {}).get("dataset_range"),
                    "points_analyzed": total_points,
                }
            },
            "reasoning": self._sanitize_reasoning(reasoning),
            "data_quality_flags": normalized_quality_flags,
        }

    def format_failure_prediction_results(
        self,
        device_id: str,
        job_id: str,
        failure_probability_pct: float,
        risk_breakdown: Dict[str, float],
        risk_factors: List[Dict[str, Any]],
        model_confidence: str,
        days_available: float,
        anomaly_score: float = 0.0,
        metadata: Dict[str, Any] | None = None,
        ensemble: Dict[str, Any] | None = None,
        time_to_failure: Dict[str, Any] | None = None,
        reasoning: Dict[str, Any] | None = None,
        degradation_series: List[float] | None = None,
        data_quality_flags: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        prob = float(max(0.0, min(100.0, failure_probability_pct)))

        if prob < 15:
            risk_level, remaining_life, urgency = "Minimal", "60+ days", "Routine"
        elif prob < 35:
            risk_level, remaining_life, urgency = "Low", "~30 days", "Routine"
        elif prob < 60:
            risk_level, remaining_life, urgency = "Medium", "~15 days", "Schedule Soon"
        elif prob < 80:
            risk_level, remaining_life, urgency = "High", "< 7 days", "Urgent"
        else:
            risk_level, remaining_life, urgency = "Critical", "< 24 hours", "Immediate"

        health_score = round(max(0.0, min(100.0, 100.0 - (anomaly_score * 0.60) - (prob * 0.40))), 1)
        filtered_factors = self._filter_risk_factors(risk_factors)
        insufficient_trend_signal = len(filtered_factors) == 0
        display_factors = filtered_factors
        if insufficient_trend_signal and risk_factors:
            # Fallback: still show top contributors so user always has direction.
            display_factors = sorted(
                risk_factors,
                key=lambda x: float(x.get("contribution_pct", 0.0)),
                reverse=True,
            )[:3]

        recs = self._failure_recommendations(display_factors, risk_level)
        if insufficient_trend_signal or not recs:
            recs = [
                {
                    "rank": 1,
                    "action": "Continue monitoring and collect more telemetry",
                    "urgency": "Routine" if risk_level in {"Minimal", "Low"} else "Within 3 days",
                    "reasoning": "Insufficient trend signal for parameter-specific recommendation.",
                    "parameter": "system",
                }
            ]

        safe_pct = float(risk_breakdown.get("safe_pct", 100.0))
        warning_pct = float(risk_breakdown.get("warning_pct", 0.0))
        critical_pct = float(risk_breakdown.get("critical_pct", 0.0))
        total = safe_pct + warning_pct + critical_pct
        if total > 0:
            safe_pct = round(safe_pct / total * 100, 1)
            warning_pct = round(warning_pct / total * 100, 1)
            critical_pct = round(critical_pct / total * 100, 1)

        points_for_conf = int((metadata or {}).get("data_points_analyzed", max(1, int(days_available * 1440))))
        normalized_days = self._resolve_days_available(
            provided_days=days_available,
            data_points=points_for_conf,
        )
        confidence = self._confidence_from_days(
            normalized_days,
            str((metadata or {}).get("sensitivity", "medium")),
        )
        normalized_quality_flags = self._normalize_data_confidence_flags(
            data_quality_flags or [],
            confidence,
        )
        attention_required = str((ensemble or {}).get("verdict") or "").upper() in {"WATCH", "WARNING", "CRITICAL"}
        confidence_summary = self._build_confidence_summary(
            title="Prediction Confidence",
            level=confidence["level"],
            evidence_strength=self._evidence_strength(confidence["level"], model_confidence),
            summary=(reasoning or {}).get("summary"),
            interpretation=(
                f"Maintenance urgency is {urgency.lower()} with estimated remaining life {remaining_life}."
            ),
            recommended_action=recs[0]["action"] if recs else "Continue monitoring and schedule a maintenance review.",
            factors=list((reasoning or {}).get("top_risk_factors") or [rf.get("parameter", "system") for rf in display_factors[:3]]),
        )
        return {
            "analysis_type": "failure_prediction",
            "device_id": device_id,
            "job_id": job_id,
            "days_available": round(normalized_days, 1),
            "hours_available": round(normalized_days * 24, 1),
            "confidence_badge": confidence,
            "health_score": health_score,
            "attention_required": attention_required,
            "confidence_summary": confidence_summary,
            "confidence": {
                "level": confidence["level"],
                "badge_color": confidence["badge_color"],
                "banner_text": confidence["banner_text"],
                "banner_style": confidence["banner_style"],
                "days_available": round(normalized_days, 1),
                "model_agreement_confidence": model_confidence,
            },
            "summary": {
                "failure_risk": risk_level,
                "failure_probability_pct": round(prob, 1),
                "failure_probability_meter": round(prob, 1),
                "safe_probability_pct": round(100.0 - prob, 1),
                "estimated_remaining_life": remaining_life,
                "maintenance_urgency": urgency,
                "confidence_level": confidence["level"],
                "model_agreement_confidence": model_confidence,
                "days_analyzed": round(normalized_days, 1),
            },
            "risk_breakdown": {
                "safe_pct": safe_pct,
                "warning_pct": warning_pct,
                "critical_pct": critical_pct,
            },
            "risk_factors": display_factors,
            "insufficient_trend_signal": insufficient_trend_signal,
            "recommended_actions": recs,
            "metadata": {
                "model_confidence": model_confidence or confidence["level"],
                "days_analyzed": round(normalized_days, 1),
                "data_completeness_pct": float((metadata or {}).get("data_completeness_pct", 100.0)),
                "fallback_mode": bool((metadata or {}).get("fallback_mode", False)),
                "insufficient_trend_signal": insufficient_trend_signal,
                "attention_required": attention_required,
            },
            "execution_metadata": {
                "data_window": {
                    "requested_range": (metadata or {}).get("requested_range"),
                    "dataset_range": (metadata or {}).get("dataset_range"),
                    "points_analyzed": int((metadata or {}).get("data_points_analyzed", 0)),
                }
            },
            "time_to_failure": time_to_failure or {},
            "reasoning": self._sanitize_reasoning(reasoning),
            "degradation_series": degradation_series or [],
            "data_quality_flags": normalized_quality_flags,
        }

    @staticmethod
    def _resolve_days_available(provided_days: Any, data_points: int) -> float:
        try:
            if provided_days is not None and float(provided_days) > 0:
                return float(provided_days)
        except Exception:
            pass
        return round(max(1, int(data_points)) / 1440.0, 3)

    @staticmethod
    def _confidence_from_days(days_available: float, sensitivity: str) -> Dict[str, Any]:
        confidence = get_confidence(max(1, int(days_available * 1440)), sensitivity).to_dict()
        confidence["days_available"] = round(float(days_available), 1)
        return confidence

    @staticmethod
    def _normalize_data_confidence_flags(
        flags: List[Dict[str, Any]],
        confidence: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        style = str(confidence.get("banner_style", "blue")).lower()
        color_map = {
            "red": "red",
            "orange": "orange",
            "amber": "orange",
            "yellow": "yellow",
            "blue": "blue",
            "green": "green",
        }
        color = color_map.get(style, "blue")
        severity = "warning" if color in {"red", "orange"} else "info"
        canonical = {
            "type": "data_confidence",
            "confidence_level": confidence.get("level", "Low"),
            "color": color,
            "message": confidence.get("banner_text", ""),
            "severity": severity,
        }

        out: List[Dict[str, Any]] = []
        replaced = False
        for flag in flags:
            if str(flag.get("type")) == "data_confidence":
                if not replaced:
                    out.append(canonical)
                    replaced = True
                continue
            out.append(flag)
        if not replaced:
            out.insert(0, canonical)
        return out

    @staticmethod
    def _evidence_strength(*levels: Any) -> str:
        rank = {
            "VERY HIGH": 4,
            "HIGH": 3,
            "MEDIUM": 2,
            "MODERATE": 2,
            "LOW": 1,
            "NORMAL": 1,
        }
        best = 0
        for level in levels:
            best = max(best, rank.get(str(level or "").strip().upper(), 0))
        if best >= 3:
            return "Strong"
        if best == 2:
            return "Moderate"
        if best == 1:
            return "Developing"
        return "Limited"

    @staticmethod
    def _build_confidence_summary(
        *,
        title: str,
        level: str,
        evidence_strength: str,
        summary: Any,
        interpretation: str,
        recommended_action: str,
        factors: List[Any],
    ) -> Dict[str, Any]:
        clean_factors = [str(item).strip() for item in factors if str(item).strip()][:5]
        return {
            "title": title,
            "level": str(level or "Moderate"),
            "evidence_strength": evidence_strength,
            "summary": str(summary or "Analysis completed successfully."),
            "interpretation": interpretation,
            "recommended_action": recommended_action,
            "factors": clean_factors,
        }

    @staticmethod
    def _sanitize_reasoning(reasoning: Dict[str, Any] | None) -> Dict[str, Any]:
        if not reasoning:
            return {}
        clean = deepcopy(reasoning)
        if "agreement_text" in clean and "evidence_text" not in clean:
            clean["evidence_text"] = clean.pop("agreement_text")
        clean.pop("models_voted", None)
        clean.pop("per_model", None)
        return clean

    def format_fleet_results(
        self,
        job_id: str,
        analysis_type: str,
        device_results: List[Dict[str, Any]],
        child_job_map: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        if not device_results:
            return {
                "analysis_type": "fleet",
                "job_id": job_id,
                "fleet_health_score": 0.0,
                "worst_device_id": None,
                "worst_device_health": 0.0,
                "device_summaries": [],
                "critical_devices": [],
                "source_analysis_type": analysis_type,
            }

        summaries = []
        critical = []
        weighted = 0.0
        total_weight = 0.0

        worst_id = None
        worst_health = 101.0

        for item in device_results:
            summary = item.get("summary", {})
            device_id = item.get("device_id") or summary.get("device_id") or "unknown"
            health = float(item.get("health_score", 0.0))
            points = float(summary.get("data_points_analyzed", 1) or 1)
            weighted += health * points
            total_weight += points

            if health < worst_health:
                worst_health = health
                worst_id = device_id

            risk = summary.get("failure_risk")
            if risk in {"Critical", "High"}:
                critical.append(device_id)

            summaries.append(
                {
                    "device_id": device_id,
                    "health_score": round(health, 1),
                    "failure_risk": summary.get("failure_risk"),
                    "total_anomalies": summary.get("total_anomalies", 0),
                    "anomaly_rate_pct": summary.get("anomaly_rate_pct", 0),
                    "maintenance_urgency": summary.get("maintenance_urgency"),
                    "child_job_id": (child_job_map or {}).get(device_id),
                }
            )

        fleet_health = round(weighted / total_weight, 1) if total_weight > 0 else 0.0

        return {
            "analysis_type": "fleet",
            "job_id": job_id,
            "fleet_health_score": fleet_health,
            "worst_device_id": worst_id,
            "worst_device_health": round(worst_health if worst_health <= 100 else 0.0, 1),
            "device_summaries": summaries,
            "critical_devices": sorted(set(critical)),
            "source_analysis_type": analysis_type,
        }

    @staticmethod
    def _confidence_label(days: int | float) -> str:
        if days >= 30:
            return "Very High"
        if days >= 7:
            return "High"
        if days >= 1:
            return "Moderate"
        return "Low"

    @staticmethod
    def _anomaly_action(parameters: List[str], severity: str) -> str:
        for p in parameters:
            pl = p.lower()
            if "temp" in pl:
                return "Check cooling system and air filters"
            if "vibr" in pl:
                return "Inspect bearings and coupling alignment"
            if "press" in pl:
                return "Check pressure relief valves and seals"
            if "current" in pl:
                return "Check motor winding and electrical connections"
            if "power" in pl:
                return "Review load conditions and efficiency"
        if severity == "high":
            return "Immediate inspection required"
        if severity == "medium":
            return "Schedule inspection within 24 hours"
        return "Add to maintenance watchlist"

    @staticmethod
    def _anomaly_recommendations(rate: float, breakdown: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if rate > 10:
            urgency = "Immediate"
        elif rate > 5:
            urgency = "Within 24h"
        elif rate > 2:
            urgency = "Within 48h"
        else:
            urgency = "This week"

        recommendations = []
        for i, row in enumerate(breakdown[:3]):
            high_count = row.get("severity_distribution", {}).get("high", 0)
            recommendations.append(
                {
                    "rank": i + 1,
                    "action": ResultFormatter._anomaly_action([row["parameter"]], "high"),
                    "urgency": urgency if i == 0 else "This week",
                    "reasoning": f"{row['anomaly_count']} anomalies in {row['parameter']} ({high_count} high severity)",
                    "parameter": row["parameter"],
                }
            )
        return recommendations

    @staticmethod
    def _failure_recommendations(risk_factors: List[Dict[str, Any]], risk_level: str) -> List[Dict[str, Any]]:
        urgency_map = {
            "Critical": "Immediate",
            "High": "Within 3 days",
            "Medium": "Within 3 days",
            "Low": "This week",
            "Minimal": "Routine",
        }
        base = urgency_map.get(risk_level, "This week")
        recs = []

        for i, rf in enumerate(risk_factors[:4]):
            param = str(rf.get("parameter", "parameter"))
            trend = str(rf.get("trend", "stable"))
            pl = param.lower()
            if "vibr" in pl and trend == "increasing":
                action = f"Inspect and lubricate bearings - {param} trend indicates wear"
            elif "temp" in pl and trend == "increasing":
                action = "Check cooling system - clean filters and verify coolant levels"
            elif "current" in pl and trend == "erratic":
                action = "Run electrical diagnostic on motor windings"
            elif "press" in pl and trend == "decreasing":
                action = "Inspect pressure seals and O-rings"
            else:
                action = f"Inspect {param} subsystem - {trend} trend detected"

            recs.append(
                {
                    "rank": i + 1,
                    "action": action,
                    "urgency": base if i < 2 else "This week",
                    "reasoning": f"{rf.get('contribution_pct', 0)}% contribution - {rf.get('context', '')}",
                    "parameter": param,
                }
            )
        return recs

    @staticmethod
    def _filter_risk_factors(risk_factors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filtered = []
        for rf in risk_factors:
            contribution = float(rf.get("contribution_pct", 0.0) or 0.0)
            trend = str(rf.get("trend", "stable")).lower()
            current = float(rf.get("current_value", 0.0) or 0.0)
            baseline = float(rf.get("baseline_value", 0.0) or 0.0)
            pct_change = abs((current - baseline) / (abs(baseline) + 1e-9) * 100)

            if contribution < 5.0:
                continue
            if trend == "stable" and pct_change < 3.0:
                continue
            filtered.append(rf)
        return filtered
