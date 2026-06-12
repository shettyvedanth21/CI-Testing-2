# AI_FactoryOPS Table Schema

This document summarizes the MySQL schema used by the project in the unified database (`ai_factoryops` / `AI_FactoryOPS`).

## Scope
- Included: application tables created/managed by service models + migrations.
- Included: migration version tables used in single-DB mode.
- Not included: InfluxDB measurements (telemetry time-series), MinIO objects, Redis (none), browser/local storage.

## Service Ownership Map
- `device-service`: `devices`, `device_shifts`, `parameter_health_config`, `device_performance_trends`, `device_properties`, `idle_running_log`
- `rule-engine-service`: `rules`, `alerts`, `activity_events`
- `reporting-service`: `energy_reports`, `scheduled_reports`, `tenant_tariffs`, `tariff_config`, `notification_channels`
- `analytics-service`: `analytics_jobs`
- `waste-analysis-service`: `waste_analysis_jobs`, `waste_device_summary`
- `data-export-service`: `export_checkpoints`

---

## 1) device-service

### `devices`
Primary key: `device_id` (VARCHAR(50))

Columns:
- `device_id` VARCHAR(50) PK
- `tenant_id` VARCHAR(50) NULL, indexed
- `device_name` VARCHAR(255) NOT NULL
- `device_type` VARCHAR(100) NOT NULL, indexed
- `manufacturer` VARCHAR(255) NULL
- `model` VARCHAR(255) NULL
- `location` VARCHAR(500) NULL
- `phase_type` VARCHAR(20) NULL, indexed
- `data_source_type` VARCHAR(20) NOT NULL, default `metered`, indexed
- `idle_current_threshold` NUMERIC(10,4) NULL
- `legacy_status` VARCHAR(50) NOT NULL, default `active`, indexed
- `last_seen_timestamp` DATETIME(tz) NULL, indexed
- `metadata_json` TEXT NULL
- `created_at` DATETIME(tz) NOT NULL
- `updated_at` DATETIME(tz) NOT NULL
- `deleted_at` DATETIME(tz) NULL

### `device_shifts`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `device_id` VARCHAR(50) NOT NULL, indexed
- `tenant_id` VARCHAR(50) NULL, indexed
- `shift_name` VARCHAR(100) NOT NULL
- `shift_start` TIME NOT NULL
- `shift_end` TIME NOT NULL
- `maintenance_break_minutes` INT NOT NULL, default 0
- `day_of_week` INT NULL
- `is_active` BOOLEAN NOT NULL, default true
- `created_at` DATETIME(tz) NOT NULL
- `updated_at` DATETIME(tz) NOT NULL

Constraints:
- FK `device_id -> devices.device_id` (ON DELETE CASCADE)

### `parameter_health_config`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `device_id` VARCHAR(50) NOT NULL, indexed
- `tenant_id` VARCHAR(50) NULL, indexed
- `parameter_name` VARCHAR(100) NOT NULL
- `normal_min` FLOAT NULL
- `normal_max` FLOAT NULL
- `weight` FLOAT NOT NULL, default 0.0
- `ignore_zero_value` BOOLEAN NOT NULL, default false
- `is_active` BOOLEAN NOT NULL, default true
- `created_at` DATETIME(tz) NOT NULL
- `updated_at` DATETIME(tz) NOT NULL

Constraints:
- FK `device_id -> devices.device_id` (ON DELETE CASCADE)

### `device_performance_trends`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `device_id` VARCHAR(50) NOT NULL, indexed
- `bucket_start_utc` DATETIME(tz) NOT NULL, indexed
- `bucket_end_utc` DATETIME(tz) NOT NULL
- `bucket_timezone` VARCHAR(64) NOT NULL (default `Asia/Kolkata`)
- `interval_minutes` INT NOT NULL (default 5)
- `health_score` FLOAT NULL
- `uptime_percentage` FLOAT NULL
- `planned_minutes` INT NOT NULL
- `effective_minutes` INT NOT NULL
- `break_minutes` INT NOT NULL
- `points_used` INT NOT NULL
- `is_valid` BOOLEAN NOT NULL
- `message` TEXT NULL
- `created_at` DATETIME(tz) NOT NULL, indexed

Constraints:
- FK `device_id -> devices.device_id` (ON DELETE CASCADE)
- Unique: `uq_perf_trend_device_bucket (device_id, bucket_start_utc)`

