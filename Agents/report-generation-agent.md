# Report Generation Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Consolidates the outputs of all upstream agents into a single, polished, human-readable
Markdown report. Presents an executive summary, per-domain findings, dependency/breakpoint
analysis, root cause analysis, patch suggestions, and prioritised action items in a format
suitable for sharing with engineering leadership or an incident review.

**Outputs:** `datadog_analysis_report.md`

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
report_config:
  input_files:
    - "output/<dataset>/normalised_data.json"
    - "output/<dataset>/log_analysis.json"
    - "output/<dataset>/metrics_report.json"
    - "output/<dataset>/apm_report.json"
    - "output/<dataset>/security_report.json"
    - "output/<dataset>/anomaly_report.json"
    - "output/<dataset>/dependency_report.json"
    - "output/<dataset>/root_cause.json"
    - "output/<dataset>/recommendations.json"
    - "output/<dataset>/patch_suggestions.json"

  output_file: "output/<dataset>/datadog_analysis_report.md"

  settings:
    include_executive_summary:   true
    include_all_sections:        true
    max_top_issues_per_section:  5
    include_patch_suggestions:   true
    include_appendix_raw_counts: true
```

---

## Pre-requisites

- All upstream agent output JSON files under `output/<dataset>/` must exist
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_files` and write only the configured `output_file`.
- Every `input_files` entry and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from report titles, timestamps, services, upstream filenames, or existing files in `output/`.
- If the input files do not all share the same dataset folder, or if the output path points elsewhere, stop before writing and report the mismatch.
- The final markdown must identify the dataset name or input target it summarizes so a human can verify it belongs to the expected input.

---

## CORE RULES

### MUST
- MUST load all upstream report JSON files
- MUST preserve the per-dataset runner contract by noting that every generated dataset folder MUST contain a companion `run_datadog_analysis.py` for future reruns, and that the script MUST be written only inside that resolved output dataset folder
- MUST produce an executive summary at the top — overall health verdict, incident count, top 3 risks
- MUST include one section per domain: Errors & Data Quality, Performance & Infrastructure,
  Pipeline Health, Security, Anomalies, Dependency/Breakpoint Analysis
- MUST include a dedicated Root Cause Analysis section summarising each incident
- MUST include a dedicated Recommendations section listing all ranked recommendations with priority
- MUST include a Patch Suggestions section (if `include_patch_suggestions=true`), clearly marked as requiring human review
- MUST use consistent Markdown formatting — headers, tables, and severity badges (🔴 CRITICAL / 🟠 ERROR / 🟡 WARN / 🟢 OK)
- MUST cap each domain section to `max_top_issues_per_section` issues, noting how many more exist
- MUST include an overall health score or verdict (HEALTHY / DEGRADED / CRITICAL)
- MUST write the final report to `datadog_analysis_report.md`

### MUST NOT
- MUST NOT omit the Recommendations section under any circumstance, even if empty
- MUST NOT re-analyse or recompute findings — only consolidate and format what upstream agents produced
- MUST NOT include raw PII or credential values (must already be redacted by Security Audit Agent)
- MUST NOT present patch suggestions as already applied — must clearly state human review is required
- MUST NOT modify any upstream input files

---

## Overall Health Verdict Logic

| Verdict | Condition |
|---|---|
| `CRITICAL` | 1+ HIGH-confidence root cause incident with CRITICAL severity, or any CRITICAL-severity finding in `security_report.json` |
| `DEGRADED` | 1+ ERROR-severity finding across any domain, or 1+ MEDIUM-confidence incident |
| `HEALTHY` | No ERROR or CRITICAL findings in any upstream report |

---

## Report Structure — `datadog_analysis_report.md`

```markdown
# Datadog Observability Analysis Report
**Generated:** {{timestamp}} | **Analysis Period:** {{from}} → {{to}}
**Overall Health:** {{HEALTHY | DEGRADED | CRITICAL}}

---

## Executive Summary
- Total incidents identified: {{N}}
- Critical issues: {{N}} | Error issues: {{N}} | Warnings: {{N}}
- Top risks:
  1. {{top risk 1}}
  2. {{top risk 2}}
  3. {{top risk 3}}

---

## 1. Errors & Data Quality
{{table from log_analysis.json}}

## 2. Performance & Infrastructure
{{table from metrics_report.json}}

## 3. Pipeline Health
{{table from apm_report.json}}

## 4. Security
{{table of redacted findings from security_report.json}}

## 5. Anomalies & Trends
{{table from anomaly_report.json}}

## 6. Dependency & Breakpoint Analysis
{{breakpoints and cascading failures from dependency_report.json}}

---

## Root Cause Analysis
{{incident summaries from root_cause.json}}

---

## Recommendations
{{ranked recommendations table from recommendations.json, grouped by priority}}

---

## Patch Suggestions (Human Review Required)
{{diffs and explanations from patch_suggestions.json, if include_patch_suggestions=true}}

---

## Appendix — Ingestion Summary
{{raw record counts from normalised_data.json, if include_appendix_raw_counts=true}}
```

