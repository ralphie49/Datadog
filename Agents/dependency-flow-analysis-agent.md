# Dependency/Flow Analysis Agent
**Version:** 1.0.0 | **Domain:** Datadog Observability Analysis
**Design credit:** originally proposed by teammate as part of her pipeline flow

---

## Purpose

Builds a service dependency graph from trace and metric data, and identifies the
breakpoint(s) — the point in the service call chain where a failure or degradation
actually originates and propagates outward. Sits between Anomaly Detection and Root
Cause, giving Root Cause a structural map of *how* services depend on each other,
instead of relying purely on time-based correlation.

**Outputs:** `dependency_report.json`

---

## 🔧 DEVELOPER CONFIGURATION

```yaml
dependency_flow_config:
  input_files:
    - "output/<dataset>/normalised_data.json"
    - "output/<dataset>/metrics_report.json"
    - "output/<dataset>/apm_report.json"
    - "output/<dataset>/anomaly_report.json"

  output_file: "output/<dataset>/dependency_report.json"

  settings:
    min_call_count_for_edge:   5     # Minimum observed calls between two services to count as a dependency edge.
                                      # ADAPTIVE: if total trace spans in the input is under 200, this agent MUST
                                      # scale the threshold down to max(1, min(5, floor(total_spans / 20))) instead
                                      # of using this value as-is — a fixed threshold of 5 silently produces an
                                      # empty graph on small or early-stage datasets. Report the effective
                                      # threshold actually used in the output summary.
    breakpoint_confidence_threshold: 0.6   # Minimum score (0-1) to declare a node the breakpoint
    max_hops_upstream:         5     # How many hops upstream to trace when isolating a breakpoint
    include_external_calls:    true  # Whether to include calls to third-party/external services
```

---

## Pre-requisites

- `output/<dataset>/normalised_data.json`, `output/<dataset>/metrics_report.json`, `output/<dataset>/apm_report.json`, and `output/<dataset>/anomaly_report.json` must exist
- Input must contain trace records with `trace_id` and `span_id` for call-chain reconstruction
- Output folder `output/<dataset>/` must be writable

---

## Dataset-to-Output Routing Contract

- `<dataset>` MUST already be resolved by the orchestrator or caller before this agent runs.
- This agent MUST read only the configured `input_files` and write only the configured `output_file`.
- Every `input_files` entry and `output_file` MUST be inside the same resolved `output/<dataset>/` folder.
- This agent MUST NOT derive a new output folder from services, dependency graph nodes, breakpoint services, dates, upstream filenames, or existing files in `output/`.
- If the input files do not all share the same dataset folder, or if the output path points elsewhere, stop before writing and report the mismatch.

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist).
  `analysis_period` MUST be a JSON object with exactly the keys `from` and `to` — never a bare array/list
- MUST reconstruct a service call graph from trace spans — parent/child relationships via `trace_id` and `span_id`
- MUST build a dependency edge between two services only if observed call count >= `min_call_count_for_edge`
- MUST identify, for each incident window flagged in `anomaly_report.json`, which node in the call graph is the most upstream failing point
- MUST distinguish between a breakpoint (originating failure) and a propagated symptom (downstream effect of the breakpoint)
- MUST assign a breakpoint confidence score (0-1) based on how consistently failures trace back to that node
- MUST, once a breakpoint candidate is identified, trace `downstream_impact` through the FULL connected chain —
  not just its immediate one-hop neighbor. If node A → B → C are all edges in `dependency_graph`, and the
  incident's affected services include both B and C, a breakpoint at A must list `downstream_impact: [B, C]`
  and `hops_to_furthest_symptom: 2`, not just `[B]`. Stopping at one hop is the most common way this agent
  under-reports cascading failures
- MUST flag cascading failure chains — where a single breakpoint's failure visibly propagates through 2+ downstream services
- MUST write the dependency graph and breakpoint findings to `dependency_report.json`

### MUST NOT
- MUST NOT declare a breakpoint below `breakpoint_confidence_threshold`
- MUST NOT treat every node with elevated latency as a breakpoint — only the most upstream node in the chain qualifies
- MUST NOT declare `BREAKPOINT_IDENTIFIED` for any entity that is not itself a node in `dependency_graph.nodes` (i.e.
  reconstructed from trace spans). Host-level infrastructure anomalies (CPU/memory/disk spikes on a `host-*`
  entity from `anomaly_report.json`) are not part of the service call graph and MUST NOT be forced into a
  breakpoint finding — a host is not a service, and treating it as one fabricates a call-chain relationship that
  was never observed in trace data. If a host anomaly has no corresponding service-graph node, omit it from
  `breakpoints` entirely rather than inventing a zero-impact entry for it
