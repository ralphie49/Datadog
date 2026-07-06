# Datadog Analyser Orchestrator

## Purpose
Enable **GitHub Copilot / Claude** as a **Senior Observability Engineer** orchestrating a complete **Datadog log and metrics analysis pipeline** across any monitored system.

**Technology:** Python, Datadog Exported Files (JSON / CSV), Markdown

**Invocation Examples:**
- `Datadog Analyser Orchestrator` *(reads config from this file, runs all agents)*
- `Datadog Analyser Orchestrator (logs only)` *(runs only log-related agents)*
- `Datadog Analyser Orchestrator (full analysis)` *(runs all agents end to end)*
- `Datadog Analyser Orchestrator (multi-source)` *(runs each input subfolder separately and writes outputs under `output/<dataset>/`)*

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
orchestrator_config:
  pipeline_name: "Datadog Observability Analysis"
  version: "1.0.0"

  # PATH CONVENTION: every path in this file and every agent file is relative to this
  # project's root folder (wherever you place your input folder, output/, and agents/ as siblings).
  # The root folder itself can be named anything — nothing anywhere in this pipeline
  # hardcodes a project name. Just keep your input folder, output/, and agents/ as siblings under
  # whatever root you use, and run the orchestrator from that root.

  # ── Input Files ────────────────────────────────────────────────────────────
  # No filenames are hardcoded. The orchestrator points to a FOLDER; the
  # Log Ingestion & Normaliser Agent scans every file in it and auto-detects
  # each file's type (logs / metrics / traces / alerts / infrastructure) by
  # inspecting its structure and field names — never by filename or extension
  # alone. This means it works unmodified against any real Datadog export,
  # regardless of what the files are named or what date is in the filename.
  input:
    input_root: "<path-to-your-input-folder>/"

  # ── Output Folder ──────────────────────────────────────────────────────────
  output:
    root: "output/"
    normalised_data:  "output/<dataset>/normalised_data.json"
    log_analysis:     "output/<dataset>/log_analysis.json"
    metrics_report:   "output/<dataset>/metrics_report.json"
    apm_report:       "output/<dataset>/apm_report.json"
    security_report:  "output/<dataset>/security_report.json"
    anomaly_report:   "output/<dataset>/anomaly_report.json"
    dependency_report: "output/<dataset>/dependency_report.json"
    root_cause:       "output/<dataset>/root_cause.json"
    recommendations:  "output/<dataset>/recommendations.json"
    patch_suggestions: "output/<dataset>/patch_suggestions.json"
    final_report:     "output/<dataset>/datadog_analysis_report.md"

  # Note: a specific input folder name is not required. The orchestrator should support any
  # valid input target passed by the caller.
  #
  # DATASET ROUTING CONTRACT:
  # - A dataset is one caller-supplied input target.
  # - If the target is a folder, the dataset name is that folder's basename, and all
  #   classified files inside that folder are processed together into one output folder.
  #   Example: input/ containing logs, metrics, traces, alerts, and infrastructure files
  #   writes ONLY to output/input/.
  # - If the target is a single file explicitly passed by the caller, the dataset name is
  #   that file's basename without extension, and only that file writes to its matching
  #   output folder.
  # - If the caller passes an input root that contains multiple child dataset folders, each
  #   child folder is processed independently under output/<child-folder-name>/.
  # - The orchestrator MUST resolve <dataset> exactly once per input target before Phase 0
  #   and pass the fully resolved output paths to every agent. Downstream agents MUST NOT
  #   recalculate, rename, guess, or switch the dataset folder.
  # - Every artifact for one dataset MUST stay under output/<dataset>/ and MUST NOT be
  #   written to output/, another dataset folder, the input folder, or the project root.
  # - If any phase is about to read from one dataset folder and write to a different dataset
  #   folder, the orchestrator MUST stop before writing and mark the run invalid.
  #
  # If code generation is used, generated `run_datadog_analysis.py` for a given dataset
  # must be created inside that dataset's matching `output/<dataset>/` subfolder so future
  # runs of that same dataset can be replayed without needing to remember the original
  # folder name. It MUST NOT be created in the project root, the top-level `output/`
  # folder, or any other dataset folder. This rule applies to every future input folder
  # name as well, including folders introduced later by new exports.
  # Example CLI:
  #   python run_datadog_analysis.py --input-root <path-to-your-input-folder> --output-root output
  #   python run_datadog_analysis.py <path-to-your-input-folder>/customerA <path-to-your-input-folder>/customerB --output-root output
  #   python run_datadog_analysis.py <path-to-your-input-folder>/export1.json <path-to-your-input-folder>/export2.csv --output-root output

  # ── Analysis Settings ──────────────────────────────────────────────────────
  settings:
    error_threshold:       "ERROR"
    latency_threshold_ms:  1000
    anomaly_sensitivity:   "medium"
    max_recommendations:   10
    max_patches:            10
