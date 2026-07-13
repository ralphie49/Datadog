# Datadog Observability Analysis Report
**Dataset:** `input` | **Generated:** 2026-07-10T11:22:13Z
**Analysis Period:** 2026-07-02T08:00:00.000Z -> 2026-07-02T10:00:01.000Z
**Overall Health:** CRITICAL

---

## Executive Summary
- Total incidents identified: 2
- Critical issues: 27 | Error issues: 19 | Warnings: 11
- Top risks:
  1. checkout-consumer identified as origin; failure propagated to order-service, payment-service within 5 minutes
  2. Unauthorised access attempt: 403 Forbidden for endpoint /admin/users
  3. PII field 'email' detected in log message: user record email=[REDACTED]

---

## 1. Errors & Data Quality
| Service | Issue | Severity | Detail |
|---|---|---|---|
| checkout-consumer | UNKNOWN | 🔴 CRITICAL | Kafka consumer lag critical for topic ecommerce-events, lag=125000 |
| checkout-consumer | UNKNOWN | 🔴 CRITICAL | Kafka consumer lag critical for topic ecommerce-events, lag=118000 |
| user-service | UNKNOWN | 🔴 CRITICAL | PII field 'email' detected in log message: user record email=[REDACTED] |
| user-service | UNKNOWN | 🔴 CRITICAL | Credential leak detected: Authorization:[REDACTED] |
| payment-service | CONNECTION_FAILURE | 🟠 ERROR | DB connection refused: connection to payments-db timed out |

_...14 more error(s) not shown; see log_analysis.json.all_errors for the full list._

**Worst offending DQ columns:**
| Column | Rejection Count | Rule Type |
|---|---|---|
| email | 45 | FORMAT_MISMATCH |
| phone | 30 | NULL_VALUE |

## 2. Performance & Infrastructure
**Latency by service:**
| Service | Avg (ms) | P95 (ms) | P99 (ms) | Verdict |
|---|---|---|---|---|
| order-service | 2093.8 | 4140.0 | 4188.0 | 🔴 CRITICAL |
| payment-service | 1982.5 | 3855.0 | 3891.0 | 🔴 CRITICAL |
| checkout-consumer | 3400.0 | 3490.0 | 3498.0 | 🔴 CRITICAL |
| user-service | 75.0 | 88.5 | 89.7 | 🟢 OK |

**Slowest traces:**
| Service | Operation | Duration (ms) | Timestamp |
|---|---|---|---|
| order-service | create_order | 4200 | 2026-07-02T09:30:00.000Z |
| payment-service | process_payment | 3900 | 2026-07-02T09:30:01.000Z |
| order-service | create_order | 3800 | 2026-07-02T09:31:00.000Z |
| payment-service | process_payment | 3600 | 2026-07-02T09:31:02.000Z |
| checkout-consumer | consume_event | 3500 | 2026-07-02T09:29:50.000Z |

**Host resource health:**
| Host | CPU % | Memory % | Disk % | Verdict | Issues |
|---|---|---|---|---|---|
| host-01 | 95.0 | 91.0 | 43.0 | 🔴 CRITICAL | HIGH_CPU, HIGH_MEMORY, NETWORK_SATURATION, RESOURCE_EXHAUSTION, HOST_DOWN |
| host-03 | 88.0 | 82.0 | 61.0 | 🔴 CRITICAL | HIGH_CPU, HIGH_MEMORY, NETWORK_SATURATION, HOST_DOWN |
| host-02 | 72.0 | 68.0 | 36.0 | 🟡 WARN | HOST_DOWN |
| host-04 | 42.0 | 44.0 | 31.0 | 🟢 OK | - |

