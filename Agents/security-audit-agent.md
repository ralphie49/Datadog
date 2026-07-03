# Security Audit Agent
**Version:** 1.3.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Scans all normalised Datadog data for security violations and compliance issues. Detects
PII exposure in logs, credential leaks, unauthorised access attempts, firewall violations,
permission escalations, and compliance breaches across any system monitored in Datadog.

**Outputs:** `security_report.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
security_audit_config:
  input_file:  "output/normalised_data.json"
  output_file: "output/security_report.json"

  settings:
    pii_columns:          ["email", "phone", "ssn", "dob", "address", "credit_card", "national_id", "password", "token", "secret"]
    pii_value_patterns:   # regex patterns to catch PII values even when NOT labelled by a field name above
      email:        '[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}'
      phone:        '\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}'
      ssn:          '\b\d{3}-\d{2}-\d{4}\b'
      credit_card:  '\b(?:\d[ -]*?){13,16}\b'
    credential_patterns:  ["password=", "api_key=", "token=", "secret=", "Authorization:", "Bearer "]
    auth_failure_threshold: 5
    permission_escalation_keywords: ["sudo", "privilege", "escalat", "root access", "admin override"]
    compliance_frameworks: ["GDPR", "HIPAA", "PCI-DSS"]
```

---

## Pre-requisites

- `normalised_data.json` must exist (produced by Log Ingestion & Normaliser Agent)
- Output folder `output/` must be writable

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist).
  `analysis_period` MUST be a JSON object with exactly the keys `from` and `to` — never a bare array/list
- MUST scan all log messages two ways: (1) by `pii_columns` field-name labels (e.g. `email=...`), and (2)
  independently by `pii_value_patterns` regex against the raw message text, so unlabelled PII (a bare email or
  phone number with no field prefix) is still caught — never log raw PII values in findings
- MUST detect credential patterns in log messages and flag as CRITICAL
- MUST detect authentication failures and flag brute force patterns
- MUST detect permission escalation attempts
- MUST detect unauthorised access — 401, 403 HTTP responses, access denied messages
- MUST flag compliance-related keywords (GDPR, HIPAA, PCI-DSS violations)
- MUST redact any PII or credential values found before writing to report
- MUST write all findings to `security_report.json`

### MUST NOT
- MUST NOT write raw PII values or credentials into the output report — always redact
- MUST NOT modify the input `normalised_data.json`
- MUST NOT ignore CRITICAL severity security findings

---

## Security Issue Types

| Issue Type | Severity | Description |
|---|---|---|
| `PII_IN_LOGS` | CRITICAL | PII field values detected in log messages |
| `CREDENTIAL_LEAK` | CRITICAL | Password, token, or API key detected in logs |
| `BRUTE_FORCE_ATTEMPT` | CRITICAL | Auth failures exceed threshold per minute |
| `UNAUTHORISED_ACCESS` | ERROR | 401/403 responses or access denied messages |
| `PERMISSION_ESCALATION` | ERROR | Privilege escalation keywords detected |
| `FIREWALL_VIOLATION` | ERROR | Blocked connection attempts detected |
| `COMPLIANCE_BREACH` | ERROR | GDPR/HIPAA/PCI-DSS violation keywords detected |
| `SUSPICIOUS_ACTIVITY` | WARN | Unusual access patterns or off-hours activity |

---

## Output Schema — `security_report.json`

```json
{
  "summary": {
    "total_security_issues": 0,
    "critical_issues":       0,
    "error_issues":          0,
    "warn_issues":           0,
    "pii_exposures":         0,
    "credential_leaks":      0,
    "auth_failures":         0,
    "analysis_period": { "from": "", "to": "" }
  },
  "findings": [
    {
      "issue_type":  "PII_IN_LOGS",
      "severity":    "CRITICAL",
      "service":     "user-service",
      "timestamp":   "2026-07-02T09:00:00Z",
      "description": "PII field 'email' detected in log message",
      "redacted":    true,
      "action":      "Remove PII from log statements immediately"
    }
  ],
  "auth_failures": [],
  "compliance":    [],
  "all_issues":    []
}
```

