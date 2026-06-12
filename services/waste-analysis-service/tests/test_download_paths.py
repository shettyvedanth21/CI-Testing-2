from src.utils.downloads import build_waste_download_path


def test_build_waste_download_path_uses_public_waste_route():
    assert build_waste_download_path("job-123") == "/api/waste/analysis/job-123/file"
