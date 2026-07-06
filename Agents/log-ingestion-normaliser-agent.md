# Log Ingestion & Normaliser Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Reads and ingests all exported Datadog files — logs (JSON), metrics (CSV), traces (JSON),
alerts (JSON), and infrastructure data (JSON). Normalises them into a single unified structure,
detects the source type and format of each entry, and tags every record so downstream agents
know exactly how to process it.

**Outputs:** `normalised_data.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
ingestion_config:
  # No hardcoded filenames. Point this at a folder — every file inside it is
  # scanned and auto-classified by content, not by name. This is what lets the
  # exact same agent work on a teammate's exports, a real Datadog account
  # export, or any future test data without editing this config.
  input_folder: "<path-to-your-input-folder>/"

  output_file: "output/<dataset>/normalised_data.json"

  settings:
    skip_unreadable_files: true   # true = skip files that fail to parse and continue | false = stop on first failure
    require_all_types:     false  # true = STOP if any of the 5 source types (logs/metrics/traces/alerts/infra) isn't found in the folder | false = ingest whatever types are present
    timestamp_format:      "auto" # iso8601 | unix | auto — auto is recommended for real-world exports with mixed formats
    encoding:               "utf-8"
    file_extensions_scanned: [".json", ".csv", ".log", ".ndjson"]
```

---

## Pre-requisites

- `input_folder` must exist and contain at least one readable file
- No specific filenames are required or assumed — any file matching `file_extensions_scanned`
  is inspected and classified by content
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST write only to the exact configured `output_file`.
- The configured `output_file` MUST be inside the resolved `output/<dataset>/` folder for the current input folder.
- For a folder input such as `input/`, all classified files in that folder are one dataset and produce one `normalised_data.json` under `output/input/`.
- This agent MUST NOT create output folders based on individual source filenames, source types, dates, service names, or existing files in `output/`.
- If `input_folder` and `output_file` do not map to the same dataset route, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST scan every file in `input_folder` matching `file_extensions_scanned` — never assume a fixed set of filenames
- MUST classify each file's source type (log / metric / trace / alert / infrastructure) purely from its
  structure and field names, per the Auto-Detection Rules below — filename and extension are hints only,
  never the deciding factor
- MUST detect the severity/level of each log entry — DEBUG, INFO, WARN, ERROR, CRITICAL
- MUST normalise all timestamps to ISO 8601 format regardless of source format (unix epoch, RFC3339, etc.)
- MUST tag every record with: `source_type`, `severity`, `service`, `timestamp`, `environment`
- MUST derive `environment` the same way for every source type: (1) use an explicit `environment`/`env` field if present, (2) else parse a `env:<value>` tag if present, (3) else default to `"unknown"` — never leave it blank
- MUST extract `source_ip` and `user` from log records when present in the message or fields (e.g. `ip=`, `user=`, `client_ip=`) and include them on the normalised record for downstream correlation (auth-failure grouping, security analysis) — set to `null` if absent, never fabricated
- MUST write all normalised records into a single `normalised_data.json` output file
- MUST report a summary of record counts per detected source type after ingestion, including which
  physical file each type was read from
- MUST stop and report if `require_all_types=true` and one of the 5 source types was not found in any file
- MUST skip and log (not crash on) any file that cannot be parsed or classified, if `skip_unreadable_files=true`

### MUST NOT
- MUST NOT hardcode, assume, or require any specific filename
- MUST NOT modify or delete the original input files
- MUST NOT drop any records during normalisation
- MUST NOT classify a file's type by filename alone — filename may be used only as a secondary hint
  when content is ambiguous
- MUST NOT proceed if output folder is not writable

---

## Auto-Detection Rules

Since real Datadog exports are never guaranteed to have predictable filenames, every file in
`input_folder` is classified by inspecting its actual structure:

| Detected as | Detection logic |
|---|---|
| `metric` | File extension is `.csv` **and** header row contains a `value` or `metric_name` column |
| `trace` | JSON array where records contain both `trace_id` and `span_id` fields |
| `alert` | JSON array where records contain `monitor_name` and (`priority` or `status`) fields |
| `log` | JSON array where records contain `timestamp` plus `message` or `level`/`severity`. A log record may also contain `host`; `host` alone MUST NOT make it infrastructure |
| `infrastructure` | JSON array where records contain `host` plus at least 3 numeric resource fields from: `cpu_pct`, `memory_pct`, `disk_pct`, `network_in`, `network_out`, and do NOT contain log-signature fields such as `message` plus `level`/`severity` |
| `unknown` | File does not match any signature above — logged and excluded from `normalised_data.json`, reported in the ingestion summary |

If a file matches more than one signature (e.g. ambiguous schema), the most specific match wins
in this priority order: `trace` > `alert` > `metric` > `log` > `infrastructure`.

