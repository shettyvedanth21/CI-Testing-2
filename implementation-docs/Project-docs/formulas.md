# FactoryOPS Formulas and Calculations Documentation

This document provides comprehensive details on all calculation formulas, algorithms, and methodologies used in the FactoryOPS system. This is critical for production deployment verification.

---

# SECTION 1: Device-Level Metrics (Machine Health)

## 1.1 Health Score Calculation

### Purpose
Real-time machine health assessment based on configured parameter thresholds and weights.

### Data Flow
```
TELEMETRY → HealthConfigService → Parameter Score → Weighted Sum → Final Health Score
```

### Formula

#### Step 1: Raw Score Calculation for Each Parameter

The system uses three discrete scoring zones based on where the value falls relative to the configured normal range:

**Case 1: Inside Normal Range (normal_min ≤ value ≤ normal_max)**
```
parameter_score = 100
```
- Full parameter score

**Case 2: Near the Normal Range**
```
if (normal_min - normal_min × 0.15) ≤ value < normal_min:
    parameter_score = 50
elif normal_max < value ≤ (normal_max + normal_max × 0.15):
    parameter_score = 50
```
- Half parameter score
- Fixed 15% tolerance outside each normal boundary

**Case 3: Outside Tolerance**
```
parameter_score = 0
```
- No parameter score

#### Step 2: Weighted Score Calculation
```
health_score = Σ(parameter_score_i × (weight_i / 100))

Where:
- parameter_score_i ∈ {100, 50, 0}
- weight_i = configured weight for parameter i expressed as a percentage
- All configured weights must sum to 100% (validated by system)
- Parameters with is_active=False are excluded
```

### Machine-State Eligibility

- Health scoring runs for `RUNNING`, `IDLE`, and `UNLOAD`
- Health scoring returns standby / no score for `OFF` and `POWER CUT`

### Status Thresholds

| Health Score | Status | Color | Description |
|-------------|--------|-------|-------------|
| ≥ 90 | Excellent | 🟢 | Optimal operating condition |
| 75-89 | Good | 🟡 | Normal operation with minor deviations |
| 50-74 | At Risk | 🟠 | Attention needed, potential issues |
| < 50 | Critical | 🔴 | Immediate action required |

### Parameter Configuration Requirements

| Field | Type | Description | Required |
|-------|------|-------------|----------|
| parameter_name | string | Name matching telemetry field | Yes |
| normal_min | float | Normal operating minimum | Yes |
| normal_max | float | Normal operating maximum | Yes |
| weight | float | Contribution to final score (must sum to 100) | Yes |
| ignore_zero_value | boolean | Skip calculation if value is 0 | No |
| is_active | boolean | Include in health calculation | Yes |

### Example Calculation

```
Configuration:
- Temperature: normal_min=60, normal_max=80, weight=40%
- Vibration: normal_min=0, normal_max=5, weight=30%
- Current: normal_min=10, normal_max=20, weight=30%

Telemetry:
- Temperature = 85°C
- Vibration = 3mm/s
- Current = 15A

Calculation:
- Temperature: value=85 > normal_max(80)
  upper tolerance boundary = 80 + (80 × 0.15) = 92
  parameter_score = 50

- Vibration: value=3 within [0,5]
  parameter_score = 100

- Current: value=15 within [10,20]
  parameter_score = 100

Final Health Score:
health_score = (50 × 0.40) + (100 × 0.30) + (100 × 0.30)
             = 20 + 30 + 30
             = 80 → Status: "Good"
```

---

## 1.2 Uptime Calculation

### Purpose
Calculate actual machine running time within scheduled shift windows.

### Data Flow
```
TELEMETRY → ShiftService → Running Detection → Time Aggregation → Uptime %
```

### Running Detection Logic

A sample is considered "running" if ANY of the following conditions are true:
```
condition_1 = power > 0
condition_2 = (current > 0 AND voltage > 0)

is_running = condition_1 OR condition_2
```

### Uptime Formula
```
uptime_percentage = (actual_running_minutes / effective_runtime_minutes) × 100

Where:
- actual_running_minutes = Sum of time intervals where is_running = TRUE
- effective_runtime_minutes = planned_minutes - maintenance_break_minutes
```

### Shift Configuration

| Field | Type | Description |
|-------|------|-------------|
| shift_name | string | Human-readable shift name |
| shift_start | time | Shift start time (HH:MM) |
| shift_end | time | Shift end time (HH:MM) |
| day_of_week | integer | 0=Monday, 6=Sunday (null = all days) |
| maintenance_break_minutes | integer | Break time deducted from uptime |
| is_active | boolean | Include in uptime calculation |

### Data Quality Assessment

```
data_coverage_pct = (covered_seconds / window_seconds) × 100

Coverage thresholds:
- ≥ 80%: "high" quality
- 40-79%: "medium" quality
- < 40%: "low" quality
```

### Example Calculation

```
Shift Configuration:
- Shift: 09:00 - 18:00
- Maintenance break: 60 minutes
- Planned minutes = (18-09) × 60 = 540 minutes
- Effective minutes = 540 - 60 = 480 minutes

Telemetry Data (09:00 - 18:00):
- 09:00-09:15: Running (power=5kW)
- 09:15-09:45: Stopped (power=0)
- 09:45-12:00: Running (power=6kW)
- 12:00-12:30: Running (power=5.5kW)
- 12:30-13:30: Stopped (break)
- 13:30-18:00: Running (power=6kW)

Actual Running:
- 09:00-09:15: 15 min
- 09:45-12:00: 135 min
- 12:00-12:30: 30 min
- 13:30-18:00: 270 min
Total = 450 minutes

Uptime = (450 / 480) × 100 = 93.75%
```

---

## 1.3 Energy & Loss Calculations

### Purpose
Calculate energy consumption, identify waste categories, and compute associated costs.

### Data Flow
```
TELEMETRY → Sample Aggregation → Loss Category Classification → Cost Calculation
```

### Energy Calculation Formula
```
For each telemetry sample:
  interval_hours = (timestamp_next - timestamp_current) / 3600
  energy_kwh = max(0, power_kw × interval_hours)

Total Energy = Σ(energy_kwh for all samples)
```

### Loss Categories

#### 1. Idle Energy (During Shift Hours)
```
Condition: 
  - inside_shift = TRUE
  - current ≤ idle_current_threshold
  - power > 0

idle_kwh = Σ(energy_kwh where condition is TRUE)
```

