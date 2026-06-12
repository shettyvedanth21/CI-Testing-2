from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.database import AsyncSessionLocal
from app.services.reconciliation_preview import ReconciliationPreviewRequest, ReconciliationPreviewService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview suspicious historical energy periods without applying fixes.")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--device-id", action="append", dest="device_ids", default=[])
    parser.add_argument("--requested-by", default="ops-preview")
    parser.add_argument("--affected-window-start", default=None)
    parser.add_argument("--affected-window-end", default=None)
    parser.add_argument("--min-drift-kwh", type=float, default=0.25)
    parser.add_argument("--min-drift-ratio", type=float, default=0.25)
    parser.add_argument("--skip-report-intersections", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    async with AsyncSessionLocal() as session:
        service = ReconciliationPreviewService(session)
        result = await service.preview(
            ReconciliationPreviewRequest(
                start_date=date.fromisoformat(args.start_date),
                end_date=date.fromisoformat(args.end_date),
                tenant_id=args.tenant_id,
                device_ids=args.device_ids,
                requested_by=args.requested_by,
                affected_window_start=date.fromisoformat(args.affected_window_start)
                if args.affected_window_start
                else None,
                affected_window_end=date.fromisoformat(args.affected_window_end)
                if args.affected_window_end
                else None,
                min_drift_kwh=args.min_drift_kwh,
                min_drift_ratio=args.min_drift_ratio,
                include_report_intersections=not args.skip_report_intersections,
            )
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
