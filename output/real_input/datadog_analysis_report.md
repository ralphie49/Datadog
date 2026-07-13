# Datadog Observability Analysis Report
**Dataset:** `real_input` | **Generated:** 2026-07-10T11:22:13Z
**Analysis Period:** 2026-07-07T10:49:00.000Z -> 2026-07-07T11:13:30.072Z
**Overall Health:** HEALTHY

---

## Executive Summary
- Total incidents identified: 0
- Critical issues: 0 | Error issues: 0 | Warnings: 0
- Top risks: _none identified -- no CRITICAL/HIGH-confidence findings in this analysis period._

---

## 1. Errors & Data Quality
_No application errors detected in this analysis period._

_No data-quality rejection evidence found in this analysis period._

## 2. Performance & Infrastructure
**Latency by service:**
_No trace-derived latency data available in this analysis period._

_No trace records were present in the input, so no slowest-trace ranking is available._

**Host resource health:**
| Host | CPU % | Memory % | Disk % | Verdict | Issues |
|---|---|---|---|---|---|
| AVDAILAB102-119 | 7.43 | - | - | 🟢 OK | - |

## 3. Pipeline Health
_No Kafka consumer lag metrics or log evidence were found in this analysis period._

_No checkpoint, SLA-breach, or backlog issues detected._

## 4. Security
_No security findings (PII exposure, credential leaks, unauthorized access) detected in this analysis period._

## 5. Anomalies & Trends
| Service | Anomaly | Confidence | Detail |
|---|---|---|---|
| AVDAILAB102-119 | CPU_SPIKE | MEDIUM | AVDAILAB102-119 cpu_pct reading 7.43 exceeded 2.0x its own prior baseline 3.27 |

## 6. Dependency & Breakpoint Analysis
_No service dependency graph could be built: the input contained no (or insufficient) trace spans to reconstruct call relationships. This is a valid empty result, not a parsing failure._

---

## Root Cause Analysis
_No incidents met the minimum-evidence-sources bar for root cause analysis in this analysis period._

---

## Recommendations
_No recommendations were generated -- no incidents required action in this analysis period._

---

## Patch Suggestions (Human Review Required)
⚠️ All patches require human review before applying.

_No patches were generated in this analysis period._

---

## Appendix — Ingestion Summary
| Source Type | Record Count |
|---|---|
| log | 1 |
| metric | 50 |
| trace | 0 |
| alert | 0 |
| infrastructure | 0 |

Total records ingested: 51

**Skipped/unreadable files:**
| File | Reason |
|---|---|
| datadog_traces_export_real.json | file is empty (0 bytes) -- not parseable JSON/CSV |

