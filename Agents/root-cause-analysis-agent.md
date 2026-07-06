# Root Cause Analysis Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis

---

## Purpose

Correlates findings from all upstream agents — errors, data quality, performance,
infrastructure, streaming health, security, anomalies, and service dependency structure —
to identify the most likely root cause(s) behind system degradation. Produces a ranked,
actionable list of recommendations tied directly to evidence gathered across the pipeline.

**Outputs:** `root_cause.json`, `recommendations.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
root_cause_config:
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

  settings:
    max_recommendations:        10
    min_evidence_sources:       2
    correlation_window_minutes: 5
    confidence_high_threshold:  3
    confidence_medium_threshold: 2
```

---

## Pre-requisites

- All upstream agent output JSON files under `output/<dataset>/` must exist, including `dependency_report.json`
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_files` and write only the configured `output_files`.
- Every `input_files` entry and every `output_files` entry MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from incident IDs, services, root-cause categories, dates, upstream filenames, or existing files in `output/`.
- If the input files and output files do not all share the same dataset folder, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist)
- MUST load all upstream report JSON files, including the dependency graph and breakpoint findings
- MUST group findings that occur within `correlation_window_minutes` of each other into a single incident
- MUST prefer a breakpoint identified in `dependency_report.json` as the root cause candidate when one exists for the incident window, over pure time-correlation guessing
- MUST require at least `min_evidence_sources` corroborating findings before naming a root cause
- MUST assign a confidence level to each root cause: LOW | MEDIUM | HIGH
- MUST distinguish between root cause (originating issue) and symptom (downstream effect)
- MUST rank incidents by severity and blast radius (number of affected services)
- MUST produce specific, actionable recommendations — never generic advice
- MUST tie every recommendation back to the specific evidence/finding that justifies it
- MUST cap output at `max_recommendations`, ranked by impact descending
- MUST write root cause analysis to `root_cause.json` and recommendations to `recommendations.json`

### MUST NOT
- MUST NOT declare a root cause from a single uncorroborated finding
- MUST NOT confuse a symptom for a root cause when a dependency-graph breakpoint or an upstream trigger exists in the same incident window
- MUST NOT produce recommendations unrelated to actual findings in the input reports
- MUST NOT cite an `issue_type` / finding category (e.g. `BRUTE_FORCE_ATTEMPT`, `KAFKA_LAG_CRITICAL`) in `root_cause_finding`, `evidence_sources`, or a recommendation's `evidence` field unless that exact issue type literally appears in the named upstream JSON file's own findings/issues list. A raw log line, alert message, or record elsewhere in the pipeline that merely *uses similar wording* is not evidence that the responsible domain agent (e.g. Security Audit Agent) confirmed it — if that agent didn't independently flag it, this agent must not either
- MUST NOT assign a single `root_cause_category` to an incident whose findings don't causally belong together. If an incident's evidence spans unrelated domains (e.g. a `PIPELINE_BACKPRESSURE` finding on one service plus a `CREDENTIAL_LEAK`/`PII_IN_LOGS` finding on an unconnected service) that were only clustered by time proximity, split them into separate incidents — a security finding is never a "downstream symptom" of a pipeline or infrastructure root cause unless a concrete causal path (e.g. a dependency-graph edge, or the same service) connects them
- MUST NOT allow a CRITICAL-severity finding from `apm_report.json`, `metrics_report.json`, `security_report.json`, or a `dependency_report.json` breakpoint to disappear from the output entirely. Every such finding must end up either represented in an incident's `evidence_sources`, or explicitly listed in `unresolved_findings` with a reason — never neither (see the timestamp-robustness safeguard in Phase 1)
- MUST NOT modify any upstream input files

---

## Root Cause Categories

| Category | Description |
|---|---|
| `RESOURCE_SATURATION` | Root cause traced to CPU/memory/disk exhaustion on one or more hosts |
| `UPSTREAM_DEPENDENCY_FAILURE` | Root cause traced to a downstream service/DB/API failing, confirmed via dependency graph breakpoint |
| `DATA_QUALITY_DEGRADATION` | Root cause traced to rising rejection rates or malformed upstream data |
| `PIPELINE_BACKPRESSURE` | Root cause traced to Kafka lag, checkpoint failure, or processing backlog |
| `SECURITY_INCIDENT` | Root cause traced to unauthorised access, credential leak, or brute force activity |
| `CONFIGURATION_DRIFT` | Root cause traced to a recent config/threshold change reflected in error patterns |
| `CAPACITY_SHORTFALL` | Root cause traced to sustained worsening trend indicating undersized infrastructure |
| `UNDETERMINED` | Evidence insufficient to confidently assign a root cause category |

---

## Category Assignment — Mandatory Mapping (prevents defaulting to UNDETERMINED)

`UNDETERMINED` is reserved for incidents where the root-cause finding genuinely does not match any
row below. It is NOT a safe default and it is NOT interchangeable with "I'd rather not commit." Before
writing `UNDETERMINED` for any incident, the agent MUST check the incident's `root_cause_finding` and
`evidence_sources` against this table, in order, and use the first row that matches:

| If `root_cause_finding` / evidence includes... | Assign `root_cause_category` |
|---|---|
| `KAFKA_LAG_*`, `CHECKPOINT_STALE`, `PROCESSING_BACKLOG`, `MISSED_TRIGGER`, `SLA_BREACH` (from `apm_report.json`) | `PIPELINE_BACKPRESSURE` |
| `PII_IN_LOGS`, `CREDENTIAL_LEAK`, `BRUTE_FORCE_ATTEMPT`, `UNAUTHORISED_ACCESS`, `PERMISSION_ESCALATION`, `COMPLIANCE_BREACH` (from `security_report.json`) | `SECURITY_INCIDENT` |
| `HIGH_REJECTION_RATE`, `REJECTION_RATE_WORSENING`, `*_SPIKE` column rejection findings (from `log_analysis.json` DQ section) | `DATA_QUALITY_DEGRADATION` |
| `HIGH_CPU`, `HIGH_MEMORY`, `HIGH_DISK`, `RESOURCE_EXHAUSTION` (from `metrics_report.json`) | `RESOURCE_SATURATION` |
| A `breakpoints[]` entry exists in `dependency_report.json` for this incident window | `UPSTREAM_DEPENDENCY_FAILURE` (this always overrides a same-incident CONNECTION_FAILURE/TIMEOUT reading from `log_analysis.json`, per the breakpoint-preference rule above) |
| `CONNECTION_FAILURE` or `TIMEOUT` error type (from `log_analysis.json`) with no dependency breakpoint identified | `UPSTREAM_DEPENDENCY_FAILURE` |
| A `WORSENING_TREND` anomaly spans the incident window with no single acute trigger | `CAPACITY_SHORTFALL` |
| None of the above apply after checking every evidence source listed for the incident | `UNDETERMINED` — and in this case the incident's `root_cause_finding` field MUST explain specifically what evidence was checked and why nothing matched, not just restate a raw log line |

A `root_cause_category` of `UNDETERMINED` on more than 0 incidents in a run is not inherently wrong, but
before finalizing output the agent MUST re-check every `UNDETERMINED` incident against this table one more
time as a self-review pass — this catches the common failure mode of correctly gathering strong evidence
(e.g. a `KAFKA_LAG_CRITICAL` finding, a `PII_IN_LOGS` finding) into `evidence_sources` / `root_cause_finding`
but then not translating that evidence into the matching category.

---

## Recommendation Priority Levels

| Priority | Description |
|---|---|
| `P1_IMMEDIATE` | Actively causing outage or critical degradation — act now |
| `P2_URGENT` | Degrading service, likely to worsen — act within hours |
| `P3_PLANNED` | Contributing to risk but not yet critical — schedule fix |
| `P4_ADVISORY` | Best-practice improvement — no immediate risk |

---

## Output Schema — `root_cause.json`

```json
{
  "summary": {
    "total_incidents":   0,
    "high_confidence":   0,
    "medium_confidence": 0,
    "low_confidence":    0,
    "analysis_period": { "from": "", "to": "" }
  },
  "incidents": [
    {
      "incident_id":         "incident_001",
      "root_cause_category": "PIPELINE_BACKPRESSURE",
      "confidence":          "HIGH",
      "primary_service":     "checkout-consumer",
      "affected_services":   ["checkout-consumer", "payment-service", "order-service"],
      "timeframe":           { "from": "2026-07-02T09:28:00Z", "to": "2026-07-02T09:40:00Z" },
      "root_cause_finding":  "KAFKA_LAG_CRITICAL on topic ecommerce-events (lag 125000)",
      "dependency_breakpoint": "checkout-consumer",
      "downstream_symptoms": [
        "HIGH_LATENCY on payment-service (p99 1200ms)",
        "ERROR_RATE_SPIKE on order-service (5.6x baseline)"
      ],
      "evidence_sources": ["apm_report.json", "metrics_report.json", "anomaly_report.json", "dependency_report.json"],
      "severity":     "CRITICAL",
      "blast_radius": 3
    }
  ],
  "unresolved_findings": [],
  "all_incidents": []
}
```

---

## Regression Gates (must pass before this agent is considered done)
- If `dependency_report.json` contains a breakpoint for an incident window, `root_cause.json.summary.total_incidents` must be at least 1 and `recommendations.json.summary.total_recommendations` must be at least 1.
- A critical dependency breakpoint must never disappear into an empty incident list; it must either become an incident or be listed in `unresolved_findings` with a reason.

## Output Schema — `recommendations.json`

```json
{
  "summary": {
    "total_recommendations": 0,
    "p1_immediate": 0,
    "p2_urgent":    0,
    "p3_planned":   0,
    "p4_advisory":  0
  },
  "recommendations": [
    {
      "rank":              1,
      "priority":          "P1_IMMEDIATE",
      "incident_id":       "incident_001",
      "title":             "Scale Kafka consumer group for ecommerce-events topic",
      "description":       "Consumer lag reached 125000 messages, 12.5x the critical threshold, causing cascading latency and error spikes in dependent services.",
      "action":            "Increase consumer instances for consumer group 'pyspark-consumer' and verify partition count supports the added parallelism",
      "affected_services": ["checkout-consumer", "payment-service", "order-service"],
      "evidence":          "apm_report.json: KAFKA_LAG_CRITICAL, lag=125000; dependency_report.json: breakpoint=checkout-consumer"
    }
  ]
}
```

---

## Execution Workflow

### Phase 0 — Load All Upstream Reports
1. Read all input JSON files, including `dependency_report.json`
2. Extract every flagged issue, error, anomaly, and breakpoint finding with its timestamp and service

### Phase 1 — Incident Clustering
1. Sort all findings chronologically across all sources
2. Group findings into incidents where timestamps fall within `correlation_window_minutes` of each other AND
   share an overlapping or dependent service. "Dependent service" means: the same service, OR two services
   connected by an edge (in either direction, any number of hops) in `dependency_report.json`'s
   `dependency_graph`. Time proximity alone is NEVER sufficient — a security finding on `user-service` occurring
   near in time to a pipeline finding on `checkout-consumer` MUST NOT be merged into one incident unless the
   dependency graph actually connects them
3. Discard incidents with fewer than `min_evidence_sources` corroborating findings
4. Deduplicate: if the same finding (identical `issue_type` + `service` + `timestamp`) appears in more than one
   upstream report, it MUST appear only once in the resulting incident's `downstream_symptoms` list
5. Before adding any finding to `downstream_symptoms`, resolve a non-empty human-readable string for it — prefer
   that finding's own `description` field, falling back to `message` or a synthesized "`issue_type` on `service`"
   string if `description` is blank or absent. An entry in `downstream_symptoms` MUST NEVER be an empty string
   (`""`) or whitespace-only; if no readable text can be resolved for a finding, omit that finding from the list
   entirely rather than inserting a blank placeholder
6. **Timestamp-robustness safeguard.** Upstream reports occasionally carry corrupted or missing per-finding
   timestamps (e.g. a finding defaulted to the analysis-period start instead of its real occurrence time), which
   can cause step 2's time-window clustering to fail even though strong, clearly-related evidence exists across
   multiple reports. Before finalizing the incident list, cross-check every CRITICAL-severity finding in
   `apm_report.json`, `metrics_report.json`, `security_report.json`, and every `breakpoints[]` entry in
   `dependency_report.json` against the incidents produced so far. If a CRITICAL finding or a breakpoint is not
   represented in any incident's `evidence_sources`/`root_cause_finding`, do not silently drop it — instead
   attempt clustering by *service identity* alone (ignore the timestamp match for this one check, since the
   dependency graph or shared service name is independent, non-corruptible evidence of relatedness) before
   giving up and routing it to `unresolved_findings` with a `reason` explaining why it couldn't be clustered.
   `unresolved_findings` existing and being non-empty is normal and expected on messy data; a CRITICAL finding
   disappearing from the output entirely, with no incident and no unresolved_findings entry, is not

### Phase 2 — Root Cause Identification
1. Check `dependency_report.json` first — if a breakpoint was identified for this incident window, treat it as the primary root cause candidate
2. If no breakpoint available, fall back to identifying the earliest-occurring finding as the candidate root cause
3. Classify the candidate into a `root_cause_category`
4. Classify all later findings in the same cluster as downstream symptoms
5. Assign confidence: HIGH if sources >= `confidence_high_threshold`, MEDIUM if >= `confidence_medium_threshold`, else LOW

### Phase 3 — Severity & Blast Radius
1. Calculate blast radius = count of distinct affected services in the incident
2. Assign severity based on the highest severity finding in the cluster
3. Rank incidents by severity descending, then blast radius descending

### Phase 4 — Recommendation Generation
1. For each incident, generate one or more specific recommendations tied to the root cause finding
2. Assign priority per severity/confidence/trend
3. Rank all recommendations by priority, then blast radius
4. Truncate to `max_recommendations`

### Phase 5 — Write Output
1. Build summary statistics for both files
2. Write `root_cause.json` and `recommendations.json`

---

## Implementation Notes (pseudocode — MUST be followed structurally, not just in spirit)

This is the most consequential agent in the pipeline to get right — its two most valuable rules (prefer a
dependency breakpoint over time correlation; fall back to service-identity clustering when timestamps don't
align) are also the ones most likely to be skipped by a straightforward implementation, because both
require an explicit fallback branch rather than a single linear pass.

**1. Dependency-graph breakpoints MUST NOT be given a null/placeholder timestamp.** A breakpoint entry in
`dependency_report.json` does not carry its own `timestamp` field — but that does NOT mean this agent should
represent it as a finding with `timestamp: null`. A finding with `timestamp: null` can never satisfy a
time-window clustering check, which silently guarantees every breakpoint finding falls through to
`unresolved_findings` regardless of how obviously related it is. Instead, derive a timestamp for the
breakpoint finding from the incident window it's attached to:
```
for bp in dependency_report.breakpoints:
    # bp has no own timestamp — derive one instead of using None
    related_anomaly = anomaly_report.anomalies.find(service == bp.breakpoint_service)
    bp_timestamp = related_anomaly.timestamp if related_anomaly else incident_window_start_estimate
    add_finding(source="dependency_report.json", issue_type=bp.issue_type,
                service=bp.breakpoint_service, timestamp=bp_timestamp, ...)   # never timestamp=None
