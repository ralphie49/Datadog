# Output Content Validation Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Validates the generated pipeline outputs for content correctness, not just file existence or JSON parsing.
This agent is the final gate after report generation. It must fail the run if any output file has the right
name but wrong content, wrong schema, wrong dataset routing, missing evidence, shallow placeholder data, or
incorrect cross-file counts.

**Outputs:** `validation_manifest.json`

---

## Developer Configuration

```yaml
output_validation_config:
  input_target: "<path-to-your-input-folder>/"
  output_dir: "output/<dataset>/"
  required_artifacts:
    - normalised_data.json
    - log_analysis.json
    - metrics_report.json
    - apm_report.json
    - security_report.json
    - anomaly_report.json
    - dependency_report.json
    - root_cause.json
    - recommendations.json
    - patch_suggestions.json
    - datadog_analysis_report.md
  output_file: "output/<dataset>/validation_manifest.json"
```

---

## Core Rule

The pipeline is not complete until this validator writes `validation_manifest.json` with:

```json
{
  "dataset_name": "input",
  "status": "valid | invalid | valid_with_warnings",
  "checks": [
    {
      "check_id": "normalised_records_present",
      "artifact": "normalised_data.json",
      "status": "passed | failed",
      "detail": "human-readable explanation"
    }
  ],
  "failed_checks": [],
  "artifact_paths": []
}
```

The manifest MUST contain real content checks. A manifest that only confirms file paths or JSON parsing is invalid.

---

## Mandatory Generic Checks

These checks apply to every dataset, regardless of input folder name.

### Routing
- Every artifact path MUST be inside `output/<dataset>/`.
- The generated/replay script `run_datadog_analysis.py`, if generated, MUST be inside `output/<dataset>/`.
  A script written to the project root, the top-level `output/` folder, or any other dataset folder is a routing failure.
- The final manifest MUST list expected path, actual path, and pass/fail status for every artifact.

### Normalised Data
- `normalised_data.json.records[]` MUST exist and contain every classified input record.
- `record_counts.total` MUST equal `len(records)`.
- For each source type, `record_counts.<type>` MUST equal the number of records with that `source_type`.
- Trace records MUST be classified as `trace` when they contain `trace_id` and `span_id`, even if they also
  contain fields like `status` or `timestamp`.
- Alert records MUST require `monitor_name` plus `priority` or alert `status`. A generic `status` field alone
  MUST NOT classify a trace as an alert.
- `analysis_period.from` and `analysis_period.to` MUST be computed from all normalized records, not only logs
  or alerts.

### Log Analysis
- `summary.total_errors` MUST count both `ERROR` and `CRITICAL` log records.
- Every `ERROR`/`CRITICAL` log record in `normalised_data.json.records[]` MUST appear in `all_errors[]`.
- DQ alert `count=N` values MUST feed `worst_columns[].rejection_count`.

### Security
- PII and credential evidence MUST be fully redacted. Do not leave email-like strings, bearer tokens, or
  credential-looking values in `security_report.json` or the final markdown report, even if the source text
  contains the word `redacted`.
- Security findings MUST include PII, credential, and unauthorized/brute-force evidence when those records exist.

### Metrics and Traces
- If trace records exist, `metrics_report.json` MUST include trace latency analysis and slowest traces.
- A metrics report that only summarizes host CPU/memory while traces exist is incomplete.

### Pipeline Health
- If Kafka lag metrics or Kafka lag log lines exist, `apm_report.json.kafka.topics[]` MUST include topic,
  consumer group/service, timestamps, and lag values.

### Dependency
- `dependency_report.json.dependency_graph` MUST be an object with `nodes` and `edges`.
- `dependency_report.json` MUST NOT use `service_graph`.
- Breakpoints MUST use `breakpoint_service`, not a generic `service` field.

