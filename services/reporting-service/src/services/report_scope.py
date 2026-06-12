from __future__ import annotations

from typing import Any


def _unique_device_ids(device_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for device_id in device_ids:
        value = str(device_id).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def extract_device_ids_from_report_params(params: dict[str, Any] | None) -> list[str]:
    payload = params or {}

    resolved_device_ids = payload.get("resolved_device_ids")
    if isinstance(resolved_device_ids, list):
        return _unique_device_ids([str(device_id) for device_id in resolved_device_ids])

    device_ids = payload.get("device_ids")
    if isinstance(device_ids, list):
        return _unique_device_ids([str(device_id) for device_id in device_ids])

    raw_device_id = payload.get("device_id")
    if isinstance(raw_device_id, str) and raw_device_id.strip() and raw_device_id.upper() != "ALL":
        return [raw_device_id.strip()]

    machine_ids: list[str] = []
    for key in ("machine_a_id", "machine_b_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and value.lower() != "all":
            machine_ids.append(value.strip())

    return _unique_device_ids(machine_ids)


def extract_device_ids_from_schedule_params(params_template: dict[str, Any] | None) -> list[str]:
    payload = params_template or {}
    device_ids = payload.get("device_ids")
    if isinstance(device_ids, list):
        return _unique_device_ids([str(device_id) for device_id in device_ids])
    return []


def report_visible_to_scope(
    params: dict[str, Any] | None,
    accessible_device_ids: list[str] | None,
) -> bool:
    if accessible_device_ids is None:
        return True

    allowed = set(accessible_device_ids)
    if not allowed:
        return False

    scoped_device_ids = extract_device_ids_from_report_params(params)
    if not scoped_device_ids:
        return False

    return set(scoped_device_ids).issubset(allowed)


def schedule_visible_to_scope(
    params_template: dict[str, Any] | None,
    accessible_device_ids: list[str] | None,
) -> bool:
    if accessible_device_ids is None:
        return True

    allowed = set(accessible_device_ids)
    if not allowed:
        return False

    scoped_device_ids = extract_device_ids_from_schedule_params(params_template)
    if not scoped_device_ids:
        return False

    return set(scoped_device_ids).issubset(allowed)


def normalize_schedule_params_template(
    params_template: dict[str, Any],
    accessible_device_ids: list[str] | None,
) -> dict[str, Any]:
    normalized = dict(params_template or {})
    device_ids = extract_device_ids_from_schedule_params(normalized)
    if not device_ids:
        raise ValueError("params_template.device_ids must include at least one device.")

    if accessible_device_ids is not None:
        allowed = set(accessible_device_ids)
        if not allowed:
            raise PermissionError("No accessible devices are available for scheduling reports.")
        foreign = [device_id for device_id in device_ids if device_id not in allowed]
        if foreign:
            raise PermissionError("Scheduled reports can include only devices from your assigned plants.")

    normalized["device_ids"] = device_ids
    return normalized
