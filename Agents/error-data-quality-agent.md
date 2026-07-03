# Error & Data Quality Agent
**Version:** 1.2.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Scans the normalised data for every error, exception, warning, and data quality issue.
Classifies application errors by type, severity, and recurrence, **and** tracks data
quality health — rejection rates, quarantine/dead-letter volume, worst offending columns,
and DQ alert history. Merges what were previously two separate concerns (application
errors and data quality failures) into a single agent, since DQ failures are, at their
core, a specialised category of error.

**Outputs:** `log_analysis.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
error_dq_config:
  input_file:  "output/normalised_data.json"
  output_file: "output/log_analysis.json"

  error_settings:
    error_threshold:      "ERROR"   # Minimum level to flag: DEBUG | INFO | WARN | ERROR | CRITICAL
    recurring_threshold:  3         # Flag as recurring if same error appears more than N times
    top_errors_limit:     10        # Report top N most frequent errors

  dq_thresholds:
    rejection_rate_warn_pct:     10    # Warn if rejection rate exceeds this %
    rejection_rate_critical_pct: 25    # Critical if rejection rate exceeds this %
    quarantine_volume_warn:      1000  # Warn if quarantine record count exceeds this
    dead_letter_volume_warn:     500   # Warn if dead-letter record count exceeds this
    dq_alert_frequency_warn:     5     # Warn if same DQ alert fires more than N times
```

---

## Pre-requisites

- `normalised_data.json` must exist (produced by Log Ingestion & Normaliser Agent)
- Output folder `output/` must be writable

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist)
- MUST scan all records with severity >= `error_threshold`
- MUST classify every application error into one of the defined error types
- MUST count frequency of each unique error and flag recurring ones
- MUST extract DQ metrics from log messages matching patterns: `DQ_METRICS`, `rejection_rate`, `quarantine`, `dead_letter`
- MUST calculate rejection rate per batch: `failed / total × 100`
- MUST flag rejection rate above warn and critical thresholds
- MUST identify the top 5 worst offending columns by rejection count
- MUST detect DQ alert patterns — same alert firing repeatedly
- MUST produce batch-level DQ trend — improving or worsening
- MUST write both application-error and DQ findings to `log_analysis.json`

### MUST NOT
- MUST NOT ignore CRITICAL severity records regardless of threshold setting
- MUST NOT deduplicate errors without preserving frequency count
- MUST NOT ignore DQ alerts even if rejection rate is below threshold
- MUST NOT modify the input `normalised_data.json`

---

## Application Error Types

| Error Type | Examples |
|---|---|
| `CONNECTION_FAILURE` | DB connection refused, network unreachable, socket timeout |
| `TIMEOUT` | Request timeout, query timeout, operation timed out |
| `OUT_OF_MEMORY` | Java heap space, OOM killer, memory limit exceeded |
| `NULL_POINTER` | NullPointerException, null reference, undefined value |
| `AUTHENTICATION_FAILURE` | Invalid credentials, token expired, unauthorised |
| `PERMISSION_DENIED` | Access denied, forbidden, insufficient privileges |
| `SCHEMA_MISMATCH` | Column not found, type mismatch, schema evolution error |
| `RESOURCE_EXHAUSTION` | CPU throttled, disk full, thread pool exhausted |
| `CHECKPOINT_FAILURE` | Checkpoint corrupted, offset missing, recovery failed |
| `DELTA_CONFLICT` | Concurrent write conflict, transaction failed |
| `APPLICATION_ERROR` | Unhandled exception, stack overflow, assertion failed |
| `UNKNOWN` | Cannot be classified into above types |

Several patterns can match the same message (e.g. a connection message containing the word "timed out" matches
both `CONNECTION_FAILURE` and `TIMEOUT`). When more than one pattern matches, resolve using this fixed priority
order — most specific/actionable category wins, top to bottom:

`CHECKPOINT_FAILURE` > `DELTA_CONFLICT` > `OUT_OF_MEMORY` > `RESOURCE_EXHAUSTION` > `AUTHENTICATION_FAILURE` >
`PERMISSION_DENIED` > `SCHEMA_MISMATCH` > `NULL_POINTER` > `CONNECTION_FAILURE` > `TIMEOUT` > `APPLICATION_ERROR` > `UNKNOWN`

This ordering must be applied consistently across every run so the same message always classifies the same way.

## Data Quality Issue Types

| Issue Type | Description |
|---|---|
| `HIGH_REJECTION_RATE` | Rejection rate exceeded warn or critical threshold |
| `QUARANTINE_VOLUME_HIGH` | Quarantine table volume exceeded warn threshold |
| `DEAD_LETTER_VOLUME_HIGH` | Dead-letter table volume exceeded warn threshold |
| `RECURRING_DQ_ALERT` | Same DQ alert fired more than N times |
| `REJECTION_RATE_WORSENING` | Rejection rate increasing across consecutive batches |
| `NULL_VALUE_SPIKE` | Sudden increase in NULL_VALUE rejections |
| `FORMAT_MISMATCH_SPIKE` | Sudden increase in FORMAT_MISMATCH rejections |
| `INVALID_VALUE_SPIKE` | Sudden increase in INVALID_VALUE rejections |

---

## Output Schema — `log_analysis.json`

