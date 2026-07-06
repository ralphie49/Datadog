# Pipeline Health Monitor Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Analyses normalised data for streaming and pipeline-specific health indicators. Detects
Kafka consumer lag, checkpoint corruption, SLA breaches, missed trigger intervals, and
processing backlogs across any streaming or batch pipeline monitored in Datadog.

**Outputs:** `apm_report.json`

---

## 🚫 Anti-Hardcoding & Generalization Contract (read before writing any code)

This spec will be used to generate an implementation that MUST run correctly against **inputs it has
never seen** — different services, different metric values, different incidents, possibly zero
incidents, possibly many more files than the sample. An implementation that only reproduces this
project's bundled sample data is non-compliant, even if every self-test / acceptance-test check
elsewhere in this file passes.

**Rules:**
- Any `Self-Test Cases`, `Mandatory Acceptance Test For Sample input/`, or "for the sample `input/`
  folder, X MUST equal N" section in this file describes the expected *result* of running the general
  algorithm on the bundled sample data. It is a regression check on that algorithm, not a specification
  of literal values to write into the code. Treat every number, service name, incident ID, topic name,
  or string in those sections as derived output to verify — never as a constant to embed.
- Do NOT special-case any literal from the sample dataset (e.g. specific service names, specific lag/
  latency/error-rate numbers, specific incident IDs) anywhere in the implementation's logic, thresholds,
  or output-construction code. Every such value may only appear in the output because the algorithm
  computed it from the actual input file contents at runtime — never because it was typed into the code.
- Before this agent is considered done, run the implementation against a **second, structurally
  different input dataset** (different service names, different metric values, a different or absent
  incident, a different number of input files) and confirm the output changes accordingly and remains
  internally consistent. If running the code against a different input still produces the sample
  dataset's specific service names, IDs, or numeric findings, that is proof of hardcoding — reject the
  implementation and rewrite it.
- If a self-test/acceptance check in this file cannot be satisfied by an implementation that also passes
  the different-dataset test above, treat that as a reason to flag the self-test for spec review — never
  as a license to hardcode the literal expected value instead of implementing the described logic.

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
pipeline_health_config:
  input_file:  "output/<dataset>/normalised_data.json"
  output_file: "output/<dataset>/apm_report.json"

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

- `output/<dataset>/normalised_data.json` must exist (produced by Log Ingestion & Normaliser Agent)
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_file` and write only the configured `output_file`.
- `input_file` and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from topic names, consumer groups, pipeline names, dates, the input filename, or existing files in `output/`.
- If the input and output paths point to different dataset folders, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist).
  `analysis_period` MUST be a JSON object with exactly the keys `from` and `to` — never a bare array/list
- MUST read Kafka, checkpoint, trace, log, and alert evidence from `normalised_data.json.records[]`;
  do not use `samples`, `classified_files`, or summary-only metadata as a substitute for actual records.
- MUST scan all records for Kafka lag metrics and flag breaches
- MUST preserve `topic` and `consumer_group` as two distinct fields, each populated from its own actual
  source field in the normalised data — NEVER substitute one for the other when a value is missing or
  unclear. If the true topic name genuinely cannot be determined for a lag record, set `topic: null` rather
  than writing the consumer group's name (or any other value) into the `topic` field
- MUST include a `timestamp` field on every entry in `kafka.topics[]`, taken from that record's own source
  timestamp — an untimestamped Kafka finding cannot be correlated by any downstream agent and is a data
  integrity defect, not an optional field
- MUST detect checkpoint health issues from log messages
- MUST identify SLA breaches — batches running longer than `sla_breach_threshold_ms`, using an actual
  measured batch/request duration value compared against the threshold. A generic retry/timeout log line
  (e.g. "request timeout, retrying") is evidence for a `TIMEOUT` application error (handled by the Error &
  Data Quality Agent) or a `KAFKA_LAG_*`/backlog finding (handled by this agent's own Kafka/backlog sections)
  — it is NOT by itself an `SLA_BREACH` unless the record carries an explicit duration value that exceeds
  `sla_breach_threshold_ms`. Do not create an `SLA_BREACH` entry from a message that contains no measured
  duration
- MUST detect missed trigger intervals in streaming pipelines
- MUST detect processing backlogs building up over time
- MUST group findings by pipeline or topic name
- MUST ensure `pipelines_with_issues` never exceeds `total_pipelines_analysed` — since it is a count of
  distinct pipeline/topic/consumer-group names, not a count of issues (see summary schema note below), verify
  this arithmetically before writing output: `len(set(pipeline_name for every finding across kafka/checkpoints/
  sla_breaches/backlogs)) <= total_pipelines_analysed`. If the computed distinct-pipeline count would exceed
  `total_pipelines_analysed`, that means `total_pipelines_analysed` itself was undercounted during ingestion
  scanning — recompute it as the count of distinct pipeline/topic/consumer-group names seen across the input,
  never leave the two numbers contradicting each other
- MUST write all findings to `apm_report.json`

### MUST NOT
- MUST NOT accept a summary-only `normalised_data.json` that has no `records[]` array
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

## Implementation Notes (pseudocode — MUST be followed structurally, not just in spirit)

Observed defect: when Kafka lag comes from a metrics CSV/metric record, that record's own fields are
`metric_name` (e.g. `kafka_consumer_lag`) and `service` (e.g. `checkout-consumer`) — it does NOT contain a
real Kafka topic name. A naive implementation writes `topic = metric_name`, which produces a nonsense
topic value (the metric's own name, not a topic). `metric_name` and `service` are NEVER valid values for
`topic` — enforce this explicitly:

```
for lag_metric_record in kafka_lag_metric_records:
    topic = None                                    # never default to metric_name or service
    consumer_group = lag_metric_record.service       # this field is legitimately the consumer group/service
    # attempt to recover the real topic name by cross-referencing log/alert records with matching
    # lag value and overlapping timestamp/service — real Datadog exports commonly log the topic name
    # in a companion log line even when the metric itself doesn't carry it:
    for log_or_alert in log_and_alert_records:
        if log_or_alert.service == consumer_group and abs(time_diff) < 2_minutes:
            match = regex_search(r"topic[= ]([\w.-]+)", log_or_alert.message)
            if match:
                topic = match.group(1)
                break
    # if no match found after checking companion log/alert records, topic stays null — do NOT fall back
    # to metric_name, service, or any other field as a substitute value
    kafka_issues.append({topic: topic, consumer_group: consumer_group, lag: ..., ...})