```

## Validation and Regression Rules

## Dataset and Artifact Routing Rules

These rules prevent a common failure mode where the generated implementation reads one input dataset
but writes artifacts into the wrong output folder or overwrites artifacts from another dataset.

- The orchestrator MUST build a dataset manifest before Phase 0. Each entry must contain:
  `dataset_name`, `input_target`, `output_dir`, and the exact artifact paths for every phase.
- `dataset_name` MUST be path-safe and deterministic: use the input target basename, trim extensions
  only for single-file targets, replace path separators and unsafe characters with `_`, and never use
  a date, guessed customer name, source type, or hardcoded string unless it is literally the target basename.
- For a folder target such as `input/`, all Datadog export files inside that folder belong to the same
  dataset and all artifacts must go under `output/input/`.
- For multiple dataset targets, each phase must run in a per-dataset loop. Finish and validate all phases
  for dataset A under `output/A/` before writing any artifact for dataset B under `output/B/`.
- Before every write, verify the destination path starts with that dataset's resolved `output_dir`.
- Before every downstream read, verify every input artifact path starts with the same dataset's resolved
  `output_dir`. Mixed reads such as `output/customerA/log_analysis.json` plus
  `output/customerB/metrics_report.json` are invalid.
- Do not infer output paths from existing files in `output/`. Existing artifacts may be stale or belong
  to another input. The current run's manifest is the source of truth.
- Do not fall back to `output/input/`, `output/default/`, `output/<source_type>/`, or the project root
  when an output path is missing. Stop and report the missing route instead.
- The validation manifest MUST record, for every artifact, the dataset name, source input target, expected
  path, actual written path, and a pass/fail value for "path is inside dataset output_dir".

Reject the full pipeline output if any artifact is written outside its dataset's resolved output folder,
even if the JSON schema and final markdown content otherwise look correct.

## Schema Fidelity Rules

These rules prevent another common failure mode: the generated script creates files with plausible names
but simplified or renamed schemas that downstream agents cannot trust.

- The generated implementation MUST use the exact artifact schemas declared in each agent file. Do not
  replace them with smaller convenience schemas.
- Property names are part of the contract. For example, `dependency_report.json` MUST use
  `dependency_graph` and `breakpoints[].breakpoint_service`; it MUST NOT use `service_graph` or
  `breakpoints[].service` as substitutes.
- `patch_suggestions.json` MUST use a `patches` array with `diff` and `requires_human_review`; it MUST
  NOT use a prose-only `patch_suggestions` array as a substitute.
- `root_cause.json` MUST include `summary.total_incidents`, `incidents[].root_cause_category`,
  `unresolved_findings`, and `all_incidents`.
- `recommendations.json` MUST include `summary.total_recommendations` and ranked recommendation entries
  with evidence. A list of titles and descriptions is not enough.
- `apm_report.json` MUST include the Kafka section defined by the Pipeline Health Monitor Agent; a generic
  `sla_breaches` list is not a substitute for Kafka/topic/consumer-group analysis.
- If a generated implementation cannot populate a required field from evidence, it must write an empty
  value of the correct type plus a validation warning. It MUST NOT rename or remove the field.
- The validation manifest MUST include a schema-fidelity check for every artifact and must mark the run
  invalid if any artifact uses renamed, missing, or simplified fields.

Reject the full pipeline output if any generated artifact has the right filename but the wrong schema.

The orchestrator must fail fast if a downstream artifact is empty when the upstream evidence clearly supports a non-empty result. In particular:
- `dependency_report.json` must not be empty when the anomaly phase produced a multi-service correlated incident and the dependency graph contained an edge between those services.
- `root_cause.json` and `recommendations.json` must not be empty when a dependency breakpoint exists.
- `patch_suggestions.json` must not be empty when a P1/P2 recommendation exists.
- The final markdown report must contain explicit content in the root-cause, recommendations, and patch sections instead of blank tables or placeholder-only output.


The pipeline must be robust to arbitrary Datadog exports and avoid the faults fixed in the current implementation:

- Input discovery must be content-driven; do not depend on fixed filenames.
- Every output JSON file must be valid and include required schema fields.
- `analysis_period` must always be an object with `from` and `to`.
- The final markdown report must not render blank table rows, and truncated tables must include an explicit note.
- `log_analysis.json` must preserve every error occurrence in `all_errors`, not just top grouped errors.
- `metrics_report.json` must only flag hosts when their actual host metrics exceed thresholds, so healthy hosts such as `host-02` are not incorrectly flagged.
- `anomaly_report.json` must derive anomaly timestamps from the triggering records and deduplicate correlated anomalies.
- `apm_report.json` must count `pipelines_with_issues` as distinct pipeline/topic/consumer-group names, never as a raw issue count.
- Dependency analysis must dedupe breakpoints and only declare them when supported by upstream evidence.
- Root cause incidents must not merge unrelated domains without a causal path, and every critical upstream finding must be represented in incident evidence or unresolved findings.

---

## Pipeline Overview

```
Input Files (logs / metrics / traces / alerts / infrastructure)
  ↓
