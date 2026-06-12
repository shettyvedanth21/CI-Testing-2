# Notification Usage Billing Ledger

## Source Of Truth
`notification_delivery_logs` is the only billing-proof source for notification usage.
Monthly summary, detailed logs, and CSV exports must be derived only from this ledger.

## Delivery Status Semantics
- `queued`: reserved lifecycle state (not billed).
- `attempted`: outbound send attempt was executed (not billed).
- `provider_accepted`: provider accepted send (billable).
- `delivered`: provider reported final delivery (billable).
- `failed`: provider/send failure (not billed).
- `skipped`: intentionally not sent (disabled/misconfigured/no-op path, not billed).

Status progression is monotonic and idempotent:
- `attempted -> provider_accepted -> delivered`
- `attempted -> failed`
- `provider_accepted -> failed` or `provider_accepted -> delivered`
- terminal states (`delivered`, `failed`, `skipped`) do not move backward.

## Billable Semantics
- `billable_units = 1` only for `provider_accepted` and `delivered`.
- `billable_units = 0` for `queued`, `attempted`, `failed`, `skipped`.
- `billable_count` is `SUM(billable_units)` from ledger rows.

## Masking Policy
- `recipient_masked` is the only recipient value exposed to super-admin APIs and CSV.
- `recipient_hash` supports reconciliation/dedupe without exposing raw recipient values.
- Raw recipients are write-path input only and are not part of admin read contracts.

## Export Proof Model
- CSV export uses the same SQL-filtered ledger source as detailed logs.
- Export ordering is deterministic: `attempted_at ASC, id ASC`.
- Export responses include:
  - `X-Notification-Usage-Month`
  - `X-Export-Generated-At`

## Retention Policy
- Cleanup is explicit and opt-in.
- `NOTIFICATION_DELIVERY_RETENTION_ENABLED=false` by default (no deletion).
- When enabled, retention keeps at least 12 months (`NOTIFICATION_DELIVERY_RETENTION_MONTHS` is clamped to minimum 12).