#### 2. Off-Hours Energy (Outside Shift Hours)
```
Condition:
  - inside_shift = FALSE
  - power > 0 OR (current > 0 AND voltage > 0)

off_hours_kwh = Σ(energy_kwh where condition is TRUE)
```

#### 3. Overconsumption Energy
```
Condition:
  - current > overconsumption_current_threshold_a

Calculation Method A (power available):
  ratio = min(1.0, max(0.0, (current - threshold) / current))
  over_kwh = interval_energy × ratio

Calculation Method B (power derived):
  over_power_kw = ((current - threshold) × voltage × power_factor) / 1000
  over_kwh = min(interval_energy, over_power_kw × interval_hours)

overconsumption_kwh = Σ(over_kwh for all samples where condition is TRUE)
```
```
Overconsumption - overconsumption is the excess energy a machine consumes beyond its expected (baseline) consumption for a given operating state.
Formula
E_over = E_actual − E_baseline
Symbol	Meaning
E_actual	Real energy consumed (from CT sensor readings)
E_baseline	Expected energy for that state & duration
E_baseline = P_baseline × T_operating state

Symbol	Meaning
P_baseline(state)	Percentile power for each state
T(state)	        Time spent in that state
T_total	            Total operating time
 
Example
State	    P_baseline	Duration	Energy
RUNNING	    25 kW	    6 hrs	    150 kWh
IDLE	    5 kW	    2 hrs	    10 kWh
Total		            8 hrs	    160 kWh
E_baseline = 160 kWh
 
Overconsumption
E_over = E_actual − E_baseline
If your CT sensors recorded 195 kWh actual:
E_over = 195 − 160 = 35 kWh wasted

How P_baseline is Established
    - Statistical Baseline (from historical sensor data)
    - P_baseline(state) = Percentile(P_measured, 50th or 75th)
    - Computed per machine, per state (RUNNING, IDLE) over a rolling 30-day window.
    - Which percentile to use?
        Percentile	    Use When	                        Effect
        50th (Median)	Anomaly detection, alerts	        Flags more deviations
        75th	        Conservative baseline, ROI claims	Fewer false positives
    - E.g.
        import numpy as np
        data = [12, 45, 67, ..., 89]  # 1440 values
        p75 = np.percentile(data, 75)
        print("P75:", p75)
    - This needs to be done for 30 days data to calculate the final P75th percentile for Power across each of the state

    import numpy as np
    
    # Example: 1440 minute-level datapoints
    data = np.random.rand(1440) * 100  # replace with your actual data
    
    # Calculate 75th percentile
    p75 = np.percentile(data, 75)
    
    print("P75:", p75)
```


### Total Loss Formula
```
total_loss_kwh = idle_kwh + off_hours_kwh + overconsumption_kwh
```

### Cost Calculation
```
energy_cost = total_energy_kwh × tariff_rate
loss_cost = total_loss_kwh × tariff_rate

```

### Example Calculation

```
Device Configuration:
- idle_current_threshold = 5.0 A
- overconsumption_current_threshold_a = 25.0 A

Tariff Rate: 8.00 INR/kWh

Time Period: 1 hour (10:00-11:00, during shift)

Telemetry:
| Time   | Power (kW) | Current (A) | Voltage (V) | PF  |
|--------|------------|-------------|-------------|-----|
| 10:00  | 2.0        | 4.0         | 230         | 0.85|
| 10:15  | 2.5        | 5.5         | 230         | 0.85|
| 10:30  | 8.0        | 20.0        | 230         | 0.85|
| 10:45  | 12.0       | 30.0        | 230         | 0.85|

Calculations:
- 10:00-10:15: power=2kW, current=4A ≤ 5A (idle threshold)
  - interval = 0.25 hours
  - energy = 2.0 × 0.25 = 0.5 kWh
  - IDLE: 0.5 kWh ✓

- 10:15-10:30: power=8kW, current=20A > 5A (idle), < 25A (over)
  - interval = 0.25 hours
  - energy = 8.0 × 0.25 = 2.0 kWh
  - RUNNING: 2.0 kWh ✓

- 10:30-10:45: power=12kW, current=30A > 25A (overconsumption)
  - interval = 0.25 hours
  - energy = 12.0 × 0.25 = 3.0 kWh
  - over_ratio = (30-25)/25 = 0.2
  - overconsumption = 3.0 × 0.2 = 0.6 kWh
  - remaining = 3.0 - 0.6 = 2.4 kWh (running)

Summary:
- Total Energy: 0.5 + 2.0 + 3.0 = 5.5 kWh
- Idle: 0.5 kWh
- Running: 2.0 + 2.5 = 4.5 kWh
- Overconsumption: 0.6 kWh
- Total Loss: 0.5 + 0.6 = 1.1 kWh
- Loss Cost: 1.1 × 8.00 = 8.80 INR
```

---

# SECTION 2: Energy & Cost Analytics (Reporting Service)

## 2.1 Energy Calculation

### Purpose
Calculate total energy consumption from raw telemetry with multiple derivation modes.

### Data Flow
```
TELEMETRY → Mode Detection → Energy Calculation → Aggregation
```

### Calculation Modes

#### Mode 1: Direct Power Available
```
If "power" field exists in telemetry:

For each sample pair (i-1, i):
  delta_seconds = timestamp_i - timestamp_{i-1}
  avg_power_w = (power_{i-1} + power_i) / 2
  energy_wh = avg_power_w × delta_seconds / 3600
  total_wh += energy_wh

total_kwh = total_wh / 1000
```

#### Mode 2: Derived from Voltage, Current, Power Factor
```
Single Phase:
  power_w = voltage × current × power_factor

Three Phase:
  power_w = √3 × voltage × current × power_factor
  (where √3 = 1.73205080757)

Energy calculation same as Mode 1 using derived power.
```

### Derived Power Formula Derivation

```
Single Phase Power:
P = V × I × PF

Where:
- P = Active Power (Watts)
- V = Voltage (Volts)
- I = Current (Amperes)
- PF = Power Factor (dimensionless, 0-1)

Three Phase Power:
P = √3 × V_L × I_L × PF

Where:
- V_L = Line Voltage
- I_L = Line Current
- √3 = Square root of 3 (geometric factor for 3-phase)
```

### Output Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| total_kwh | total_wh / 1000 | kWh |
| avg_power_w | sum(powers) / count | Watts |
| peak_power_w | max(powers) | Watts |
| min_power_w | min(powers) | Watts |
| duration_hours | (last_ts - first_ts) / 3600 | Hours |

---

## 2.2 Cost Calculation

