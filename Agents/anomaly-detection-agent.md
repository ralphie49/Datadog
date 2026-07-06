# Anomaly Detection Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

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
    - "output/<dataset>/log_analysis.json"
    - "output/<dataset>/metrics_report.json"
    - "output/<dataset>/apm_report.json"
    - "output/<dataset>/security_report.json"

  output_file: "output/<dataset>/anomaly_report.json"

  settings:
    sensitivity:              "medium"
    spike_multiplier:         2.0
    drop_multiplier:          0.5
    trend_window_batches:     5
    min_data_points:          3
```

---

## Pre-requisites

- All upstream agent output JSON files under `output/<dataset>/` must exist
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_files` and write only the configured `output_file`.
- Every `input_files` entry and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from anomaly types, services, timestamps, upstream filenames, or existing files in `output/`.
- If the input files do not all share the same dataset folder, or if the output path points elsewhere, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist).
  `analysis_period` MUST be a JSON object with exactly the keys `from` and `to` — never a bare array/list
  like `["from_value", "to_value"]`
- MUST populate each individual anomaly's own `timestamp` field with the actual timestamp of the specific
  record/finding that triggered it (e.g. the trace timestamp for a `LATENCY_SPIKE`, the log timestamp for a
  `KAFKA_LAG_SPIKE`) — NEVER default an anomaly's `timestamp` to `analysis_period.from`, the run start time,
  or any other placeholder. A wrong per-anomaly timestamp breaks every downstream agent that correlates by
  time window (Dependency/Flow Analysis Agent, Root Cause Analysis Agent), so this is treated as a data
  integrity failure, not a cosmetic one. If the true timestamp genuinely cannot be determined for a specific
  anomaly, set it to `null` explicitly rather than substituting a different real timestamp that misrepresents
  when the anomaly occurred
- MUST load all upstream report JSON files
- MUST detect spikes — sudden increase above `spike_multiplier × rolling average`
- MUST detect drops — sudden decrease below `drop_multiplier × rolling average`
- MUST detect worsening trends — metric consistently increasing/decreasing over `trend_window_batches`
- MUST correlate anomalies across multiple data sources — same timeframe anomaly in multiple sources = higher confidence
- MUST assign confidence score to each anomaly: LOW | MEDIUM | HIGH
- MUST ensure every anomaly's `description` field describes that anomaly's own `anomaly_type` and `value` —
  never copy or borrow a message/description that actually belongs to a different anomaly or a different
  metric domain (e.g. a `THROUGHPUT_DROP` entry's description must describe a throughput measurement, not a
  Kafka lag log line — if the only available context for a throughput drop IS the Kafka lag event, phrase the
  description to make that causal link explicit rather than pasting the unrelated raw message verbatim)
- MUST write all anomalies and trends to `anomaly_report.json`

### MUST NOT
- MUST NOT flag single-point anomalies as HIGH confidence without corroboration
- MUST NOT require more than `min_data_points` to report an anomaly
- MUST NOT emit more than one `CORRELATED_ANOMALY` entry for the same pair (or set) of corroborating
  anomaly types on the same service within the same correlation timeframe — a single underlying correlation
  (e.g. "LATENCY_SPIKE + KAFKA_LAG_SPIKE on checkout-consumer") produces exactly one `CORRELATED_ANOMALY`
  entry, never a copy per contributing raw data point. Before writing output, deduplicate `anomalies[]` /
  `all_anomalies[]` by the combination of (`anomaly_type`, `service`, `corroborated_by` set, rounded
  timeframe) and collapse duplicates into one entry
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
1. For each anomaly, check if other metrics show anomalies within ±5 minutes, using each anomaly's own
   accurate `timestamp` (per the MUST rule above) — never the analysis-period start
2. If 2+ sources show anomalies at the same time → CORRELATED_ANOMALY, HIGH confidence
3. Single-source anomaly → MEDIUM or LOW confidence depending on deviation magnitude
4. Before adding a new `CORRELATED_ANOMALY` entry, check whether an entry already exists for the same
   `service` + same `corroborated_by` set (order-independent) within the same timeframe — if so, do not add
   a duplicate; this is the most common failure mode of this phase

### Phase 4 — Write Output
1. Sort anomalies by confidence descending, then by deviation descending
2. Build summary statistics
3. Write `anomaly_report.json`

---

## Implementation Notes (pseudocode — MUST be followed structurally, not just in spirit)

Three specific defects have been observed in prior generated code for this agent.

