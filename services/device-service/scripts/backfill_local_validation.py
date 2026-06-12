"""Local-only validation backfill.

Seeds historical STEADY_RUNNING feature windows so that baseline promotion
occurs under the real DEGRADATION_BASELINE_MINIMUM_DAYS=7 and
ANOMALY_BASELINE_MINIMUM_DAYS=7 rules, without lowering thresholds.

This script is NOT used in production.  It is a developer convenience for
repeatable local end-to-end verification of the machine-health feature.

Usage (inside device-service container):
    python scripts/backfill_local_validation.py

Environment:
    LOCAL_BACKFILL_ENABLED  – must be "true" to activate (safety gate)
    DATABASE_URL            – standard SQLAlchemy async URL (auto-detected)
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

_SEED_DEVICE_ID = "AD00000001"
_SEED_TENANT_ID = "SH00000001"
_BACKFILL_DAYS = 8
_WINDOW_MINUTES = 5
_STEADY_RUNNING = "STEADY_RUNNING"

_BASE = {
    "current_avg_mean": 40.0,
    "current_avg_std": 0.5,
    "current_avg_p95": 41.5,
    "current_l1_mean": 40.0,
    "current_l2_mean": 40.0,
    "current_l3_mean": 40.0,
    "power_mean": 25000.0,
    "power_p95": 25200.0,
    "power_factor_mean": 0.90,
    "voltage_avg_mean": 410.0,
    "voltage_imbalance": 0.002,
    "phase_imbalance": 0.002,
    "frequency_mean": 50.0,
    "telemetry_coverage": 1.0,
    "sample_count": 60,
}


def _noised(base: float, rel_sigma: float = 0.005) -> float:
    return round(base * (1.0 + random.gauss(0, rel_sigma)), 6)


async def _get_connection():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(url, pool_pre_ping=True)
    return engine


async def run() -> None:
    if os.getenv("LOCAL_BACKFILL_ENABLED", "").lower() != "true":
        print("LOCAL_BACKFILL_ENABLED is not 'true' — skipping backfill.")
        return

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine = await _get_connection()
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        r = await session.execute(text("""
            SELECT COUNT(*) AS cnt,
                   MIN(window_start) AS earliest,
                   MAX(window_end)   AS latest
            FROM machine_health_feature_windows
            WHERE device_id = :did AND running_state = 'STEADY_RUNNING'
        """), {"did": _SEED_DEVICE_ID})
        row = r.fetchone()

        if row and row.earliest and row.latest:
            span_days = (row.latest - row.earliest).total_seconds() / 86400.0
            if span_days >= 7:
                print(f"Backfill not needed: {row.cnt} windows already span {span_days:.1f} days.")
                await engine.dispose()
                return

        now = datetime.now(timezone.utc)
        end = now.replace(second=0, microsecond=0)
        start = end - timedelta(days=_BACKFILL_DAYS)

        windows: list[tuple] = []
        cursor = start
        energy_acc = 0.0
        while cursor < end:
            window_end = cursor + timedelta(minutes=_WINDOW_MINUTES)
            energy_acc += 25000.0 / 3600.0 / 1000.0 * _WINDOW_MINUTES / 60.0

            windows.append((
                _SEED_TENANT_ID,
                _SEED_DEVICE_ID,
                cursor,
                window_end,
                _WINDOW_MINUTES,
                _STEADY_RUNNING,
                _noised(_BASE["current_avg_mean"], 0.003),
                _noised(_BASE["current_avg_std"], 0.05),
                _noised(_BASE["current_avg_p95"], 0.003),
                _noised(_BASE["current_l1_mean"], 0.003),
                _noised(_BASE["current_l2_mean"], 0.003),
                _noised(_BASE["current_l3_mean"], 0.003),
                _noised(_BASE["power_mean"], 0.003),
                _noised(_BASE["power_p95"], 0.003),
                _noised(_BASE["power_factor_mean"], 0.002),
                _noised(_BASE["voltage_avg_mean"], 0.002),
                abs(_noised(_BASE["voltage_imbalance"], 0.2)),
                abs(_noised(_BASE["phase_imbalance"], 0.2)),
                _noised(_BASE["frequency_mean"], 0.001),
                round(energy_acc, 3),
                _BASE["telemetry_coverage"],
                _BASE["sample_count"],
                None,
            ))
            cursor = window_end

        insert_sql = text("""
            INSERT IGNORE INTO machine_health_feature_windows
                (tenant_id, device_id, window_start, window_end, window_minutes,
                 running_state, current_avg_mean, current_avg_std, current_avg_p95,
                 current_l1_mean, current_l2_mean, current_l3_mean,
                 power_mean, power_p95, power_factor_mean,
                 voltage_avg_mean, voltage_imbalance, phase_imbalance,
                 frequency_mean, energy_kwh, telemetry_coverage,
                 sample_count, excluded_reason)
            VALUES
                (:t, :d, :ws, :we, :wm, :rs, :cam, :cas, :cap,
                 :cl1, :cl2, :cl3, :pm, :pp, :pfm,
                 :vam, :vi, :pi, :fm, :ek, :tc, :sc, :er)
        """)

        param_keys = [
            "t", "d", "ws", "we", "wm", "rs", "cam", "cas", "cap",
            "cl1", "cl2", "cl3", "pm", "pp", "pfm",
            "vam", "vi", "pi", "fm", "ek", "tc", "sc", "er",
        ]

        batch_size = 200
        inserted = 0
        for i in range(0, len(windows), batch_size):
            batch = windows[i : i + batch_size]
            params = [dict(zip(param_keys, w)) for w in batch]
            result = await session.execute(insert_sql, params)
            inserted += result.rowcount
            await session.commit()

        print(f"Backfilled {inserted}/{len(windows)} feature windows "
              f"for {_SEED_DEVICE_ID} spanning {_BACKFILL_DAYS} days "
              f"({start.isoformat()} → {end.isoformat()}).")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