### `device_properties`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `device_id` VARCHAR(50) NOT NULL, indexed
- `property_name` VARCHAR(100) NOT NULL
- `data_type` VARCHAR(20) NOT NULL (default `float`)
- `is_numeric` BOOLEAN NOT NULL (default true)
- `discovered_at` DATETIME(tz) NOT NULL
- `last_seen_at` DATETIME(tz) NOT NULL

Constraints:
- FK `device_id -> devices.device_id` (ON DELETE CASCADE)

### `idle_running_log`
Primary key: `id` (BIGINT, autoincrement)

Columns:
- `id` BIGINT PK
- `device_id` VARCHAR(50) NOT NULL
- `period_start` DATETIME(tz) NOT NULL
- `period_end` DATETIME(tz) NOT NULL
- `idle_duration_sec` INT NOT NULL, default 0
- `idle_energy_kwh` NUMERIC(12,6) NOT NULL, default 0
- `idle_cost` NUMERIC(12,4) NOT NULL, default 0
- `currency` VARCHAR(10) NOT NULL, default `INR`
- `tariff_rate_used` NUMERIC(10,4) NOT NULL, default 0
- `pf_estimated` BOOLEAN NOT NULL, default false
- `created_at` DATETIME(tz) NOT NULL
- `updated_at` DATETIME(tz) NOT NULL

Constraints / indexes:
- FK `device_id -> devices.device_id` (ON DELETE CASCADE)
- Unique: `uq_idle_log_device_day (device_id, period_start)`
- Index: `idx_idle_log_device_period (device_id, period_start)`

---

## 2) rule-engine-service

### `rules`
Primary key: `rule_id` (VARCHAR(36), UUID)

Columns:
- `rule_id` VARCHAR(36) PK
- `tenant_id` VARCHAR(50) NULL, indexed
- `rule_name` VARCHAR(255) NOT NULL
- `description` TEXT NULL
- `scope` VARCHAR(50) NOT NULL
- `property` VARCHAR(100) NULL, indexed
- `condition` VARCHAR(20) NULL
- `threshold` FLOAT NULL
- `rule_type` VARCHAR(20) NOT NULL, indexed (`threshold` / `time_based`)
- `cooldown_mode` VARCHAR(20) NOT NULL (`interval` / `no_repeat`)
- `time_window_start` VARCHAR(5) NULL
- `time_window_end` VARCHAR(5) NULL
- `timezone` VARCHAR(64) NOT NULL (default `Asia/Kolkata`)
- `time_condition` VARCHAR(50) NULL
- `triggered_once` BOOLEAN NOT NULL, default false
- `status` VARCHAR(50) NOT NULL, indexed
- `notification_channels` JSON NOT NULL
- `cooldown_minutes` INT NOT NULL, default 15
- `last_triggered_at` DATETIME(tz) NULL
- `created_at` DATETIME(tz) NOT NULL
- `updated_at` DATETIME(tz) NOT NULL
- `deleted_at` DATETIME(tz) NULL
- `device_ids` JSON NOT NULL

Indexes:
- `ix_rules_tenant_id`, `ix_rules_property`, `ix_rules_status` (+ model-level index on `rule_type`)

### `alerts`
Primary key: `alert_id` (VARCHAR(36), UUID)

Columns:
- `alert_id` VARCHAR(36) PK
- `tenant_id` VARCHAR(50) NULL, indexed
- `rule_id` VARCHAR(36) NOT NULL, indexed
- `device_id` VARCHAR(50) NOT NULL, indexed
- `severity` VARCHAR(50) NOT NULL
- `message` TEXT NOT NULL
- `actual_value` FLOAT NOT NULL
- `threshold_value` FLOAT NOT NULL
- `status` VARCHAR(50) NOT NULL, indexed
- `acknowledged_by` VARCHAR(255) NULL
- `acknowledged_at` DATETIME(tz) NULL
- `resolved_at` DATETIME(tz) NULL
- `created_at` DATETIME(tz) NOT NULL

Constraints:
- FK `rule_id -> rules.rule_id` (ON DELETE CASCADE)

### `activity_events`
Primary key: `event_id` (VARCHAR(36), UUID)