### Purpose
Calculate total energy cost including multiple charge components.

### Data Flow
```
ENERGY + DEMAND + REACTIVE + TARIFF → COST BREAKDOWN
```

### Tariff Configuration

| Field | Type | Description |
|-------|------|-------------|
| energy_rate_per_kwh | float | Cost per kWh (INR) |
| demand_charge_per_kw | float | Cost per kW of peak demand |
| reactive_penalty_rate | float | Cost per kVARh (reactive) |
| fixed_monthly_charge | float | Monthly fixed cost |
| power_factor_threshold | float | PF below which penalty applies (default 0.90) |
| currency | string | Currency code (default INR) |

### Cost Components

#### Energy Cost
```
energy_cost = total_kwh × energy_rate_per_kwh
```

#### Demand Cost
```
demand_cost = peak_demand_kw × demand_charge_per_kw
```

#### Reactive Penalty
```
if avg_power_factor < power_factor_threshold:
    reactive_penalty = total_kvarh × reactive_penalty_rate
else:
    reactive_penalty = 0
```

#### Fixed Charge (Prorated)
```
fixed_charge = fixed_monthly × (duration_days / 30)
```

#### Total Cost
```
total_cost = energy_cost + demand_cost + reactive_penalty + fixed_charge
```

### Example Calculation

```
Tariff Configuration:
- energy_rate_per_kwh = 8.00 INR
- demand_charge_per_kw = 150.00 INR
- reactive_penalty_rate = 1.50 INR/kVARh
- fixed_monthly_charge = 500.00 INR
- power_factor_threshold = 0.90

Device Data:
- total_kwh = 1000 kWh
- peak_demand_kw = 50 kW
- total_kvarh = 200 kVARh
- avg_power_factor = 0.85
- duration_days = 30

Calculations:
- energy_cost = 1000 × 8.00 = 8,000.00 INR
- demand_cost = 50 × 150.00 = 7,500.00 INR
- reactive_penalty = 0.85 < 0.90 → 200 × 1.50 = 300.00 INR
- fixed_charge = 500 × (30/30) = 500.00 INR

TOTAL COST = 8,000 + 7,500 + 300 + 500 = 16,300.00 INR
```

---

## 2.3 Demand Calculation

### Purpose
Calculate peak demand using sliding window methodology.

### Data Flow
```
POWER SERIES → WINDOW SPLITTING → AVERAGE CALCULATION → PEAK IDENTIFICATION
```

### Formula
```
window_seconds = window_minutes × 60

For each time window [window_start, window_end):
  window_power = [all power_w values in window]
  avg_power_kw = sum(window_power) / count(window_power) / 1000
  
  Store (window_start, avg_power_kw)

peak_demand_kw = max(all avg_power_kw values)
peak_timestamp = window_start corresponding to peak_demand_kw
```

### Default Parameters
- Window: 15 minutes (industry standard)
- Minimum data points: 1 per window (uses single point if only one available)

### Output

| Metric | Description |
|--------|-------------|
| peak_demand_kw | Maximum demand in any window |
| peak_demand_timestamp | When peak occurred |
| demand_window_minutes | Window size used |
| top_5_windows | Peak windows for analysis |
| all_window_averages | Full demand profile |

---

## 2.4 Load Factor

### Purpose
Measure efficiency of power utilization - ratio of average to peak demand.

### Formula
```
avg_load_kw = total_kwh / duration_hours

load_factor = avg_load_kw / peak_demand_kw

load_factor = clamp(load_factor, 0, 1)
```

### Classification

| Load Factor | Classification | Color | Recommendation |
|-------------|----------------|-------|----------------|
| ≥ 0.75 | Good | Green | Continuous efficient operation |
| 0.50-0.75 | Moderate | Yellow | Consider load balancing |
| < 0.50 | Poor | Red | Significant demand peaks |

### Interpretation
- **1.0 (100%)**: Perfect efficiency - always running at peak capacity
- **0.75**: Good - minimal waste
- **0.50**: Moderate - some inefficiency
- **0.25**: Poor - significant capacity sitting idle

---

## 2.5 Power Quality Metrics

### Purpose
Analyze electrical quality parameters for compliance and equipment health.

### Voltage Analysis
```
mean_voltage = sum(voltages) / count(voltages)

variance = Σ(v - mean_voltage)² / count(voltages)
std_voltage = √variance

nominal = mean_voltage

outside_count = count where |v - nominal| / nominal > 0.10
outside_pct = (outside_count / count) × 100
```

### Frequency Analysis
```
mean_frequency = sum(frequencies) / count(frequencies)

outside_count = count where |f - 50.0| > 0.5
outside_pct = (outside_count / count) × 100
```

### THD (Total Harmonic Distortion) Analysis
```
mean_thd = sum(thd_values) / count(thd_values)

above_count = count where thd > 5.0
above_pct = (above_count / count) × 100
```

### Thresholds Summary

| Parameter | Normal Range | Warning Threshold |
|-----------|--------------|-------------------|
| Voltage | ±10% of nominal | >10% outside range |
| Frequency | 50 ± 0.5 Hz | >0.5 Hz deviation |
| THD | < 5% | >5% |

---

# SECTION 3: ML-Based Anomaly Detection

## 3.1 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ANOMALY DETECTION PIPELINE                        │
└─────────────────────────────────────────────────────────────────────┘

   RAW TELEMETRY
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DATA PREPARATION                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ 1. Timestamp normalization (UTC)                                ││
│  │ 2. Numeric columns extraction                                  ││
│  │ 3. Resample to 1-minute intervals                              ││
│  │ 4. Forward/backward fill (max 15 min gaps)                    ││
│  │ 5. Outlier clipping (±5 sigma)                                ││
│  │ 6. Sanitization (inf/nan handling, median fallback)           ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
   TRAIN/TEST SPLIT (80/20)
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│ TRAIN │ │ TEST  │
│  80%  │ │  20%  │
│       │ │       │
└───┬───┘ └───┬───┘
    │         │
    ▼         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 3-MODEL ENSEMBLE                                     │