**Critical anti-regression rule:** Datadog log exports commonly contain a `host` field. A generated
implementation MUST NOT classify a JSON file as infrastructure merely because the first record contains
`host`. Infrastructure classification requires actual resource metric fields (`cpu_pct`, `memory_pct`,
`disk_pct`, `network_in`, `network_out`) with numeric values. If a record has `timestamp`, `message`,
and `level`/`severity`, classify it as `log` even when it also has `host`, `environment`, and `tags`.

---

## Output File Schema - `normalised_data.json`

`normalised_data.json` MUST be a JSON object containing both run metadata and the complete normalized
record array. A summary-only file is invalid because downstream agents need the actual records.

```json
{
  "dataset_name": "input",
  "input_root": "<absolute-or-relative-input-folder>",
  "analysis_period": { "from": "2026-07-02T08:00:00Z", "to": "2026-07-02T10:00:01Z" },
  "record_counts": {
    "log": 26,
    "metric": 35,
    "trace": 12,
    "alert": 7,
    "infrastructure": 10,
    "total": 90
  },
  "classified_files": [
    { "path": "datadog_logs_export_20260702.json", "source_type": "log", "record_count": 26 },
    { "path": "datadog_metrics_export_20260702.csv", "source_type": "metric", "record_count": 35 }
  ],
  "skipped_files": [],
  "records": [
    {
      "record_id": "log_000001",
      "source_type": "log",
      "severity": "ERROR",
      "service": "payment-service",
      "environment": "prod",
      "timestamp": "2026-07-02T08:05:00Z",
      "message": "DB connection refused: connection to payments-db timed out",
      "tags": ["team:payments"],
      "source_ip": null,
      "user": null,
      "raw": { "original record fields": "preserved here" }
    }
  ]
}
```

Rules:
- `records` MUST contain every normalized record from every classified file. It is not optional.
- `record_counts.total` MUST equal `len(records)`.
- `record_counts.<source_type>` MUST equal the number of records in `records[]` with that `source_type`.
- `classified_files[]` MUST list each physical input file once with the detected `source_type` and record count.
- `samples` may be included for debugging, but samples MUST NOT replace `records`.
- Downstream agents MUST read from `records[]`; they MUST NOT depend on `samples[]` or summary-only counts.

---

## Normalised Record Schema

```json
{
  "record_id":    "unique identifier",
  "source_type":  "log | metric | trace | alert | infrastructure",
  "severity":     "DEBUG | INFO | WARN | ERROR | CRITICAL",
  "service":      "service or host name",
  "environment":  "prod | staging | dev | unknown",
  "timestamp":    "2026-07-02T10:00:00Z",
  "message":      "raw log message or description",
  "tags":         ["tag1", "tag2"],
  "source_ip":    "extracted client/source IP, or null if absent",
  "user":         "extracted username/account, or null if absent",
  "raw":          { "original record fields" }
}
```

---

## Execution Workflow

### Phase 0 — Discover Input Files
1. List every file in `input_folder` matching `file_extensions_scanned`
2. If the folder is empty or unreadable → STOP and report
3. Attempt to parse each file as CSV or JSON based on actual content, not extension
4. If a file fails to parse: log it as `unreadable`; skip if `skip_unreadable_files=true`, else STOP

### Phase 1 — Classify Each File
1. Run each successfully parsed file against the Auto-Detection Rules table
2. Record the detected `source_type` for that file
3. If `require_all_types=true` and one of the 5 expected types has zero matching files → STOP and
   report which type is missing
4. Files classified as `unknown` are excluded from ingestion and listed in the summary
5. Before accepting classification, run the negative check: any file whose records contain
   `timestamp`, `level`/`severity`, and `message` MUST appear in the `logs` classified-files list,
   not in `infrastructure`, even if the records also contain `host`

### Phase 2 — Ingest Logs (files classified as `log`)
1. Parse JSON array of log entries
2. Extract: `timestamp`, `level/severity`, `service`, `message`, `host`, `tags`, `environment`
3. Normalise severity to: DEBUG / INFO / WARN / ERROR / CRITICAL
4. Tag each record: `source_type: "log"`

### Phase 3 — Ingest Metrics (files classified as `metric`)
1. Parse CSV with headers
2. Extract: `timestamp`, `metric_name`, `value`, `host`, `service`, `tags`
3. Tag each record: `source_type: "metric"`
4. Normalise numeric values to float
5. Tag each record: `environment` per the derivation rule above

### Phase 4 — Ingest Traces (files classified as `trace`)
1. Parse JSON array of trace spans
2. Extract: `trace_id`, `span_id`, `parent_span_id` (if present — real Datadog trace exports usually include it), `service`, `operation`, `duration_ms`, `status`, `timestamp`
3. Tag each record: `source_type: "trace"`
4. Flag traces with `status: "error"` as severity ERROR
5. Tag each record: `environment` per the derivation rule above
6. If `parent_span_id` is absent from the source data entirely, set it to `null` on every span in that file and add the tag `"parent_span_id_missing"` to the file's ingestion summary entry — this tells the Dependency/Flow Analysis Agent it must fall back to timestamp-ordering within each `trace_id` (a lower-confidence approximation) instead of true parent/child linkage

