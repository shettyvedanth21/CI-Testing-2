

# """Job runner abstraction for executing analytics jobs."""

# import time
# from datetime import datetime
# from typing import Any, Dict

# import pandas as pd
# import structlog

# from src.models.schemas import AnalyticsRequest, AnalyticsType, JobStatus
# from src.services.analytics.anomaly_detection import AnomalyDetectionPipeline
# from src.services.analytics.failure_prediction import FailurePredictionPipeline
# from src.services.analytics.forecasting import ForecastingPipeline
# from src.services.dataset_service import DatasetService
# from src.services.result_repository import ResultRepository
# from src.utils.exceptions import AnalyticsError

# logger = structlog.get_logger()


# class JobRunner:
#     """Runner for executing analytics jobs."""

#     def __init__(
#         self,
#         dataset_service: DatasetService,
#         result_repository: ResultRepository,
#     ):
#         self._dataset_service = dataset_service
#         self._result_repo = result_repository
#         self._logger = logger.bind(service="JobRunner")

#         self._pipelines = {
#             AnalyticsType.ANOMALY: AnomalyDetectionPipeline(),
#             AnalyticsType.PREDICTION: FailurePredictionPipeline(),
#             AnalyticsType.FORECAST: ForecastingPipeline(),
#         }

#     async def run_job(self, job_id: str, request: AnalyticsRequest) -> None:
#         start_clock = time.time()

#         self._logger.info(
#             "job_started",
#             job_id=job_id,
#             analysis_type=request.analysis_type.value,
#             model_name=request.model_name,
#         )

#         try:
#             await self._result_repo.update_job_status(
#                 job_id=job_id,
#                 status=JobStatus.RUNNING,
#                 started_at=datetime.utcnow(),
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 10.0, "Loading dataset"
#             )

#             # -------------------------------------------------------
#             # Dataset loading
#             # -------------------------------------------------------
#             df = await self._dataset_service.load_dataset(
#                 device_id=request.device_id,
#                 start_time=request.start_time,
#                 end_time=request.end_time,
#                 s3_key=getattr(request, "dataset_key", None),
#             )

#             pipeline = self._pipelines.get(request.analysis_type)
#             if not pipeline:
#                 raise AnalyticsError(
#                     f"Unknown analysis type: {request.analysis_type}"
#                 )

#             await self._result_repo.update_job_progress(
#                 job_id, 30.0, "Preparing features"
#             )

#             train_df, test_df = pipeline.prepare_data(
#                 df, request.parameters
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 50.0, "Training model"
#             )

#             model = pipeline.train(
#                 train_df, request.model_name, request.parameters
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 75.0, "Running inference"
#             )

#             # -------------------------------------------------------
#             # PERMANENT FIX
#             # Inference must run on FULL dataframe
#             # -------------------------------------------------------
#             results = pipeline.predict(
#                 df, model, request.parameters
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 90.0, "Calculating metrics"
#             )

#             # evaluation is still done only on test split
#             metrics = pipeline.evaluate(
#                 test_df, results, request.parameters
#             )

#             # ---------------------------------------------------------
#             # Attach timestamp aligned points for anomaly jobs
#             # (must use FULL dataframe)
#             # ---------------------------------------------------------
#             if request.analysis_type == AnalyticsType.ANOMALY:
#                 self._attach_anomaly_points(results, df)

#             execution_time = int(time.time() - start_clock)

#             await self._result_repo.save_results(
#                 job_id=job_id,
#                 results=results,
#                 accuracy_metrics=metrics,
#                 execution_time_seconds=execution_time,
#             )

#             await self._result_repo.update_job_status(
#                 job_id=job_id,
#                 status=JobStatus.COMPLETED,
#                 completed_at=datetime.utcnow(),
#                 progress=100.0,
#                 message="Analysis completed successfully",
#             )

#             self._logger.info(
#                 "job_completed",
#                 job_id=job_id,
#                 execution_time_seconds=execution_time,
#             )

#         except Exception as e:
#             self._logger.error(
#                 "job_failed",
#                 job_id=job_id,
#                 error=str(e),
#                 exc_info=True,
#             )

#             await self._result_repo.update_job_status(
#                 job_id=job_id,
#                 status=JobStatus.FAILED,
#                 completed_at=datetime.utcnow(),
#                 message="Job failed",
#                 error_message=str(e),
#             )

#             raise AnalyticsError(f"Job execution failed: {e}") from e

#     def _attach_anomaly_points(
#         self,
#         results: Dict[str, Any],
#         df: pd.DataFrame,
#     ) -> None:
#         """
#         Attach timestamp-aligned anomaly points to results.

#         Produces:
#         results["points"] = [
#             {
#                 "timestamp": ...,
#                 "anomaly_score": ...,
#                 "is_anomaly": ...
#             }
#         ]
#         """

#         # ---------------------------------------------------------
#         # Robust timestamp column detection
#         # ---------------------------------------------------------
#         if "timestamp" in df.columns:
#             ts_col = "timestamp"
#         elif "_time" in df.columns:
#             ts_col = "_time"
#         else:
#             raise AnalyticsError(
#                 "No timestamp column found in dataset (expected 'timestamp' or '_time')"
#             )

#         anomaly_scores = results.get("anomaly_score")
#         is_anomaly = results.get("is_anomaly")

#         if anomaly_scores is None or is_anomaly is None:
#             raise AnalyticsError(
#                 "Anomaly results missing 'anomaly_score' or 'is_anomaly'"
#             )

#         if len(df) != len(anomaly_scores):
#             raise AnalyticsError(
#                 "Mismatch between dataframe length and anomaly result length"
#             )