│                                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐                │
│  │   ISOLATION FOREST   │  │   LSTM AUTOENCODER    │                │
│  │                      │  │                      │                │
│  │ - 200 trees          │  │ - Sequence: 30 steps │                │
│  │ - Contamination:     │  │ - Reconstruction     │                │
│  │   0.01-0.05         │  │   error threshold    │                │
│  │ - StandardScaler     │  │ - StandardScaler     │                │
│  │ - Weight: 40%        │  │ - Weight: 40%        │                │
│  └──────────┬───────────┘  └──────────┬───────────┘                │
│             │                          │                             │
│             └──────────────┬───────────┘                             │
│                            ▼                                        │
│                   ┌────────────────┐                                │
│                   │  CUSUM DETECTOR │                                │
│                   │                  │                                │
│                   │ - Drift detection│                               │
│                   │ - Cumulative sum │                               │
│                   │ - Weight: 20%    │                                │
│                   └────────┬───────┘                                │
│                            ▼                                        │
│                   ┌────────────────┐                                │
│                   │  VOTING ENGINE  │◄──────────────────────────────┘
│                   │  (Weighted Avg) │                              
│                   └────────┬───────┘                              
│                            ▼                                        
│         ┌─────────────────┴─────────────────┐                      
│         │                                   │                      
│         ▼                                   ▼                      
│   ┌──────────────┐                  ┌─────────────────┐            
│   │ANOMALY SCORE │                  │  ANOMALY RESULT │            
│   │ [0.0 - 1.0] │                  │  + SEVERITY      │            
│   └──────────────┘                  │  + REASONING    │            
│                                      └─────────────────┘            
└─────────────────────────────────────────────────────────────────────┘
```

## 3.2 Data Preparation Details

### Step-by-Step Process

#### Step 1: Timestamp Normalization
```
input_df["timestamp"] = pd.to_datetime(timestamp, utc=True, errors="coerce")
# Rename "_time" to "timestamp" if present
```

#### Step 2: Column Selection
```
Exclude columns:
- timestamp, _time
- device_id, schema_version
- enrichment_status, table
- hour, minute, second, day, month, year
- day_of_week, day_of_year, week, week_of_year
- quarter, is_weekend, index

Select only numeric columns for analysis
```

#### Step 3: Resampling
```
resampled = original.set_index("timestamp").resample("1min").mean()
# Aggregates to 1-minute intervals using mean
```

#### Step 4: Gap Filling
```
resampled[columns] = resampled[columns].ffill(limit=15)  # Forward fill
resampled[columns] = resampled[columns].bfill(limit=15)  # Backward fill
# Maximum 15-minute gap filling
```

#### Step 5: Outlier Clipping
```
For each column:
    mean = data[col].mean()
    std = data[col].std()
    lower = mean - 5 × std
    upper = mean + 5 × std
    data[col] = data[col].clip(lower, upper)
```

#### Step 6: Sanitization
```
For each column:
    p01 = data[col].quantile(0.01)
    p99 = data[col].quantile(0.99)
    median = data[col].median()
    
    data[col] = data[col].replace([inf] → p99)
    data[col] = data[col].replace([-inf] → p01)
    data[col] = data[col].fillna(median)
```

## 3.3 Isolation Forest Model

### Algorithm Overview
Isolation Forest is an unsupervised machine learning algorithm that isolates anomalies by randomly selecting features and split values. Anomalies are easier to isolate (shorter path lengths) than normal points.

### Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| n_estimators | 200 | Number of isolation trees |
| contamination | 0.01-0.05 | Expected proportion of anomalies |
| random_state | 42 | Reproducibility seed |
| n_jobs | -1 | Use all CPU cores |

### Contamination Selection (Sensitivity-Based)
```
sensitivity = params.get("sensitivity", "medium")

if sensitivity == "low":
    contamination = min(base_contamination, 0.02)
elif sensitivity == "high":
    contamination = min(0.06, base_contamination + 0.01)
else:  # medium
    contamination = base_contamination

Base contamination by data volume:
- < 10 points: 0.01
- 10-360 points: 0.01
- 360-10080 points: 0.02
- 10080-43200 points: 0.03
- > 43200 points: 0.05
```

### Preprocessing
```
1. StandardScaler fit_transform on training data
2. Clip scaled values to [-10, 10] range
3. Transform test data using same scaler
```

### Scoring
```
# Raw anomaly scores from decision_function
raw_scores = clf.decision_function(X_scaled)
# More negative = more anomalous

# Convert to 0-1 scale (inverted)
inverse_scores = -raw_scores

# Min-max normalization
anomaly_score = (inverse_scores - min) / (max - min + 1e-9)
anomaly_score = clip(anomaly_score, 0.0, 1.0)

# Binary prediction
is_anomaly = predictions == -1
```

## 3.4 LSTM Autoencoder Model

### Algorithm Overview
LSTM Autoencoder learns the normal pattern of sequences and flags instances with high reconstruction error as anomalies. It captures temporal dependencies that Isolation Forest might miss.

### Sequence Building
```
Sequence Length: 30 timesteps

For each numeric column:
    sequence[i] = [value[t-i], value[t-i-1], ..., value[t-30]]
    
Minimum 50 sequences required for training
```

### Architecture (Simplified)
```
Input: (batch_size, 30, num_features)
    ↓
LSTM Encoder (64 units)
    ↓
Latent Representation
    ↓
LSTM Decoder (64 units)
    ↓
Output: (batch_size, 30, num_features)
    ↓
Reconstruction Error = MSE(input, output)
```

### Anomaly Detection
```
reconstruction_error = mean((input - output)²)

If reconstruction_error > threshold:
    is_anomaly = True
Else:
    is_anomaly = False
```

## 3.5 CUSUM Detector

### Algorithm Overview
Cumulative Sum (CUSUM) detects gradual shifts in mean that might not trigger threshold-based alerts but indicate drift.

### Formula
```
For each parameter:
    reference = mean of baseline period
    Slack = k × std_dev
    
    CUSUM_positive[t] = max(0, CUSUM_positive[t-1] + (value[t] - reference - k))
    CUSUM_negative[t] = max(0, CUSUM_negative[t-1] + (reference - value[t] - k))
    
    CUSUM[t] = max(CUSUM_positive[t], CUSUM_negative[t])
    
    If CUSUM[t] > decision_boundary:
        drift_detected = True
```

### Parameters
| Parameter | Value | Description |
|-----------|-------|-------------|
| k | 0.5 | Allowable slack (half of detectable shift) |
| decision_boundary | 5.0 | Threshold for drift detection |

## 3.6 Voting Engine (Anomaly Ensemble)

### Weighted Combination
```
combined_score = (0.40 × isolation_forest_score) 
               + (0.40 × lstm_score) 
               + (0.20 × cusum_score)
