#!/usr/bin/env python3
"""Reviewed remediation for legacy hardware inventory data."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine


SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
for path in (SERVICE_ROOT, REPO_ROOT / "services", REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.services.hardware_legacy_remediation import (
    apply_reviewed_hardware_unit_fixes,
    build_hardware_unit_review_plan,
    build_installation_review_plan,
    fetch_invalid_hardware_units,
    fetch_invalid_installations,
    group_rows_by_value,
)


def _sync_database_url(raw_url: str) -> str:
    if raw_url.startswith("mysql+aiomysql://"):
        return raw_url.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
    return raw_url


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    if not counts:
        print("  none")
        return
    for value, count in counts.items():
        print(f"  {value}: {count}")


def _print_plan(title: str, rows: list[tuple[str, int, str, str | None, str, bool]]) -> None:
    print(title)
    if not rows:
        print("  none")
        return
    for table_name, row_id, current_value, proposed_value, reason, ambiguous in rows:
        proposal = proposed_value or "BLOCKED"
        status = "AMBIGUOUS" if ambiguous else "REVIEWED"
        print(
            f"  [{status}] {table_name}.id={row_id} current={current_value!r} "
            f"proposed={proposal!r} reason={reason}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply reviewed non-ambiguous remediations.",
    )
    args = parser.parse_args()

    raw_database_url = os.getenv("DATABASE_URL")
    if not raw_database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(_sync_database_url(raw_database_url), future=True)

    with engine.begin() as connection:
        invalid_hardware_units = fetch_invalid_hardware_units(connection)
        invalid_installations = fetch_invalid_installations(connection)

        hardware_plan = build_hardware_unit_review_plan(invalid_hardware_units)
        installation_plan = build_installation_review_plan(invalid_installations)
        combined_plan = hardware_plan + installation_plan

        _print_counts("Invalid hardware unit_type values:", group_rows_by_value(invalid_hardware_units))
        _print_counts("Invalid installation_role values:", group_rows_by_value(invalid_installations))
        _print_plan(
            "Reviewed remediation plan:",
            [
                (
                    item.table_name,
                    item.row_id,
                    item.current_value,
                    item.proposed_value,
                    item.reason,
                    item.ambiguous,
                )
                for item in combined_plan
            ],
        )

        if args.apply:
            applied = apply_reviewed_hardware_unit_fixes(connection, plan=hardware_plan)
            print(f"Applied reviewed fixes: {applied}")

        remaining_hardware_units = fetch_invalid_hardware_units(connection)
        remaining_installations = fetch_invalid_installations(connection)
        print(f"Remaining invalid hardware_units rows: {len(remaining_hardware_units)}")
        print(f"Remaining invalid device_hardware_installations rows: {len(remaining_installations)}")

    if remaining_hardware_units or remaining_installations:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
