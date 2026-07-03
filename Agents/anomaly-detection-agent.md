# Anomaly Detection Agent
**Version:** 1.1.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Analyses findings from all upstream agents to detect anomalies and degradation trends
over time. Compares metrics across the analysis period, identifies unusual spikes or
drops, and flags patterns that indicate deteriorating system health before they become
critical failures.

**Outputs:** `anomaly_report.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
anomaly_config:
  input_files:
    - "output/log_analysis.json"
    - "output/metrics_report.json"
    - "output/apm_report.json"
    - "output/security_report.json"

  output_file: "output/anomaly_report.json"

  settings:
    sensitivity:              "medium"
    spike_multiplier:         2.0
    drop_multiplier:          0.5
    trend_window_batches:     5
    min_data_points:          3
```

---

## Pre-requisites

- All upstream agent output JSON files must exist
- Output folder `output/` must be writable

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist)
- MUST load all upstream report JSON files
- MUST detect spikes — sudden increase above `spike_multiplier × rolling average`
- MUST detect drops — sudden decrease below `drop_multiplier × rolling average`
- MUST detect worsening trends — metric consistently increasing/decreasing over `trend_window_batches`
- MUST correlate anomalies across multiple data sources — same timeframe anomaly in multiple sources = higher confidence
- MUST assign confidence score to each anomaly: LOW | MEDIUM | HIGH
- MUST write all anomalies and trends to `anomaly_report.json`

### MUST NOT
- MUST NOT flag single-point anomalies as HIGH confidence without corroboration
- MUST NOT require more than `min_data_points` to report an anomaly
- MUST NOT modify any upstream input files

---

## Anomaly Types

| Anomaly Type | Description |
|---|---|
| `ERROR_RATE_SPIKE` | Sudden increase in error rate |
| `LATENCY_SPIKE` | Sudden increase in response latency |
| `CPU_SPIKE` | Sudden CPU usage spike |
| `MEMORY_SPIKE` | Sudden memory usage spike |
| `REJECTION_RATE_SPIKE` | Sudden increase in DQ rejection rate |
| `KAFKA_LAG_SPIKE` | Sudden increase in Kafka consumer lag |
| `THROUGHPUT_DROP` | Sudden drop in request throughput |
| `WORSENING_TREND` | Metric consistently degrading over time |
| `IMPROVING_TREND` | Metric consistently improving over time |
| `CORRELATED_ANOMALY` | Same timeframe anomaly detected across multiple data sources |

---

## Output Schema — `anomaly_report.json`

```json
{
  "summary": {
    "total_anomalies":       0,
    "high_confidence":       0,
    "medium_confidence":     0,
    "low_confidence":        0,
    "correlated_anomalies":  0,
    "worsening_trends":      0,
    "improving_trends":      0,
    "analysis_period": { "from": "", "to": "" }
  },
  "anomalies": [
    {
      "anomaly_type":    "ERROR_RATE_SPIKE",
      "service":         "payment-service",
      "timestamp":       "2026-07-02T09:30:00Z",
      "value":           45.2,
      "baseline":        8.1,
      "deviation_pct":   457.0,
      "confidence":      "HIGH",
      "corroborated_by": ["LATENCY_SPIKE", "CPU_SPIKE"],
      "description":     "Error rate spiked 5.6× above baseline at 09:30"
    }
  ],
  "trends": [],
  "all_anomalies": []
}
```

---

## Execution Workflow

### Phase 0 — Load All Upstream Reports
1. Read all input JSON files listed in config
2. Extract time-series data points from each report
3. Build unified timeline of metrics across all sources

### Phase 1 — Spike Detection
1. For each metric series: calculate rolling average over the analysis period
2. Flag any point where value > `spike_multiplier × rolling average` as spike
3. Flag any point where value < `drop_multiplier × rolling average` as drop

### Phase 2 — Trend Detection
1. For each metric with >= `min_data_points` data points
2. Calculate linear trend over `trend_window_batches` consecutive points
3. Flag consistently increasing metrics as WORSENING_TREND, decreasing as IMPROVING_TREND

### Phase 3 — Correlation Analysis
1. For each anomaly, check if other metrics show anomalies within ±5 minutes
2. If 2+ sources show anomalies at the same time → CORRELATED_ANOMALY, HIGH confidence
3. Single-source anomaly → MEDIUM or LOW confidence depending on deviation magnitude

### Phase 4 — Write Output
1. Sort anomalies by confidence descending, then by deviation descending
2. Build summary statistics
3. Write `anomaly_report.json`

---

## Output Specification

| Artifact | Description |
|---|---|
| `anomaly_report.json` | Detected anomalies with type, confidence, deviation, corroboration, and trend analysis |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No anomalies detected | Sensitivity too low or data too uniform | Raise sensitivity to "high" or add more varied sample data |
| Too many false positives | Sensitivity too high | Lower sensitivity to "low" or raise spike_multiplier |
| Insufficient data points | Sample data too small | Add more data points to sample files |

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-02 | anomaly-detection-agent | Initial release — spike detection, trend analysis, cross-source correlation, confidence scoring |
| 1.1.0 | 2026-07-03 | anomaly-detection-agent | Added MUST rule requiring analysis_period.from/to to be populated from actual record timestamps instead of left null |