```

### Confidence Levels

| Vote Count | Confidence | Description |
|------------|------------|-------------|
| 3/3 models | HIGH | All models agree anomaly |
| 2/3 models | MEDIUM | Strong consensus |
| 1/3 models | LOW | Single model detection |
| 0/3 models | NORMAL | No anomaly detected |

### Severity Classification
```
For each detected anomaly:

if isolation_score >= 0.8:
    severity = "high"
elif isolation_score >= 0.5:
    severity = "medium"
else:
    severity = "low"
```

### Reasoning Generation
```
For each anomaly:
    1. Identify which parameters triggered Z-score > 2.0
    2. Compare current value to normal band (mean ± 2std)
    3. Generate context string:
       - If value > mean: "{param} spike to {value} (normal: {lo}-{hi})"
       - If value < mean: "{param} dropped to {value} (normal: {lo}-{hi})"
```

---

# SECTION 4: ML-Based Failure Prediction

## 4.1 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                  FAILURE PREDICTION PIPELINE                        │
└─────────────────────────────────────────────────────────────────────┘

   RAW TELEMETRY (device_id, timestamp, parameters...)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  FEATURE ENGINEERING                                                │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ Rolling Statistics (5,15,30,60,360 min windows):                ││
│  │   - Rolling Mean: Average in window                             ││
│  │   - Rolling Std: Standard deviation in window                   ││
│  │   - Rolling Max: Peak value in window                          ││
│  │   - Rolling Min: Minimum value in window                        ││
│  │   - Rate of Change: Difference from N minutes ago              ││
│  │   - Rate of Change Absolute: |diff| for magnitude               ││
│  ├─────────────────────────────────────────────────────────────────┤│
│  │ Derived Features:                                               ││
│  │   - Coefficient of Variation: std/mean (volatility)             ││
│  │   - Above P95: Binary flag for extreme high                      ││
│  │   - Below P05: Binary flag for extreme low                       ││
│  │   - Trend Slope: Linear regression over 60 min                  ││
│  ├─────────────────────────────────────────────────────────────────┤│
│  │ Time Features:                                                  ││
│  │   - Hour of Day: Cyclic time identifier                         ││
│  │   - Day of Week: Day identifier                                 ││
│  │   - Is Night Shift: Binary (22:00-06:00)                       ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SYNTHETIC LABEL GENERATION                                         │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ Step 1: Band Violation Detection                                ││
│  │   for each parameter:                                           ││
│  │     p10 = quantile(data, 0.10)                                  ││
│  │     p90 = quantile(data, 0.90)                                  ││
│  │     band_viol[col] = (value < p10) OR (value > p90)             ││
│  │                                                                   ││
│  │   multi_violation = sum(band_viol) >= 2 parameters              ││
│  ├─────────────────────────────────────────────────────────────────┤│
│  │ Step 2: Rate of Change Stress                                   ││
│  │   for each parameter:                                           ││
│  │     roc = diff(value)                                           ││
│  │     roc_stress = roc > quantile(roc, 0.95)                     ││
│  ├─────────────────────────────────────────────────────────────────┤│
│  │ Step 3: Combined Label                                          ││
│  │   labels = (multi_violation OR roc_stress)                       ││
│  │                                                                   ││
│  │   If labels.sum() < 5:                                          ││
│  │     labels.iloc[-10:] = 1  (fallback for sparse data)           ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 3-MODEL ENSEMBLE                                     │
│                                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐                │
│  │      XGBOOST          │  │   LSTM CLASSIFIER    │                │
│  │   (40% weight)       │  │   (40% weight)      │                │
│  │                      │  │                      │                │
│  │ - n_estimators: 200  │  │ - Sequence: 30 steps │                │
│  │ - max_depth: 8       │  │ - Binary classification│             │
│  │ - class_weight: bal. │  │ - Softmax output      │                │
│  │ - Feature importance │  │ - Probability output  │                │
│  └──────────┬───────────┘  └──────────┬───────────┘                │
│             │                          │                             │
│             └──────────────┬───────────┘                             │
│                            ▼                                         │
│                   ┌────────────────┐                                │
│                   │ DEGRADATION    │                                │
│                   │ TRACKER        │                                │
│                   │ (20% weight)  │                                │
│                   │                │                                │
│                   │ - Trend fitting│                                │
│                   │ - TTF estimate │                                │
│                   └────────┬───────┘                                │
│                            ▼                                        │
│                   ┌────────────────┐                                │
│                   │  VOTING ENGINE  │◄──────────────────────────────┘
│                   │  (Weighted Avg) │                              
│                   └────────┬───────┘                              
│                            ▼                                        
│         ┌─────────────────┴─────────────────┐                      
│         │                                   │                      
│         ▼                                   ▼                      
│   ┌──────────────┐                  ┌─────────────────┐            
│   │FAILURE       │                  │  RISK FACTORS   │            
│   │PROBABILITY   │                  │  + TRENDS       │            
│   │[0.0 - 1.0]  │                  │  + CONTEXT      │            
│   └──────────────┘                  └─────────────────┘            
│         │                                                      
│         ▼                                                      
│   ┌──────────────┐                                            
│   │TIME TO       │                                            
│   │FAILURE (hrs) │                                            
│   └──────────────┘                                            
└─────────────────────────────────────────────────────────────────────┘
```

## 4.2 Synthetic Label Generation Logic

### Purpose
Since historical failure data is often sparse or unavailable, the system generates synthetic failure labels based on stress indicators in the telemetry data.

### Algorithm

#### Step 1: Band Violation Detection
```
For each numeric parameter:
    p10 = data[col].quantile(0.10)   # 10th percentile
    p90 = data[col].quantile(0.90)   # 90th percentile
    
    # Flag values outside normal operating band
    band_viol[col] = (data[col] < p10) OR (data[col] > p90)

# Multi-parameter violation (at least 2 parameters outside normal)
multi_violation = band_viol.sum(axis=1) >= 2
```

#### Step 2: Rate of Change Stress
```
For each numeric parameter:
    # Rate of change from previous reading
    roc = data[col].diff().abs().fillna(0)
    
    # Stress if ROC is in top 5% of all ROC values
    roc_threshold = roc.quantile(0.95)
    roc_stress = roc > roc_threshold

# Combined stress indicator
stress_indicator = multi_violation OR roc_stress
```

#### Step 3: Final Label
```
labels = stress_indicator.astype(int)

# Fallback: If very few labels, mark last 10 points as failure
# This ensures model has some positive examples to learn from
if labels.sum() < 5:
    labels.iloc[-min(10, len(labels)):] = 1
```

### Why This Approach?

