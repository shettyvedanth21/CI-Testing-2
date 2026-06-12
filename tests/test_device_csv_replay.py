from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools" / "device-simulator"


def load_tool_module(module_name: str, filename: str):
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    spec = importlib.util.spec_from_file_location(module_name, TOOLS_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_load_replay_samples_skips_influx_metadata_and_extracts_numeric_fields(tmp_path):
    replay = load_tool_module("device_csv_replay_parse", "csv_replay.py")

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "#group,FALSE,FALSE,TRUE,TRUE,FALSE,TRUE,TRUE,TRUE\n"
        "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,string,string,double\n"
        "#default,_result,,,,,,,\n"
        ",result,table,_start,_stop,_time,_measurement,device_id,current,power\n"
        ",,0,2026-04-08T00:00:00Z,2026-04-09T00:00:00Z,2026-04-09T06:56:01Z,device_telemetry,TD00000001,0.4,-12.5\n"
        ",,0,2026-04-08T00:00:00Z,2026-04-09T00:00:00Z,2026-04-09T06:56:21Z,device_telemetry,TD00000001,,7.0\n",
        encoding="utf-8",
    )

    samples = replay.load_replay_samples(csv_path)

    assert len(samples) == 2
    assert samples[0].timestamp.isoformat() == "2026-04-09T06:56:01+00:00"
    assert samples[0].telemetry == {"current": 0.4, "power": -12.5}
    assert samples[1].telemetry == {"power": 7.0}


def test_load_replay_samples_skips_non_numeric_export_columns(tmp_path):
    replay = load_tool_module("device_csv_replay_non_numeric", "csv_replay.py")

    csv_path = tmp_path / "sample_with_export_fields.csv"
    csv_path.write_text(
        "#group,FALSE,FALSE,TRUE,TRUE,FALSE,TRUE,TRUE,TRUE,TRUE,TRUE\n"
        ",result,table,_start,_stop,_time,_measurement,device_id,current,power,enrichment_status,schema_version\n"
        ",,0,2026-03-26T00:00:00Z,2026-03-27T00:00:00Z,2026-03-26T09:31:55Z,device_telemetry,TD-1,28.8,-6517.511,success,v1\n",
        encoding="utf-8",
    )

    samples = replay.load_replay_samples(csv_path)

    assert len(samples) == 1
    assert samples[0].telemetry == {"current": 28.8, "power": -6517.511}


def test_replayer_preserves_original_row_gaps_and_topic_contract():
    replay = load_tool_module("device_csv_replay_runtime", "csv_replay.py")
    config_mod = load_tool_module("device_csv_replay_config", "config.py")

    class FakeMQTTClient:
        def __init__(self):
            self.is_connected = True
            self.published = []

        def connect(self):
            return True

        def disconnect(self):
            return None

        def publish(self, topic, payload):
            self.published.append((topic, payload))
            return True

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    config = config_mod.SimulatorConfig(
        device_id="TD00000002",
        tenant_id="SH00000001",
        broker_host="localhost",
        broker_port=1883,
    )
    fake_client = FakeMQTTClient()
    replayer = replay.CSVTelemetryReplayer(
        csv_path=TOOLS_DIR.parents[1] / "td00000001.csv",
        config=config,
        mqtt_client=fake_client,
        sleeper=fake_sleep,
    )

    original_samples = replayer._samples
    replayer._samples = original_samples[:3]
    published = replayer.start()

    assert published == 3
    assert sleep_calls == [20.0, 21.0]
    assert all(topic == "SH00000001/devices/TD00000002/telemetry" for topic, _ in fake_client.published)
    assert fake_client.published[0][1]["device_id"] == "TD00000002"
    assert fake_client.published[0][1]["tenant_id"] == "SH00000001"
    assert fake_client.published[0][1]["timestamp"] == "2026-04-09T06:56:01Z"


def test_replayer_can_seed_without_runtime_delay_when_exact_timestamps_are_preserved():
    replay = load_tool_module("device_csv_replay_no_delay", "csv_replay.py")
    config_mod = load_tool_module("device_csv_replay_no_delay_config", "config.py")

    class FakeMQTTClient:
        def __init__(self):
            self.is_connected = True
            self.published = []

        def connect(self):
            return True

        def disconnect(self):
            return None

        def publish(self, topic, payload):
            self.published.append((topic, payload))
            return True

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    config = config_mod.SimulatorConfig(
        device_id="TD00000003",
        tenant_id="SH00000001",
        broker_host="localhost",
        broker_port=1883,
    )
    fake_client = FakeMQTTClient()
    replayer = replay.CSVTelemetryReplayer(
        csv_path=TOOLS_DIR.parents[1] / "td00000001.csv",
        config=config,
        mqtt_client=fake_client,
        sleeper=fake_sleep,
        preserve_delays=False,
    )

    original_samples = replayer._samples
    replayer._samples = original_samples[:3]
    published = replayer.start()

    assert published == 3
    assert sleep_calls == []
    assert [payload["timestamp"] for _, payload in fake_client.published] == [
        "2026-04-09T06:56:01Z",
        "2026-04-09T06:56:21Z",
        "2026-04-09T06:56:42Z",
    ]
