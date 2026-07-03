# Datadog Observability Analysis Report

**Generated:** 2026-07-03T09:23:14Z | **Analysis Period:** 2026-07-02T08:00:00Z → 2026-07-02T10:00:01Z

**Overall Health:** CRITICAL

---

## Executive Summary

- Total incidents identified: 1

- Critical issues: 13 | Error issues: 19 | Warnings: 10

- Top risks:

  1. CRITICAL incident: SECURITY_INCIDENT impacting user-service

  2. KAFKA_LAG_HIGH on checkout-consumer

  3. KAFKA_LAG_HIGH on checkout-consumer

---

## 1. Errors & Data Quality

| Service | Issue | Severity | Detail |
|---|---|---|---|
| user-service | AUTHENTICATION_FAILURE | ERROR | Unauthorised access attempt: 401 Unauthorized invalid token |
| payment-service | CONNECTION_FAILURE | ERROR | DB connection refused: connection to payments-db timed out |
| checkout-consumer | UNKNOWN | ERROR | consume_event |
| host-01 | UNKNOWN | CRITICAL | infrastructure metrics |
| order-service | UNKNOWN | ERROR | create_order |

## 2. Performance & Infrastructure

| Service | Issue | Severity | Detail |
|---|---|---|---|
| order-service | Latency | CRITICAL | p99 3800ms |
| payment-service | Latency | CRITICAL | p99 3600ms |
| checkout-consumer | Latency | CRITICAL | p99 3300ms |
| user-service | Latency | OK | p99 60ms |

## 3. Pipeline Health

| Pipeline | Issue | Severity | Detail |
|---|---|---|---|
| checkout-consumer | KAFKA_LAG_HIGH | OK | Kafka lag 8000 |
| checkout-consumer | KAFKA_LAG_HIGH | WARN | Kafka lag 25000 |
| checkout-consumer | KAFKA_LAG_HIGH | WARN | Kafka lag 68000 |
| checkout-consumer | KAFKA_LAG_CRITICAL | CRITICAL | Kafka lag 125000 |
| checkout-consumer | KAFKA_LAG_CRITICAL | CRITICAL | Kafka lag 118000 |

## 4. Security

| Service | Issue | Severity | Detail |
|---|---|---|---|
| user-service | UNAUTHORISED_ACCESS | ERROR | Unauthorised access attempt detected |
| user-service | UNAUTHORISED_ACCESS | ERROR | Unauthorised access attempt detected |
| user-service | UNAUTHORISED_ACCESS | ERROR | Unauthorised access attempt detected |
| user-service | UNAUTHORISED_ACCESS | ERROR | Unauthorised access attempt detected |
| user-service | UNAUTHORISED_ACCESS | ERROR | Unauthorised access attempt detected |

## 5. Anomalies & Trends

| Service | Anomaly | Confidence | Detail |
|---|---|---|---|
| order-service | LATENCY_SPIKE | HIGH | p99 latency 3800ms |
| payment-service | LATENCY_SPIKE | HIGH | p99 latency 3600ms |
| checkout-consumer | LATENCY_SPIKE | HIGH | p99 latency 3300ms |
| user-service | LATENCY_SPIKE | HIGH | p99 latency 60ms |
| checkout-consumer | KAFKA_LAG_SPIKE | HIGH | Kafka lag 125000 |

## 6. Dependency & Breakpoint Analysis

| Breakpoint | Issue | Confidence | Downstream Impact |
|---|---|---|---|
| order-service | BREAKPOINT_IDENTIFIED | 0.75 | payment-service |
| checkout-consumer | BREAKPOINT_IDENTIFIED | 0.75 | order-service |
| checkout-consumer | BREAKPOINT_IDENTIFIED | 0.75 | order-service |
| checkout-consumer | BREAKPOINT_IDENTIFIED | 0.75 | order-service |
| checkout-consumer | BREAKPOINT_IDENTIFIED | 0.75 | order-service |

## Root Cause Analysis

| Incident | Category | Confidence | Root Cause |
|---|---|---|---|
| incident_026 | SECURITY_INCIDENT | HIGH | PII field detected in log message |

## Recommendations

| Priority | Title | Services | Evidence |
|---|---|---|---|
| P1_IMMEDIATE | Investigate security findings and redact sensitive logging | user-service | security_report.json: security findings |

## Patch Suggestions (Human Review Required)

| Patch | Type | Risk | Explanation |
|---|---|---|---|
| patch_001 | LOGGING_REDACTION_ADD | MEDIUM | Authorization failures and credential exposure were detected in user-service logs. |

## Appendix — Ingestion Summary
- infrastructure: 10
- log: 26
- metric: 35
- trace: 12
- alert: 7