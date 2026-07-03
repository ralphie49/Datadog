# Log Ingestion & Normaliser Agent
**Version:** 2.0.0 | **Domain:** Datadog Observability Analysis

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
  input_folder: "input/"

  output_file: "output/normalised_data.json"

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
- Output folder `output/` must be writable

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
| `infrastructure` | JSON array where records contain `host` plus at least 2 of: `cpu_pct`, `memory_pct`, `disk_pct`, `network_in`, `network_out` |
| `log` | JSON array where records contain `message` or `level`/`severity`, and do NOT match trace/alert/infrastructure signatures above |
| `unknown` | File does not match any signature above — logged and excluded from `normalised_data.json`, reported in the ingestion summary |

If a file matches more than one signature (e.g. ambiguous schema), the most specific match wins
in this priority order: `trace` > `alert` > `infrastructure` > `metric` > `log`.

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
1. Merge all normalised records into a single array
2. Sort by timestamp ascending
3. Write to `output/normalised_data.json`
4. Print ingestion summary showing which physical file each source type was read from

---

## Output Specification

| Artifact | Description |
|---|---|
| `normalised_data.json` | Unified array of all ingested records, tagged by source type, severity, service, environment, timestamp |

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
| Output folder not writable | Permissions issue | Create `output/` folder manually |

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-02 | log-ingestion-normaliser-agent | Initial release — multi-format ingestion, unified normalisation, source tagging |
| 2.0.0 | 2026-07-03 | log-ingestion-normaliser-agent | Removed all hardcoded filename assumptions — agent now scans an input folder and classifies each file's type purely by content/schema, so it works unmodified against real Datadog exports with arbitrary filenames |
| 2.1.0 | 2026-07-03 | log-ingestion-normaliser-agent | Added consistent `environment` derivation across all source types; added `parent_span_id` extraction with documented fallback flag for traces; added `source_ip`/`user` extraction from logs for downstream security correlation |