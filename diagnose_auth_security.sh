#!/bin/bash
echo ""
echo "=============================================="
echo " FactoryOPS Auth Security Diagnosis"
echo "=============================================="
echo ""

PASS=0
FAIL=0
WARN=0

pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; echo "        $2"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN: $1"; echo "        $2"; WARN=$((WARN+1)); }

# ── Get tokens ──────────────────────────────────────────────────────────────

SA_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@factoryops.local","password":"Admin1234!"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))")

TATA_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abhishek@tata.com","password":"Abhishek@123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))")

OBEYA_TOKEN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"client@obeya.com","password":"Obeya@123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token','FAILED'))")

echo "-- Token tests --"
echo ""

[ "$SA_TOKEN" != "FAILED" ] && pass "Super admin login" || fail "Super admin login" "Cannot get token"
[ "$TATA_TOKEN" != "FAILED" ] && pass "Tata login" || fail "Tata login" "Cannot get token"
[ "$OBEYA_TOKEN" != "FAILED" ] && pass "Obeya login" || fail "Obeya login" "Cannot get token"

# ── Decode JWT claims ────────────────────────────────────────────────────────

echo ""
echo "-- JWT claim verification --"
echo ""

decode_jwt() {
  echo "$1" | cut -d. -f2 | python3 -c "
import sys, base64, json
data = sys.stdin.read().strip()
pad = 4 - len(data) % 4
data += '=' * pad
print(json.dumps(json.loads(base64.b64decode(data))))
" 2>/dev/null
}

SA_CLAIMS=$(decode_jwt "$SA_TOKEN")
TATA_CLAIMS=$(decode_jwt "$TATA_TOKEN")
OBEYA_CLAIMS=$(decode_jwt "$OBEYA_TOKEN")

# Check roles are correct
SA_ROLE=$(echo $SA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('role','missing'))")
TATA_ROLE=$(echo $TATA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('role','missing'))")
OBEYA_ROLE=$(echo $OBEYA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('role','missing'))")

[ "$SA_ROLE" = "super_admin" ] && pass "Super admin JWT role = super_admin" || fail "Super admin role wrong" "Got: $SA_ROLE"
[ "$TATA_ROLE" = "org_admin" ] && pass "Tata JWT role = org_admin" || fail "Tata role wrong" "Got: $TATA_ROLE"
[ "$OBEYA_ROLE" = "org_admin" ] && pass "Obeya JWT role = org_admin" || fail "Obeya role wrong" "Got: $OBEYA_ROLE"

# Check org_id isolation in JWT
TATA_ORG=$(echo $TATA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('org_id','missing'))")
OBEYA_ORG=$(echo $OBEYA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('org_id','missing'))")
SA_ORG=$(echo $SA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('org_id','None'))")

[ "$TATA_ORG" != "$OBEYA_ORG" ] && pass "Tata and Obeya have different org_ids in JWT" || fail "CRITICAL: Tata and Obeya share same org_id" "Both: $TATA_ORG"
[ "$SA_ORG" = "None" ] || warn "Super admin has org_id in JWT" "Expected null, got: $SA_ORG"

# Check token expiry
SA_EXP=$(echo $SA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('exp',0))")
TATA_EXP=$(echo $TATA_CLAIMS | python3 -c "import sys,json; print(json.load(sys.stdin).get('exp',0))")
NOW=$(python3 -c "import time; print(int(time.time()))")
TTL=$((TATA_EXP - NOW))
[ $TTL -gt 0 ] && pass "Tata token not expired (TTL: ${TTL}s)" || fail "Tata token already expired" "TTL: ${TTL}s"

# ── CRITICAL: Cookie sharing security test ───────────────────────────────────

echo ""
echo "-- Cookie isolation security test --"
echo ""

warn "KNOWN ISSUE: Shared httpOnly cookie" \
  "Refresh token cookie is shared across all browser tabs at same origin.
        If users A and B are logged into different orgs in same browser,
        whichever logged in last will steal the other's session on token refresh.
        This is the bug you observed. See REMEDIATION below."

# ── Cross-org data access tests ──────────────────────────────────────────────

echo ""
echo "-- Cross-org data access (no query param) --"
echo ""

