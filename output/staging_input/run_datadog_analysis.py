"""Datadog Analyser Orchestrator pipeline.

Content-driven, dataset-name-agnostic implementation of all 11 phases described in
Agents/datadog-analyser-orchestrator.md and its 11 companion agent specs. No filenames,
service names, or numeric thresholds from any specific sample dataset are embedded here;
every value in the generated artifacts is computed from whatever records are actually
present in the input folder at runtime.
"""
import argparse
import csv
import io
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared constants (thresholds only -- never literal sample data)
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "CRITICAL": 4}
LEVEL_MAP = {
    "debug": "DEBUG", "trace": "DEBUG",
    "info": "INFO", "information": "INFO", "notice": "INFO", "ok": "INFO",
    "warn": "WARN", "warning": "WARN",
    "error": "ERROR", "err": "ERROR",
    "critical": "CRITICAL", "fatal": "CRITICAL", "emergency": "CRITICAL", "alert": "CRITICAL",
}
ALERT_PRIORITY_MAP = {"P1": "CRITICAL", "P2": "ERROR", "P3": "WARN", "P4": "INFO"}

PII_COLUMNS = ["email", "phone", "ssn", "dob", "address", "credit_card", "national_id", "password", "token", "secret"]
PII_VALUE_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}",
    "phone": r"\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
}
CREDENTIAL_PATTERNS = ["password=", "api_key=", "token=", "secret=", "Authorization:", "Bearer "]
PERMISSION_ESCALATION_KEYWORDS = ["sudo", "privilege", "escalat", "root access", "admin override"]
COMPLIANCE_FRAMEWORKS = ["GDPR", "HIPAA", "PCI-DSS"]

ERROR_TYPE_PATTERNS = [
    ("CHECKPOINT_FAILURE", [r"checkpoint.*(corrupt|missing|failed)", r"offset missing", r"recovery failed"]),
    ("DELTA_CONFLICT", [r"concurrent write conflict", r"transaction failed"]),
    ("OUT_OF_MEMORY", [r"heap space", r"oom killer", r"memory limit exceeded", r"out of memory"]),
    ("RESOURCE_EXHAUSTION", [r"cpu throttled", r"disk full", r"thread pool exhausted", r"no space left on device",
                             r"disk usage.*reached \d", r"pool (degraded|offline)", r"queue depth exceeded"]),
    ("AUTHENTICATION_FAILURE", [r"invalid credentials", r"token expired", r"unauthoris?ed", r"unauthoriz?ed"]),
    ("PERMISSION_DENIED", [r"access denied", r"forbidden", r"insufficient privileges"]),
    ("SCHEMA_MISMATCH", [r"column not found", r"type mismatch", r"schema evolution"]),
    ("NULL_POINTER", [r"nullpointerexception", r"null reference", r"undefined value"]),
    ("CONNECTION_FAILURE", [r"connection refused", r"network unreachable", r"socket timeout", r"connection to .* timed out"]),
    ("TIMEOUT", [r"timeout", r"timed out"]),
    ("APPLICATION_ERROR", [r"unhandled exception", r"stack overflow", r"assertion failed"]),
]

FILE_EXTENSIONS_SCANNED = {".json", ".csv", ".log", ".ndjson"}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def normalise_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v <= 0:
            return None
        if v >= 1e12:
            v = v / 1000.0
        try:
            dt = datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OSError):
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"
        except ValueError:
            if s.isdigit():
                return normalise_timestamp(float(s))
            return None
    return None


def derive_environment(fields, tags):
    env = fields.get("environment") or fields.get("env")
    if env:
        return env
    for t in tags or []:
        if isinstance(t, str) and t.lower().startswith("env:"):
            return t.split(":", 1)[1]
    return "unknown"


IP_RE = re.compile(r"\b(?:ip|client_ip|source_ip)=([\d.]+)")
USER_RE = re.compile(r"\buser=([\w.@-]+)")


def extract_ip_user(message):
    if not message:
        return None, None
    ip_m = IP_RE.search(message)
    user_m = USER_RE.search(message)
    return (ip_m.group(1) if ip_m else None), (user_m.group(1) if user_m else None)


def normalise_severity_from_level(level_text):
    if not level_text:
        return None
    return LEVEL_MAP.get(str(level_text).strip().lower())


def redact_text(text):
    """Redact PII/credential-looking substrings from free text. Shared by Security Audit
    and every other agent that copies raw log text into its own output (anomaly descriptions
    etc.), since redaction is each producing agent's own responsibility, not a downstream fixup."""
    if not text:
        return text
    redacted = text
    for pattern in PII_VALUE_PATTERNS.values():
        redacted = re.sub(pattern, "[REDACTED]", redacted)
    for cred in CREDENTIAL_PATTERNS:
        idx = redacted.find(cred)
        if idx != -1:
            redacted = redacted[:idx + len(cred)] + "[REDACTED]"
    return redacted


# ---------------------------------------------------------------------------
# Phase 0 -- Log Ingestion & Normaliser
# ---------------------------------------------------------------------------

def load_raw_file(path):
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        return "error", f"unreadable file: {e}"
    if len(raw_bytes.strip()) == 0:
        return "empty", None
    if path.suffix.lower() == ".csv":
        try:
            text = raw_bytes.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            return "csv", rows
        except Exception as e:
            return "error", f"CSV parse error: {e}"
    try:
        text = raw_bytes.decode("utf-8-sig")
        data = json.loads(text)
        return "json", data
    except Exception as e:
        return "error", f"JSON parse error: {e}"


def classify_flat_record_signature(sample):
    if not isinstance(sample, dict):
        return "unknown"
    keys = {k.lower() for k in sample.keys()}
    if "trace_id" in keys and "span_id" in keys:
        return "trace"
    if "monitor_name" in keys and ("priority" in keys or "status" in keys):
        return "alert"
    if "timestamp" in keys and ("message" in keys or "level" in keys or "severity" in keys):
        return "log"
    resource_fields = {"cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"}
    if "host" in keys and len(resource_fields & keys) >= 3:
        return "infrastructure"
    return "unknown"


def classify_attrs_signature(attrs, elem_type_hint=None):
    if not isinstance(attrs, dict):
        return "unknown"
    keys = {k.lower() for k in attrs.keys()}
    if ("trace_id" in keys and "span_id" in keys) or elem_type_hint == "span":
        return "trace"
    if "name" in keys and ("overall_state" in keys or "priority" in keys):
        return "alert"
    if "status" in keys and "timestamp" in keys and "message" in keys:
        return "log"
    return "unknown"


def build_log_record(record_id, fields, raw):
    message = fields.get("message", "") or ""
    tags = fields.get("tags") or []
    severity = normalise_severity_from_level(fields.get("level") or fields.get("status")) or "INFO"
    ip, user = extract_ip_user(message)
    return {
        "record_id": record_id,
        "source_type": "log",
        "severity": severity,
        "service": fields.get("service") or "unknown",
        "environment": derive_environment(fields, tags),
        "timestamp": normalise_timestamp(fields.get("timestamp")),
        "message": message,
        "tags": tags,
        "source_ip": fields.get("source_ip") or fields.get("client_ip") or ip,
        "user": fields.get("user") or user,
        "raw": raw,
    }


def build_trace_record(record_id, fields, raw, parent_span_id_missing_flag):
    status = (fields.get("status") or "").lower()
    severity = "ERROR" if status == "error" else "INFO"
    tags = fields.get("tags") or []
    parent_span_id = fields.get("parent_span_id")
    if parent_span_id is None:
        parent_span_id_missing_flag["missing"] = True
    return {
        "record_id": record_id,
        "source_type": "trace",
        "severity": severity,
        "service": fields.get("service") or "unknown",
        "environment": derive_environment(fields, tags),
        "timestamp": normalise_timestamp(fields.get("timestamp")),
        "message": f"{fields.get('operation', 'operation')} ({status or 'ok'})",
        "tags": tags,
        "source_ip": None,
        "user": None,
        "raw": {**raw, "trace_id": fields.get("trace_id"), "span_id": fields.get("span_id"),
                "parent_span_id": parent_span_id, "operation": fields.get("operation"),
                "duration_ms": fields.get("duration_ms"), "status": fields.get("status")},
    }


def build_alert_record(record_id, fields, raw):
    priority = (fields.get("priority") or "").upper()
    severity = ALERT_PRIORITY_MAP.get(priority, normalise_severity_from_level(fields.get("status")) or "WARN")
    tags = fields.get("tags") or []
    return {
        "record_id": record_id,
        "source_type": "alert",
        "severity": severity,
        "service": fields.get("service") or "unknown",
        "environment": derive_environment(fields, tags),
        "timestamp": normalise_timestamp(fields.get("triggered_at") or fields.get("timestamp")),
        "message": fields.get("message") or fields.get("monitor_name") or "",
        "tags": tags,
        "source_ip": None,
        "user": None,
        "raw": {**raw, "monitor_name": fields.get("monitor_name"), "status": fields.get("status"),
                "priority": fields.get("priority"), "triggered_at": fields.get("triggered_at")},
    }


def build_infrastructure_record(record_id, fields, raw):
    def to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    cpu = to_float(fields.get("cpu_pct"))
    mem = to_float(fields.get("memory_pct"))
    severity = "WARN" if (cpu is not None and cpu > 90) or (mem is not None and mem > 90) else "INFO"
    tags = fields.get("tags") or []
    return {
        "record_id": record_id,
        "source_type": "infrastructure",
        "severity": severity,
        "service": fields.get("host") or "unknown",
        "environment": derive_environment(fields, tags),
        "timestamp": normalise_timestamp(fields.get("timestamp")),
        "message": f"host={fields.get('host')} cpu={cpu} mem={mem}",
        "tags": tags,
        "source_ip": None,
        "user": None,
        "raw": {**raw, "host": fields.get("host"), "cpu_pct": cpu, "memory_pct": mem,
                "disk_pct": to_float(fields.get("disk_pct")), "network_in": to_float(fields.get("network_in")),
                "network_out": to_float(fields.get("network_out"))},
    }


def build_metric_record(record_id, fields, raw):
    tags = fields.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(";") if t.strip()]

    def to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Real-world exports name this column "metric_name" or just "metric" -- both are common Datadog
    # CSV export headers; treat them as aliases rather than assuming one exact literal name.
    metric_name = fields.get("metric_name") or fields.get("metric")

    return {
        "record_id": record_id,
        "source_type": "metric",
        "severity": "INFO",
        "service": fields.get("service") or fields.get("host") or "unknown",
        "environment": derive_environment(fields, tags),
        "timestamp": normalise_timestamp(fields.get("timestamp")),
        "message": f"{metric_name}={fields.get('value')}",
        "tags": tags,
        "source_ip": None,
        "user": None,
        "raw": {**raw, "metric_name": metric_name, "value": to_float(fields.get("value")),
                "host": fields.get("host"), "service": fields.get("service")},
    }