---

## Execution Workflow

### Phase 0 — Load All Upstream Reports
1. Read all input JSON files
2. Validate each parses correctly; if any is missing, note "Section unavailable" in the report rather than failing entirely

### Phase 1 — Compute Executive Summary
1. Aggregate total CRITICAL / ERROR / WARN counts across all domain reports using this exact formula — sum the
   pre-computed summary fields from each report rather than re-deriving them: `total_critical` =
   `log_analysis.summary.total_critical` + `metrics_report.summary.critical_issues` +
   `apm_report.summary.critical_issues` + `security_report.summary.critical_issues`; `total_error` =
   `log_analysis.summary.total_errors`; `total_warn` = `log_analysis.summary.total_warnings` +
   `metrics_report.summary.warn_issues` + `apm_report.summary.warn_issues` + `security_report.summary.warn_issues`.
   Never hand-pick a subset of reports or leave any of the four out
2. Determine overall health verdict
3. Select top 3 risks: prioritise HIGH-confidence root cause incidents, then highest blast radius, then highest
   severity standalone findings. Each top risk MUST be a complete, human-readable sentence describing the
   problem and its impact (e.g. "Kafka consumer lag critical on checkout-consumer, cascading to order-service
   and payment-service latency") — never a bare `issue_type` label on its own (e.g. never just "KAFKA_LAG_HIGH"
   or "BRUTE_FORCE_ATTEMPT" with no context)

### Phase 2 — Build Domain Sections
1. For each domain report, extract top issues (up to `max_top_issues_per_section`)
2. Format as Markdown tables: Service | Issue | Severity | Detail
3. Omit empty sections only if `include_all_sections=false`

### Phase 3 — Dependency, Root Cause & Recommendations Sections
1. Render breakpoints and cascading failures from `dependency_report.json`
2. Render each incident from `root_cause.json`: category, confidence, affected services, root cause finding, downstream symptoms
3. Render `recommendations.json` as a table grouped by priority

### Phase 4 — Patch Suggestions Section
1. If `include_patch_suggestions=true`, render each patch from `patch_suggestions.json` with its diff, risk level, and explanation
2. Prominently label the section: "⚠️ All patches require human review before applying"

### Phase 5 — Appendix & Write Output
1. If `include_appendix_raw_counts=true`, summarise record counts per source type from `normalised_data.json`
2. Assemble all sections in order and write final Markdown to `datadog_analysis_report.md`

