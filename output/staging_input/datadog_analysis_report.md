# Datadog Observability Analysis Report
**Dataset:** `staging_input` | **Generated:** 2026-07-10T11:26:34Z
**Analysis Period:** 2026-07-15T14:00:00.000Z -> 2026-07-15T15:02:01.000Z
**Overall Health:** CRITICAL

---

## Executive Summary
- Total incidents identified: 2
- Critical issues: 14 | Error issues: 7 | Warnings: 5
- Top risks:
  1. video-encoder identified as origin; failure propagated to cdn-gateway, recommendation-engine within 5 minutes
  2. host enc-host-01 flagged HIGH_DISK (verdict CRITICAL)

---

## 1. Errors & Data Quality
| Service | Issue | Severity | Detail |
|---|---|---|---|
| video-encoder | RESOURCE_EXHAUSTION | 🔴 CRITICAL | Disk usage on /var/encode/tmp reached 98%, encoder pool degraded |
| video-encoder | RESOURCE_EXHAUSTION | 🔴 CRITICAL | Disk usage on /var/encode/tmp reached 99%, encoder pool offline |
| video-encoder | RESOURCE_EXHAUSTION | 🟠 ERROR | Encode job failed for asset_id=9932: No space left on device |
| video-encoder | RESOURCE_EXHAUSTION | 🟠 ERROR | Encode job failed for asset_id=9933: No space left on device |
| video-encoder | RESOURCE_EXHAUSTION | 🟠 ERROR | Encode job failed for asset_id=9934: No space left on device |

_...2 more error(s) not shown; see log_analysis.json.all_errors for the full list._

_No data-quality rejection evidence found in this analysis period._

## 2. Performance & Infrastructure
**Latency by service:**
| Service | Avg (ms) | P95 (ms) | P99 (ms) | Verdict |
|---|---|---|---|---|
| cdn-gateway | 2683.8 | 5285.0 | 5297.0 | 🔴 CRITICAL |
| video-encoder | 2623.8 | 5193.5 | 5206.7 | 🔴 CRITICAL |
| recommendation-engine | 4800.0 | 4800 | 4800 | 🔴 CRITICAL |
| billing-service | 200.0 | 200 | 200 | 🟢 OK |

**Slowest traces:**
| Service | Operation | Duration (ms) | Timestamp |
|---|---|---|---|
| cdn-gateway | fetch_asset | 5300 | 2026-07-15T14:27:00.000Z |
| video-encoder | encode_request | 5210 | 2026-07-15T14:27:01.000Z |
| cdn-gateway | fetch_asset | 5200 | 2026-07-15T14:26:00.000Z |
| video-encoder | encode_request | 5100 | 2026-07-15T14:26:01.000Z |
| recommendation-engine | get_recommendations | 4800 | 2026-07-15T14:27:02.000Z |

**Host resource health:**
| Host | CPU % | Memory % | Disk % | Verdict | Issues |
|---|---|---|---|---|---|
| enc-host-01 | 91.0 | 74.0 | 99.0 | 🔴 CRITICAL | HIGH_CPU, HIGH_DISK, HOST_DOWN |
| cdn-host-01 | 60.0 | 48.0 | 36.0 | 🔴 CRITICAL | NETWORK_SATURATION, HOST_DOWN |
| bill-host-01 | 22.0 | 35.0 | 28.0 | 🟡 WARN | HOST_DOWN |
| rec-host-01 | 28.0 | 38.0 | 30.0 | 🟡 WARN | HOST_DOWN |

**All performance/infra issues:**
| Service | Issue | Severity |
|---|---|---|
| cdn-gateway | HIGH_ERROR_RATE | 🔴 CRITICAL |
| video-encoder | HIGH_ERROR_RATE | 🔴 CRITICAL |
| recommendation-engine | HIGH_ERROR_RATE | 🔴 CRITICAL |
| enc-host-01 | HIGH_CPU | 🔴 CRITICAL |
| enc-host-01 | HIGH_DISK | 🔴 CRITICAL |
| enc-host-01 | HOST_DOWN | 🔴 CRITICAL |
| cdn-host-01 | NETWORK_SATURATION | 🔴 CRITICAL |
| cdn-host-01 | HOST_DOWN | 🔴 CRITICAL |
| cdn-gateway | HIGH_LATENCY | 🔴 CRITICAL |
| video-encoder | HIGH_LATENCY | 🔴 CRITICAL |
| recommendation-engine | HIGH_LATENCY | 🔴 CRITICAL |