| Trigger | Rationale |
|---------|-----------|
| Band violation (P10/P90) | Captures parameters operating outside normal range |
| Multi-parameter | Real failures usually affect multiple systems |
| Rate of change | Sudden shifts indicate potential problems |
| ROC threshold (95th) | Top 5% represents significant changes |

## 4.3 Feature Engineering Details

### Rolling Statistics Features

| Feature Suffix | Windows (minutes) | Description |
|---------------|-------------------|-------------|
| _mean_Xm | 5, 15, 30, 60, 360 | Rolling average over X minutes |
| _std_Xm | 5, 15, 30, 60, 360 | Rolling standard deviation |
| _max_Xm | 5, 15, 30, 60, 360 | Rolling maximum |
| _min_Xm | 5, 15, 30, 60, 360 | Rolling minimum |
| _roc_Xm | 5, 15, 30, 60, 360 | Rate of change (current - X min ago) |
| _roc_abs_Xm | 5, 15, 30, 60, 360 | Absolute rate of change |

### Derived Features

| Feature | Formula | Purpose |
|---------|---------|---------|
| _cv_60m | std / (mean + 1e-9) | Coefficient of Variation - measures volatility |
| _above_p95 | (value > p95).astype(int) | Binary flag for extreme high |
| _below_p05 | (value < p05).astype(int) | Binary flag for extreme low |
| _trend_60m | Linear regression slope | Direction of change over 1 hour |

### Time-Based Features

| Feature | Formula | Purpose |
|---------|---------|---------|
| hour_of_day | index.hour | Capture daily operational patterns |
| day_of_week | index.dayofweek | Weekly patterns |
| is_night_shift | (hour >= 22) OR (hour < 6) | Night vs day operation |

### Feature Count Calculation
```
For 1 parameter: 5 windows × 6 features + 4 derived + 3 time = 37 features
For N parameters: N × 37 + 3 time features
```

### Example Feature Values

```
Raw telemetry:
- temperature: [60, 62, 65, 70, 75, 80, 85, 90, 92, 95]

Computed features (window=5):
- temperature_mean_5m: [62.0, 63.5, 67.0, 72.5, 78.0, 82.5, 87.0, 92.0, ...]
- temperature_std_5m: [1.41, 1.87, 2.16, 2.38, 2.58, 2.50, 2.45, 2.44, ...]
- temperature_roc_5m: [2.0, 3.0, 5.0, 5.0, 5.0, 5.0, 5.0, 3.0, ...]
- temperature_cv_60m: (computed over 60-point window)
```

## 4.4 XGBoost Classifier

### Algorithm Overview
XGBoost is a gradient boosting algorithm that builds decision trees sequentially, where each tree corrects the errors of previous trees. It provides feature importance rankings and probability outputs.

### Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| n_estimators | 200 (default, configurable) | Number of trees |
| max_depth | 8 (default, configurable) | Maximum tree depth |
| class_weight | balanced | Handle imbalanced classes |
| random_state | 42 | Reproducibility |
| n_jobs | -1 | Use all CPU cores |

### Training
```
1. Build features using FeatureEngineer
2. Generate synthetic labels
3. StandardScaler transform
4. Clip values to [-10, 10]
5. Fit XGBoost on features and labels
```

### Prediction Output
```
probabilities = model.predict_proba(X)[:, 1]  # Probability of failure
predictions = probabilities >= 0.5            # Binary prediction
```

## 4.5 LSTM Classifier

### Algorithm Overview
LSTM (Long Short-Term Memory) classifier captures temporal patterns in the data, learning from sequences of features to predict failure probability.

### Sequence Building
```
Sequence Length: 30 timesteps

For each feature:
    sequence[i] = [feature[t-i], feature[t-i-1], ..., feature[t-30]]
```

### Architecture (Simplified)
```
Input: (batch_size, 30, num_features)
    ↓
LSTM Layer (64 units, return_sequences=True)
    ↓
LSTM Layer (32 units)
    ↓
Dense Layer (16 units, ReLU)
    ↓
Output Layer (2 units, Softmax)  # Binary: normal/failure
    ↓
Probability = softmax_output[1]
```

## 4.6 Degradation Tracker

### Algorithm Overview
Degradation tracker monitors the gradual decline of equipment health by fitting trend lines to rolling health metrics.

### Degradation Score Calculation
```
For each parameter:
    1. Compute rolling mean over 60-minute window
    2. Normalize to 0-1 scale based on historical range
    3. Invert so higher = worse (1 - normalized)
    
degradation_score = mean(all_parameter_scores)
```

### Time-to-Failure Estimation

#### Linear Trend
```
Fit linear regression: score = a × time + b

hours_to_failure = (threshold - b) / a
where threshold = 0.8 (80% degradation)
```

#### Exponential Trend
```
Fit exponential: score = a × e^(b × time)

hours_to_failure = (ln(threshold) - ln(a)) / b
```

### Trend Reliability
```
R² = 1 - (SS_res / SS_tot)

If R² >= 0.3:
    trend_reliable = True
Else:
    trend_reliable = False
```

## 4.7 Voting Engine (Failure Ensemble)

### Weighted Combination
```
combined_probability = 0.40 × xgb_probability 
                     + 0.40 × lstm_probability 
                     + 0.20 × degradation_vote
```

### Voting Logic

#### XGBoost/LSTM Votes
```
# Use 90th percentile of recent predictions
recent_90th = percentile(predictions[-n_recent:], 90)

vote = recent_90th >= 0.50
```

#### Degradation Vote
```
vote = (trend_type in ["linear", "exponential"]) 
       AND (hours_to_failure < 168)  # 7 days
```

#### Combined Verdict
```
total_votes = xgb_vote + lstm_vote + deg_vote

Verdict Mapping:
- 3 votes: CRITICAL
- 2 votes: WARNING  
- 1 vote: WATCH
- 0 votes: NORMAL

Confidence Mapping:
- 3 votes: HIGH
- 2 votes: MEDIUM
- 0-1 votes: LOW
```

### Risk Breakdown
```
safe_pct = mean(probability < 0.30) × 100
warning_pct = mean(0.30 <= probability < 0.70) × 100
critical_pct = mean(probability >= 0.70) × 100
```

## 4.8 Risk Factor Analysis

### Purpose
Identify which parameters contribute most to failure risk.

### Feature Importance Extraction
```
For each feature:
    importance = model.feature_importances_[feature_index]
    
    # Map to original parameters
    if feature.startswith(parameter_name):
        parameter_importance += importance
```

