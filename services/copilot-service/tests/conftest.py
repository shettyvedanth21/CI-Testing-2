import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MYSQL_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("MYSQL_READONLY_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("DATA_SERVICE_URL", "http://localhost:8081")
os.environ.setdefault("REPORTING_SERVICE_URL", "http://localhost:8085")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://localhost:8010")
