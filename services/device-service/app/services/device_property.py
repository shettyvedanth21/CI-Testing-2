"""Device property service - handles dynamic property discovery from telemetry."""

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set

from sqlalchemy import delete, false, select, tuple_
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import (
    DeviceProperty,
    Device,
    DeviceDashboardWidget,
    DeviceDashboardWidgetSetting,
)
import logging

logger = logging.getLogger(__name__)


class DevicePropertyService:
    """Service for managing device properties discovered from telemetry."""
    
    EXCLUDED_FIELDS = {'timestamp', 'device_id', 'tenant_id', 'schema_version', 'enrichment_status', 'table', 'enriched_at'}
    
    def __init__(self, session: AsyncSession):
        self._session = session

    def _discover_field_types(self, telemetry_data: dict) -> dict[str, tuple[bool, str]]:
        discovered_fields: dict[str, tuple[bool, str]] = {}
        for key, value in telemetry_data.items():
            if key in self.EXCLUDED_FIELDS or isinstance(value, bool):
                continue

            if isinstance(value, (int, float)):
                data_type = "float" if isinstance(value, float) else "integer"
                discovered_fields[key] = (True, data_type)
            elif isinstance(value, str):
                discovered_fields[key] = (False, "string")

        return discovered_fields

    async def _sync_discovered_fields(
        self,
        *,
        tenant_id: str,
        discovered_by_device: dict[str, dict[str, tuple[bool, str]]],
        commit: bool,
    ) -> Dict[str, List[DeviceProperty]]:
        all_pairs = [
            (device_id, property_name)
            for device_id, discovered_fields in discovered_by_device.items()
            for property_name in discovered_fields
        ]
        if not all_pairs:
            return {}

        now = datetime.utcnow()
        dialect_name = self._session.bind.dialect.name if self._session.bind is not None else ""

        if dialect_name == "mysql":
            mappings = []
            for device_id, discovered_fields in discovered_by_device.items():
                for field_name, (is_numeric, data_type) in discovered_fields.items():
                    mappings.append(
                        {
                            "device_id": device_id,
                            "tenant_id": tenant_id,
                            "property_name": field_name,
                            "data_type": data_type,
                            "is_numeric": is_numeric,
                            "discovered_at": now,
                            "last_seen_at": now,
                        }
                    )

            if mappings:
                stmt = mysql_insert(DeviceProperty).values(mappings)
                stmt = stmt.on_duplicate_key_update(
                    tenant_id=stmt.inserted.tenant_id,
                    data_type=stmt.inserted.data_type,
                    is_numeric=stmt.inserted.is_numeric,
                    last_seen_at=stmt.inserted.last_seen_at,
                )
                await self._session.execute(stmt)
        else:
            result = await self._session.execute(
                select(DeviceProperty).where(
                    DeviceProperty.tenant_id == tenant_id,
                    tuple_(DeviceProperty.device_id, DeviceProperty.property_name).in_(all_pairs),
                )
            )
            existing_rows = {
                (str(row.device_id), str(row.property_name)): row
                for row in result.scalars().all()
            }

            for device_id, discovered_fields in discovered_by_device.items():
                for field_name, (is_numeric, data_type) in discovered_fields.items():
                    existing = existing_rows.get((device_id, field_name))
                    if existing is not None:
                        existing.last_seen_at = now
                        existing.is_numeric = is_numeric
                        existing.data_type = data_type
                        continue

                    self._session.add(
                        DeviceProperty(
                            device_id=device_id,
                            tenant_id=tenant_id,
                            property_name=field_name,
                            is_numeric=is_numeric,
                            data_type=data_type,
                            discovered_at=now,
                            last_seen_at=now,
                        )
                    )

        await self._session.flush()
        if commit:
            await self._session.commit()

        result = await self._session.execute(
            select(DeviceProperty).where(
                DeviceProperty.tenant_id == tenant_id,
                tuple_(DeviceProperty.device_id, DeviceProperty.property_name).in_(all_pairs),
            )
        )

        updated_by_device: dict[str, list[DeviceProperty]] = defaultdict(list)
        for row in result.scalars().all():
            updated_by_device[str(row.device_id)].append(row)

        for rows in updated_by_device.values():
            rows.sort(key=lambda prop: prop.property_name)
        return dict(updated_by_device)
    
    async def discover_properties(
        self, 
        device_id: str, 
        telemetry_data: dict,
        tenant_id: str,
    ) -> List[DeviceProperty]:
        """Discover and update properties from telemetry data.
        
        Args:
            device_id: Device ID
            telemetry_data: Dictionary containing telemetry fields
            
        Returns:
            List of updated/created DeviceProperty instances
        """
        discovered_fields = self._discover_field_types(telemetry_data)
        if not discovered_fields:
            return []
        synced = await self._sync_discovered_fields(
            tenant_id=tenant_id,
            discovered_by_device={device_id: discovered_fields},
            commit=True,
        )
        return synced.get(device_id, [])
    
    async def get_device_properties(
        self, 
        device_id: str,
        numeric_only: bool = True,
        tenant_id: Optional[str] = None,
    ) -> List[DeviceProperty]:
        """Get all properties for a device.
        
        Args:
            device_id: Device ID
            numeric_only: Only return numeric properties (for rules)
            
        Returns:
            List of DeviceProperty instances
        """
        query = select(DeviceProperty).where(
            DeviceProperty.device_id == device_id
        )
        if tenant_id is not None:
            query = query.where(DeviceProperty.tenant_id == tenant_id)
        
        if numeric_only:
            query = query.where(DeviceProperty.is_numeric == True)
        
        query = query.order_by(DeviceProperty.property_name)
        
        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    async def get_all_devices_properties(
        self,
        tenant_id: Optional[str] = None,
        accessible_plant_ids: Optional[List[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, List[str]]:
        """Get properties for all active devices.
        
        Returns:
            Dictionary mapping device_id to list of property names
        """
        # Get all devices regardless of status (runtime status is for display, not filtering)
        device_query = select(Device.device_id)
        if tenant_id:
            device_query = device_query.where(Device.tenant_id == tenant_id)
        if accessible_plant_ids is not None:
            if accessible_plant_ids:
                device_query = device_query.where(Device.plant_id.in_(accessible_plant_ids))
            else:
                device_query = device_query.where(false())

        device_query = (
            device_query
            .order_by(Device.device_id.asc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        
        device_result = await self._session.execute(device_query)
        device_ids = [row[0] for row in device_result.fetchall()]
        
        result_dict: Dict[str, List[str]] = {}
        
        for dev_id in device_ids:
            props = await self.get_device_properties(dev_id, numeric_only=True, tenant_id=tenant_id)
            result_dict[dev_id] = [p.property_name for p in props]
        
        return result_dict
    
    async def get_common_properties(
        self,
        device_ids: List[str],
        tenant_id: Optional[str] = None,
    ) -> List[str]:
        """Get common properties across multiple devices (intersection).
        
        Args:
            device_ids: List of device IDs
            
        Returns:
            List of property names common to all devices
        """
        if not device_ids:
            return []
        
        if len(device_ids) == 1:
            props = await self.get_device_properties(device_ids[0], numeric_only=True, tenant_id=tenant_id)
            return [p.property_name for p in props]
        
        property_sets: List[Set[str]] = []
        
        for device_id in device_ids:
            props = await self.get_device_properties(device_id, numeric_only=True, tenant_id=tenant_id)
            property_sets.append(set(p.property_name for p in props))
        
        common = property_sets[0]
        for prop_set in property_sets[1:]:
            common = common.intersection(prop_set)
        
        return sorted(list(common))
    
    async def sync_from_telemetry(
        self,
        device_id: str,
        telemetry_values: Dict[str, float],
        tenant_id: str,
    ) -> List[DeviceProperty]:
        """Sync properties from incoming telemetry values.
        
        Args:
            device_id: Device ID
            telemetry_values: Dictionary of parameter values
            
        Returns:
            List of updated/created properties
        """
        return await self.discover_properties(device_id, telemetry_values, tenant_id)

    async def sync_from_telemetry_batch(
        self,
        *,
        tenant_id: str,
        telemetry_by_device: Dict[str, Dict[str, object]],
    ) -> Dict[str, List[DeviceProperty]]:
        """Batch-sync numeric properties discovered from telemetry.

        This path is safe to coalesce by device because property discovery is
        additive and last-seen based; it does not carry ordered event semantics.
        """

        discovered_by_device: dict[str, dict[str, tuple[bool, str]]] = {}
        for device_id, telemetry_data in telemetry_by_device.items():
            discovered_fields = self._discover_field_types(telemetry_data)
            if not discovered_fields:
                continue
            discovered_by_device[device_id] = discovered_fields
        return await self._sync_discovered_fields(
            tenant_id=tenant_id,
            discovered_by_device=discovered_by_device,
            commit=False,
        )
    
    async def cleanup_stale_properties(self, days: int = 30) -> int:
        """Remove properties not seen in specified days.
        
        Args:
            days: Number of days to consider stale
            
        Returns:
            Number of properties deleted
        """
        from datetime import timedelta
        
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        query = delete(DeviceProperty).where(
            DeviceProperty.last_seen_at < cutoff
        )
        
        result = await self._session.execute(query)
        await self._session.commit()
        
        return result.rowcount

    async def get_dashboard_widget_config(self, device_id: str, tenant_id: str) -> Dict[str, object]:
        """Return widget configuration for a device.

        If no explicit selection exists, defaults to all discovered numeric fields.
        """
        device_query = select(Device).where(Device.device_id == device_id, Device.tenant_id == tenant_id)
        device_result = await self._session.execute(device_query)
        if device_result.scalar_one_or_none() is None:
            raise ValueError(f"Device '{device_id}' not found")

        properties = await self.get_device_properties(device_id=device_id, numeric_only=True, tenant_id=tenant_id)
        available_fields = sorted([p.property_name for p in properties])

        widget_query = (
            select(DeviceDashboardWidget)
            .where(DeviceDashboardWidget.device_id == device_id, DeviceDashboardWidget.tenant_id == tenant_id)
            .order_by(DeviceDashboardWidget.display_order.asc(), DeviceDashboardWidget.field_name.asc())
        )
        widget_result = await self._session.execute(widget_query)
        selected_fields = [w.field_name for w in widget_result.scalars().all()]

        settings_query = select(DeviceDashboardWidgetSetting).where(
            DeviceDashboardWidgetSetting.device_id == device_id,
            DeviceDashboardWidgetSetting.tenant_id == tenant_id,
        )
        settings_result = await self._session.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        # Backward compatibility: if legacy selected rows exist without settings row,
        # treat them as explicit config to avoid silently reverting to default mode.
        has_explicit_config = bool((settings and settings.is_configured) or selected_fields)
        default_applied = not has_explicit_config
        effective_fields = selected_fields if has_explicit_config else available_fields

        return {
            "device_id": device_id,
            "available_fields": available_fields,
            "selected_fields": selected_fields,
            "effective_fields": effective_fields,
            "default_applied": default_applied,
        }

    async def replace_dashboard_widget_config(self, device_id: str, tenant_id: str, selected_fields: List[str]) -> Dict[str, object]:
        """Replace widget configuration for a device in an idempotent way."""
        device_query = select(Device).where(Device.device_id == device_id, Device.tenant_id == tenant_id)
        device_result = await self._session.execute(device_query)
        if device_result.scalar_one_or_none() is None:
            raise ValueError(f"Device '{device_id}' not found")

        properties = await self.get_device_properties(device_id=device_id, numeric_only=True, tenant_id=tenant_id)
        available_fields = sorted([p.property_name for p in properties])
        available_set = set(available_fields)

        requested = [f.strip() for f in selected_fields if f and f.strip()]
        deduped: List[str] = []
        seen = set()
        for field in requested:
            if field not in seen:
                seen.add(field)
                deduped.append(field)

        invalid_fields = sorted([field for field in deduped if field not in available_set])
        if invalid_fields:
            raise LookupError(f"Unknown/unavailable widget fields: {invalid_fields}")

        await self._session.execute(
            delete(DeviceDashboardWidget).where(
                DeviceDashboardWidget.device_id == device_id,
                DeviceDashboardWidget.tenant_id == tenant_id,
            )
        )
        for order, field_name in enumerate(deduped):
            self._session.add(
                DeviceDashboardWidget(
                    device_id=device_id,
                    tenant_id=tenant_id,
                    field_name=field_name,
                    display_order=order,
                )
            )

        settings_query = select(DeviceDashboardWidgetSetting).where(
            DeviceDashboardWidgetSetting.device_id == device_id,
            DeviceDashboardWidgetSetting.tenant_id == tenant_id,
        )
        settings_result = await self._session.execute(settings_query)
        settings = settings_result.scalar_one_or_none()
        if settings is None:
            self._session.add(
                DeviceDashboardWidgetSetting(
                    device_id=device_id,
                    tenant_id=tenant_id,
                    is_configured=True,
                )
            )
        else:
            settings.is_configured = True

        await self._session.commit()
        return await self.get_dashboard_widget_config(device_id, tenant_id)
