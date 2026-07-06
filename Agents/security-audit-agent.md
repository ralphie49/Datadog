# Security Audit Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Scans all normalised Datadog data for security violations and compliance issues. Detects
PII exposure in logs, credential leaks, unauthorised access attempts, firewall violations,
permission escalations, and compliance breaches across any system monitored in Datadog.

**Outputs:** `security_report.json`

---

## 🚫 Anti-Hardcoding & Generalization Contract (read before writing any code)

This spec will be used to generate an implementation that MUST run correctly against **inputs it has
never seen** — different services, different metric values, different incidents, possibly zero
incidents, possibly many more files than the sample. An implementation that only reproduces this
project's bundled sample data is non-compliant, even if every self-test / acceptance-test check
elsewhere in this file passes.

**Rules:**
- Any `Self-Test Cases`, `Mandatory Acceptance Test For Sample input/`, or "for the sample `input/`
  folder, X MUST equal N" section in this file describes the expected *result* of running the general
  algorithm on the bundled sample data. It is a regression check on that algorithm, not a specification
  of literal values to write into the code. Treat every number, service name, incident ID, topic name,
  or string in those sections as derived output to verify — never as a constant to embed.
- Do NOT special-case any literal from the sample dataset (e.g. specific service names, specific lag/
  latency/error-rate numbers, specific incident IDs) anywhere in the implementation's logic, thresholds,
  or output-construction code. Every such value may only appear in the output because the algorithm
  computed it from the actual input file contents at runtime — never because it was typed into the code.
- Before this agent is considered done, run the implementation against a **second, structurally
  different input dataset** (different service names, different metric values, a different or absent
  incident, a different number of input files) and confirm the output changes accordingly and remains
  internally consistent. If running the code against a different input still produces the sample
  dataset's specific service names, IDs, or numeric findings, that is proof of hardcoding — reject the
  implementation and rewrite it.
- If a self-test/acceptance check in this file cannot be satisfied by an implementation that also passes
  the different-dataset test above, treat that as a reason to flag the self-test for spec review — never
  as a license to hardcode the literal expected value instead of implementing the described logic.

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
security_audit_config:
  input_file:  "output/<dataset>/normalised_data.json"
  output_file: "output/<dataset>/security_report.json"

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

- `output/<dataset>/normalised_data.json` must exist (produced by Log Ingestion & Normaliser Agent)
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_file` and write only the configured `output_file`.
- `input_file` and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from users, IPs, services, dates, the input filename, or existing files in `output/`.
- If the input and output paths point to different dataset folders, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist).
  `analysis_period` MUST be a JSON object with exactly the keys `from` and `to` — never a bare array/list
- MUST read security-relevant records from `normalised_data.json.records[]`, especially records with
  `source_type == "log"` and `source_type == "alert"`. Do not scan only `samples` or classified-file summaries.
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
- MUST NOT accept a summary-only `normalised_data.json` that has no `records[]` array
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

## Implementation Notes (pseudocode — MUST be followed structurally, not just in spirit)

Observed defect: the field-name pass and the regex-value pass for PII detection are commonly implemented
as two independent `if` blocks that each unconditionally append a finding — with no shared state between
them, so a single log line matching both passes (e.g. a message containing `email=someone@x.com`, which
matches the `email` field-name check AND the email regex) produces two duplicate `PII_IN_LOGS` findings for
the one underlying event. Track which records have already produced a PII finding, per record, not per pass:

```
for record in log_and_alert_records:
    pii_already_flagged_for_this_record = False        # reset per record, not per pass

    if any(f"{col}=" in record.message.lower() for col in pii_columns):
        if not pii_already_flagged_for_this_record:
            findings.append(make_pii_finding(record))
            pii_already_flagged_for_this_record = True

    for pattern in pii_value_patterns.values():
        if regex_search(pattern, record.message):
            if not pii_already_flagged_for_this_record:   # this check is what prevents the duplicate
                findings.append(make_pii_finding(record))
                pii_already_flagged_for_this_record = True
            break
```
The two passes still both run (a value-only match with no field-name label must still be caught), but the
per-record flag ensures at most one `PII_IN_LOGS` finding is emitted per record even when both passes fire.

## Self-Test Cases (regression check only — see Anti-Hardcoding Contract above; verify via the algorithm, never hardcode these literal values)

Given the single log line "PII field 'email' detected in log message: user record
email=redacted@example.com" (which matches both the `email` field-name pass and the email regex pass):
- `security_report.json.summary.pii_exposures` MUST equal `1` for this line, not `2`.
- `security_report.json.findings` MUST contain exactly one `PII_IN_LOGS` entry with `timestamp:
  2026-07-02T09:54:00Z`, not two. If two entries exist with the same `issue_type`, `service`, and
  `timestamp`, the per-record dedup was not implemented and must be added.

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

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- PII and credential findings MUST be redacted in descriptions and evidence. Do not copy raw secrets, tokens, email addresses, or credentials into output.
- `summary.total_security_issues` MUST equal `len(findings)`.
- `summary.critical_issues`, `summary.error_issues`, and `summary.warn_issues` MUST be counted from `findings[].severity`.
- `summary.pii_exposures` MUST count findings with `issue_type == "PII_IN_LOGS"`.
- `summary.credential_leaks` MUST count findings with `issue_type == "CREDENTIAL_LEAK"`.
- Auth-failure grouping MUST be deterministic by service, source IP, user, or the best available stable key. If user/IP is missing, group by service and explain that fallback.
- Do not count the same raw auth event twice because it appears in both logs and alerts unless the alert is separately reported as monitor evidence.
- Every CRITICAL security finding MUST include `service`, `timestamp`, `description`, `redacted`, and `action`.
- `all_issues` MUST mirror security findings in a compact form; no finding may be absent from `all_issues`.
- If `normalised_data.json` contains log records with PII/credential evidence such as `email=`, `PII`,
  `Credential leak`, `Authorization: Bearer`, `password`, `token`, or repeated unauthorized access messages,
  `security_report.json.findings` MUST NOT be empty.
- For the sample `input/` folder, findings MUST include at least one `PII_IN_LOGS`, one `CREDENTIAL_LEAK`,
  and one brute-force/unauthorized-access finding for `user-service`.
- Reject the output if security findings are zero because logs were not ingested or because only alert records
  were scanned.

Reject the generated output if any unredacted secret-like or PII-like value appears in the report.


---

## 🚫 Final Hardcoding Check (applies on top of everything above)

Before accepting this agent's implementation as done: pick any literal value in its output (a service
name, an ID, a count, a percentage) and ask "would this exact value still appear if I fed the agent a
different input file with different data?" If the answer is yes for a value that should depend on the
input, the implementation is hardcoded and must be rewritten to derive that value from the actual input
at runtime. This check applies to every JSON/Markdown artifact this agent produces, not just the fields
called out elsewhere in this file.