```

## Self-Test Cases (regression check only — see Anti-Hardcoding Contract above; verify via the algorithm, never hardcode these literal values)

Given `datadog_metrics_export_20260702.csv` (metric_name=`kafka_consumer_lag`, service=`checkout-consumer`,
no topic column) and `datadog_logs_export_20260702.json` (which contains the line "Kafka consumer lag
critical for topic ecommerce-events, lag=125000"):
- `apm_report.json.kafka.topics[].topic` for the lag=125000 and lag=118000 entries MUST equal
  `"ecommerce-events"` — the value recovered from the companion log line — and MUST NOT equal
  `"kafka_consumer_lag"` (the metric_name) or `"checkout-consumer"` (the consumer_group/service).
- If any `topic` value in the output is identical to any `consumer_group` value or identical to the string
  `"kafka_consumer_lag"`, this is a data-integrity defect and the implementation must be corrected before
  the output is accepted.

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

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- Kafka lag findings MUST come only from actual Kafka lag metrics/logs/alerts, not generic latency or timeout records.
- `apm_report.json` MUST include the top-level `kafka` object with a `topics` array. Do not replace Kafka
  analysis with only `sla_breaches`, `trace_failures`, or a generic issues list.
- `topic` and `consumer_group` MUST remain separate. Never copy `consumer_group`, service name, or `metric_name` into `topic` as a fallback.
- If the real topic cannot be determined, set `topic: null` and explain the missing source in a note or skipped-detail field.
- Every `kafka.topics[]` entry MUST include the source record timestamp.
- WARN and CRITICAL Kafka lag severities MUST follow configured thresholds exactly.
- `PROCESSING_BACKLOG` entries derived from Kafka lag MUST use the same verdict as the underlying lag finding.
- Do not create `SLA_BREACH` from a generic timeout message unless an explicit duration exceeds `sla_breach_threshold_ms`.
- `summary.total_pipelines_analysed` MUST be the count of distinct topic/consumer-group/pipeline identifiers observed in relevant input.
- `summary.pipelines_with_issues` MUST be the count of distinct identifiers with at least one issue and MUST NOT exceed `total_pipelines_analysed`.
- `summary.critical_issues` and `summary.warn_issues` MUST be counted from `all_issues`.

Reject the generated output if topic equals `kafka_consumer_lag`, equals the consumer group, or is invented without evidence.
Also reject it if the `kafka` section is missing while the input contains `kafka_consumer_lag` metrics or
Kafka lag log/alert messages.


---

## 🚫 Final Hardcoding Check (applies on top of everything above)

Before accepting this agent's implementation as done: pick any literal value in its output (a service
name, an ID, a count, a percentage) and ask "would this exact value still appear if I fed the agent a
different input file with different data?" If the answer is yes for a value that should depend on the
input, the implementation is hardcoded and must be rewritten to derive that value from the actual input
at runtime. This check applies to every JSON/Markdown artifact this agent produces, not just the fields
called out elsewhere in this file.