### Trend Analysis
```
Split data into two halves:
- Old half: first 50% of readings
- Recent half: last 50% of readings

For each parameter:
    old_mean = old_half[parameter].mean()
    recent_mean = recent_half[parameter].mean()
    recent_std = recent_half[parameter].std()
    
    coefficient_of_variation = recent_std / (abs(recent_mean) + 1e-9)
    
    if cv > 0.15:
        trend = "erratic"
    elif recent_mean > old_mean × 1.05:
        trend = "increasing"
    elif recent_mean < old_mean × 0.95:
        trend = "decreasing"
    else:
        trend = "stable"
```

### Reasoning Generation
```
Parameter + Trend → Explanation

Examples:
- "temp" + "increasing" → "Rising temperature indicates cooling degradation"
- "vibration" + "increasing" → "Progressive vibration increase is bearing failure precursor"
- "current" + "erratic" → "Erratic current draw suggests electrical issue"
- "power" + "increasing" → "Increasing power consumption indicates inefficiency"
```

---

# SECTION 5: Confidence Scoring Methodology

## 5.1 Data Points to Confidence Mapping

### Purpose
Provide users with reliability indicators for ML results based on data availability.

### Data Volume Thresholds

| Data Points | Time Coverage | Confidence Level | Badge Color | Contamination |
|------------|---------------|------------------|-------------|---------------|
| < 10 | < 10 minutes | **Low** | #DC2626 (Red) | 0.01 |
| 10-360 | 10 min - 6 hours | **Low** | #DC2626 (Red) | 0.01 |
| 360-10080 | 6 hours - 7 days | **Moderate** | #D97706 (Amber) | 0.02 |
| 10080-43200 | 7 - 30 days | **High** | #059669 (Green) | 0.03 |
| > 43200 | > 30 days | **Very High** | #4F46E5 (Indigo) | 0.05 |

### Contamination Parameter

The contamination parameter in Isolation Forest controls the expected proportion of anomalies:

```
contamination = base_contamination × confidence_multiplier

Where confidence_multiplier adjusts based on data volume:
- Low data: Lower contamination (more conservative anomaly detection)
- High data: Higher contamination (trust more data points as normal)
```

### Z-Score Multiplier

Used for determining outlier thresholds:

| Confidence | Z-Score Multiplier | Outlier Threshold |
|------------|-------------------|-------------------|
| Low | 1.5 | mean ± 1.5×std |
| Low | 1.3 | mean ± 1.3×std |
| Moderate | 1.1-1.0 | mean ± 1.1×std to mean ± 1×std |
| High/Very High | 1.0 | mean ± 1×std |

## 5.2 Confidence Output Structure

```json
{
    "level": "High",
    "badge_color": "#059669",
    "contamination": 0.03,
    "zscore_multiplier": 1.0,
    "banner_text": "High confidence: 14 days of data. Results are reliable for maintenance decisions.",
    "banner_style": "green",
    "minutes_available": 20160.0,
    "days_available": 14.0
}
```

## 5.3 Banner Messages by Confidence Level

| Level | Banner Text |
|-------|-------------|
| Low | "Low confidence: only {window} of data. Results are indicative; collect more telemetry." |
| Low | "Low confidence: only {window} of data. Re-run after 6 hours for stronger reliability." |
| Moderate | "Moderate confidence: {window} of data. Re-run after 7 days for high confidence." |
| High | "High confidence: {window} of data. Results are reliable for maintenance decisions." |
| Very High | "Very high confidence: {window} of data. Long-cycle behavior captured for maximum reliability." |

---

# SECTION 6: Parameter Descriptions

## 6.1 Device Telemetry Fields

| Field | Type | Description | Unit |
|-------|------|-------------|------|
| timestamp | datetime | UTC timestamp of reading | ISO8601 |
| device_id | string | Unique device identifier | - |
| power | float | Active power | Watts |
| power_kw | float | Active power | kW |
| current | float | Current draw | Amperes |
| voltage | float | Voltage | Volts |
| power_factor | float | Power factor (0-1) | - |
| frequency | float | Grid frequency | Hz |
| temperature | float | Temperature | °C |
| vibration | float | Vibration level | mm/s |

## 6.2 Health Configuration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| parameter_name | string | Telemetry field name to monitor |
| normal_min | float | Normal operating minimum |
| normal_max | float | Normal operating maximum |
| weight | float | Contribution to final score (%) |
| ignore_zero_value | boolean | Skip zero values |
| is_active | boolean | Include in calculation |

## 6.3 Shift Configuration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| shift_name | string | Human-readable name |
| shift_start | time | Start time (HH:MM) |
| shift_end | time | End time (HH:MM) |
| day_of_week | integer | 0=Monday, 6=Sunday, null=all |
| maintenance_break_minutes | integer | Break time deduction |
| is_active | boolean | Include in uptime |

## 6.4 Tariff Configuration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| energy_rate_per_kwh | float | Cost per kWh |
| demand_charge_per_kw | float | Peak demand charge |
| reactive_penalty_rate | float | Reactive power penalty |
| fixed_monthly_charge | float | Monthly fixed cost |
| power_factor_threshold | float | PF penalty threshold |
| currency | string | Currency code |

## 6.5 ML Model Parameters

### Anomaly Detection

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| sensitivity | string | "medium" | Detection sensitivity (low/medium/high) |
| lookback_days | integer | 7 | Days of historical data to use |
| min_points | integer | 50 | Minimum data points required |

### Failure Prediction

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| sensitivity | string | "medium" | Prediction sensitivity |
| n_estimators | integer | 200 | XGBoost trees |
| max_depth | integer | 8 | XGBoost tree depth |
| sequence_length | integer | 30 | LSTM sequence length |
| failure_threshold | float | 0.5 | Probability threshold |

---

# SECTION 7: Data Quality Flags

## 7.1 Data Confidence Flags

| Condition | Level | Color | Message |
|-----------|-------|-------|---------|
| < 1 hour | Very Low | red | "Demo only — less than 1 hour of data." |
| 1-24 hours | Low | orange | "{hours}h of data — directional only." |
| 1-7 days | Moderate | yellow | "{days} days — accuracy improving." |
| 7-30 days | Good | blue | "{days} days — reliable for warnings." |
| 30-90 days | High | green | "{days} days — production grade." |
| > 90 days | Very High | green | "{days} days — robust baseline." |

## 7.2 Model Training Flags