TATA_SUMMARY=$(curl -s "http://localhost:8000/api/v1/devices/dashboard/summary" \
  -H "Authorization: Bearer $TATA_TOKEN")
OBEYA_SUMMARY=$(curl -s "http://localhost:8000/api/v1/devices/dashboard/summary" \
  -H "Authorization: Bearer $OBEYA_TOKEN")

TATA_COUNT=$(echo $TATA_SUMMARY | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary',{}).get('total_devices',0))" 2>/dev/null)
OBEYA_COUNT=$(echo $OBEYA_SUMMARY | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary',{}).get('total_devices',0))" 2>/dev/null)

TATA_HAS_OBEYA=$(echo $TATA_SUMMARY | python3 -c "import sys,json; d=json.load(sys.stdin); ids=[x.get('device_id','') for x in d.get('devices',[])]; print('YES' if any('OB-' in i for i in ids) else 'NO')" 2>/dev/null)
OBEYA_HAS_TATA=$(echo $OBEYA_SUMMARY | python3 -c "import sys,json; d=json.load(sys.stdin); ids=[x.get('device_id','') for x in d.get('devices',[])]; print('YES' if any('TATA-' in i for i in ids) else 'NO')" 2>/dev/null)

[ "$TATA_HAS_OBEYA" = "NO" ] && pass "Tata summary has no Obeya devices" || fail "CRITICAL: Tata sees Obeya devices" ""
[ "$OBEYA_HAS_TATA" = "NO" ] && pass "Obeya summary has no Tata devices" || fail "CRITICAL: Obeya sees Tata devices" ""

# ── Refresh token rotation test ───────────────────────────────────────────────

echo ""
echo "-- Refresh token security --"
echo ""

# Login and get refresh token in body
TATA_LOGIN=$(curl -s -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abhishek@tata.com","password":"Abhishek@123"}')

TATA_REFRESH=$(echo $TATA_LOGIN | python3 -c "import sys,json; print(json.load(sys.stdin).get('refresh_token','FAILED'))")

# Use it once
NEW_TOKENS=$(curl -s -X POST http://localhost:8090/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$TATA_REFRESH\"}")

NEW_ACCESS=$(echo $NEW_TOKENS | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token','FAILED'))")

[ "$NEW_ACCESS" != "FAILED" ] && pass "Refresh token issues new access token" || fail "Refresh token failed" ""

# Use old refresh token again — must fail (rotation)
OLD_REUSE=$(curl -s -X POST http://localhost:8090/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$TATA_REFRESH\"}")

OLD_ERROR=$(echo $OLD_REUSE | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail',{}).get('code','NO_ERROR'))" 2>/dev/null)

[ "$OLD_ERROR" != "NO_ERROR" ] && pass "Old refresh token rejected after rotation (reuse blocked)" || fail "SECURITY: Old refresh token still valid after rotation" "Token rotation not working"

# ── Brute force protection check ─────────────────────────────────────────────

echo ""
echo "-- Brute force protection --"
echo ""

FAIL_1=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abhishek@tata.com","password":"wrongpassword1"}')
FAIL_2=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abhishek@tata.com","password":"wrongpassword2"}')
FAIL_3=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8090/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abhishek@tata.com","password":"wrongpassword3"}')

[ "$FAIL_1" = "401" ] && [ "$FAIL_2" = "401" ] && [ "$FAIL_3" = "401" ] && \
  warn "Wrong passwords return 401 but no rate limiting detected" \
  "Brute force protection not implemented. Add rate limiting to /login." || \
  pass "Login returns correct error codes"

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "=============================================="
echo " Results: $PASS passed, $WARN warnings, $FAIL failed"
echo "=============================================="
echo ""

echo "REMEDIATION REQUIRED:"
echo ""
echo "1. CRITICAL — Cookie sharing across tabs:"
echo "   The httpOnly refresh token cookie is shared across all tabs"
echo "   at the same browser origin. Fix: store refresh token in"
echo "   sessionStorage (tab-isolated) instead of httpOnly cookie."
echo "   Trade-off: slightly less secure against XSS but eliminates"
echo "   cross-tab session contamination completely."
echo ""
echo "2. IMPORTANT — Add rate limiting to /login endpoint:"
echo "   No brute force protection detected."
echo "   Fix: add slowapi or similar rate limiter to auth-service."
echo ""
