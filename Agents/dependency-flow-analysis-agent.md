# Dependency/Flow Analysis Agent
**Version:** 1.4.0 | **Domain:** Datadog Observability Analysis
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
    - "output/normalised_data.json"
    - "output/metrics_report.json"
    - "output/apm_report.json"
    - "output/anomaly_report.json"

  output_file: "output/dependency_report.json"

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

- `normalised_data.json`, `metrics_report.json`, `apm_report.json`, and `anomaly_report.json` must exist
- Input must contain trace records with `trace_id` and `span_id` for call-chain reconstruction
- Output folder `output/` must be writable

---

## CORE RULES

### MUST
- MUST populate `analysis_period.from` and `analysis_period.to` as the min and max `timestamp` values
  across every record this agent actually processed (never leave them null when input records exist)
- MUST reconstruct a service call graph from trace spans — parent/child relationships via `trace_id` and `span_id`
- MUST build a dependency edge between two services only if observed call count >= `min_call_count_for_edge`
- MUST identify, for each incident window flagged in `anomaly_report.json`, which node in the call graph is the most upstream failing point
- MUST distinguish between a breakpoint (originating failure) and a propagated symptom (downstream effect of the breakpoint)
- MUST assign a breakpoint confidence score (0-1) based on how consistently failures trace back to that node
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

## Version History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-07-03 | dependency-flow-analysis-agent | Initial release — service dependency graph construction, breakpoint identification, cascading failure detection |
| 1.1.0 | 2026-07-03 | dependency-flow-analysis-agent | Added timestamp-order fallback for graph construction when `parent_span_id` is unavailable, with capped confidence; made `min_call_count_for_edge` adaptive so small datasets no longer produce an empty graph |
| 1.2.0 | 2026-07-03 | dependency-flow-analysis-agent | Added MUST rule requiring analysis_period.from/to to be populated from actual record timestamps instead of left null |
| 1.3.0 | 2026-07-03 | dependency-flow-analysis-agent | Added MUST NOT rules preventing host-level infra anomalies from being fabricated into service-graph breakpoints they cannot structurally belong to, and preventing zero-downstream-impact 'breakpoints' |
| 1.4.0 | 2026-07-03 | dependency-flow-analysis-agent | Fixed observed failure mode where a populated graph plus a known multi-service correlated incident still produced zero breakpoints — replaced vague "score confidence based on consistency" with a concrete, reproducible confidence formula, and added a mandatory self-check before writing an empty breakpoints array |