### Phase 6 — Mandatory Pre-Write Validation
Before writing the file, run this checklist against the assembled content. Do not skip it even under time
pressure — these are the specific failure modes observed in prior runs of this pipeline:
1. **Top risks are sentences, not labels.** For each of the 3 top risks, confirm it is a complete sentence
   naming the problem, the affected service(s), and the impact — not a bare `issue_type` or the pattern
   "`{SEVERITY}` incident: `{root_cause_category}` impacting `{services}`". If `root_cause.json` has an incident
   whose `root_cause_category` is `UNDETERMINED`, do not simply render "UNDETERMINED impacting X" — instead
   write the sentence from that incident's `root_cause_finding` text (e.g. "Kafka consumer lag reached
   125,000 on ecommerce-events, cascading to order-service and payment-service"), since `root_cause_finding`
   is required by that agent's spec to always be populated even when the category itself is undetermined.
   This sentence requirement applies EQUALLY when `root_cause.json` has fewer than 3 incidents and the
   remaining top-risk slots are filled from "highest severity standalone findings" per that agent's own
   selection logic — a standalone finding pulled directly from a domain report (e.g. `apm_report.json`'s
   `kafka.topics[]`) still has a `description` field; render the sentence from that field (e.g. "Kafka
   consumer lag on checkout-consumer reached 68,000 messages, approaching the critical threshold"), never
   the bare `issue_type` value (e.g. never just "KAFKA_LAG_HIGH on checkout-consumer"). Additionally, verify
   the 3 top risks are not duplicates of each other (same underlying finding rendered twice) — if fewer than
   3 genuinely distinct risks exist in the input, list fewer than 3 rather than repeating one
2. **Recommendations/Patches sections aren't silently empty when they shouldn't be.** If `root_cause.json` has
   1+ incidents but `recommendations.json` has 0 entries, this is very likely an upstream defect (the Root
   Cause Analysis Agent should generate a recommendation for every incident) — flag this explicitly in the
   report as "⚠️ N incident(s) identified but 0 recommendations were generated — verify Root Cause Analysis
   Agent ran correctly" rather than quietly rendering an empty section as if it were a clean bill of health
3. **No malformed rows.** Scan domain tables for blank `Issue`/`Detail` cells (e.g. a Pipeline Health row with
   an empty issue_type and empty detail) — if the underlying JSON entry has no usable description, omit that
   row rather than rendering a table row of blanks
4. **No duplicate entities.** If a domain report's underlying array contains more than one entry for what is
   structurally the same entity (e.g. the same host appearing twice with conflicting data), consolidate to one
   row and note the discrepancy rather than printing both

---

## Table Integrity (added after a real run produced misaligned columns)

A prior implementation rendered an "Errors & Data Quality" table whose header was
`Service | Issue | Severity | Detail` but whose data rows were shifted by one column — the `Service`
cell showed a bare rank number (`1`, `2`, `3`...) instead of a service name, an extra unlabeled value
appeared, and the actual message/detail text was missing entirely. This kind of shift happens when a
row is built by positionally concatenating fields in a different order than the header declares,
instead of explicitly mapping each named field to its named column.

**MUST:** build every table row by explicitly assigning each source field to its declared header
column by name (e.g. `row.service`, `row.issue_type`, `row.severity`, `row.detail`) — never by
positional list/tuple unpacking that could silently shift if an extra or reordered field is present
upstream.

**MUST:** before writing the file, verify for every table: (a) every data row has exactly as many
`|`-separated cells as the header row, and (b) the cell under a column named `Service`/`Host` contains
a service or host name string, never a bare integer (a bare integer there is a near-certain sign the
row is shifted). Fail this self-check and fix the row-building logic rather than shipping a
misaligned table.

## Severity-Aware Truncation (added after a real run silently dropped CRITICAL findings)

A prior implementation's Security section showed only the first 5 `UNAUTHORISED_ACCESS` (ERROR)
findings from the input's original order and cut off before reaching the `PII_IN_LOGS`,
`CREDENTIAL_LEAK`, and `BRUTE_FORCE_ATTEMPT` findings (all CRITICAL) that appeared later in the source
array — directly violating the existing "MUST NOT hide critical security findings" rule elsewhere in
this file, because truncation was applied in raw input order rather than severity order.

**MUST:** before applying `max_top_issues_per_section`, sort each domain's findings by severity
descending (CRITICAL > ERROR > WARN > INFO), then by whatever secondary key the domain uses (e.g.
deviation, blast radius) — truncate only after that sort. Every domain table capped by
`max_top_issues_per_section` MUST include all CRITICAL findings for that domain before including any
lower-severity ones, even if that means fewer ERROR/WARN rows are shown.

Self-test: given 6 ERROR findings and 3 CRITICAL findings in `security_report.json` and
`max_top_issues_per_section: 5`, the rendered Security table MUST include all 3 CRITICAL findings
(dropping ERROR-level rows to make room), never all 5 slots filled by ERROR findings while CRITICAL
findings are omitted.

## Output Specification

| Artifact | Description |
|---|---|
| `datadog_analysis_report.md` | Full human-readable report — executive summary, all domain findings, dependency/breakpoint analysis, root cause analysis, ranked recommendations, patch suggestions |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| Report missing a section | Upstream JSON file not found or malformed | Verify the corresponding agent ran successfully and produced valid JSON |
| Health verdict seems wrong | Verdict logic misapplied | Re-check CRITICAL/ERROR counts across all domain reports against verdict table |
| Recommendations section empty | `recommendations.json` has zero entries | Verify Root Cause Analysis Agent found at least one incident |
| Patch section missing | `patch_suggestions.json` not generated | Verify Code Patch Generator Agent ran and `include_patch_suggestions=true` |

---

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- The markdown report MUST be written as valid UTF-8. Characters such as arrows, dashes, or quotes must not render as mojibake like `â†’` or `â€”`.
- Every count in the executive summary MUST be computed from upstream JSON fields using documented formulas; do not manually restate stale numbers.
- If an upstream report has known validation failures or unresolved critical findings, the report MUST surface them in the executive summary or a validation notes section.
- Do not hide critical security findings behind a single dependency incident. Top risks must include the highest-severity issues across all domains.
- The report MUST preserve traceability: each major claim must map to at least one upstream report section or finding.
- If `patch_suggestions.patches[].requires_human_review` is true, the patch section heading or row must make human review visible.
- Before writing, scan the generated markdown for replacement characters, mojibake sequences, empty tables, and unresolved placeholders.
- The final markdown MUST include all required top-level sections: Executive Summary, Errors & Data Quality,
  Performance & Infrastructure, Pipeline Health, Security, Anomalies & Trends, Dependency & Breakpoint
  Analysis, Root Cause Analysis, Recommendations, Patch Suggestions, and Appendix/Ingestion Summary.
- Reject the report if it is only a short root-cause/recommendations summary and omits domain sections while
  the corresponding upstream JSON files contain findings.

Reject the generated report if it contains encoding artifacts, placeholder text, or executive-summary counts that disagree with upstream JSON.


---

## 🚫 Final Hardcoding Check (applies on top of everything above)

Before accepting this agent's implementation as done: pick any literal value in its output (a service
name, an ID, a count, a percentage) and ask "would this exact value still appear if I fed the agent a
different input file with different data?" If the answer is yes for a value that should depend on the
input, the implementation is hardcoded and must be rewritten to derive that value from the actual input
at runtime. This check applies to every JSON/Markdown artifact this agent produces, not just the fields
called out elsewhere in this file.