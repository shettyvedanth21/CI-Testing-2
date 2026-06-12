"""Replay historical CSV telemetry through the live MQTT ingestion path."""

from __future__ import annotations

import argparse
import csv
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from config import SimulatorConfig
from mqtt_client import MQTTClient

logger = logging.getLogger(__name__)

CSV_METADATA_PREFIX = "#"
CSV_EXCLUDED_COLUMNS = {
    "",
    "result",
    "table",
    "_start",
    "_stop",
    "_time",
    "_measurement",
    "device_id",
    "tenant_id",
}


@dataclass(frozen=True)
class ReplaySample:
    timestamp: datetime
    telemetry: dict[str, float]

    def to_payload(self, *, device_id: str, tenant_id: str | None) -> dict[str, object]:
        payload: dict[str, object] = {
            "device_id": device_id,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "schema_version": "v1",
            **self.telemetry,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        return payload


def _iter_data_lines(path: Path) -> Iterable[str]:
    with path.open("r", newline="") as handle:
        for line in handle:
            if line.startswith(CSV_METADATA_PREFIX):
                continue
            yield line


def _parse_timestamp(raw_timestamp: str) -> datetime:
    return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))


def _parse_float(value: str) -> float | None:
    stripped = value.strip()
    if stripped == "":
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def load_replay_samples(path: str | Path) -> list[ReplaySample]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    reader = csv.DictReader(_iter_data_lines(csv_path))
    if not reader.fieldnames:
        raise ValueError("CSV file does not contain a usable header row")

    samples: list[ReplaySample] = []
    for row in reader:
        raw_timestamp = (row.get("_time") or "").strip()
        if not raw_timestamp:
            continue

        telemetry: dict[str, float] = {}
        for field, raw_value in row.items():
            if field in CSV_EXCLUDED_COLUMNS or raw_value is None:
                continue
            parsed = _parse_float(raw_value)
            if parsed is not None:
                telemetry[field] = parsed

        samples.append(
            ReplaySample(
                timestamp=_parse_timestamp(raw_timestamp),
                telemetry=telemetry,
            )
        )

    if not samples:
        raise ValueError("CSV file did not contain any replayable telemetry rows")

    return samples


class CSVTelemetryReplayer:
    def __init__(
        self,
        *,
        csv_path: str | Path,
        config: SimulatorConfig,
        mqtt_client: MQTTClient | None = None,
        sleeper=time.sleep,
        preserve_delays: bool = True,
    ) -> None:
        self._csv_path = Path(csv_path)
        self._config = config
        self._mqtt_client = mqtt_client or MQTTClient(
            broker_host=config.broker_host,
            broker_port=config.broker_port,
            client_id=f"csv-replay-{config.tenant_id}-{config.device_id}".replace("/", "-"),
        )
        self._sleeper = sleeper
        self._preserve_delays = preserve_delays
        self._running = False
        self._published = 0
        self._samples = load_replay_samples(self._csv_path)

    @property
    def topic(self) -> str:
        return self._config.topic

    @property
    def samples(self) -> Sequence[ReplaySample]:
        return self._samples

    def stop(self) -> None:
        self._running = False

    def start(self) -> int:
        logger.info(
            "Starting CSV telemetry replay",
            extra={
                "csv_path": str(self._csv_path),
                "device_id": self._config.device_id,
                "tenant_id": self._config.tenant_id,
                "topic": self.topic,
                "sample_count": len(self._samples),
            },
        )
        if not self._mqtt_client.connect():
            raise RuntimeError("Failed to connect to MQTT broker")

        self._running = True
        try:
            self._run()
        finally:
            self._mqtt_client.disconnect()
        return self._published

    def _run(self) -> None:
        previous_timestamp: datetime | None = None

        for sample in self._samples:
            if not self._running:
                logger.info("CSV telemetry replay stopped before completion")
                return

            if previous_timestamp is not None:
                gap_seconds = max(0.0, (sample.timestamp - previous_timestamp).total_seconds())
                if self._preserve_delays and gap_seconds > 0:
                    self._sleeper(gap_seconds)

            payload = sample.to_payload(
                device_id=self._config.device_id,
                tenant_id=self._config.tenant_id,
            )
            if not self._mqtt_client.publish(self.topic, payload):
                raise RuntimeError(
                    f"Failed to publish telemetry for {self._config.device_id} to {self.topic}"
                )

            self._published += 1
            previous_timestamp = sample.timestamp

            logger.info(
                "Replayed telemetry sample",
                extra={
                    "device_id": self._config.device_id,
                    "published_count": self._published,
                    "timestamp": payload["timestamp"],
                },
            )

        logger.info(
            "CSV telemetry replay completed",
            extra={
                "device_id": self._config.device_id,
                "published_count": self._published,
            },
        )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay telemetry CSV through the live MQTT ingestion path",
    )
    parser.add_argument("--csv", required=True, help="Path to the telemetry CSV export")
    parser.add_argument("--device-id", required=True, help="Target platform device identifier")
    parser.add_argument(
        "--tenant-id",
        "--tenant-id",
        dest="tenant_id",
        required=True,
        help="Tenant or organization identifier used in the MQTT topic",
    )
    parser.add_argument("--broker", default="localhost", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    parser.add_argument(
        "--no-delay",
        action="store_true",
        help="Publish rows back-to-back while preserving the original payload timestamps",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_argument_parser().parse_args(argv)


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    if log_level.upper() != "DEBUG":
        logging.getLogger("paho").setLevel(logging.WARNING)


def _register_signal_handlers(replayer: CSVTelemetryReplayer) -> None:
    def _handle_signal(_signum, _frame) -> None:
        replayer.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)

    config = SimulatorConfig(
        device_id=args.device_id,
        tenant_id=args.tenant_id,
        broker_host=args.broker,
        broker_port=args.port,
        publish_interval=1.0,
        log_level=args.log_level,
    )
    replayer = CSVTelemetryReplayer(
        csv_path=args.csv,
        config=config,
        preserve_delays=not args.no_delay,
    )
    _register_signal_handlers(replayer)

    try:
        replayer.start()
        return 0
    except KeyboardInterrupt:
        replayer.stop()
        return 0
    except Exception:
        logger.exception("CSV telemetry replay failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
