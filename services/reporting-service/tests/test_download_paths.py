from src.utils.downloads import build_report_download_path


def test_build_report_download_path_uses_backend_route():
    assert build_report_download_path("report-123") == "/api/reports/report-123/download"
