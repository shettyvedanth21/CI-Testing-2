#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/validation/reset_reporting_validation_scope.sh --tenant <TENANT_ID> --yes [--cutoff <ISO8601_UTC>]

Purpose:
  Hard reset reporting validation scope so pre-cutoff artifacts cannot be confused with fresh validation output.
  This deletes tenant-scoped report artifacts from MinIO and tenant-scoped reporting rows from MySQL.

Examples:
  scripts/validation/reset_reporting_validation_scope.sh --tenant SH00000001 --yes
  scripts/validation/reset_reporting_validation_scope.sh --tenant SH00000001 --cutoff 2026-04-17T00:00:00Z --yes
EOF
}

TENANT_ID=""
CUTOFF_UTC=""
CONFIRM_NO=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant)
      TENANT_ID="${2:-}"
      shift 2
      ;;
    --cutoff)
      CUTOFF_UTC="${2:-}"
      shift 2
      ;;
    --yes)
      CONFIRM_NO=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$TENANT_ID" ]]; then
  echo "--tenant is required" >&2
  usage
  exit 1
fi

if [[ $CONFIRM_NO -ne 0 ]]; then
  echo "Refusing destructive validation reset without --yes" >&2
  exit 1
fi

if [[ -z "$CUTOFF_UTC" ]]; then
  CUTOFF_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
fi

MYSQL_CONTAINER="${MYSQL_CONTAINER:-energy_mysql}"
MYSQL_DB="${MYSQL_DB:-ai_factoryops}"
MYSQL_USER="${MYSQL_USER:-energy}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-energy}"
MINIO_CONTAINER="${MINIO_CONTAINER:-minio}"
MINIO_BUCKET="${MINIO_BUCKET:-energy-platform-datasets}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minio}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minio123}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
MC_IMAGE="${MC_IMAGE:-minio/mc:latest}"

NETWORK_NAME="$(
  docker inspect "$MINIO_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{printf "%s\n" $k}}{{end}}' | head -n 1
)"

if [[ -z "$NETWORK_NAME" ]]; then
  echo "Could not resolve MinIO docker network from container '$MINIO_CONTAINER'" >&2
  exit 1
fi

echo "[reset] tenant=$TENANT_ID cutoff_utc=$CUTOFF_UTC network=$NETWORK_NAME"

echo "[reset] pre-delete DB counts"
docker exec "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -D "$MYSQL_DB" -e "
SELECT '$TENANT_ID' AS tenant_id,
       COUNT(*) AS energy_reports,
       SUM(status='completed') AS completed_reports
FROM energy_reports
WHERE tenant_id = '$TENANT_ID';
SELECT '$TENANT_ID' AS tenant_id,
       COUNT(*) AS scheduled_reports
FROM scheduled_reports
WHERE tenant_id = '$TENANT_ID';
"

echo "[reset] pre-delete MinIO object count under reports/$TENANT_ID"
docker run --rm --network "$NETWORK_NAME" --entrypoint /bin/sh "$MC_IMAGE" -lc "
  mc alias set local '$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null &&
  (mc find 'local/$MINIO_BUCKET/reports/$TENANT_ID' | wc -l || true)
"

echo "[reset] deleting MinIO prefix reports/$TENANT_ID"
docker run --rm --network "$NETWORK_NAME" --entrypoint /bin/sh "$MC_IMAGE" -lc "
  mc alias set local '$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null &&
  mc rm --recursive --force 'local/$MINIO_BUCKET/reports/$TENANT_ID' || true
"

echo "[reset] deleting tenant-scoped reporting rows"
docker exec "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -D "$MYSQL_DB" -e "
DELETE FROM energy_reports WHERE tenant_id = '$TENANT_ID';
DELETE FROM scheduled_reports WHERE tenant_id = '$TENANT_ID';
"

echo "[reset] post-delete DB counts"
docker exec "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -D "$MYSQL_DB" -e "
SELECT '$TENANT_ID' AS tenant_id,
       COUNT(*) AS energy_reports_after_reset
FROM energy_reports
WHERE tenant_id = '$TENANT_ID';
SELECT '$TENANT_ID' AS tenant_id,
       COUNT(*) AS scheduled_reports_after_reset
FROM scheduled_reports
WHERE tenant_id = '$TENANT_ID';
"

echo "[reset] post-delete MinIO object count under reports/$TENANT_ID"
docker run --rm --network "$NETWORK_NAME" --entrypoint /bin/sh "$MC_IMAGE" -lc "
  mc alias set local '$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null &&
  (mc find 'local/$MINIO_BUCKET/reports/$TENANT_ID' | wc -l || true)
"

mkdir -p artifacts/validation-cutoffs
CUTOFF_MARKER="artifacts/validation-cutoffs/${TENANT_ID}.cutoff"
{
  echo "tenant_id=$TENANT_ID"
  echo "cutoff_utc=$CUTOFF_UTC"
  echo "policy=ignore_any_report_or_pdf_created_before_cutoff"
  echo "reset_scope=minio_prefix:reports/$TENANT_ID + mysql:energy_reports,scheduled_reports"
} > "$CUTOFF_MARKER"

echo "[reset] wrote cutoff marker: $CUTOFF_MARKER"
cat "$CUTOFF_MARKER"