[Phase 0] Log Ingestion & Normaliser Agent        — normalise and classify all input files
[Phase 1] Error & Data Quality Agent              — detect errors + DQ rejection/quarantine issues
[Phase 2] Performance & Infrastructure Health Agent — latency, throughput, CPU/memory/disk, host health
[Phase 3] Pipeline Health Monitor Agent            — Kafka lag, checkpoint, SLA breaches
[Phase 4] Security Audit Agent                     — PII exposure, unauthorised access, compliance
[Phase 5] Anomaly Detection Agent                  — anomalies and degradation over time
[Phase 6] Dependency/Flow Analysis Agent           — service dependency graph, breakpoint identification
[Phase 7] Root Cause Analysis Agent                — correlate all findings, suggest fixes
[Phase 8] Code Patch Generator Agent               — draft patch suggestions for human review
[Phase 9] Report Generation Agent                  — final markdown report
[Phase 10] Output Content Validation Agent         — validate every output file's content and schema
```

---

## Agent Invocations

### Phase 0 — Log Ingestion & Normaliser Agent
**Agent:** `agents/log-ingestion-normaliser-agent.md`

```yaml
context:
  input_folder: "<path-to-your-input-folder>/"
  output_file:  "output/<dataset>/normalised_data.json"
```

**Expected output:** `normalised_data.json`
**STOP condition:** If the input folder is empty, unreadable, or contains no file that can be
classified as logs/metrics/traces/alerts/infrastructure after auto-detection → report and stop.

---

### Phase 1 — Error & Data Quality Agent
**Agent:** `agents/error-data-quality-agent.md`

```yaml
context:
  input_file:  "output/<dataset>/normalised_data.json"
  output_file: "output/<dataset>/log_analysis.json"
  error_threshold: "ERROR"