Columns:
- `event_id` VARCHAR(36) PK
- `tenant_id` VARCHAR(50) NULL, indexed
- `device_id` VARCHAR(50) NULL, indexed
- `rule_id` VARCHAR(36) NULL, indexed
- `alert_id` VARCHAR(36) NULL, indexed
- `event_type` VARCHAR(100) NOT NULL, indexed
- `title` VARCHAR(255) NOT NULL
- `message` TEXT NOT NULL
- `metadata_json` JSON NOT NULL
- `is_read` BOOLEAN NOT NULL, indexed
- `read_at` DATETIME(tz) NULL
- `created_at` DATETIME(tz) NOT NULL, indexed

---

## 3) reporting-service

### `energy_reports`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `report_id` VARCHAR(36) UNIQUE NOT NULL
- `tenant_id` VARCHAR(50) NOT NULL, indexed
- `report_type` ENUM(`consumption`,`comparison`) NOT NULL
- `status` ENUM(`pending`,`processing`,`completed`,`failed`) NOT NULL
- `params` JSON NOT NULL
- `computation_mode` ENUM(`direct_power`,`derived_single`,`derived_three`) NULL
- `phase_type_used` VARCHAR(20) NULL
- `result_json` JSON NULL
- `s3_key` VARCHAR(500) NULL
- `error_code` VARCHAR(100) NULL
- `error_message` TEXT NULL
- `progress` INT NOT NULL
- `created_at` DATETIME NOT NULL
- `completed_at` DATETIME NULL

Indexes:
- `ix_energy_reports_tenant_id`
- `ix_energy_reports_tenant_status (tenant_id, status)`
- `ix_energy_reports_tenant_type_created (tenant_id, report_type, created_at)`

### `scheduled_reports`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `schedule_id` VARCHAR(36) UNIQUE NOT NULL
- `tenant_id` VARCHAR(50) NOT NULL, indexed
- `report_type` ENUM(`consumption`,`comparison`) NOT NULL
- `frequency` ENUM(`daily`,`weekly`,`monthly`) NOT NULL
- `params_template` JSON NOT NULL
- `is_active` BOOLEAN NOT NULL
- `last_run_at` DATETIME NULL
- `next_run_at` DATETIME NULL
- `last_status` VARCHAR(50) NULL
- `retry_count` INT NOT NULL
- `last_result_url` VARCHAR(2000) NULL
- `created_at` DATETIME NOT NULL
- `updated_at` DATETIME NOT NULL

### `tenant_tariffs` (legacy/compat path)
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `tenant_id` VARCHAR(50) UNIQUE NOT NULL
- `energy_rate_per_kwh` FLOAT NOT NULL
- `demand_charge_per_kw` FLOAT NOT NULL
- `reactive_penalty_rate` FLOAT NOT NULL
- `fixed_monthly_charge` FLOAT NOT NULL
- `power_factor_threshold` FLOAT NOT NULL
- `currency` VARCHAR(10) NOT NULL
- `created_at` DATETIME NOT NULL
- `updated_at` DATETIME NOT NULL

### `tariff_config`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `rate` NUMERIC(10,4) NOT NULL
- `currency` VARCHAR(10) NOT NULL (default `INR`)
- `updated_at` DATETIME NOT NULL
- `updated_by` VARCHAR(100) NULL

### `notification_channels`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `channel_type` VARCHAR(20) NOT NULL, indexed
- `value` VARCHAR(255) NOT NULL
- `is_active` BOOLEAN NOT NULL, indexed
- `created_at` DATETIME NOT NULL

Notes:
- Email recipients are read from active rows where `channel_type='email'`.

---

## 4) analytics-service

### `analytics_jobs`
Primary key: `id` (VARCHAR(36), UUID)

Columns:
- `id` VARCHAR(36) PK
- `job_id` VARCHAR(100) UNIQUE NOT NULL, indexed
- `device_id` VARCHAR(50) NOT NULL, indexed
- `analysis_type` VARCHAR(50) NOT NULL
- `model_name` VARCHAR(100) NOT NULL
- `date_range_start` DATETIME(tz) NOT NULL
- `date_range_end` DATETIME(tz) NOT NULL
- `parameters` JSON NULL
- `status` VARCHAR(50) NOT NULL (default `pending`)
- `progress` FLOAT NULL
- `message` TEXT NULL
- `error_message` TEXT NULL
- `results` JSON NULL
- `accuracy_metrics` JSON NULL
- `execution_time_seconds` INT NULL
- `created_at` DATETIME(tz) NOT NULL
- `started_at` DATETIME(tz) NULL
- `completed_at` DATETIME(tz) NULL
- `updated_at` DATETIME(tz) NOT NULL

