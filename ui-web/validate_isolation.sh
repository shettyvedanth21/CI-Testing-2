#!/bin/bash
echo ""
echo "======================================"
echo " FactoryOPS Org Isolation Validator"
echo "======================================"
echo ""

PASS=0
FAIL=0

check() {
  local label=$1
  local result=$2
  local expected=$3
  if echo "$result" | grep -q "$expected"; then
    echo "  PASS: $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $label"
    echo "      Expected to find: $expected"
    FAIL=$((FAIL + 1))
  fi
}

check_absent() {
  local label=$1
  local result=$2
  local should_not_contain=$3
  if echo "$result" | grep -q "$should_not_contain"; then
    echo "  FAIL: $label"
    echo "      Should NOT contain: $should_not_contain but it does"
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: $label"
    PASS=$((PASS + 1))
  fi
}

echo "Getting tokens..."

PRIMARY_TENANT_ID="${VALIDATE_PRIMARY_TENANT_ID:-SH00000001}"
SECONDARY_TENANT_ID="${VALIDATE_SECONDARY_TENANT_ID:-SH00000002}"
PRIMARY_TENANT_LABEL="${VALIDATE_PRIMARY_TENANT_LABEL:-Primary}"
SECONDARY_TENANT_LABEL="${VALIDATE_SECONDARY_TENANT_LABEL:-Secondary}"
PRIMARY_SAMPLE_DEVICE="${VALIDATE_PRIMARY_SAMPLE_DEVICE:-PRIMARY-DEVICE-001}"
SECONDARY_SAMPLE_DEVICE="${VALIDATE_SECONDARY_SAMPLE_DEVICE:-SECONDARY-DEVICE-001}"
PRIMARY_PLANT_ID="${VALIDATE_PRIMARY_PLANT_ID:-}"
SECONDARY_PLANT_ID="${VALIDATE_SECONDARY_PLANT_ID:-}"
SUPER_ADMIN_EMAIL="${VALIDATE_SUPER_ADMIN_EMAIL:-${BOOTSTRAP_SUPER_ADMIN_EMAIL:-manash.ray@cittagent.com}}"
SUPER_ADMIN_PASSWORD="${VALIDATE_SUPER_ADMIN_PASSWORD:-${BOOTSTRAP_SUPER_ADMIN_PASSWORD:-Shivex@2706}}"

SA_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${SUPER_ADMIN_EMAIL}\",\"password\":\"${SUPER_ADMIN_PASSWORD}\"}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))" 2>/dev/null)

ABHI_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abhishek@tata.com","password":"Abhishek@123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))" 2>/dev/null)

OBEYA_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"client@obeya.com","password":"Obeya@123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))" 2>/dev/null)

JAGDISH_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"jagdish@tata.com","password":"Jagdish@123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))" 2>/dev/null)

check "Super admin login" "$SA_TOKEN" "eyJ"
check "Abhishek Tata org admin login" "$ABHI_TOKEN" "eyJ"
check "Obeya admin login" "$OBEYA_TOKEN" "eyJ"
check "Jagdish plant manager login" "$JAGDISH_TOKEN" "eyJ"

echo ""
echo "-- Backend API checks --"
echo ""

SNAPSHOT=$(curl -s \
  "http://localhost:8000/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=10" \
  -H "Authorization: Bearer $SA_TOKEN")

check "fleet-snapshot returns plant_id field" "$SNAPSHOT" "plant_id"
check "fleet-snapshot has TATA-PA-001" "$SNAPSHOT" "TATA-PA-001"

TATA_ONLY=$(curl -s \
  "http://localhost:8000/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=10" \
  -H "Authorization: Bearer $SA_TOKEN" \
  -H "X-Target-Tenant-Id: ${PRIMARY_TENANT_ID}")

check "${PRIMARY_TENANT_LABEL} tenant filter returns ${PRIMARY_TENANT_LABEL} devices" "$TATA_ONLY" "${PRIMARY_SAMPLE_DEVICE}"

OBEYA_ONLY=$(curl -s \
  "http://localhost:8000/api/v1/devices/dashboard/fleet-snapshot?page=1&page_size=10" \
  -H "Authorization: Bearer $SA_TOKEN" \
  -H "X-Target-Tenant-Id: ${SECONDARY_TENANT_ID}")

check_absent "${SECONDARY_TENANT_LABEL} tenant filter excludes ${PRIMARY_TENANT_LABEL} devices" "$OBEYA_ONLY" "${PRIMARY_SAMPLE_DEVICE}"

if [ -n "$PRIMARY_PLANT_ID" ] && [ -n "$SECONDARY_PLANT_ID" ]; then
  PLANT_A=$(curl -s \
    "http://localhost:8000/api/v1/devices?plant_id=${PRIMARY_PLANT_ID}" \
    -H "Authorization: Bearer $SA_TOKEN")

  check "Primary plant filter returns primary sample device" "$PLANT_A" "${PRIMARY_SAMPLE_DEVICE}"
  check_absent "Primary plant filter excludes secondary sample device" "$PLANT_A" "${SECONDARY_SAMPLE_DEVICE}"

  PLANT_B=$(curl -s \
    "http://localhost:8000/api/v1/devices?plant_id=${SECONDARY_PLANT_ID}" \
    -H "Authorization: Bearer $SA_TOKEN")

  check "Secondary plant filter returns secondary sample device" "$PLANT_B" "${SECONDARY_SAMPLE_DEVICE}"
  check_absent "Secondary plant filter excludes primary sample device" "$PLANT_B" "${PRIMARY_SAMPLE_DEVICE}"
else
  echo "  BLOCKED: Plant-scoped checks require VALIDATE_PRIMARY_PLANT_ID and VALIDATE_SECONDARY_PLANT_ID"
fi

echo ""
echo "-- Auth checks --"
echo ""

ABHI_ME=$(curl -s http://localhost:8090/api/v1/auth/me \
  -H "Authorization: Bearer $ABHI_TOKEN")

check "Abhishek is org_admin" "$ABHI_ME" "org_admin"
check "Primary org admin belongs to primary SH tenant" "$ABHI_ME" "${PRIMARY_TENANT_ID}"

OBEYA_ME=$(curl -s http://localhost:8090/api/v1/auth/me \
  -H "Authorization: Bearer $OBEYA_TOKEN")

check "Obeya admin is org_admin" "$OBEYA_ME" "org_admin"
check "Secondary org admin belongs to secondary SH tenant" "$OBEYA_ME" "${SECONDARY_TENANT_ID}"
check_absent "Secondary org admin does NOT belong to primary SH tenant" "$OBEYA_ME" "${PRIMARY_TENANT_ID}"

JAGDISH_ME=$(curl -s http://localhost:8090/api/v1/auth/me \
  -H "Authorization: Bearer $JAGDISH_TOKEN")

check "Jagdish is plant_manager" "$JAGDISH_ME" "plant_manager"
check "Jagdish has plant_ids assigned" "$JAGDISH_ME" "plant_ids"

echo ""
echo "-- TypeScript check --"
echo ""

BUILD=$(cd ui-web && npx tsc --noEmit 2>&1)
if [ -z "$BUILD" ]; then
  echo "  PASS: TypeScript zero errors"
  PASS=$((PASS + 1))
else
  echo "  FAIL: TypeScript errors"
  echo "$BUILD"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "======================================"
echo " Results: $PASS passed, $FAIL failed"
echo "======================================"
echo ""

if [ $FAIL -eq 0 ]; then
  echo " ALL CHECKS PASSED."
else
  echo " $FAIL checks failed. Fix before browser testing."
fi
echo ""
