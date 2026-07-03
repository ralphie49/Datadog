# Performance & Infrastructure Health Agent
**Version:** 1.3.0 | **Domain:** Datadog Observability Analysis

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
  input_file:  "output/normalised_data.json"
  output_file: "output/metrics_report.json"

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

- `normalised_data.json` must exist (produced by Log Ingestion & Normaliser Agent)
- Input must contain records of `source_type: "metric"`, `"trace"`, and `"infrastructure"`
- Output folder `output/` must be writable

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist)
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

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-03 | performance-infrastructure-health-agent | Merged release — combines service-level performance analysis with host-level infrastructure health into a single agent |
| 1.1.0 | 2026-07-03 | performance-infrastructure-health-agent | Clarified critical_issues/warn_issues must aggregate across all subsections (latency, throughput, error rate, infra), not infra findings alone; added analysis_period population rule |
| 1.2.0 | 2026-07-03 | performance-infrastructure-health-agent | hosts[].issues must include HOST_DOWN and a consistent verdict whenever that host appears in downtime_issues, so the two lists can no longer disagree |
| 1.3.0 | 2026-07-03 | performance-infrastructure-health-agent | Fixed observed failure mode where hosts[] contained duplicate phantom entries per host (all-null rows) instead of merging HOST_DOWN into the existing per-host row — hosts[] is now explicitly specified as exactly one entry per distinct host, with a count cross-check against total_hosts_analysed before writing output |