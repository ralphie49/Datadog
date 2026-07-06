# Performance & Infrastructure Health Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Analyses normalised metrics, trace, and infrastructure data to detect performance
degradation and resource health issues across any monitored system. Covers both
service-level performance (latency, throughput, error rate) and host-level
infrastructure health (CPU/memory/disk, network, storage, host downtime) — merged
into a single agent since both ultimately trace back to the same underlying resource
pressure.

**Outputs:** `metrics_report.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
performance_infra_config:
  input_file:  "output/<dataset>/normalised_data.json"
  output_file: "output/<dataset>/metrics_report.json"

  performance_thresholds:
    latency_warn_ms:         500
    latency_critical_ms:     1000
    throughput_drop_pct:     30
    error_rate_warn_pct:     5
    error_rate_critical_pct: 15

  infra_thresholds:
    cpu_warn_pct:             75
    cpu_critical_pct:         90
    memory_warn_pct:          75
    memory_critical_pct:      90
    disk_warn_pct:            80
    disk_critical_pct:        95
    network_saturation_mbps:  900
    small_files_warn:         1000
    vacuum_overdue_hours:     168
    host_downtime_warn_min:   5
```

---

## Pre-requisites

- `output/<dataset>/normalised_data.json` must exist (produced by Log Ingestion & Normaliser Agent)
- Input must contain records of `source_type: "metric"`, `"trace"`, and `"infrastructure"`
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_file` and write only the configured `output_file`.
- `input_file` and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from host names, services, metric names, dates, the input filename, or existing files in `output/`.
- If the input and output paths point to different dataset folders, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist)
- MUST read metric, trace, and infrastructure records from `normalised_data.json.records[]`; do not use
  `samples`, `classified_files`, or summary counts as the input dataset.
- MUST analyse all trace records for latency and error rate per service
- MUST calculate average, p95, and p99 latency per service
- MUST detect throughput drops compared to the baseline period
- MUST analyse all infrastructure records for CPU, memory, disk, network per host
- MUST detect storage-specific issues — small files, VACUUM overdue, write conflicts
- MUST identify hosts that were down or unreachable during the analysis period
- MUST calculate a health score per host and rank hosts worst-first
- MUST group all findings by service or host and by severity
- MUST write all findings to `metrics_report.json`

### MUST NOT
- MUST NOT accept a summary-only `normalised_data.json` that has no `records[]` array
- MUST NOT skip metric/infra records with null values — flag them as DATA_MISSING
- MUST NOT assume all services or hosts have the same thresholds
- MUST NOT modify the input `normalised_data.json`

---

## Performance Issue Types

| Issue Type | Description |
|---|---|
| `HIGH_LATENCY` | Trace duration exceeded latency threshold |
| `HIGH_ERROR_RATE` | Error rate exceeded threshold |
| `THROUGHPUT_DROP` | Throughput dropped more than threshold % |

## Infrastructure Issue Types

| Issue Type | Description |
|---|---|
| `HIGH_CPU` | CPU usage exceeded threshold |
| `HIGH_MEMORY` | Memory usage exceeded threshold |
| `HIGH_DISK` | Disk usage exceeded threshold |
| `NETWORK_SATURATION` | Network throughput exceeded threshold |
| `HOST_DOWN` | Host was unreachable during analysis period |
| `SMALL_FILES_EXCESS` | Too many small files in storage |
| `VACUUM_OVERDUE` | Storage VACUUM/cleanup not run within expected window |
| `WRITE_CONFLICT` | Concurrent write conflicts detected in storage logs |
| `RESOURCE_EXHAUSTION` | 3+ resources simultaneously above critical |
| `DATA_MISSING` | Host/service present but metrics are null or missing |

---

## Output Schema — `metrics_report.json`

```json
{
  "summary": {
    "total_services_analysed": 0,
    "total_hosts_analysed":    0,
    "services_with_issues":    0,
    "hosts_with_issues":       0,
    // critical_issues / warn_issues MUST aggregate across every subsection this agent produces --
    // latency, throughput, error rate, AND host/infra findings combined, not infra findings alone
    "critical_issues":         0,
    "warn_issues":             0,
    "hosts_down":              0,
    "analysis_period": { "from": "", "to": "" }
  },
  "latency": {
    "slowest_traces": [],
    "by_service": [
      {
        "service": "payment-service",
        "avg_ms":  450,
        "p95_ms":  980,
        "p99_ms":  1200,
        "verdict": "CRITICAL"
      }
    ]
  },
  "throughput": [],
  "hosts": [
    {
      "host":         "host-01",
      "cpu_pct":      92,
      "memory_pct":   88,
      "disk_pct":     45,
      "network_mbps": 450,
      "health_score": 35,
      "verdict":      "CRITICAL",
      "issues":       ["HIGH_CPU", "HIGH_MEMORY"]
    }
  ],
  "storage_issues": [
    {
      "issue_type":    "VACUUM_OVERDUE",
      "target":        "events_delta_table",
      "last_vacuum":   "2026-06-25T10:00:00Z",
      "hours_overdue": 175
    }
  ],
  "network": [],
  "all_issues": []
}
```

---

## Execution Workflow

### Phase 0 — Load Input
1. Read `normalised_data.json`
2. Separate metric, trace, and infrastructure records

### Phase 1 — Analyse Latency & Throughput
1. Calculate avg, p95, p99 latency per service; flag WARN/CRITICAL per thresholds
2. Extract top 5 slowest traces
3. Split analysis period into baseline (first half) and current (second half); flag services with throughput drop > `throughput_drop_pct`

### Phase 2 — Analyse Error Rate
1. Calculate error rate per service = error_traces / total_traces × 100
2. Flag WARN/CRITICAL per thresholds

### Phase 3 — Host Resource Analysis
1. For each host extract CPU, memory, disk, network
2. Flag breaches against warn/critical thresholds
3. Detect RESOURCE_EXHAUSTION if 3+ resources above critical simultaneously
4. Calculate health score per host (100 − weighted penalty per breach); rank worst-first

### Phase 4 — Host Availability & Storage Health
1. Detect gaps in host metric timestamps > `host_downtime_warn_min` → flag HOST_DOWN. A single host may have
   multiple qualifying gaps in the analysis period; that produces multiple entries in `downtime_issues`
   (one per gap, each with its own from/to), but see step 2 — it MUST NOT produce multiple entries for the
   same host in `hosts[]`
2. The `hosts[]` array MUST contain exactly one entry per distinct `host` name — it is a per-host summary, not
   a per-event log. Build it by first computing one resource-usage summary row per host (Phase 3), then, for
   any host that also has one or more `HOST_DOWN` gaps from step 1, MERGE into that same existing row: append
   `"HOST_DOWN"` to its `issues` array (do not duplicate if already present) and upgrade `verdict` to at least
   WARN (CRITICAL if combined with other breaches). NEVER create a second, separate `hosts[]` entry for a host
   that already has a row, and NEVER write a `hosts[]` entry with all resource fields (`cpu_pct`, `memory_pct`,
   etc.) set to `null` just to represent a downtime gap — that data belongs only in `downtime_issues`, not as
   a phantom host row. Before writing output, verify: count of distinct `host` values in `hosts[]` equals count
   of distinct `host` values in `total_hosts_analysed` — if not, entries were duplicated and must be merged
3. Scan logs for `small files`, `VACUUM`, `OPTIMIZE`, `write conflict`; flag against thresholds

### Phase 5 — Network Analysis
1. Extract network in/out per host; flag hosts exceeding `network_saturation_mbps`

### Phase 6 — Write Output
1. Build combined summary statistics
2. Write `metrics_report.json`

---

## Implementation Notes (pseudocode — MUST be followed structurally, not just in spirit)

Three specific defects have been observed in prior generated code for this agent. Each is caused by a
generic implementation shortcut, not a misunderstanding of the rule — so the pseudocode below is
deliberately literal about the shortcut to avoid.

**1. `analysis_period` MUST be computed from the full, untouched record set — never from a loop variable
that may have been reused/shadowed.**
```
all_records = normalised_data["records"]          # keep this reference untouched, never reassign it
trace_records = [r for r in all_records if r.source_type == "trace"]
infra_records = [r for r in all_records if r.source_type == "infrastructure"]
metric_records = [r for r in all_records if r.source_type == "metric"]
# ... do NOT write: for host_name, records in records_by_host.items(): ...
#     (reusing the name "records" for a per-host loop variable silently shadows all_records
#      in many languages/scopes — use a distinct name, e.g. "host_records", for any per-host loop)
analysis_period.from, analysis_period.to = min/max timestamp across all_records  # the ORIGINAL full set
```
Self-check before writing output: `analysis_period.to` must be >= the max timestamp of every individual
record this agent read, including trace records. If it is earlier than any trace timestamp processed,
the wrong variable was used to compute it.

**2. `slowest_traces` MUST actually be populated, not left as a placeholder empty list.**
```
slowest_traces = sorted(trace_records, key=lambda t: t.duration_ms, reverse=True)[:5]
# each entry: {service, operation, duration_ms, timestamp, trace_id}
```
Self-check: if `trace_records` is non-empty, `slowest_traces` MUST be non-empty.

**3. `critical_issues`/`warn_issues` MUST count host/infra findings, not just latency/throughput findings.**
The most common defect: host-level issues (`HIGH_CPU`, `HIGH_MEMORY`, `HOST_DOWN`, etc.) get added to a
per-host `issues` set/list for display, but are never also appended to the flat `all_issues` list that the
summary counters read from. Both writes MUST happen for every host-level issue:
```
for host in hosts:
    for issue_type in host.issues:               # e.g. HIGH_CPU, HOST_DOWN, NETWORK_SATURATION
        all_issues.append({service: host.host, issue_type: issue_type, verdict: host.verdict, description: ...})
        # ^ this append is required in addition to host.issues.add(issue_type) — do not skip it