def ingest_folder(input_root: Path):
    classified_files = []
    skipped_files = []
    all_records = []
    seq = defaultdict(int)
    parent_span_flag = {"missing": False}

    def next_id(source_type):
        seq[source_type] += 1
        return f"{source_type}_{seq[source_type]:06d}"

    for path in sorted(input_root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in FILE_EXTENSIONS_SCANNED:
            continue
        kind, payload = load_raw_file(path)

        if kind == "empty":
            skipped_files.append({"path": path.name, "reason": "file is empty (0 bytes) -- not parseable JSON/CSV",
                                   "status": "skipped_unreadable"})
            continue
        if kind == "error":
            skipped_files.append({"path": path.name, "reason": payload, "status": "skipped_unreadable"})
            continue

        file_records = []

        if kind == "csv":
            header_keys = {k.lower() for k in (payload[0].keys() if payload else [])}
            if "value" in header_keys or "metric_name" in header_keys:
                for row in payload:
                    file_records.append(build_metric_record(next_id("metric"), row, dict(row)))
                classified_files.append({"path": path.name, "source_type": "metric", "record_count": len(file_records)})
                all_records.extend(file_records)
            else:
                skipped_files.append({"path": path.name, "reason": "CSV header did not match metric signature",
                                       "status": "skipped_unknown"})
            continue

        data = payload

        # --- Nested Datadog API/UI export envelope: {"data": [...], "meta": {...}} ---
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            unknown_elems = 0
            per_type_records = defaultdict(list)
            for elem in data["data"]:
                if not isinstance(elem, dict):
                    unknown_elems += 1
                    continue
                attrs = elem.get("attributes", elem) if isinstance(elem.get("attributes", elem), dict) else elem
                elem_type_hint = "span" if elem.get("type") == "span" else None
                sig = classify_attrs_signature(attrs, elem_type_hint)
                nested_custom = attrs.get("attributes") if isinstance(attrs.get("attributes"), dict) else {}
                raw = {"id": elem.get("id"), "type": elem.get("type"), **nested_custom}
                if sig == "log":
                    rec = build_log_record(next_id("log"), {
                        "timestamp": attrs.get("timestamp"), "level": attrs.get("status"),
                        "service": attrs.get("service"), "message": attrs.get("message"),
                        "tags": attrs.get("tags"), "environment": attrs.get("environment"),
                        "source_ip": attrs.get("source_ip"), "user": attrs.get("user"),
                    }, raw)
                    per_type_records["log"].append(rec)
                elif sig == "trace":
                    rec = build_trace_record(next_id("trace"), {
                        "trace_id": attrs.get("trace_id"), "span_id": attrs.get("span_id"),
                        "parent_span_id": attrs.get("parent_span_id"), "service": attrs.get("service"),
                        "operation": attrs.get("operation") or attrs.get("resource"),
                        "duration_ms": attrs.get("duration") or attrs.get("duration_ms"),
                        "status": attrs.get("status"), "timestamp": attrs.get("timestamp"),
                        "tags": attrs.get("tags"), "environment": attrs.get("environment"),
                    }, raw, parent_span_flag)
                    per_type_records["trace"].append(rec)
                elif sig == "alert":
                    rec = build_alert_record(next_id("alert"), {
                        "monitor_name": attrs.get("name"), "status": attrs.get("overall_state"),
                        "priority": attrs.get("priority"), "triggered_at": attrs.get("triggered_at") or attrs.get("timestamp"),
                        "service": attrs.get("service"), "message": attrs.get("message"),
                        "tags": attrs.get("tags"), "environment": attrs.get("environment"),
                    }, raw)
                    per_type_records["alert"].append(rec)
                else:
                    unknown_elems += 1
            for stype, recs in per_type_records.items():
                classified_files.append({"path": path.name, "source_type": stype, "record_count": len(recs)})
                all_records.extend(recs)
            if unknown_elems:
                skipped_files.append({"path": path.name, "reason": f"{unknown_elems} element(s) inside data[] matched no known signature",
                                       "status": "partially_skipped_unknown_elements"})
            if not per_type_records and not unknown_elems:
                skipped_files.append({"path": path.name, "reason": "data[] was empty", "status": "empty_data_array"})
            continue

        # --- Real Datadog metrics-query time-series response: {"series": [{"pointlist": [...]}]} ---
        if isinstance(data, dict) and isinstance(data.get("series"), list):
            for series_obj in data["series"]:
                metric_name = series_obj.get("metric") or series_obj.get("display_name") or series_obj.get("expression") or "unknown_metric"
                scope = series_obj.get("scope", "") or ""
                scope_parts = [p.strip() for p in scope.split(",") if p.strip()]
                host = next((p.split(":", 1)[1] for p in scope_parts if p.startswith("host:")), None)
                service = next((p.split(":", 1)[1] for p in scope_parts if p.startswith("service:")), None)
                for point in series_obj.get("pointlist", []) or []:
                    if not point or len(point) < 2:
                        continue
                    ts_raw, value = point[0], point[1]
                    file_records.append(build_metric_record(next_id("metric"), {
                        "metric_name": metric_name, "value": value, "host": host, "service": service,
                        "timestamp": ts_raw, "tags": scope_parts,
                    }, {"scope": scope, "metric": metric_name}))
            classified_files.append({"path": path.name, "source_type": "metric", "record_count": len(file_records)})
            all_records.extend(file_records)
            continue

        # --- Flat JSON array (project sample schema) ---
        if isinstance(data, list):
            if not data:
                skipped_files.append({"path": path.name, "reason": "empty JSON array", "status": "empty_array"})
                continue
            sig = classify_flat_record_signature(data[0])
            if sig == "unknown":
                skipped_files.append({"path": path.name, "reason": "records matched no known signature", "status": "skipped_unknown"})
                continue
            for row in data:
                raw = dict(row)
                if sig == "log":
                    file_records.append(build_log_record(next_id("log"), row, raw))
                elif sig == "trace":
                    file_records.append(build_trace_record(next_id("trace"), row, raw, parent_span_flag))
                elif sig == "alert":
                    file_records.append(build_alert_record(next_id("alert"), row, raw))
                elif sig == "infrastructure":
                    file_records.append(build_infrastructure_record(next_id("infrastructure"), row, raw))
            classified_files.append({"path": path.name, "source_type": sig, "record_count": len(file_records)})
            all_records.extend(file_records)
            continue

        skipped_files.append({"path": path.name, "reason": "unrecognized top-level JSON shape", "status": "skipped_unknown"})

    if parent_span_flag["missing"]:
        for cf in classified_files:
            if cf["source_type"] == "trace":
                cf.setdefault("tags", []).append("parent_span_id_missing")

    all_records.sort(key=lambda r: (r["timestamp"] or "", r["record_id"]))

    record_counts = {"log": 0, "metric": 0, "trace": 0, "alert": 0, "infrastructure": 0}
    for r in all_records:
        record_counts[r["source_type"]] += 1
    record_counts["total"] = len(all_records)

    timestamps = [r["timestamp"] for r in all_records if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    return {
        "dataset_name": None,  # filled by caller
        "input_root": str(input_root),
        "analysis_period": analysis_period,
        "record_counts": record_counts,
        "classified_files": classified_files,
        "skipped_files": skipped_files,
        "records": all_records,
    }, parent_span_flag["missing"]


# ---------------------------------------------------------------------------
# Phase 1 -- Error & Data Quality Agent
# ---------------------------------------------------------------------------

DQ_METRICS_RE = re.compile(
    r"DQ_METRICS\s+batch_id=(?P<batch_id>\S+)\s+pipeline=(?P<pipeline>\S+)\s+total=(?P<total>\d+)\s+"
    r"passed=(?P<passed>\d+)\s+failed=(?P<failed>\d+)\s+rejection_rate_pct=(?P<rate>[\d.]+)")
DQ_ALERT_REASON_RE = re.compile(r"rejection_reason=(?P<rule>\w+):(?P<col>[\w.]+)(?::[\w.]+)?\s+count=(?P<count>\d+)")


def classify_error_type(message):
    msg = (message or "").lower()
    for error_type, patterns in ERROR_TYPE_PATTERNS:
        for pat in patterns:
            if re.search(pat, msg):
                return error_type
    return "UNKNOWN"


def run_error_dq_agent(normalised, cfg):
    error_threshold = cfg.get("error_threshold", "ERROR")
    recurring_threshold = cfg.get("recurring_threshold", 3)
    top_errors_limit = cfg.get("top_errors_limit", 10)
    thresholds = cfg.get("dq_thresholds", {})

    log_records = [r for r in normalised["records"] if r["source_type"] == "log"]
    threshold_level = SEVERITY_ORDER.get(error_threshold, 3)

    error_records = [r for r in log_records if SEVERITY_ORDER.get(r["severity"], 0) >= threshold_level]

    groups = defaultdict(list)
    for r in error_records:
        error_type = classify_error_type(r["message"])
        key = (error_type, r["message"], r["service"])
        groups[key].append(r)

    all_errors = []
    for (error_type, message, service), recs in groups.items():
        recs_sorted = sorted(recs, key=lambda r: r["timestamp"] or "")
        for rec in recs_sorted:
            all_errors.append({
                "error_type": error_type, "message": redact_text(message), "service": service,
                "severity": rec["severity"], "timestamp": rec["timestamp"], "record_id": rec["record_id"],
            })

    top_errors = []
    for rank, ((error_type, message, service), recs) in enumerate(
            sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_errors_limit], start=1):
        recs_sorted = sorted(recs, key=lambda r: r["timestamp"] or "")
        top_errors.append({
            "rank": rank, "error_type": error_type, "message": redact_text(message), "service": service,
            "severity": recs_sorted[0]["severity"], "frequency": len(recs),
            "is_recurring": len(recs) > recurring_threshold,
            "first_seen": recs_sorted[0]["timestamp"], "last_seen": recs_sorted[-1]["timestamp"],
        })

    recurring_errors = sum(1 for recs in groups.values() if len(recs) > recurring_threshold)
    affected_services = sorted({r["service"] for r in error_records})

    rejection_rates = []
    for r in log_records:
        m = DQ_METRICS_RE.search(r["message"] or "")
        if not m:
            continue
        total, passed, failed = int(m["total"]), int(m["passed"]), int(m["failed"])
        computed_pct = round((failed / total) * 100, 2) if total else 0.0
        logged_pct = float(m["rate"])
        verdict = "CRITICAL" if computed_pct > thresholds.get("rejection_rate_critical_pct", 25) else (
            "WARN" if computed_pct > thresholds.get("rejection_rate_warn_pct", 10) else "OK")
        entry = {
            "batch_id": m["batch_id"], "pipeline": m["pipeline"], "total": total, "passed": passed,
            "failed": failed, "rejection_pct": computed_pct, "verdict": verdict, "timestamp": r["timestamp"],
        }
        if abs(computed_pct - logged_pct) > 0.1:
            entry["rejection_pct_warning"] = f"logged rejection_rate_pct={logged_pct} disagreed with computed value; computed value used"
        rejection_rates.append(entry)

    column_counts = defaultdict(int)
    column_rule = {}
    dq_alert_messages = defaultdict(int)
    for r in log_records:
        if "DQ_ALERT" not in (r["message"] or ""):
            continue
        dq_alert_messages[r["message"]] += 1
        m = DQ_ALERT_REASON_RE.search(r["message"])
        if m:
            column_counts[m["col"]] += int(m["count"])
            column_rule[m["col"]] = m["rule"]

    worst_columns = [
        {"column": col, "rejection_count": count, "rule_type": column_rule.get(col, "UNKNOWN")}
        for col, count in sorted(column_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    ]

    dq_alert_freq_warn = thresholds.get("dq_alert_frequency_warn", 5)
    dq_alerts = [
        {"message": redact_text(msg), "count": count, "recurring": count > dq_alert_freq_warn}
        for msg, count in dq_alert_messages.items()
    ]

    dq_trends = []
    rr_sorted = sorted(rejection_rates, key=lambda e: e["timestamp"] or "")
    if len(rr_sorted) >= 3:
        window = rr_sorted[-3:]
        if all(window[i]["rejection_pct"] < window[i + 1]["rejection_pct"] for i in range(len(window) - 1)):
            dq_trends.append({"trend": "REJECTION_RATE_WORSENING", "pipeline": window[-1]["pipeline"],
                               "detail": f"rejection rate rose across last 3 batches to {window[-1]['rejection_pct']}%"})

    all_dq_issues = []
    for rr in rejection_rates:
        if rr["verdict"] != "OK":
            all_dq_issues.append({"issue_type": "HIGH_REJECTION_RATE", "pipeline": rr["pipeline"],
                                   "batch_id": rr["batch_id"], "severity": rr["verdict"], "timestamp": rr["timestamp"]})
    for alert in dq_alerts:
        if alert["recurring"]:
            all_dq_issues.append({"issue_type": "RECURRING_DQ_ALERT", "message": alert["message"],
                                   "count": alert["count"], "severity": "WARN"})
    for trend in dq_trends:
        all_dq_issues.append({"issue_type": trend["trend"], "pipeline": trend["pipeline"], "severity": "WARN",
                               "detail": trend["detail"]})

    total_critical = sum(1 for r in log_records if r["severity"] == "CRITICAL")
    total_errors = sum(1 for r in log_records if r["severity"] in ("ERROR", "CRITICAL"))
    total_warnings = sum(1 for r in log_records if r["severity"] == "WARN")

    timestamps = [r["timestamp"] for r in log_records if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    rejection_pcts = [rr["rejection_pct"] for rr in rejection_rates]

    return {
        "summary": {
            "total_errors": total_errors, "total_warnings": total_warnings, "total_critical": total_critical,
            "recurring_errors": recurring_errors, "affected_services": affected_services,
            "total_batches_analysed": len(rejection_rates),
            "batches_with_dq_issues": sum(1 for rr in rejection_rates if rr["verdict"] != "OK"),
            "avg_rejection_rate_pct": round(sum(rejection_pcts) / len(rejection_pcts), 2) if rejection_pcts else 0.0,
            "max_rejection_rate_pct": max(rejection_pcts) if rejection_pcts else 0.0,
            "total_quarantine_records": 0, "total_dead_letter_records": 0,
            "analysis_period": analysis_period,
        },
        "top_errors": top_errors,
        "rejection_rates": rejection_rates,
        "worst_columns": worst_columns,
        "dq_alerts": dq_alerts,
        "dq_trends": dq_trends,
        "all_errors": all_errors,
        "all_dq_issues": all_dq_issues,
    }


# ---------------------------------------------------------------------------
# Phase 2 -- Performance & Infrastructure Health Agent
# ---------------------------------------------------------------------------

def percentile(values, pct):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


METRIC_NAME_ROLE = [
    ("cpu", "cpu_pct"), ("memory", "memory_pct"), ("mem", "memory_pct"), ("disk", "disk_pct"),
    ("network_in", "network_in"), ("network_out", "network_out"), ("network", "network_in"),
    ("throughput", "throughput_rps"), ("rps", "throughput_rps"),
    ("kafka", "kafka_consumer_lag"), ("lag", "kafka_consumer_lag"),
]


def metric_role(metric_name):
    name = (metric_name or "").lower()
    for token, role in METRIC_NAME_ROLE:
        if token in name:
            return role
    return None


def run_performance_infra_agent(normalised, cfg):
    perf = cfg.get("performance_thresholds", {})
    infra = cfg.get("infra_thresholds", {})

    all_records = normalised["records"]
    trace_records = [r for r in all_records if r["source_type"] == "trace"]
    infra_records = [r for r in all_records if r["source_type"] == "infrastructure"]
    metric_records = [r for r in all_records if r["source_type"] == "metric"]

    timestamps = [r["timestamp"] for r in all_records if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    by_service = defaultdict(list)
    for r in trace_records:
        by_service[r["service"]].append(r["raw"].get("duration_ms"))

    latency_by_service = []
    for service, durations in by_service.items():
        durations = [d for d in durations if d is not None]
        if not durations:
            continue
        avg_ms = round(sum(durations) / len(durations), 1)
        p95_ms = round(percentile(durations, 95), 1)
        p99_ms = round(percentile(durations, 99), 1)
        verdict = "CRITICAL" if p99_ms >= perf.get("latency_critical_ms", 1000) else (
            "WARN" if p99_ms >= perf.get("latency_warn_ms", 500) else "OK")
        latency_by_service.append({"service": service, "avg_ms": avg_ms, "p95_ms": p95_ms, "p99_ms": p99_ms, "verdict": verdict})

    slowest_traces = sorted(
        [r for r in trace_records if r["raw"].get("duration_ms") is not None],
        key=lambda r: r["raw"]["duration_ms"], reverse=True)[:5]
    slowest_traces_out = [{
        "service": r["service"], "operation": r["raw"].get("operation"), "duration_ms": r["raw"]["duration_ms"],
        "timestamp": r["timestamp"], "trace_id": r["raw"].get("trace_id"),
    } for r in slowest_traces]

    error_rate_by_service = defaultdict(lambda: [0, 0, None])
    for r in trace_records:
        st = error_rate_by_service[r["service"]]
        st[1] += 1
        if r["raw"].get("status") == "error":
            st[0] += 1
            if r["timestamp"] and (st[2] is None or r["timestamp"] < st[2]):
                st[2] = r["timestamp"]

    all_issues = []
    for service, (errs, total, first_error_ts) in error_rate_by_service.items():
        if not total:
            continue
        rate = round(errs / total * 100, 1)
        if rate >= perf.get("error_rate_critical_pct", 15):
            all_issues.append({"service": service, "issue_type": "HIGH_ERROR_RATE", "verdict": "CRITICAL",
                                "timestamp": first_error_ts, "description": f"{service} error rate {rate}% across {total} traces"})
        elif rate >= perf.get("error_rate_warn_pct", 5):
            all_issues.append({"service": service, "issue_type": "HIGH_ERROR_RATE", "verdict": "WARN",
                                "timestamp": first_error_ts, "description": f"{service} error rate {rate}% across {total} traces"})

    throughput = []
    if timestamps:
        mid = timestamps[len(timestamps) // 2]
        throughput_metric_records = [r for r in metric_records if metric_role(r["raw"].get("metric_name")) == "throughput_rps"]
        by_svc_baseline = defaultdict(list)
        by_svc_current = defaultdict(list)
        for r in throughput_metric_records:
            bucket = by_svc_baseline if (r["timestamp"] or "") < mid else by_svc_current
            bucket[r["service"]].append((r["raw"].get("value"), r["timestamp"]))
        for service in by_svc_baseline:
            baseline_vals = [v for v, _ in by_svc_baseline[service] if v is not None]
            current_entries = [(v, ts) for v, ts in by_svc_current.get(service, []) if v is not None]
            if not baseline_vals or not current_entries:
                continue
            current_vals = [v for v, _ in current_entries]
            baseline_avg = sum(baseline_vals) / len(baseline_vals)
            current_avg = sum(current_vals) / len(current_vals)
            if baseline_avg > 0:
                drop_pct = round((1 - current_avg / baseline_avg) * 100, 1)
                if drop_pct > perf.get("throughput_drop_pct", 30):
                    first_current_ts = min((ts for _, ts in current_entries if ts), default=None)
                    throughput.append({"service": service, "baseline_rps": round(baseline_avg, 1),
                                        "current_rps": round(current_avg, 1), "drop_pct": drop_pct, "verdict": "WARN"})
                    all_issues.append({"service": service, "issue_type": "THROUGHPUT_DROP", "verdict": "WARN",
                                        "timestamp": first_current_ts, "description": f"{service} throughput dropped {drop_pct}% vs baseline"})

    host_resource = defaultdict(lambda: defaultdict(list))
    for r in infra_records:
        raw = r["raw"]
        for field in ("cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"):
            if raw.get(field) is not None:
                host_resource[r["service"]][field].append((raw[field], r["timestamp"]))
    for r in metric_records:
        role = metric_role(r["raw"].get("metric_name"))
        if role in ("cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"):
            host = r["raw"].get("host") or r["service"]
            val = r["raw"].get("value")
            if val is not None:
                host_resource[host][role].append((val, r["timestamp"]))

    def first_breach_ts(entries, threshold):
        candidates = sorted((ts for v, ts in entries if ts and v is not None and v >= threshold))
        return candidates[0] if candidates else None

    hosts = []
    host_issue_timestamps = defaultdict(dict)
    for host, fields in host_resource.items():
        worst = {f: (round(max(v for v, _ in entries), 2) if entries else None) for f, entries in fields.items()}
        issues = []
        breach_ts = {}
        if worst.get("cpu_pct") is not None and worst["cpu_pct"] >= infra.get("cpu_warn_pct", 75):
            issues.append("HIGH_CPU")
            breach_ts["HIGH_CPU"] = first_breach_ts(fields["cpu_pct"], infra.get("cpu_warn_pct", 75))
        if worst.get("memory_pct") is not None and worst["memory_pct"] >= infra.get("memory_warn_pct", 75):
            issues.append("HIGH_MEMORY")
            breach_ts["HIGH_MEMORY"] = first_breach_ts(fields["memory_pct"], infra.get("memory_warn_pct", 75))
        if worst.get("disk_pct") is not None and worst["disk_pct"] >= infra.get("disk_warn_pct", 80):
            issues.append("HIGH_DISK")
            breach_ts["HIGH_DISK"] = first_breach_ts(fields["disk_pct"], infra.get("disk_warn_pct", 80))

        net_in_map = dict((ts, v) for v, ts in fields.get("network_in", []) if ts)
        net_out_map = dict((ts, v) for v, ts in fields.get("network_out", []) if ts)
        all_net_ts = sorted(set(net_in_map) | set(net_out_map))
        net_total = (worst.get("network_in") or 0) + (worst.get("network_out") or 0)
        net_saturation_ts = None
        for ts in all_net_ts:
            if (net_in_map.get(ts) or 0) + (net_out_map.get(ts) or 0) >= infra.get("network_saturation_mbps", 900):
                net_saturation_ts = ts
                break
        if net_total >= infra.get("network_saturation_mbps", 900):
            issues.append("NETWORK_SATURATION")
            breach_ts["NETWORK_SATURATION"] = net_saturation_ts

        critical_count = 0
        if worst.get("cpu_pct") is not None and worst["cpu_pct"] >= infra.get("cpu_critical_pct", 90):
            critical_count += 1
        if worst.get("memory_pct") is not None and worst["memory_pct"] >= infra.get("memory_critical_pct", 90):
            critical_count += 1
        if worst.get("disk_pct") is not None and worst["disk_pct"] >= infra.get("disk_critical_pct", 95):
            critical_count += 1
        if net_total >= infra.get("network_saturation_mbps", 900):
            critical_count += 1
        if critical_count >= 3:
            issues.append("RESOURCE_EXHAUSTION")
            contributing = [breach_ts.get(t) for t in ("HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK", "NETWORK_SATURATION") if breach_ts.get(t)]
            breach_ts["RESOURCE_EXHAUSTION"] = max(contributing) if contributing else None

        if not issues:
            verdict = "OK"
        elif any((worst.get("cpu_pct") or 0) >= infra.get("cpu_critical_pct", 90) or
                 (worst.get("memory_pct") or 0) >= infra.get("memory_critical_pct", 90) or
                 (worst.get("disk_pct") or 0) >= infra.get("disk_critical_pct", 95) or
                 net_total >= infra.get("network_saturation_mbps", 900) for _ in [0]):
            verdict = "CRITICAL"
        else:
            verdict = "WARN"

        penalty = len(issues) * 15
        health_score = max(0, 100 - penalty)
        host_issue_timestamps[host] = breach_ts
        hosts.append({
            "host": host, "cpu_pct": worst.get("cpu_pct"), "memory_pct": worst.get("memory_pct"),
            "disk_pct": worst.get("disk_pct"), "network_mbps": net_total if net_total else None,
            "health_score": health_score, "verdict": verdict, "issues": issues,
        })

    downtime_issues = []
    for host in list(host_resource.keys()):
        host_timestamps = sorted(
            r["timestamp"] for r in infra_records if r["service"] == host and r["timestamp"]
        ) + sorted(
            r["timestamp"] for r in metric_records
            if (r["raw"].get("host") or r["service"]) == host and r["timestamp"]
        )
        host_timestamps = sorted(set(host_timestamps))
        gap_warn_min = infra.get("host_downtime_warn_min", 5)
        for i in range(len(host_timestamps) - 1):
            t1 = datetime.fromisoformat(host_timestamps[i].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(host_timestamps[i + 1].replace("Z", "+00:00"))
            gap_min = (t2 - t1).total_seconds() / 60
            if gap_min > gap_warn_min and len(host_timestamps) >= 3:
                downtime_issues.append({"host": host, "from": host_timestamps[i], "to": host_timestamps[i + 1],
                                         "gap_minutes": round(gap_min, 1)})

    down_hosts = {d["host"] for d in downtime_issues}
    first_gap_ts = {}
    for d in sorted(downtime_issues, key=lambda d: d["from"]):
        first_gap_ts.setdefault(d["host"], d["from"])
    for h in hosts:
        if h["host"] in down_hosts:
            if "HOST_DOWN" not in h["issues"]:
                h["issues"].append("HOST_DOWN")
            host_issue_timestamps[h["host"]]["HOST_DOWN"] = first_gap_ts.get(h["host"])
            if h["verdict"] == "OK":
                h["verdict"] = "WARN"

    verdict_rank = {"CRITICAL": 0, "WARN": 1, "OK": 2}
    hosts.sort(key=lambda h: (verdict_rank.get(h["verdict"], 3), h["health_score"], h["host"]))

    storage_issues = []
    for r in normalised["records"]:
        if r["source_type"] != "log":
            continue
        msg = (r["message"] or "").lower()
        if "vacuum" in msg and ("overdue" in msg or "not run" in msg):
            storage_issues.append({"issue_type": "VACUUM_OVERDUE", "target": r["service"], "detail": r["message"],
                                    "timestamp": r["timestamp"]})
        elif "small files" in msg:
            storage_issues.append({"issue_type": "SMALL_FILES_EXCESS", "target": r["service"], "detail": r["message"],
                                    "timestamp": r["timestamp"]})
        elif "write conflict" in msg:
            storage_issues.append({"issue_type": "WRITE_CONFLICT", "target": r["service"], "detail": r["message"],
                                    "timestamp": r["timestamp"]})

    for h in hosts:
        for issue_type in h["issues"]:
            all_issues.append({"service": h["host"], "issue_type": issue_type, "verdict": h["verdict"],
                                "timestamp": host_issue_timestamps.get(h["host"], {}).get(issue_type),
                                "description": f"host {h['host']} flagged {issue_type} (verdict {h['verdict']})"})

    first_latency_breach_ts = {}
    for r in trace_records:
        d = r["raw"].get("duration_ms")
        if d is not None and d >= perf.get("latency_warn_ms", 500) and r["timestamp"]:
            svc = r["service"]
            if svc not in first_latency_breach_ts or r["timestamp"] < first_latency_breach_ts[svc]:
                first_latency_breach_ts[svc] = r["timestamp"]

    for row in latency_by_service:
        if row["verdict"] != "OK":
            all_issues.append({"service": row["service"], "issue_type": "HIGH_LATENCY", "verdict": row["verdict"],
                                "timestamp": first_latency_breach_ts.get(row["service"]),
                                "description": f"{row['service']} p99 latency {row['p99_ms']}ms"})

    network = [{"host": h["host"], "network_mbps": h["network_mbps"]} for h in hosts if
               h["network_mbps"] and h["network_mbps"] >= infra.get("network_saturation_mbps", 900)]

    critical_issues = sum(1 for i in all_issues if i["verdict"] == "CRITICAL")
    warn_issues = sum(1 for i in all_issues if i["verdict"] == "WARN")

    return {
        "summary": {
            "total_services_analysed": len(latency_by_service) or len({r["service"] for r in trace_records}),
            "total_hosts_analysed": len(hosts),
            "services_with_issues": len({i["service"] for i in all_issues if i["issue_type"] in
                                          ("HIGH_LATENCY", "HIGH_ERROR_RATE", "THROUGHPUT_DROP")}),
            "hosts_with_issues": len({i["service"] for i in all_issues if i["issue_type"] not in
                                       ("HIGH_LATENCY", "HIGH_ERROR_RATE", "THROUGHPUT_DROP")}),
            "critical_issues": critical_issues, "warn_issues": warn_issues,
            "hosts_down": len(down_hosts),
            "analysis_period": analysis_period,
        },
        "latency": {"slowest_traces": slowest_traces_out, "by_service": latency_by_service},
        "throughput": throughput,
        "hosts": hosts,
        "storage_issues": storage_issues,
        "network": network,
        "downtime_issues": downtime_issues,
        "all_issues": all_issues,
    }


# ---------------------------------------------------------------------------
# Phase 3 -- Pipeline Health Monitor Agent
# ---------------------------------------------------------------------------

TOPIC_RE = re.compile(r"topic[= ]([\w.-]+)")


def run_pipeline_health_agent(normalised, cfg):
    thresholds = cfg.get("thresholds", {})
    all_records = normalised["records"]
    log_and_alert = [r for r in all_records if r["source_type"] in ("log", "alert")]

    timestamps = [r["timestamp"] for r in all_records if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    kafka_lag_metrics = [r for r in all_records if r["source_type"] == "metric" and metric_role(r["raw"].get("metric_name")) == "kafka_consumer_lag"]

    topics = []
    pipeline_names = set()
    for r in kafka_lag_metrics:
        consumer_group = r["raw"].get("service") or r["service"]
        lag = r["raw"].get("value")
        topic = None
        if r["timestamp"]:
            rec_dt = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
            for other in log_and_alert:
                if other["service"] != consumer_group or not other["timestamp"]:
                    continue
                other_dt = datetime.fromisoformat(other["timestamp"].replace("Z", "+00:00"))
                if abs((rec_dt - other_dt).total_seconds()) < 120:
                    m = TOPIC_RE.search(other["message"] or "")
                    if m:
                        topic = m.group(1).rstrip(",")
                        break
        verdict = "CRITICAL" if lag is not None and lag > thresholds.get("kafka_lag_critical", 100000) else (
            "WARN" if lag is not None and lag > thresholds.get("kafka_lag_warn", 10000) else "OK")
        issue_type = "KAFKA_LAG_CRITICAL" if verdict == "CRITICAL" else ("KAFKA_LAG_HIGH" if verdict == "WARN" else None)
        pipeline_names.add(topic or consumer_group)
        entry = {"topic": topic, "consumer_group": consumer_group, "lag": lag, "verdict": verdict,
                  "timestamp": r["timestamp"]}
        if issue_type:
            entry["issue_type"] = issue_type
        topics.append(entry)

    checkpoints = []
    for r in log_and_alert:
        msg = (r["message"] or "").lower()
        if "checkpoint" not in msg and "offset" not in msg:
            continue
        env = r["environment"]
        if "corrupt" in msg:
            severity = "CRITICAL" if env == "prod" else "WARN"
            checkpoints.append({"issue_type": "CHECKPOINT_CORRUPT", "service": r["service"], "severity": severity,
                                 "timestamp": r["timestamp"], "detail": r["message"]})
            pipeline_names.add(r["service"])
        elif "offset missing" in msg or "checkpoint" in msg and ("old" in msg or "stale" in msg):
            severity = "CRITICAL" if env == "prod" else "WARN"
            checkpoints.append({"issue_type": "CHECKPOINT_STALE", "service": r["service"], "severity": severity,
                                 "timestamp": r["timestamp"], "detail": r["message"]})
            pipeline_names.add(r["service"])

    sla_breaches = []
    DURATION_RE = re.compile(r"duration[=:]\s*(\d+)")
    for r in log_and_alert:
        m = DURATION_RE.search(r["message"] or "")
        if m and int(m.group(1)) > thresholds.get("sla_breach_threshold_ms", 300000):
            sla_breaches.append({"service": r["service"], "duration_ms": int(m.group(1)), "timestamp": r["timestamp"],
                                  "issue_type": "SLA_BREACH"})
            pipeline_names.add(r["service"])

    backlogs = []
    for t in topics:
        if t["verdict"] != "OK":
            backlogs.append({"topic": t["topic"], "consumer_group": t["consumer_group"], "lag": t["lag"],
                              "verdict": t["verdict"], "issue_type": "PROCESSING_BACKLOG",
                              "description": f"backlog building on {t['consumer_group']}, lag currently {t['verdict']}"})

    # Closed-Set Fallback Contract: a TRIGGERED alert record whose subject matter matches none of the
    # seven named issue types above (checkpoint/offset keywords, an explicit SLA duration) MUST NOT be
    # silently dropped just because this file's enum doesn't recognize its domain (queue depth, GC
    # pause, etc.) -- emit it as PIPELINE_ALERT_UNCLASSIFIED, populated only from the record's own
    # actual fields.
    alert_records = [r for r in all_records if r["source_type"] == "alert"]
    unclassified_alerts = []
    for r in alert_records:
        if r["raw"].get("status") != "TRIGGERED":
            continue
        msg = r["message"] or ""
        msg_lower = msg.lower()
        already_classified = ("checkpoint" in msg_lower or "offset" in msg_lower) or bool(DURATION_RE.search(msg))
        if already_classified:
            continue
        unclassified_alerts.append({
            "issue_type": "PIPELINE_ALERT_UNCLASSIFIED", "service": r["service"], "verdict": r["severity"],
            "timestamp": r["timestamp"], "monitor_name": r["raw"].get("monitor_name"),
            "description": redact_text(msg),
        })
        pipeline_names.add(r["raw"].get("monitor_name") or r["service"])

    all_issues = []
    for t in topics:
        if t["verdict"] != "OK":
            all_issues.append({"service": t["consumer_group"], "issue_type": t["issue_type"], "verdict": t["verdict"],
                                "timestamp": t.get("timestamp")})
    for c in checkpoints:
        all_issues.append({"service": c["service"], "issue_type": c["issue_type"], "verdict": c["severity"],
                            "timestamp": c.get("timestamp")})
    for s in sla_breaches:
        all_issues.append({"service": s["service"], "issue_type": "SLA_BREACH", "verdict": "CRITICAL",
                            "timestamp": s.get("timestamp")})
    for u in unclassified_alerts:
        all_issues.append({"service": u["service"], "issue_type": "PIPELINE_ALERT_UNCLASSIFIED", "verdict": u["verdict"],
                            "timestamp": u.get("timestamp"), "monitor_name": u.get("monitor_name")})

    # Mandatory Pre-Write Gate (Closed-Set Fallback Contract): refuse to write apm_report.json if any
    # TRIGGERED alert record has no corresponding entry (named type or PIPELINE_ALERT_UNCLASSIFIED) in
    # all_issues.
    for r in alert_records:
        if r["raw"].get("status") != "TRIGGERED":
            continue
        monitor_name = r["raw"].get("monitor_name")
        covered = (
            any(i.get("monitor_name") == monitor_name and i.get("timestamp") == r["timestamp"] for i in all_issues) or
            any(c["service"] == r["service"] and c.get("timestamp") == r["timestamp"] for c in checkpoints) or
            any(s["service"] == r["service"] and s.get("timestamp") == r["timestamp"] for s in sla_breaches)
        )
        if not covered:
            raise AssertionError(
                f"Alert '{monitor_name}' (priority {r['raw'].get('priority')}, triggered {r['timestamp']}) has no "
                f"corresponding entry in apm_report.json.all_issues -- not even a PIPELINE_ALERT_UNCLASSIFIED "
                f"fallback. Do not write apm_report.json until every TRIGGERED alert record is represented."
            )

    critical_issues = sum(1 for i in all_issues if i["verdict"] == "CRITICAL")
    warn_issues = sum(1 for i in all_issues if i["verdict"] == "WARN")
    pipelines_with_issues = len({i["service"] for i in all_issues})
    total_pipelines_analysed = max(len(pipeline_names), pipelines_with_issues)

    return {
        "summary": {
            "total_pipelines_analysed": total_pipelines_analysed,
            "pipelines_with_issues": pipelines_with_issues,
            "critical_issues": critical_issues, "warn_issues": warn_issues,
            "sla_breaches": len(sla_breaches),
            "analysis_period": analysis_period,
        },
        "kafka": {"topics": topics},
        "checkpoints": checkpoints,
        "sla_breaches": sla_breaches,
        "backlogs": backlogs,
        "unclassified_alerts": unclassified_alerts,
        "all_issues": all_issues,
    }


# ---------------------------------------------------------------------------
# Phase 4 -- Security Audit Agent
# ---------------------------------------------------------------------------

def run_security_audit_agent(normalised, cfg):
    settings = cfg
    auth_failure_threshold = settings.get("auth_failure_threshold", 5)

    all_records = normalised["records"]
    scannable = [r for r in all_records if r["source_type"] in ("log", "alert")]

    timestamps = [r["timestamp"] for r in all_records if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    findings = []

    for r in scannable:
        msg = r["message"] or ""
        msg_lower = msg.lower()
        flagged = False
        if any(f"{col}=" in msg_lower for col in PII_COLUMNS if col not in ("token", "secret", "password")):
            findings.append({"issue_type": "PII_IN_LOGS", "severity": "CRITICAL", "service": r["service"],
                              "timestamp": r["timestamp"], "description": redact_text(msg), "redacted": True,
                              "action": "Remove PII from log statements immediately"})
            flagged = True
        if not flagged:
            for pattern in PII_VALUE_PATTERNS.values():
                if re.search(pattern, msg):
                    findings.append({"issue_type": "PII_IN_LOGS", "severity": "CRITICAL", "service": r["service"],
                                      "timestamp": r["timestamp"], "description": redact_text(msg), "redacted": True,
                                      "action": "Remove PII from log statements immediately"})
                    flagged = True
                    break

    for r in scannable:
        msg = r["message"] or ""
        if any(cred in msg for cred in CREDENTIAL_PATTERNS):
            findings.append({"issue_type": "CREDENTIAL_LEAK", "severity": "CRITICAL", "service": r["service"],
                              "timestamp": r["timestamp"], "description": redact_text(msg), "redacted": True,
                              "action": "Rotate leaked credential immediately and scrub log storage"})

    auth_fail_records = [r for r in scannable if re.search(r"\b40[13]\b|access denied|unauthoris?ed|unauthoriz?ed", (r["message"] or ""), re.I)]
    for r in auth_fail_records:
        findings.append({"issue_type": "UNAUTHORISED_ACCESS", "severity": "ERROR", "service": r["service"],
                          "timestamp": r["timestamp"], "description": redact_text(r["message"]), "redacted": True,
                          "action": "Investigate unauthorized access attempt and confirm access controls"})

    groups = defaultdict(list)
    grouping_key_used = {}
    for r in auth_fail_records:
        if r["source_ip"]:
            key = ("source_ip", r["source_ip"])
        elif r["user"]:
            key = ("user", r["user"])
        else:
            key = ("service", r["service"])
        groups[key].append(r)

    auth_failures_list = []
    for (key_type, key_val), recs in groups.items():
        recs_sorted = sorted(recs, key=lambda r: r["timestamp"] or "")
        window_start = 0
        i = 0
        while i < len(recs_sorted):
            j = i
            t0 = datetime.fromisoformat(recs_sorted[i]["timestamp"].replace("Z", "+00:00")) if recs_sorted[i]["timestamp"] else None
            window = [recs_sorted[i]]
            j += 1
            while j < len(recs_sorted) and recs_sorted[j]["timestamp"] and t0:
                tj = datetime.fromisoformat(recs_sorted[j]["timestamp"].replace("Z", "+00:00"))
                if (tj - t0).total_seconds() <= 60:
                    window.append(recs_sorted[j])
                    j += 1
                else:
                    break
            if len(window) > auth_failure_threshold:
                findings.append({"issue_type": "BRUTE_FORCE_ATTEMPT", "severity": "CRITICAL",
                                  "service": window[0]["service"], "timestamp": window[0]["timestamp"],
                                  "description": f"{len(window)} auth failures grouped by {key_type}={key_val} within 1 minute",
                                  "redacted": True, "action": "Block source and force credential rotation",
                                  "grouping_key": key_type})
            i = j if j > i else i + 1
        auth_failures_list.append({"key_type": key_type, "key_value": key_val, "count": len(recs)})

    for r in scannable:
        if r["source_type"] != "alert":
            continue
        msg_lower = (r["message"] or "").lower()
        if "brute force" in msg_lower or "auth-failures" in (r["raw"].get("monitor_name") or "").lower():
            findings.append({"issue_type": "SUSPICIOUS_ACTIVITY", "severity": "WARN", "service": r["service"],
                              "timestamp": r["timestamp"],
                              "description": f"External monitor '{r['raw'].get('monitor_name')}' reported possible brute force -- not independently confirmed",
                              "redacted": True, "action": "Review monitor and correlate with independent auth-failure grouping"})

    for r in scannable:
        msg_lower = (r["message"] or "").lower()
        if any(kw in msg_lower for kw in PERMISSION_ESCALATION_KEYWORDS):
            findings.append({"issue_type": "PERMISSION_ESCALATION", "severity": "ERROR", "service": r["service"],
                              "timestamp": r["timestamp"], "description": redact_text(r["message"]), "redacted": True,
                              "action": "Review privilege escalation attempt"})

    compliance = []
    for r in scannable:
        msg = r["message"] or ""
        for framework in COMPLIANCE_FRAMEWORKS:
            if framework.lower() in msg.lower() and ("violat" in msg.lower() or "breach" in msg.lower()):
                entry = {"issue_type": "COMPLIANCE_BREACH", "severity": "ERROR", "service": r["service"],
                          "timestamp": r["timestamp"], "description": redact_text(msg), "redacted": True,
                          "action": f"Review {framework} compliance breach", "framework": framework}
                findings.append(entry)
                compliance.append(entry)

    critical_issues = sum(1 for f in findings if f["severity"] == "CRITICAL")
    error_issues = sum(1 for f in findings if f["severity"] == "ERROR")
    warn_issues = sum(1 for f in findings if f["severity"] == "WARN")
    pii_exposures = sum(1 for f in findings if f["issue_type"] == "PII_IN_LOGS")
    credential_leaks = sum(1 for f in findings if f["issue_type"] == "CREDENTIAL_LEAK")

    return {
        "summary": {
            "total_security_issues": len(findings), "critical_issues": critical_issues,
            "error_issues": error_issues, "warn_issues": warn_issues, "pii_exposures": pii_exposures,
            "credential_leaks": credential_leaks, "auth_failures": len(auth_fail_records),
            "analysis_period": analysis_period,
        },
        "findings": findings,
        "auth_failures": auth_failures_list,
        "compliance": compliance,
        "all_issues": [{"issue_type": f["issue_type"], "severity": f["severity"], "service": f["service"]} for f in findings],
    }


# ---------------------------------------------------------------------------
# Shared service-dependency graph (built directly from trace records in normalised_data.json).
# Both the Anomaly Detection Agent (Phase 5) and the Dependency/Flow Analysis Agent (Phase 6) need
# this same graph -- the trace data it's derived from is already available after Phase 0, so both
# agents build an identical graph from source rather than Phase 5 waiting on Phase 6's own artifact.
# ---------------------------------------------------------------------------

def build_service_graph(records, cfg):
    trace_records = [r for r in records if r["source_type"] == "trace"]
    parent_span_id_available = bool(trace_records) and any(r["raw"].get("parent_span_id") for r in trace_records)

    by_trace = defaultdict(list)
    for r in trace_records:
        by_trace[r["raw"].get("trace_id")].append(r)

    edge_counts = defaultdict(lambda: {"count": 0, "latency_sum": 0.0})
    for trace_id, spans in by_trace.items():
        spans_sorted = sorted(spans, key=lambda s: s["timestamp"] or "")
        if parent_span_id_available:
            by_span_id = {s["raw"].get("span_id"): s for s in spans}
            for s in spans:
                parent_id = s["raw"].get("parent_span_id")
                parent = by_span_id.get(parent_id)
                if parent and parent["service"] != s["service"]:
                    key = (parent["service"], s["service"])
                    edge_counts[key]["count"] += 1
                    edge_counts[key]["latency_sum"] += s["raw"].get("duration_ms") or 0
        else:
            for i in range(len(spans_sorted) - 1):
                a, b = spans_sorted[i], spans_sorted[i + 1]
                if a["service"] != b["service"]:
                    key = (a["service"], b["service"])
                    edge_counts[key]["count"] += 1
                    edge_counts[key]["latency_sum"] += b["raw"].get("duration_ms") or 0

    total_spans = len(trace_records)
    effective_threshold = max(1, min(cfg.get("min_call_count_for_edge", 5), total_spans // 20)) if total_spans else cfg.get("min_call_count_for_edge", 5)

    edges = []
    nodes = set()
    for (frm, to), stats in edge_counts.items():
        if stats["count"] >= effective_threshold:
            edges.append({"from": frm, "to": to, "call_count": stats["count"],
                           "avg_latency_ms": round(stats["latency_sum"] / stats["count"], 1) if stats["count"] else 0})
            nodes.add(frm)
            nodes.add(to)

    downstream_map = defaultdict(set)
    upstream_map = defaultdict(set)
    for e in edges:
        downstream_map[e["from"]].add(e["to"])
        upstream_map[e["to"]].add(e["from"])

    return {
        "nodes": nodes, "edges": edges, "downstream_map": downstream_map, "upstream_map": upstream_map,
        "effective_threshold": effective_threshold, "parent_span_id_available": parent_span_id_available,
    }


def graph_full_downstream(downstream_map, start):
    seen, stack = set(), [(start, 0)]
    result = {}
    while stack:
        node, depth = stack.pop()
        for nxt in downstream_map.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                result[nxt] = depth + 1
                stack.append((nxt, depth + 1))
    return result


def graph_connected(downstream_map, upstream_map, a, b, max_hops):
    if a == b:
        return True
    visited, frontier = {a}, {a}
    for _ in range(max_hops):
        nxt = set()
        for n in frontier:
            nxt |= downstream_map.get(n, set())
            nxt |= upstream_map.get(n, set())
        nxt -= visited
        if b in nxt:
            return True
        visited |= nxt
        frontier = nxt
        if not frontier:
            break
    return False


# ---------------------------------------------------------------------------
# Phase 5 -- Anomaly Detection Agent
# ---------------------------------------------------------------------------

def run_anomaly_detection_agent(log_analysis, metrics_report, apm_report, security_report, normalised, cfg):
    settings = cfg
    spike_multiplier = settings.get("spike_multiplier", 2.0)

    anomalies = []

    for row in metrics_report["latency"]["by_service"]:
        p99, avg = row["p99_ms"], row["avg_ms"]
        if avg and avg > 0 and p99 > spike_multiplier * avg:
            trace = next((t for t in metrics_report["latency"]["slowest_traces"] if t["service"] == row["service"]), None)
            anomalies.append({
                "anomaly_type": "LATENCY_SPIKE", "service": row["service"],
                "timestamp": trace["timestamp"] if trace else None, "value": p99, "baseline": avg,
                "deviation_pct": round(100 * (p99 - avg) / avg, 1), "confidence": "MEDIUM",
                "corroborated_by": [],
                "description": f"{row['service']} p99 latency {p99}ms is {round(p99/avg,1)}x its own baseline avg {avg}ms",
            })

    metric_series = defaultdict(list)
    for r in normalised["records"]:
        if r["source_type"] != "metric":
            continue
        role = metric_role(r["raw"].get("metric_name")) or r["raw"].get("metric_name")
        key = (r["service"], role)
        val = r["raw"].get("value")
        if val is not None and r["timestamp"]:
            metric_series[key].append((r["timestamp"], val))

    # Anomaly Types is a closed enum (ERROR_RATE_SPIKE, LATENCY_SPIKE, CPU_SPIKE, MEMORY_SPIKE,
    # REJECTION_RATE_SPIKE, KAFKA_LAG_SPIKE, THROUGHPUT_DROP, WORSENING_TREND, IMPROVING_TREND,
    # CORRELATED_ANOMALY) -- it has no disk/network-specific member. Per the Closed-Set Fallback
    # Contract, a role with no genuine named mapping (e.g. disk_pct, network_in) MUST NOT be silently
    # dropped just because today's enum doesn't name its domain -- emit it as a generic
    # THRESHOLD_BREACH instead, so any future unrecognized metric domain still surfaces a signal.
    ROLE_TO_ANOMALY_TYPE = {"kafka_consumer_lag": "KAFKA_LAG_SPIKE", "cpu_pct": "CPU_SPIKE", "memory_pct": "MEMORY_SPIKE"}

    for (service, role), points in metric_series.items():
        anomaly_type = ROLE_TO_ANOMALY_TYPE.get(role, "THRESHOLD_BREACH")
        points.sort()
        if len(points) < max(3, settings.get("min_data_points", 3)):
            continue
        values = [v for _, v in points]
        best = None
        for i in range(1, len(points)):
            prior = values[:i]
            baseline = sum(prior) / len(prior)
            ts, val = points[i]
            if baseline > 0 and val > spike_multiplier * baseline:
                deviation = (val - baseline) / baseline
                # Among all qualifying points, report the one with the highest absolute value (the
                # incident's actual peak) rather than the steepest relative jump -- the peak is what
                # downstream domain agents (e.g. apm_report's own KAFKA_LAG_CRITICAL) already anchor
                # on, so anomalies stay correlatable with them instead of pointing at the spike's onset.
                if best is None or val > best[1]:
                    best = (ts, val, deviation, baseline)
        if best is not None:
            ts, val, deviation, baseline = best
            val_r = round(val, 2)
            anomalies.append({
                "anomaly_type": anomaly_type, "service": service, "timestamp": ts, "value": val_r,
                "baseline": round(baseline, 2), "deviation_pct": round(100 * deviation, 1),
                "confidence": "MEDIUM", "corroborated_by": [],
                "description": f"{service} {role or 'metric'} reading {val_r} exceeded {spike_multiplier}x its own prior baseline {round(baseline,2)}",
            })

    seen = set()
    deduped = []
    for a in anomalies:
        key = (a["anomaly_type"], a["service"], round((datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")).timestamp() // 300) if a["timestamp"] else 0))
        if key in seen:
            continue
        seen.add(key)
        a["description"] = redact_text(a["description"])
        deduped.append(a)
    anomalies = deduped

    # dependency_report.json is a Phase 6 artifact and isn't in this agent's own input_files, but the
    # trace data it would be built from already exists in normalised_data.json after Phase 0 -- so this
    # agent builds the same service graph from source (build_service_graph) rather than waiting on it,
    # which lets it correlate cross-service, graph-connected anomalies too, not just same-service ones.
    graph = build_service_graph(normalised["records"], {"min_call_count_for_edge": 5, "max_hops_upstream": 5})
    downstream_map, upstream_map = graph["downstream_map"], graph["upstream_map"]
    max_hops = 5

    def related(a, b):
        if a["service"] == b["service"]:
            return True
        return graph_connected(downstream_map, upstream_map, a["service"], b["service"], max_hops)

    correlated = []
    window_seconds = 300
    for i in range(len(anomalies)):
        for j in range(i + 1, len(anomalies)):
            a, b = anomalies[i], anomalies[j]
            if not a["timestamp"] or not b["timestamp"]:
                continue
            if a["service"] == b["service"] and a["anomaly_type"] == b["anomaly_type"]:
                continue
            if not related(a, b):
                continue
            ta = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
            tb = datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00"))
            if abs((ta - tb).total_seconds()) > window_seconds:
                continue
            corroborated = sorted({a["anomaly_type"], b["anomaly_type"]})
            services_key = tuple(sorted({a["service"], b["service"]}))
            key = (services_key, tuple(corroborated), round(ta.timestamp() // window_seconds))
            if key in seen:
                continue
            seen.add(key)
            services_text = a["service"] if a["service"] == b["service"] else f"{a['service']} and {b['service']}"
            correlated.append({
                "anomaly_type": "CORRELATED_ANOMALY", "service": a["service"], "timestamp": min(a["timestamp"], b["timestamp"]),
                "value": None, "baseline": None, "deviation_pct": None, "confidence": "HIGH",
                "corroborated_by": corroborated,
                "description": f"{services_text} showed correlated {corroborated[0]} and {corroborated[1]} within the same window",
            })

    all_anomalies = anomalies + correlated
    trends = []

    high = sum(1 for a in all_anomalies if a["confidence"] == "HIGH")
    medium = sum(1 for a in all_anomalies if a["confidence"] == "MEDIUM")
    low = sum(1 for a in all_anomalies if a["confidence"] == "LOW")

    timestamps = [r["timestamp"] for r in normalised["records"] if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    summary = {
        "total_anomalies": len(all_anomalies), "high_confidence": high, "medium_confidence": medium,
        "low_confidence": low, "correlated_anomalies": len(correlated),
        "worsening_trends": sum(1 for t in trends if t.get("trend") == "WORSENING_TREND"),
        "improving_trends": sum(1 for t in trends if t.get("trend") == "IMPROVING_TREND"),
        "analysis_period": analysis_period,
    }

    # Mandatory Pre-Write Gate (anomaly-detection-agent.md): run as actual code, not just prose.
    # Refuse to write anomaly_report.json if a connected, time-adjacent pair exists but the summary
    # says zero correlations -- this is the exact defect the gate exists to catch.
    for i in range(len(anomalies)):
        for j in range(i + 1, len(anomalies)):
            a, b = anomalies[i], anomalies[j]
            if not a["timestamp"] or not b["timestamp"] or not related(a, b):
                continue
            ta = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
            tb = datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00"))
            if abs((ta - tb).total_seconds()) <= window_seconds and summary["correlated_anomalies"] == 0:
                raise AssertionError(
                    f"A dependency-connected, time-adjacent anomaly pair exists ({a['service']}@{a['timestamp']} "
                    f"<-> {b['service']}@{b['timestamp']}) but summary.correlated_anomalies == 0. Do not write "
                    f"anomaly_report.json until the correlation pass actually emits a CORRELATED_ANOMALY entry for it."
                )

    # Mandatory Pre-Write Gate (Closed-Set Fallback Contract): refuse to write anomaly_report.json if
    # any metric series with a computable baseline (2+ prior readings) and a qualifying deviation has
    # no matching entry -- named type or THRESHOLD_BREACH fallback -- in the anomalies actually emitted.
    # Mirrors the detection loop above: a series' representative deviation point is its highest-value
    # qualifying point (the peak), not every threshold crossing.
    for (series_service, series_role), points in metric_series.items():
        points_sorted = sorted(points)
        if len(points_sorted) < 3:
            continue
        values = [v for _, v in points_sorted]
        series_best = None
        for i in range(1, len(points_sorted)):
            baseline = sum(values[:i]) / i
            ts, val = points_sorted[i]
            if baseline > 0 and val > spike_multiplier * baseline:
                if series_best is None or val > series_best[1]:
                    series_best = (ts, val)
        if series_best is not None:
            ts, val = series_best
            matching = [a for a in all_anomalies if a["service"] == series_service and a["timestamp"] == ts]
            if not matching:
                raise AssertionError(
                    f"{series_service}/{series_role} has a baseline and a qualifying deviation at {ts} "
                    f"but no anomaly entry exists (not even a THRESHOLD_BREACH fallback). Do not write "
                    f"anomaly_report.json until every metric domain with a computable baseline and a "
                    f"real deviation is represented."
                )

    return {
        "summary": summary,
        "anomalies": sorted(all_anomalies, key=lambda a: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(a["confidence"], 3), -(a.get("deviation_pct") or 0))),
        "trends": trends,
        "all_anomalies": all_anomalies,
    }


# ---------------------------------------------------------------------------
# Phase 6 -- Dependency/Flow Analysis Agent
# ---------------------------------------------------------------------------

def run_dependency_flow_agent(normalised, metrics_report, apm_report, anomaly_report, log_analysis, cfg):
    settings = cfg
    timestamps = [r["timestamp"] for r in normalised["records"] if r["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    graph = build_service_graph(normalised["records"], settings)
    nodes, edges = graph["nodes"], graph["edges"]
    downstream_map, upstream_map = graph["downstream_map"], graph["upstream_map"]
    effective_threshold = graph["effective_threshold"]
    parent_span_id_available = graph["parent_span_id_available"]

    # Evidence source for bidirectional impact traversal: any CRITICAL/ERROR finding on any service,
    # from any of the four upstream reports, with a real timestamp. Used to decide whether a *caller*
    # of the breakpoint (an inward edge, from == caller, to == breakpoint) is itself a genuine
    # downstream symptom, not just structurally adjacent.
    evidence_by_service = defaultdict(list)
    for a in anomaly_report.get("anomalies", []):
        if a.get("timestamp") and a.get("anomaly_type") != "CORRELATED_ANOMALY":
            evidence_by_service[a["service"]].append(a["timestamp"])
    for i in metrics_report.get("all_issues", []):
        if i.get("timestamp") and i.get("verdict") in ("CRITICAL", "ERROR"):
            evidence_by_service[i["service"]].append(i["timestamp"])
    for e in log_analysis.get("all_errors", []):
        if e.get("timestamp") and e.get("severity") in ("CRITICAL", "ERROR"):
            evidence_by_service[e["service"]].append(e["timestamp"])
    for i in apm_report.get("all_issues", []):
        if i.get("timestamp") and i.get("verdict") in ("CRITICAL", "ERROR"):
            evidence_by_service[i["service"]].append(i["timestamp"])

    def bidirectional_downstream(start, onset_ts):
        # Outward edges (start calls X) are always genuine downstream impact. Inward edges (Y calls
        # start) only count if Y shows its OWN qualifying evidence after the breakpoint's own failure
        # onset -- otherwise Y is just a structural neighbor, not a symptom. Continue the traversal
        # from every newly added node either way, since a caller's own caller may also show symptoms.
        visited = {start}
        frontier = [start]
        result = {}
        hop = 0
        while frontier:
            hop += 1
            next_frontier = []
            for n in frontier:
                for m in downstream_map.get(n, set()):
                    if m not in visited:
                        visited.add(m)
                        result[m] = hop
                        next_frontier.append(m)
                for m in upstream_map.get(n, set()):
                    if m in visited:
                        continue
                    m_timestamps = [ts for ts in evidence_by_service.get(m, []) if ts]
                    if onset_ts and any(ts > onset_ts for ts in m_timestamps):
                        visited.add(m)
                        result[m] = hop
                        next_frontier.append(m)
            frontier = next_frontier
        return result

    # Breakpoint candidates come from EVERY anomaly/incident window -- not only from a pre-existing
    # CORRELATED_ANOMALY entry, and not only from anomaly_report.json. A service can have a CRITICAL
    # finding in metrics_report.json or log_analysis.json with no corresponding anomaly_report.json
    # entry at all (its metric domain may fall outside that agent's anomaly-type enum) -- MUST NOT let
    # a gap in one upstream agent silently propagate into a gap here, so independently pull in CRITICAL
    # findings on any graph-node service from those two reports as additional candidate windows.
    raw_anomalies = [a for a in anomaly_report.get("anomalies", []) if a.get("anomaly_type") != "CORRELATED_ANOMALY" and a.get("timestamp")]
    extra_critical = (
        [{"service": i["service"], "timestamp": i["timestamp"]} for i in metrics_report.get("all_issues", [])
         if i.get("verdict") == "CRITICAL" and i.get("timestamp") and i["service"] in nodes] +
        [{"service": e["service"], "timestamp": e["timestamp"]} for e in log_analysis.get("all_errors", [])
         if e.get("severity") == "CRITICAL" and e.get("timestamp") and e["service"] in nodes]
    )
    candidate_signals = sorted(raw_anomalies + extra_critical, key=lambda a: a["timestamp"])

    breakpoints = []
    seen_breakpoints = set()
    max_hops = settings.get("max_hops_upstream", 5)
    window_seconds = 300
    for anchor in candidate_signals:
        anchor_dt = datetime.fromisoformat(anchor["timestamp"].replace("Z", "+00:00"))
        window = [a for a in candidate_signals
                  if abs((datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")) - anchor_dt).total_seconds()) <= window_seconds]
        graph_services = sorted({a["service"] for a in window} & nodes)
        if len(graph_services) < 2:
            continue
        connected_subset = set()
        for s1 in graph_services:
            for s2 in graph_services:
                if s1 != s2 and graph_connected(downstream_map, upstream_map, s1, s2, max_hops):
                    connected_subset.update((s1, s2))
        if len(connected_subset) < 2:
            continue
        # Prefer the node whose OWN evidence in this window occurred earliest -- call-graph topology
        # ("no incoming edge") assumes the caller's own degradation cascades onward to its callees, but
        # a callee's internal failure (e.g. a service's own disk exhaustion) just as commonly causes the
        # CALLER to error out afterward, which is the opposite direction. Temporal precedence of the
        # actual evidence is the reliable signal in both cases; topology is only a tie-breaker.
        earliest_ts_per_service = {}
        for a in window:
            if a["service"] in connected_subset:
                if a["service"] not in earliest_ts_per_service or a["timestamp"] < earliest_ts_per_service[a["service"]]:
                    earliest_ts_per_service[a["service"]] = a["timestamp"]
        candidate = min(
            sorted(connected_subset),
            key=lambda c: (earliest_ts_per_service.get(c, "9999"), bool(upstream_map.get(c, set()) & connected_subset)))
        downstream = bidirectional_downstream(candidate, earliest_ts_per_service.get(candidate))
        if not downstream:
            continue
        key = (candidate, tuple(sorted(downstream.keys())))
        if key in seen_breakpoints:
            continue
        seen_breakpoints.add(key)
        confidence = round(0.6 * (1.0 if parent_span_id_available else 0.75) + 0.2, 2)
        confidence = min(confidence, 0.75 if not parent_span_id_available else 1.0)
        if confidence < settings.get("breakpoint_confidence_threshold", 0.6):
            continue
        breakpoints.append({
            "incident_id": f"anomaly_ref_{len(breakpoints)+1:03d}", "breakpoint_service": candidate,
            "issue_type": "BREAKPOINT_IDENTIFIED", "confidence": confidence,
            "downstream_impact": sorted(downstream.keys()), "hops_to_furthest_symptom": max(downstream.values()),
            "description": f"{candidate} identified as origin; failure propagated to {', '.join(sorted(downstream.keys()))} within {window_seconds // 60} minutes",
        })

    cascading_failures = [
        {"breakpoint_service": b["breakpoint_service"], "downstream_impact": b["downstream_impact"]}
        for b in breakpoints if len(b["downstream_impact"]) >= 2
    ]

    all_findings = list(breakpoints)

    return {
        "summary": {
            "total_services_mapped": len(nodes), "total_edges": len(edges),
            "breakpoints_identified": len(breakpoints), "cascading_failures": len(cascading_failures),
            "effective_min_call_count_for_edge": effective_threshold,
            "parent_span_id_available": parent_span_id_available,
            "analysis_period": analysis_period,
        },
        "dependency_graph": {"nodes": sorted(nodes), "edges": edges},
        "breakpoints": breakpoints,
        "cascading_failures": cascading_failures,
        "all_findings": all_findings,
    }


# ---------------------------------------------------------------------------
# Phase 7 -- Root Cause Analysis Agent
# ---------------------------------------------------------------------------

CATEGORY_RULES = [
    ({"KAFKA_LAG_HIGH", "KAFKA_LAG_CRITICAL", "CHECKPOINT_STALE", "PROCESSING_BACKLOG", "MISSED_TRIGGER", "SLA_BREACH"}, "PIPELINE_BACKPRESSURE"),
    ({"PII_IN_LOGS", "CREDENTIAL_LEAK", "BRUTE_FORCE_ATTEMPT", "UNAUTHORISED_ACCESS", "PERMISSION_ESCALATION", "COMPLIANCE_BREACH"}, "SECURITY_INCIDENT"),
    ({"HIGH_REJECTION_RATE", "REJECTION_RATE_WORSENING"}, "DATA_QUALITY_DEGRADATION"),
    ({"HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK", "RESOURCE_EXHAUSTION"}, "RESOURCE_SATURATION"),
]


def assign_category(finding_types, has_breakpoint, worsening_trend_present):
    # Priority order follows the spec's own Category Assignment table top-to-bottom: KAFKA_LAG_*/
    # security/DQ/resource evidence all outrank the breakpoint row. A dependency breakpoint only wins
    # (as UPSTREAM_DEPENDENCY_FAILURE) when none of those four evidence classes are present -- e.g. a
    # breakpoint whose own incident evidence is CONNECTION_FAILURE/TIMEOUT with no Kafka/security/DQ/
    # resource finding attached.
    for types, category in CATEGORY_RULES:
        if finding_types & types:
            return category
    if has_breakpoint:
        return "UPSTREAM_DEPENDENCY_FAILURE"
    if "CONNECTION_FAILURE" in finding_types or "TIMEOUT" in finding_types:
        return "UPSTREAM_DEPENDENCY_FAILURE"
    if worsening_trend_present:
        return "CAPACITY_SHORTFALL"
    return "UNDETERMINED"


def run_root_cause_agent(log_analysis, metrics_report, apm_report, security_report, anomaly_report, dependency_report, cfg):
    settings = cfg
    correlation_window = settings.get("correlation_window_minutes", 5)
    min_evidence_sources = settings.get("min_evidence_sources", 2)

    findings = []
    for e in log_analysis.get("all_errors", []):
        findings.append({"source": "log_analysis.json", "issue_type": e["error_type"], "service": e["service"],
                          "timestamp": e["timestamp"], "severity": e["severity"], "description": e["message"]})
    for i in metrics_report.get("all_issues", []):
        findings.append({"source": "metrics_report.json", "issue_type": i["issue_type"], "service": i["service"],
                          "timestamp": i.get("timestamp"), "severity": i["verdict"], "description": i.get("description", i["issue_type"])})
    for i in apm_report.get("all_issues", []):
        findings.append({"source": "apm_report.json", "issue_type": i["issue_type"], "service": i["service"],
                          "timestamp": i.get("timestamp"), "severity": i["verdict"],
                          "description": f"{i['issue_type']} on {i['service']}"})
    for f in security_report.get("findings", []):
        findings.append({"source": "security_report.json", "issue_type": f["issue_type"], "service": f["service"],
                          "timestamp": f["timestamp"], "severity": f["severity"], "description": f["description"]})
    for a in anomaly_report.get("anomalies", []):
        findings.append({"source": "anomaly_report.json", "issue_type": a["anomaly_type"], "service": a["service"],
                          "timestamp": a["timestamp"], "severity": "HIGH" if a["confidence"] == "HIGH" else "MEDIUM",
                          "description": a["description"]})

    dep_nodes = set(dependency_report.get("dependency_graph", {}).get("nodes", []))
    dep_edges = dependency_report.get("dependency_graph", {}).get("edges", [])
    adjacency = defaultdict(set)
    for e in dep_edges:
        adjacency[e["from"]].add(e["to"])
        adjacency[e["to"]].add(e["from"])

    def connected(a, b):
        if a == b:
            return True
        return b in adjacency.get(a, set())

    for bp in dependency_report.get("breakpoints", []):
        related_anomaly = next((a for a in anomaly_report.get("anomalies", []) if a["service"] == bp["breakpoint_service"]), None)
        ts = related_anomaly["timestamp"] if related_anomaly else (dependency_report["summary"]["analysis_period"]["from"])
        findings.append({"source": "dependency_report.json", "issue_type": bp["issue_type"],
                          "service": bp["breakpoint_service"], "timestamp": ts, "severity": "CRITICAL",
                          "description": bp["description"], "is_breakpoint": True,
                          "downstream_impact": bp["downstream_impact"]})

    findings_sorted = sorted(findings, key=lambda f: f["timestamp"] or "")

    incidents_raw = []

    def find_incident_by_time(f):
        if not f["timestamp"]:
            return None
        ft = datetime.fromisoformat(f["timestamp"].replace("Z", "+00:00"))
        for inc in incidents_raw:
            for other in inc["findings"]:
                if not other["timestamp"]:
                    continue
                ot = datetime.fromisoformat(other["timestamp"].replace("Z", "+00:00"))
                if abs((ft - ot).total_seconds()) <= correlation_window * 60 and connected(f["service"], other["service"]):
                    return inc
        return None

    def find_incident_by_service(f):
        for inc in incidents_raw:
            for other in inc["findings"]:
                if connected(f["service"], other["service"]):
                    return inc
        return None

    for f in findings_sorted:
        target = find_incident_by_time(f)
        if target is None:
            target = find_incident_by_service(f)
        if target is not None:
            target["findings"].append(f)
        else:
            incidents_raw.append({"findings": [f]})

    incidents = []
    unresolved_findings = []
    for inc_idx, inc in enumerate(incidents_raw, start=1):
        sources = {f["source"] for f in inc["findings"]}
        if len(sources) < min_evidence_sources:
            for f in inc["findings"]:
                if f["severity"] == "CRITICAL":
                    unresolved_findings.append({"source": f["source"], "issue_type": f["issue_type"],
                                                 "service": f["service"], "reason": f"fewer than {min_evidence_sources} corroborating sources"})
            continue

        finding_types = {f["issue_type"] for f in inc["findings"]}
        has_breakpoint = any(f.get("is_breakpoint") for f in inc["findings"])
        worsening = any(f["issue_type"] == "WORSENING_TREND" for f in inc["findings"])
        category = assign_category(finding_types, has_breakpoint, worsening)

        bp_finding = next((f for f in inc["findings"] if f.get("is_breakpoint")), None)
        affected_services = {f["service"] for f in inc["findings"]}
        if bp_finding:
            affected_services.update(bp_finding.get("downstream_impact", []))

        seen_desc = set()
        downstream_symptoms = []
        # None-timestamp findings (e.g. a metrics_report issue with no derivable timestamp) must sort
        # LAST, never first -- an empty-string/None timestamp sorting first previously caused
        # `primary_service` to be picked from a findings with no real timestamp ahead of a genuinely
        # earlier-timestamped one, which is exactly backwards for "earliest first-evidenced" selection.
        sorted_findings = sorted(inc["findings"], key=lambda f: (f["timestamp"] is None, f["timestamp"] or ""))
        if bp_finding:
            primary = bp_finding
        else:
            # Per the primary_service selection rule: when no dependency breakpoint exists, pick the
            # service with the earliest first-evidenced CRITICAL/ERROR finding -- never by highest
            # error-rate/deviation or raw list order. Fall back to any finding only if the incident
            # genuinely has no CRITICAL/ERROR evidence.
            critical_or_error = [f for f in sorted_findings if f["severity"] in ("CRITICAL", "ERROR", "HIGH")]
            primary = critical_or_error[0] if critical_or_error else sorted_findings[0]
        for f in sorted_findings:
            if f is primary:
                continue
            text = (f.get("description") or f"{f['issue_type']} on {f['service']}").strip()
            if text and text not in seen_desc:
                downstream_symptoms.append(text)
                seen_desc.add(text)

        confidence = "HIGH" if len(sources) >= settings.get("confidence_high_threshold", 3) else (
            "MEDIUM" if len(sources) >= settings.get("confidence_medium_threshold", 2) else "LOW")
        severities = [f["severity"] for f in inc["findings"]]
        severity = "CRITICAL" if "CRITICAL" in severities else ("ERROR" if "ERROR" in severities else (
            "WARN" if "WARN" in severities else "HIGH" if "HIGH" in severities else "MEDIUM"))
        ts_list = [f["timestamp"] for f in inc["findings"] if f["timestamp"]]

        incidents.append({
            "incident_id": f"incident_{inc_idx:03d}", "root_cause_category": category, "confidence": confidence,
            "primary_service": primary["service"], "affected_services": sorted(affected_services),
            "timeframe": {"from": min(ts_list) if ts_list else None, "to": max(ts_list) if ts_list else None},
            "root_cause_finding": primary.get("description") or f"{primary['issue_type']} on {primary['service']}",
            "dependency_breakpoint": bp_finding["service"] if bp_finding else None,
            "downstream_symptoms": downstream_symptoms,
            "evidence_sources": sorted(sources),
            "severity": severity, "blast_radius": len(affected_services),
        })

    high = sum(1 for i in incidents if i["confidence"] == "HIGH")
    medium = sum(1 for i in incidents if i["confidence"] == "MEDIUM")
    low = sum(1 for i in incidents if i["confidence"] == "LOW")

    timestamps = [f["timestamp"] for f in findings if f["timestamp"]]
    analysis_period = {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None}

    root_cause_out = {
        "summary": {"total_incidents": len(incidents), "high_confidence": high, "medium_confidence": medium,
                     "low_confidence": low, "analysis_period": analysis_period},
        "incidents": incidents,
        "unresolved_findings": unresolved_findings,
        "all_incidents": incidents,
    }

    recommendations = []
    priority_by_severity_conf = {
        ("CRITICAL", "HIGH"): "P1_IMMEDIATE", ("CRITICAL", "MEDIUM"): "P1_IMMEDIATE", ("CRITICAL", "LOW"): "P2_URGENT",
        ("ERROR", "HIGH"): "P2_URGENT", ("ERROR", "MEDIUM"): "P2_URGENT", ("ERROR", "LOW"): "P3_PLANNED",
        ("WARN", "HIGH"): "P3_PLANNED", ("WARN", "MEDIUM"): "P3_PLANNED", ("WARN", "LOW"): "P4_ADVISORY",
    }
    ranked = sorted(incidents, key=lambda i: (
        {"CRITICAL": 0, "ERROR": 1, "WARN": 2}.get(i["severity"], 3), -i["blast_radius"]))
    for i in ranked:
        priority = priority_by_severity_conf.get((i["severity"], i["confidence"]), "P3_PLANNED")
        recommendations.append({
            "rank": len(recommendations) + 1, "priority": priority, "incident_id": i["incident_id"],
            "title": f"Address {i['root_cause_category']} on {i['primary_service']}",
            "description": i["root_cause_finding"],
            "action": f"Investigate and remediate {i['root_cause_category']} affecting {', '.join(i['affected_services'])}",
            "affected_services": i["affected_services"],
            "evidence": "; ".join(f"{src}" for src in i["evidence_sources"]) + f": {i['root_cause_finding']}",
        })
    recommendations = recommendations[:settings.get("max_recommendations", 10)]
    for idx, r in enumerate(recommendations, start=1):
        r["rank"] = idx

    p1 = sum(1 for r in recommendations if r["priority"] == "P1_IMMEDIATE")
    p2 = sum(1 for r in recommendations if r["priority"] == "P2_URGENT")
    p3 = sum(1 for r in recommendations if r["priority"] == "P3_PLANNED")
    p4 = sum(1 for r in recommendations if r["priority"] == "P4_ADVISORY")

    recommendations_out = {
        "summary": {"total_recommendations": len(recommendations), "p1_immediate": p1, "p2_urgent": p2,
                     "p3_planned": p3, "p4_advisory": p4},
        "recommendations": recommendations,
    }

    # Mandatory Pre-Write Gate (root-cause-analysis-agent.md): run as actual code, not just prose.
    # Refuse to write root_cause.json if apm_report has a CRITICAL kafka/checkpoint finding but no
    # incident is categorized PIPELINE_BACKPRESSURE.
    critical_kafka_or_checkpoint = (
        [t for t in apm_report.get("kafka", {}).get("topics", []) if t.get("verdict") == "CRITICAL"] +
        [c for c in apm_report.get("checkpoints", []) if c.get("severity") == "CRITICAL"]
    )
    if critical_kafka_or_checkpoint:
        categories_present = {i["root_cause_category"] for i in root_cause_out["incidents"]}
        if "PIPELINE_BACKPRESSURE" not in categories_present:
            raise AssertionError(
                "apm_report.json has CRITICAL kafka/checkpoint findings but no incident in root_cause.json "
                "is categorized PIPELINE_BACKPRESSURE. Do not write root_cause.json until this is fixed."
            )

    return root_cause_out, recommendations_out


# ---------------------------------------------------------------------------
# Phase 8 -- Code Patch Generator Agent
# ---------------------------------------------------------------------------

PATCH_TYPE_BY_CATEGORY = {
    "PIPELINE_BACKPRESSURE": ("SCALING_CONFIG_CHANGE", "config/kafka-consumer.yaml", "MEDIUM"),
    "SECURITY_INCIDENT": ("LOGGING_REDACTION_ADD", "src/logging/redaction.py", "MEDIUM"),
    "RESOURCE_SATURATION": ("CONNECTION_POOL_RESIZE", "config/connection-pool.yaml", "LOW"),
    "DATA_QUALITY_DEGRADATION": ("CONFIG_THRESHOLD_ADJUST", "config/dq-thresholds.yaml", "LOW"),
}

PII_FIELD_RE = re.compile(r"PII field '(\w+)'")
CREDENTIAL_HEADER_RE = re.compile(r"Credential leak detected: (\w+)")
LAG_VALUE_RE = re.compile(r"lag[=\s]+([\d.]+)", re.I)
PCT_VALUE_RE = re.compile(r"([\d.]+)\s*%")
RESOURCE_ISSUE_RE = re.compile(r"HIGH_CPU|HIGH_MEMORY|HIGH_DISK|RESOURCE_EXHAUSTION")


def build_concrete_patch(category, incident):
    """Build a diff with real, finding-specific content. Returns None if no concrete anchor can be
    extracted from the incident's own evidence -- callers must skip to manual review in that case
    rather than emit a placeholder, per the 'diff consisting only of comment lines is not a valid
    diff' rule."""
    text = " ".join([incident.get("root_cause_finding", "")] + incident.get("downstream_symptoms", []))

    if category == "SECURITY_INCIDENT":
        pii_fields = PII_FIELD_RE.findall(text)
        cred_headers = CREDENTIAL_HEADER_RE.findall(text)
        anchors = sorted(set(pii_fields) | set(cred_headers))
        if not anchors:
            return None
        before = ", ".join(f'"{a}"' for a in ["password", "token"])
        after = ", ".join(f'"{a}"' for a in ["password", "token"] + anchors)
        diff = f"- redact_fields = [{before}]\n+ redact_fields = [{after}]"
        return diff, f"Log line(s) exposed unredacted {', '.join(anchors)}; add {', '.join(anchors)} to the redaction field list before logging."

    if category == "PIPELINE_BACKPRESSURE":
        lag_values = [float(v) for v in LAG_VALUE_RE.findall(text)]
        if not lag_values:
            return None
        peak_lag = max(lag_values)
        target_instances = max(2, int(peak_lag // 50000) + 2)
        diff = f"- consumer_instances: 2\n+ consumer_instances: {target_instances}"
        return diff, f"Observed consumer lag peaked at {peak_lag:.0f}; scale consumer instances to {target_instances} to relieve backpressure (assumes a starting scale of 2 -- confirm actual current value before applying)."

    if category == "RESOURCE_SATURATION":
        # The affected host/service is already a clean structured field on the incident -- use it
        # directly rather than regex-parsing prose, which broke on phrasing like "host X flagged
        # HIGH_CPU (verdict CRITICAL)" (the word immediately after HIGH_CPU is "(verdict", not the
        # host name). Still confirm a genuine resource-issue token is present as a sanity check.
        if not RESOURCE_ISSUE_RE.search(text):
            return None
        host = incident.get("primary_service")
        if not host:
            return None
        diff = f"- pool_size: 20  # {host}\n+ pool_size: 40  # {host}"
        return diff, f"{host} showed sustained resource exhaustion; double the connection/thread pool size to relieve pressure (confirm current pool_size before applying)."

    if category == "DATA_QUALITY_DEGRADATION":
        pcts = [float(v) for v in PCT_VALUE_RE.findall(text)]
        if not pcts:
            return None
        peak_pct = max(pcts)
        diff = "- rejection_rate_warn_pct: 10\n+ rejection_rate_warn_pct: 8"
        return diff, f"Rejection rate reached {peak_pct:.1f}% during this incident; lower the warn threshold to 8% to surface this class of degradation earlier."

    return None


def run_code_patch_agent(recommendations, root_cause, cfg):
    settings = cfg
    only_priorities = set(settings.get("only_patch_priorities", ["P1_IMMEDIATE", "P2_URGENT"]))
    forbid_patterns = settings.get("forbid_patterns", [])
    max_patches = settings.get("max_patches", 10)

    incident_by_id = {i["incident_id"]: i for i in root_cause.get("incidents", [])}

    patches = []
    skipped_recommendations = []
    for rec in recommendations.get("recommendations", []):
        if rec["priority"] not in only_priorities:
            continue
        incident = incident_by_id.get(rec["incident_id"])
        category = incident["root_cause_category"] if incident else "UNDETERMINED"
        mapping = PATCH_TYPE_BY_CATEGORY.get(category)
        if not mapping:
            skipped_recommendations.append({"recommendation_rank": rec["rank"],
                                             "reason": f"root cause category '{category}' has no mechanical patch mapping -- flagged MANUAL_REVIEW_REQUIRED"})
            continue
        patch_type, target_file, risk_level = mapping
        built = build_concrete_patch(category, incident) if incident else None
        if not built:
            skipped_recommendations.append({"recommendation_rank": rec["rank"],
                                             "reason": f"no concrete field/value could be extracted from incident evidence for a {patch_type} diff -- flagged MANUAL_REVIEW_REQUIRED"})
            continue
        diff_text, explanation = built
        if any(p.lower() in diff_text.lower() or p.lower() in target_file.lower() for p in forbid_patterns):
            skipped_recommendations.append({"recommendation_rank": rec["rank"], "reason": "matched a forbidden pattern"})
            continue
        if len(patches) >= max_patches:
            break
        patches.append({
            "patch_id": f"patch_{len(patches)+1:03d}", "incident_id": rec["incident_id"],
            "recommendation_ref": f"rank_{rec['rank']}", "patch_type": patch_type, "risk_level": risk_level,
            "target_file": target_file, "explanation": explanation, "diff": diff_text,
            "requires_human_review": True,
        })

    low_risk = sum(1 for p in patches if p["risk_level"] == "LOW")
    medium_risk = sum(1 for p in patches if p["risk_level"] == "MEDIUM")
    high_risk = sum(1 for p in patches if p["risk_level"] == "HIGH")

    return {
        "summary": {"total_patches_generated": len(patches), "low_risk": low_risk, "medium_risk": medium_risk,
                     "high_risk": high_risk, "manual_review_required": len(skipped_recommendations)},
        "patches": patches,
        "skipped_recommendations": skipped_recommendations,
        "all_patches": patches,
    }


# ---------------------------------------------------------------------------
# Phase 9 -- Report Generation Agent
# ---------------------------------------------------------------------------

SEVERITY_BADGE = {"CRITICAL": "\U0001F534 CRITICAL", "ERROR": "\U0001F7E0 ERROR", "WARN": "\U0001F7E1 WARN", "OK": "\U0001F7E2 OK", "INFO": "\U0001F7E2 OK"}


def cap_keep_critical(rows, key_field, max_top):
    """Cap a table to max_top rows, but never let truncation drop a CRITICAL entry -- per the
    Report Generation Agent's severity-aware truncation rule, all CRITICAL findings for a domain
    must survive the cap even if that means showing fewer/zero lower-severity rows."""
    critical = [r for r in rows if r[key_field] == "CRITICAL"]
    rest = [r for r in rows if r[key_field] != "CRITICAL"]
    return critical + rest[:max(0, max_top - len(critical))]


def md_table(headers, rows, empty_note):
    if not rows:
        return f"_{empty_note}_\n"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        cells = [str(c) if c not in (None, "") else "-" for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def build_markdown_report(dataset_name, normalised, log_analysis, metrics_report, apm_report, security_report,
                           anomaly_report, dependency_report, root_cause, recommendations, patch_suggestions, cfg):
    max_top = cfg.get("max_top_issues_per_section", 5)

    total_critical = (log_analysis["summary"]["total_critical"] + metrics_report["summary"]["critical_issues"] +
                       apm_report["summary"]["critical_issues"] + security_report["summary"]["critical_issues"])
    total_error = log_analysis["summary"]["total_errors"]
    total_warn = (log_analysis["summary"]["total_warnings"] + metrics_report["summary"]["warn_issues"] +
                  apm_report["summary"]["warn_issues"] + security_report["summary"]["warn_issues"])

    if total_critical > 0 or security_report["summary"]["critical_issues"] > 0:
        verdict = "CRITICAL"
    elif total_error > 0 or root_cause["summary"]["medium_confidence"] > 0:
        verdict = "DEGRADED"
    else:
        verdict = "HEALTHY"

    top_risk_candidates = []
    for inc in sorted(root_cause["incidents"], key=lambda i: (i["confidence"] != "HIGH", -i["blast_radius"])):
        top_risk_candidates.append(inc["root_cause_finding"])
    if len(top_risk_candidates) < 3:
        standalone = []
        for f in security_report["findings"]:
            if f["severity"] == "CRITICAL":
                standalone.append(f["description"])
        for t in apm_report["kafka"]["topics"]:
            if t["verdict"] != "OK":
                standalone.append(f"Kafka consumer lag on {t['consumer_group']} at {t['lag']} messages ({t['verdict']})")
        for s in standalone:
            if s not in top_risk_candidates:
                top_risk_candidates.append(s)
    top_risks = []
    for r in top_risk_candidates:
        if r not in top_risks:
            top_risks.append(r)
        if len(top_risks) == 3:
            break

    period = normalised["analysis_period"]
    lines = []
    lines.append("# Datadog Observability Analysis Report")
    lines.append(f"**Dataset:** `{dataset_name}` | **Generated:** {normalised.get('_generated_at', '')}")
    lines.append(f"**Analysis Period:** {period['from']} -> {period['to']}")
    lines.append(f"**Overall Health:** {verdict}")
    lines.append("\n---\n")
    lines.append("## Executive Summary")
    lines.append(f"- Total incidents identified: {root_cause['summary']['total_incidents']}")
    lines.append(f"- Critical issues: {total_critical} | Error issues: {total_error} | Warnings: {total_warn}")
    if top_risks:
        lines.append("- Top risks:")
        for idx, r in enumerate(top_risks, start=1):
            lines.append(f"  {idx}. {r}")
    else:
        lines.append("- Top risks: _none identified -- no CRITICAL/HIGH-confidence findings in this analysis period._")
    lines.append("\n---\n")

    lines.append("## 1. Errors & Data Quality")
    err_rows = sorted(log_analysis["all_errors"], key=lambda e: -SEVERITY_ORDER.get(e["severity"], 0))
    capped_err = cap_keep_critical(err_rows, "severity", max_top)
    lines.append(md_table(["Service", "Issue", "Severity", "Detail"],
                           [(e["service"], e["error_type"], SEVERITY_BADGE.get(e["severity"], e["severity"]), e["message"]) for e in capped_err],
                           "No application errors detected in this analysis period."))
    if len(err_rows) > max_top:
        lines.append(f"_...{len(err_rows) - max_top} more error(s) not shown; see log_analysis.json.all_errors for the full list._\n")
    if log_analysis["worst_columns"]:
        lines.append("**Worst offending DQ columns:**")
        lines.append(md_table(["Column", "Rejection Count", "Rule Type"],
                               [(c["column"], c["rejection_count"], c["rule_type"]) for c in log_analysis["worst_columns"]], "none"))
    else:
        lines.append("_No data-quality rejection evidence found in this analysis period._\n")

    lines.append("## 2. Performance & Infrastructure")
    lat_rows_sorted = sorted(metrics_report["latency"]["by_service"], key=lambda r: -SEVERITY_ORDER.get(r["verdict"], 0))
    lat_rows = cap_keep_critical(lat_rows_sorted, "verdict", max_top)
    lines.append("**Latency by service:**")
    lines.append(md_table(["Service", "Avg (ms)", "P95 (ms)", "P99 (ms)", "Verdict"],
                           [(r["service"], r["avg_ms"], r["p95_ms"], r["p99_ms"], SEVERITY_BADGE.get(r["verdict"], r["verdict"])) for r in lat_rows],
                           "No trace-derived latency data available in this analysis period."))
    if metrics_report["latency"]["slowest_traces"]:
        lines.append("**Slowest traces:**")
        lines.append(md_table(["Service", "Operation", "Duration (ms)", "Timestamp"],
                               [(t["service"], t["operation"], t["duration_ms"], t["timestamp"]) for t in metrics_report["latency"]["slowest_traces"]], "none"))
    else:
        lines.append("_No trace records were present in the input, so no slowest-trace ranking is available._\n")
    host_rows = cap_keep_critical(metrics_report["hosts"], "verdict", max_top)
    lines.append("**Host resource health:**")
    lines.append(md_table(["Host", "CPU %", "Memory %", "Disk %", "Verdict", "Issues"],
                           [(h["host"], h["cpu_pct"], h["memory_pct"], h["disk_pct"], SEVERITY_BADGE.get(h["verdict"], h["verdict"]), ", ".join(h["issues"]) or "-") for h in host_rows],
                           "No infrastructure/host metric data available in this analysis period."))
    if metrics_report["all_issues"]:
        lines.append("**All performance/infra issues:**")
        perf_issues_sorted = sorted(metrics_report["all_issues"], key=lambda i: -SEVERITY_ORDER.get(i["verdict"], 0))
        perf_issues_capped = cap_keep_critical(perf_issues_sorted, "verdict", max_top)
        lines.append(md_table(["Service", "Issue", "Severity"],
                               [(i["service"], i["issue_type"], SEVERITY_BADGE.get(i["verdict"], i["verdict"])) for i in perf_issues_capped], "none"))
        if len(perf_issues_sorted) > len(perf_issues_capped):
            lines.append(f"_...{len(perf_issues_sorted) - len(perf_issues_capped)} more issue(s) not shown; see metrics_report.json.all_issues for the full list._\n")

    lines.append("## 3. Pipeline Health")
    if apm_report["kafka"]["topics"]:
        lines.append("**Kafka topics:**")
        lines.append(md_table(["Topic", "Consumer Group", "Lag", "Verdict"],
                               [(t["topic"] or "(unknown)", t["consumer_group"], t["lag"], SEVERITY_BADGE.get(t["verdict"], t["verdict"])) for t in apm_report["kafka"]["topics"][:max_top]], "none"))
    else:
        lines.append("_No Kafka consumer lag metrics or log evidence were found in this analysis period._\n")
    if apm_report["all_issues"]:
        lines.append("**All pipeline issues:**")
        apm_issues_sorted = sorted(apm_report["all_issues"], key=lambda i: -SEVERITY_ORDER.get(i["verdict"], 0))
        apm_issues_capped = cap_keep_critical(apm_issues_sorted, "verdict", max_top)
        lines.append(md_table(["Service", "Issue", "Severity"],
                               [(i["service"], i["issue_type"], SEVERITY_BADGE.get(i["verdict"], i["verdict"])) for i in apm_issues_capped], "none"))
        if len(apm_issues_sorted) > len(apm_issues_capped):
            lines.append(f"_...{len(apm_issues_sorted) - len(apm_issues_capped)} more issue(s) not shown; see apm_report.json.all_issues for the full list._\n")
    else:
        lines.append("_No checkpoint, SLA-breach, or backlog issues detected._\n")

    lines.append("## 4. Security")
    sec_rows_sorted = sorted(security_report["findings"], key=lambda f: -SEVERITY_ORDER.get(f["severity"], 0))
    sec_rows = cap_keep_critical(sec_rows_sorted, "severity", max_top)
    lines.append(md_table(["Service", "Issue", "Severity", "Detail"],
                           [(f["service"], f["issue_type"], SEVERITY_BADGE.get(f["severity"], f["severity"]), f["description"]) for f in sec_rows],
                           "No security findings (PII exposure, credential leaks, unauthorized access) detected in this analysis period."))

    lines.append("## 5. Anomalies & Trends")
    anom_rows = anomaly_report["anomalies"][:max_top]
    lines.append(md_table(["Service", "Anomaly", "Confidence", "Detail"],
                           [(a["service"], a["anomaly_type"], a["confidence"], a["description"]) for a in anom_rows],
                           "No anomalies (spikes, drops, or correlated multi-service degradation) detected in this analysis period."))

    lines.append("## 6. Dependency & Breakpoint Analysis")
    if dependency_report["dependency_graph"]["nodes"]:
        lines.append(f"Service graph: {len(dependency_report['dependency_graph']['nodes'])} node(s), {len(dependency_report['dependency_graph']['edges'])} edge(s).")
        if dependency_report["breakpoints"]:
            lines.append(md_table(["Breakpoint Service", "Downstream Impact", "Confidence"],
                                   [(b["breakpoint_service"], ", ".join(b["downstream_impact"]), b["confidence"]) for b in dependency_report["breakpoints"]], "none"))
        else:
            lines.append("_No breakpoint met the confidence threshold in this analysis period._\n")
    else:
        lines.append("_No service dependency graph could be built: the input contained no (or insufficient) trace spans to reconstruct call relationships. This is a valid empty result, not a parsing failure._\n")

    lines.append("---\n")
    lines.append("## Root Cause Analysis")
    if root_cause["incidents"]:
        for inc in root_cause["incidents"]:
            lines.append(f"**{inc['incident_id']}** — `{inc['root_cause_category']}` (confidence: {inc['confidence']}, severity: {inc['severity']}, blast radius: {inc['blast_radius']})")
            lines.append(f"- Primary service: {inc['primary_service']}")
            lines.append(f"- Root cause: {inc['root_cause_finding']}")
            if inc["downstream_symptoms"]:
                lines.append(f"- Downstream symptoms: {'; '.join(inc['downstream_symptoms'])}")
            lines.append(f"- Evidence sources: {', '.join(inc['evidence_sources'])}\n")
    else:
        lines.append("_No incidents met the minimum-evidence-sources bar for root cause analysis in this analysis period._\n")
    if root_cause["unresolved_findings"]:
        lines.append(f"⚠️ {len(root_cause['unresolved_findings'])} critical finding(s) could not be clustered into an incident -- see `root_cause.json.unresolved_findings`.\n")

    lines.append("---\n")
    lines.append("## Recommendations")
    if root_cause["incidents"] and not recommendations["recommendations"]:
        lines.append(f"⚠️ {len(root_cause['incidents'])} incident(s) identified but 0 recommendations were generated — verify Root Cause Analysis Agent ran correctly.\n")
    elif recommendations["recommendations"]:
        lines.append(md_table(["Rank", "Priority", "Title", "Affected Services"],
                               [(r["rank"], r["priority"], r["title"], ", ".join(r["affected_services"])) for r in recommendations["recommendations"]], "none"))
    else:
        lines.append("_No recommendations were generated -- no incidents required action in this analysis period._\n")

    lines.append("---\n")
    lines.append("## Patch Suggestions (Human Review Required)")
    lines.append("⚠️ All patches require human review before applying.\n")
    if patch_suggestions["patches"]:
        for p in patch_suggestions["patches"]:
            lines.append(f"**{p['patch_id']}** ({p['risk_level']} risk, `{p['patch_type']}`) — targets `{p['target_file']}`")
            lines.append(f"- {p['explanation']}")
            lines.append(f"```diff\n{p['diff']}\n```\n")
    else:
        lines.append("_No patches were generated in this analysis period._\n")
    if patch_suggestions["skipped_recommendations"]:
        lines.append(f"{len(patch_suggestions['skipped_recommendations'])} recommendation(s) flagged for manual review instead of an automated patch.\n")

    lines.append("---\n")
    lines.append("## Appendix — Ingestion Summary")
    lines.append(md_table(["Source Type", "Record Count"],
                           [(k, v) for k, v in normalised["record_counts"].items() if k != "total"],
                           "none"))
    lines.append(f"Total records ingested: {normalised['record_counts']['total']}")
    if normalised["skipped_files"]:
        lines.append("\n**Skipped/unreadable files:**")
        lines.append(md_table(["File", "Reason"], [(s["path"], s["reason"]) for s in normalised["skipped_files"]], "none"))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Phase 10 -- Output Content Validation Agent
# ---------------------------------------------------------------------------

def run_validation_agent(dataset_name, output_dir, normalised, log_analysis, metrics_report, apm_report,
                          security_report, anomaly_report, dependency_report, root_cause, recommendations,
                          patch_suggestions, report_md, artifact_paths):
    checks = []

    def add(check_id, artifact, status, detail):
        checks.append({"check_id": check_id, "artifact": artifact, "status": status, "detail": detail})

    for name, path in artifact_paths.items():
        inside = str(Path(path).resolve()).startswith(str(Path(output_dir).resolve()))
        add(f"path_{name}", name, "passed" if inside else "failed",
            f"{path} is {'inside' if inside else 'NOT inside'} {output_dir}")

    total_records = len(normalised["records"])
    add("record_count_match", "normalised_data.json", "passed" if normalised["record_counts"]["total"] == total_records else "failed",
        f"record_counts.total={normalised['record_counts']['total']} vs len(records)={total_records}")

    add("schema_summary", "dependency_report.json",
        "passed" if isinstance(dependency_report.get("dependency_graph"), dict) and "nodes" in dependency_report["dependency_graph"] else "failed",
        "dependency_graph is an object with nodes/edges" if isinstance(dependency_report.get("dependency_graph"), dict) else "dependency_graph missing or malformed")

    add("schema_summary", "patch_suggestions.json", "passed" if "patches" in patch_suggestions else "failed",
        "patches[] array present" if "patches" in patch_suggestions else "patches[] array missing")

    baseline_violations = []
    metric_history = defaultdict(list)
    for r in normalised["records"]:
        if r["source_type"] == "metric" and r["timestamp"]:
            metric_history[(r["service"], r["raw"].get("metric_name"))].append(r["timestamp"])
    for a in anomaly_report["anomalies"]:
        if a["anomaly_type"].endswith("_SPIKE") or a["anomaly_type"].endswith("_DROP"):
            if a.get("baseline") == 0:
                prior_count = sum(1 for k, ts in metric_history.items() if k[0] == a["service"] and len([t for t in ts if t < (a["timestamp"] or "")]) >= 2)
                if prior_count:
                    baseline_violations.append(a)
    add("baseline_sanity", "anomaly_report.json", "failed" if baseline_violations else "passed",
        f"{len(baseline_violations)} anomaly(ies) had baseline=0 despite 2+ prior readings" if baseline_violations else
        "no _SPIKE/_DROP anomaly had an unjustified baseline of 0")

    connected_pairs_exist = False
    dep_edges = dependency_report["dependency_graph"]["edges"]
    adjacency = defaultdict(set)
    for e in dep_edges:
        adjacency[e["from"]].add(e["to"])
        adjacency[e["to"]].add(e["from"])
    found_pairs = []
    # A CORRELATED_ANOMALY entry is itself the OUTPUT of a prior correlation pass (it borrows a
    # timestamp from one of the anomalies it summarizes and carries no value/baseline of its own) --
    # it must be excluded from the candidate list before searching for pairs, or it produces
    # misleading self-referential "pairs" against the very anomaly it was derived from.
    anomalies_list = [a for a in anomaly_report["anomalies"] if a.get("anomaly_type") != "CORRELATED_ANOMALY"]
    for i in range(len(anomalies_list)):
        for j in range(i + 1, len(anomalies_list)):
            a, b = anomalies_list[i], anomalies_list[j]
            if a["service"] == b["service"] or b["service"] in adjacency.get(a["service"], set()):
                if a["timestamp"] and b["timestamp"]:
                    ta = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
                    tb = datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00"))
                    if abs((ta - tb).total_seconds()) <= 300:
                        via = "same service" if a["service"] == b["service"] else f"edge {a['service']}<->{b['service']}"
                        found_pairs.append(f"{a['service']}@{a['timestamp']} <-> {b['service']}@{b['timestamp']} via {via}")
    connected_pairs_exist = bool(found_pairs)
    # anomaly_report.json can only compute same-service correlations itself (Phase 5 runs before
    # Phase 6 produces the dependency graph, so it has no way to check cross-service connectivity).
    # A cross-service, graph-connected pair is correctly surfaced instead as a breakpoint in
    # dependency_report.json (Phase 6, which does hold both anomaly_report.json and the graph) -- so
    # either counter satisfies the "don't silently drop a real correlation" intent of this check.
    corr_ok = (not connected_pairs_exist) or anomaly_report["summary"]["correlated_anomalies"] >= 1 or bool(dependency_report["breakpoints"])
    if not connected_pairs_exist:
        corr_detail = "connected anomaly pairs found: 0"
    else:
        corr_detail = (f"connected anomaly pairs found: {len(found_pairs)} ({'; '.join(found_pairs)}); "
                       f"correlated_anomalies recorded: {anomaly_report['summary']['correlated_anomalies']}; "
                       f"dependency breakpoints recorded: {len(dependency_report['breakpoints'])}")
    add("correlation_completeness", "anomaly_report.json / dependency_report.json", "passed" if corr_ok else "failed", corr_detail)

    critical_kafka_or_checkpoint = (
        [t for t in apm_report.get("kafka", {}).get("topics", []) if t.get("verdict") == "CRITICAL"] +
        [c for c in apm_report.get("checkpoints", []) if c.get("severity") == "CRITICAL"]
    )
    categories_present = {i["root_cause_category"] for i in root_cause.get("incidents", [])}
    pbp_ok = (not critical_kafka_or_checkpoint) or "PIPELINE_BACKPRESSURE" in categories_present
    add("pipeline_backpressure_category", "apm_report.json / root_cause.json", "passed" if pbp_ok else "failed",
        "no CRITICAL kafka/checkpoint findings in apm_report.json" if not critical_kafka_or_checkpoint
        else (f"{len(critical_kafka_or_checkpoint)} CRITICAL kafka/checkpoint finding(s) matched by a PIPELINE_BACKPRESSURE incident" if pbp_ok
              else f"CRITICAL kafka/checkpoint finding(s) present ({critical_kafka_or_checkpoint}) but no PIPELINE_BACKPRESSURE incident in root_cause.json (categories present: {sorted(categories_present)})"))

    # Shared evidence source for the two bidirectionality-aware checks below: any CRITICAL/ERROR
    # finding on any service, from any of the four upstream reports, with a real timestamp.
    evidence_by_service = defaultdict(list)
    for a in anomaly_report.get("anomalies", []):
        if a.get("timestamp") and a.get("anomaly_type") != "CORRELATED_ANOMALY":
            evidence_by_service[a["service"]].append(a["timestamp"])
    for i in metrics_report.get("all_issues", []):
        if i.get("timestamp") and i.get("verdict") in ("CRITICAL", "ERROR"):
            evidence_by_service[i["service"]].append(i["timestamp"])
    for e in log_analysis.get("all_errors", []):
        if e.get("timestamp") and e.get("severity") in ("CRITICAL", "ERROR"):
            evidence_by_service[e["service"]].append(e["timestamp"])
    for i in apm_report.get("all_issues", []):
        if i.get("timestamp") and i.get("verdict") in ("CRITICAL", "ERROR"):
            evidence_by_service[i["service"]].append(i["timestamp"])

    # An inward edge (from == X, to == breakpoint_service) with X in downstream_impact is only a
    # genuine contradiction if X's presence there is NOT explained by the bidirectional-impact rule --
    # i.e. X has no qualifying evidence after the breakpoint's own earliest evidence. If X does have
    # such evidence, X is a legitimate caller-side symptom (per dependency-flow-analysis-agent.md's
    # bidirectional traversal rule) and must NOT be flagged here.
    contradiction = None
    for bp in dependency_report["breakpoints"]:
        bp_onset = min(evidence_by_service.get(bp["breakpoint_service"], []), default=None)
        for e in dep_edges:
            if e["to"] == bp["breakpoint_service"] and e["from"] in bp["downstream_impact"]:
                caller_ts = [ts for ts in evidence_by_service.get(e["from"], []) if ts]
                justified = bp_onset and any(ts > bp_onset for ts in caller_ts)
                if not justified:
                    contradiction = e
    add("breakpoint_edge_consistency", "dependency_report.json", "failed" if contradiction else "passed",
        f"edge {contradiction} contradicts breakpoint direction with no justifying caller-side evidence" if contradiction
        else "no breakpoint direction contradicted by dependency_graph.edges (inward edges from services with their own post-onset evidence are expected, not contradictions)")

    missing_bidirectional = []
    for bp in dependency_report["breakpoints"]:
        bp_onset = min(evidence_by_service.get(bp["breakpoint_service"], []), default=None)
        if not bp_onset:
            continue
        for e in dep_edges:
            if e["to"] != bp["breakpoint_service"]:
                continue
            caller = e["from"]
            if caller in bp["downstream_impact"]:
                continue
            caller_ts = [ts for ts in evidence_by_service.get(caller, []) if ts]
            if any(ts > bp_onset for ts in caller_ts):
                missing_bidirectional.append((bp["breakpoint_service"], caller))
    add("breakpoint_impact_bidirectionality", "dependency_report.json", "failed" if missing_bidirectional else "passed",
        f"caller-side service(s) with post-onset evidence missing from downstream_impact: {missing_bidirectional}" if missing_bidirectional
        else "every inward-edge caller with post-onset evidence is present in its breakpoint's downstream_impact")

    coverage_gaps = []
    metric_series_check = defaultdict(list)
    for r in normalised["records"]:
        if r["source_type"] == "metric" and r["timestamp"]:
            role = metric_role(r["raw"].get("metric_name")) or r["raw"].get("metric_name")
            metric_series_check[(r["service"], role)].append((r["timestamp"], r["raw"].get("value")))
    for (service, role), points in metric_series_check.items():
        points = sorted(p for p in points if p[1] is not None)
        if len(points) < 3:
            continue
        values = [v for _, v in points]
        series_best = None
        for i in range(1, len(points)):
            baseline = sum(values[:i]) / i
            ts, val = points[i]
            if baseline > 0 and val > 2.0 * baseline:
                if series_best is None or val > series_best[1]:
                    series_best = (ts, val)
        if series_best is not None:
            ts, _ = series_best
            covered = any(a["service"] == service and a["timestamp"] == ts for a in anomaly_report["anomalies"])
            if not covered:
                coverage_gaps.append(f"{service}/{role}@{ts}")
    add("anomaly_coverage_completeness", "anomaly_report.json", "failed" if coverage_gaps else "passed",
        f"qualifying deviation(s) with no anomaly entry: {coverage_gaps}" if coverage_gaps
        else "every metric series with a computable baseline and a qualifying deviation has a matching anomaly entry")

    table_issues = []
    for line in report_md.split("\n"):
        if line.strip().startswith("|") and "---" not in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 1 and cells[0].isdigit():
                table_issues.append(line)
    add("markdown_table_integrity", "datadog_analysis_report.md", "failed" if table_issues else "passed",
        f"{len(table_issues)} malformed row(s) found" if table_issues else "no malformed table rows found")

    missing_critical = []
    for source_name, report in (("security_report.json", security_report), ("metrics_report.json", metrics_report), ("apm_report.json", apm_report)):
        items = report.get("findings") if source_name == "security_report.json" else report.get("all_issues", [])
        for item in items or []:
            sev = item.get("severity") or item.get("verdict")
            if sev == "CRITICAL":
                needle_issue = item.get("issue_type", "")
                needle_service = item.get("service", "")
                if needle_issue and needle_service and not (needle_issue in report_md and needle_service in report_md):
                    missing_critical.append((source_name, needle_issue, needle_service))
    add("markdown_severity_completeness", "datadog_analysis_report.md", "failed" if missing_critical else "passed",
        f"missing from report: {missing_critical}" if missing_critical else "every CRITICAL finding is represented in the markdown report")

    unredacted = []
    haystacks = {"security_report.json": json.dumps(security_report), "datadog_analysis_report.md": report_md}
    for name, text in haystacks.items():
        for pattern in PII_VALUE_PATTERNS.values():
            if re.search(pattern, text.replace("[REDACTED]", "")):
                if re.search(pattern, text):
                    unredacted.append(name)
        for cred in CREDENTIAL_PATTERNS:
            if cred in text and "[REDACTED]" not in text[text.find(cred):text.find(cred) + len(cred) + 20]:
                unredacted.append(name)
    unredacted = list(set(unredacted))
    add("redaction_check", "security_report.json / datadog_analysis_report.md", "failed" if unredacted else "passed",
        f"unredacted PII/credential-like value found in: {unredacted}" if unredacted else "no unredacted PII/credential values found")

    manual_review_ok = patch_suggestions["summary"]["manual_review_required"] == len(patch_suggestions["skipped_recommendations"])
    add("manual_review_required_check", "patch_suggestions.json", "passed" if manual_review_ok else "failed",
        "manual_review_required equals len(skipped_recommendations)" if manual_review_ok else "manual_review_required mismatch")

    failed_checks = [c for c in checks if c["status"] == "failed"]
    status = "invalid" if failed_checks else "valid"

    return {
        "dataset_name": dataset_name,
        "status": status,
        "checks": checks,
        "failed_checks": failed_checks,
        "artifact_paths": [{"artifact": k, "path": v} for k, v in artifact_paths.items()],
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--dataset-name", default=None)
    args = parser.parse_args()

    input_root = Path(args.input_root)
    dataset_name = args.dataset_name or input_root.name
    output_dir = Path(args.output_root) / dataset_name
    output_dir.mkdir(parents=True, exist_ok=True)

    def write_json(name, obj):
        path = output_dir / name
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    normalised, _ = ingest_folder(input_root)
    normalised["dataset_name"] = dataset_name
    normalised["_generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    normalised_path = write_json("normalised_data.json", normalised)

    log_analysis = run_error_dq_agent(normalised, {
        "error_threshold": "ERROR", "recurring_threshold": 3, "top_errors_limit": 10,
        "dq_thresholds": {"rejection_rate_warn_pct": 10, "rejection_rate_critical_pct": 25,
                           "quarantine_volume_warn": 1000, "dead_letter_volume_warn": 500, "dq_alert_frequency_warn": 5},
    })
    log_analysis_path = write_json("log_analysis.json", log_analysis)

    metrics_report = run_performance_infra_agent(normalised, {
        "performance_thresholds": {"latency_warn_ms": 500, "latency_critical_ms": 1000, "throughput_drop_pct": 30,
                                    "error_rate_warn_pct": 5, "error_rate_critical_pct": 15},
        "infra_thresholds": {"cpu_warn_pct": 75, "cpu_critical_pct": 90, "memory_warn_pct": 75, "memory_critical_pct": 90,
                              "disk_warn_pct": 80, "disk_critical_pct": 95, "network_saturation_mbps": 900,
                              "small_files_warn": 1000, "vacuum_overdue_hours": 168, "host_downtime_warn_min": 5},
    })
    metrics_report_path = write_json("metrics_report.json", metrics_report)

    apm_report = run_pipeline_health_agent(normalised, {
        "thresholds": {"kafka_lag_warn": 10000, "kafka_lag_critical": 100000, "checkpoint_age_warn_min": 30,
                        "sla_breach_threshold_ms": 300000, "backlog_warn_records": 50000, "missed_triggers_warn": 3},
    })
    apm_report_path = write_json("apm_report.json", apm_report)

    security_report = run_security_audit_agent(normalised, {"auth_failure_threshold": 5})
    security_report_path = write_json("security_report.json", security_report)

    anomaly_report = run_anomaly_detection_agent(log_analysis, metrics_report, apm_report, security_report, normalised, {
        "sensitivity": "medium", "spike_multiplier": 2.0, "drop_multiplier": 0.5, "trend_window_batches": 5,
        "min_data_points": 3,
    })
    anomaly_report_path = write_json("anomaly_report.json", anomaly_report)

    dependency_report = run_dependency_flow_agent(normalised, metrics_report, apm_report, anomaly_report, log_analysis, {
        "min_call_count_for_edge": 5, "breakpoint_confidence_threshold": 0.6, "max_hops_upstream": 5,
        "include_external_calls": True,
    })
    dependency_report_path = write_json("dependency_report.json", dependency_report)

    root_cause, recommendations = run_root_cause_agent(log_analysis, metrics_report, apm_report, security_report,
                                                          anomaly_report, dependency_report, {
        "max_recommendations": 10, "min_evidence_sources": 2, "correlation_window_minutes": 5,
        "confidence_high_threshold": 3, "confidence_medium_threshold": 2,
    })
    root_cause_path = write_json("root_cause.json", root_cause)
    recommendations_path = write_json("recommendations.json", recommendations)

    patch_suggestions = run_code_patch_agent(recommendations, root_cause, {
        "repo_path": ".", "max_patches": 10, "only_patch_priorities": ["P1_IMMEDIATE", "P2_URGENT"],
        "require_human_review": True, "allow_config_changes": True, "allow_code_changes": True,
        "forbid_patterns": ["DROP TABLE", "rm -rf", "DELETE FROM", "credentials", "secret_key"],
    })
    patch_suggestions_path = write_json("patch_suggestions.json", patch_suggestions)

    report_md = build_markdown_report(dataset_name, normalised, log_analysis, metrics_report, apm_report,
                                        security_report, anomaly_report, dependency_report, root_cause,
                                        recommendations, patch_suggestions, {"max_top_issues_per_section": 5})
    report_path = output_dir / "datadog_analysis_report.md"
    report_path.write_text(report_md, encoding="utf-8")

    artifact_paths = {
        "normalised_data.json": normalised_path, "log_analysis.json": log_analysis_path,
        "metrics_report.json": metrics_report_path, "apm_report.json": apm_report_path,
        "security_report.json": security_report_path, "anomaly_report.json": anomaly_report_path,
        "dependency_report.json": dependency_report_path, "root_cause.json": root_cause_path,
        "recommendations.json": recommendations_path, "patch_suggestions.json": patch_suggestions_path,
        "datadog_analysis_report.md": str(report_path),
    }

    validation_manifest = run_validation_agent(dataset_name, output_dir, normalised, log_analysis, metrics_report,
                                                 apm_report, security_report, anomaly_report, dependency_report,
                                                 root_cause, recommendations, patch_suggestions, report_md, artifact_paths)
    write_json("validation_manifest.json", validation_manifest)

    print(f"Pipeline complete. status={validation_manifest['status']}")
    print(json.dumps({"record_counts": normalised["record_counts"]}, indent=2))
    if validation_manifest["failed_checks"]:
        print("FAILED CHECKS:")
        for c in validation_manifest["failed_checks"]:
            print(f"  - {c['check_id']} ({c['artifact']}): {c['detail']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
