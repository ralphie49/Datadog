# Datadog Analyser Orchestrator

## Purpose
Enable **GitHub Copilot / Claude** as a **Senior Observability Engineer** orchestrating a complete **Datadog log and metrics analysis pipeline** across any monitored system.

**Technology:** Python, Datadog Exported Files (JSON / CSV), Markdown

**Invocation Examples:**
- `Datadog Analyser Orchestrator` *(reads config from this file, runs all agents)*
- `Datadog Analyser Orchestrator (logs only)` *(runs only log-related agents)*
- `Datadog Analyser Orchestrator (full analysis)` *(runs all agents end to end)*

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
orchestrator_config:
  pipeline_name: "Datadog Observability Analysis"
  version: "2.2.0"

  # PATH CONVENTION: every path in this file and every agent file is relative to this
  # project's root folder (wherever you place input/, output/, and agents/ as siblings).
  # The root folder itself can be named anything — nothing anywhere in this pipeline
  # hardcodes a project name. Just keep input/, output/, and agents/ as siblings under
  # whatever root you use, and run the orchestrator from that root.

  # ── Input Files ────────────────────────────────────────────────────────────
  # No filenames are hardcoded. The orchestrator points to a FOLDER; the
  # Log Ingestion & Normaliser Agent scans every file in it and auto-detects
  # each file's type (logs / metrics / traces / alerts / infrastructure) by
  # inspecting its structure and field names — never by filename or extension
  # alone. This means it works unmodified against any real Datadog export,
  # regardless of what the files are named or what date is in the filename.
  input:
    input_folder: "input/"

  # ── Output Folder ──────────────────────────────────────────────────────────
  output:
    folder: "output/"
    normalised_data:  "output/normalised_data.json"
    log_analysis:     "output/log_analysis.json"
    metrics_report:   "output/metrics_report.json"
    apm_report:       "output/apm_report.json"
    security_report:  "output/security_report.json"
    anomaly_report:   "output/anomaly_report.json"
    dependency_report: "output/dependency_report.json"
    root_cause:       "output/root_cause.json"
    recommendations:  "output/recommendations.json"
    patch_suggestions: "output/patch_suggestions.json"
    final_report:     "output/datadog_analysis_report.md"

  # ── Analysis Settings ──────────────────────────────────────────────────────
  settings:
    error_threshold:       "ERROR"
    latency_threshold_ms:  1000
    anomaly_sensitivity:   "medium"
    max_recommendations:   10
    max_patches:            10
```

---

## Pipeline Overview

```
Input Files (logs / metrics / traces / alerts / infrastructure)
  ↓
[Phase 0] Log Ingestion & Normaliser Agent        — normalise and classify all input files
[Phase 1] Error & Data Quality Agent              — detect errors + DQ rejection/quarantine issues
[Phase 2] Performance & Infrastructure Health Agent — latency, throughput, CPU/memory/disk, host health
[Phase 3] Pipeline Health Monitor Agent            — Kafka lag, checkpoint, SLA breaches
[Phase 4] Security Audit Agent                     — PII exposure, unauthorised access, compliance
[Phase 5] Anomaly Detection Agent                  — anomalies and degradation over time
[Phase 6] Dependency/Flow Analysis Agent           — service dependency graph, breakpoint identification
[Phase 7] Root Cause Analysis Agent                — correlate all findings, suggest fixes
[Phase 8] Code Patch Generator Agent               — draft patch suggestions for human review
[Phase 9] Report Generation Agent                  — final markdown report
```

---

## Agent Invocations

### Phase 0 — Log Ingestion & Normaliser Agent
**Agent:** `agents/log-ingestion-normaliser-agent.md`

```yaml
context:
  input_folder: "input/"
  output_file:  "output/normalised_data.json"
```

**Expected output:** `normalised_data.json`
**STOP condition:** If the input folder is empty, unreadable, or contains no file that can be
classified as logs/metrics/traces/alerts/infrastructure after auto-detection → report and stop.

---

### Phase 1 — Error & Data Quality Agent
**Agent:** `agents/error-data-quality-agent.md`

```yaml
context:
  input_file:  "output/normalised_data.json"
  output_file: "output/log_analysis.json"
  error_threshold: "ERROR"
```

**Expected output:** `log_analysis.json` — classified application errors + DQ rejection rates, quarantine trends, worst columns

---

### Phase 2 — Performance & Infrastructure Health Agent
**Agent:** `agents/performance-infrastructure-health-agent.md`

```yaml
context:
  input_file:           "output/normalised_data.json"
  output_file:          "output/metrics_report.json"
  latency_threshold_ms: 1000
```

**Expected output:** `metrics_report.json` — latency/throughput per service, host resource health, storage issues

---

### Phase 3 — Pipeline Health Monitor Agent
**Agent:** `agents/pipeline-health-monitor-agent.md`

```yaml
context:
  input_file:  "output/normalised_data.json"
  output_file: "output/apm_report.json"
```

**Expected output:** `apm_report.json` — Kafka lag, checkpoint health, SLA breaches, pipeline backlog

---

### Phase 4 — Security Audit Agent
**Agent:** `agents/security-audit-agent.md`

```yaml
context:
  input_file:  "output/normalised_data.json"
  output_file: "output/security_report.json"
```

**Expected output:** `security_report.json` — PII exposure, unauthorised access, compliance violations

---

### Phase 5 — Anomaly Detection Agent
**Agent:** `agents/anomaly-detection-agent.md`

```yaml
context:
  input_files:
    - "output/log_analysis.json"
    - "output/metrics_report.json"
    - "output/apm_report.json"
    - "output/security_report.json"
  output_file:         "output/anomaly_report.json"
  anomaly_sensitivity: "medium"
