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
code_patch_config:
  input_files:
    - "output/<dataset>/recommendations.json"
    - "output/<dataset>/root_cause.json"

  output_file: "output/<dataset>/patch_suggestions.json"

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

- `output/<dataset>/recommendations.json` and `output/<dataset>/root_cause.json` must exist
- Read access to the target repository specified in `repo_path` (read-only — this agent never writes directly to source files)
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_files` and write only the configured `output_file`.
- Every `input_files` entry and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent scans `repo_path` read-only for patch context, but its own JSON artifact MUST still be written only to the configured `output_file`.
- This agent MUST NOT derive a new output folder from target files, recommendations, incident IDs, patch types, dates, upstream filenames, or existing files in `output/`.
- If the input files and output file do not all share the same dataset folder, stop before writing and report the mismatch.

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

## Implementation Notes (pseudocode — MUST be followed structurally, not just in spirit)

Observed defect: `summary.manual_review_required` was implemented as a count of patches where
`requires_human_review == true` — but `requires_human_review` is `true` on every patch, always, by design
(see the `require_human_review: true` MUST-remain-true setting above). Counting it produces
`manual_review_required == total_patches_generated` every run, which is meaningless. This field means
something different: it is the count of *recommendations that were skipped* because the fix was too
structural for an automated patch (i.e. `len(skipped_recommendations)`), not a property of the patches that
were actually generated.
```
manual_review_required = len(skipped_recommendations)   # NOT len([p for p in patches if p.requires_human_review])
```

## Self-Test Case (regression check only — see Anti-Hardcoding Contract above; do not hardcode)

Given 2 recommendations where both were feasible and produced concrete patches (`skipped_recommendations`
is empty): `summary.manual_review_required` MUST equal `0`. If it equals `2` (the total patch count)
instead, the wrong field was counted.

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

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- Generate patches only from recommendations that cite a concrete incident or unresolved critical finding.
- Do not invent target files. If a target file is inferred rather than present in the input, set `requires_human_review: true` and explain the assumption.
- The output array MUST be named exactly `patches`. Do not write `patch_suggestions`, `suggestions`, or
  prose-only patch objects as a substitute for the required schema.
- Every patch MUST include `patch_id`, `incident_id`, `recommendation_ref`, `patch_type`, `risk_level`, `target_file`, `explanation`, `diff`, and `requires_human_review`.
- `summary.total_patches_generated` MUST equal `len(patches)`.
- `summary.manual_review_required` MUST equal `len(skipped_recommendations)`. It MUST NOT count generated patches merely because `requires_human_review == true`; every generated patch requires human review by design.
- `summary.low_risk`, `summary.medium_risk`, and `summary.high_risk` MUST count patches by `risk_level`.
- The diff MUST be syntactically recognizable as a unified or minimal before/after diff. Do not output vague prose as `diff`.
- If no patch can be safely generated, write zero patches and explain all skipped recommendations. Do not fabricate a config change.

Reject the generated output if `manual_review_required` is anything other than the count of skipped recommendations requiring manual investigation.
Also reject it if the file contains a `patch_suggestions` array instead of `patches`, or if any generated
patch has prose in `patch` but no syntactically recognizable `diff`.


---

## 🚫 Final Hardcoding Check (applies on top of everything above)

Before accepting this agent's implementation as done: pick any literal value in its output (a service
name, an ID, a count, a percentage) and ask "would this exact value still appear if I fed the agent a
different input file with different data?" If the answer is yes for a value that should depend on the
input, the implementation is hardcoded and must be rewritten to derive that value from the actual input
at runtime. This check applies to every JSON/Markdown artifact this agent produces, not just the fields
called out elsewhere in this file.