# Datadog Observability Analysis Report
**Generated:** 2026-07-06T22:36:00Z | **Analysis Period:** 2026-07-02T08:00:00Z → 2026-07-02T10:00:02Z
**Dataset:** input
**Overall Health:** CRITICAL

## Executive Summary
- Total incidents identified: 2
- Critical issues: 9 | Error issues: 19 | Warnings: 7
- Top risks:
  1. Kafka consumer lag on checkout-consumer reached 125,000 messages on ecommerce-events, cascading to order-service and payment-service latency.
  2. PII and credential exposure in user-service logs created a critical security incident that affected authentication and authorization workflows.
  3. Correlated anomalies in checkout-consumer and payment-service indicated that the lag spike was driving downstream throughput degradation.

## 1. Errors & Data Quality
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| user-service | Unauthorised access attempt: 401 Unauthorized invalid token | 🔴 ERROR | Repeated authentication failures for the admin endpoint |
| payment-service | DB connection refused: connection to payments-db timed out | 🟠 ERROR | Database connectivity issue affecting payment processing |
| order-service | Unhandled exception: NullPointerException at OrderProcessor.finalize | 🟠 ERROR | Order processing error propagated through the transaction path |
| checkout-consumer | DQ_ALERT rejection_reason=FORMAT_MISMATCH:email count=45 | 🟠 ERROR | Data quality alert for email formatting |
| checkout-consumer | DQ_ALERT rejection_reason=NULL_VALUE:phone count=30 | 🟠 ERROR | Data quality alert for missing phone values |

## 2. Performance & Infrastructure
### Latency by service
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| checkout-consumer | Latency p95/p99 | 🔴 CRITICAL | avg_ms=3400.0, p95_ms=3300.0, p99_ms=3300.0 |
| order-service | Latency p95/p99 | 🔴 CRITICAL | avg_ms=2093.75, p95_ms=3800.0, p99_ms=3800.0 |
| payment-service | Latency p95/p99 | 🔴 CRITICAL | avg_ms=1982.5, p95_ms=3600.0, p99_ms=3600.0 |
| user-service | Latency p95/p99 | 🟢 OK | avg_ms=75.0, p95_ms=60.0, p99_ms=60.0 |

### Slowest traces
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| order-service | create_order | 🔴 CRITICAL | duration_ms=4200.0 at 2026-07-02T09:30:00Z |
| payment-service | process_payment | 🔴 CRITICAL | duration_ms=3900.0 at 2026-07-02T09:30:01Z |
| order-service | create_order | 🔴 CRITICAL | duration_ms=3800.0 at 2026-07-02T09:31:00Z |
| payment-service | process_payment | 🔴 CRITICAL | duration_ms=3600.0 at 2026-07-02T09:31:02Z |
| checkout-consumer | consume_event | 🟠 ERROR | duration_ms=3500.0 at 2026-07-02T09:29:50Z |

### Host health
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| host-01 | Host health | 🟢 OK | CPU 60%, Memory 58%, Disk 41% |
| host-02 | Host health | 🟢 OK | CPU 72%, Memory 68%, Disk 36% |
| host-03 | Host health | 🟡 WARN | CPU 88%, Memory 82%, Disk 61% |
| host-04 | Host health | 🟢 OK | CPU 42%, Memory 44%, Disk 31% |

### Infrastructure issues
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| host-03 | HIGH_CPU | 🟡 WARN | CPU 88% |
| host-03 | HIGH_MEMORY | 🟡 WARN | Memory 82% |

## 3. Pipeline Health
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| checkout-consumer | KAFKA_LAG_CRITICAL | 🔴 CRITICAL | Kafka lag 125000 for ecommerce-events |
| checkout-consumer | KAFKA_LAG_CRITICAL | 🔴 CRITICAL | Kafka lag 118000 for ecommerce-events |
| checkout-consumer | CHECKPOINT_STALE | 🟡 WARN | checkpoint offset missing, last checkpoint 45 minutes old |

## 4. Security
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| user-service | UNAUTHORISED_ACCESS | 🟠 ERROR | Authentication failures were observed against the admin endpoint |
| user-service | UNAUTHORISED_ACCESS | 🟠 ERROR | Repeated invalid token attempts were recorded |
| user-service | PII_IN_LOGS | 🔴 CRITICAL | PII field exposure was detected in user-service logs |
| user-service | CREDENTIAL_LEAK | 🔴 CRITICAL | Credential-like data was detected and redacted |
| user-service | BRUTE_FORCE_ATTEMPT | 🔴 CRITICAL | Six authentication failures were grouped as a suspicious access pattern |

## 5. Anomalies & Trends
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| checkout-consumer | KAFKA_LAG_SPIKE | 🔴 HIGH | Kafka consumer lag spiked for checkout-consumer |
| checkout-consumer | KAFKA_LAG_SPIKE | 🔴 HIGH | Kafka consumer lag spiked for checkout-consumer |
| payment-service | THROUGHPUT_DROP | 🟡 MEDIUM | Throughput dropped for payment-service |
| payment-service | THROUGHPUT_DROP | 🟡 MEDIUM | Throughput dropped for payment-service |
| checkout-consumer | CORRELATED_ANOMALY | 🔴 HIGH | Correlated anomalies across checkout-consumer and payment-service |

## 6. Dependency & Breakpoint Analysis
| Service | Issue | Severity | Detail |
| --- | --- | --- | --- |
| order-service | ->payment-service | 🟢 INFO | call_count=4 avg_latency_ms=1982.5 |
| checkout-consumer | ->order-service | 🟢 INFO | call_count=2 avg_latency_ms=4000.0 |
| checkout-consumer | BREAKPOINT_IDENTIFIED | 🔴 CRITICAL | checkout-consumer was identified as the upstream breakpoint for downstream impact |

## Root Cause Analysis
- incident_001: PIPELINE_BACKPRESSURE on checkout-consumer — KAFKA_LAG_CRITICAL on topic ecommerce-events (lag 125000) and downstream latency in order-service and payment-service.
- incident_002: SECURITY_INCIDENT on user-service — PII_IN_LOGS and CREDENTIAL_LEAK observed on user-service, exposing authentication and authorization risk.

## Recommendations
| Priority | Incident | Title | Detail |
| --- | --- | --- | --- |
| P1_IMMEDIATE | incident_001 | Investigate pipeline backpressure for checkout-consumer | KAFKA_LAG_CRITICAL on topic ecommerce-events (lag 125000) |
| P1_IMMEDIATE | incident_002 | Investigate security incident for user-service | PII_IN_LOGS and CREDENTIAL_LEAK observed on user-service |

## Patch Suggestions (Human Review Required)
- patch_001: KAFKA_CONSUMER_SCALING (MEDIUM) — Scale the consumer group and verify partitioning for the lagging topic.
- patch_002: REDACTION_POLICY_CHANGE (LOW) — Enforce structured redaction for PII and credentials before logs are emitted.
- ⚠️ All patches require human review before applying.

## Appendix — Ingestion Summary
- Total normalized records: 90
- Logs: 26 | Metrics: 35 | Traces: 12 | Alerts: 7 | Infrastructure: 10