---

## Execution Workflow

### Phase 0 — Load Input
1. Read `normalised_data.json`
2. Prepare PII keyword list and credential patterns from config

### Phase 1 — PII Scan
1. Scan for PII column names from `pii_columns` config (labelled PII, e.g. `email=...`)
2. Independently scan raw message text against each regex in `pii_value_patterns` (catches unlabelled PII —
   a bare email/phone/SSN with no field-name prefix)
3. Flag any match from either pass as PII_IN_LOGS CRITICAL, redact the actual value before writing to report;
   deduplicate if both passes catch the same value in the same message

### Phase 2 — Credential Leak Detection
1. Scan all log messages for `credential_patterns`; flag matches as CREDENTIAL_LEAK CRITICAL; redact

### Phase 3 — Auth Failure Analysis
1. Extract HTTP 401/403 and "access denied" / "unauthorised" messages
2. Group using the most specific key available, in this priority order: `source_ip` (if the normalised record
   has one) → `user` (if present) → `service` — always fall back to `service` rather than skipping the check,
   since real exports don't always carry a client IP or user field
3. Group within 1-minute windows using the chosen key
4. Flag groups exceeding `auth_failure_threshold` as BRUTE_FORCE_ATTEMPT, and record which grouping key was used
   in the finding so downstream readers know the confidence level (IP/user grouping is more precise than
   service-level grouping)
5. **Ingested `alert`-type records that already assert a security verdict** (e.g. a Datadog monitor named
   `*-auth-failures` whose message says "possible brute force") MUST NOT be passed through directly as a
   BRUTE_FORCE_ATTEMPT finding. This agent's own threshold-based grouping (steps 1-4) is the sole source of
   truth for that issue type — an upstream monitor's opinion is not independent confirmation. Instead, surface
   the alert as a `SUSPICIOUS_ACTIVITY` (WARN) finding with `description` noting it originated from an external
   monitor and citing the monitor name, so it's visible without being misrepresented as a verified finding.
   Only steps 1-4 may ever produce a `BRUTE_FORCE_ATTEMPT` entry.

### Phase 4 — Permission Escalation
1. Scan messages for `permission_escalation_keywords`; flag as PERMISSION_ESCALATION ERROR

### Phase 5 — Compliance Scan
1. Scan messages for `compliance_frameworks` keywords combined with violation terms; flag as COMPLIANCE_BREACH ERROR

### Phase 6 — Write Output
1. Build summary statistics
2. Write `security_report.json` with all findings redacted

---

## Output Specification

| Artifact | Description |
|---|---|
| `security_report.json` | Security findings with severity, service, timestamp, description — all PII and credentials redacted |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No security findings | Sample data has no security events | Add auth failure and PII log entries to sample_logs.json |
| False positive PII detections | Column name appears in non-PII context | Review flagged messages and refine PII patterns |
| Credential leak not detected | Pattern not in config | Add pattern to `credential_patterns` list |

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-02 | security-audit-agent | Initial release — PII scan, credential leak, brute force, permission escalation, compliance |
| 1.1.0 | 2026-07-03 | security-audit-agent | Added regex-based PII value detection (catches unlabelled PII, not just field-name matches); fixed brute-force grouping to use available `source_ip`/`user`/`service` fields with a documented fallback instead of assuming fields that ingestion never produced |
| 1.2.0 | 2026-07-03 | security-audit-agent | Added rule preventing ingested alert-type records from being passed through as a confirmed BRUTE_FORCE_ATTEMPT; such alerts now surface as SUSPICIOUS_ACTIVITY citing the source monitor instead; added analysis_period population rule |
| 1.3.0 | 2026-07-03 | security-audit-agent | Fixed observed bug: analysis_period was being written as a bare array instead of a {from, to} object — now explicitly specified as an object-only field |