Indexes:
- `idx_analytics_jobs_status (status)`
- `idx_analytics_jobs_created_at (created_at)`

---

## 5) waste-analysis-service

### `waste_analysis_jobs`
Primary key: `id` (VARCHAR(36), UUID)

Columns:
- `id` VARCHAR(36) PK
- `job_name` VARCHAR(255) NULL
- `scope` ENUM(`all`,`selected`) NOT NULL
- `device_ids` JSON NULL
- `start_date` DATE NOT NULL
- `end_date` DATE NOT NULL
- `granularity` ENUM(`daily`,`weekly`,`monthly`) NOT NULL
- `status` ENUM(`pending`,`running`,`completed`,`failed`) NOT NULL
- `progress_pct` INT NOT NULL
- `stage` VARCHAR(255) NULL
- `result_json` JSON NULL
- `s3_key` VARCHAR(500) NULL
- `download_url` VARCHAR(500) NULL
- `tariff_rate_used` FLOAT NULL
- `currency` VARCHAR(10) NULL
- `error_code` VARCHAR(64) NULL
- `error_message` TEXT NULL
- `created_at` DATETIME NOT NULL
- `completed_at` DATETIME NULL

Indexes:
- `idx_waste_jobs_status_created (status, created_at)`

### `waste_device_summary`
Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `job_id` VARCHAR(36) NOT NULL, indexed
- `device_id` VARCHAR(100) NOT NULL
- `device_name` VARCHAR(255) NULL
- `data_source_type` VARCHAR(20) NULL
- `idle_duration_sec` INT NOT NULL
- `idle_energy_kwh` FLOAT NOT NULL
- `idle_cost` FLOAT NULL
- `standby_power_kw` FLOAT NULL
- `standby_energy_kwh` FLOAT NULL
- `standby_cost` FLOAT NULL
- `total_energy_kwh` FLOAT NOT NULL
- `total_cost` FLOAT NULL
- `offhours_energy_kwh` FLOAT NULL
- `offhours_cost` FLOAT NULL
- `data_quality` VARCHAR(20) NULL
- `energy_quality` VARCHAR(20) NULL
- `idle_quality` VARCHAR(20) NULL
- `standby_quality` VARCHAR(20) NULL
- `overall_quality` VARCHAR(20) NULL
- `idle_status` VARCHAR(32) NULL
- `pf_estimated` BOOLEAN NOT NULL
- `warnings` JSON NULL
- `calculation_method` VARCHAR(50) NULL

Constraints / indexes:
- Unique: `uq_waste_job_device (job_id, device_id)`
- Index: `idx_waste_job_device (job_id, device_id)`

---

## 6) data-export-service

### `export_checkpoints`
Created dynamically by checkpoint repository.

Primary key: `id` (INT, autoincrement)

Columns:
- `id` INT PK
- `device_id` VARCHAR(50) NOT NULL
- `last_exported_at` DATETIME(6) NOT NULL
- `last_sequence` INT DEFAULT 0
- `status` VARCHAR(50) NOT NULL
- `s3_key` VARCHAR(500) NULL
- `record_count` INT DEFAULT 0
- `error_message` TEXT NULL
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP

Constraints / indexes:
- Unique: `uq_device_exported_at (device_id, last_exported_at)`
- Indexes: `idx_checkpoint_device_id(device_id)`, `idx_checkpoint_status(status)`, `idx_checkpoint_updated(updated_at)`

---

## 7) Migration Metadata Tables (single-DB deployment)

In unified DB mode, each service keeps an independent Alembic version table:
- `alembic_version_device`
- `alembic_version_rule_engine`
- `alembic_version_reporting`
- `alembic_version_waste`
- `alembic_version_analytics`

These store revision pointers and are required for per-service migrations.

---

## 8) Operational Notes
- Unified DB target in compose/env: `ai_factoryops` (case-insensitive in MySQL).
- Data-service telemetry is not stored in MySQL tables in this repo; it is read from InfluxDB.
- Some services (for example analytics) rely on model-driven table creation and/or service startup initialization; verify migration policy per environment before production cutover.
