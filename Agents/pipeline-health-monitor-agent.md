# Pipeline Health Monitor Agent
**Version:** 1.2.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Analyses normalised data for streaming and pipeline-specific health indicators. Detects
Kafka consumer lag, checkpoint corruption, SLA breaches, missed trigger intervals, and
processing backlogs across any streaming or batch pipeline monitored in Datadog.

**Outputs:** `apm_report.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
pipeline_health_config:
  input_file:  "output/normalised_data.json"
  output_file: "output/apm_report.json"

  thresholds:
    kafka_lag_warn:          10000
    kafka_lag_critical:      100000
    checkpoint_age_warn_min: 30
    sla_breach_threshold_ms: 300000
    backlog_warn_records:    50000
    missed_triggers_warn:    3
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
- MUST scan all records for Kafka lag metrics and flag breaches
- MUST detect checkpoint health issues from log messages
- MUST identify SLA breaches — batches running longer than threshold
- MUST detect missed trigger intervals in streaming pipelines
- MUST detect processing backlogs building up over time
- MUST group findings by pipeline or topic name
- MUST write all findings to `apm_report.json`

### MUST NOT
- MUST NOT flag checkpoint warnings in test/dev environments as CRITICAL
- MUST NOT modify the input `normalised_data.json`

---

## Health Issue Types

| Issue Type | Description |
|---|---|
| `KAFKA_LAG_HIGH` | Consumer lag exceeded warn threshold |
| `KAFKA_LAG_CRITICAL` | Consumer lag exceeded critical threshold |
| `CHECKPOINT_STALE` | Checkpoint not updated within expected window |
| `CHECKPOINT_CORRUPT` | Checkpoint corruption detected in logs |
| `SLA_BREACH` | Batch or pipeline run exceeded SLA threshold |
| `MISSED_TRIGGER` | Streaming trigger interval was missed |
| `PROCESSING_BACKLOG` | Records accumulating faster than being processed |
| `PIPELINE_STOPPED` | Pipeline stopped unexpectedly |

---

## Output Schema — `apm_report.json`

```json
{
  "summary": {
    "total_pipelines_analysed": 0,
    // pipelines_with_issues = count of DISTINCT pipeline/topic/consumer-group names with at least one
    // flagged issue attached -- never the total issue count. Can never exceed total_pipelines_analysed.
    "pipelines_with_issues":    0,
    // critical_issues / warn_issues = total findings across ALL subsections (kafka + checkpoints +
    // sla_breaches + backlogs combined) at that severity
    "critical_issues":          0,
    "warn_issues":              0,
    "sla_breaches":             0,
    "analysis_period": { "from": "", "to": "" }
  },
  "kafka": {
    "topics": [
      {
        "topic":          "ecommerce-events",
        "consumer_group": "pyspark-consumer",
        "lag":            125000,
        "verdict":        "CRITICAL",
        "issue_type":     "KAFKA_LAG_CRITICAL"
      }
    ]
  },
  "checkpoints": [],
  "sla_breaches": [],
  "backlogs": [],
  "all_issues": []
}
```

---

## Execution Workflow

### Phase 0 — Load Input
1. Read `normalised_data.json`
2. Filter records relevant to streaming: Kafka metrics, checkpoint logs, pipeline batch logs

### Phase 1 — Kafka Lag Analysis
1. Extract Kafka consumer lag metrics per topic and consumer group
2. Flag lag > `kafka_lag_warn` as WARN, > `kafka_lag_critical` as CRITICAL
3. Track lag trend — growing or stable

### Phase 2 — Checkpoint Health
1. Scan log messages for checkpoint-related keywords: `checkpoint`, `offset`, `recovery`, `corrupt`
2. Flag CHECKPOINT_STALE if checkpoint timestamp > `checkpoint_age_warn_min` old
3. Flag CHECKPOINT_CORRUPT if corruption keywords detected

### Phase 3 — SLA Breach Detection
1. Extract batch duration metrics from pipeline logs
2. Flag batches exceeding `sla_breach_threshold_ms` as SLA_BREACH

### Phase 4 — Backlog & Trigger Analysis
1. Detect missed trigger intervals; flag if missed count > `missed_triggers_warn`
2. Calculate record backlog from input vs output rate; flag if > `backlog_warn_records`
3. When a backlog entry is derived from a Kafka lag finding, its `description` text MUST reflect that finding's
   actual verdict (WARN or CRITICAL) rather than a hardcoded severity word — e.g. do not write "lag is critical"
   when the underlying lag value only breached `kafka_lag_warn`, not `kafka_lag_critical`

### Phase 5 — Write Output
1. Build summary statistics
2. Write `apm_report.json`

---

## Output Specification

| Artifact | Description |
|---|---|
| `apm_report.json` | Kafka lag per topic, checkpoint health, SLA breaches, missed triggers, processing backlogs |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No Kafka data found | No Kafka metrics in input | Verify sample data includes Kafka consumer lag metrics |
| SLA breach false positives | Threshold too low | Raise `sla_breach_threshold_ms` |
| Checkpoint age always stale | Old test data being used | Use recent timestamps in sample input |

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-02 | pipeline-health-monitor-agent | Initial release — Kafka lag, checkpoint health, SLA breach, backlog detection |
| 1.1.0 | 2026-07-03 | pipeline-health-monitor-agent | Clarified pipelines_with_issues counts distinct pipelines, not total issue count, and can never exceed total_pipelines_analysed; added analysis_period population rule |
| 1.2.0 | 2026-07-03 | pipeline-health-monitor-agent | Backlog description text must reflect the actual computed verdict (WARN/CRITICAL) instead of a hardcoded severity word |