#         timestamps = pd.to_datetime(
#             df[ts_col],
#             utc=True,
#             errors="coerce",
#         )

#         if timestamps.isna().any():
#             raise AnalyticsError(
#                 "Invalid timestamp values found in dataset"
#             )

#         points = []

#         for ts, score, flag in zip(
#             timestamps,
#             anomaly_scores,
#             is_anomaly,
#         ):
#             points.append(
#                 {
#                     "timestamp": ts.isoformat(),
#                     "anomaly_score": float(score),
#                     "is_anomaly": bool(flag),
#                 }
#             )

#         results["points"] = points
























"""Job runner abstraction for executing analytics jobs."""

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Callable

import pandas as pd
import structlog
from sqlalchemy import select

from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.s3_client import S3Client
from src.models.database import AccuracyEvaluation
from src.models.schemas import AnalyticsRequest, AnalyticsType, JobStatus
from src.services.dataset_service import DatasetService
from src.services.readiness_orchestrator import dataset_window_from_key, ensure_device_ready
from src.services.result_formatter import ResultFormatter
from src.services.progress_tracking import JobProgressReporter, SINGLE_JOB_PHASES
from src.services.result_repository import ResultRepository
from src.utils.exceptions import AnalyticsError
from services.shared.telemetry_coverage import build_window_coverage_result

logger = structlog.get_logger()

_ANOMALY_FEATURE_PREPARATION_STEPS = (
    "artifact_lookup",
    "anomaly_feature_preparation",
)
_ANOMALY_MODEL_EXECUTION_STEPS = (
    "isolation_forest",
    "lstm_sequence_preparation",
    "lstm_model",
    "lstm_scoring",
    "cusum_scoring",
    "ensemble_voting",
)

_ANOMALY_IMPORT_ERROR: Exception | None = None
_FAILURE_IMPORT_ERROR: Exception | None = None
_FORECAST_IMPORT_ERROR: Exception | None = None

try:
    from src.services.analytics.ensemble.anomaly_ensemble import AnomalyEnsemble
except ModuleNotFoundError as exc:  # pragma: no cover - runtime fallback exercised in worker tests
    AnomalyEnsemble = None  # type: ignore[assignment]
    _ANOMALY_IMPORT_ERROR = exc

try:
    from src.services.analytics.ensemble.failure_ensemble import FailureEnsemble
except ModuleNotFoundError as exc:  # pragma: no cover - runtime fallback exercised in worker tests
    FailureEnsemble = None  # type: ignore[assignment]
    _FAILURE_IMPORT_ERROR = exc

try:
    from src.services.analytics.forecasting import ForecastingPipeline
except ModuleNotFoundError as exc:  # pragma: no cover - runtime fallback exercised in worker tests
    ForecastingPipeline = None  # type: ignore[assignment]
    _FORECAST_IMPORT_ERROR = exc


# ----------------------------------------------------------------------
# Permanent JSON safety boundary
# ----------------------------------------------------------------------
def _json_safe(obj: Any):
    # Handle numpy-like arrays without importing numpy in hot path.
    if hasattr(obj, "tolist") and not isinstance(obj, (str, bytes, bytearray)):
        try:
            return _json_safe(obj.tolist())
        except Exception:
            pass

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]

    return obj