```

**Expected output:** `anomaly_report.json` — detected anomalies, degradation trends across all data

---

### Phase 6 — Dependency/Flow Analysis Agent
**Agent:** `agents/dependency-flow-analysis-agent.md`

```yaml
context:
  input_files:
    - "output/normalised_data.json"
    - "output/metrics_report.json"
    - "output/apm_report.json"
    - "output/anomaly_report.json"
  output_file: "output/dependency_report.json"
```

**Expected output:** `dependency_report.json` — service dependency graph, identified breakpoints, cascading failure chains

---

### Phase 7 — Root Cause Analysis Agent
**Agent:** `agents/root-cause-analysis-agent.md`

```yaml
context:
  input_files:
    - "output/log_analysis.json"
    - "output/metrics_report.json"
    - "output/apm_report.json"
    - "output/security_report.json"
    - "output/anomaly_report.json"
    - "output/dependency_report.json"
  output_files:
    root_cause:      "output/root_cause.json"
    recommendations: "output/recommendations.json"
  max_recommendations: 10
```

**Expected output:** `root_cause.json` + `recommendations.json` — correlated root cause (incorporating dependency breakpoints) and ranked fixes

---

### Phase 8 — Code Patch Generator Agent
**Agent:** `agents/code-patch-generator-agent.md`

```yaml
context:
  input_files:
    - "output/recommendations.json"
    - "output/root_cause.json"
  output_file:  "output/patch_suggestions.json"
  max_patches:  10
```

**Expected output:** `patch_suggestions.json` — draft patches for P1/P2 recommendations, human review required before applying

---

### Phase 9 — Report Generation Agent
**Agent:** `agents/report-generation-agent.md`

```yaml
context:
  input_files:
    - "output/normalised_data.json"
    - "output/log_analysis.json"
    - "output/metrics_report.json"
    - "output/apm_report.json"
    - "output/security_report.json"
    - "output/anomaly_report.json"
    - "output/dependency_report.json"
    - "output/root_cause.json"
    - "output/recommendations.json"
    - "output/patch_suggestions.json"
  output_file: "output/datadog_analysis_report.md"
```

**Expected output:** `datadog_analysis_report.md` — final human-readable report with all findings, dependency analysis, root cause, recommendations, and patch suggestions

---

## Completion Checklist

| Phase | Agent | Status |
|---|---|---|
| 0 | Log Ingestion & Normaliser | [ ] |
| 1 | Error & Data Quality | [ ] |
| 2 | Performance & Infrastructure Health | [ ] |
| 3 | Pipeline Health Monitor | [ ] |
| 4 | Security Audit | [ ] |
| 5 | Anomaly Detection | [ ] |
| 6 | Dependency/Flow Analysis | [ ] |
| 7 | Root Cause Analysis | [ ] |
| 8 | Code Patch Generator | [ ] |
| 9 | Report Generation | [ ] |

**Analysis is complete when all boxes are checked and `datadog_analysis_report.md` is generated.**

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-02 | datadog-analyser-orchestrator | Initial release — 10 agent pipeline, universal log/metric/trace/alert analysis |
| 2.0.0 | 2026-07-03 | datadog-analyser-orchestrator | Merged Error+DQ and Performance+Infrastructure agents; renamed agents; added Dependency/Flow Analysis and Code Patch Generator agents |
| 2.1.0 | 2026-07-03 | datadog-analyser-orchestrator | Bugfix pass based on a real run that produced defective output: root-cause-analysis-agent → 1.3.0 (fixed all-incidents-UNDETERMINED defaulting, empty-string downstream_symptoms), dependency-flow-analysis-agent → 1.4.0 (fixed zero-breakpoints-despite-evidence via a concrete confidence formula and mandatory self-check), performance-infrastructure-health-agent → 1.3.0 (fixed duplicate phantom HOST_DOWN host rows), report-generation-agent → 1.3.0 (added mechanical pre-write validation checklist). Agents not touched (log-ingestion-normaliser, error-data-quality, pipeline-health-monitor, security-audit, anomaly-detection, code-patch-generator) were not implicated in the observed failures |
| 2.2.0 | 2026-07-03 | datadog-analyser-orchestrator | Second bugfix pass from a follow-up run: anomaly-detection-agent → 1.2.0 (fixed per-anomaly timestamps defaulting to analysis_period.from, which broke downstream time-window correlation; fixed duplicate CORRELATED_ANOMALY entries; fixed anomaly_type/description mismatches), pipeline-health-monitor-agent → 1.3.0 (fixed pipelines_with_issues exceeding total_pipelines_analysed, topic field being overwritten by consumer_group, fabricated SLA_BREACH entries from generic retry logs, missing per-entry timestamps), dependency-flow-analysis-agent → 1.5.0 (fixed 10+ duplicate breakpoint entries for one real incident, fixed downstream_impact only tracing one hop and under-reporting real cascades), security-audit-agent → 1.3.0 (analysis_period schema), root-cause-analysis-agent → 1.4.0 (added a safeguard so a severe finding can no longer be silently dropped when an upstream timestamp is corrupted), report-generation-agent → 1.4.0 (closed a gap where bare issue_type labels reappeared via the standalone-finding fallback path, and added a duplicate-top-risk check). The analysis_period-as-array bug was independently present in three agents (pipeline-health-monitor, dependency-flow-analysis, security-audit) and is now worded identically in all three as an explicit object-only requirement |