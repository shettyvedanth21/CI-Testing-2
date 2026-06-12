"""Helpers for persistent device identity allocation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, DeviceIdClass, DeviceIdSequence
from app.services.device_errors import DeviceIdAllocationError

_SEQUENCE_WIDTH = 8
_MAX_SEQUENCE_VALUE = 99_999_999
_ALLOCATION_ATTEMPTS = 20


@dataclass(frozen=True)
class DeviceIdClassDefinition:
    value: str
    prefix: str
    label: str


DEVICE_ID_CLASS_DEFINITIONS: tuple[DeviceIdClassDefinition, ...] = (
    DeviceIdClassDefinition(DeviceIdClass.ACTIVE.value, "AD", "Active Device"),
    DeviceIdClassDefinition(DeviceIdClass.TEST.value, "TD", "Test Device"),
    DeviceIdClassDefinition(DeviceIdClass.VIRTUAL.value, "VD", "Virtual Device"),
)
DEVICE_ID_CLASS_PREFIXES = {definition.value: definition.prefix for definition in DEVICE_ID_CLASS_DEFINITIONS}
DEVICE_ID_PREFIX_CLASSES = {definition.prefix: definition.value for definition in DEVICE_ID_CLASS_DEFINITIONS}


def normalize_device_id_class(device_id_class: str) -> str:
    normalized = (device_id_class or "").strip().lower()
    if normalized not in DEVICE_ID_CLASS_PREFIXES:
        valid_values = ", ".join(sorted(DEVICE_ID_CLASS_PREFIXES))
        raise DeviceIdAllocationError(f"Unsupported device_id_class '{device_id_class}'. Expected one of: {valid_values}")
    return normalized


def format_device_id(prefix: str, sequence_value: int) -> str:
    if sequence_value < 1 or sequence_value > _MAX_SEQUENCE_VALUE:
        raise DeviceIdAllocationError(f"Sequence value {sequence_value} is outside the supported device ID range")
    return f"{prefix}{sequence_value:0{_SEQUENCE_WIDTH}d}"


def extract_device_sequence(device_id: str | None, *, prefix: str) -> int | None:
    normalized = str(device_id or "").strip()
    if not normalized.startswith(prefix):
        return None
    suffix = normalized[len(prefix):]
    if len(suffix) != _SEQUENCE_WIDTH or not suffix.isdigit():
        return None
    return int(suffix)


async def ensure_device_allocator_state(session: AsyncSession) -> dict[str, int]:
    """Ensure all generated device ID sequences exist and are advanced enough."""

    device_ids = (await session.execute(select(Device.device_id))).scalars().all()
    max_existing_by_prefix = {
        definition.prefix: 0 for definition in DEVICE_ID_CLASS_DEFINITIONS
    }
    for raw_device_id in device_ids:
        for prefix in max_existing_by_prefix:
            sequence_value = extract_device_sequence(raw_device_id, prefix=prefix)
            if sequence_value is not None:
                max_existing_by_prefix[prefix] = max(max_existing_by_prefix[prefix], sequence_value)
                break

    existing_rows = (
        await session.execute(select(DeviceIdSequence).where(DeviceIdSequence.prefix.in_(max_existing_by_prefix)))
    ).scalars().all()
    existing_by_prefix = {row.prefix: row for row in existing_rows}
    updated_prefixes: dict[str, int] = {}

    for prefix, max_existing_sequence in max_existing_by_prefix.items():
        desired_next_value = max_existing_sequence + 1 if max_existing_sequence else 1
        allocator = existing_by_prefix.get(prefix)
        if allocator is None:
            session.add(DeviceIdSequence(prefix=prefix, next_value=desired_next_value))
            updated_prefixes[prefix] = desired_next_value
            continue
        if int(allocator.next_value) < desired_next_value:
            allocator.next_value = desired_next_value
            updated_prefixes[prefix] = desired_next_value

    if updated_prefixes:
        await session.commit()
    return updated_prefixes


class DeviceIdAllocator:
    """Allocates platform-wide device IDs from persistent per-prefix sequences."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def allocate(self, device_id_class: str) -> str:
        normalized_class = normalize_device_id_class(device_id_class)
        prefix = DEVICE_ID_CLASS_PREFIXES[normalized_class]

        for _ in range(_ALLOCATION_ATTEMPTS):
            current_value = await self._load_current_value(prefix)
            if current_value is None:
                raise DeviceIdAllocationError(
                    f"Device ID sequence is not configured for prefix '{prefix}'. Run the device-service migration/reset before creating devices."
                )
            if current_value > _MAX_SEQUENCE_VALUE:
                raise DeviceIdAllocationError(f"Device ID sequence for prefix '{prefix}' is exhausted")

            result = await self._session.execute(
                update(DeviceIdSequence)
                .where(
                    DeviceIdSequence.prefix == prefix,
                    DeviceIdSequence.next_value == current_value,
                )
                .values(next_value=current_value + 1)
            )
            if int(result.rowcount or 0) == 1:
                return format_device_id(prefix, current_value)

        raise DeviceIdAllocationError(f"Unable to allocate a unique device ID for prefix '{prefix}'")

    async def advance_past_existing(self, device_id: str) -> None:
        """Move a stale sequence past already persisted generated IDs.

        This protects environments where historical rows exist but the
        allocator sequence was reset or restored to an older value.
        """

        prefix = device_id[:2]
        if prefix not in DEVICE_ID_CLASS_PREFIXES.values():
            return

        max_existing_sequence = await self._max_existing_sequence(prefix)
        if max_existing_sequence is None:
            return

        target_next_value = max_existing_sequence + 1
        await self._session.execute(
            update(DeviceIdSequence)
            .where(
                DeviceIdSequence.prefix == prefix,
                DeviceIdSequence.next_value < target_next_value,
            )
            .values(next_value=target_next_value)
        )

    async def _load_current_value(self, prefix: str) -> int | None:
        result = await self._session.execute(
            select(DeviceIdSequence.next_value).where(DeviceIdSequence.prefix == prefix)
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    async def _max_existing_sequence(self, prefix: str) -> int | None:
        result = await self._session.execute(
            select(Device.device_id).where(Device.device_id.like(f"{prefix}%"))
        )
        max_value: int | None = None
        for raw_device_id in result.scalars():
            value = extract_device_sequence(raw_device_id, prefix=prefix)
            if value is None:
                continue
            max_value = value if max_value is None else max(max_value, value)
        return max_value
