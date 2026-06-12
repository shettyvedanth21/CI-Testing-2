# Shivex Machine Health Intelligence — Complete Working Logic

This document is a complete, in-depth, line-by-line trace of how the **Risk Assessment** and **Anomaly Activity** features work in the actual shipped codebase. Every formula, threshold, weight, schedule, and decision rule is documented here so that a stakeholder, operator, or reviewer can understand the system without reading Python source code.

This document reflects the exact implementation as of the current codebase. No code changes are made here — this is documentation only.

---

## Table of Contents

1. [What These Two Features Are](#1-what-these-two-features-are)
2. [High-Level Data Flow](#2-high-level-data-flow)
3. [Raw Telemetry Inputs Used](#3-raw-telemetry-inputs-used)
4. [Feature Window Creation](#4-feature-window-creation)
5. [Running-State Classification](#5-running-state-classification)
6. [Feature Window Statistics Computed](#6-feature-window-statistics-computed)
7. [Risk Assessment: Baseline Learning](#7-risk-assessment-baseline-learning)
8. [Risk Assessment: Baseline Quality Formula](#8-risk-assessment-baseline-quality-formula)
9. [Risk Assessment: Baseline Status (Active vs Candidate)](#9-risk-assessment-baseline-status-active-vs-candidate)
10. [Risk Assessment: Score Calculation](#10-risk-assessment-score-calculation)
11. [Risk Assessment: Signal Drift Computation](#11-risk-assessment-signal-drift-computation)
12. [Risk Assessment: Weighted Score and Final Scale](#12-risk-assessment-weighted-score-and-final-scale)
13. [Risk Assessment: Confidence Formula](#13-risk-assessment-confidence-formula)
14. [Risk Assessment: Status Bands and Safety Rules](#14-risk-assessment-status-bands-and-safety-rules)
15. [Risk Assessment: Top Reasons](#15-risk-assessment-top-reasons)
16. [Risk Assessment: Score Trend (History Chart)](#16-risk-assessment-score-trend-history-chart)
17. [Anomaly Activity: Per-Field Baseline Learning](#17-anomaly-activity-per-field-baseline-learning)
18. [Anomaly Activity: Per-Field Quality Formula](#18-anomaly-activity-per-field-quality-formula)
19. [Anomaly Activity: When a Field Baseline Becomes Active](#19-anomaly-activity-when-a-field-baseline-becomes-active)
20. [Anomaly Activity: Detection — z-Score Computation](#20-anomaly-activity-detection--z-score-computation)
21. [Anomaly Activity: Direction Rules Per Signal](#21-anomaly-activity-direction-rules-per-signal)
22. [Anomaly Activity: Severity Thresholds](#22-anomaly-activity-severity-thresholds)
23. [Anomaly Activity: Confirmation Rules](#23-anomaly-activity-confirmation-rules)
24. [Anomaly Activity: Event Types (deviation, persistent, trend)](#24-anomaly-activity-event-types-deviation-persistent-trend)
25. [Anomaly Activity: Event Merging and Duration](#25-anomaly-activity-event-merging-and-duration)
26. [Anomaly Activity: Supply-Related vs Machine-Related Heuristic](#26-anomaly-activity-supply-related-vs-machine-related-heuristic)
27. [Anomaly Activity: Startup-Adjacent and Mode-Change Flags](#27-anomaly-activity-startup-adjacent-and-mode-change-flags)
28. [Anomaly Activity: Confidence Formula](#28-anomaly-activity-confidence-formula)
29. [Anomaly Activity: Aggregation — Daily Counts](#29-anomaly-activity-aggregation--daily-counts)
30. [Anomaly Activity: Aggregation — Weekly Counts](#30-anomaly-activity-aggregation--weekly-counts)
31. [Anomaly Activity: Counting Policy](#31-anomaly-activity-counting-policy)
32. [Scheduler: Background Jobs and Timing](#32-scheduler-background-jobs-and-timing)
33. [Database Tables Used](#33-database-tables-used)
34. [API Endpoints and Response Behavior](#34-api-endpoints-and-response-behavior)
35. [Staleness Detection](#35-staleness-detection)
36. [Cleanup and Retention](#36-cleanup-and-retention)
37. [Configuration Defaults](#37-configuration-defaults)
38. [Example: Reading the Widget at 94% Confidence](#38-example-reading-the-widget-at-94-confidence)
39. [What These Features Are Not](#39-what-these-features-are-not)
40. [Stakeholder Summary](#40-stakeholder-summary)
41. [Source Code Reference Files](#41-source-code-reference-files)

---

## 1. What These Two Features Are

Shivex machine health has two operator-facing intelligence widgets:

**Risk Assessment** — a machine-level degradation risk score from `1.0` to `10.0` (lower is better). Answers: *"Is this machine slowly drifting away from its normal behavior?"*

**Anomaly Activity** — a per-signal abnormal event detector and counter. Answers: *"What unusual signal-level events have recently happened on this machine?"*

They are related but not the same:
- Risk Assessment is a weighted, explainable condition score
- Anomaly Activity is a signal-event detector and counter

---

## 2. High-Level Data Flow

The machine page does **not** compute health live from raw telemetry. The shipped design is:

```
Raw telemetry (from DeviceRecentTelemetrySample in MySQL)
  → 5-minute feature windows (machine_health_feature_windows)
  → baseline learning (machine_health_baselines / machine_anomaly_baselines)
  → score/anomaly background jobs (scheduler_runner.py)
  → latest snapshot + history tables (machine_health_latest, machine_health_history, machine_anomaly_events, machine_anomaly_daily_counts, machine_anomaly_weekly_counts)
  → fast dashboard/API reads (no raw telemetry scan during page load)
```

This is critical: the dashboard reads **precomputed** rows. Even with 100 organizations and 1500+ devices, the UI stays fast because it queries MySQL snapshot tables, not Influx or raw telemetry.

---

## 3. Raw Telemetry Inputs Used

The system can work with partial telemetry, but confidence falls when important fields are missing.

Fields consumed by the feature aggregator:

| Field | Used For |
|---|---|
| `current_avg` | Current mean, std, p95 |
| `current_l1` | Per-phase current, phase imbalance |
| `current_l2` | Per-phase current, phase imbalance |
| `current_l3` | Per-phase current, phase imbalance |
| `power` | Power mean, p95 |
| `power_factor` | Power factor mean |
| `voltage_avg` | Voltage mean |
| `voltage_l1` | Voltage imbalance |
| `voltage_l2` | Voltage imbalance |
| `voltage_l3` | Voltage imbalance |
| `frequency` | Frequency mean |
| `energy_kwh` | Energy delta (last − first in window) |
| `timestamp` | Window alignment and ordering |

The scheduler reads these from `DeviceRecentTelemetrySample.telemetry_json` (MySQL), not from InfluxDB.

---

## 4. Feature Window Creation

Both widgets depend on the **same shared feature-window layer**.

### Interval

The default feature window interval is:

```
DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS = 300  (5 minutes)
```

This is configurable via environment variable.

### How windows are aligned

The scheduler computes window boundaries using **epoch-flooring**:

1. Find the latest `sample_ts` for the device
2. Convert to UTC epoch seconds
3. Floor to the nearest multiple of `interval_seconds`
4. That becomes `window_end`
5. `window_start = window_end - interval_seconds`

This means windows are always aligned to clock boundaries (e.g., 13:00:00–13:05:00, 13:05:00–13:10:00).

### Upsert behavior

Each window is uniquely identified by `(tenant_id, device_id, window_start)`. If a window already exists for that key, it is **updated in-place**. If not, a new row is inserted. This handles re-runs safely.

### How the scheduler reads telemetry

For each device, the scheduler:
1. Queries `DeviceRecentTelemetrySample` for all rows where `sample_ts >= window_start AND sample_ts < window_end`
2. Parses each `telemetry_json` field into a `TelemetrySample` object
3. Passes the sample list to the pure aggregator function

---

## 5. Running-State Classification

Before a window is used for health intelligence, it is classified into one of six states:

| State | Meaning |
|---|---|
| `OFF` | Machine is not running (power/current near zero) |
| `STARTUP` | Machine just started (current rising from near-zero) |
| `STEADY_RUNNING` | Machine is running stably (low coefficient of variation) |
| `LOAD_CHANGE` | Machine is undergoing a load transition |
| `SHUTDOWN` | Machine is shutting down (current falling to near-zero) |
| `UNKNOWN` | Cannot determine state (insufficient data or conflicting signals) |

### Minimum samples required

The classifier requires **at least 3 samples** in the window. If fewer than 3, the state is `UNKNOWN`.

### OFF detection

```
If mean power < 50 W → OFF
If no power data and mean current < 0.5 A → OFF
```

Thresholds:
- `_OFF_POWER_THRESHOLD = 50.0`
- `_OFF_CURRENT_THRESHOLD = 0.5`

### STARTUP / SHUTDOWN detection

The classifier splits the current series into first-third and last-third, then:

1. Counts **step changes**: consecutive sample pairs where `|delta| / overall_mean > 0.25`
2. Computes **ramp**: `(last_third_mean - first_third_mean) / overall_mean`
3. If ramp > 0.5 and first_third_mean < 1.0 A → **STARTUP**
4. If ramp < −0.5 and last_third_mean < 1.0 A → **SHUTDOWN**

Thresholds:
- `_LOAD_CHANGE_STEP_FRACTION = 0.25`
- `_STARTUP_RAMP_FRACTION = 0.5`

### STEADY_RUNNING detection

If the **coefficient of variation** of current is ≤ 0.15, and there are no step changes, the state is **STEADY_RUNNING**.

```
cv = std(currents) / mean(currents)
if cv <= 0.15 → STEADY_RUNNING
```

Even if cv > 0.15, if there are **zero step changes**, the window can still be classified as STEADY_RUNNING (the variation is smooth, not abrupt).

Threshold:
- `_STEADY_CV_THRESHOLD = 0.15`

### LOAD_CHANGE detection

If step changes exist but the ramp does not qualify as startup or shutdown → **LOAD_CHANGE**.

### Fallback

If power data exists but current data is insufficient, the classifier falls back to power-only analysis using the same CV threshold.

### Why this matters

The implementation is intentionally conservative. Only **STEADY_RUNNING** windows are used for baseline learning and degradation scoring. STARTUP spikes, SHUTDOWN tails, and LOAD_CHANGE transitions are excluded to avoid false positives.

---

## 6. Feature Window Statistics Computed

For each 5-minute window, the aggregator computes:

| Statistic | Source | Computation |
|---|---|---|
| `current_avg_mean` | `current_avg` values | Arithmetic mean |
| `current_avg_std` | `current_avg` values | Sample standard deviation (n−1 denominator). Requires ≥ 2 values |
| `current_avg_p95` | `current_avg` values | 95th percentile |
| `current_l1_mean` | `current_l1` values | Arithmetic mean |
| `current_l2_mean` | `current_l2` values | Arithmetic mean |
| `current_l3_mean` | `current_l3` values | Arithmetic mean |
| `power_mean` | `power` values | Arithmetic mean |
| `power_p95` | `power` values | 95th percentile |
| `power_factor_mean` | `power_factor` values | Arithmetic mean |
| `voltage_avg_mean` | `voltage_avg` values | Arithmetic mean |
| `voltage_imbalance` | last `voltage_l1`, `voltage_l2`, `voltage_l3` | `max(|phase − avg|) / |avg|` |
| `phase_imbalance` | last `current_l1`, `current_l2`, `current_l3` | `max(|phase − avg|) / |avg|` |
| `frequency_mean` | `frequency` values | Arithmetic mean |
| `energy_kwh` | `energy_kwh` values | `max(0, last − first)` (requires ≥ 2 values) |
| `telemetry_coverage` | sample count vs expected | `clamp(sample_count / expected_sample_count, 0, 1)`. If expected = 0, coverage = 1.0 |
| `sample_count` | total raw samples | Count |
| `running_state` | all samples | Output of classifier (see §5) |

### Phase imbalance detail

Phase imbalance is computed from the **last** (most recent) phase values in the window, not the mean across all samples. This captures the instantaneous imbalance at the end of the window.

Formula:
```
phases = [l1, l2, l3]  (only valid/finite values)
avg = mean(phases)
if |avg| < 1e-9:
    if all phases near 0 → imbalance = 0.0
    else → None (cannot compute)
imbalance = max(|p - avg| for p in phases) / |avg|
```

Requires **at least 2 valid phase values**. If only one phase is available, phase imbalance is `None`.

---

## 7. Risk Assessment: Baseline Learning

The baseline is **per-machine, not global**. Each device learns its own normal.

### Data source

Baseline learning uses **STEADY_RUNNING** windows only. All other states (OFF, STARTUP, LOAD_CHANGE, SHUTDOWN, UNKNOWN) are excluded.

### How many windows are loaded

The `load_feature_windows_for_baseline` function calculates:

```
windows_per_day = 24 * 60 * 60 / interval_seconds
window_limit = max(24, minimum_days * windows_per_day + windows_per_day)
```

For the default 300-second interval and 7-day minimum:
- `windows_per_day = 288`
- `window_limit = max(24, 7 * 288 + 288) = 2304`

So up to 2304 windows (7+ days of 5-minute windows) are loaded for baseline learning.

### Statistics computed from steady windows

For each signal, the baseline learns:

| Baseline Field | Source | Computation |
|---|---|---|
| `current_avg_mean` | `current_avg_mean` from steady windows | Mean of means |
| `current_avg_std` | `current_avg_std` from steady windows | If ≥ 2 std values: std of stds. Else: mean of stds |
| `power_mean` | `power_mean` from steady windows | Mean of means |
| `power_p95` | `power_p95` from steady windows | Mean of p95s |
| `power_factor_mean` | `power_factor_mean` from steady windows | Mean of means |
| `voltage_avg_mean` | `voltage_avg_mean` from steady windows | Mean of means |
| `phase_imbalance_mean` | `phase_imbalance` from steady windows | Mean of imbalances |
| `frequency_mean` | `frequency_mean` from steady windows | Mean of means |

### Temporal span

The baseline also computes the temporal span of steady-running windows:

```
span_days = (max(window_end) - min(window_start)).total_seconds() / 86400
```

This is used to enforce the minimum-day requirement.

---

## 8. Risk Assessment: Baseline Quality Formula

Baseline quality is a number from `0.0` to `1.0` that reflects how trustworthy the baseline is.

### Step 1: Signal completeness

Signal completeness measures how many key fields are present across steady windows:

```
key_fields = [
    "current_avg_mean", "current_avg_std",
    "power_mean", "power_p95", "power_factor_mean",
    "voltage_avg_mean", "phase_imbalance", "frequency_mean",
]

total_possible = number_of_steady_windows * 8
total_present = count of non-None, finite values across all windows and key_fields
signal_completeness = clamp(total_present / total_possible, 0, 1)
```

### Step 2: Steady-running coverage

```
steady_coverage = clamp(steady_window_count / total_window_count, 0, 1)
```

### Step 3: Window count factor

```
window_count_factor = min(steady_window_count / 20, 1.0)
```

This saturates at 1.0 once there are 20 or more steady-running windows.

### Step 4: Quality score

```
if steady_window_count < 3:
    quality_score = (steady_window_count / 3) * 0.2
else:
    quality_score = signal_completeness * 0.5
                    + steady_coverage * 0.3
                    + window_count_factor * 0.2
```

The weights are:
- Signal completeness: **50%**
- Steady-running coverage: **30%**
- Window count factor: **20%**

### Step 5: Temporal span penalty

If the steady-running temporal span is **below the minimum days** (default 7):

```
quality_score *= clamp(span_days / minimum_days, 0, 1)
```

This means a 3.5-day span would halve the quality score (3.5 / 7 = 0.5).

### Quality bands

| Band | Range |
|---|---|
| `high` | ≥ 0.85 |
| `medium` | ≥ 0.70 |
| `low` | ≥ 0.50 |
| `insufficient` | < 0.50 |

---

## 9. Risk Assessment: Baseline Status (Active vs Candidate)

A baseline row has a `status` field:

```
if quality_score >= 0.3 AND learning_window_count >= 3:
    status = "active"
else:
    status = "candidate"
```

So a baseline becomes **active** only when:
1. Quality score is at least 0.3
2. At least 3 steady-running windows contributed

Otherwise it remains **candidate** and the system shows "Learning baseline" in the UI.

### Baseline persistence

The system looks for an existing `active` or `candidate` baseline for the device. If one exists, it is **updated in-place** with the latest learning results. If none exists, a new row is inserted.

Baseline version is incremented when an active baseline already exists and the system creates a new one.

---

## 10. Risk Assessment: Score Calculation

The degradation score is computed by the pure function `compute_degradation_score()` in `scorer.py`. It takes:
- A `BaselineInput` (the learned baseline)
- Recent `FeatureWindowInput` objects (last 5 steady-running windows)
- Optional `PriorScoreEntry` history (last 10 historical scores)

### Signals used

The score uses **five explainable signals**:

| Signal | What It Measures | Weight | Drift Mode | Max Drift |
|---|---|---|---|---|
| `current_variability_drift` | Is current fluctuation noisier than normal? | **0.25** (25%) | `increase` | 3.0 |
| `power_factor_drop` | Is PF lower than normal? | **0.25** (25%) | `decrease` | 1.0 |
| `abnormal_power_draw` | Is power behavior materially different? | **0.20** (20%) | `absolute` | 3.0 |
| `phase_imbalance_drift` | Is phase imbalance worse than normal? | **0.15** (15%) | `increase` | 3.0 |
| `trend_worsening` | Have recent scores been getting worse? | **0.15** (15%) | positive slope only | 3.0 |

**Weights sum to 1.0**: 0.25 + 0.25 + 0.20 + 0.15 + 0.15 = 1.00

### Required signals

Two signals are **required** — if either is unavailable, the score is not produced:

```
_REQUIRED_SIGNALS = {"current_variability_drift", "power_factor_drop"}
```

If `current_variability_drift` or `power_factor_drop` is missing, the result status is `insufficient_signals` and the score is `None`.

### Minimum signal completeness

Even if the required signals are present, if the overall signal completeness is below 40%:

```
_MIN_SIGNAL_COMPLETENESS = 0.4
```

the score is not produced.

---

## 11. Risk Assessment: Signal Drift Computation

Each signal is compared against the baseline to compute a **drift value**.

### How "recent" values are computed

For the four telemetry-based signals, the scorer takes the **average** of the available values from the most recent feature windows:

```
recent_std = avg(current_avg_std from recent windows)
recent_pf = avg(power_factor_mean from recent windows)
recent_power = avg(power_mean from recent windows)
recent_phase = avg(phase_imbalance from recent windows)
```

The `score_device()` function in `service.py` loads the **last 5 feature windows** (of any running state), then passes `windows[-5:]` to the scorer.

### Drift modes

#### `increase` mode (current_variability, phase_imbalance)

Only drifts **above** baseline count:

```
if |baseline| < 1e-9:
    drift = 3.0 if recent > 1e-9 else 0.0
else:
    drift = max(0.0, (recent - baseline) / |baseline|)
```

#### `decrease` mode (power_factor)

Only drifts **below** baseline count:

```
if |baseline| < 1e-9:
    drift = 3.0 if recent < -1e-9 else 0.0
else:
    drift = max(0.0, (baseline - recent) / |baseline|)
```

This is why "Power factor below baseline" is the label — only PF drops contribute.

#### `absolute` mode (power_draw)

Drift in **either** direction counts:

```
if |baseline| < 1e-9:
    drift = 0.0 if |recent| < 1e-9 else 3.0
else:
    drift = |recent - baseline| / |baseline|
```

### Trend worsening signal

This signal is different — it uses prior score history, not telemetry.

```
If fewer than 2 prior scores exist → trend = None (signal unavailable)
Compute linear regression slope:
    x_mean = (n - 1) / 2
    slope = Σ(i - x_mean)(score_i - score_mean) / Σ(i - x_mean)²
If slope ≤ 0 → trend = 0.0 (improving or flat)
If slope > 0 → trend = min(slope / 0.5, 3.0)
```

So a slope of 0.5 maps to trend = 1.0 (significant but not extreme), and a slope of 1.5 maps to trend = 3.0 (maximum contribution).

### Normalization

Each drift value is normalized by its signal's max drift:

```
normalized_drift = clamp(drift / max_drift, 0.0, 1.0)
```

This maps every signal to a 0.0–1.0 scale regardless of its raw drift magnitude.

---

## 12. Risk Assessment: Weighted Score and Final Scale

### Weighted raw score

```
total_weighted = Σ(weight * normalized_drift) for all available signals
```

### Normalization by available weight

If the sum of weights for available signals > 0:

```
total_weighted = total_weighted / available_weight_sum
```

This means the score is computed using **only** the available signals, and their weights are renormalized to sum to 1.0. Missing signals do not drag the score down — they just reduce confidence.

### Final scale mapping

```
score = 1.0 + total_weighted * 9.0
score = clamp(score, 1.0, 10.0)
```

This maps:
- `total_weighted = 0.0` → score = **1.0** (perfect health)
- `total_weighted = 0.5` → score = **5.5** (mid-range)
- `total_weighted = 1.0` → score = **10.0** (maximum risk)

If the score is not finite (NaN/Infinity), it is forced to 1.0.

---

## 13. Risk Assessment: Confidence Formula

Confidence is **not** a probability of failure. It is confidence that the score reflects the true machine condition given the data quality.

### Formula

```
confidence = clamp(signal_completeness * baseline_quality_score, 0.0, 1.0)
```

Where:
- `signal_completeness` = fraction of weighted score inputs available (available_count / total_signals)
- `baseline_quality_score` = the learned baseline's quality from 0.0 to 1.0

### Example: How 94% confidence happens

If a device has:
- `baseline_quality_score = 0.98` (very strong baseline)
- `signal_completeness = 0.96` (5 of 5 signals available, or 4 of 5 with slight imperfection)

Then:
```
confidence = 0.96 * 0.98 = 0.9408 ≈ 94%
```

### When confidence is hidden

The API hides the proper scored result when confidence < 0.3, returning a `learning` state instead.

---

## 14. Risk Assessment: Status Bands and Safety Rules

### Score bands

| Score Range | Status |
|---|---|
| < 3.0 | `healthy` |
| 3.0 – 4.99 | `watch` |
| 5.0 – 6.99 | `warning` |
| ≥ 7.0 | `critical` |

### Weak baseline override

If `baseline_quality < 0.3`, the status is forced to `learning` regardless of score:

```
if baseline_quality < 0.3:
    status = "learning"
```

This prevents a low-quality baseline from producing a misleading confident status.

### Insufficient signals

If required signals (`current_variability_drift` or `power_factor_drop`) are missing, or overall signal completeness < 0.4:

- `score = None`
- `status = "insufficient_signals"`
- Confidence is still computed but the score is not produced

### API-level hiding

Even if a score exists internally, the API returns `available=False, state="learning"` when:
1. `latest.status == "learning"`
2. `latest.status == "insufficient_signals"`
3. `latest.confidence < 0.3`

In these cases the UI should show learning-state semantics, not a scored result.

---

## 15. Risk Assessment: Top Reasons

The scorer selects the **top 3** reasons by contribution weight.

### How they are ranked

```
For each available contribution:
    ranking_score = drift * weight
Sort by ranking_score descending
Take top 3
```

### Reason labels

| Signal | Label |
|---|---|
| `current_variability_drift` | "Current variability above baseline" |
| `power_factor_drop` | "Power factor below baseline" |
| `abnormal_power_draw` | "Power draw deviating from baseline" |
| `phase_imbalance_drift` | "Phase imbalance above baseline" |
| `trend_worsening` | "Degradation trend worsening" |

If no signals have active drift, top_reasons is an empty tuple.

### Why reasons can be shown with a low score

A reason being visible does not mean high risk. Some signals may have small but non-zero drift, producing visible reasons while the weighted total remains in the healthy range.

---

## 16. Risk Assessment: Score Trend (History Chart)

The trend chart shows the last 7 days of saved score history.

### How trend data is loaded

```
trend_cutoff = now - 7 days
Query MachineHealthHistory where computed_at >= trend_cutoff
Order by computed_at desc, limit 168
Then reverse to chronological order
```

So up to **168 data points** (7 days × 24 hours) are returned.

### What is stored per history point

Each time the scoring job runs, it writes:
- One `MachineHealthLatest` row (upsert — one row per device)
- One `MachineHealthHistory` row (append — one row per scoring event)

The history row stores:
- `score`
- `status`
- `confidence`
- `baseline_version`
- `contributions_json` (full signal breakdown)

### Scoring cadence → history density

Since scoring runs every `1800s` (30 minutes) by default, a 7-day window produces approximately:

```
7 days × 24 hours × 2 scores/hour = 336 potential points
```

The API limits to 168 (about 3.5 days at 30-minute cadence), which is sufficient for a clear trend chart.

---

## 17. Anomaly Activity: Per-Field Baseline Learning

Anomaly detection has its own **per-field** baseline system. Each of the 5 monitored fields gets an independent baseline row.

### Monitored fields

```
SUPPORTED_FIELDS = ("current_avg", "power", "power_factor", "voltage_avg", "phase_imbalance")
```

### Field-to-feature mapping

Each anomaly field maps to one attribute on the shared `FeatureWindowInput`:

| Anomaly Field | Feature Window Attribute |
|---|---|
| `current_avg` | `current_avg_mean` |
| `power` | `power_mean` |
| `power_factor` | `power_factor_mean` |
| `voltage_avg` | `voltage_avg_mean` |
| `phase_imbalance` | `phase_imbalance` |

### Data source

Like Risk Assessment, anomaly baseline learning uses **STEADY_RUNNING** windows only.

### Statistics computed per field

For each field, from all valid values in steady-running windows:

| Statistic | Computation |
|---|---|
| `baseline_mean` | Arithmetic mean of field values |
| `baseline_std` | Sample standard deviation (n−1 denominator). Requires ≥ 2 values |
| `baseline_median` | Median of field values |
| `baseline_mad` | Median Absolute Deviation, scaled by `1.4826` for consistency with std |
| `baseline_p05` | 5th percentile |
| `baseline_p95` | 95th percentile |
| `reading_count` | Count of valid values |
| `field_coverage` | `clamp(reading_count / steady_window_count, 0, 1)` |
| `steady_coverage` | `clamp(steady_window_count / total_window_count, 0, 1)` |

### MAD detail

The MAD (Median Absolute Deviation) is computed as:

```
1. Compute median of values
2. Compute absolute deviations from median
3. Compute median of those deviations (raw MAD)
4. Scale: baseline_mad = 1.4826 * raw_mad
```

The scaling factor `1.4826` makes the MAD consistent with standard deviation for normal distributions. This is used as a fallback when std is too small or outlier-heavy.

---

## 18. Anomaly Activity: Per-Field Quality Formula

Each field baseline has its own quality score from `0.0` to `1.0`.

### Formula

```
if reading_count < 5:
    quality_score = clamp(reading_count / 5 * 0.2, 0, 1)
else:
    quality_score = field_coverage * 0.5
                    + steady_coverage * 0.3
                    + min(reading_count / 30, 1.0) * 0.2
```

The weights are:
- **Field coverage**: 50%
- **Steady-running coverage**: 30%
- **Reading count factor**: 20% (saturates at 30 readings)

### Temporal span penalty

Same as Risk Assessment — if the steady-running span is below the minimum days:

```
quality_score *= clamp(span_days / minimum_days, 0, 1)
```

Default `minimum_days = 7`.

### Quality bands

Same bands as Risk Assessment:

| Band | Range |
|---|---|
| `high` | ≥ 0.85 |
| `medium` | ≥ 0.70 |
| `low` | ≥ 0.50 |
| `insufficient` | < 0.50 |

---

## 19. Anomaly Activity: When a Field Baseline Becomes Active

A field baseline becomes `active` only if **all** of these conditions are met:

1. `reading_count >= 5`
2. `baseline_std is not None` and `baseline_std > 1e-9` (std must be meaningful)
3. `quality_score >= 0.3`
4. `minimum_days_met` (temporal span ≥ 7 days)

If any condition fails, the baseline stays `candidate`.

### Widget availability rule

The anomaly widget requires **at least 3 active field baselines** to become available. If fewer:

```
state = "learning"
available = False
```

This is why the UI can show "Building baseline — 5 of 5 signals learned" while still not showing the fully available event widget until enough baselines are active.

### Baseline persistence and churn control

When refreshing baselines, the system uses a **churn hysteresis** of `0.1`:

```
If a new candidate baseline would become active AND
   its quality_score is at least 0.1 better than the current active baseline:
    → Retire the old active baseline
    → Insert the new baseline with version += 1
Else:
    → Skip (avoid unnecessary baseline churn)
```

If no active baseline exists yet but a candidate row exists, the candidate is **updated in-place** until it becomes usable. This avoids unique-key collisions on `(tenant_id, device_id, field_name)`.

---

## 20. Anomaly Activity: Detection — z-Score Computation

Anomaly detection is z-score based, with two methods.

### Standard z-score (preferred)

Used when baseline standard deviation is meaningful:

```
if baseline_std > 1e-9:
    z = (observed - baseline_mean) / baseline_std
```

### Modified z-score (fallback)

Used when std is not usable but MAD is:

```
if baseline_mad > 1e-9 and baseline_median is valid:
    z = 0.6745 * (observed - baseline_median) / baseline_mad
```

The constant `0.6745` is the reciprocal of the 0.75 quantile of the standard normal distribution, which makes the modified z-score robust against outlier-heavy distributions.

### If neither works

If both std and MAD are too small or unavailable, the z-score is `None` and no anomaly is emitted for that field.

---

## 21. Anomaly Activity: Direction Rules Per Signal

Not every deviation counts the same way. The detector uses different direction modes:

| Field | Direction Mode | What Triggers |
|---|---|---|
| `current_avg` | `two_tailed` | Any direction (above or below baseline) |
| `power` | `two_tailed` | Any direction |
| `power_factor` | `decrease` | Only when PF drops below baseline |
| `voltage_avg` | `two_tailed` | Any direction |
| `phase_imbalance` | `increase` | Only when imbalance rises above baseline |

### Effective z-score

The raw z-score is converted to an **effective z magnitude** based on direction:

```
two_tailed:  effective_z = |z|
decrease:    effective_z = -z if z < 0, else 0.0
increase:    effective_z = z if z > 0, else 0.0
```

So for `power_factor`, a z-score of +2.0 (PF above baseline) is ignored (effective_z = 0). Only negative z (PF below baseline) produces a candidate.

---

## 22. Anomaly Activity: Severity Thresholds

After direction handling, the effective z magnitude is classified:

| Effective z | Severity |
|---|---|
| ≥ 4.0 | `severe` |
| ≥ 3.0 | `strong` |
| ≥ 2.0 | `mild` |
| < 2.0 | No anomaly candidate |

If effective_z < 2.0, no anomaly candidate is created for that field/window.

---

## 23. Anomaly Activity: Confirmation Rules

The detector does not raise on every single-window blip. Confirmation depends on severity:

### Severe

Can confirm from a **single window** if:
- It is NOT startup-adjacent
- It is NOT supply-related (voltage-only without machine-side corroboration)

### Strong

Can confirm from a **single window** if there is **cross-field support** (≥ 2 fields anomalous simultaneously).

Also confirms when there is **1+ consecutive matching event** in prior events (same field + same severity).

### Mild

Requires **at least 2 consecutive windows** of matching severity.

OR cross-field support (≥ 2 fields anomalous simultaneously).

Confirmation thresholds:

```
_MILD_CONFIRM_WINDOWS = 2
_STRONG_CONFIRM_WINDOWS = 1
```

### Cross-field support detail

A "cross-field" check counts how many of the **confidence-boost fields** are also anomalous:

```
_CONFIDENCE_BOOST_FIELDS = {"current_avg", "power_factor", "phase_imbalance"}
```

If at least 2 fields total are anomalous in the same window, confirmation is easier.

---

## 24. Anomaly Activity: Event Types (deviation, persistent, trend)

Each anomaly event has a type:

| Type | When Assigned |
|---|---|
| `deviation` | Default — a confirmed unusual event |
| `persistent` | When `merged_window_count >= 3` (event has spanned 3+ consecutive windows) |
| `trend` | When z-score history shows monotonic drift |

### Trend detection detail

A trend requires:
1. At least 3 z-score entries in history
2. All z-scores drift in the **same direction** for the field
3. Effective z magnitudes are **non-decreasing** (gradual escalation)

Example for `power_factor` (decrease mode):
- z-scores: −2.1, −2.5, −3.0
- All negative (PF dropping), magnitudes increasing → **trend**

Example for `current_avg` (two-tailed):
- z-scores: +2.0, +2.3, +2.8
- All same sign, magnitudes increasing → **trend**

---

## 25. Anomaly Activity: Event Merging and Duration

The detector **merges** adjacent matching anomaly windows into one event.

### Merge key

Two events can merge if they match on:
- Same `signal_field`
- Same `severity`
- Prior event has `merged_window_count < _MAX_MERGE_WINDOWS` (6)

### Gap threshold

There is also a gap check. If the gap between the prior event's `ended_at` and the current `window_start` exceeds `gap_threshold` feature-window intervals, the events are **not** merged (a new event starts instead).

Default `gap_threshold = 1` (no gap allowed).

### What happens during a merge

When a new candidate merges with a prior open event:

- `merged_window_count` increases by 1
- `z_score` is updated to the one with the **larger absolute magnitude**
- `signal_value` is updated to match the larger z-score
- `anomaly_type` is upgraded to `persistent` if merged count reaches 3
- `occurred_at` stays as the earliest timestamp
- `ended_at` becomes the current window end
- `duration_seconds` is recalculated
- `z_score_history` is extended with the new z-score
- `confidence` takes the **higher** of the two
- `supply_related`, `startup_adjacent`, `mode_change`, `recurring` are OR'd
- `correlated_signals` takes the longer tuple

### Maximum merge span

```
_MAX_MERGE_WINDOWS = 6
```

An event can span at most 6 consecutive windows (30 minutes at 5-minute intervals). After that, a new event starts.

### Duration

```
duration_seconds = (ended_at - occurred_at).total_seconds()
```

For a single-window event (1 × 5-minute window): `duration_seconds = 300`
For a 3-window merge: `duration_seconds = 900` (15 minutes)

---

## 26. Anomaly Activity: Supply-Related vs Machine-Related Heuristic

The code explicitly tries not to blame the machine for likely supply problems.

### Rule

```
If the anomalous field is "voltage_avg" (a supply-indicator field)
AND current_avg is NOT also anomalous
AND power_factor is NOT also anomalous
→ the event is tagged supply_related = True
```

Rationale: if only voltage is abnormal without corroborating machine-side signals, the issue is likely external supply, not machine degradation.

If voltage AND current (or voltage AND power_factor) are both anomalous, the voltage issue is more likely machine-driven, and `supply_related = False`.

---

## 27. Anomaly Activity: Startup-Adjacent and Mode-Change Flags

Each anomaly event carries context flags:

### `startup_adjacent`

```
True if:
    running_state of current window is STARTUP or SHUTDOWN
    OR running_state of the prior window is STARTUP or SHUTDOWN
```

### `mode_change`

```
True if:
    running_state of current window is LOAD_CHANGE
    OR running_state of prior window is LOAD_CHANGE
```

### `recurring`

```
True if:
    Any prior event for this device has the same signal_field and severity
```

These flags are informational — they help operators interpret events but do not suppress detection.

---

## 28. Anomaly Activity: Confidence Formula

Anomaly confidence reflects how well-supported the detected event is.

### Base confidence

```
quality_band_multiplier:
    "high"        → 1.0
    "medium"      → 0.7
    "low"         → 0.4
    "insufficient" → 0.0

raw_confidence = quality_band_multiplier * field_quality_score
```

### Cross-field boost

If other **confidence-boost fields** (`current_avg`, `power_factor`, `phase_imbalance`) are also anomalous:

```
raw_confidence *= 1.2
```

### Final clamp

```
confidence = clamp(raw_confidence, 0.0, 1.0)
```

### Example: How "Confidence 94%" appears

If:
- The field baseline quality band is `high` → multiplier = 1.0
- The field quality score is approximately 0.94
- There is cross-field support from another confidence-boost field

Then:
```
raw_confidence = 1.0 * 0.94 = 0.94
After boost: 0.94 * 1.2 = 1.128
After clamp: min(1.128, 1.0) = 1.0
```

Or without boost:
```
raw_confidence = 1.0 * 0.94 = 0.94 → shown as 94%
```

This means: the system has **94% confidence that the detected anomaly is a real, meaningful abnormality** given the baseline quality and data evidence.

It does **NOT** mean 94% chance of machine failure.

---

## 29. Anomaly Activity: Aggregation — Daily Counts

The system aggregates anomaly events into daily count rows for fast widget reads.

### Aggregation function

`aggregate_daily_counts()` takes all events for a single device on a target date and produces:

| Field | Meaning |
|---|---|
| `total_count` | Total number of events on this date |
| `mild_count` | Mild severity events (excluding supply-related and startup-adjacent) |
| `strong_count` | Strong severity events (excluding supply-related and startup-adjacent) |
| `severe_count` | Severe severity events (excluding supply-related and startup-adjacent) |
| `supply_related_count` | Events tagged as supply-related |
| `top_signal` | Most frequent signal_field (ties broken alphabetically) |
| `avg_confidence` | Mean confidence across all events |
| `signal_breakdown` | Per-signal field counts (field_name, count, mild, strong, severe) |

### Timezone handling

Daily aggregation uses the **platform timezone** (configurable via `PLATFORM_TIMEZONE` setting, defaulting to IST for Indian deployments):

```
local_midnight = midnight in platform timezone
day_start = local_midnight converted to UTC
day_end = (local_midnight + 1 day) converted to UTC
```

Events are filtered by `occurred_at >= day_start AND occurred_at < day_end` in UTC.

### Scheduler behavior

The daily aggregation job runs for the **current day and the 2 prior days** (3 days total):

```
for days_ago in range(3):
    target_date = today - timedelta(days=days_ago)
```

This provides self-healing: if a day's aggregation was missed or events were added later, it gets recomputed.

### Persistence

Daily counts use **upsert** on `(tenant_id, device_id, date)`. If a row exists, it is updated. If no events exist for a date, any stale count row is deleted.

---

## 30. Anomaly Activity: Aggregation — Weekly Counts

Weekly counts are derived from daily counts.

### Week definition

Weeks use **ISO week** convention: `week_start_date` is the Monday of the ISO week.

```
week_start = today - timedelta(days=today.weekday())  # Monday of current week
```

### Aggregation function

`aggregate_weekly_counts()` takes daily counts for the 7 days of the target week and produces:

| Field | Meaning |
|---|---|
| `total_count` | Sum of daily totals |
| `mild_count` | Sum of daily mild counts |
| `strong_count` | Sum of daily strong counts |
| `severe_count` | Sum of daily severe counts |
| `supply_related_count` | Sum of daily supply-related counts |
| `top_signal` | Signal with highest total count contribution |
| `avg_confidence` | Mean of daily avg_confidences |
| `signal_breakdown` | Merged per-signal counts across all days |
| `week_over_week_change` | `this_week_total − prior_week_total` |

### Week-over-week change

The system loads the prior week's total count from `MachineAnomalyWeeklyCount`:

```
prior_week_start = week_start - timedelta(days=7)
prior_total = load_prior_week_total(tenant_id, device_id, prior_week_start)
week_over_week_change = this_week_total - prior_total
```

If no prior week data exists, `week_over_week_change = None`.

### Scheduler behavior

The weekly aggregation job runs for the **current week and the prior week** (2 weeks total):

```
for week_offset in range(2):
    ws = week_start - timedelta(weeks=week_offset)
```

---

## 31. Anomaly Activity: Counting Policy

The counting policy is explicit and important:

| Event Category | Counts Toward Total | Counts Toward Severity Bucket (mild/strong/severe) |
|---|---|---|
| Normal event | Yes | Yes |
| `supply_related` event | Yes | **No** |
| `startup_adjacent` event | Yes | **No** |
| `mode_change` event | Yes | **Yes** |

Why:
- Supply-related events are real but should not be blamed on the machine
- Startup-adjacent events are expected transients
- Mode-change events are machine behavior and should count toward severity

### One merged event = one count

Each merged event counts as **one event**, regardless of how many feature windows were merged into it. A 3-window merge is one count, not three.

---

## 32. Scheduler: Background Jobs and Timing

All health intelligence is computed by background scheduler jobs. **No computation happens during page load.**

### Risk Assessment jobs

| Job | Default Interval | Config Key | What It Does |
|---|---|---|---|
| Feature window creation | 300s (5 min) | `DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS` | Reads recent telemetry, creates 5-min windows for all devices |
| Baseline refresh | 7200s (2 hrs) | `DEGRADATION_BASELINE_INTERVAL_SECONDS` | Re-learns degradation baseline from feature windows |
| Score calculation | 1800s (30 min) | `DEGRADATION_SCORING_INTERVAL_SECONDS` | Computes degradation score for each device |
| Cleanup | 7200s (2 hrs) | `DEGRADATION_CLEANUP_INTERVAL_SECONDS` | Deletes old feature windows and history rows |

### Anomaly Activity jobs

| Job | Default Interval | Config Key | What It Does |
|---|---|---|---|
| Baseline refresh | 7200s (2 hrs) | `ANOMALY_BASELINE_INTERVAL_SECONDS` | Re-learns per-field anomaly baselines |
| Detection | 1800s (30 min) | `ANOMALY_DETECTION_INTERVAL_SECONDS` | Runs anomaly detection for each device |
| Daily aggregation | 3600s (1 hr) | `ANOMALY_DAILY_AGGREGATION_INTERVAL_SECONDS` | Aggregates events into daily counts (3-day lookback) |
| Weekly aggregation | 21600s (6 hrs) | `ANOMALY_WEEKLY_AGGREGATION_INTERVAL_SECONDS` | Aggregates daily counts into weekly counts (2-week lookback) |
| Cleanup | 7200s (2 hrs) | `ANOMALY_CLEANUP_INTERVAL_SECONDS` | Deletes old events, counts, and retired baselines |

### Minimum interval enforcement

All intervals are enforced with a **minimum of 300 seconds** (5 minutes):

```
max(300, int(settings.INTERVAL_KEY))
```

### Concurrency model

All scheduler jobs run as `asyncio.Task` instances within a single process. They do not run in parallel per-device; they iterate through devices sequentially within each cycle.

Each device is processed in a **nested transaction** (`session.begin_nested()`), so one device's failure does not roll back others.

### Feature toggles

Both features are **disabled by default** and must be explicitly enabled:

```
DEGRADATION_ENABLED = False   # Must be set to True
ANOMALY_ENABLED = False       # Must be set to True
```

---

## 33. Database Tables Used

### Risk Assessment tables

| Table | Purpose |
|---|---|
| `machine_health_feature_windows` | 5-minute compact telemetry summaries (shared by both features) |
| `machine_health_baselines` | Degradation baseline (one row per device) |
| `machine_health_latest` | Latest degradation score snapshot (one row per device) |
| `machine_health_history` | Historical degradation scores (append-only) |

### Anomaly Activity tables

| Table | Purpose |
|---|---|
| `machine_anomaly_baselines` | Per-field anomaly baselines (one row per device-field) |
| `machine_anomaly_events` | Individual anomaly events (append, with merge/extend) |
| `machine_anomaly_daily_counts` | Daily aggregated counts (one row per device-date) |
| `machine_anomaly_weekly_counts` | Weekly aggregated counts (one row per device-week) |

### Shared table

`machine_health_feature_windows` is the **single shared foundation** that both features consume. This avoids the architectural mistake of separate aggregation paths.

---

## 34. API Endpoints and Response Behavior

### Risk Assessment endpoint

```
GET /api/v1/devices/{device_id}/degradation-score
```

Optional query parameter: `include_trend_contributions=true` to include per-point signal contributions in trend data.

#### Response states

| Condition | `available` | `state` | Score Included |
|---|---|---|---|
| No latest row exists | `false` | `unavailable` | No |
| Status is `learning` or `insufficient_signals` | `false` | `learning` | No |
| Confidence < 0.3 | `false` | `learning` | No |
| Score computed but older than stale threshold | `true` | `stale` | Yes |
| Score current and usable | `true` | `scored` | Yes |

#### Response payload (when available)

```json
{
  "device_id": "AD00000001",
  "available": true,
  "state": "scored",
  "score": 1.6,
  "status": "healthy",
  "confidence": 0.94,
  "signal_completeness": 0.96,
  "baseline_quality": "high",
  "top_reasons": ["Current variability above baseline", "..."],
  "contributions": [
    {
      "signal": "current_variability_drift",
      "weight": 0.25,
      "drift": 0.12,
      "available": true,
      "observed_value": 1.23,
      "baseline_value": 1.10,
      "raw_drift": 0.118
    }
  ],
  "score_trend": [
    {
      "computed_at": "2026-06-03T12:00:00Z",
      "score": 1.5,
      "status": "healthy"
    }
  ],
  "computed_at": "2026-06-03T12:30:00Z",
  "updated_minutes_ago": 5.2
}
```

### Anomaly Activity endpoint

```
GET /api/v1/devices/{device_id}/anomaly-activity
```

#### Response states

| Condition | `available` | `state` | Baseline Status |
|---|---|---|---|
| No baselines exist at all | `false` | `unavailable` | `none` |
| < 3 active baselines | `false` | `learning` | `candidate` or `partial` |
| Active baselines exist, counts current | `true` | `available` | `active` |
| Aggregated data older than stale threshold | `true` | `stale` | `active` |

#### Response payload (when available)

```json
{
  "device_id": "AD00000001",
  "available": true,
  "state": "available",
  "today_counts": {
    "total": 3, "mild": 2, "strong": 1, "severe": 0, "supply_related": 0
  },
  "this_week_counts": {
    "total": 12, "mild": 8, "strong": 3, "severe": 1, "supply_related": 0
  },
  "this_month_counts": {
    "total": 41, "mild": 28, "strong": 11, "severe": 2, "supply_related": 0
  },
  "week_over_week_change": 5,
  "top_signal": "current_avg",
  "avg_confidence": 0.89,
  "last_anomaly": {
    "occurred_at": "2026-06-03T11:22:00Z",
    "signal_field": "power_factor",
    "severity": "strong",
    "anomaly_type": "deviation",
    "supply_related": false,
    "signal_value": 0.72,
    "baseline_mean": 0.85,
    "z_score": -3.8,
    "duration_seconds": 300,
    "ended_at": "2026-06-03T11:27:00Z",
    "confidence": 0.94,
    "startup_adjacent": false,
    "mode_change": false,
    "recurring": true
  },
  "baseline_status": "active",
  "baseline_field_count": 5,
  "baseline_quality": "high",
  "computed_at": "2026-06-03T12:00:00Z",
  "updated_minutes_ago": 3.5,
  "signal_breakdown": [
    {"field_name": "current_avg", "count": 5, "mild": 3, "strong": 2, "severe": 0}
  ],
  "baseline_signals": [
    {"field_name": "current_avg", "status": "active", "quality_score": 0.95}
  ]
}
```

### Month counts

Monthly counts are **not stored in a separate table**. They are computed on-the-fly from daily counts during the API call:

```sql
SELECT SUM(total_count), SUM(mild_count), ...
FROM machine_anomaly_daily_counts
WHERE date >= month_start AND date <= today
```

This keeps the table count small while still providing fast monthly rollups.

---

## 35. Staleness Detection

Both endpoints check staleness using the same threshold:

```
DEGRADATION_STALE_THRESHOLD_MINUTES = 60
```

If the latest computation is older than 60 minutes, the response `state` becomes `stale`.

Calculation:
```
updated_minutes_ago = (now_utc - computed_at).total_seconds() / 60.0
if updated_minutes_ago > 60:
    stale = True
    state = "stale"
```

The UI can use this to show "Last updated X min ago" and optionally indicate staleness.

---

## 36. Cleanup and Retention

### Risk Assessment cleanup

Deletes feature windows and history rows older than:

```
DEGRADATION_RETENTION_DAYS = 90
```

Cascading delete on both `MachineHealthFeatureWindow` and `MachineHealthHistory`.

### Anomaly Activity cleanup

| Data Type | Retention |
|---|---|
| Anomaly events | `ANOMALY_RETENTION_DAYS` (default 90 days) |
| Daily counts | 365 days (hardcoded) |
| Weekly counts | 730 days (hardcoded) |
| Retired baselines | 180 days (hardcoded) |
| Candidate baselines with quality < 0.1 | 30 days (hardcoded) |

The last rule prevents accumulation of useless candidate baselines from devices that briefly sent telemetry but never established a real baseline.

---

## 37. Configuration Defaults

All configuration is in `services/device-service/app/config.py`:

### Risk Assessment

| Key | Default | Meaning |
|---|---|---|
| `DEGRADATION_ENABLED` | `False` | Must be explicitly enabled |
| `DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS` | `300` | 5-minute windows |
| `DEGRADATION_BASELINE_INTERVAL_SECONDS` | `7200` | Baseline refresh every 2 hours |
| `DEGRADATION_SCORING_INTERVAL_SECONDS` | `1800` | Score every 30 minutes |
| `DEGRADATION_RETENTION_DAYS` | `90` | Keep history 90 days |
| `DEGRADATION_CLEANUP_INTERVAL_SECONDS` | `7200` | Cleanup every 2 hours |
| `DEGRADATION_STALE_THRESHOLD_MINUTES` | `60` | Mark stale after 1 hour |
| `DEGRADATION_BASELINE_MINIMUM_DAYS` | `7` | Require 7 days for baseline |

### Anomaly Activity

| Key | Default | Meaning |
|---|---|---|
| `ANOMALY_ENABLED` | `False` | Must be explicitly enabled |
| `ANOMALY_DETECTION_INTERVAL_SECONDS` | `1800` | Detection every 30 minutes |
| `ANOMALY_BASELINE_INTERVAL_SECONDS` | `7200` | Baseline refresh every 2 hours |
| `ANOMALY_DAILY_AGGREGATION_INTERVAL_SECONDS` | `3600` | Daily aggregation every hour |
| `ANOMALY_WEEKLY_AGGREGATION_INTERVAL_SECONDS` | `21600` | Weekly aggregation every 6 hours |
| `ANOMALY_RETENTION_DAYS` | `90` | Keep events 90 days |
| `ANOMALY_CLEANUP_INTERVAL_SECONDS` | `7200` | Cleanup every 2 hours |
| `ANOMALY_MAX_OPEN_EVENT_AGE_HOURS` | `24` | Auto-close open events after 24 hours |
| `ANOMALY_BASELINE_MINIMUM_DAYS` | `7` | Require 7 days for baseline |

### Open event auto-closure

The detection job also closes **stale open events** — events with `ended_at = None` that are older than `ANOMALY_MAX_OPEN_EVENT_AGE_HOURS`:

```
cutoff = now_utc - timedelta(hours=24)
For each open event where occurred_at < cutoff:
    ended_at = cutoff
    duration_seconds = (cutoff - occurred_at).total_seconds()
```

This prevents events from staying "open" indefinitely if the detector never produces a matching merge candidate.

---

## 38. Example: Reading the Widget at 94% Confidence

### Risk Assessment example

If the UI shows:

```
Risk Assessment
1.6 / 10
Healthy
Confidence: 94%
Updated 5 min ago
```

What this means internally:

1. The baseline was learned from 7+ days of STEADY_RUNNING windows with high quality
2. `baseline_quality_score ≈ 0.98` and `signal_completeness ≈ 0.96`
3. `confidence = 0.96 × 0.98 = 0.94` → displayed as 94%
4. All 5 signals had very small drift from baseline
5. The weighted total was very low, producing `score ≈ 1.6`
6. Since 1.6 < 3.0, status = `healthy`
7. Since baseline_quality > 0.3 and confidence > 0.3, the API returns `available=True, state="scored"`

### Anomaly Activity example

If the UI shows:

```
Anomaly Activity
No anomalies today
This Week: 88
This Month: 88
Last anomaly: Strong Power factor
Confidence: 94%
```

What this means internally:

1. Today's daily count has `total = 0` (no events on the local calendar date)
2. The weekly count shows 88 events in the current ISO week
3. Monthly count is computed from daily counts for the current month
4. The most recent event (by `occurred_at`) was a `strong` anomaly on `power_factor`
5. That event's confidence was computed as:
   - `quality_band = "high"` → multiplier = 1.0
   - `field_quality_score ≈ 0.94`
   - Possibly with cross-field boost (× 1.2) → clamped
   - Result ≈ 94%
6. The 88 events this week but 0 today means the machine was more active earlier in the week

---

## 39. What These Features Are Not

These features are **not**:

- Exact remaining useful life prediction
- Exact failure-date prediction
- A replacement for root-cause diagnostics
- A pure ML black box
- A guarantee of anything

They **are**:

- Explainable telemetry-based machine condition intelligence
- Machine-specific baseline comparison
- Conservative enough for operator dashboards
- Every score can be traced to the exact signals that contributed

---

## 40. Stakeholder Summary

### Risk Assessment — One-Paragraph Explanation

Every 5 minutes, Shivex summarizes each machine's telemetry into a compact behavior window. Over at least 7 days, it learns what normal steady operation looks like for that specific machine. The risk score compares current behavior against that learned baseline across five explainable health dimensions: current stability (25% weight), power factor (25%), power draw (20%), phase imbalance (15%), and recent worsening trend (15%). Those signals are weighted into a 1-to-10 score, and a separate confidence value tells us how trustworthy the score is based on baseline maturity and signal completeness. A score of 1.6 with 94% confidence means the machine is operating very close to its learned normal behavior and we have high trust in that assessment.

### Anomaly Activity — One-Paragraph Explanation

Anomaly Activity continuously checks five core signals (current, power, power factor, voltage, and phase imbalance) against the machine's own learned baseline using statistical z-score methods. A z-score of 2.0 or above triggers a mild anomaly candidate; 3.0 or above is strong; 4.0 or above is severe. But the system does not raise on every blip — it applies confirmation rules (consecutive windows, cross-field support), merges adjacent events into single entries, and explicitly avoids blaming the machine for supply voltage issues or startup transients. Events are counted into daily and weekly totals with a clear policy: supply-related and startup-adjacent events count toward the total but not toward severity buckets, so the machine is not unfairly penalized for external factors. The 94% confidence on an anomaly means the baseline quality and data evidence strongly support that this is a real abnormality, not noise.

---

## 41. Source Code Reference Files

Every formula and threshold in this document was traced from these actual source files:

| File | Purpose |
|---|---|
| `services/device-service/app/services/degradation/types.py` | Data types (TelemetrySample, FeatureWindowInput, BaselineInput, ScoreResult, Contribution, etc.) |
| `services/device-service/app/services/degradation/feature_aggregator.py` | Feature window creation, running-state classifier, phase/voltage imbalance computation |
| `services/device-service/app/services/degradation/baseline_learner.py` | Degradation baseline learning, quality formula, temporal span penalty |
| `services/device-service/app/services/degradation/scorer.py` | Score calculation, drift computation, trend worsening, confidence, status bands, top reasons |
| `services/device-service/app/services/degradation/service.py` | Orchestration: load windows, load baseline, score device, persist snapshots, cleanup |
| `services/device-service/app/services/anomaly/types.py` | Data types (AnomalyFieldBaseline, AnomalyCandidate, DailyCountResult, WeeklyCountResult, SUPPORTED_FIELDS, etc.) |
| `services/device-service/app/services/anomaly/baseline_learner.py` | Per-field anomaly baseline learning, MAD computation, quality formula, active/candidate logic |
| `services/device-service/app/services/anomaly/detector.py` | z-score computation, direction rules, severity thresholds, confirmation, merging, trend detection, supply-related heuristic |
| `services/device-service/app/services/anomaly/aggregator.py` | Daily and weekly count aggregation, counting policy |
| `services/device-service/app/services/anomaly/service.py` | Orchestration: load baselines, detect anomalies, persist events, aggregate counts, cleanup, baseline churn control |
| `services/device-service/app/services/anomaly/helpers.py` | Dict-building helpers for ORM persistence |
| `services/device-service/app/services/anomaly/tz.py` | Platform timezone helpers (IST-aware date calculations) |
| `services/device-service/app/scheduler_runner.py` | Background job scheduling, intervals, per-device iteration, nested transactions |
| `services/device-service/app/api/v1/devices.py` | API endpoints: `/degradation-score` and `/anomaly-activity`, response states, staleness detection |
| `services/device-service/app/config.py` | All configurable defaults and feature toggles |
