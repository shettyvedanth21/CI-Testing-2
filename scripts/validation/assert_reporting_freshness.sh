#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/validation/assert_reporting_freshness.sh --tenant <TENANT_ID> [--cutoff <ISO8601_UTC>]

If --cutoff is omitted, this script reads:
  artifacts/validation-cutoffs/<TENANT_ID>.cutoff
EOF
}

TENANT_ID=""
CUTOFF_UTC=""

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

if [[ -z "$CUTOFF_UTC" ]]; then
  MARKER="artifacts/validation-cutoffs/${TENANT_ID}.cutoff"
  if [[ ! -f "$MARKER" ]]; then
    echo "No cutoff marker found at $MARKER; provide --cutoff explicitly." >&2
    exit 1
  fi
  CUTOFF_UTC="$(awk -F= '$1=="cutoff_utc"{print $2}' "$MARKER")"
fi

if [[ -z "$CUTOFF_UTC" ]]; then
  echo "Resolved empty cutoff timestamp." >&2
  exit 1
fi

MYSQL_CONTAINER="${MYSQL_CONTAINER:-energy_mysql}"
MYSQL_DB="${MYSQL_DB:-ai_factoryops}"
MYSQL_USER="${MYSQL_USER:-energy}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-energy}"

echo "[freshness] tenant=$TENANT_ID cutoff_utc=$CUTOFF_UTC"

OLDER_COUNT="$(
  docker exec "$MYSQL_CONTAINER" mysql -N -B -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -D "$MYSQL_DB" -e "
SELECT COUNT(*)
FROM energy_reports
WHERE tenant_id = '$TENANT_ID' AND created_at < '$CUTOFF_UTC';
"
)"

if [[ "$OLDER_COUNT" != "0" ]]; then
  echo "[freshness] FAIL: found $OLDER_COUNT pre-cutoff report rows for tenant $TENANT_ID" >&2
  docker exec "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -D "$MYSQL_DB" -e "
SELECT report_id,status,created_at,s3_key
FROM energy_reports
WHERE tenant_id = '$TENANT_ID' AND created_at < '$CUTOFF_UTC'
ORDER BY created_at DESC
LIMIT 20;
"
  exit 2
fi

echo "[freshness] PASS: no pre-cutoff report rows for tenant $TENANT_ID"
docker exec "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -D "$MYSQL_DB" -e "
SELECT COUNT(*) AS post_cutoff_reports
FROM energy_reports
WHERE tenant_id = '$TENANT_ID' AND created_at >= '$CUTOFF_UTC';
SELECT report_id,status,created_at,s3_key
FROM energy_reports
WHERE tenant_id = '$TENANT_ID' AND created_at >= '$CUTOFF_UTC'
ORDER BY created_at DESC
LIMIT 20;
"