```

**Expected output:** `log_analysis.json` — classified application errors + DQ rejection rates, quarantine trends, worst columns

---

### Phase 2 — Performance & Infrastructure Health Agent
**Agent:** `agents/performance-infrastructure-health-agent.md`

```yaml
context:
  input_file:           "output/<dataset>/normalised_data.json"
  output_file:          "output/<dataset>/metrics_report.json"
  latency_threshold_ms: 1000
```

**Expected output:** `metrics_report.json` — latency/throughput per service, host resource health, storage issues

---

### Phase 3 — Pipeline Health Monitor Agent
**Agent:** `agents/pipeline-health-monitor-agent.md`

```yaml
context:
  input_file:  "output/<dataset>/normalised_data.json"
  output_file: "output/<dataset>/apm_report.json"
```

**Expected output:** `apm_report.json` — Kafka lag, checkpoint health, SLA breaches, pipeline backlog

---

### Phase 4 — Security Audit Agent
**Agent:** `agents/security-audit-agent.md`

```yaml
context:
  input_file:  "output/<dataset>/normalised_data.json"
  output_file: "output/<dataset>/security_report.json"
```

**Expected output:** `security_report.json` — PII exposure, unauthorised access, compliance violations

---

### Phase 5 — Anomaly Detection Agent
**Agent:** `agents/anomaly-detection-agent.md`

```yaml
context:
  input_files:
    - "output/<dataset>/log_analysis.json"
    - "output/<dataset>/metrics_report.json"
    - "output/<dataset>/apm_report.json"
    - "output/<dataset>/security_report.json"
  output_file:         "output/<dataset>/anomaly_report.json"
  anomaly_sensitivity: "medium"
```

**Expected output:** `anomaly_report.json` — detected anomalies, degradation trends across all data

---

### Phase 6 — Dependency/Flow Analysis Agent
**Agent:** `agents/dependency-flow-analysis-agent.md`

```yaml
context:
  input_files:
    - "output/<dataset>/normalised_data.json"
    - "output/<dataset>/metrics_report.json"
    - "output/<dataset>/apm_report.json"
    - "output/<dataset>/anomaly_report.json"
  output_file: "output/<dataset>/dependency_report.json"
```

**Expected output:** `dependency_report.json` — service dependency graph, identified breakpoints, cascading failure chains

---

### Phase 7 — Root Cause Analysis Agent
**Agent:** `agents/root-cause-analysis-agent.md`

```yaml
context:
  input_files:
    - "output/<dataset>/log_analysis.json"
    - "output/<dataset>/metrics_report.json"
    - "output/<dataset>/apm_report.json"
    - "output/<dataset>/security_report.json"
    - "output/<dataset>/anomaly_report.json"
    - "output/<dataset>/dependency_report.json"
  output_files:
    root_cause:      "output/<dataset>/root_cause.json"
    recommendations: "output/<dataset>/recommendations.json"
  max_recommendations: 10
```

**Expected output:** `root_cause.json` + `recommendations.json` — correlated root cause (incorporating dependency breakpoints) and ranked fixes

---

### Phase 8 — Code Patch Generator Agent
**Agent:** `agents/code-patch-generator-agent.md`

```yaml
context:
  input_files:
    - "output/<dataset>/recommendations.json"
    - "output/<dataset>/root_cause.json"
  output_file:  "output/<dataset>/patch_suggestions.json"
  max_patches:  10
```

**Expected output:** `patch_suggestions.json` — draft patches for P1/P2 recommendations, human review required before applying

---

### Phase 9 — Report Generation Agent
**Agent:** `agents/report-generation-agent.md`

```yaml
context:
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
```

**Expected output:** `datadog_analysis_report.md` — final human-readable report with all findings, dependency analysis, root cause, recommendations, and patch suggestions

---

### Phase 10 — Output Content Validation Agent
**Agent:** `agents/output-content-validation-agent.md`

```yaml
context:
  input_target: "<path-to-your-input-folder>/"
  output_dir: "output/<dataset>/"
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
    - "output/<dataset>/datadog_analysis_report.md"
  output_file: "output/<dataset>/validation_manifest.json"
