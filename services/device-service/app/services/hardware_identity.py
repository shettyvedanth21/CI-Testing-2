"""Helpers for persistent hardware unit identity allocation."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import HardwareUnit, HardwareUnitSequence
from app.services.device_errors import HardwareUnitIdAllocationError

_HARDWARE_UNIT_PREFIX = "HWU"
_SEQUENCE_WIDTH = 8
_MAX_SEQUENCE_VALUE = 99_999_999
_ALLOCATION_ATTEMPTS = 20


def format_hardware_unit_id(sequence_value: int) -> str:
    if sequence_value < 1 or sequence_value > _MAX_SEQUENCE_VALUE:
        raise HardwareUnitIdAllocationError(
            f"Sequence value {sequence_value} is outside the supported hardware unit ID range"
        )
    return f"{_HARDWARE_UNIT_PREFIX}{sequence_value:0{_SEQUENCE_WIDTH}d}"


def extract_hardware_unit_sequence(hardware_unit_id: str | None) -> int | None:
    normalized = str(hardware_unit_id or "").strip()
    if not normalized.startswith(_HARDWARE_UNIT_PREFIX):
        return None
    suffix = normalized[len(_HARDWARE_UNIT_PREFIX):]
    if len(suffix) != _SEQUENCE_WIDTH or not suffix.isdigit():
        return None
    return int(suffix)


async def ensure_hardware_unit_allocator_state(session: AsyncSession) -> bool:
    """Ensure the hardware unit allocator row exists and is advanced enough."""

    hardware_unit_ids = (await session.execute(select(HardwareUnit.hardware_unit_id))).scalars().all()
    max_existing_sequence = 0
    for raw_hardware_unit_id in hardware_unit_ids:
        sequence_value = extract_hardware_unit_sequence(raw_hardware_unit_id)
        if sequence_value is None:
            continue
        max_existing_sequence = max(max_existing_sequence, sequence_value)

    desired_next_value = max_existing_sequence + 1 if max_existing_sequence else 1
    allocator = await session.scalar(
        select(HardwareUnitSequence).where(HardwareUnitSequence.prefix == _HARDWARE_UNIT_PREFIX).limit(1)
    )
    allocator_updated = False
    if allocator is None:
        session.add(HardwareUnitSequence(prefix=_HARDWARE_UNIT_PREFIX, next_value=desired_next_value))
        allocator_updated = True
    elif int(allocator.next_value) < desired_next_value:
        allocator.next_value = desired_next_value
        allocator_updated = True

    if allocator_updated:
        await session.commit()
    return allocator_updated


class HardwareUnitIdAllocator:
    """Allocates platform-wide hardware unit IDs from a persistent sequence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def allocate(self) -> str:
        for _ in range(_ALLOCATION_ATTEMPTS):
            current_value = await self._load_current_value()
            if current_value is None:
                raise HardwareUnitIdAllocationError(
                    "Hardware unit ID sequence is not configured. Run the device-service migration before creating hardware units."
                )
            if current_value > _MAX_SEQUENCE_VALUE:
                raise HardwareUnitIdAllocationError("Hardware unit ID sequence is exhausted")

            result = await self._session.execute(
                update(HardwareUnitSequence)
                .where(
                    HardwareUnitSequence.prefix == _HARDWARE_UNIT_PREFIX,
                    HardwareUnitSequence.next_value == current_value,
                )
                .values(next_value=current_value + 1)
            )
            if int(result.rowcount or 0) == 1:
                return format_hardware_unit_id(current_value)

        raise HardwareUnitIdAllocationError("Unable to allocate a unique hardware unit ID")

    async def _load_current_value(self) -> int | None:
        result = await self._session.execute(
            select(HardwareUnitSequence.next_value).where(
                HardwareUnitSequence.prefix == _HARDWARE_UNIT_PREFIX
            )
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None
