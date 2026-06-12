from typing import Any
from pydantic import BaseModel


class HiddenOverconsumptionDailyResponse(BaseModel):
    date: str
    actual_energy_kwh: float
    p75_power_baseline_w: float | None = None
    baseline_energy_kwh: float | None = None
    hidden_overconsumption_kwh: float
    hidden_overconsumption_cost: float | None = None
    sample_count: int
    covered_duration_hours: float
    tariff_rate_used: float | None = None


class HiddenOverconsumptionDeviceResponse(BaseModel):
    date: str
    device_id: str | None = None
    device_name: str | None = None
    actual_energy_kwh: float
    p75_power_baseline_w: float | None = None
    baseline_energy_kwh: float | None = None
    difference_vs_baseline_kwh: float | None = None
    status: str
    hidden_overconsumption_kwh: float
    hidden_overconsumption_cost: float | None = None
    sample_count: int
    covered_duration_hours: float
    tariff_rate_used: float | None = None


class HiddenOverconsumptionSummaryResponse(BaseModel):
    selected_days: int
    total_actual_energy_kwh: float
    aggregate_p75_baseline_reference: float | None = None
    total_baseline_energy_kwh: float
    total_hidden_overconsumption_kwh: float
    total_hidden_overconsumption_cost: float | None = None
    tariff_rate_used: float | None = None


class HiddenOverconsumptionInsightResponse(BaseModel):
    summary: HiddenOverconsumptionSummaryResponse
    daily_breakdown: list[HiddenOverconsumptionDailyResponse]
    device_breakdown: list[HiddenOverconsumptionDeviceResponse] = []
    aggregation_rule: dict[str, str]
    insight_text: str | None = None


class ReportResponse(BaseModel):
    report_id: str
    status: str
    created_at: str
    queue_position: int | None = None
    estimated_wait_seconds: int | None = None
    estimated_completion_seconds: int | None = None
    result_ready: bool = False
    artifact_ready: bool = False
    download_url: str | None = None
    result_url: str | None = None
    coverage_result: dict[str, Any] | None = None


class ReportResultResponse(BaseModel):
    report_id: str
    status: str
    result: dict | None
    error_code: str | None
    error_message: str | None
    created_at: str
    started_at: str | None = None
    completed_at: str | None
    result_ready: bool = False
    artifact_ready: bool = False
    download_url: str | None = None
    coverage_result: dict[str, Any] | None = None


class TariffResponse(BaseModel):
    tenant_id: str
    energy_rate_per_kwh: float
    demand_charge_per_kw: float
    reactive_penalty_rate: float
    fixed_monthly_charge: float
    power_factor_threshold: float
    currency: str


class ErrorResponse(BaseModel):
    error: str
    message: str