```
Self-check before writing output: `critical_issues` = count of CRITICAL entries in `all_issues`, and
`all_issues` must include an entry for every host whose `verdict` is WARN or CRITICAL, not only services
with latency/throughput issues. If any host in `hosts[]` has verdict != OK but contributed zero entries to
`all_issues`, that host's issues were not aggregated into the summary and must be added.

## Self-Test Cases (run against this project's own sample data before considering this agent done)

Given the provided `datadog_traces_export_20260702.json` (max trace timestamp `2026-07-02T10:00:01Z`) and
`datadog_infrastructure_export_20260702.json`:
- `metrics_report.json.summary.analysis_period.to` MUST equal `2026-07-02T10:00:01Z` (the trace timestamp),
  not an earlier infrastructure-only timestamp like `09:50:00Z`.
- `metrics_report.json.latency.slowest_traces` MUST be non-empty and MUST include the `order-service`
  `create_order` span with `duration_ms: 4200`.
- `metrics_report.json.summary.critical_issues` MUST be >= 4 (3 CRITICAL latency verdicts + host-03's
  CRITICAL verdict from HIGH_CPU/HIGH_MEMORY/NETWORK_SATURATION), and `warn_issues` MUST be >= 3 (the three
  WARN-verdict hosts). If `critical_issues` is 3 or `warn_issues` is 0, host findings were not aggregated
  into `all_issues` and the implementation must be corrected.

---

## Output Specification

| Artifact | Description |
|---|---|
| `metrics_report.json` | Latency/throughput/error rate per service, resource usage and health score per host, storage issues, network saturation, host downtime |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No metrics found | Input has no metric/trace records | Verify sample data was ingested correctly |
| All latency critical | Threshold too low | Raise `latency_warn_ms` / `latency_critical_ms` |
| All hosts DATA_MISSING | Metric fields have different names | Check field names in sample_infrastructure.json |
| VACUUM_OVERDUE always firing | Old test data timestamps | Use recent timestamps in sample data |

---

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- Host resource evaluation MUST consider every infrastructure record for a host, not only the latest record.
- A host that breached CPU, memory, disk, or network thresholds at any point in the analysis period must carry that issue unless the report explicitly separates current state from historical findings.
- For each host, compute current values from the latest record and worst observed values across the period. If the output schema only has `cpu_pct`, `memory_pct`, `disk_pct`, and `network_mbps`, those values MUST represent the worst observed values used for issue detection.
- Severity MUST follow configured thresholds exactly: warn threshold creates WARN; critical threshold creates CRITICAL. Do not mark a warn-level breach as CRITICAL unless a documented combined-health rule upgrades it and the reason is recorded.
- `hosts[]` MUST contain exactly one row per distinct infrastructure host.
- `hosts[]` MUST be sorted worst-first: CRITICAL before WARN before OK, then lower `health_score`, then host name.
- Every host issue in `hosts[].issues` MUST also appear once in `all_issues`.
- `summary.critical_issues` and `summary.warn_issues` MUST be computed from `all_issues`, not separately guessed.
- `summary.hosts_with_issues` MUST equal the number of distinct host names in `all_issues` where issue type is an infrastructure issue.
- `HOST_DOWN` MUST be based on documented gap logic. If the dataset is sparse and no expected sampling interval is known, record uncertainty instead of blindly treating every large gap as downtime.
- If trace records exist, `latency.slowest_traces` MUST be non-empty and sorted by `duration_ms` descending.
- Do not flag a host as CPU/memory/disk critical unless that host's own observed values crossed the configured
  threshold. Do not mark a host wrong merely because another host crossed a threshold.
- Reject the output if a host such as `host-03` is flagged for high CPU or memory while its own max CPU and
  memory values in the report are below the configured threshold that the reason claims was breached.

Reject the generated output if any host breach present in the input disappears from both `hosts[].issues` and `all_issues`.
