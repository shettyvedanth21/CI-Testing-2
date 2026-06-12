#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd jq
need_cmd python3

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

curl -sS "${BASE_URL}/api/v1/devices?page=1&page_size=500" > "${tmp_dir}/devices.json"

python3 - <<'PY' "${tmp_dir}" "${BASE_URL}"
import json
import sys
import urllib.request

tmp_dir = sys.argv[1]
base = sys.argv[2].rstrip("/")

with open(f"{tmp_dir}/devices.json", "r", encoding="utf-8") as f:
    payload = json.load(f)

devices = payload.get("data", []) if isinstance(payload, dict) else []

def to_minutes(t: str) -> int:
    hh, mm, *_ = t.split(":")
    return int(hh) * 60 + int(mm)

def expand_segments(start: str, end: str, day):
    s = to_minutes(start)
    e = to_minutes(end)
    if s == e:
        return []
    days = list(range(7)) if day is None else [day]
    out = []
    for d in days:
        if e > s:
            out.append((d, s, e))
        else:
            out.append((d, s, 24 * 60))
            out.append(((d + 1) % 7, 0, e))
    return out

def overlaps(a, b):
    if a[0] != b[0]:
        return False
    return a[1] < b[2] and b[1] < a[2]

conflicts = []
for dev in devices:
    device_id = dev.get("device_id")
    if not device_id:
        continue
    with urllib.request.urlopen(f"{base}/api/v1/devices/{device_id}/shifts") as r:
        shifts_payload = json.loads(r.read().decode("utf-8"))
    shifts = shifts_payload.get("data", [])
    for i in range(len(shifts)):
        s1 = shifts[i]
        seg1 = expand_segments(s1["shift_start"], s1["shift_end"], s1.get("day_of_week"))
        for j in range(i + 1, len(shifts)):
            s2 = shifts[j]
            # Skip exact duplicates; those are handled by migration.
            if (
                s1.get("day_of_week") == s2.get("day_of_week")
                and s1.get("shift_start") == s2.get("shift_start")
                and s1.get("shift_end") == s2.get("shift_end")
            ):
                continue
            seg2 = expand_segments(s2["shift_start"], s2["shift_end"], s2.get("day_of_week"))
            if any(overlaps(a, b) for a in seg1 for b in seg2):
                conflicts.append(
                    {
                        "device_id": device_id,
                        "shift_a": {"id": s1["id"], "name": s1["shift_name"], "start": s1["shift_start"], "end": s1["shift_end"], "day": s1.get("day_of_week")},
                        "shift_b": {"id": s2["id"], "name": s2["shift_name"], "start": s2["shift_start"], "end": s2["shift_end"], "day": s2.get("day_of_week")},
                    }
                )

if not conflicts:
    print("No non-exact overlapping shift conflicts found.")
    sys.exit(0)

print("Found non-exact overlapping shift conflicts:")
for c in conflicts:
    a = c["shift_a"]
    b = c["shift_b"]
    print(
        f"- {c['device_id']}: "
        f"{a['name']}#{a['id']} ({a['start']}-{a['end']}, day={a['day']}) "
        f"overlaps "
        f"{b['name']}#{b['id']} ({b['start']}-{b['end']}, day={b['day']})"
    )
sys.exit(1)
PY