```json
{
  "summary": {
    "total_errors":      0,
    "total_warnings":    0,
    "total_critical":    0,
    "recurring_errors":  0,
    "affected_services": [],
    "total_batches_analysed":   0,
    "batches_with_dq_issues":   0,
    "avg_rejection_rate_pct":   0.0,
    "max_rejection_rate_pct":   0.0,
    "total_quarantine_records": 0,
    "total_dead_letter_records": 0,
    "analysis_period": { "from": "", "to": "" }
  },
  "top_errors": [
    {
      "rank":          1,
      "error_type":    "CONNECTION_FAILURE",
      "message":       "DB connection refused",
      "service":       "payment-service",
      "severity":      "ERROR",
      "frequency":     42,
      "is_recurring":  true,
      "first_seen":    "2026-07-02T08:00:00Z",
      "last_seen":     "2026-07-02T10:00:00Z"
    }
  ],
  "rejection_rates": [
    {
      "batch_id":      "batch_001",
      "pipeline":      "cdc-pipeline",
      "total":         1000,
      "passed":        920,
      "failed":        80,
      "rejection_pct": 8.0,
      "verdict":       "WARN"
    }
  ],
  "worst_columns": [
    {
      "column":          "email",
      "rejection_count": 45,
      "rule_type":       "FORMAT_MISMATCH"
    }
  ],
  "dq_alerts":  [],
  "dq_trends":  [],
  "all_errors": [],
  "all_dq_issues": []
}
```

---

## Execution Workflow

### Phase 0 — Load Input
1. Read `normalised_data.json`
2. Filter records where `severity` >= `error_threshold` for the error path
3. Separately filter records containing DQ-related keywords in message or tags for the DQ path

### Phase 1 — Classify Application Errors
1. For each error record, match message against error type patterns
2. If multiple patterns match, resolve using the fixed priority order defined in the Application Error Types
   section; assign `error_type` from classification table; if no match → `UNKNOWN`
3. Group by `error_type` + `message` + `service`, count occurrences
4. Flag groups with count > `recurring_threshold` as `is_recurring: true`
5. Record `first_seen` / `last_seen` per group
6. Sort by frequency descending, take top `top_errors_limit`

### Phase 2 — Extract DQ Metrics
1. Parse log messages for patterns: `total=`, `passed=`, `failed=`, `rejection_rate_pct=`
2. Extract batch_id, pipeline name, total, passed, failed, rejection_rate_pct per batch
3. Flag batches above warn and critical thresholds

### Phase 3 — Quarantine, Dead-Letter & Worst Columns
1. Extract quarantine/dead-letter write counts, flag volumes above thresholds
2. Parse rejection_reason values (`NULL_VALUE:col`, `FORMAT_MISMATCH:col`, `INVALID_VALUE:col:val`); when the
   log line also carries an explicit `count=N` field (e.g. `DQ_ALERT rejection_reason=FORMAT_MISMATCH:email
   count=45`), that N is the per-column rejection count — parse it with a numeric regex (`count=(\d+)`) and use
   it directly rather than counting log-line occurrences, since a single alert line commonly represents many
   underlying rejected records at once
3. Group by column and rule type, summing rejection counts per column across all matching lines; rank top 5
   worst offending columns by that summed count. If zero DQ_ALERT lines carry a `rejection_reason`, `worst_columns`
   is legitimately empty — but if `rejection_reason` values are present in the input, `worst_columns` MUST NOT be
   empty in the output

### Phase 4 — DQ Alert History & Trend
1. Extract DQ_ALERT log entries. For each **unique alert message string**, set its `dq_alerts[].count` to the
   number of times that exact message occurs in the analysis period (this is alert *recurrence*, a distinct
   value from the `count=N` rejection figure parsed in Phase 3 — do not conflate the two; a DQ_ALERT line that
   appears once has `count: 1` here even though its embedded `count=45` feeds `worst_columns` instead)
2. Flag alerts firing more than `dq_alert_frequency_warn` times as RECURRING_DQ_ALERT
3. Sort batches by timestamp, calculate rolling rejection rate
4. Flag REJECTION_RATE_WORSENING if rate increasing over last 3+ batches

### Phase 5 — Write Output
1. Build combined summary statistics for both errors and DQ
2. Write `log_analysis.json`

---

## Output Specification

| Artifact | Description |
|---|---|
| `log_analysis.json` | Classified application errors (type, severity, frequency, recurrence) plus DQ findings (rejection rates, quarantine/dead-letter volume, worst columns, DQ alert trend) |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No errors found | Threshold too high | Lower `error_threshold` to WARN |
| All errors UNKNOWN | Message patterns not matching | Review raw messages and extend classification patterns |
| No DQ metrics found | Logs don't contain DQ patterns | Verify pipeline uses standard DQ log format |
| Rejection rate always 0 | Parsing pattern mismatch | Check log message format against extraction patterns |
| Worst columns missing | rejection_reason not in logs | Verify DQ logging includes rejection_reason field |

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-03 | error-data-quality-agent | Merged release — combines application error classification with data quality metrics tracking into a single agent |
| 1.1.0 | 2026-07-03 | error-data-quality-agent | Added fixed priority order for error-type classification so overlapping message patterns resolve consistently across runs |
| 1.2.0 | 2026-07-03 | error-data-quality-agent | Disambiguated dq_alerts[].count (alert recurrence) from the embedded count=N rejection figure used for worst_columns; clarified worst_columns must sum parsed rejection counts; added analysis_period population rule |