- MUST NOT declare a `BREAKPOINT_IDENTIFIED` finding with an empty `downstream_impact` — a breakpoint is by
  definition an origin whose failure *propagates*; a finding with zero downstream impact and zero hops is not a
  breakpoint, it is an isolated finding that belongs in the source domain report instead (e.g. metrics_report.json)
- MUST NOT emit more than one breakpoint entry for the same `breakpoint_service` with the same `downstream_impact`
  set within the same incident window, even if multiple raw anomaly records (e.g. several near-identical
  `CORRELATED_ANOMALY` entries in `anomaly_report.json`) reference that window. One underlying incident produces
  one breakpoint entry, referencing one representative `incident_id` — not one entry per contributing anomaly
  record. Before writing output, deduplicate `breakpoints[]` / `all_findings[]` by (`breakpoint_service`,
  sorted `downstream_impact`) and collapse duplicates into a single entry; if the upstream `anomaly_report.json`
  contains many near-duplicate anomaly records for what is clearly one real incident, that is itself evidence of
  an upstream deduplication defect worth noting, not a reason to produce many breakpoint entries here
- MUST NOT modify any upstream input files

---

## Flow Issue Types

| Issue Type | Description |
|---|---|
| `BREAKPOINT_IDENTIFIED` | A specific service identified as the origin of a cascading failure |
| `CASCADING_FAILURE` | Failure propagating through 2+ downstream dependent services |
| `CIRCULAR_DEPENDENCY` | Two or more services calling each other in a cycle |
| `ORPHANED_SERVICE` | Service with no observed dependency edges — possibly misconfigured tracing |
| `SINGLE_POINT_OF_FAILURE` | A service with high fan-in (many services depend on it) currently degraded |
| `EXTERNAL_DEPENDENCY_FAILURE` | Breakpoint traced to a third-party/external service call |

---

## Output Schema — `dependency_report.json`

```json
{
  "summary": {
    "total_services_mapped":  0,
    "total_edges":            0,
    "breakpoints_identified": 0,
    "cascading_failures":     0,
    "effective_min_call_count_for_edge": 5,
    "parent_span_id_available": true,
    "analysis_period": { "from": "", "to": "" }
  },
  "dependency_graph": {
    "nodes": ["checkout-consumer", "payment-service", "order-service"],
    "edges": [
      { "from": "order-service", "to": "payment-service", "call_count": 4200, "avg_latency_ms": 120 }
    ]
  },
  "breakpoints": [
    {
      "incident_id":        "anomaly_ref_001",
      "breakpoint_service": "checkout-consumer",
      "issue_type":         "BREAKPOINT_IDENTIFIED",
      "confidence":         0.82,
      "downstream_impact":  ["payment-service", "order-service"],
      "hops_to_furthest_symptom": 2,
      "description":        "checkout-consumer identified as origin; failure propagated to payment-service and order-service within 4 minutes"
    }
  ],
  "cascading_failures": [],
  "all_findings": []
}
```

---

## Execution Workflow

### Phase 0 — Load Input
1. Read `normalised_data.json` for raw trace spans
2. Read `metrics_report.json`, `apm_report.json`, `anomaly_report.json` for known issue windows