### Root Cause
- `root_cause.json.summary.total_incidents` MUST equal `len(incidents)`.
- `blast_radius` MUST be numeric and equal the count of distinct affected services.
- `root_cause_category` MUST use the allowed enum from the Root Cause Analysis Agent:
  `RESOURCE_SATURATION`, `UPSTREAM_DEPENDENCY_FAILURE`, `DATA_QUALITY_DEGRADATION`,
  `PIPELINE_BACKPRESSURE`, `SECURITY_INCIDENT`, `CONFIGURATION_DRIFT`, `CAPACITY_SHORTFALL`,
  or `UNDETERMINED`.

### Recommendations
- Every recommendation MUST include `rank`, `priority`, `incident_id`, `title`, `description`, `action`,
  `affected_services`, and `evidence`.
- `priority` MUST use the configured enum, such as `P1_IMMEDIATE`, not a shortened value like `P1`.

### Patch Suggestions
- `patch_suggestions.json.summary` MUST exist.
- The output array MUST be named `patches`.
- Every patch MUST include `patch_id`, `incident_id`, `recommendation_ref`, `patch_type`, `risk_level`,
  `target_file`, `explanation`, `diff`, and `requires_human_review`.
- A security/log-redaction recommendation MUST NOT receive a Kafka autoscaling diff. Patch content must match
  the recommendation domain.

### Final Markdown
- The final report MUST include these sections:
  Executive Summary, Errors & Data Quality, Performance & Infrastructure, Pipeline Health, Security,
  Anomalies & Trends, Dependency & Breakpoint Analysis, Root Cause Analysis, Recommendations,
  Patch Suggestions, Appendix/Ingestion Summary.
- The final report MUST NOT expose raw or partially raw PII/credential values.

---

## Mandatory Acceptance Test For Sample `input/`

When the dataset name is `input` and the source files match this project sample, all checks below MUST pass.
If any fail, `validation_manifest.json.status` MUST be `invalid`.

- `datadog_logs_export_20260702.json` classified as `log` with 26 records.
- `datadog_metrics_export_20260702.csv` classified as `metric` with 35 records.
- `datadog_traces_export_20260702.json` classified as `trace` with 12 records.
- `datadog_monitor_alerts_20260702.json` classified as `alert` with 7 records.
- `datadog_infrastructure_export_20260702.json` classified as `infrastructure` with 10 records.
- `normalised_data.json.record_counts.total` equals 90.
- `normalised_data.json.analysis_period.to` equals `2026-07-02T10:00:01Z`.
- `log_analysis.json.summary.total_errors` equals 19.
- `security_report.json.findings[]` includes `PII_IN_LOGS`, `CREDENTIAL_LEAK`, and unauthorized/brute-force evidence for `user-service`.
- `security_report.json` and `datadog_analysis_report.md` do not contain `redacted@example.com`, `abc123redacted`, or any `Bearer ` token value.
- `apm_report.json.kafka.topics[]` includes lag values 125000 and 118000 for `ecommerce-events` / `checkout-consumer`.
- `dependency_report.json.breakpoints[].breakpoint_service` includes `checkout-consumer` for the Kafka lag incident.
- `root_cause.json` includes a `PIPELINE_BACKPRESSURE` incident and a `SECURITY_INCIDENT` incident.
- `root_cause.json.incidents[].blast_radius` is numeric.
- `patch_suggestions.json.summary.total_patches_generated` equals `len(patches)`.
- A security patch, if generated, has a redaction/logging-related diff, not a Kafka scaling diff.

---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator:

- The generated implementation MUST include a validator function that runs after all artifacts are written.
- The validator MUST inspect file contents, not only file paths.
- The validator MUST write `validation_manifest.json`.
- The final console/status output MUST say `valid` only when every required content check passes.
- If any check fails, the generated implementation MUST exit non-zero or clearly mark the run invalid.

Reject the generated implementation if the manifest can say `valid` while traces are misclassified, required
schema fields are missing, security evidence is unredacted, or the generated runner is outside `output/<dataset>/`.