**1. A spike MUST be computed, not inferred from another report's threshold verdict.** A common shortcut is
to re-emit every service already marked WARN/CRITICAL in `metrics_report.json` as a `LATENCY_SPIKE`, without
checking whether the current value actually exceeds `spike_multiplier × baseline`. This produces false
positives whenever the "critical" verdict came from an absolute threshold (e.g. p99 >= 1000ms) rather than a
relative jump — a service can be CRITICAL by absolute threshold while its current value is *below* its own
baseline, which is not a spike.
```
for service_row in metrics_report.latency.by_service:
    value = service_row.p99_ms
    baseline = service_row.avg_ms
    if baseline > 0 and value > spike_multiplier * baseline:   # this comparison is mandatory
        anomalies.append({anomaly_type: "LATENCY_SPIKE", service: ..., value: value, baseline: baseline,
                           deviation_pct: round(100*(value-baseline)/baseline, 1), ...})
    # do NOT emit a LATENCY_SPIKE just because service_row.verdict is WARN/CRITICAL —
    # verdict reflects an absolute threshold breach in metrics_report.json, not a relative spike
```

**2. Each anomaly's `timestamp` MUST be the specific record's own timestamp, never a report-level summary
field.** A common shortcut re-uses `metrics_report.summary.analysis_period.to` (or `.from`) as a stand-in
timestamp for every anomaly derived from that report. This is explicitly forbidden by the MUST rule above —
use the timestamp on the specific trace/metric/log record that triggered the anomaly, e.g. the timestamp of
the actual slowest trace for that service, not the report's overall date range.

**3. Correlation MUST also catch same-`anomaly_type` anomalies across different, dependency-connected
services at the same timestamp — not only different-type anomalies on the same service.** A common
shortcut only pairs `a.anomaly_type != b.anomaly_type`, which silently excludes the case of e.g. three
different services all spiking in latency at the same moment (a real, high-value correlation signal for a
cascading failure) because they share the same `anomaly_type`.
```
for a, b in all_pairs(anomalies):
    same_moment = abs(a.timestamp - b.timestamp) <= 5_minutes
    related = (a.service == b.service) or dependency_graph_connects(a.service, b.service)
    if same_moment and related and (a.service != b.service or a.anomaly_type != b.anomaly_type):
        # correlate on (a) same service + different type, OR (b) different service + same type,
        # as long as they are graph-connected — both are valid corroboration signals
        emit_correlated_anomaly(a, b)
```

## Regression Gates (must pass before this agent is considered done)
- The output must contain at least one `CORRELATED_ANOMALY` entry when two or more dependency-connected anomalies occur within the same 5-minute window.
- Each anomaly's `timestamp` must be taken from the triggering record or finding, never from the overall report analysis period start.
- The generated `anomaly_report.json` must not contain empty or placeholder timestamps for critical findings when the upstream records carry real timestamps.

## Self-Test Cases (run against this project's own sample data before considering this agent done)

- `checkout-consumer`'s latency anomaly (p99=3300ms, avg/baseline=3400ms) MUST NOT appear in
  `anomaly_report.json.anomalies` as a `LATENCY_SPIKE`, since 3300 < 3400 (the value is below baseline,
  not above `spike_multiplier × baseline`). If it appears, spike math was not actually implemented.
- The three simultaneous latency findings on `order-service`, `payment-service`, and `checkout-consumer`
  (all at `09:50:00Z`, all dependency-connected per `dependency_report.json`) MUST produce at least one
  `CORRELATED_ANOMALY` entry. If none exists, cross-service same-type correlation was not implemented.
- No anomaly's `timestamp` field may equal `metrics_report.json.summary.analysis_period.to` unless that is
  genuinely also the specific triggering record's own timestamp.

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

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- Do not convert every WARN or CRITICAL upstream finding into an anomaly. An anomaly requires a relative spike, drop, worsening trend, or correlation rule to be satisfied.
- A `LATENCY_SPIKE` MUST compare a current or triggering latency value against a baseline. If `value <= baseline * spike_multiplier`, do not emit `LATENCY_SPIKE` even if the upstream metrics verdict is CRITICAL.
- A single-source anomaly MUST NOT be `HIGH` confidence unless its deviation crosses a documented high-confidence threshold and that rule is implemented explicitly. Default single-source anomalies to `MEDIUM` or `LOW`.
- If two or more anomalies occur within the correlation window and their services are dependency-connected, emit at least one `CORRELATED_ANOMALY` or record a machine-readable `correlation_skipped_reason`.
- `summary.correlated_anomalies` MUST equal the count of emitted correlated anomaly records according to the schema. The counting rule must be implemented consistently.
- Every anomaly timestamp MUST come from the specific triggering record/finding. Never use a report-level `analysis_period.from` or `analysis_period.to` as a placeholder.
- Every anomaly description MUST describe that anomaly's own `anomaly_type`, service, value, and baseline. Do not paste an unrelated source message.
- Deduplicate anomalies by `anomaly_type`, `service`, rounded timestamp/correlation window, and `corroborated_by` set.
- `summary.total_anomalies` MUST equal `len(anomalies)`.

Reject the generated output if a high-confidence anomaly has neither corroboration nor an explicit high-deviation rule.