**All performance/infra issues:**
| Service | Issue | Severity |
|---|---|---|
| order-service | HIGH_ERROR_RATE | 🔴 CRITICAL |
| payment-service | HIGH_ERROR_RATE | 🔴 CRITICAL |
| checkout-consumer | HIGH_ERROR_RATE | 🔴 CRITICAL |
| user-service | HIGH_ERROR_RATE | 🔴 CRITICAL |
| host-01 | HIGH_CPU | 🔴 CRITICAL |
| host-01 | HIGH_MEMORY | 🔴 CRITICAL |
| host-01 | NETWORK_SATURATION | 🔴 CRITICAL |
| host-01 | RESOURCE_EXHAUSTION | 🔴 CRITICAL |
| host-01 | HOST_DOWN | 🔴 CRITICAL |
| host-03 | HIGH_CPU | 🔴 CRITICAL |
| host-03 | HIGH_MEMORY | 🔴 CRITICAL |
| host-03 | NETWORK_SATURATION | 🔴 CRITICAL |
| host-03 | HOST_DOWN | 🔴 CRITICAL |
| order-service | HIGH_LATENCY | 🔴 CRITICAL |
| payment-service | HIGH_LATENCY | 🔴 CRITICAL |
| checkout-consumer | HIGH_LATENCY | 🔴 CRITICAL |

_...2 more issue(s) not shown; see metrics_report.json.all_issues for the full list._

## 3. Pipeline Health
**Kafka topics:**
| Topic | Consumer Group | Lag | Verdict |
|---|---|---|---|
| (unknown) | checkout-consumer | 8000.0 | 🟢 OK |
| (unknown) | checkout-consumer | 25000.0 | 🟡 WARN |
| (unknown) | checkout-consumer | 68000.0 | 🟡 WARN |
| ecommerce-events | checkout-consumer | 125000.0 | 🔴 CRITICAL |
| ecommerce-events | checkout-consumer | 118000.0 | 🔴 CRITICAL |

**All pipeline issues:**
| Service | Issue | Severity |
|---|---|---|
| checkout-consumer | KAFKA_LAG_CRITICAL | 🔴 CRITICAL |
| checkout-consumer | KAFKA_LAG_CRITICAL | 🔴 CRITICAL |
| checkout-consumer | CHECKPOINT_STALE | 🔴 CRITICAL |
| checkout-consumer | PIPELINE_ALERT_UNCLASSIFIED | 🔴 CRITICAL |
| user-service | PIPELINE_ALERT_UNCLASSIFIED | 🔴 CRITICAL |

_...7 more issue(s) not shown; see apm_report.json.all_issues for the full list._

## 4. Security
| Service | Issue | Severity | Detail |
|---|---|---|---|
| user-service | PII_IN_LOGS | 🔴 CRITICAL | PII field 'email' detected in log message: user record email=[REDACTED] |
| user-service | CREDENTIAL_LEAK | 🔴 CRITICAL | Credential leak detected: Authorization:[REDACTED] |
| user-service | UNAUTHORISED_ACCESS | 🟠 ERROR | Unauthorised access attempt: 403 Forbidden for endpoint /admin/users |
| user-service | UNAUTHORISED_ACCESS | 🟠 ERROR | Unauthorised access attempt: 401 Unauthorized invalid token |
| user-service | UNAUTHORISED_ACCESS | 🟠 ERROR | Unauthorised access attempt: 401 Unauthorized invalid token |

## 5. Anomalies & Trends
| Service | Anomaly | Confidence | Detail |
|---|---|---|---|
| order-service | CORRELATED_ANOMALY | HIGH | order-service and checkout-consumer showed correlated KAFKA_LAG_SPIKE and LATENCY_SPIKE within the same window |
| checkout-consumer | KAFKA_LAG_SPIKE | MEDIUM | checkout-consumer kafka_consumer_lag reading 125000.0 exceeded 2.0x its own prior baseline 33666.67 |
| order-service | LATENCY_SPIKE | MEDIUM | order-service p99 latency 4188.0ms is 2.0x its own baseline avg 2093.8ms |

## 6. Dependency & Breakpoint Analysis
Service graph: 3 node(s), 2 edge(s).
| Breakpoint Service | Downstream Impact | Confidence |
|---|---|---|
| checkout-consumer | order-service, payment-service | 0.65 |

---

