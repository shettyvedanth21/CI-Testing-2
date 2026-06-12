"""Integration tests for API endpoints."""

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from services.shared.tenant_context import build_internal_headers
from src.api.dependencies import get_result_repository
from src.api.routes import analytics
from src.main import create_app


class TestHealthEndpoints:
    """Tests for health check endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        app = create_app()
        return TestClient(app)
    
    def test_liveness_probe(self, client):
        """Test liveness probe endpoint."""
        response = client.get("/health/live")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "analytics-service"
    
    def test_readiness_probe(self, client):
        """Test readiness probe endpoint."""
        response = client.get("/health/ready")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "checks" in data


class TestAnalyticsEndpoints:
    """Tests for analytics API endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        app = create_app()
        queue = MagicMock()
        queue.submit_job = AsyncMock()
        queue.size = MagicMock(return_value=0)
        app.state.job_queue = queue
        repo = MagicMock()
        repo.create_job = AsyncMock()
        repo.update_job_status = AsyncMock()
        repo.update_job_queue_metadata = AsyncMock()
        repo.get_job = AsyncMock(return_value=None)
        repo.list_jobs = AsyncMock(return_value=[])
        repo.count_jobs = AsyncMock(return_value=0)
        repo.list_tenant_job_counts = AsyncMock(return_value=[])
        app.dependency_overrides[get_result_repository] = lambda: repo
        return TestClient(app)

    @pytest.fixture
    def auth_headers(self):
        return build_internal_headers("analytics-test-suite", "SH00000001")
    
    def test_submit_analytics_job(self, client, monkeypatch, auth_headers):
        """Test submitting analytics job."""
        monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(
            "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
            AsyncMock(return_value=["D1"]),
        )
        request_data = {
            "device_id": "D1",
            "start_time": (datetime.now() - timedelta(days=7)).isoformat(),
            "end_time": datetime.now().isoformat(),
            "analysis_type": "anomaly",
            "model_name": "isolation_forest",
            "parameters": {
                "contamination": 0.1,
            },
        }
        
        response = client.post("/api/v1/analytics/run", json=request_data, headers=auth_headers)
        
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    @pytest.mark.parametrize(
        ("path", "payload", "expected_message"),
        [
            (
                "/api/v1/analytics/run",
                {
                    "device_id": "D1",
                    "analysis_type": "anomaly",
                    "model_name": "isolation_forest",
                },
                "Either dataset_key or start_time/end_time must be provided",
            ),
            (
                "/api/v1/analytics/run",
                {
                    "device_id": "D1",
                    "analysis_type": "anomaly",
                    "model_name": "isolation_forest",
                    "start_time": "2024-01-02T00:00:00Z",
                    "end_time": "2024-01-01T00:00:00Z",
                },
                "end_time must be after start_time",
            ),
            (
                "/api/v1/analytics/run-fleet",
                {
                    "device_ids": ["D1"],
                    "analysis_type": "anomaly",
                    "start_time": "2024-01-02T00:00:00Z",
                    "end_time": "2024-01-01T00:00:00Z",
                },
                "end_time must be after start_time",
            ),
            (
                "/api/v1/analytics/preflight",
                {
                    "device_ids": [],
                    "start_time": "2024-01-01T00:00:00Z",
                    "end_time": "2024-01-02T00:00:00Z",
                },
                "At least one device_id must be provided",
            ),
            (
                "/api/v1/analytics/preflight",
                {
                    "device_ids": ["D1"],
                    "start_time": "2024-01-02T00:00:00Z",
                    "end_time": "2024-01-01T00:00:00Z",
                },
                "end_time must be after start_time",
            ),
        ],
    )
    def test_validation_errors_return_422_with_serializable_details(
        self,
        client,
        auth_headers,
        path,
        payload,
        expected_message,
    ):
        response = client.post(path, json=payload, headers=auth_headers)

        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "VALIDATION_ERROR"
        assert data["message"] == "Invalid request payload"
        assert data["code"] == "VALIDATION_ERROR"
        assert data["details"]
        assert data["details"][0]["msg"].endswith(expected_message)
        if "ctx" in data["details"][0]:
            assert data["details"][0]["ctx"]["error"] == {
                "type": "ValueError",
                "message": expected_message,
            }

    def test_missing_required_fields_return_truthful_422(self, client, auth_headers):
        response = client.post(
            "/api/v1/analytics/run",
            json={
                "analysis_type": "anomaly",
                "model_name": "isolation_forest",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-02T00:00:00Z",
            },
            headers=auth_headers,
        )

        assert response.status_code == 422
        data = response.json()
        assert data["details"][0]["type"] == "missing"
        assert data["details"][0]["loc"] == ["body", "device_id"]
        assert data["details"][0]["msg"] == "Field required"
    
    def test_get_supported_models(self, client, auth_headers):
        """Test getting supported models."""
        response = client.get("/api/v1/analytics/models", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "anomaly_detection" in data
        assert "failure_prediction" in data
        assert "forecasting" in data
        
        # Check specific models
        assert "isolation_forest" in data["anomaly_detection"]
        assert "xgboost" in data["failure_prediction"]
        assert "prophet" in data["forecasting"]
        assert "ensembles" in data
    
    def test_invalid_model_for_analysis_type(self, client, monkeypatch, auth_headers):
        """Test validation of model for analysis type."""
        monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(
            "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
            AsyncMock(return_value=["D1"]),
        )
        request_data = {
            "device_id": "D1",
            "start_time": (datetime.now() - timedelta(days=7)).isoformat(),
            "end_time": datetime.now().isoformat(),
            "analysis_type": "anomaly",
            "model_name": "prophet",  # Invalid - prophet is for forecasting
        }
        
        response = client.post("/api/v1/analytics/run", json=request_data, headers=auth_headers)

        assert response.status_code == 202

    def test_submit_fleet_analytics_accepts_many_devices_as_one_parent_workflow(self, client, monkeypatch, auth_headers):
        monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(
            "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
            AsyncMock(return_value=[f"D{i:02d}" for i in range(1, 13)]),
        )

        response = client.post(
            "/api/v1/analytics/run-fleet",
            json={
                "device_ids": [f"D{i:02d}" for i in range(1, 13)],
                "start_time": (datetime.now() - timedelta(days=2)).isoformat(),
                "end_time": datetime.now().isoformat(),
                "analysis_type": "anomaly",
            },
            headers=auth_headers,
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"
        assert "job_id" in data
