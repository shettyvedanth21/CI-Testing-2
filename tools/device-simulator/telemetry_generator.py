"""Telemetry data generator with realistic patterns and fault injection and dynamic metrics support."""
import enum
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


class MachineState(enum.Enum):
    OFF = "off"
    STARTUP = "startup"
    RUNNING = "running"
    LOAD_CHANGE = "load_change"
    SHUTDOWN = "shutdown"


@dataclass
class TelemetryPoint:
    device_id: str
    timestamp: str
    schema_version: str
    metrics: Dict[str, float]

    def to_dict(self) -> dict:
        result: dict = {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }
        for key, value in self.metrics.items():
            result[key] = value
        return result


class TelemetryGenerator:
    DEFAULT_METRICS = {
        "voltage": {"base": 415.0, "min": 380.0, "max": 440.0, "noise": 2.0, "drift": 5.0},
        "current": {"base": 25.0, "min": 0.0, "max": 80.0, "noise": 0.5, "drift": 1.5},
        "power": {"base": 15000.0, "min": 0.0, "max": 50000.0, "noise": 100.0, "drift": 500.0},
        "temperature": {"base": 65.0, "min": 20.0, "max": 120.0, "noise": 0.5, "drift": 1.0},
    }

    INDUSTRIAL_PHASE_METRICS = {
        "voltage_l1": {"base": 415.0, "min": 380.0, "max": 440.0, "noise": 2.0, "drift": 5.0},
        "voltage_l2": {"base": 415.0, "min": 380.0, "max": 440.0, "noise": 2.0, "drift": 5.0},
        "voltage_l3": {"base": 415.0, "min": 380.0, "max": 440.0, "noise": 2.0, "drift": 5.0},
        "current_l1": {"base": 25.0, "min": 0.0, "max": 80.0, "noise": 0.5, "drift": 1.5},
        "current_l2": {"base": 25.0, "min": 0.0, "max": 80.0, "noise": 0.5, "drift": 1.5},
        "current_l3": {"base": 25.0, "min": 0.0, "max": 80.0, "noise": 0.5, "drift": 1.5},
        "power": {"base": 15000.0, "min": 0.0, "max": 50000.0, "noise": 100.0, "drift": 500.0},
        "power_factor": {"base": 0.92, "min": 0.80, "max": 1.0, "noise": 0.005, "drift": 0.01},
        "frequency": {"base": 50.0, "min": 48.0, "max": 52.0, "noise": 0.05, "drift": 0.1},
        "temperature": {"base": 65.0, "min": 20.0, "max": 120.0, "noise": 0.5, "drift": 1.0},
    }

    _STARTUP_RAMP_TICKS = 12
    _SHUTDOWN_RAMP_TICKS = 8
    _LOAD_CHANGE_TICKS = 10
    _CYCLE_DWELL_OFF = 30
    _CYCLE_DWELL_RUNNING = 120

    _LOAD_CHANGE_POWER_SCALE = (0.6, 1.4)
    _LOAD_CHANGE_CURRENT_SCALE = (0.6, 1.4)

    def __init__(
        self,
        device_id: str,
        fault_mode: str = "none",
        noise_factor: float = 0.02,
        metric_config: Optional[Dict[str, List[float]]] = None,
    ):
        self._device_id = device_id
        self._fault_mode = fault_mode
        self._noise_factor = noise_factor

        if metric_config:
            self._metrics = {}
            for name, range_vals in metric_config.items():
                if len(range_vals) >= 2:
                    base = (range_vals[0] + range_vals[1]) / 2
                    self._metrics[name] = {
                        "base": base,
                        "min": range_vals[0],
                        "max": range_vals[1],
                        "noise": (range_vals[1] - range_vals[0]) * 0.02,
                        "drift": (range_vals[1] - range_vals[0]) * 0.05,
                    }
        else:
            self._metrics = self.DEFAULT_METRICS.copy()

        self._current_values = {name: config["base"] for name, config in self._metrics.items()}

        self._fault_counter = 0
        self._in_fault_state = False
        self._energy_kwh = 0.0

        self._machine_state = MachineState.RUNNING
        self._state_tick = 0
        self._phase_imbalance_target_phase: Optional[str] = None
        self._load_change_scale = 1.0
        self._pf_degradation_active = False

    def generate(self) -> TelemetryPoint:
        self._advance_operating_cycle()

        base_targets = self._compute_base_targets()

        for name, config in self._metrics.items():
            target = base_targets.get(name, config["base"])
            self._current_values[name] = self._update_value(
                self._current_values[name],
                target,
                max_delta=config["drift"],
                noise_scale=config["noise"]
            )

        if self._fault_mode != "none":
            self._current_values = self._apply_fault(self._current_values)

        clamped_values = {}
        for name, config in self._metrics.items():
            value = self._current_values[name]
            value = max(config["min"], min(config["max"], value))
            if name == "power":
                value = round(value, 2)
            elif name in ("power_factor",):
                value = round(value, 4)
            elif name == "frequency":
                value = round(value, 3)
            else:
                value = round(value, 3) if abs(value) < 10 else round(value, 2)
            clamped_values[name] = value

        clamped_values = self._compute_derived_fields(clamped_values)

        return TelemetryPoint(
            device_id=self._device_id,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            schema_version="v1",
            metrics=clamped_values,
        )

    def _compute_base_targets(self) -> Dict[str, float]:
        targets = {}
        ramp_fraction = self._current_ramp_fraction()

        for name, config in self._metrics.items():
            base = config["base"]
            if self._machine_state == MachineState.OFF:
                if name in ("current_l1", "current_l2", "current_l3", "current"):
                    base = 0.0
                elif name == "power":
                    base = 0.0
                elif name == "temperature":
                    base = min(config["base"], 25.0)
                elif name == "power_factor":
                    base = 1.0
            elif self._machine_state == MachineState.STARTUP:
                if name in ("current_l1", "current_l2", "current_l3", "current"):
                    base = config["base"] * 1.5 * ramp_fraction + config["base"] * 0.3 * (1 - ramp_fraction)
                elif name == "power":
                    base = config["base"] * ramp_fraction
                elif name == "power_factor":
                    base = 0.85 + (config["base"] - 0.85) * ramp_fraction
            elif self._machine_state == MachineState.SHUTDOWN:
                if name in ("current_l1", "current_l2", "current_l3", "current"):
                    base = config["base"] * ramp_fraction
                elif name == "power":
                    base = config["base"] * ramp_fraction
                elif name == "power_factor":
                    base = 0.85 + (config["base"] - 0.85) * ramp_fraction
            elif self._machine_state == MachineState.LOAD_CHANGE:
                if name in ("current_l1", "current_l2", "current_l3", "current"):
                    base = config["base"] * self._load_change_scale
                elif name == "power":
                    base = config["base"] * self._load_change_scale
            targets[name] = base

        return targets

    def _current_ramp_fraction(self) -> float:
        if self._machine_state == MachineState.STARTUP:
            return min(1.0, self._state_tick / self._STARTUP_RAMP_TICKS)
        elif self._machine_state == MachineState.SHUTDOWN:
            return max(0.0, 1.0 - self._state_tick / self._SHUTDOWN_RAMP_TICKS)
        elif self._machine_state == MachineState.OFF:
            return 0.0
        elif self._machine_state == MachineState.LOAD_CHANGE:
            return 1.0
        return 1.0

    def _advance_operating_cycle(self) -> None:
        self._state_tick += 1

        use_cycle = self._fault_mode in ("load_cycle",)

        if self._machine_state == MachineState.RUNNING:
            if use_cycle and self._state_tick >= self._CYCLE_DWELL_RUNNING:
                transition = random.random()
                if transition < 0.4:
                    self._machine_state = MachineState.LOAD_CHANGE
                    self._load_change_scale = random.uniform(*self._LOAD_CHANGE_POWER_SCALE)
                elif transition < 0.7:
                    self._machine_state = MachineState.SHUTDOWN
                else:
                    pass
                self._state_tick = 0

        elif self._machine_state == MachineState.STARTUP:
            if self._state_tick >= self._STARTUP_RAMP_TICKS:
                self._machine_state = MachineState.RUNNING
                self._state_tick = 0

        elif self._machine_state == MachineState.SHUTDOWN:
            if self._state_tick >= self._SHUTDOWN_RAMP_TICKS:
                self._machine_state = MachineState.OFF
                self._state_tick = 0

        elif self._machine_state == MachineState.OFF:
            if self._state_tick >= self._CYCLE_DWELL_OFF:
                self._machine_state = MachineState.STARTUP
                self._state_tick = 0

        elif self._machine_state == MachineState.LOAD_CHANGE:
            if self._state_tick >= self._LOAD_CHANGE_TICKS:
                self._machine_state = MachineState.RUNNING
                self._state_tick = 0

    def _compute_derived_fields(self, values: Dict[str, float]) -> Dict[str, float]:
        l1 = values.get("current_l1")
        l2 = values.get("current_l2")
        l3 = values.get("current_l3")
        if l1 is not None and l2 is not None and l3 is not None:
            values["current_avg"] = round((l1 + l2 + l3) / 3.0, 2)

        vl1 = values.get("voltage_l1")
        vl2 = values.get("voltage_l2")
        vl3 = values.get("voltage_l3")
        if vl1 is not None and vl2 is not None and vl3 is not None:
            values["voltage_avg"] = round((vl1 + vl2 + vl3) / 3.0, 2)

        power = values.get("power")
        if power is not None and power > 0 and self._machine_state in (
            MachineState.RUNNING,
            MachineState.STARTUP,
            MachineState.LOAD_CHANGE,
        ):
            self._energy_kwh += power / 3600.0 / 1000.0 * 5.0
        values["energy_kwh"] = round(self._energy_kwh, 3)

        return values

    def _update_value(
        self,
        current: float,
        target: float,
        max_delta: float,
        noise_scale: float,
    ) -> float:
        drift = (target - current) * 0.1
        drift = max(-max_delta, min(max_delta, drift))

        noise = random.gauss(0, noise_scale) * self._noise_factor

        return current + drift + noise

    def _apply_fault(self, values: Dict[str, float]) -> Dict[str, float]:
        self._fault_counter += 1

        if self._fault_mode == "spike":
            self._apply_spike(values)
        elif self._fault_mode == "drop":
            self._apply_drop(values)
        elif self._fault_mode == "overheating":
            self._apply_overheating(values)
        elif self._fault_mode == "phase_imbalance":
            self._apply_phase_imbalance(values)
        elif self._fault_mode == "power_factor_drop":
            self._apply_power_factor_drop(values)
        elif self._fault_mode == "load_cycle":
            pass

        return values

    def _apply_spike(self, values: Dict[str, float]) -> None:
        if random.random() < 0.1:
            spike_mag = random.uniform(20, 50)
            for vk in ("voltage", "voltage_l1", "voltage_l2", "voltage_l3"):
                if vk in values:
                    values[vk] = values[vk] + spike_mag
            self._in_fault_state = True
        elif self._in_fault_state and random.random() < 0.3:
            self._in_fault_state = False

    def _apply_drop(self, values: Dict[str, float]) -> None:
        if random.random() < 0.05:
            drop_val = random.uniform(0.01, 0.1)
            for ck in ("current", "current_l1", "current_l2", "current_l3"):
                if ck in values:
                    values[ck] = drop_val
            if "power" in values:
                v = values.get("voltage", values.get("voltage_avg", 415.0))
                values["power"] = v * drop_val * 3
            self._in_fault_state = True
        elif self._in_fault_state and random.random() < 0.5:
            self._in_fault_state = False

    def _apply_overheating(self, values: Dict[str, float]) -> None:
        if "temperature" in values and self._fault_counter % 20 == 0:
            values["temperature"] += random.uniform(2, 5)
            self._in_fault_state = True
        elif "temperature" in values and values["temperature"] > 70:
            values["temperature"] -= random.uniform(0.5, 1.5)
            if values["temperature"] < 50:
                self._in_fault_state = False
                self._fault_counter = 0

    def _apply_phase_imbalance(self, values: Dict[str, float]) -> None:
        if self._phase_imbalance_target_phase is None:
            self._phase_imbalance_target_phase = random.choice(
                ["current_l1", "current_l2", "current_l3"]
            )
        phase = self._phase_imbalance_target_phase
        if phase in values:
            if self._fault_counter % 60 < 30:
                values[phase] = values[phase] * random.uniform(1.8, 2.5)
            else:
                values[phase] = values[phase] * random.uniform(0.3, 0.6)

    def _apply_power_factor_drop(self, values: Dict[str, float]) -> None:
        if "power_factor" in values:
            if self._fault_counter % 100 < 50 and not self._pf_degradation_active:
                self._pf_degradation_active = True
            if self._pf_degradation_active:
                pf = values["power_factor"]
                target = random.uniform(0.80, 0.86)
                values["power_factor"] = pf - (pf - target) * 0.05
                if values["power_factor"] < 0.82 and random.random() < 0.2:
                    self._pf_degradation_active = False

    @property
    def metrics(self) -> List[str]:
        return list(self._metrics.keys())
