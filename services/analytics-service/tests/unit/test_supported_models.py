from pathlib import Path


def test_supported_models_advertise_deployment_models():
    route_file = Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "analytics.py"
    content = route_file.read_text()

    assert 'forecasting_models = ["prophet", "arima"]' in content
    assert "import statsmodels" not in content
    assert '"prophet"' in content
    assert '"arima"' in content
