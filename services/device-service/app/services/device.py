"""Device service layer - business logic."""

from typing import Optional, List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.repositories.device import DeviceRepository
from app.services.device_errors import DeviceAlreadyExistsError, DeviceIdAllocationError, DevicePlantRequiredError
from app.schemas.device import DeviceCreate, DeviceUpdate
from app.services.device_identity import DeviceIdAllocator
from services.shared.tenant_context import TenantContext
import logging

logger = logging.getLogger(__name__)
_DEVICE_CREATE_ATTEMPTS = 5


class DeviceService:
    """Service layer for device management business logic.
    
    This service encapsulates all business rules and operations
    related to device management, providing a clean API for
    the HTTP handlers.
    """
    
    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        self._session = session
        effective_ctx = ctx or TenantContext(
            tenant_id=None,
            user_id="svc:device-service",
            role="super_admin",
            plant_ids=[],
            is_super_admin=True,
        )
        self._repository = DeviceRepository(
            session,
            effective_ctx,
            allow_cross_tenant=ctx is None,
        )
        self._device_id_allocator = DeviceIdAllocator(session)
    
    async def create_device(self, device_data: DeviceCreate, *, commit: bool = True) -> Device:
        """Create a new device.
        
        Note: status field is DEPRECATED and ignored.
        Runtime status is computed automatically based on telemetry activity.
        
        Args:
            device_data: Device creation data
            
        Returns:
            Created Device instance
            
        Raises:
            DeviceIdAllocationError: If a unique device ID cannot be allocated
        """
        device_data.plant_id = self._normalize_required_plant_id(device_data.plant_id)
        if device_data.device_id:
            return await self._create_device_with_id(device_data, device_data.device_id, generated=False, commit=commit)

        for _attempt in range(_DEVICE_CREATE_ATTEMPTS):
            generated_device_id = await self._device_id_allocator.allocate(device_data.device_id_class)
            try:
                return await self._create_device_with_id(device_data, generated_device_id, generated=True, commit=commit)
            except DeviceAlreadyExistsError:
                logger.warning(
                    "Generated device_id conflict detected during create",
                    extra={
                        "device_id": generated_device_id,
                        "device_id_class": device_data.device_id_class,
                    },
                )
                await self._device_id_allocator.advance_past_existing(generated_device_id)
                continue

        logger.error(
            "Device ID allocation failed after exhausting generated ID attempts",
            extra={
                "tenant_id": device_data.tenant_id,
                "device_id_class": device_data.device_id_class,
                "attempts": _DEVICE_CREATE_ATTEMPTS,
            },
        )
        raise DeviceIdAllocationError("Unable to allocate a unique device ID")

    async def _create_device_with_id(
        self,
        device_data: DeviceCreate,
        device_id: str,
        *,
        generated: bool,
        commit: bool,
    ) -> Device:
        device = Device(
            device_id=device_id,
            tenant_id=device_data.tenant_id,
            plant_id=device_data.plant_id,
            device_name=device_data.device_name,
            device_type=device_data.device_type,
            device_id_class=device_data.device_id_class,
            manufacturer=device_data.manufacturer,
            model=device_data.model,
            location=device_data.location,
            phase_type=device_data.phase_type,
            data_source_type=device_data.data_source_type,
            energy_flow_mode=device_data.energy_flow_mode,
            polarity_mode=device_data.polarity_mode,
            legacy_status="active",
            metadata_json=device_data.metadata_json,
        )

        try:
            created_device = await self._repository.create(device)
            if commit:
                await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            if generated:
                raise DeviceAlreadyExistsError(f"Generated device ID '{device_id}' already exists")
            raise DeviceAlreadyExistsError(f"Device ID '{device_id}' already exists")

        logger.info(
            "Device created successfully",
            extra={
                "device_id": created_device.device_id,
                "device_type": created_device.device_type,
                "device_id_class": created_device.device_id_class,
                "id_source": "generated" if generated else "manual",
            }
        )
        return created_device
    
    async def get_device(
        self, 
        device_id: str, 
        tenant_id: str
    ) -> Optional[Device]:
        """Get device by ID.
        
        Args:
            device_id: Device identifier
            tenant_id: Optional tenant ID for multi-tenancy
            
        Returns:
            Device instance or None if not found
        """
        return await self._repository.get_by_id(device_id)
    
    async def list_devices(
        self,
        tenant_id: str,
        plant_id: Optional[str] = None,
        accessible_plant_ids: Optional[List[str]] = None,
        device_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Device], int]:
        """List devices with filtering and pagination.
        
        Args:
            tenant_id: Optional tenant filter
            device_type: Optional device type filter
            status: Optional status filter
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            Tuple of (devices list, total count)
        """
        return await self._repository.list_devices(
            plant_id=plant_id,
            accessible_plant_ids=accessible_plant_ids,
            device_type=device_type,
            status=status,
            page=page,
            page_size=page_size,
        )
    
    async def update_device(
        self,
        device_id: str,
        device_data: DeviceUpdate,
        tenant_id: str,
    ) -> Optional[Device]:
        """Update an existing device.
        
        Args:
            device_id: Device identifier
            device_data: Update data
            tenant_id: Optional tenant ID for multi-tenancy
            
        Returns:
            Updated Device instance or None if not found
        """
        # Fetch existing device
        device = await self._repository.get_by_id(device_id)
        if not device:
            logger.warning(
                "Attempted to update non-existent device",
                extra={"device_id": device_id}
            )
            return None
        
        # Update only provided fields
        update_data = device_data.model_dump(exclude_unset=True)
        if "plant_id" in update_data:
            update_data["plant_id"] = self._normalize_required_plant_id(update_data["plant_id"])
        
        # Handle status field (deprecated) - map to legacy_status
        if "status" in update_data:
            update_data["legacy_status"] = update_data.pop("status")
        
        for field, value in update_data.items():
            setattr(device, field, value)
        
        # Persist changes
        updated_device = await self._repository.update(device)
        await self._session.commit()
        
        logger.info(
            "Device updated successfully",
            extra={"device_id": updated_device.device_id}
        )
        
        return updated_device

    @staticmethod
    def _normalize_required_plant_id(plant_id: str | None) -> str:
        normalized = (plant_id or "").strip()
        if not normalized:
            raise DevicePlantRequiredError("Plant ID is required for every device.")
        return normalized
    
    async def delete_device(
        self,
        device_id: str,
        tenant_id: Optional[str] = None,
        soft: bool = True,
    ) -> bool:
        """Delete a device.
        
        Args:
            device_id: Device identifier
            tenant_id: Optional tenant ID for multi-tenancy
            soft: If True, performs soft delete; otherwise hard delete
            
        Returns:
            True if deleted successfully, False if not found
        """
        device = await self._repository.get_by_id(device_id)
        if not device:
            return False
        
        await self._repository.delete(device, soft=soft)
        await self._session.commit()
        
        logger.info(
            "Device deleted successfully",
            extra={
                "device_id": device_id,
                "soft_delete": soft,
            }
        )
        
        return True
    
    async def update_last_seen(self, device_id: str, tenant_id: str) -> Optional[Device]:
        """Update last_seen_timestamp for a device when telemetry is received.
        
        This is called by the telemetry service when data is received for a device.
        The runtime status will automatically be computed based on this timestamp.
        
        Args:
            device_id: Device identifier
            
        Returns:
            Updated Device instance or None if not found
        """
        device = await self._repository.get_by_id(device_id)
        if not device:
            logger.warning(
                "Attempted to update last_seen for non-existent device",
                extra={"device_id": device_id}
            )
            return None
        
        # Update last_seen_timestamp to now
        device.update_last_seen()
        
        # Persist changes
        updated_device = await self._repository.update(device)
        await self._session.commit()
        
        logger.debug(
            "Device last_seen updated",
            extra={
                "device_id": device_id,
                "last_seen": updated_device.last_seen_timestamp,
                "runtime_status": updated_device.get_runtime_status(),
            }
        )
        
        return updated_device