_...2 more issue(s) not shown; see metrics_report.json.all_issues for the full list._

## 3. Pipeline Health
_No Kafka consumer lag metrics or log evidence were found in this analysis period._

**All pipeline issues:**
| Service | Issue | Severity |
|---|---|---|
| video-encoder | PIPELINE_ALERT_UNCLASSIFIED | 🔴 CRITICAL |
| video-encoder | PIPELINE_ALERT_UNCLASSIFIED | 🟠 ERROR |
| cdn-gateway | PIPELINE_ALERT_UNCLASSIFIED | 🟠 ERROR |

## 4. Security
_No security findings (PII exposure, credential leaks, unauthorized access) detected in this analysis period._

## 5. Anomalies & Trends
| Service | Anomaly | Confidence | Detail |
|---|---|---|---|
| enc-host-01 | CPU_SPIKE | MEDIUM | enc-host-01 cpu_pct reading 88.0 exceeded 2.0x its own prior baseline 42.5 |

## 6. Dependency & Breakpoint Analysis
Service graph: 3 node(s), 2 edge(s).
| Breakpoint Service | Downstream Impact | Confidence |
|---|---|---|
| video-encoder | cdn-gateway, recommendation-engine | 0.65 |

---

## Root Cause Analysis
**incident_004** — `RESOURCE_SATURATION` (confidence: HIGH, severity: CRITICAL, blast radius: 3)
- Primary service: video-encoder
- Root cause: video-encoder identified as origin; failure propagated to cdn-gateway, recommendation-engine within 5 minutes
- Downstream symptoms: Encode job failed for asset_id=9932: No space left on device; Encode job failed for asset_id=9933: No space left on device; Disk usage on /var/encode/tmp reached 98%, encoder pool degraded; Disk usage exceeded 95% on enc-host-01; Encode job failed for asset_id=9934: No space left on device; encode queue depth exceeded 500 on video-encode-queue; Upstream timeout: video-encoder not responding within 5000ms; cdn-gateway error rate 50.0% across 4 traces; cdn-gateway p99 latency 5297.0ms; Upstream timeout rate exceeded threshold; video-encoder error rate 50.0% across 4 traces; video-encoder p99 latency 5206.7ms; recommendation-engine error rate 100.0% across 1 traces; recommendation-engine p99 latency 4800ms; Disk usage on /var/encode/tmp reached 99%, encoder pool offline
- Evidence sources: apm_report.json, dependency_report.json, log_analysis.json, metrics_report.json

**incident_005** — `RESOURCE_SATURATION` (confidence: MEDIUM, severity: CRITICAL, blast radius: 1)
- Primary service: enc-host-01
- Root cause: host enc-host-01 flagged HIGH_DISK (verdict CRITICAL)
- Downstream symptoms: host enc-host-01 flagged HIGH_CPU (verdict CRITICAL); host enc-host-01 flagged HOST_DOWN (verdict CRITICAL); enc-host-01 cpu_pct reading 88.0 exceeded 2.0x its own prior baseline 42.5
- Evidence sources: anomaly_report.json, metrics_report.json

⚠️ 2 critical finding(s) could not be clustered into an incident -- see `root_cause.json.unresolved_findings`.

---

## Recommendations
| Rank | Priority | Title | Affected Services |
|---|---|---|---|
| 1 | P1_IMMEDIATE | Address RESOURCE_SATURATION on video-encoder | cdn-gateway, recommendation-engine, video-encoder |
| 2 | P1_IMMEDIATE | Address RESOURCE_SATURATION on enc-host-01 | enc-host-01 |

---

## Patch Suggestions (Human Review Required)
⚠️ All patches require human review before applying.

**patch_001** (LOW risk, `CONNECTION_POOL_RESIZE`) — targets `config/connection-pool.yaml`
- enc-host-01 showed sustained resource exhaustion; double the connection/thread pool size to relieve pressure (confirm current pool_size before applying).
```diff
- pool_size: 20  # enc-host-01
+ pool_size: 40  # enc-host-01
```

1 recommendation(s) flagged for manual review instead of an automated patch.

---

## Appendix — Ingestion Summary
| Source Type | Record Count |
|---|---|
| log | 14 |
| metric | 26 |
| trace | 10 |
| alert | 4 |
| infrastructure | 8 |

Total records ingested: 62