### Phase 1 — Build Dependency Graph
1. Group trace spans by `trace_id`
2. If `parent_span_id` is present on the spans, reconstruct true parent/child relationships from it
3. If `parent_span_id` is absent (per the ingestion summary's `parent_span_id_missing` tag), fall back to
   ordering spans within each `trace_id` by `timestamp` and treating consecutive spans as parent→child — set
   `parent_span_id_available: false` in the output summary and cap breakpoint confidence scores at 0.75 in this
   mode, since timestamp-order is an approximation, not verified call structure
4. For each parent→child service pair, count calls and average latency
5. Compute the effective edge threshold per the adaptive rule in the config, and create an edge only where call
   count >= that effective threshold
6. Optionally exclude external calls if `include_external_calls: false`

### Phase 2 — Map Anomalies onto the Graph
1. For each anomaly/incident window from `anomaly_report.json`, identify which services showed issues during that window
2. Locate those services within the dependency graph

### Phase 3 — Breakpoint Identification
1. For each anomaly/incident window mapped in Phase 2, take the set of graph nodes (services) that showed
   an issue in that window
2. If 2+ of those nodes are connected by edges in `dependency_graph` (in either direction, any number of hops
   up to `max_hops_upstream`), trace upstream along those edges to find the most upstream node in the connected
   set — that node is the breakpoint candidate. Do not require every affected service to be graph-connected;
   evaluate the connected subset even if one flagged service (e.g. a `host-*` infra anomaly) has no graph node
3. Compute confidence as a concrete score, not a subjective impression:
   `confidence = (edges_confirming_upstream_direction / total_edges_examined_in_the_trace) × source_multiplier`,
   where `source_multiplier` = 1.0 if `parent_span_id_available: true`, else 0.75 (per the capped-confidence
   rule above). `edges_confirming_upstream_direction` = edges whose observed call direction and timing are
   consistent with the candidate node being upstream of the other affected node(s) in this incident window.
   Round to 2 decimal places
4. Declare a breakpoint only if confidence >= `breakpoint_confidence_threshold`
5. Self-check before finalizing: if `dependency_graph.edges` is non-empty AND `anomaly_report.json` contains
   any `CORRELATED_ANOMALY` or multi-service incident whose services appear in `dependency_graph.nodes`, the
   agent MUST NOT write an empty `breakpoints` array without first explicitly walking through steps 1-4 for
   that incident and recording why it fell below threshold (e.g. in `all_findings`) — a graph with edges plus
   a known multi-service incident silently producing zero breakpoints is the most common failure mode of this
   agent and almost always indicates step 1-4 were skipped rather than that no breakpoint exists

### Phase 4 — Cascade & Structural Analysis
1. Flag CASCADING_FAILURE where a breakpoint's failure is observed in 2+ downstream services
2. Flag CIRCULAR_DEPENDENCY where call graph contains cycles
3. Flag SINGLE_POINT_OF_FAILURE where a degraded node has high fan-in

### Phase 5 — Write Output
1. Build summary statistics
2. Write `dependency_report.json`

---

## Regression Gates (must pass before this agent is considered done)
- If `anomaly_report.json` contains a multi-service incident and the dependency graph has at least one edge between those services, `dependency_report.json.summary.breakpoints_identified` must be at least 1.
- A breakpoint must be emitted only when it has non-empty `downstream_impact`; otherwise the agent should record the finding in the source report instead of fabricating a breakpoint.

## Output Specification

| Artifact | Description |
|---|---|
| `dependency_report.json` | Service dependency graph, identified breakpoints with confidence scores, cascading failure chains — consumed as additional evidence by the Root Cause Analysis Agent |

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No dependency graph built | Trace records missing `trace_id`/`span_id` | Verify trace ingestion captured full span hierarchy |
| No breakpoints identified | Confidence threshold too high, or anomalies too sparse | Lower `breakpoint_confidence_threshold` or verify anomaly_report.json has entries |
| Graph looks disconnected | `min_call_count_for_edge` too high for sample data | Lower threshold for small sample datasets |

---

## Version Notes

- This agent is version 1.0.0 and follows the current Datadog analysis contract.
- If a replay runner script such as `run_datadog_analysis.py` is generated, it MUST be written only inside the resolved output dataset folder for that input target and MUST NOT be created in the project root, the top-level `output/` folder, or any other dataset folder.
---

## LLM Output Contract

When this file is used as a prompt for Copilot, Claude, or another code generator, the generated implementation is not complete until it proves these checks in code:

- `dependency_graph.nodes` MUST include every service that appears in trace spans and participates in at least one retained edge.
- The top-level graph field MUST be named exactly `dependency_graph`. The generated output MUST NOT use
  `service_graph`, `graph`, or any other replacement field name.
- If `parent_span_id` is missing and timestamp fallback is used, set `parent_span_id_available: false` and cap confidence at `0.75`.
- The adaptive edge threshold MUST be computed before filtering edges and written to `summary.effective_min_call_count_for_edge`.
- `downstream_impact` MUST be computed by graph traversal from the breakpoint candidate through the full connected downstream chain, not only the first neighbor.
- `hops_to_furthest_symptom` MUST equal the longest graph distance from the breakpoint to any service in `downstream_impact`.
- If a breakpoint has 2 or more downstream impacted services, emit a `CASCADING_FAILURE` entry and increment `summary.cascading_failures`.
- Do not emit a breakpoint whose `breakpoint_service` is absent from `dependency_graph.nodes`.
- Each breakpoint MUST use the field name `breakpoint_service`; do not write only `service` and expect
  downstream agents to infer that it means the breakpoint.
- Do not emit a breakpoint with empty `downstream_impact`.
- Deduplicate breakpoints by `(breakpoint_service, sorted downstream_impact, incident window)`.
- `summary.breakpoints_identified` MUST equal `len(breakpoints)`.
- Every service named in `downstream_impact` MUST exist in `dependency_graph.nodes`.

Reject the generated output if the graph contains a chain A -> B -> C, the breakpoint is A, and only B is listed when C is also affected in the same incident window.
Also reject it if `dependency_report.json` has `service_graph` instead of `dependency_graph`, or
`breakpoints[].service` instead of `breakpoints[].breakpoint_service`.
