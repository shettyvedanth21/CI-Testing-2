"""Unit tests for job runner."""

import time
import pytest
from datetime import datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock
from unittest.mock import patch

import pandas as pd

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.models.schemas import AnalyticsRequest, AnalyticsType, JobStatus
from src.services.job_runner import JobRunner
from src.utils.exceptions import DatasetNotFoundError


class TestJobRunner:
    """Tests for JobRunner."""

    @pytest.fixture(autouse=True)
    def stub_ml_runtime(self, monkeypatch):
        class _FastAnomalyEnsemble:
            def run(self, df, params, progress_callback=None):
                size = len(df)
                return {
                    "is_anomaly": [False] * size,
                    "anomaly_score": [0.1] * size,
                }

        monkeypatch.setattr(
            "src.services.job_runner.AnomalyEnsemble",
            _FastAnomalyEnsemble,
        )
    
    @pytest.fixture
    def job_runner(self, mock_s3_client, mock_result_repository):
        """Create JobRunner instance with mocks."""
        from src.services.dataset_service import DatasetService
        
        dataset_service = DatasetService(mock_s3_client)
        return JobRunner(dataset_service, mock_result_repository)
    
    @pytest.mark.asyncio
    async def test_run_job_success(self, job_runner, mock_result_repository, sample_telemetry_data):
        """Test successful job execution."""
        # Mock dataset loading
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)
        
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=7),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="isolation_forest",
        )
        
        await job_runner.run_job("test-job-123", request)
        
        # Verify status updates
        assert mock_result_repository.update_job_status.called
        assert mock_result_repository.save_results.called
        
        # Verify job was marked completed
        final_call = mock_result_repository.update_job_status.call_args_list[-1]
        assert final_call.kwargs["status"] == JobStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_run_job_dataset_not_found(self, job_runner, mock_result_repository):
        """Test job failure when dataset not found."""
        job_runner._dataset_service.load_dataset = AsyncMock(
            side_effect=DatasetNotFoundError("Dataset not found")
        )
        
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=7),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="isolation_forest",
        )
        
        with pytest.raises(Exception):
            await job_runner.run_job("test-job-123", request)
    
    @pytest.mark.asyncio
    async def test_run_job_updates_progress(self, job_runner, mock_result_repository, sample_telemetry_data):
        """Test that job progress is updated during execution."""
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)
        
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=7),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="isolation_forest",
        )
        
        await job_runner.run_job("test-job-123", request)
        
        # Verify progress updates were called
        assert mock_result_repository.update_job_progress.called
        
        # Check that progress increases
        progress_calls = [
            call for call in mock_result_repository.update_job_progress.call_args_list
        ]
        assert len(progress_calls) > 0

    @pytest.mark.asyncio
    async def test_run_job_anomaly_uses_measured_stage_progress_without_estimated_plateau(
        self, job_runner, mock_result_repository, sample_telemetry_data
    ):
        """Anomaly path should use measured stage progress rather than estimated caps."""
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)

        class _MeasuredAnomalyEnsemble:
            def run(self, df, params, progress_callback=None):
                assert progress_callback is not None
                time.sleep(0.2)
                progress_callback("start", "anomaly_feature_preparation", "Preparing anomaly features")
                progress_callback("complete", "anomaly_feature_preparation", "Preparing anomaly features")
                progress_callback("start", "isolation_forest", "Training isolation forest")
                progress_callback("complete", "isolation_forest", "Isolation forest ready")
                progress_callback("start", "lstm_sequence_preparation", "Preparing LSTM sequences")
                progress_callback("complete", "lstm_sequence_preparation", "LSTM sequences prepared")
                progress_callback("start", "lstm_model", "Training temporal autoencoder")
                progress_callback("complete", "lstm_model", "Temporal autoencoder ready")
                progress_callback("start", "lstm_scoring", "Scoring temporal autoencoder")
                progress_callback("complete", "lstm_scoring", "Temporal autoencoder scoring complete")
                progress_callback("start", "cusum_scoring", "Running CUSUM drift detection")
                progress_callback("complete", "cusum_scoring", "CUSUM drift detection complete")
                progress_callback("start", "ensemble_voting", "Combining ensemble signals")
                progress_callback("complete", "ensemble_voting", "Anomaly ensemble signals combined")
                n = min(25, len(df))
                return {
                    "is_anomaly": [False] * n,
                    "anomaly_score": [0.1] * n,
                }

        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=7),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="anomaly_ensemble",
        )

        with patch(
            "src.services.job_runner.AnomalyEnsemble",
            return_value=_MeasuredAnomalyEnsemble(),
        ), patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="test",
                ml_require_exact_dataset_range=False,
                ml_data_readiness_gate_enabled=False,
                ml_formatted_results_enabled=False,
            ),
        ):
            await job_runner.run_job("test-job-long-phase", request)

        model_phase_calls = [
            call for call in mock_result_repository.update_job_progress.call_args_list
            if call.kwargs.get("phase") == "model_execution"
        ]
        model_phase_progress = [float(call.kwargs.get("phase_progress", 0.0)) for call in model_phase_calls]
        model_phase_labels = [str(call.kwargs.get("phase_label") or "") for call in model_phase_calls]
        overall_progress = [float(call.kwargs.get("progress", 0.0)) for call in mock_result_repository.update_job_progress.call_args_list]

        assert model_phase_progress
        assert 0.98 not in model_phase_progress
        assert max(model_phase_progress) == pytest.approx(1.0)
        assert all("(estimated)" not in str(call.kwargs.get("message", "")) for call in model_phase_calls)
        assert all("(estimated)" not in label for label in model_phase_labels)
        assert overall_progress == sorted(overall_progress)
        assert "Training isolation forest" in model_phase_labels
        assert "Training temporal autoencoder" in model_phase_labels
        assert "Combining ensemble signals" in model_phase_labels
        assert all(abs(progress - 84.3) > 0.05 for progress in overall_progress)

    @pytest.mark.asyncio
    async def test_run_job_anomaly_emits_truthful_substage_labels_in_order(
        self, job_runner, mock_result_repository, sample_telemetry_data
    ):
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)

        class _OrderedAnomalyEnsemble:
            def run(self, df, params, progress_callback=None):
                assert progress_callback is not None
                events = [
                    ("start", "anomaly_feature_preparation", "Preparing anomaly features"),
                    ("complete", "anomaly_feature_preparation", "Preparing anomaly features"),
                    ("start", "isolation_forest", "Loading isolation forest"),
                    ("complete", "isolation_forest", "Isolation forest ready"),
                    ("start", "lstm_sequence_preparation", "Preparing LSTM sequences"),
                    ("complete", "lstm_sequence_preparation", "LSTM sequences prepared"),
                    ("start", "lstm_model", "Loading temporal autoencoder"),
                    ("complete", "lstm_model", "Temporal autoencoder ready"),
                    ("start", "lstm_scoring", "Scoring temporal autoencoder"),
                    ("complete", "lstm_scoring", "Temporal autoencoder scoring complete"),
                    ("start", "cusum_scoring", "Running CUSUM drift detection"),
                    ("complete", "cusum_scoring", "CUSUM drift detection complete"),
                    ("start", "ensemble_voting", "Combining ensemble signals"),
                    ("complete", "ensemble_voting", "Anomaly ensemble signals combined"),
                ]
                for event in events:
                    progress_callback(*event)
                size = len(df)
                return {
                    "is_anomaly": [False] * size,
                    "anomaly_score": [0.1] * size,
                }

        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="anomaly_ensemble",
        )

        with patch(
            "src.services.job_runner.AnomalyEnsemble",
            return_value=_OrderedAnomalyEnsemble(),
        ), patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="test",
                ml_require_exact_dataset_range=False,
                ml_data_readiness_gate_enabled=False,
                ml_formatted_results_enabled=False,
            ),
        ):
            await job_runner.run_job("test-job-stage-order", request)

        phase_labels = [
            str(call.kwargs.get("phase_label") or "")
            for call in mock_result_repository.update_job_progress.call_args_list
            if call.kwargs.get("phase") in {"feature_preparation", "model_execution", "metrics_formatting", "final_persistence"}
        ]

        expected_labels = [
            "Loading anomaly model artifacts",
            "Preparing anomaly features",
            "Loading isolation forest",
            "Preparing LSTM sequences",
            "Loading temporal autoencoder",
            "Scoring temporal autoencoder",
            "Running CUSUM drift detection",
            "Combining ensemble signals",
            "Anomaly results ready for persistence",
            "Persisting anomaly outputs",
        ]

        for label in expected_labels:
            assert label in phase_labels

        ordered_positions = [phase_labels.index(label) for label in expected_labels]
        assert ordered_positions == sorted(ordered_positions)

    @pytest.mark.asyncio
    async def test_run_job_anomaly_emits_elapsed_heartbeats_for_long_temporal_stage(
        self, job_runner, mock_result_repository, sample_telemetry_data
    ):
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)

        class _SlowTemporalStageEnsemble:
            def run(self, df, params, progress_callback=None):
                assert progress_callback is not None
                progress_callback("start", "anomaly_feature_preparation", "Preparing anomaly features")
                progress_callback("complete", "anomaly_feature_preparation", "Preparing anomaly features")
                progress_callback("start", "isolation_forest", "Training isolation forest")
                progress_callback("complete", "isolation_forest", "Isolation forest ready")
                progress_callback("start", "lstm_sequence_preparation", "Preparing LSTM sequences")
                progress_callback("complete", "lstm_sequence_preparation", "LSTM sequences prepared")
                progress_callback("start", "lstm_model", "Training temporal autoencoder")
                time.sleep(1.25)
                progress_callback("complete", "lstm_model", "Temporal autoencoder ready")
                progress_callback("start", "lstm_scoring", "Scoring temporal autoencoder")
                progress_callback("complete", "lstm_scoring", "Temporal autoencoder scoring complete")
                progress_callback("start", "cusum_scoring", "Running CUSUM drift detection")
                progress_callback("complete", "cusum_scoring", "CUSUM drift detection complete")
                progress_callback("start", "ensemble_voting", "Combining ensemble signals")
                progress_callback("complete", "ensemble_voting", "Anomaly ensemble signals combined")
                size = len(df)
                return {
                    "is_anomaly": [False] * size,
                    "anomaly_score": [0.1] * size,
                }

        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="anomaly_ensemble",
        )

        with patch(
            "src.services.job_runner.AnomalyEnsemble",
            return_value=_SlowTemporalStageEnsemble(),
        ), patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="test",
                ml_require_exact_dataset_range=False,
                ml_data_readiness_gate_enabled=False,
                ml_formatted_results_enabled=False,
                ml_stage_activity_heartbeat_seconds=0,
            ),
        ):
            await job_runner.run_job("test-job-heartbeat", request)

        heartbeat_labels = [
            str(call.kwargs.get("phase_label") or "")
            for call in mock_result_repository.update_job_progress.call_args_list
            if "Training temporal autoencoder (" in str(call.kwargs.get("phase_label") or "")
        ]
        assert heartbeat_labels

    @pytest.mark.asyncio
    async def test_run_job_falls_back_to_direct_data_when_readiness_unavailable(
        self, job_runner, mock_result_repository, sample_telemetry_data
    ):
        """If export/S3 readiness path times out, job should still run via direct exact-range load."""
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="isolation_forest",
        )

        with patch(
            "src.services.job_runner.ensure_device_ready",
            AsyncMock(return_value=("D1", None, {"reason": "export_timeout", "export_attempted": True, "wait_seconds": 60.0})),
        ) as readiness_mock, patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="development",
                ml_require_exact_dataset_range=True,
                ml_data_readiness_gate_enabled=True,
                ml_formatted_results_enabled=True,
            ),
        ):
            await job_runner.run_job("test-job-fallback-123", request)

        assert readiness_mock.await_args.kwargs["tenant_id"] is None
        final_call = mock_result_repository.update_job_status.call_args_list[-1]
        assert final_call.kwargs["status"] == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_job_passes_tenant_scope_to_exact_range_readiness(
        self, job_runner, mock_result_repository, sample_telemetry_data
    ):
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="isolation_forest",
            parameters={"tenant_id": "ORG-A"},
        )

        with patch(
            "src.services.job_runner.ensure_device_ready",
            AsyncMock(return_value=("D1", "datasets/D1/20260401_20260401.parquet", {"reason": "ready_exact"})),
        ) as readiness_mock, patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="development",
                ml_require_exact_dataset_range=True,
                ml_data_readiness_gate_enabled=True,
                ml_formatted_results_enabled=True,
            ),
        ):
            await job_runner.run_job("test-job-tenant-readiness", request)

        assert readiness_mock.await_args.kwargs["tenant_id"] == "ORG-A"

    @pytest.mark.asyncio
    async def test_run_job_finalizes_no_telemetry_as_business_outcome(
        self, job_runner, mock_result_repository
    ):
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="isolation_forest",
            parameters={"tenant_id": "ORG-A"},
        )

        with patch(
            "src.services.job_runner.ensure_device_ready",
            AsyncMock(return_value=("D1", None, {"reason": "no_telemetry_in_range", "export_attempted": True, "wait_seconds": 0.0})),
        ), patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="development",
                ml_require_exact_dataset_range=True,
                ml_data_readiness_gate_enabled=True,
                ml_formatted_results_enabled=True,
            ),
        ):
            await job_runner.run_job("test-job-no-telemetry", request)

        saved = mock_result_repository.save_results.await_args.kwargs["results"]
        assert saved["coverage_result"]["level"] == "no_coverage"
        assert saved["coverage_result"]["usable_for_business_decisions"] is False
        assert saved["formatted"]["analysis_type"] == "anomaly_detection"
        assert saved["formatted"]["status"] == "no_data"
        mock_result_repository.update_job_status.assert_any_await(
            job_id="test-job-no-telemetry",
            status=JobStatus.COMPLETED,
            completed_at=ANY,
            progress=100.0,
            message="No telemetry was available for the selected window.",
            error_message=None,
            phase="no_coverage",
            phase_label="No Data",
            phase_progress=1.0,
        )
        mock_result_repository.update_job_queue_metadata.assert_awaited_with(
            job_id="test-job-no-telemetry",
            error_code="NO_TELEMETRY_IN_RANGE",
        )

    @pytest.mark.asyncio
    async def test_run_job_finalizes_insufficient_coverage_with_renderable_blocked_contract(
        self, job_runner, mock_result_repository
    ):
        ts = pd.Timestamp("2026-05-01T00:00:00Z")
        job_runner._dataset_service.load_dataset = AsyncMock(
            return_value=pd.DataFrame([{"timestamp": ts, "power": 10.0}])
        )
        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.PREDICTION,
            model_name="failure_ensemble",
            parameters={"tenant_id": "ORG-A"},
        )

        with patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="test",
                ml_require_exact_dataset_range=False,
                ml_data_readiness_gate_enabled=False,
                ml_formatted_results_enabled=True,
            ),
        ):
            await job_runner.run_job("test-job-insufficient-coverage", request)

        saved = mock_result_repository.save_results.await_args.kwargs["results"]
        assert saved["coverage_result"]["level"] == "insufficient_coverage"
        assert saved["formatted"]["analysis_type"] == "failure_prediction"
        assert saved["formatted"]["status"] == "insufficient_coverage"
        mock_result_repository.update_job_queue_metadata.assert_awaited_with(
            job_id="test-job-insufficient-coverage",
            error_code="INSUFFICIENT_TELEMETRY_COVERAGE",
        )

    @pytest.mark.asyncio
    async def test_run_job_persists_artifact_updates_with_request_tenant_scope(
        self, job_runner, mock_result_repository, sample_telemetry_data
    ):
        """Artifact persistence must use request tenant scope, not runner-owned job state."""
        job_runner._dataset_service.load_dataset = AsyncMock(return_value=sample_telemetry_data)

        class _ArtifactAnomalyEnsemble:
            def run(self, df, params, progress_callback=None):
                n = len(df)
                return {
                    "is_anomaly": [False] * n,
                    "anomaly_score": [0.1] * n,
                    "artifact_updates": {
                        "isolation_forest": {
                            "artifact_payload": b"model-bytes",
                            "feature_schema_hash": "schema-v1",
                            "metrics": {"training_duration_seconds": 1.25},
                        }
                    },
                }

        request = AnalyticsRequest(
            device_id="D1",
            start_time=datetime.now() - timedelta(days=1),
            end_time=datetime.now(),
            analysis_type=AnalyticsType.ANOMALY,
            model_name="anomaly_ensemble",
            parameters={"tenant_id": "ORG-A"},
        )

        with patch(
            "src.services.job_runner.AnomalyEnsemble",
            return_value=_ArtifactAnomalyEnsemble(),
        ), patch(
            "src.services.job_runner.get_settings",
            return_value=MagicMock(
                app_env="test",
                ml_require_exact_dataset_range=False,
                ml_data_readiness_gate_enabled=False,
                ml_formatted_results_enabled=False,
            ),
        ):
            await job_runner.run_job("test-job-artifact-tenant", request)

        mock_result_repository.get_model_artifact.assert_any_await(
            tenant_id="ORG-A",
            device_id="D1",
            analysis_type=AnalyticsType.ANOMALY.value,
            model_key="isolation_forest",
        )
        mock_result_repository.upsert_model_artifact.assert_awaited_once()
        assert mock_result_repository.upsert_model_artifact.await_args.kwargs["tenant_id"] == "ORG-A"
        final_call = mock_result_repository.update_job_status.call_args_list[-1]
        assert final_call.kwargs["status"] == JobStatus.COMPLETED

    def test_model_phase_time_estimate_scales_with_dataset_size(self, job_runner):
        small = job_runner._estimate_model_phase_seconds(
            analysis_type=AnalyticsType.ANOMALY,
            current_rows=500,
        )
        large = job_runner._estimate_model_phase_seconds(
            analysis_type=AnalyticsType.ANOMALY,
            current_rows=50000,
        )
        assert large > small