### Phase 5 — Ingest Alerts (files classified as `alert`)
1. Parse JSON array of monitor alerts
2. Extract: `monitor_name`, `status`, `triggered_at`, `service`, `message`, `priority`
3. Tag each record: `source_type: "alert"`
4. Map alert priority to severity: P1→CRITICAL, P2→ERROR, P3→WARN, P4→INFO
5. Tag each record: `environment` per the derivation rule above

### Phase 6 — Ingest Infrastructure (files classified as `infrastructure`)
1. Parse JSON array of host metrics
2. Extract: `host`, `cpu_pct`, `memory_pct`, `disk_pct`, `network_in`, `network_out`, `timestamp`
3. Tag each record: `source_type: "infrastructure"`
4. Flag hosts with cpu_pct > 90 or memory_pct > 90 as severity WARN
5. Tag each record: `environment` per the derivation rule above

### Phase 7 — Write Output
1. Merge all normalised records into `records[]`
2. Sort `records[]` by timestamp ascending, then `record_id`
3. Build `record_counts`, `classified_files`, `skipped_files`, and `analysis_period`
4. Write the full object to `output/<dataset>/normalised_data.json`
5. Print ingestion summary showing which physical file each source type was read from

---

## Output Specification

| Artifact | Description |
|---|---|
| `normalised_data.json` | Object containing metadata, `record_counts`, `classified_files`, `skipped_files`, and a full `records[]` array of all ingested records tagged by source type, severity, service, environment, timestamp |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No files found | `input_folder` empty or wrong path | Verify the folder exists and contains at least one file matching `file_extensions_scanned` |
| File misclassified | Ambiguous schema matches multiple signatures | Check the Auto-Detection Rules priority order; add distinguishing fields to the source export if possible |
| File shows as `unknown` | Structure doesn't match any known signature | Verify the export includes the required identifying fields (e.g. `trace_id`+`span_id` for traces, `monitor_name` for alerts) |
| JSON parse error | Malformed input file | Validate JSON with a linter; if `skip_unreadable_files=true` it will be skipped and logged instead of stopping the run |
| CSV headers missing | Metrics file has no header row | Add header row to CSV |
| Timestamps inconsistent | Mixed formats in input | `timestamp_format: "auto"` already handles this by detecting format per record |
| Pipeline stops at Phase 0 | `require_all_types=true` but one type has zero matching files | Either add that export type to the folder, or set `require_all_types: false` to proceed with whatever's available |
| Output folder not writable | Permissions issue | Create `output/<dataset>/` folder manually |

---

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- Every input record from every classified input file MUST produce exactly one normalized record unless it is unreadable or malformed; skipped records must be counted with reasons.
- `normalised_data.json.records` MUST exist and MUST be a non-empty array whenever input records were classified.
- `record_id` MUST be unique across the entire `normalised_data.json`.
- `source_type` MUST be one of `log`, `metric`, `trace`, `alert`, or `infrastructure`.
- `timestamp` MUST be normalized to ISO-8601 UTC where possible. If a timestamp cannot be parsed, preserve the raw value under `raw` and set normalized `timestamp` to null rather than inventing one.
- Severity mapping MUST be deterministic. Do not infer CRITICAL/ERROR from vague text unless the rule is explicitly listed.
- For metrics, preserve `metric_name`, numeric `value`, `service`, `host`, and tags in `raw`.
- For traces, preserve `trace_id`, `span_id`, optional `parent_span_id`, `service`, `operation`, `duration_ms`, and `status` in `raw`.
- For alerts, preserve `monitor_name`, `status`, `priority`, `triggered_at`, `service`, and message in `raw`.
- For infrastructure, preserve host resource fields in `raw`; do not rename host as service except in the normalized service field where the schema requires a grouping key.
- The ingestion summary MUST list each file, classification, status, and record count.
- The output path MUST exactly match the configured `output_file`; do not rewrite it from the input filename or source type.
- A file shaped like `datadog_logs_export_20260702.json` with records containing `timestamp`, `level`,
  `service`, `message`, `host`, `environment`, and `tags` MUST be classified as `log`.
- The same log-shaped file MUST NOT appear under `classified_files.infrastructure`.
- If log-shaped input files exist, `record_counts.log` MUST be greater than 0 and downstream `log_analysis.json`
  MUST see those log records.
- A summary-only `normalised_data.json` that contains `classified_files` and counts but no full `records[]`
  array MUST be rejected.

Reject the generated output if source row counts and normalized record counts do not reconcile.