def _analytics_covered_hours(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    ts_col = "timestamp" if "timestamp" in df.columns else "_time" if "_time" in df.columns else None
    if ts_col is None:
        return 0.0
    timestamps = pd.to_datetime(df[ts_col], utc=True, errors="coerce").dropna().sort_values()
    if len(timestamps) < 2:
        return 0.0
    return max((timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds(), 0.0) / 3600.0


def _analytics_no_coverage_result(job_id: str, request: AnalyticsRequest, message: str) -> dict[str, Any]:
    coverage = build_window_coverage_result(
        selected_window_start=request.start_time,
        selected_window_end=request.end_time,
        covered_duration_hours=0.0,
        has_any_data=False,
        warnings=[message],
        has_usable_result=False,
        artifact_generation_allowed=False,
    ).to_dict()
    return _analytics_blocked_result(job_id, request, coverage)


def _analytics_blocked_result(
    job_id: str,
    request: AnalyticsRequest,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    analysis_type_map = {
        AnalyticsType.ANOMALY: "anomaly_detection",
        AnalyticsType.PREDICTION: "failure_prediction",
        AnalyticsType.FORECAST: "forecasting",
    }
    level = str(coverage.get("level") or "no_coverage")
    is_no_coverage = level == "no_coverage"
    summary = str(coverage.get("message") or "Telemetry coverage is insufficient for this analysis.")
    return {
        "formatted": {
            "job_id": job_id,
            "device_id": request.device_id,
            "analysis_type": analysis_type_map.get(request.analysis_type, request.analysis_type.value),
            "status": "no_data" if is_no_coverage else "insufficient_coverage",
            "coverage_result": coverage,
            "summary": summary,
        },
        "coverage_result": coverage,
        "data_quality_flags": [
            {
                "code": "NO_TELEMETRY_IN_RANGE" if is_no_coverage else "INSUFFICIENT_TELEMETRY_COVERAGE",
                "severity": "info" if is_no_coverage else "warning",
                "message": summary,
            }
        ],
    }


class JobRunner:
    """Runner for executing analytics jobs."""

    def __init__(
        self,
        dataset_service: DatasetService,
        result_repository: ResultRepository,
    ):
        self._dataset_service = dataset_service
        self._result_repo = result_repository
        self._logger = logger.bind(service="JobRunner")
        self._pipelines: dict[AnalyticsType, object] = {}

    @staticmethod
    def _require_runtime_dependency(
        dependency: object | None,
        *,
        analysis_type: AnalyticsType,
        import_error: Exception | None,
    ) -> object:
        if dependency is None:
            detail = str(import_error) if import_error is not None else "dependency missing"
            raise AnalyticsError(
                f"{analysis_type.value} runtime dependency is unavailable in this environment: {detail}"
            )
        return dependency

    def _build_anomaly_ensemble(self):
        dependency = self._require_runtime_dependency(
            AnomalyEnsemble,
            analysis_type=AnalyticsType.ANOMALY,
            import_error=_ANOMALY_IMPORT_ERROR,
        )
        return dependency()

    def _build_failure_ensemble(self):
        dependency = self._require_runtime_dependency(
            FailureEnsemble,
            analysis_type=AnalyticsType.PREDICTION,
            import_error=_FAILURE_IMPORT_ERROR,
        )
        return dependency()

    def _get_forecasting_pipeline(self):
        pipeline = self._pipelines.get(AnalyticsType.FORECAST)
        if pipeline is not None:
            return pipeline
        dependency = self._require_runtime_dependency(
            ForecastingPipeline,
            analysis_type=AnalyticsType.FORECAST,
            import_error=_FORECAST_IMPORT_ERROR,
        )
        pipeline = dependency()
        self._pipelines[AnalyticsType.FORECAST] = pipeline
        return pipeline

    @staticmethod
    def _phase_ratio_from_completed(
        completed: set[str],
        steps: tuple[str, ...],
    ) -> float:
        if not steps:
            return 0.0
        complete_count = sum(1 for step in steps if step in completed)
        return complete_count / len(steps)

    async def _run_anomaly_with_measured_progress(
        self,
        *,
        progress: JobProgressReporter,
        ensemble: Any,
        df: pd.DataFrame,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        settings = get_settings()
        progress_events: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        completed_feature_steps: set[str] = set()
        completed_model_steps: set[str] = set()
        active_stage_key: str | None = None
        active_stage_label: str | None = None
        active_stage_started_at = 0.0
        last_stage_heartbeat_at = 0.0
        heartbeat_interval = max(1, int(settings.ml_stage_activity_heartbeat_seconds))
        phase_specs: dict[str, tuple[str, tuple[str, ...], set[str]]] = {
            "anomaly_feature_preparation": (
                "feature_preparation",
                _ANOMALY_FEATURE_PREPARATION_STEPS,
                completed_feature_steps,
            ),
            "isolation_forest": (
                "model_execution",
                _ANOMALY_MODEL_EXECUTION_STEPS,
                completed_model_steps,
            ),
            "lstm_sequence_preparation": (
                "model_execution",
                _ANOMALY_MODEL_EXECUTION_STEPS,
                completed_model_steps,
            ),
            "lstm_model": (
                "model_execution",
                _ANOMALY_MODEL_EXECUTION_STEPS,
                completed_model_steps,
            ),
            "lstm_scoring": (
                "model_execution",
                _ANOMALY_MODEL_EXECUTION_STEPS,
                completed_model_steps,
            ),
            "cusum_scoring": (
                "model_execution",
                _ANOMALY_MODEL_EXECUTION_STEPS,
                completed_model_steps,
            ),
            "ensemble_voting": (
                "model_execution",
                _ANOMALY_MODEL_EXECUTION_STEPS,
                completed_model_steps,
            ),
        }

        def _progress_callback(event_type: str, stage_key: str, label: str) -> None:
            loop.call_soon_threadsafe(progress_events.put_nowait, (event_type, stage_key, label))

        task = loop.create_task(
            asyncio.to_thread(ensemble.run, df, params, _progress_callback)
        )

        while True:
            if task.done() and progress_events.empty():
                break
            try:
                event_type, stage_key, label = await asyncio.wait_for(progress_events.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if active_stage_key and active_stage_label:
                    now = time.monotonic()
                    if now - last_stage_heartbeat_at >= heartbeat_interval:
                        spec = phase_specs.get(active_stage_key)
                        if spec is not None:
                            phase, steps, completed_steps = spec
                            elapsed_seconds = max(1, int(now - active_stage_started_at))
                            phase_progress = self._phase_ratio_from_completed(completed_steps, steps)
                            heartbeat_label = f"{active_stage_label} ({elapsed_seconds}s elapsed)"
                            await progress.update(
                                phase,
                                phase_progress=phase_progress,
                                message=heartbeat_label,
                                phase_label=heartbeat_label,
                            )
                            last_stage_heartbeat_at = now
                continue

            spec = phase_specs.get(stage_key)
            if spec is None:
                continue

            phase, steps, completed_steps = spec
            if event_type == "start":
                active_stage_key = stage_key
                active_stage_label = label
                active_stage_started_at = time.monotonic()
                last_stage_heartbeat_at = active_stage_started_at
            if event_type == "complete":
                completed_steps.add(stage_key)
                if active_stage_key == stage_key:
                    active_stage_key = None
                    active_stage_label = None
                    active_stage_started_at = 0.0
                    last_stage_heartbeat_at = 0.0
            phase_progress = self._phase_ratio_from_completed(completed_steps, steps)
            await progress.update(
                phase,
                phase_progress=phase_progress,
                message=label,
                phase_label=label,
            )

        return await task

    async def run_job(self, job_id: str, request: AnalyticsRequest) -> None:
        start_clock = time.time()
        params = request.parameters or {}
        settings = get_settings()
        resolved_request = request

        self._logger.info(
            "job_started",
            job_id=job_id,
            analysis_type=request.analysis_type.value,
            model_name=request.model_name,
        )

        try:
            await self._result_repo.update_job_status(
                job_id=job_id,
                status=JobStatus.RUNNING,
                started_at=datetime.utcnow(),
                phase="dataset_loading",
                phase_label="Starting analytics execution",
                phase_progress=0.0,
            )

            progress = JobProgressReporter(
                self._result_repo,
                job_id,
                phase_ranges=SINGLE_JOB_PHASES,
            )
            await progress.update(
                "dataset_loading",
                phase_progress=0.05,
                message="Preparing dataset loading",
            )

            readiness_enabled = (
                settings.app_env.lower() != "test"
                and (settings.ml_require_exact_dataset_range or settings.ml_data_readiness_gate_enabled)
            )

            if (
                not request.dataset_key
                and request.start_time
                and request.end_time
                and request.analysis_type in {AnalyticsType.ANOMALY, AnalyticsType.PREDICTION}
                and readiness_enabled
            ):
                await progress.update(
                    "readiness",
                    phase_progress=0.2,
                    message="Preparing exact-range dataset",
                )
                s3_client = S3Client()
                ready_device_id, dataset_key, readiness_meta = await ensure_device_ready(
                    s3_client=s3_client,
                    dataset_service=self._dataset_service,
                    device_id=request.device_id,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    tenant_id=str(params.get("tenant_id")) if params.get("tenant_id") else None,
                )
                if not dataset_key:
                    reason = str((readiness_meta or {}).get("reason") or "dataset_not_ready")
                    hard_reasons = {"device_not_found", "no_telemetry_in_range", "tenant_scope_required"}
                    if reason in hard_reasons:
                        code = {
                            "device_not_found": "DEVICE_NOT_FOUND",
                            "no_telemetry_in_range": "NO_TELEMETRY_IN_RANGE",
                            "tenant_scope_required": "TENANT_SCOPE_REQUIRED",
                        }.get(reason, "DATASET_NOT_READY")
                        raise AnalyticsError(
                            f"{code}: readiness failed for device={ready_device_id}, "
                            f"reason={reason}, export_attempted={bool((readiness_meta or {}).get('export_attempted', False))}, "
                            f"wait_seconds={float((readiness_meta or {}).get('wait_seconds', 0.0))}"
                        )
                    # Permanent hardening:
                    # when export/S3 path is unavailable, continue with exact-range direct data-service fetch.
                    resolved_request = request
                    await progress.update(
                        "dataset_loading",
                        phase_progress=0.2,
                        message="Readiness fallback: loading exact-range telemetry directly",
                    )
                else:
                    resolved_request = request.model_copy(update={"dataset_key": dataset_key})
                    await progress.update(
                        "dataset_loading",
                        phase_progress=0.2,
                        message="Exact-range dataset resolved; loading dataset",
                    )

            # -------------------------------------------------------
            # Dataset loading
            # -------------------------------------------------------
            await progress.update(
                "dataset_loading",
                phase_progress=0.35,
                message="Loading dataset from storage",
            )
            df = await self._dataset_service.load_dataset(
                device_id=resolved_request.device_id,
                start_time=resolved_request.start_time,
                end_time=resolved_request.end_time,
                s3_key=getattr(resolved_request, "dataset_key", None),
                tenant_id=str(params.get("tenant_id")) if params.get("tenant_id") else None,
            )
            current_rows = len(df)
            coverage_result = build_window_coverage_result(
                selected_window_start=resolved_request.start_time,
                selected_window_end=resolved_request.end_time,
                covered_duration_hours=_analytics_covered_hours(df),
                has_any_data=current_rows > 0,
                warnings=[],
                has_usable_result=current_rows >= 2,
                artifact_generation_allowed=current_rows >= 2,
            ).to_dict()
            if coverage_result["level"] in {"no_coverage", "insufficient_coverage"}:
                message = str(coverage_result["message"])
                safe_results = _analytics_blocked_result(job_id, request, coverage_result)
                await self._result_repo.save_results(
                    job_id=job_id,
                    results=safe_results,
                    accuracy_metrics={},
                    execution_time_seconds=int(time.time() - start_clock),
                )
                await self._result_repo.update_job_status(
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    completed_at=datetime.utcnow(),
                    progress=100.0,
                    message=message,
                    error_message=None,
                    phase=coverage_result["level"],
                    phase_label="No Data" if coverage_result["level"] == "no_coverage" else "Insufficient Coverage",
                    phase_progress=1.0,
                )
                await self._result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    error_code="NO_TELEMETRY_IN_RANGE" if coverage_result["level"] == "no_coverage" else "INSUFFICIENT_TELEMETRY_COVERAGE",
                )
                return

            await progress.update(
                "dataset_loading",
                phase_progress=1.0,
                message=f"Dataset loaded ({current_rows} rows)",
            )
            await progress.update(
                "feature_preparation",
                phase_progress=0.0,
                message="Loading anomaly model artifacts" if request.analysis_type == AnalyticsType.ANOMALY else "Preparing features",
                phase_label="Loading anomaly model artifacts" if request.analysis_type == AnalyticsType.ANOMALY else "Preparing features",
            )
            tenant_id = str(params.get("tenant_id")) if params.get("tenant_id") else None
            if request.analysis_type == AnalyticsType.ANOMALY:
                preloaded_artifacts = await self._load_valid_cached_artifacts(
                    tenant_id=tenant_id,
                    device_id=request.device_id,
                    analysis_type=request.analysis_type.value,
                    model_keys=("isolation_forest", "lstm_autoencoder", "cusum"),
                    current_rows=current_rows,
                )
                await progress.update(
                    "feature_preparation",
                    phase_progress=0.5,
                    message="Loading anomaly model artifacts",
                    phase_label="Loading anomaly model artifacts",
                )
                ensemble = self._build_anomaly_ensemble()
                run_params = dict(params)
                run_params["__artifacts"] = preloaded_artifacts
                results = await self._run_anomaly_with_measured_progress(
                    progress=progress,
                    ensemble=ensemble,
                    df=df,
                    params=run_params,
                )
                artifact_updates = results.pop("artifact_updates", {}) if isinstance(results, dict) else {}
                await self._persist_artifact_updates(
                    tenant_id=tenant_id,
                    device_id=request.device_id,
                    analysis_type=request.analysis_type.value,
                    artifact_updates=artifact_updates,
                    current_rows=current_rows,
                    start_time=resolved_request.start_time,
                    end_time=resolved_request.end_time,
                )
                await progress.update(
                    "metrics_formatting",
                    phase_progress=0.5,
                    message="Calculating anomaly metrics",
                    phase_label="Calculating anomaly metrics",
                )
                total = len(results.get("is_anomaly", []))
                detected = int(sum(1 for x in results.get("is_anomaly", []) if x))
                scores = pd.Series(results.get("anomaly_score", []), dtype=float)
                metrics = {
                    "total_points": float(total),
                    "anomalies_detected": float(detected),
                    "anomaly_rate_pct": float((detected / total * 100) if total else 0.0),
                    "mean_anomaly_score": float(scores.mean()) if len(scores) else 0.0,
                    "max_anomaly_score": float(scores.max()) if len(scores) else 0.0,
                }
            elif request.analysis_type == AnalyticsType.PREDICTION:
                preloaded_artifacts = await self._load_valid_cached_artifacts(
                    tenant_id=tenant_id,
                    device_id=request.device_id,
                    analysis_type=request.analysis_type.value,
                    model_keys=("xgboost", "lstm_classifier", "degradation_tracker"),
                    current_rows=current_rows,
                )
                await progress.update(
                    "feature_preparation",
                    phase_progress=1.0,
                    message="Features prepared for failure ensemble",
                )
                ensemble = self._build_failure_ensemble()
                model_expected_seconds = self._estimate_model_phase_seconds(
                    analysis_type=request.analysis_type,
                    current_rows=current_rows,
                )
                run_params = dict(params)
                run_params["__artifacts"] = preloaded_artifacts
                results = await self._run_with_phase_progress(
                    progress=progress,
                    phase="model_execution",
                    expected_seconds=model_expected_seconds,
                    message_template="Running failure ensemble",
                    work=lambda: ensemble.run(df, run_params),
                )
                artifact_updates = results.pop("artifact_updates", {}) if isinstance(results, dict) else {}
                await self._persist_artifact_updates(
                    tenant_id=tenant_id,
                    device_id=request.device_id,
                    analysis_type=request.analysis_type.value,
                    artifact_updates=artifact_updates,
                    current_rows=current_rows,
                    start_time=resolved_request.start_time,
                    end_time=resolved_request.end_time,
                )
                await progress.update(
                    "metrics_formatting",
                    phase_progress=0.35,
                    message="Calculating prediction metrics",
                )
                metrics = {
                    "failure_probability_pct": float(results.get("failure_probability_pct", 0.0)),
                    "model_confidence": str(results.get("model_confidence", "Low")),
                }
                cert_flag = await self._latest_accuracy_flag(request.device_id)
                if cert_flag:
                    results.setdefault("data_quality_flags", []).append(cert_flag)
            else:
                pipeline = self._get_forecasting_pipeline()
                if not pipeline:
                    raise AnalyticsError(
                        f"Unknown analysis type: {request.analysis_type}"
                    )

                train_df, test_df = pipeline.prepare_data(
                    df, params
                )

                await progress.update(
                    "feature_preparation",
                    phase_progress=1.0,
                    message="Features prepared for forecasting",
                )

                model = await self._run_with_phase_progress(
                    progress=progress,
                    phase="model_execution",
                    expected_seconds=self._estimate_model_phase_seconds(
                        analysis_type=request.analysis_type,
                        current_rows=current_rows,
                    ),
                    message_template="Training forecast model",
                    work=lambda: pipeline.train(train_df, resolved_request.model_name, params),
                )

                await progress.update(
                    "metrics_formatting",
                    phase_progress=0.2,
                    message="Running forecast inference",
                )
                results = await asyncio.to_thread(pipeline.predict, df, model, params)

                await progress.update(
                    "metrics_formatting",
                    phase_progress=0.5,
                    message="Calculating forecast metrics",
                )

                metrics = await asyncio.to_thread(
                    pipeline.evaluate, test_df, results, params
                )

            # ---------------------------------------------------------
            # Attach timestamp aligned points
            # ---------------------------------------------------------
            if request.analysis_type == AnalyticsType.ANOMALY:
                self._attach_anomaly_points(results, df)

            if request.analysis_type == AnalyticsType.PREDICTION:
                self._attach_failure_points(results, df)

            if settings.ml_formatted_results_enabled:
                formatting_message = (
                    "Formatting anomaly results"
                    if request.analysis_type == AnalyticsType.ANOMALY
                    else "Formatting analytics payload"
                )
                await progress.update(
                    "metrics_formatting",
                    phase_progress=0.8,
                    message=formatting_message,
                    phase_label=formatting_message,
                )
                requested_start = (
                    (
                        resolved_request.start_time.astimezone(timezone.utc)
                        if resolved_request.start_time.tzinfo
                        else resolved_request.start_time.replace(tzinfo=timezone.utc)
                    ).isoformat()
                    if resolved_request.start_time
                    else None
                )
                requested_end = (
                    (
                        resolved_request.end_time.astimezone(timezone.utc)
                        if resolved_request.end_time.tzinfo
                        else resolved_request.end_time.replace(tzinfo=timezone.utc)
                    ).isoformat()
                    if resolved_request.end_time
                    else None
                )
                dataset_window = dataset_window_from_key(getattr(resolved_request, "dataset_key", None))
                formatter = ResultFormatter()
                if request.analysis_type == AnalyticsType.ANOMALY:
                    results["formatted"] = formatter.format_anomaly_results(
                        device_id=request.device_id,
                        job_id=job_id,
                        anomaly_details=results.get("anomaly_details", []),
                        total_points=len(results.get("is_anomaly", [])),
                        sensitivity=params.get("sensitivity", "medium"),
                        lookback_days=int(params.get("lookback_days", 7)),
                        metadata={
                            "data_completeness_pct": results.get("data_completeness_pct", 100.0),
                            "fallback_mode": results.get("fallback_mode", False),
                            "days_available": results.get("days_available", params.get("lookback_days", 7)),
                            "data_points_analyzed": len(results.get("is_anomaly", [])),
                            "requested_range": {"start_time": requested_start, "end_time": requested_end},
                            "dataset_range": dataset_window,
                        },
                        ensemble=results.get("ensemble"),
                        reasoning=results.get("reasoning"),
                        data_quality_flags=results.get("data_quality_flags"),
                    )
                    await progress.update(
                        "metrics_formatting",
                        phase_progress=1.0,
                        message="Formatting anomaly results",
                        phase_label="Formatting anomaly results",
                    )
                elif request.analysis_type == AnalyticsType.PREDICTION:
                    results["formatted"] = formatter.format_failure_prediction_results(
                        device_id=request.device_id,
                        job_id=job_id,
                        failure_probability_pct=results.get("failure_probability_pct", 0.0),
                        risk_breakdown=results.get("risk_breakdown", {}),
                        risk_factors=results.get("risk_factors", []),
                        model_confidence=results.get("model_confidence", "Low"),
                        days_available=results.get("days_available", 0.0),
                        anomaly_score=float(metrics.get("anomaly_rate_pct", 0.0)) if isinstance(metrics, dict) else 0.0,
                        metadata={
                            "data_completeness_pct": results.get("data_completeness_pct", 100.0),
                            "fallback_mode": results.get("fallback_mode", False),
                            "data_points_analyzed": len(results.get("failure_probability", [])),
                            "sensitivity": params.get("sensitivity", "medium"),
                            "requested_range": {"start_time": requested_start, "end_time": requested_end},
                            "dataset_range": dataset_window,
                        },
                        ensemble=results.get("ensemble"),
                        time_to_failure=results.get("time_to_failure"),
                        reasoning=results.get("reasoning"),
                        degradation_series=results.get("degradation_series"),
                        data_quality_flags=results.get("data_quality_flags"),
                    )
            if request.analysis_type == AnalyticsType.ANOMALY:
                await progress.update(
                    "metrics_formatting",
                    phase_progress=1.0,
                    message="Anomaly results ready for persistence",
                    phase_label="Anomaly results ready for persistence",
                )

            # ---------------------------------------------------------
            # Permanent JSON safety boundary (NO NaN / NO inf)
            # ---------------------------------------------------------
            if isinstance(results, dict):
                results["coverage_result"] = coverage_result
                results.setdefault("data_quality_flags", [])
                if coverage_result["level"] == "partial_coverage":
                    results["data_quality_flags"].append(
                        {
                            "code": "PARTIAL_TELEMETRY_COVERAGE",
                            "severity": "warning",
                            "message": coverage_result["message"],
                        }
                    )
                formatted = results.get("formatted")
                if isinstance(formatted, dict):
                    formatted["coverage_result"] = coverage_result
            safe_results = _json_safe(results)
            safe_metrics = _json_safe(metrics)

            execution_time = int(time.time() - start_clock)
            await progress.update(
                "final_persistence",
                phase_progress=0.3,
                message="Persisting anomaly outputs" if request.analysis_type == AnalyticsType.ANOMALY else "Persisting analysis outputs",
                phase_label="Persisting anomaly outputs" if request.analysis_type == AnalyticsType.ANOMALY else "Persisting analysis outputs",
            )

            await self._result_repo.save_results(
                job_id=job_id,
                results=safe_results,
                accuracy_metrics=safe_metrics,
                execution_time_seconds=execution_time,
            )
            await progress.update(
                "final_persistence",
                phase_progress=1.0,
                message="Finalizing anomaly job" if request.analysis_type == AnalyticsType.ANOMALY else "Finalizing analytics job",
                phase_label="Finalizing anomaly job" if request.analysis_type == AnalyticsType.ANOMALY else "Finalizing analytics job",
            )

            await self._result_repo.update_job_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                completed_at=datetime.utcnow(),
                progress=100.0,
                message="Analysis completed successfully",
                phase="completed",
                phase_label="Completed",
                phase_progress=1.0,
            )

            self._logger.info(
                "job_completed",
                job_id=job_id,
                execution_time_seconds=execution_time,
            )

        except Exception as e:
            if isinstance(e, AnalyticsError) and "NO_TELEMETRY_IN_RANGE" in str(e):
                message = "No telemetry was available for the selected window."
                try:
                    await self._result_repo.rollback()
                except Exception:
                    pass
                coverage_result = build_window_coverage_result(
                    selected_window_start=request.start_time,
                    selected_window_end=request.end_time,
                    covered_duration_hours=0.0,
                    has_any_data=False,
                    warnings=[message],
                    has_usable_result=False,
                    artifact_generation_allowed=False,
                ).to_dict()
                await self._result_repo.save_results(
                    job_id=job_id,
                    results=_analytics_blocked_result(job_id, request, coverage_result),
                    accuracy_metrics={},
                    execution_time_seconds=int(time.time() - start_clock),
                )
                await self._result_repo.update_job_status(
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    completed_at=datetime.utcnow(),
                    progress=100.0,
                    message=message,
                    error_message=None,
                    phase="no_coverage",
                    phase_label="No Data",
                    phase_progress=1.0,
                )
                await self._result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    error_code="NO_TELEMETRY_IN_RANGE",
                )
                return
            self._logger.error(
                "job_failed",
                job_id=job_id,
                error=str(e),
                exc_info=True,
            )

            # IMPORTANT: always rollback a failed async session
            try:
                await self._result_repo.rollback()
            except Exception:
                pass

            await self._result_repo.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.utcnow(),
                message="Job failed",
                error_message=str(e),
                phase="failed",
                phase_label="Failed",
                phase_progress=1.0,
            )

            raise AnalyticsError(f"Job execution failed: {e}") from e

    def _estimate_model_phase_seconds(
        self,
        *,
        analysis_type: AnalyticsType,
        current_rows: int,
    ) -> float:
        # Row-aware baseline so simulator-sized runs finish quickly while larger
        # real datasets progress proportionally slower.
        rows = max(1, int(current_rows))
        if analysis_type in {AnalyticsType.ANOMALY, AnalyticsType.PREDICTION}:
            return max(8.0, 6.0 + rows / 1200.0)
        return max(6.0, 4.0 + rows / 1800.0)

    async def _run_with_phase_progress(
        self,
        *,
        progress: JobProgressReporter,
        phase: str,
        expected_seconds: float,
        message_template: str,
        work: Callable[[], Any],
    ) -> Any:
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.to_thread(work))
        start = time.monotonic()
        update_interval = 2.0

        while not task.done():
            elapsed = max(0.0, time.monotonic() - start)
            linear_ratio = elapsed / max(1.0, expected_seconds)
            if linear_ratio <= 0.85:
                ratio = linear_ratio
            else:
                tail = 1.0 - math.exp(-(linear_ratio - 0.85))
                ratio = min(0.98, 0.85 + (0.13 * tail))
            await progress.update(
                phase,
                phase_progress=ratio,
                message=f"{message_template} (estimated)",
            )
            await asyncio.sleep(update_interval)

        result = await task
        await progress.update(
            phase,
            phase_progress=1.0,
            message=f"{message_template} completed",
        )
        return result

    async def _load_valid_cached_artifacts(
        self,
        tenant_id: str | None,
        device_id: str,
        analysis_type: str,
        model_keys: Iterable[str],
        current_rows: int,
    ) -> Dict[str, Any]:
        cached: Dict[str, Any] = {}
        for model_key in model_keys:
            try:
                artifact = await self._result_repo.get_model_artifact(
                    tenant_id=tenant_id,
                    device_id=device_id,
                    analysis_type=analysis_type,
                    model_key=model_key,
                )
            except Exception:
                artifact = None
            if not artifact:
                continue

            is_valid, data_growth = self._is_artifact_valid(artifact, current_rows)
            if is_valid:
                self._logger.info(
                    "using_cached_model",
                    device_id=device_id,
                    analysis_type=analysis_type,
                    model_key=model_key,
                    data_growth_pct=round(data_growth * 100, 1),
                )
                cached[model_key] = artifact
            else:
                self._logger.info(
                    "retraining_model",
                    device_id=device_id,
                    analysis_type=analysis_type,
                    model_key=model_key,
                    data_growth_pct=round(data_growth * 100, 1),
                )
        return cached

    def _is_artifact_valid(
        self,
        artifact: Dict[str, Any],
        current_rows: int,
    ) -> tuple[bool, float]:
        payload = artifact.get("artifact_payload")
        if not payload:
            return False, 1.0

        expires_at = artifact.get("expires_at")
        now = datetime.now(timezone.utc)
        if expires_at is not None:
            expires_at = self._as_utc(expires_at)
            if expires_at <= now:
                return False, 1.0

        metadata = artifact.get("metrics") or {}
        cached_rows = int(metadata.get("trained_on_rows") or 0)
        if cached_rows <= 0:
            return False, 1.0

        data_growth = (current_rows - cached_rows) / max(cached_rows, 1)
        if data_growth > 0.20:
            return False, data_growth

        return True, data_growth

    async def _persist_artifact_updates(
        self,
        tenant_id: str | None,
        device_id: str,
        analysis_type: str,
        artifact_updates: Dict[str, Any],
        current_rows: int,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> None:
        if not artifact_updates:
            return

        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        trained_on_start = self._as_utc(start_time).isoformat() if start_time else None
        trained_on_end = self._as_utc(end_time).isoformat() if end_time else None

        for model_key, update in artifact_updates.items():
            if not isinstance(update, dict):
                continue
            payload = update.get("artifact_payload")
            schema_hash = update.get("feature_schema_hash")
            if not payload or not schema_hash:
                continue

            artifact_metrics = dict(update.get("metrics") or {})
            artifact_metrics.update(
                {
                    "trained_on_rows": current_rows,
                    "trained_on_start": trained_on_start,
                    "trained_on_end": trained_on_end,
                    "model_version": "1.0",
                }
            )
            artifact_metrics.setdefault("training_duration_seconds", 0.0)

            await self._result_repo.upsert_model_artifact(
                tenant_id=tenant_id,
                device_id=device_id,
                analysis_type=analysis_type,
                model_key=model_key,
                feature_schema_hash=str(schema_hash),
                artifact_payload=payload,
                model_version="1.0",
                metrics=artifact_metrics,
                expires_at=expires_at,
            )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    async def _latest_accuracy_flag(self, device_id: str) -> Dict[str, Any] | None:
        async with async_session_maker() as session:
            q = (
                select(AccuracyEvaluation)
                .where(AccuracyEvaluation.analysis_type == AnalyticsType.PREDICTION.value)
                .order_by(AccuracyEvaluation.created_at.desc())
                .limit(5)
            )
            rows = list((await session.execute(q)).scalars().all())

        if not rows:
            return {
                "type": "accuracy_certification",
                "is_certified": False,
                "severity": "info",
                "message": "Accuracy not certified yet — ingest labeled events and run /accuracy/evaluate.",
            }

        picked = None
        for row in rows:
            if row.scope_device_id == device_id:
                picked = row
                break
        if picked is None:
            picked = rows[0]

        return {
            "type": "accuracy_certification",
            "is_certified": bool(picked.is_certified),
            "severity": "info" if bool(picked.is_certified) else "warning",
            "message": (
                "Certified against labeled events."
                if bool(picked.is_certified)
                else f"Not certified yet — precision={picked.precision}, recall={picked.recall}, labels={picked.labeled_events}."
            ),
        }

    # ------------------------------------------------------------------
    # Anomaly points
    # ------------------------------------------------------------------

    def _attach_anomaly_points(
        self,
        results: Dict[str, Any],
        df: pd.DataFrame,
    ) -> None:

        def _parse_points(values: Any) -> pd.Series:
            series = pd.Series(values)
            if pd.api.types.is_datetime64_any_dtype(series):
                return pd.to_datetime(series, utc=True, errors="coerce")
            return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")

        if "timestamp" in df.columns:
            ts_col = "timestamp"
        elif "_time" in df.columns:
            ts_col = "_time"
        else:
            raise AnalyticsError(
                "No timestamp column found in dataset (expected 'timestamp' or '_time')"
            )

        anomaly_scores = results.get("anomaly_score")
        is_anomaly = results.get("is_anomaly")

        if anomaly_scores is None or is_anomaly is None:
            raise AnalyticsError(
                "Anomaly results missing 'anomaly_score' or 'is_anomaly'"
            )

        point_ts = results.get("point_timestamps")
        if isinstance(point_ts, list) and len(point_ts) == len(anomaly_scores):
            timestamps = _parse_points(point_ts)
        else:
            # Fallback: align on min length instead of failing whole job
            n = min(len(df), len(anomaly_scores), len(is_anomaly))
            if n <= 0:
                raise AnalyticsError("No data points available for anomaly point attachment")
            anomaly_scores = anomaly_scores[:n]
            is_anomaly = is_anomaly[:n]
            timestamps = _parse_points(df[ts_col].iloc[:n])

        if timestamps.isna().any():
            raise AnalyticsError(
                "Invalid timestamp values found in dataset"
            )

        points = []

        for ts, score, flag in zip(
            timestamps,
            anomaly_scores,
            is_anomaly,
        ):
            points.append(
                {
                    "timestamp": ts.isoformat(),
                    "anomaly_score": float(score),
                    "is_anomaly": bool(flag),
                }
            )

        results["points"] = points

    # ------------------------------------------------------------------
    # Failure prediction points
    # ------------------------------------------------------------------

    def _attach_failure_points(
        self,
        results: Dict[str, Any],
        df: pd.DataFrame,
    ) -> None:

        if "timestamp" in df.columns:
            ts_col = "timestamp"
        elif "_time" in df.columns:
            ts_col = "_time"
        else:
            raise AnalyticsError(
                "No timestamp column found in dataset (expected 'timestamp' or '_time')"
            )

        failure_prob = results.get("failure_probability")
        predicted = results.get("predicted_failure")
        ttf = results.get("time_to_failure_hours")

        if failure_prob is None or predicted is None or ttf is None:
            raise AnalyticsError(
                "Failure prediction results missing required fields"
            )

        point_ts = results.get("point_timestamps")
        if isinstance(point_ts, list) and len(point_ts) == len(failure_prob):
            timestamps = pd.to_datetime(
                point_ts,
                utc=True,
                errors="coerce",
            )
        else:
            # Fallback: align on min length instead of failing whole job
            n = min(len(df), len(failure_prob), len(predicted), len(ttf))
            if n <= 0:
                raise AnalyticsError("No data points available for failure point attachment")
            failure_prob = failure_prob[:n]
            predicted = predicted[:n]
            ttf = ttf[:n]
            timestamps = pd.to_datetime(
                df[ts_col].iloc[:n],
                utc=True,
                errors="coerce",
            )

        if timestamps.isna().any():
            raise AnalyticsError(
                "Invalid timestamp values found in dataset"
            )

        points = []

        for ts, p, f, h in zip(
            timestamps,
            failure_prob,
            predicted,
            ttf,
        ):
            ttf_val = None
            if h is not None:
                try:
                    ttf_val = float(h)
                except Exception:
                    ttf_val = None
            points.append(
                {
                    "timestamp": ts.isoformat(),
                    "failure_probability": float(p),
                    "predicted_failure": bool(f),
                    "time_to_failure_hours": ttf_val,
                }
            )

        results["points"] = points