```

**2. Clustering MUST fall back to service-identity / dependency-graph connectivity, not only time proximity
— this is not optional polish, it is the primary mechanism by which breakpoints ever join an incident.**
```
def cluster(findings, dependency_graph):
    incidents = []
    for f in sorted(findings, by=timestamp):
        placed_incident = find_incident_within_time_window(incidents, f, correlation_window_minutes)
        if placed_incident is None:
            # TIME-BASED MATCH FAILED — do not give up yet. Try service-identity fallback:
            placed_incident = find_incident_by_service_or_graph_edge(incidents, f, dependency_graph)
            # this checks: does f.service equal or graph-connect (any hop) to any service
            # already in an existing incident, REGARDLESS of whether f.timestamp falls in that
            # incident's time window. This is what the "timestamp-robustness safeguard" requires.
        if placed_incident:
            placed_incident.findings.append(f)
        else:
            incidents.append(new_incident(f))
    return incidents
```
Concretely: if `checkout-consumer` has a `PIPELINE_BACKPRESSURE`-flavored incident already open, and a
dependency-report breakpoint finding for `checkout-consumer` (or for `order-service`/`payment-service`,
which are graph-connected to it) arrives with any timestamp, it MUST be merged into that same incident via
the service/graph fallback — not routed to `unresolved_findings`.

**3. `downstream_symptoms` MUST be deduplicated by resolved description text, not just assembled via a raw
list comprehension.**
```
downstream_symptoms = []
seen_descriptions = set()
for f in incident.findings:
    text = resolve_readable_text(f)          # per the existing MUST rule on non-empty resolved strings
    if text and text not in seen_descriptions:
        downstream_symptoms.append(text)
        seen_descriptions.add(text)
