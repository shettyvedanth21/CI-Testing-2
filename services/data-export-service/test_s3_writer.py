from datetime import datetime, timezone
from types import SimpleNamespace
import sys
import types

from models import TelemetryData

sys.modules.setdefault("aioboto3", types.SimpleNamespace(Session=lambda *args, **kwargs: None))
from s3_writer import S3Writer


def test_convert_to_dataframe_preserves_raw_power_aliases_and_pf():
    writer = S3Writer(SimpleNamespace())

    df = writer._convert_to_dataframe(
        [
            TelemetryData(
                timestamp=datetime(2026, 4, 9, 14, 36, tzinfo=timezone.utc),
                device_id="VAL-SIGN-ALIAS",
                device_type="meter",
                location="plant-floor",
                voltage=-230.0,
                current=-5.0,
                power=-1000.0,
                active_power=-4000.0,
                power_factor=-0.85,
                temperature=41.0,
            )
        ]
    )

    row = df.iloc[0]
    assert float(row["power"]) == -1000.0
    assert float(row["active_power"]) == -4000.0
    assert float(row["power_factor"]) == -0.85
