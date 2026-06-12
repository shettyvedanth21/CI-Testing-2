from __future__ import annotations

import argparse
import asyncio

from app.database import AsyncSessionLocal
from app.services.reconciliation_apply import ReconciliationApplyService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approve, reject, or apply reconciliation candidates.")
    sub = parser.add_subparsers(dest="command", required=True)

    approve = sub.add_parser("approve")
    approve.add_argument("--audit-id", type=int, required=True)
    approve.add_argument("--actor", required=True)

    reject = sub.add_parser("reject")
    reject.add_argument("--audit-id", type=int, required=True)
    reject.add_argument("--actor", required=True)
    reject.add_argument("--reason", required=True)

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--audit-id", type=int, required=True)
    apply_cmd.add_argument("--actor", required=True)
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    async with AsyncSessionLocal() as session:
        service = ReconciliationApplyService(session)
        if args.command == "approve":
            result = await service.approve_candidate(args.audit_id, actor=args.actor)
        elif args.command == "reject":
            result = await service.reject_candidate(args.audit_id, actor=args.actor, reason=args.reason)
        else:
            result = await service.apply_candidate(args.audit_id, actor=args.actor)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