```

**Expected output:** `validation_manifest.json` — content and schema validation manifest. The full pipeline is
not complete unless this manifest exists and has `status: "valid"`.

---

## Mandatory Self-Verification (applies to code generation, not just manual runs)

If this orchestrator and its agent files are being used as a spec for **generating an implementation**
(e.g. asking Copilot/Claude to write a script that performs this pipeline), code generation is not
considered complete after a single pass that merely compiles/runs without error. Each agent `.md` file
now includes a "Self-Test Cases" section with concrete input → expected-output pairs grounded in this
project's own sample data (your input folder). Before presenting generated code as done:

1. Run the generated implementation against your input folder end to end.
2. After each phase, validate the generated output against the corresponding input data and the expected
   schema/field rules before moving to the next phase. If validation fails, stop and correct the output
   instead of continuing with downstream phases.
3. For each agent whose `.md` file contains a "Self-Test Cases" section, mechanically check the
   corresponding output file against every listed assertion (exact field values, non-empty arrays,
   absence of specific placeholder/duplicate values, cross-file consistency such as a field in one report
   matching a field in another).
4. If any self-test fails, treat this the same as a compile error: locate the responsible function, fix
   it, and re-run the full pipeline — do not report the pipeline as complete with a known-failing self-test.
5. Only report generation as finished once every listed self-test passes. If a self-test cannot be
   satisfied without more information, say so explicitly rather than silently shipping code that fails it.

This step exists because prose MUST/MUST NOT rules alone have not been sufficient to prevent regressions
in prior runs of this pipeline (see individual agent version histories) — several defects were in fallback
and deduplication branches that a single untested generation pass does not reliably exercise.

## Mandatory Acceptance Test For The Sample `input/` Folder

When the input target is the sample folder named `input`, the generated implementation MUST run this exact
acceptance test before claiming success. These are not optional examples; they are regression gates derived
from failures observed in generated scripts.

### Expected Source File Classification

`normalised_data.json` MUST prove these exact classifications and counts:

| Physical input file | Required source type | Required record count |
|---|---:|---:|
| `datadog_logs_export_20260702.json` | `log` | 26 |
| `datadog_metrics_export_20260702.csv` | `metric` | 35 |
| `datadog_traces_export_20260702.json` | `trace` | 12 |
| `datadog_monitor_alerts_20260702.json` | `alert` | 7 |
| `datadog_infrastructure_export_20260702.json` | `infrastructure` | 10 |

The total normalized record count MUST be 90. `normalised_data.json.records[]` MUST contain all 90 records.
A file-count summary without `records[]` is invalid. The logs file MUST NOT appear under infrastructure.

### Expected Cross-Artifact Evidence

The generated outputs MUST pass all of these checks:

- `log_analysis.json.summary.total_errors` MUST equal 19 because the log input contains 15 `ERROR` records
  and 4 `CRITICAL` records. CRITICAL records count as errors for this summary.
- `log_analysis.json.all_errors[]` MUST include the two CRITICAL Kafka lag records, the CRITICAL PII record,
  and the CRITICAL credential leak record. Do not silently exclude CRITICAL rows by filtering only `level == "ERROR"`.
- `log_analysis.json.worst_columns[]` MUST include at least `email` with rejection count 45 and `phone` with
  rejection count 30, parsed from the `DQ_ALERT rejection_reason=... count=N` lines.
- `security_report.json.findings[]` MUST include at least one `PII_IN_LOGS`, one `CREDENTIAL_LEAK`, and one
  brute-force/unauthorized-access finding for `user-service`. Zero or one total security finding is invalid
  for this sample input.
- `apm_report.json.kafka.topics[]` MUST include Kafka lag findings for topic `ecommerce-events`,
  consumer group/service `checkout-consumer`, and lag values 125000 and 118000.
- `dependency_report.json` MUST contain top-level `dependency_graph` as an object with `nodes` and `edges`,
  not a bare array. It MUST NOT use `service_graph`.
- If a dependency breakpoint is emitted for the Kafka incident, its `breakpoint_service` MUST be
  `checkout-consumer` and its `downstream_impact` MUST include `payment-service` and/or `order-service`
  when those services show corroborating failures.
- `root_cause.json.summary.total_incidents` MUST equal `len(root_cause.json.incidents)`, and every incident
  MUST have `incident_id`, `root_cause_category`, `confidence`, `affected_services`, `root_cause_finding`,
  `evidence_sources`, `severity`, and `blast_radius`.
- `recommendations.json.summary.total_recommendations` MUST equal `len(recommendations.json.recommendations)`.
  Every recommendation MUST have `rank`, `priority`, `incident_id`, `title`, `description`, `action`,
  `affected_services`, and `evidence`.
- `patch_suggestions.json` MUST have a top-level `patches[]` array, not `patch_suggestions[]`. Every generated
  patch MUST include `patch_id`, `incident_id`, `recommendation_ref`, `patch_type`, `risk_level`,
  `target_file`, `explanation`, `diff`, and `requires_human_review`.
- `datadog_analysis_report.md` MUST include all required domain sections, not only Root Cause,
  Recommendations, and Patch Suggestions.
- `validation_manifest.json` MUST exist and mark the run invalid if any check above fails.

If any one of these checks fails, the generated implementation MUST fix the responsible phase and rerun the
full pipeline. Do not present the run as complete, even if all files exist and parse as JSON.

---

## Completion Checklist

| Phase | Agent | Status |
|---|---|---|
| 0 | Log Ingestion & Normaliser | [ ] |
| 1 | Error & Data Quality | [ ] |
| 2 | Performance & Infrastructure Health | [ ] |
| 3 | Pipeline Health Monitor | [ ] |
| 4 | Security Audit | [ ] |
| 5 | Anomaly Detection | [ ] |
| 6 | Dependency/Flow Analysis | [ ] |
| 7 | Root Cause Analysis | [ ] |
| 8 | Code Patch Generator | [ ] |
| 9 | Report Generation | [ ] |
| 10 | Output Content Validation | [ ] |

**Analysis is complete only when all boxes are checked, `datadog_analysis_report.md` is generated, and
`validation_manifest.json.status` is `valid`.**

---

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- The orchestrator MUST run agents in dependency order and MUST stop or mark the run invalid if a required upstream artifact is missing or fails JSON/CSV parsing.
- After each phase, run that agent's LLM Output Contract checks before invoking downstream phases.
- Downstream agents MUST consume only validated upstream artifacts. Do not let a later report hide or overwrite an earlier validation failure.
- The final run status MUST include `valid`, `invalid`, or `valid_with_warnings`.
- If any agent rejects its own output, the orchestrator MUST surface the agent name, artifact, and failed rule in the final report.
- The orchestrator MUST compare sample input row counts against `normalised_data.json` counts before continuing.
- Summary counts across reports MUST reconcile: report-generation counts must match the JSON reports, and JSON summary counts must match their detail arrays.
- The generated script MUST be deterministic: same inputs produce same output order, IDs, and counts.
- The orchestrator MUST write a validation manifest listing every generated artifact, status, and failed/passed contract checks.
- The validation manifest MUST be produced by the Output Content Validation Agent and MUST include content
  checks, not only path checks.
- The validation manifest MUST prove that each artifact's actual written path is exactly the path resolved
  in the dataset manifest for the current input target.
- For a folder input target, the generated implementation MUST NOT split sibling Datadog export files
  into separate output folders unless those files were explicitly passed as separate input targets.
- The generated implementation MUST reject its own output if `normalised_data.json.classified_files.logs`
  is empty while any input JSON file contains log-shaped records (`timestamp` + `message` + `level`/`severity`).
- The generated implementation MUST reject its own output if any artifact uses renamed schema fields such
  as `service_graph` instead of `dependency_graph`, `patch_suggestions` instead of `patches`, or
  `incident_count` instead of `total_incidents`.

Reject the full pipeline output if any phase fails its own contract and the final report still claims the run is clean.
