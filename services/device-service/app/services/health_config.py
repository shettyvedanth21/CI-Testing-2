"""Health configuration service layer - business logic for parameter health management and scoring."""

from dataclasses import dataclass
from collections import defaultdict
from typing import Optional, List, Dict, Any, Mapping
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.device import ParameterHealthConfig, Device
from app.schemas.device import ParameterHealthConfigCreate, ParameterHealthConfigUpdate
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedTelemetryValue:
    telemetry_key: Optional[str]
    value: Optional[float]
    resolution: str


class DuplicateHealthConfigError(ValueError):
    """Raised when a device already has a config for the same canonical parameter."""


class HealthConfigService:
    """Service layer for parameter health configuration and score calculation."""
    
    VALID_MACHINE_STATES = ["RUNNING", "OFF", "IDLE", "UNLOAD", "POWER CUT"]
    _SCORABLE_MACHINE_STATES = frozenset({"RUNNING", "IDLE", "UNLOAD"})
    _STANDBY_MACHINE_STATES = frozenset({"OFF", "POWER CUT"})
    _CANONICAL_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
        "current": ("current_a", "phase_current"),
        "power": ("active_power", "active_power_kw", "business_power_w", "power_kw", "kw"),
        "power_factor": ("pf", "cos_phi", "powerfactor", "pf_business", "raw_power_factor"),
        "voltage": ("voltage_v",),
    }
    _ALIASES_TO_CANONICAL: dict[str, str] = {
        alias.casefold(): canonical
        for canonical, aliases in _CANONICAL_PARAMETER_ALIASES.items()
        for alias in aliases
    }
    _MISSING_STATUS = "Missing Telemetry"
    _IGNORED_ZERO_STATUS = "Ignored Zero"
    
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _list_configs_for_canonical_parameter(
        self,
        *,
        device_id: str,
        tenant_id: Optional[str],
        parameter_name: str,
        exclude_id: Optional[int] = None,
    ) -> list[ParameterHealthConfig]:
        canonical_parameter_name = self._canonical_parameter_name(parameter_name)
        query = select(ParameterHealthConfig).where(
            ParameterHealthConfig.device_id == device_id,
            ParameterHealthConfig.canonical_parameter_name == canonical_parameter_name,
        )
        if tenant_id is not None:
            query = query.where(ParameterHealthConfig.tenant_id == tenant_id)
        if exclude_id is not None:
            query = query.where(ParameterHealthConfig.id != exclude_id)
        query = query.order_by(
            ParameterHealthConfig.updated_at.desc(),
            ParameterHealthConfig.id.desc(),
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def _assert_no_canonical_duplicate(
        self,
        *,
        device_id: str,
        tenant_id: Optional[str],
        parameter_name: str,
        exclude_id: Optional[int] = None,
    ) -> None:
        matches = await self._list_configs_for_canonical_parameter(
            device_id=device_id,
            tenant_id=tenant_id,
            parameter_name=parameter_name,
            exclude_id=exclude_id,
        )
        if not matches:
            return
        canonical_parameter_name = self._canonical_parameter_name(parameter_name)
        raise DuplicateHealthConfigError(
            f"Device '{device_id}' already has a health configuration for canonical parameter "
            f"'{canonical_parameter_name}'"
        )
    
    async def create_health_config(
        self, 
        config_data: ParameterHealthConfigCreate
    ) -> ParameterHealthConfig:
        """Create a new parameter health configuration.
        
        Args:
            config_data: Health configuration data
            
        Returns:
            Created ParameterHealthConfig instance
        """
        device_query = select(Device).where(Device.device_id == config_data.device_id)
        if config_data.tenant_id:
            device_query = device_query.where(Device.tenant_id == config_data.tenant_id)

        result = await self._session.execute(device_query)
        device = result.scalar_one_or_none()
        
        if not device:
            raise ValueError(f"Device '{config_data.device_id}' not found")
        
        config = ParameterHealthConfig(
            device_id=config_data.device_id,
            tenant_id=config_data.tenant_id,
            parameter_name=config_data.parameter_name,
            canonical_parameter_name=self._canonical_parameter_name(config_data.parameter_name),
            normal_min=config_data.normal_min,
            normal_max=config_data.normal_max,
            weight=config_data.weight,
            ignore_zero_value=config_data.ignore_zero_value,
            is_active=config_data.is_active,
        )

        await self._assert_no_canonical_duplicate(
            device_id=config_data.device_id,
            tenant_id=config_data.tenant_id,
            parameter_name=config_data.parameter_name,
        )
        
        self._session.add(config)
        
        try:
            await self._session.commit()
            await self._session.refresh(config)
            logger.info(
                "Health config created",
                extra={
                    "config_id": config.id,
                    "device_id": config.device_id,
                    "parameter": config.parameter_name,
                }
            )
        except IntegrityError as e:
            await self._session.rollback()
            logger.error("Failed to create health config", extra={"error": str(e)})
            raise DuplicateHealthConfigError(
                f"Device '{config_data.device_id}' already has a health configuration for "
                f"'{self._canonical_parameter_name(config_data.parameter_name)}'"
            ) from e
        
        return config
    
    async def get_health_configs_by_device(
        self,
        device_id: str,
        tenant_id: Optional[str] = None
    ) -> List[ParameterHealthConfig]:
        """Get all health configurations for a device.
        
        Args:
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            List of ParameterHealthConfig instances
        """
        query = select(ParameterHealthConfig).where(
            ParameterHealthConfig.device_id == device_id
        )
        
        if tenant_id:
            query = query.where(ParameterHealthConfig.tenant_id == tenant_id)
        
        query = query.order_by(
            ParameterHealthConfig.canonical_parameter_name,
            ParameterHealthConfig.parameter_name,
            ParameterHealthConfig.id,
        )
        
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_active_health_configs_by_devices(
        self,
        device_ids: list[str],
        tenant_id: Optional[str] = None,
    ) -> dict[str, list[ParameterHealthConfig]]:
        if not device_ids:
            return {}

        query = select(ParameterHealthConfig).where(
            ParameterHealthConfig.device_id.in_(device_ids),
            ParameterHealthConfig.is_active.is_(True),
        )
        if tenant_id:
            query = query.where(ParameterHealthConfig.tenant_id == tenant_id)
        query = query.order_by(
            ParameterHealthConfig.device_id,
            ParameterHealthConfig.canonical_parameter_name,
            ParameterHealthConfig.parameter_name,
            ParameterHealthConfig.id,
        )

        result = await self._session.execute(query)
        grouped: dict[str, list[ParameterHealthConfig]] = defaultdict(list)
        for config in result.scalars().all():
            grouped[str(config.device_id)].append(config)
        return dict(grouped)
    
    async def get_health_config(
        self,
        config_id: int,
        device_id: str,
        tenant_id: Optional[str] = None
    ) -> Optional[ParameterHealthConfig]:
        """Get a specific health configuration by ID.
        
        Args:
            config_id: Configuration ID
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            ParameterHealthConfig instance or None
        """
        query = select(ParameterHealthConfig).where(
            ParameterHealthConfig.id == config_id,
            ParameterHealthConfig.device_id == device_id
        )
        
        if tenant_id:
            query = query.where(ParameterHealthConfig.tenant_id == tenant_id)
        
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
    
    async def update_health_config(
        self,
        config_id: int,
        device_id: str,
        tenant_id: Optional[str],
        config_data: ParameterHealthConfigUpdate
    ) -> Optional[ParameterHealthConfig]:
        """Update an existing health configuration.
        
        Args:
            config_id: Configuration ID
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            config_data: Update data
            
        Returns:
            Updated ParameterHealthConfig instance or None
        """
        config = await self.get_health_config(config_id, device_id, tenant_id)
        
        if not config:
            return None
        
        update_data = config_data.model_dump(exclude_unset=True)
        next_parameter_name = update_data.get("parameter_name", config.parameter_name)

        await self._assert_no_canonical_duplicate(
            device_id=device_id,
            tenant_id=tenant_id,
            parameter_name=next_parameter_name,
            exclude_id=config.id,
        )
        
        for field, value in update_data.items():
            setattr(config, field, value)
        config.canonical_parameter_name = self._canonical_parameter_name(config.parameter_name)
        
        try:
            await self._session.commit()
            await self._session.refresh(config)
            logger.info(
                "Health config updated",
                extra={"config_id": config.id}
            )
        except IntegrityError as e:
            await self._session.rollback()
            logger.error("Failed to update health config", extra={"error": str(e)})
            raise DuplicateHealthConfigError(
                f"Device '{device_id}' already has a health configuration for "
                f"'{self._canonical_parameter_name(next_parameter_name)}'"
            ) from e
        
        return config
    
    async def delete_health_config(
        self,
        config_id: int,
        device_id: str,
        tenant_id: Optional[str]
    ) -> bool:
        """Delete a health configuration.
        
        Args:
            config_id: Configuration ID
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            True if deleted, False if not found
        """
        config = await self.get_health_config(config_id, device_id, tenant_id)
        
        if not config:
            return False
        
        await self._session.delete(config)
        await self._session.commit()
        
        logger.info(
            "Health config deleted",
            extra={"config_id": config_id}
        )
        
        return True
    
    async def validate_weights(
        self,
        device_id: str,
        tenant_id: Optional[str] = None
    ) -> dict:
        """Validate that weights sum to 100%.
        
        Args:
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            Dictionary with validation results
        """
        configs = await self.get_health_configs_by_device(device_id, tenant_id)
        active_configs = [c for c in configs if c.is_active]
        
        total_weight = sum(c.weight for c in active_configs)
        
        parameters = [
            {
                "parameter_name": c.parameter_name,
                "weight": c.weight,
                "is_active": c.is_active
            }
            for c in configs
        ]
        
        is_valid = abs(total_weight - 100.0) < 0.01
        
        return {
            "is_valid": is_valid,
            "total_weight": round(total_weight, 2),
            "message": "Weights sum to 100%" if is_valid else f"Weights sum to {total_weight}%, must equal 100%",
            "parameters": parameters
        }
    
    async def bulk_create_or_update(
        self,
        device_id: str,
        tenant_id: Optional[str],
        configs: List[dict]
    ) -> List[ParameterHealthConfig]:
        """Bulk create or update health configurations.
        
        Args:
            device_id: Device ID
            tenant_id: Optional tenant ID
            configs: List of configuration dictionaries
            
        Returns:
            List of created/updated configurations
        """
        result = []
        seen_canonical_parameters: set[str] = set()

        existing_query = select(ParameterHealthConfig).where(
            ParameterHealthConfig.device_id == device_id
        )
        if tenant_id is not None:
            existing_query = existing_query.where(ParameterHealthConfig.tenant_id == tenant_id)
        existing_result = await self._session.execute(existing_query)
        existing_configs = list(existing_result.scalars().all())
        existing_by_canonical: dict[str, list[ParameterHealthConfig]] = {}
        for existing in existing_configs:
            existing_by_canonical.setdefault(existing.canonical_parameter_name, []).append(existing)
        
        for config_dict in configs:
            param_name = config_dict.get("parameter_name")
            canonical_parameter_name = self._canonical_parameter_name(str(param_name or ""))
            if not canonical_parameter_name:
                raise ValueError("parameter_name is required for bulk health config updates")
            if canonical_parameter_name in seen_canonical_parameters:
                raise DuplicateHealthConfigError(
                    f"Bulk payload contains multiple configs for canonical parameter "
                    f"'{canonical_parameter_name}'"
                )
            seen_canonical_parameters.add(canonical_parameter_name)

            existing_matches = existing_by_canonical.get(canonical_parameter_name, [])
            if len(existing_matches) > 1:
                raise DuplicateHealthConfigError(
                    f"Device '{device_id}' has multiple existing configs for canonical parameter "
                    f"'{canonical_parameter_name}'. Repair duplicates before bulk update."
                )
            existing = existing_matches[0] if existing_matches else None

            if existing:
                for key, value in config_dict.items():
                    if value is not None and hasattr(existing, key):
                        setattr(existing, key, value)
                existing.canonical_parameter_name = canonical_parameter_name
                result.append(existing)
            else:
                create_dict = {
                    key: value
                    for key, value in config_dict.items()
                    if key in {
                        "parameter_name",
                        "normal_min",
                        "normal_max",
                        "weight",
                        "ignore_zero_value",
                        "is_active",
                    }
                }
                new_config = ParameterHealthConfig(
                    device_id=device_id,
                    tenant_id=tenant_id,
                    canonical_parameter_name=canonical_parameter_name,
                    **create_dict
                )
                self._session.add(new_config)
                result.append(new_config)
                existing_by_canonical[canonical_parameter_name] = [new_config]
        try:
            await self._session.commit()
        except IntegrityError as e:
            await self._session.rollback()
            logger.error("Failed to bulk create/update health config", extra={"error": str(e)})
            raise DuplicateHealthConfigError(
                f"Device '{device_id}' already has a health configuration for one of the submitted parameters"
            ) from e
        
        for config in result:
            await self._session.refresh(config)
        
        return result

    @staticmethod
    def extract_numeric_telemetry_values(telemetry_values: Mapping[str, Any]) -> dict[str, float]:
        """Return numeric telemetry fields without narrowing the contract."""
        return {
            str(key): float(value)
            for key, value in telemetry_values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }

    @classmethod
    def _normalize_parameter_key(cls, value: Any) -> str:
        return str(value).strip().casefold()

    @classmethod
    def _canonical_parameter_name(cls, parameter_name: str) -> str:
        normalized = cls._normalize_parameter_key(parameter_name)
        return cls._ALIASES_TO_CANONICAL.get(normalized, normalized)

    @classmethod
    def _candidate_parameter_keys(cls, parameter_name: str) -> tuple[str, ...]:
        normalized = cls._normalize_parameter_key(parameter_name)
        canonical = cls._canonical_parameter_name(parameter_name)
        aliases = cls._CANONICAL_PARAMETER_ALIASES.get(canonical, ())

        ordered = [normalized, canonical, *aliases]
        seen: set[str] = set()
        candidates: list[str] = []
        for key in ordered:
            candidate = cls._normalize_parameter_key(key)
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
        return tuple(candidates)

    @classmethod
    def _build_telemetry_index(cls, telemetry_values: Mapping[str, Any]) -> dict[str, tuple[str, float]]:
        index: dict[str, tuple[str, float]] = {}
        for key, value in telemetry_values.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            normalized_key = cls._normalize_parameter_key(key)
            if not normalized_key or normalized_key in index:
                continue
            index[normalized_key] = (str(key), float(value))
        return index

    @classmethod
    def resolve_parameter_value_from_index(
        cls,
        parameter_name: str,
        telemetry_index: Mapping[str, tuple[str, float]],
    ) -> ResolvedTelemetryValue:
        """Resolve a configured parameter name to a telemetry value."""
        for candidate_key in cls._candidate_parameter_keys(parameter_name):
            resolved = telemetry_index.get(candidate_key)
            if resolved is not None:
                telemetry_key, value = resolved
                resolution = "exact" if cls._normalize_parameter_key(parameter_name) == candidate_key else "alias"
                return ResolvedTelemetryValue(
                    telemetry_key=telemetry_key,
                    value=value,
                    resolution=resolution,
                )

        return ResolvedTelemetryValue(telemetry_key=None, value=None, resolution="missing")

    @classmethod
    def resolve_parameter_value(
        cls,
        parameter_name: str,
        telemetry_values: Mapping[str, Any],
    ) -> ResolvedTelemetryValue:
        """Resolve a configured parameter name to a telemetry value."""
        telemetry_index = cls._build_telemetry_index(telemetry_values)
        return cls.resolve_parameter_value_from_index(parameter_name, telemetry_index)

    @classmethod
    def normalize_machine_state(cls, machine_state: Optional[str]) -> str:
        normalized = str(machine_state or "RUNNING").strip().upper().replace("_", " ")
        aliases = {
            "UNLOADED": "UNLOAD",
            "POWERCUT": "POWER CUT",
        }
        return aliases.get(normalized, normalized)
    
    def _calculate_raw_score(
        self,
        value: float,
        normal_min: Optional[float],
        normal_max: Optional[float],
    ) -> float:
        """Calculate locked raw health score for a parameter value."""
        if normal_min is None or normal_max is None:
            return 100.0

        if normal_min <= value <= normal_max:
            return 100.0

        lower_tolerance = normal_min - (normal_min * 0.15)
        upper_tolerance = normal_max + (normal_max * 0.15)
        within_tolerance = (lower_tolerance <= value < normal_min) or (normal_max < value <= upper_tolerance)
        if within_tolerance:
            return 50.0

        return 0.0
    
    def _get_status_and_color(self, score: float) -> tuple[str, str]:
        """Get status label and color based on score.
        
        Args:
            score: Raw score (0-100)
            
        Returns:
            Tuple of (status, color)
        """
        if score >= 100:
            return "Healthy", "🟢"
        elif score >= 50:
            return "Warning", "🟠"
        else:
            return "Critical", "🔴"
    
    def _get_health_status_and_color(self, score: float) -> tuple[str, str]:
        """Get overall health status label and color based on score.
        
        Args:
            score: Health score (0-100)
            
        Returns:
            Tuple of (status, color)
        """
        if score >= 90:
            return "Excellent", "🟢"
        elif score >= 75:
            return "Good", "🟡"
        elif score >= 50:
            return "At Risk", "🟠"
        else:
            return "Critical", "🔴"

    async def calculate_health_score(
        self,
        device_id: str,
        telemetry_values: Dict[str, float],
        machine_state: str = "RUNNING",
        tenant_id: Optional[str] = None
    ) -> dict:
        """Calculate device health score based on telemetry values.
        
        Args:
            device_id: Device ID
            telemetry_values: Dictionary of parameter names to values
            machine_state: Current machine operational state
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            Dictionary with health score calculation results
        """
        machine_state = self.normalize_machine_state(machine_state)
        
        if machine_state not in self._SCORABLE_MACHINE_STATES:
            standby_message = (
                f"Machine is {machine_state}. Health scoring disabled for this state."
                if machine_state not in self._STANDBY_MACHINE_STATES
                else f"Machine is {machine_state}. Health scoring not calculated in this state."
            )
            return {
                "device_id": device_id,
                "health_score": None,
                "status": "Standby",
                "status_color": "⚪",
                "message": standby_message,
                "machine_state": machine_state,
                "parameter_scores": [],
                "total_weight_configured": 0.0,
                "parameters_included": 0,
                "parameters_skipped": 0
            }
        
        configs = await self.get_health_configs_by_device(device_id, tenant_id)
        active_configs = [c for c in configs if c.is_active]

        return self.calculate_health_score_from_configs(
            device_id=device_id,
            telemetry_values=telemetry_values,
            machine_state=machine_state,
            active_configs=active_configs,
        )

    def calculate_health_score_from_configs(
        self,
        *,
        device_id: str,
        telemetry_values: Dict[str, float],
        machine_state: str = "RUNNING",
        active_configs: list[ParameterHealthConfig],
    ) -> dict:
        machine_state = self.normalize_machine_state(machine_state)

        if machine_state not in self._SCORABLE_MACHINE_STATES:
            standby_message = (
                f"Machine is {machine_state}. Health scoring disabled for this state."
                if machine_state not in self._STANDBY_MACHINE_STATES
                else f"Machine is {machine_state}. Health scoring not calculated in this state."
            )
            return {
                "device_id": device_id,
                "health_score": None,
                "status": "Standby",
                "status_color": "⚪",
                "message": standby_message,
                "machine_state": machine_state,
                "parameter_scores": [],
                "total_weight_configured": 0.0,
                "parameters_included": 0,
                "parameters_skipped": 0,
            }

        if not active_configs:
            return {
                "device_id": device_id,
                "health_score": None,
                "status": "Not Configured",
                "status_color": "⚪",
                "message": "No health parameters configured. Please configure parameter ranges and weights.",
                "machine_state": machine_state,
                "parameter_scores": [],
                "total_weight_configured": 0.0,
                "parameters_included": 0,
                "parameters_skipped": 0
            }

        total_weight_configured = round(sum(config.weight for config in active_configs), 2)

        if abs(total_weight_configured - 100.0) >= 0.01:
            return {
                "device_id": device_id,
                "health_score": None,
                "status": "Invalid Configuration",
                "status_color": "⚪",
                "message": f"Weight validation failed: Weights sum to {total_weight_configured}%, must equal 100%",
                "machine_state": machine_state,
                "parameter_scores": [],
                "total_weight_configured": total_weight_configured,
                "parameters_included": 0,
                "parameters_skipped": 0
            }
        
        parameter_scores = []
        total_weighted_score = 0.0
        parameters_included = 0
        parameters_skipped = 0
        telemetry_index = self._build_telemetry_index(telemetry_values)
        
        for config in active_configs:
            param_name = config.parameter_name
            resolved = self.resolve_parameter_value_from_index(param_name, telemetry_index)
            value = resolved.value

            if value is None:
                parameters_skipped += 1
                parameter_scores.append({
                    "parameter_name": param_name,
                    "telemetry_key": resolved.telemetry_key,
                    "value": None,
                    "raw_score": None,
                    "weighted_score": 0.0,
                    "weight": config.weight,
                    "status": self._MISSING_STATUS,
                    "status_color": "⚪",
                    "resolution": resolved.resolution,
                    "included_in_score": False,
                })
                continue
            
            if value == 0 and config.ignore_zero_value:
                parameters_skipped += 1
                parameter_scores.append({
                    "parameter_name": param_name,
                    "telemetry_key": resolved.telemetry_key,
                    "value": value,
                    "raw_score": None,
                    "weighted_score": 0.0,
                    "weight": config.weight,
                    "status": self._IGNORED_ZERO_STATUS,
                    "status_color": "⚪",
                    "resolution": resolved.resolution,
                    "included_in_score": False,
                })
                continue
            
            raw_score = self._calculate_raw_score(
                value,
                config.normal_min,
                config.normal_max,
            )
            
            weighted_score = raw_score * (config.weight / 100.0)
            
            status, status_color = self._get_status_and_color(raw_score)
            
            parameter_scores.append({
                "parameter_name": param_name,
                "telemetry_key": resolved.telemetry_key,
                "value": value,
                "raw_score": round(raw_score, 2),
                "weighted_score": round(weighted_score, 2),
                "weight": config.weight,
                "status": status,
                "status_color": status_color,
                "resolution": resolved.resolution,
                "included_in_score": True,
            })
            
            total_weighted_score += weighted_score
            parameters_included += 1
        
        if parameters_included == 0:
            return {
                "device_id": device_id,
                "health_score": None,
                "status": "No Data",
                "status_color": "⚪",
                "message": "No matching telemetry parameters found for configured health metrics.",
                "machine_state": machine_state,
                "parameter_scores": parameter_scores,
                "total_weight_configured": total_weight_configured,
                "parameters_included": 0,
                "parameters_skipped": parameters_skipped
            }
        
        health_score = round(total_weighted_score, 2)
        health_status, health_color = self._get_health_status_and_color(health_score)
        
        return {
            "device_id": device_id,
            "health_score": health_score,
            "status": health_status,
            "status_color": health_color,
            "message": f"Health score calculated from {parameters_included} parameter(s)",
            "machine_state": machine_state,
            "parameter_scores": parameter_scores,
            "total_weight_configured": total_weight_configured,
            "parameters_included": parameters_included,
            "parameters_skipped": parameters_skipped
        }