## Root Cause Analysis
**incident_005** — `PIPELINE_BACKPRESSURE` (confidence: HIGH, severity: CRITICAL, blast radius: 3)
- Primary service: checkout-consumer
- Root cause: checkout-consumer identified as origin; failure propagated to order-service, payment-service within 5 minutes
- Downstream symptoms: KAFKA_LAG_HIGH on checkout-consumer; PIPELINE_ALERT_UNCLASSIFIED on checkout-consumer; DQ_ALERT rejection_reason=FORMAT_MISMATCH:email count=45; DQ_ALERT rejection_reason=NULL_VALUE:phone count=30; Kafka consumer lag critical for topic ecommerce-events, lag=125000; KAFKA_LAG_CRITICAL on checkout-consumer; order-service and checkout-consumer showed correlated KAFKA_LAG_SPIKE and LATENCY_SPIKE within the same window; checkout-consumer kafka_consumer_lag reading 125000.0 exceeded 2.0x its own prior baseline 33666.67; checkout-consumer error rate 100.0% across 2 traces; checkout-consumer p99 latency 3498.0ms; Request timeout: upstream checkout-consumer not responding; order-service error rate 50.0% across 4 traces; payment-service throughput dropped 46.8% vs baseline; order-service p99 latency 4188.0ms; PIPELINE_ALERT_UNCLASSIFIED on payment-service; order-service p99 latency 4188.0ms is 2.0x its own baseline avg 2093.8ms; payment-service error rate 50.0% across 4 traces; payment-service p99 latency 3891.0ms; Unhandled exception: NullPointerException at OrderProcessor.finalize; PIPELINE_ALERT_UNCLASSIFIED on order-service; CHECKPOINT_STALE on checkout-consumer; Kafka consumer lag critical for topic ecommerce-events, lag=118000
- Evidence sources: anomaly_report.json, apm_report.json, dependency_report.json, log_analysis.json, metrics_report.json

**incident_006** — `SECURITY_INCIDENT` (confidence: HIGH, severity: CRITICAL, blast radius: 1)
- Primary service: user-service
- Root cause: Unauthorised access attempt: 403 Forbidden for endpoint /admin/users
- Downstream symptoms: Unauthorised access attempt: 403 Forbidden for endpoint /admin/users; Unauthorised access attempt: 401 Unauthorized invalid token; user-service error rate 50.0% across 2 traces; PIPELINE_ALERT_UNCLASSIFIED on user-service; External monitor 'user-service-auth-failures' reported possible brute force -- not independently confirmed; PII field 'email' detected in log message: user record email=[REDACTED]; Credential leak detected: Authorization:[REDACTED]
- Evidence sources: apm_report.json, log_analysis.json, metrics_report.json, security_report.json

⚠️ 9 critical finding(s) could not be clustered into an incident -- see `root_cause.json.unresolved_findings`.

---

## Recommendations
| Rank | Priority | Title | Affected Services |
|---|---|---|---|
| 1 | P1_IMMEDIATE | Address PIPELINE_BACKPRESSURE on checkout-consumer | checkout-consumer, order-service, payment-service |
| 2 | P1_IMMEDIATE | Address SECURITY_INCIDENT on user-service | user-service |

---

## Patch Suggestions (Human Review Required)
⚠️ All patches require human review before applying.

**patch_001** (MEDIUM risk, `SCALING_CONFIG_CHANGE`) — targets `config/kafka-consumer.yaml`
- Observed consumer lag peaked at 125000; scale consumer instances to 4 to relieve backpressure (assumes a starting scale of 2 -- confirm actual current value before applying).
```diff
- consumer_instances: 2
+ consumer_instances: 4
```

**patch_002** (MEDIUM risk, `LOGGING_REDACTION_ADD`) — targets `src/logging/redaction.py`
- Log line(s) exposed unredacted Authorization, email; add Authorization, email to the redaction field list before logging.
```diff
- redact_fields = ["password", "token"]
+ redact_fields = ["password", "token", "Authorization", "email"]
```

---

## Appendix — Ingestion Summary
| Source Type | Record Count |
|---|---|
| log | 26 |
| metric | 35 |
| trace | 12 |
| alert | 7 |
| infrastructure | 10 |

Total records ingested: 90
