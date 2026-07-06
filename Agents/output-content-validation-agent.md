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

## Mandatory Semantic Checks (added after a real run reported "valid" while containing real bugs)

A prior run of this pipeline produced `validation_manifest.json` with `status: "valid"` and every
check `"passed"` while `anomaly_report.json` had a Kafka lag spike with `baseline: 0`,
`correlated_anomalies: 0` despite an obvious dependency-connected multi-service correlation,
`dependency_report.json` had an edge direction that contradicted its own declared breakpoint, and
`datadog_analysis_report.md` had a misaligned table and silently dropped CRITICAL security findings.
A validator that only checks file existence, JSON parsing, and field presence will miss all of these —
it MUST also check field *values* against the other artifacts' own data. Add these checks:

- **Baseline sanity:** for every anomaly in `anomaly_report.json.anomalies[]` whose `anomaly_type` ends
  in `_SPIKE` or `_DROP`, if `normalised_data.json` contains 2+ chronological readings of the
  underlying metric for that service before the anomaly's timestamp, `baseline` MUST NOT be `0`. Fail
  this check and list the offending anomaly if it is.
- **Correlation completeness:** if 2+ anomalies in `anomaly_report.json` occur within that agent's
  correlation window and their services are identical or connected via any edge in
  `dependency_report.json.dependency_graph` (any hop, either direction), `summary.correlated_anomalies`
  MUST be >= 1. Fail this check if it is `0` while such a pair exists.
- **Breakpoint/edge consistency:** for every entry in `dependency_report.json.breakpoints[]`, no edge in
  `dependency_graph.edges` may have `to == breakpoint_service` where `from` is one of that breakpoint's
  own `downstream_impact` services. Fail this check and name the contradictory edge if found.
- **Markdown table integrity:** for every Markdown table in `datadog_analysis_report.md`, every data row
  MUST have the same number of `|`-separated cells as its header row, and the cell under a
  `Service`/`Host` column MUST NOT be a bare integer. Fail this check and quote the offending row if
  violated.
- **Markdown severity completeness:** every `CRITICAL`-severity finding present in `security_report.json`,
  `metrics_report.json`, or `apm_report.json` MUST be findable in `datadog_analysis_report.md` (by
  `issue_type` + `service` substring match). Fail this check and name the missing finding if a CRITICAL
  finding from any domain report is absent from the rendered report.

`validation_manifest.json.status` MUST be `"invalid"` if any of the above semantic checks fail — a
manifest cannot say `"valid"` merely because every artifact exists and parses; it must also be
internally consistent and free of the specific defect classes above.

## Mandatory Acceptance Test For Sample `input/` (regression check only — see Anti-Hardcoding Contract above; these values MUST be produced by the general algorithm, never embedded as literals)

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


---

## 🚫 Final Hardcoding Check (applies on top of everything above)

Before accepting this agent's implementation as done: pick any literal value in its output (a service
name, an ID, a count, a percentage) and ask "would this exact value still appear if I fed the agent a
different input file with different data?" If the answer is yes for a value that should depend on the
input, the implementation is hardcoded and must be rewritten to derive that value from the actual input
at runtime. This check applies to every JSON/Markdown artifact this agent produces, not just the fields
called out elsewhere in this file.