| Flag | Condition | Severity | Message |
|------|-----------|----------|---------|
| lstm_not_trained | sequences < 50 | info | "Temporal model skipped — need 50+ sequences." |
| insufficient_data | points < 50 (anomaly) or 100 (failure) | warning | "Insufficient data for reliable prediction." |

## 7.3 Quality Assessment Metrics

### Data Completeness
```
completeness_pct = (non_null_values / (rows × columns)) × 100
```

### Days Available
```
days_available = (max_timestamp - min_timestamp).total_seconds() / 86400
```

---

# SECTION 8: Time-to-Failure Calculation

## 8.1 Overview

Time-to-failure (TTF) estimates how many hours remain before the equipment reaches a critical degradation threshold.

## 8.2 Method

### Degradation Score
```
1. Compute rolling health metrics for each parameter
2. Normalize to 0-1 scale (0 = healthy, 1 = failed)
3. Average across all parameters

degradation_score[t] = mean(normalized_health[t] for all parameters)
```

### Trend Fitting

#### Linear Fit
```
degradation = a × time + b

Where:
- a = slope (degradation rate per hour)
- b = intercept

R² = coefficient of determination
```

#### Exponential Fit
```
degradation = a × e^(b × time)

Where:
- a = initial degradation
- b = growth rate

Linearize: ln(degradation) = ln(a) + b × time
```

### TTF Calculation

#### Linear
```
threshold = 0.8 (80% degraded)

If a > 0 (degradation increasing):
    hours_to_failure = (threshold - b) / a
Else:
    hours_to_failure = None (not declining)
```

#### Exponential
```
If b > 0 (degradation growing):
    hours_to_failure = (ln(threshold) - ln(a)) / b
Else:
    hours_to_failure = None
```

### Confidence Interval
```
Standard error of estimate based on regression residuals

confidence_interval = t_value × standard_error × √(1 + 1/n + (target - mean_time)² / SS_time)
```

## 8.3 Output Structure

```json
{
    "hours_to_failure": 168.5,
    "label": "WARNING",
    "confidence_interval_hours": [142.3, 194.7],
    "trend_type": "linear",
    "trend_r2": 0.72,
    "is_reliable": true
}
```

### TTF Labels

| Hours | Label | Action |
|-------|-------|--------|
| < 24 | CRITICAL | Immediate maintenance |
| 24-72 | WARNING | Schedule maintenance soon |
| 72-168 | WATCH | Plan maintenance this week |
| > 168 | NORMAL | Continue monitoring |

---

# APPENDIX A: Example Calculations

## A.1 Complete Health Score Example

```
Device: Compressor Unit-001
Parameters Configured:
| Parameter   | Normal Min | Normal Max | Weight |
|-------------|------------|------------|--------|
| Temperature | 60         | 80         | 30     |
| Pressure    | 4          | 6          | 30     |
| Current     | 10         | 25         | 25     |
| Vibration   | 0          | 5          | 15     |

Current Readings:
- Temperature: 85°C
- Pressure: 7.2 bar
- Current: 18A
- Vibration: 4.2 mm/s

Calculations:

1. Temperature (85°C):
   - Outside normal (80), but within the 15% upper tolerance
   - upper tolerance boundary = 80 + (80 × 0.15) = 92
   - score = 50

2. Pressure (7.2 bar):
   - Outside normal (6), but within the 15% upper tolerance
   - upper tolerance boundary = 6 + (6 × 0.15) = 6.9
   - 7.2 is above 6.9
   - score = 0

3. Current (18A):
   - Within normal (10-25)
   - score = 100

4. Vibration (4.2 mm/s):
   - Within normal (0-5)
   - score = 100

Final Health Score:
= (50 × 0.30) + (0 × 0.30) + (100 × 0.25) + (100 × 0.15)
= 15 + 0 + 25 + 15
= 55

Status: "At Risk" (score 50-74)
```

## A.2 Complete Energy Cost Example

```
Period: January 2024 (31 days)
Devices: 3 machines

Energy Consumption:
| Device    | Energy (kWh) | Peak Demand (kW) |
|-----------|--------------|------------------|
| Machine-1 | 12,500       | 45               |
| Machine-2 | 8,200        | 32               |
| Machine-3 | 15,800       | 58               |

Total: 36,500 kWh, Peak: 58 kW

Tariff:
- Energy: 8.00 INR/kWh
- Demand: 150.00 INR/kW
- Fixed: 500.00 INR/month

Calculations:
- Energy Cost = 36,500 × 8.00 = 292,000.00 INR
- Demand Cost = 58 × 150.00 = 8,700.00 INR
- Fixed Cost = 500 × (31/30) = 516.67 INR

TOTAL = 301,216.67 INR

Per Device Allocation:
- Machine-1: 100,000 INR (33.2%)
- Machine-2: 65,600 INR (21.8%)
- Machine-3: 135,616.67 INR (45.0%)
```

---

# APPENDIX B: Formula Quick Reference

## B.1 Health Score
```
parameter_score = {
  if normal_min ≤ value ≤ normal_max:
    100
  elif (normal_min - normal_min × 0.15) ≤ value < normal_min:
    50
  elif normal_max < value ≤ (normal_max + normal_max × 0.15):
    50
  else:
    0
}

health_score = Σ(parameter_score_i × (weight_i / 100))
```

## B.2 Uptime
```
is_running = (power > 0) OR (current > 0 AND voltage > 0)

uptime_% = (running_minutes / effective_minutes) × 100
```

## B.3 Energy
```
energy_kwh = Σ(power_kw × interval_hours)

derived_power = {
  single_phase: V × I × PF
  three_phase: √3 × V × I × PF
}
```

## B.4 Cost
```
total = (kWh × energy_rate) + (kW × demand_rate) + (kVARh × reactive_rate) + fixed
```

## B.5 Load Factor
```
load_factor = (kWh / hours) / peak_kW
```

## B.6 Anomaly Score
```
anomaly_score = 0.40 × IF_score + 0.40 × LSTM_score + 0.20 × CUSUM_score
```

## B.7 Failure Probability
```
failure_probability = 0.40 × XGB_proba + 0.40 × LSTM_proba + 0.20 × degradation_vote
```

## B.8 Time to Failure
```
hours_to_failure = {
  linear: (threshold - intercept) / slope
  exponential: (ln(threshold) - ln(a)) / b
}
```

---

# DOCUMENT VERSION

- Version: 1.0
- Last Updated: March 2026
- Purpose: Production deployment verification

---

*This document contains proprietary calculation methodologies. All formulas, algorithms, and data flow diagrams are specific to the FactoryOPS system.*