```

## Self-Test Cases (run against this project's own sample data before considering this agent done)

- `dependency_report.json` identifies `checkout-consumer` as a breakpoint with `downstream_impact:
  [order-service, payment-service]`. The corresponding incident in `root_cause.json` MUST have
  `dependency_breakpoint: "checkout-consumer"` (not null) and `affected_services` MUST include
  `order-service` and `payment-service` in addition to `checkout-consumer` — blast_radius MUST be >= 3,
  not 1.
- `unresolved_findings` MUST NOT contain any `dependency_report.json` breakpoint entry whose
  `breakpoint_service` is `checkout-consumer`, `order-service`, or `payment-service`, since all three are
  graph-connected and should cluster into the pipeline-backpressure incident via the service-identity
  fallback.
- No `downstream_symptoms` array may contain the same string twice.

---

## Output Specification

| Artifact | Description |
|---|---|
| `root_cause.json` | Correlated incidents with root cause category, confidence, dependency-graph breakpoint, downstream symptoms, evidence sources |
| `recommendations.json` | Ranked, actionable recommendations tied to specific incidents and evidence |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No incidents found | Findings too sparse or scattered in time | Widen `correlation_window_minutes` |
| Everything marked UNDETERMINED | `min_evidence_sources` too high for available data | Lower `min_evidence_sources` to 1 for sparse sample data |
| Root cause ignores dependency breakpoint | `dependency_report.json` missing or empty | Verify Dependency/Flow Analysis Agent ran before this agent |

---

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- Root-cause clustering MUST preserve parallel incidents. A dependency incident MUST NOT hide security, data-quality, or infrastructure incidents that occur in a different service/domain.
- Every CRITICAL finding from `security_report.json`, `metrics_report.json`, `apm_report.json`, `log_analysis.json`, or `anomaly_report.json` MUST appear in one of: `incidents[].evidence_sources`, `incidents[].downstream_symptoms`, or `unresolved_findings`.
- If a security finding is CRITICAL and not causally linked to the main dependency incident, create a separate `SECURITY_INCIDENT` or record a high-priority unresolved finding. Do not drop it.
- `affected_services` MUST include the primary service plus every downstream impacted service from the dependency report and every service with evidence assigned to that incident.
- `blast_radius` MUST equal the count of distinct `affected_services`.
- `summary.total_incidents` MUST equal `len(incidents)`.
- The summary field MUST be named exactly `total_incidents`; do not write `incident_count` as a substitute.
- Every incident MUST include `root_cause_category`. Do not replace it with a title, summary, or generic
  severity-only incident object.
- `unresolved_findings` and `all_incidents` MUST always be present, even when empty.
- Confidence MUST be derived from evidence strength. Do not upgrade MEDIUM dependency confidence to HIGH without additional corroboration.
- Recommendations MUST be generated for every P1/P2 incident and every unresolved CRITICAL finding.
- `recommendations.summary.total_recommendations` MUST equal `len(recommendations)`.

Reject the generated output if any upstream CRITICAL finding disappears from both incident evidence and unresolved findings.
Also reject it if `root_cause.json` uses `summary.incident_count` without `summary.total_incidents`, or if
incidents are missing `root_cause_category`.
