import sys
import os
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

os.environ.setdefault(
    "INTERNAL_SERVICE_SHARED_SECRET",
    "test-internal-service-secret-at-least-32-chars",
)
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://user:pass@localhost:3306/test_waste_analysis",
)
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-influx-token")
os.environ.setdefault("INFLUXDB_ORG", "test-org")
os.environ.setdefault("INFLUXDB_BUCKET", "test-bucket")
