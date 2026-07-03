# Code Patch Generator Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis
**Design credit:** originally proposed by teammate as part of her pipeline flow

---

## Purpose

Consumes the ranked recommendations produced by the Root Cause Analysis Agent and drafts
concrete code-level or configuration-level patches to address them where the fix is
mechanical and low-risk (e.g. threshold/config changes, retry logic, connection pool sizing).
Produces patch suggestions as diffs plus a plain-language explanation — it does not
commit, push, or auto-merge anything. A human must review and apply each patch.

**Outputs:** `patch_suggestions.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
code_patch_config:
  input_files:
    - "output/recommendations.json"
    - "output/root_cause.json"

  output_file: "output/patch_suggestions.json"

  settings:
    repo_path:                "."     # Path to the target repository to scan for relevant files
    max_patches:               10     # Cap number of patch suggestions generated
    only_patch_priorities:     ["P1_IMMEDIATE", "P2_URGENT"]  # Only generate patches for these priority levels
    require_human_review:      true   # MUST remain true — patches are never auto-applied
    allow_config_changes:      true   # e.g. threshold values, retry counts, pool sizes
    allow_code_changes:        true   # e.g. adding null checks, timeout handling
    forbid_patterns:           ["DROP TABLE", "rm -rf", "DELETE FROM", "credentials", "secret_key"]
```

---

## Pre-requisites

- `recommendations.json` and `root_cause.json` must exist
- Read access to the target repository specified in `repo_path` (read-only — this agent never writes directly to source files)
- Output folder `output/` must be writable

---

## CORE RULES

### MUST
- MUST only generate patches for recommendations at priority levels listed in `only_patch_priorities`
- MUST only propose a patch when the fix is mechanical and traceable directly to a specific finding — never propose speculative architectural rewrites
- MUST present every patch as a diff plus a plain-language explanation of what it changes and why
- MUST cite the specific recommendation/incident the patch addresses
- MUST clearly label each patch with a risk level: LOW | MEDIUM | HIGH
- MUST refuse to generate a patch touching anything matching `forbid_patterns`
- MUST leave `require_human_review: true` — every patch is a suggestion only, never auto-applied
- MUST write all patch suggestions to `patch_suggestions.json`

### MUST NOT
- MUST NOT modify, commit, or push any file in the target repository directly
- MUST NOT generate patches for recommendations without a specific, traceable root cause finding
- MUST NOT propose patches touching authentication, credentials, secrets, or destructive database/file operations, regardless of priority
- MUST NOT generate more than `max_patches` suggestions per run
- MUST NOT modify any upstream input files (`recommendations.json`, `root_cause.json`)

---

## Patch Types

| Patch Type | Description | Typical Risk |
|---|---|---|
| `CONFIG_THRESHOLD_ADJUST` | Change a config value (timeout, pool size, retry count, threshold) | LOW |
| `RETRY_LOGIC_ADD` | Add retry/backoff logic around a flaky call identified in root cause | LOW-MEDIUM |
| `NULL_CHECK_ADD` | Add defensive null/guard checks where NULL_POINTER errors were detected | LOW |
| `CONNECTION_POOL_RESIZE` | Adjust DB/HTTP connection pool sizing to relieve `RESOURCE_EXHAUSTION` | LOW |
| `TIMEOUT_ADJUST` | Adjust request/query timeout values where `TIMEOUT` errors were flagged | LOW |
| `LOGGING_REDACTION_ADD` | Add redaction logic where `PII_IN_LOGS` was flagged by Security Audit Agent | MEDIUM |
| `SCALING_CONFIG_CHANGE` | Adjust consumer/replica scaling config where `KAFKA_LAG_CRITICAL` or `THROUGHPUT_DROP` was flagged | MEDIUM |
| `MANUAL_REVIEW_REQUIRED` | Fix is too structural/ambiguous for an automated patch — flagged for a human to design | N/A |

---

## Output Schema — `patch_suggestions.json`

```json
{
  "summary": {
    "total_patches_generated": 0,
    "low_risk":                0,
    "medium_risk":             0,
    "high_risk":               0,
    "manual_review_required":  0
  },
  "patches": [
    {
      "patch_id":        "patch_001",
      "incident_id":     "incident_001",
      "recommendation_ref": "rank_1",
      "patch_type":      "SCALING_CONFIG_CHANGE",
      "risk_level":      "MEDIUM",
      "target_file":     "config/kafka-consumer.yaml",
      "explanation":     "Consumer lag on ecommerce-events reached 125000 (critical threshold). Increasing consumer instance count should relieve backpressure.",
      "diff": "- consumer_instances: 2\n+ consumer_instances: 6",
      "requires_human_review": true
    }
  ],
  "skipped_recommendations": [
    {
      "recommendation_rank": 4,
      "reason": "Fix requires architectural redesign — flagged MANUAL_REVIEW_REQUIRED"
    }
  ],
  "all_patches": []
}
```

---

## Execution Workflow

### Phase 0 — Load Input
1. Read `recommendations.json` and `root_cause.json`
2. Filter recommendations to only those at priority levels in `only_patch_priorities`

### Phase 1 — Feasibility Check
1. For each filtered recommendation, determine if the fix is mechanical (config/simple code change) or structural
2. Structural or ambiguous fixes → mark MANUAL_REVIEW_REQUIRED and skip patch generation
3. Check candidate fix against `forbid_patterns` — if matched, skip and log the reason

### Phase 2 — Patch Drafting
1. For each feasible recommendation, locate the relevant config or code location in `repo_path` (read-only scan)
2. Draft a minimal diff addressing only the specific finding — no unrelated changes
3. Classify `patch_type` from the table above
4. Assign risk level based on patch type and blast radius from `root_cause.json`

### Phase 3 — Explanation & Citation
1. Write a plain-language explanation for each patch, citing the specific incident and evidence that justified it
2. Confirm `requires_human_review: true` is set on every patch

### Phase 4 — Write Output
1. Cap total patches at `max_patches`
2. Build summary statistics
3. Write `patch_suggestions.json`

---

## Output Specification

| Artifact | Description |
|---|---|
| `patch_suggestions.json` | Proposed diffs with risk level, plain-language explanation, and citation back to the originating incident — for human review only, never auto-applied |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No patches generated | All recommendations structural, or below priority filter | Widen `only_patch_priorities` or review skipped_recommendations for reasons |
| Patch touches wrong file | Repo scan matched an unintended file | Narrow `repo_path` or add file-path hints to the recommendation |
| Everything flagged MANUAL_REVIEW_REQUIRED | Recommendations too vague to map to a mechanical fix | Ensure Root Cause Analysis Agent recommendations include concrete evidence and target components |

---

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-03 | code-patch-generator-agent | Initial release — feasibility-checked patch drafting, risk classification, human-review-only output |