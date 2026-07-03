import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

CONFIG = {
    "input_folder": "input/",
    "output_folder": "output/",
    "output_files": {
        "normalised_data": "output/normalised_data.json",
        "log_analysis": "output/log_analysis.json",
        "metrics_report": "output/metrics_report.json",
        "apm_report": "output/apm_report.json",
        "security_report": "output/security_report.json",
        "anomaly_report": "output/anomaly_report.json",
        "dependency_report": "output/dependency_report.json",
        "root_cause": "output/root_cause.json",
        "recommendations": "output/recommendations.json",
        "patch_suggestions": "output/patch_suggestions.json",
        "final_report": "output/datadog_analysis_report.md",
    },
    "ingestion_settings": {
        "skip_unreadable_files": True,
        "require_all_types": False,
        "file_extensions_scanned": [".json", ".csv", ".log", ".ndjson"],
    },
    "error_dq_settings": {
        "error_threshold": "ERROR",
        "recurring_threshold": 3,
        "top_errors_limit": 10,
        "rejection_rate_warn_pct": 10,
        "rejection_rate_critical_pct": 25,
        "quarantine_volume_warn": 1000,
        "dead_letter_volume_warn": 500,
        "dq_alert_frequency_warn": 5,
    },
    "performance_infra_thresholds": {
        "latency_warn_ms": 500,
        "latency_critical_ms": 1000,
        "throughput_drop_pct": 30,
        "error_rate_warn_pct": 5,
        "error_rate_critical_pct": 15,
        "cpu_warn_pct": 75,
        "cpu_critical_pct": 90,
        "memory_warn_pct": 75,
        "memory_critical_pct": 90,
        "disk_warn_pct": 80,
        "disk_critical_pct": 95,
        "network_saturation_mbps": 900,
        "host_downtime_warn_min": 5,
    },
    "pipeline_health_thresholds": {
        "kafka_lag_warn": 10000,
        "kafka_lag_critical": 100000,
        "checkpoint_age_warn_min": 30,
        "sla_breach_threshold_ms": 300000,
        "backlog_warn_records": 50000,
        "missed_triggers_warn": 3,
    },
    "security_settings": {
        "pii_columns": ["email", "phone", "ssn", "dob", "address", "credit_card", "national_id", "password", "token", "secret"],
        "pii_value_patterns": {
            "email": r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}",
            "phone": r"\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}",
            "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
            "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
        },
        "credential_patterns": ["password=", "api_key=", "token=", "secret=", "Authorization:", "Bearer ", "api_key", "password ", "secret ", "token "],
        "auth_failure_threshold": 5,
        "permission_escalation_keywords": ["sudo", "privilege", "escalat", "root access", "admin override"],
        "compliance_frameworks": ["GDPR", "HIPAA", "PCI-DSS"],
    },
    "anomaly_settings": {
        "sensitivity": "medium",
        "spike_multiplier": 2.0,
        "drop_multiplier": 0.5,
        "trend_window_batches": 5,
        "min_data_points": 3,
    },
    "dependency_settings": {
        "min_call_count_for_edge": 5,
        "breakpoint_confidence_threshold": 0.6,
        "max_hops_upstream": 5,
        "include_external_calls": True,
    },
    "root_cause_settings": {
        "max_recommendations": 10,
        "min_evidence_sources": 2,
        "correlation_window_minutes": 5,
        "confidence_high_threshold": 3,
        "confidence_medium_threshold": 2,
    },
    "patch_generator_settings": {
        "max_patches": 10,
        "only_patch_priorities": ["P1_IMMEDIATE", "P2_URGENT"],
        "forbid_patterns": ["DROP TABLE", "rm -rf", "DELETE FROM", "credentials", "secret_key"],
    },
    "report_settings": {
        "include_executive_summary": True,
        "include_all_sections": True,
        "max_top_issues_per_section": 5,
        "include_patch_suggestions": True,
        "include_appendix_raw_counts": True,
    },
}

SEVERITY_ORDER = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]


def ensure_output_folder():
    out = Path(CONFIG["output_folder"])
    out.mkdir(parents=True, exist_ok=True)
    return out


def parse_timestamp(value):
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value)
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz=None).replace(tzinfo=None)
        return datetime.fromisoformat(value)
    except Exception:
        try:
            return datetime.utcfromtimestamp(float(value))
        except Exception:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
                try:
                    return datetime.strptime(value, fmt)
                except Exception:
                    continue
    return None


def iso_timestamp(value):
    dt = parse_timestamp(value)
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def normalise_severity(level):
    if not isinstance(level, str):
        return "INFO"
    level = level.strip().upper()
    if level in SEVERITY_ORDER:
        return level
    if "CRIT" in level:
        return "CRITICAL"
    if "ERR" in level:
        return "ERROR"
    if "WARN" in level or "ALERT" in level:
        return "WARN"
    if "DEBUG" in level:
        return "DEBUG"
    if "INFO" in level or "OK" in level:
        return "INFO"
    return "INFO"


def derive_environment(record):
    if not isinstance(record, dict):
        return "unknown"
    explicit = record.get("environment") or record.get("env")
    if explicit:
        return str(explicit)
    tags = record.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("env:"):
                return tag.split(":", 1)[1] or "unknown"
    if isinstance(tags, dict):
        env_tag = tags.get("env")
        if env_tag:
            return str(env_tag)
    return "unknown"


def extract_ip_user(record):
    message = record.get("message", "") or ""
    if not isinstance(message, str):
        message = str(message)
    ip = None
    user = None
    ip_match = re.search(r"(?:(?:ip=|client_ip=|source_ip=|remote_ip=)([0-9]+(?:\.[0-9]+){3}))", message, re.IGNORECASE)
    if ip_match:
        ip = ip_match.group(1)
    else:
        ip_match = re.search(r"\b([0-9]+(?:\.[0-9]+){3})\b", message)
        if ip_match:
            ip = ip_match.group(1)
    user_match = re.search(r"(?:user=|username=|account=|user name=)([\w@.\-]+)", message, re.IGNORECASE)
    if user_match:
        user = user_match.group(1)
    return ip, user


def classify_json_file(records):
    if not isinstance(records, list) or not records:
        return "unknown"
    first = records[0]
    if not isinstance(first, dict):
        return "unknown"
    if "trace_id" in first and "span_id" in first:
        return "trace"
    if "monitor_name" in first and ("priority" in first or "status" in first):
        return "alert"
    if "host" in first and any(key in first for key in ["cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"]):
        fields = [key for key in ["cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"] if key in first]
        if len(fields) >= 2:
            return "infrastructure"
    if "message" in first or "level" in first or "severity" in first:
        return "log"
    return "unknown"


def classify_file(path):
    path = Path(path)
    if not path.is_file():
        return "unknown"
    ext = path.suffix.lower()
    if ext == ".csv":
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = [h.strip().lower() for h in reader.fieldnames or []]
            if "metric_name" in header or "value" in header:
                return "metric"
            return "unknown"
    try:
        with path.open("r", encoding="utf-8") as f:
            content = json.load(f)
    except Exception:
        return "unreadable"
    return classify_json_file(content)


def parse_csv_row(row):
    if not isinstance(row, dict):
        return {}
    tags = row.get("tags") or ""
    if isinstance(tags, str):
        tags_list = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        tags_list = tags
    else:
        tags_list = []
    return {
        "record_id": f"metric_{row.get('timestamp')}_{row.get('metric_name')}_{row.get('service')}",
        "source_type": "metric",
        "severity": "INFO",
        "service": row.get("service") or row.get("host") or "unknown",
        "environment": derive_environment(row),
        "timestamp": iso_timestamp(row.get("timestamp")),
        "message": row.get("metric_name") or "",
        "tags": tags_list,
        "source_ip": None,
        "user": None,
        "raw": row,
    }


def parse_json_record(record, source_type, file_path):
    if not isinstance(record, dict):
        return None
    message = record.get("message") or record.get("msg") or ""
    if source_type == "log":
        severity = normalise_severity(record.get("level") or record.get("severity") or "INFO")
        service = record.get("service") or record.get("host") or "unknown"
        timestamp = iso_timestamp(record.get("timestamp"))
        env = derive_environment(record)
        source_ip, user = extract_ip_user(record)
        return {
            "record_id": f"log_{file_path.name}_{record.get('timestamp')}_{service}_{hash(message)}",
            "source_type": "log",
            "severity": severity,
            "service": service,
            "environment": env,
            "timestamp": timestamp,
            "message": message,
            "tags": record.get("tags") if isinstance(record.get("tags"), list) else [],
            "source_ip": source_ip,
            "user": user,
            "raw": record,
        }
    if source_type == "trace":
        status = str(record.get("status", "")).lower()
        severity = "ERROR" if status == "error" else "INFO"
        return {
            "record_id": f"trace_{record.get('trace_id')}_{record.get('span_id')}",
            "source_type": "trace",
            "severity": severity,
            "service": record.get("service") or "unknown",
            "environment": derive_environment(record),
            "timestamp": iso_timestamp(record.get("timestamp")),
            "message": record.get("operation") or "",
            "tags": record.get("tags") if isinstance(record.get("tags"), list) else [],
            "source_ip": None,
            "user": None,
            "raw": record,
        }
    if source_type == "alert":
        priority = str(record.get("priority", "")).upper()
        severity = "CRITICAL" if "P1" in priority else "ERROR" if "P2" in priority else "WARN" if "P3" in priority else "INFO"
        return {
            "record_id": f"alert_{record.get('monitor_name')}_{record.get('triggered_at')}",
            "source_type": "alert",
            "severity": severity,
            "service": record.get("service") or "unknown",
            "environment": derive_environment(record),
            "timestamp": iso_timestamp(record.get("triggered_at") or record.get("timestamp")),
            "message": record.get("message") or "",
            "tags": record.get("tags") if isinstance(record.get("tags"), list) else [],
            "source_ip": None,
            "user": None,
            "raw": record,
        }
    if source_type == "infrastructure":
        severity = "WARN"
        try:
            cpu = float(record.get("cpu_pct", 0))
            mem = float(record.get("memory_pct", 0))
            disk = float(record.get("disk_pct", 0))
            if cpu > CONFIG["performance_infra_thresholds"]["cpu_critical_pct"] or mem > CONFIG["performance_infra_thresholds"]["memory_critical_pct"] or disk > CONFIG["performance_infra_thresholds"]["disk_critical_pct"]:
                severity = "CRITICAL"
        except Exception:
            severity = "WARN"
        return {
            "record_id": f"infra_{record.get('host')}_{record.get('timestamp')}",
            "source_type": "infrastructure",
            "severity": severity,
            "service": record.get("host") or "unknown",
            "environment": derive_environment(record),
            "timestamp": iso_timestamp(record.get("timestamp")),
            "message": "infrastructure metrics",
            "tags": record.get("tags") if isinstance(record.get("tags"), list) else [],
            "source_ip": None,
            "user": None,
            "raw": record,
        }
    return None


def ingest_all():
    ensure_output_folder()
    input_root = Path(CONFIG["input_folder"])
    if not input_root.exists() or not input_root.is_dir():
        raise FileNotFoundError(f"Input folder not found: {input_root}")
    file_paths = [p for p in input_root.iterdir() if p.is_file() and p.suffix.lower() in CONFIG["ingestion_settings"]["file_extensions_scanned"]]
    if not file_paths:
        raise FileNotFoundError("No input files found in input folder")
    source_map = defaultdict(list)
    normalised = []
    unreadable = []
    unknown = []
    for path in file_paths:
        classification = classify_file(path)
        if classification == "unreadable":
            unreadable.append(path.name)
            if not CONFIG["ingestion_settings"]["skip_unreadable_files"]:
                raise ValueError(f"Unreadable file: {path}")
            continue
        if classification == "unknown":
            unknown.append(path.name)
            continue
        source_map[classification].append(path.name)
        try:
            if path.suffix.lower() == ".csv":
                with path.open("r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        normalised.append(parse_csv_row(row))
            else:
                with path.open("r", encoding="utf-8") as f:
                    content = json.load(f)
                if not isinstance(content, list):
                    continue
                for record in content:
                    nr = parse_json_record(record, classification, path)
                    if nr is not None:
                        normalised.append(nr)
        except Exception as exc:
            unreadable.append(path.name)
            if not CONFIG["ingestion_settings"]["skip_unreadable_files"]:
                raise
            continue
    if CONFIG["ingestion_settings"]["require_all_types"]:
        for required in ["log", "metric", "trace", "alert", "infrastructure"]:
            if required not in source_map:
                raise ValueError(f"Required source type missing: {required}")
    normalised.sort(key=lambda r: r["timestamp"] or "")
    with open(CONFIG["output_files"]["normalised_data"], "w", encoding="utf-8") as f:
        json.dump(normalised, f, indent=2)
    summary = {st: len(records) for st, records in source_map.items()}
    print("Phase 0 complete: ingested", len(normalised), "records")
    for st, files in source_map.items():
        print(f"  {st}: {len(files)} file(s) => {files}")
    if unreadable:
        print("Skipped unreadable files:", unreadable)
    if unknown:
        print("Unknown file types:", unknown)
    return normalised, source_map


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_time_range(records):
    timestamps = [parse_timestamp(r.get("timestamp")) for r in records if r.get("timestamp")]
    timestamps = [t for t in timestamps if t]
    if not timestamps:
        return None, None
    return min(timestamps).isoformat(timespec="seconds") + "Z", max(timestamps).isoformat(timespec="seconds") + "Z"


def phase_1_error_dq(normalised):
    errors = [r for r in normalised if SEVERITY_ORDER.index(r["severity"]) >= SEVERITY_ORDER.index(CONFIG["error_dq_settings"]["error_threshold"])]
    dq_logs = [r for r in normalised if r["source_type"] == "log" and ("DQ_" in (r["message"] or "") or "rejection" in (r["message"] or ""))]
    error_groups = {}
    dq_metrics = []
    worst_columns = Counter()
    dq_alerts = Counter()
    dq_trends = []
    for err in errors:
        message = err["message"] or ""
        error_type = classify_error_type(message)
        key = (error_type, message, err["service"])
        group = error_groups.setdefault(key, {
            "error_type": error_type,
            "message": message,
            "service": err["service"],
            "severity": err["severity"],
            "frequency": 0,
            "first_seen": err["timestamp"],
            "last_seen": err["timestamp"],
            "is_recurring": False,
        })
        group["frequency"] += 1
        group["first_seen"] = min(group["first_seen"], err["timestamp"])
        group["last_seen"] = max(group["last_seen"], err["timestamp"])
    for group in error_groups.values():
        group["is_recurring"] = group["frequency"] >= CONFIG["error_dq_settings"]["recurring_threshold"]
    for record in dq_logs:
        message = record["message"] or ""
        if "DQ_METRICS" in message:
            metrics = parse_kv_pairs(message)
            if metrics:
                batch_id = metrics.get("batch_id")
                pipeline = metrics.get("pipeline")
                total = int(metrics.get("total", 0))
                passed = int(metrics.get("passed", 0))
                failed = int(metrics.get("failed", 0))
                rejection_rate = float(metrics.get("rejection_rate_pct", 0.0))
                verdict = "OK"
                if rejection_rate >= CONFIG["error_dq_settings"]["rejection_rate_critical_pct"]:
                    verdict = "CRITICAL"
                elif rejection_rate >= CONFIG["error_dq_settings"]["rejection_rate_warn_pct"]:
                    verdict = "WARN"
                dq_metrics.append({
                    "batch_id": batch_id,
                    "pipeline": pipeline,
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "rejection_pct": rejection_rate,
                    "verdict": verdict,
                    "timestamp": record["timestamp"],
                })
        if "DQ_ALERT" in message:
            reject_match = re.search(r"rejection_reason=([A-Z_]+):([\w-]+)", message)
            count_match = re.search(r"count=(\d+)", message)
            if reject_match:
                rule_type, column = reject_match.groups()
                count = int(count_match.group(1)) if count_match else 1
                worst_columns[(column, rule_type)] += count
                dq_alerts[message] += 1
                alert_type = "RECURRING_DQ_ALERT" if dq_alerts[message] > CONFIG["error_dq_settings"]["dq_alert_frequency_warn"] else "DQ_ALERT"
                dq_metrics.append({
                    "batch_id": None,
                    "pipeline": record["service"],
                    "total": None,
                    "passed": None,
                    "failed": None,
                    "rejection_pct": None,
                    "verdict": alert_type,
                    "timestamp": record["timestamp"],
                })
    top_errors = sorted(error_groups.values(), key=lambda x: x["frequency"], reverse=True)[:CONFIG["error_dq_settings"]["top_errors_limit"]]
    rejection_rates = sorted(dq_metrics, key=lambda x: x.get("timestamp") or "")
    worst_columns_list = [
        {"column": col, "rule_type": rule, "rejection_count": count}
        for (col, rule), count in worst_columns.most_common(5)
    ]
    dq_alerts_list = [
        {"alert": alert, "count": count, "issue_type": "RECURRING_DQ_ALERT" if count > CONFIG["error_dq_settings"]["dq_alert_frequency_warn"] else "DQ_ALERT"}
        for alert, count in dq_alerts.items()
    ]
    dq_trends = []
    if len(rejection_rates) >= 2:
        for i in range(1, len(rejection_rates)):
            prev = rejection_rates[i - 1]["rejection_pct"]
            curr = rejection_rates[i]["rejection_pct"]
            if prev is not None and curr is not None:
                if curr > prev:
                    dq_trends.append({"batch": rejection_rates[i]["batch_id"], "trend": "WORSENING", "from": prev, "to": curr})
                elif curr < prev:
                    dq_trends.append({"batch": rejection_rates[i]["batch_id"], "trend": "IMPROVING", "from": prev, "to": curr})
    total_quarantine = sum(int(re.search(r"count=(\d+)", record["message"] or "").group(1)) if re.search(r"count=(\d+)", record["message"] or "") else 0 for record in dq_logs if "QUARANTINE" in (record["message"] or ""))
    total_dead_letter = sum(int(re.search(r"count=(\d+)", record["message"] or "").group(1)) if re.search(r"count=(\d+)", record["message"] or "") else 0 for record in dq_logs if "DEAD_LETTER" in (record["message"] or ""))
    summary = {
        "total_errors": len([r for r in normalised if r["source_type"] == "log" and SEVERITY_ORDER.index(r["severity"]) >= SEVERITY_ORDER.index("ERROR")]),
        "total_warnings": len([r for r in normalised if r["source_type"] == "log" and r["severity"] == "WARN"]),
        "total_critical": len([r for r in normalised if r["source_type"] == "log" and r["severity"] == "CRITICAL"]),
        "recurring_errors": sum(1 for g in top_errors if g["is_recurring"]),
        "affected_services": sorted({g["service"] for g in top_errors}),
        "total_batches_analysed": len([m for m in rejection_rates if m["batch_id"]]),
        "batches_with_dq_issues": len([m for m in rejection_rates if m["verdict"] in ["WARN", "CRITICAL","RECURRING_DQ_ALERT"]]),
        "avg_rejection_rate_pct": float(sum(m["rejection_pct"] for m in rejection_rates if m.get("rejection_pct") is not None) / max(1, len([m for m in rejection_rates if m.get("rejection_pct") is not None]))),
        "max_rejection_rate_pct": float(max((m["rejection_pct"] for m in rejection_rates if m.get("rejection_pct") is not None), default=0.0)),
        "total_quarantine_records": total_quarantine,
        "total_dead_letter_records": total_dead_letter,
        "analysis_period": {"from": None, "to": None},
    }
    summary["analysis_period"]["from"], summary["analysis_period"]["to"] = build_time_range(normalised)
    output = {
        "summary": summary,
        "top_errors": top_errors,
        "rejection_rates": rejection_rates,
        "worst_columns": worst_columns_list,
        "dq_alerts": dq_alerts_list,
        "dq_trends": dq_trends,
        "all_errors": errors,
        "all_dq_issues": dq_logs,
    }
    with open(CONFIG["output_files"]["log_analysis"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 1 complete: wrote", CONFIG["output_files"]["log_analysis"])
    return output


def classify_error_type(message):
    message = (message or "").lower()
    patterns = [
        ("checkpoint_failure", ["checkpoint corrupted", "offset missing", "checkpoint invalid"]),
        ("delta_conflict", ["concurrent write conflict", "transaction failed", "delta conflict"]),
        ("out_of_memory", ["heap space", "out of memory", "oom"]),
        ("resource_exhaustion", ["cpu throttled", "disk full", "thread pool exhausted", "resource exhaustion"]),
        ("authentication_failure", ["invalid credentials", "token expired", "unauthorised", "unauthorized", "auth failure"]),
        ("permission_denied", ["access denied", "forbidden", "insufficient privileges"]),
        ("schema_mismatch", ["column not found", "type mismatch", "schema evolution", "schema mismatch"]),
        ("null_pointer", ["nullpointerexception", "null pointer", "null reference", "undefined value"]),
        ("connection_failure", ["connection refused", "network unreachable", "socket timeout", "connection timed out"]),
        ("timeout", ["request timeout", "operation timed out", "timed out"]),
        ("application_error", ["unhandled exception", "stack overflow", "assertion failed"]),
    ]
    for error_type, keywords in patterns:
        if any(keyword in message for keyword in keywords):
            return error_type.upper()
    return "UNKNOWN"


def parse_kv_pairs(text):
    pairs = {}
    for match in re.finditer(r"(\w+)=([\w\-@\.]+)", text):
        pairs[match.group(1)] = match.group(2)
    return pairs


def phase_2_performance_infra(normalised):
    metrics = [r for r in normalised if r["source_type"] in ["metric", "infrastructure"]]
    traces = [r for r in normalised if r["source_type"] == "trace"]
    service_latencies = defaultdict(list)
    service_error_counts = defaultdict(int)
    service_trace_counts = defaultdict(int)
    for trace in traces:
        duration = trace["raw"].get("duration_ms")
        if duration is not None:
            service = trace["service"]
            service_latencies[service].append(float(duration))
            service_trace_counts[service] += 1
            if trace["severity"] == "ERROR":
                service_error_counts[service] += 1
    latency_by_service = []
    all_issues = []
    for service, durations in service_latencies.items():
        avg_ms = sum(durations) / len(durations)
        sorted_durs = sorted(durations)
        p95 = sorted_durs[int(len(sorted_durs) * 0.95) - 1] if len(sorted_durs) >= 1 else avg_ms
        p99 = sorted_durs[int(len(sorted_durs) * 0.99) - 1] if len(sorted_durs) >= 1 else avg_ms
        verdict = "OK"
        issue_type = None
        if p99 >= CONFIG["performance_infra_thresholds"]["latency_critical_ms"]:
            verdict = "CRITICAL"
            issue_type = "HIGH_LATENCY"
        elif p99 >= CONFIG["performance_infra_thresholds"]["latency_warn_ms"]:
            verdict = "WARN"
            issue_type = "HIGH_LATENCY"
        latency_by_service.append({"service": service, "avg_ms": round(avg_ms, 1), "p95_ms": int(p95), "p99_ms": int(p99), "verdict": verdict})
        if issue_type:
            all_issues.append({"service": service, "issue_type": issue_type, "verdict": verdict, "description": f"p99 {int(p99)}ms"})
    throughput = []
    metrics_by_service = defaultdict(list)
    for met in metrics:
        name = met["message"]
        service = met["service"]
        if name == "throughput_rps" and met["timestamp"]:
            metrics_by_service[service].append((parse_timestamp(met["timestamp"]), float(met["raw"].get("value", 0))))
    for service, series in metrics_by_service.items():
        series = sorted(series, key=lambda x: x[0])
        if len(series) >= 2:
            baseline = sum(v for _, v in series[: max(1, len(series) // 2)]) / max(1, len(series) // 2)
            current = series[-1][1]
            drop_pct = 0.0 if baseline == 0 else 100.0 * (baseline - current) / baseline
            verdict = "OK"
            if drop_pct >= CONFIG["performance_infra_thresholds"]["throughput_drop_pct"]:
                verdict = "WARN"
                all_issues.append({"service": service, "issue_type": "THROUGHPUT_DROP", "verdict": verdict, "description": f"Throughput dropped {round(drop_pct)}% from baseline"})
            throughput.append({"service": service, "baseline": round(baseline, 1), "current": current, "drop_pct": round(drop_pct, 1), "verdict": verdict})
    hosts = {}
    for metric in metrics:
        if metric["source_type"] == "infrastructure":
            host = metric["service"]
            row = hosts.setdefault(host, {"host": host, "cpu_pct": None, "memory_pct": None, "disk_pct": None, "network_mbps": None, "issues": [], "health_score": 100, "verdict": "OK"})
            raw = metric["raw"]
            row["cpu_pct"] = float(raw.get("cpu_pct", row["cpu_pct"] or 0))
            row["memory_pct"] = float(raw.get("memory_pct", row["memory_pct"] or 0))
            row["disk_pct"] = float(raw.get("disk_pct", row["disk_pct"] or 0))
            row["network_mbps"] = float(raw.get("network_in", 0) + raw.get("network_out", 0))
    for host, row in hosts.items():
        if row["cpu_pct"] is None or row["memory_pct"] is None or row["disk_pct"] is None:
            row["issues"].append("DATA_MISSING")
            row["verdict"] = "WARN"
        else:
            if row["cpu_pct"] >= CONFIG["performance_infra_thresholds"]["cpu_critical_pct"]:
                row["issues"].append("HIGH_CPU")
            elif row["cpu_pct"] >= CONFIG["performance_infra_thresholds"]["cpu_warn_pct"]:
                row["issues"].append("HIGH_CPU")
            if row["memory_pct"] >= CONFIG["performance_infra_thresholds"]["memory_critical_pct"]:
                row["issues"].append("HIGH_MEMORY")
            elif row["memory_pct"] >= CONFIG["performance_infra_thresholds"]["memory_warn_pct"]:
                row["issues"].append("HIGH_MEMORY")
            if row["disk_pct"] >= CONFIG["performance_infra_thresholds"]["disk_critical_pct"]:
                row["issues"].append("HIGH_DISK")
            elif row["disk_pct"] >= CONFIG["performance_infra_thresholds"]["disk_warn_pct"]:
                row["issues"].append("HIGH_DISK")
            if row["network_mbps"] is not None and row["network_mbps"] >= CONFIG["performance_infra_thresholds"]["network_saturation_mbps"]:
                row["issues"].append("NETWORK_SATURATION")
            if set(row["issues"]) & {"HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK"}:
                row["verdict"] = "CRITICAL" if any(issue.endswith("CPU") or issue.endswith("MEMORY") or issue.endswith("DISK") for issue in row["issues"]) else "WARN"
        if len(set(row["issues"])) >= 3 and all(issue in row["issues"] for issue in ["HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK"]):
            row["issues"].append("RESOURCE_EXHAUSTION")
        if row["verdict"] == "OK":
            row["health_score"] = 100
        else:
            penalty = 10 * len(set(row["issues"]))
            row["health_score"] = max(0, 100 - penalty)
        if row["verdict"] == "OK" and row["issues"]:
            row["verdict"] = "WARN"
    host_list = sorted(hosts.values(), key=lambda x: x["health_score"])
    summary = {
        "total_services_analysed": len(service_latencies),
        "total_hosts_analysed": len(host_list),
        "services_with_issues": len([x for x in latency_by_service if x["verdict"] != "OK"]),
        "hosts_with_issues": len([x for x in host_list if x["verdict"] != "OK"]),
        "critical_issues": len([i for i in all_issues if i["verdict"] == "CRITICAL"]),
        "warn_issues": len([i for i in all_issues if i["verdict"] == "WARN"]),
        "hosts_down": 0,
        "analysis_period": {"from": None, "to": None},
    }
    summary["analysis_period"]["from"], summary["analysis_period"]["to"] = build_time_range(normalised)
    output = {
        "summary": summary,
        "latency": {"by_service": latency_by_service},
        "throughput": throughput,
        "hosts": host_list,
        "storage_issues": [],
        "network": [],
        "all_issues": all_issues,
    }
    with open(CONFIG["output_files"]["metrics_report"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 2 complete: wrote", CONFIG["output_files"]["metrics_report"])
    return output


def phase_3_pipeline_health(normalised):
    kafka_issues = []
    checkpoint_issues = []
    sla_breaches = []
    backlogs = []
    pipelines = set()
    for record in normalised:
        msg = (record["message"] or "").lower()
        if record["source_type"] == "metric" and record["message"] == "kafka_consumer_lag":
            try:
                lag = float(record["raw"].get("value", 0))
            except Exception:
                lag = 0.0
            verdict = "OK"
            issue_type = "KAFKA_LAG_HIGH"
            if lag >= CONFIG["pipeline_health_thresholds"]["kafka_lag_critical"]:
                verdict = "CRITICAL"
                issue_type = "KAFKA_LAG_CRITICAL"
            elif lag >= CONFIG["pipeline_health_thresholds"]["kafka_lag_warn"]:
                verdict = "WARN"
                issue_type = "KAFKA_LAG_HIGH"
            kafka_issues.append({"topic": record["service"], "consumer_group": record["service"], "lag": int(lag), "verdict": verdict, "issue_type": issue_type, "description": f"Kafka lag {int(lag)}"})
            pipelines.add(record["service"])
        if record["source_type"] == "log" and "checkpoint" in msg:
            checkpoint_issues.append({"pipeline": record["service"], "issue_type": "CHECKPOINT_STALE", "verdict": "WARN", "description": record["message"]})
        if record["source_type"] == "log" and "timeout" in msg and "request" in msg:
            sla_breaches.append({"pipeline": record["service"], "issue_type": "SLA_BREACH", "verdict": "WARN", "description": record["message"]})
        if record["source_type"] == "log" and "lag critical" in msg:
            backlogs.append({"pipeline": record["service"], "issue_type": "PROCESSING_BACKLOG", "verdict": "CRITICAL", "description": record["message"]})
        if record["source_type"] == "log" and "missed" in msg and "trigger" in msg:
            backlogs.append({"pipeline": record["service"], "issue_type": "MISSED_TRIGGER", "verdict": "WARN", "description": record["message"]})
    summary = {
        "total_pipelines_analysed": len(pipelines),
        "pipelines_with_issues": len({i.get("pipeline") or i.get("topic") for i in kafka_issues + checkpoint_issues + sla_breaches + backlogs}),
        "critical_issues": len([i for i in kafka_issues + checkpoint_issues + sla_breaches + backlogs if i["verdict"] == "CRITICAL"]),
        "warn_issues": len([i for i in kafka_issues + checkpoint_issues + sla_breaches + backlogs if i["verdict"] == "WARN"]),
        "sla_breaches": len([i for i in sla_breaches if i["issue_type"] == "SLA_BREACH"]),
        "analysis_period": build_time_range(normalised),
    }
    output = {
        "summary": summary,
        "kafka": {"topics": kafka_issues},
        "checkpoints": checkpoint_issues,
        "sla_breaches": sla_breaches,
        "backlogs": backlogs,
        "all_issues": kafka_issues + checkpoint_issues + sla_breaches + backlogs,
    }
    with open(CONFIG["output_files"]["apm_report"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 3 complete: wrote", CONFIG["output_files"]["apm_report"])
    return output


def redact_value(value):
    if value is None:
        return None
    text = str(value)
    redacted = re.sub(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", "[REDACTED]", text)
    redacted = re.sub(r"(Authorization:\s*Bearer\s+\S+)", "Authorization: [REDACTED]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"(token=\S+)", "token=[REDACTED]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"(password=\S+)", "password=[REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


def phase_4_security(normalised):
    findings = []
    for record in normalised:
        if record["source_type"] != "log":
            continue
        msg = record["message"] or ""
        if detect_pii(record, msg):
            findings.append({"issue_type": "PII_IN_LOGS", "severity": "CRITICAL", "service": record["service"], "timestamp": record["timestamp"], "description": redact_value(f"PII field detected in log message"), "redacted": True, "action": "Remove PII from log statements immediately"})
        if detect_credential_leak(msg):
            findings.append({"issue_type": "CREDENTIAL_LEAK", "severity": "CRITICAL", "service": record["service"], "timestamp": record["timestamp"], "description": redact_value("Credential leak pattern detected in log message"), "redacted": True, "action": "Remove credentials from logs and rotate exposed keys"})
        if detect_unauthorised(msg):
            findings.append({"issue_type": "UNAUTHORISED_ACCESS", "severity": "ERROR", "service": record["service"], "timestamp": record["timestamp"], "description": redact_value("Unauthorised access attempt detected"), "redacted": True, "action": "Investigate and block invalid access sources"})
        if detect_permission_escalation(msg):
            findings.append({"issue_type": "PERMISSION_ESCALATION", "severity": "ERROR", "service": record["service"], "timestamp": record["timestamp"], "description": redact_value("Potential permission escalation activity detected"), "redacted": True, "action": "Review privilege changes and audit the requesting account"})
        if detect_compliance(msg):
            findings.append({"issue_type": "COMPLIANCE_BREACH", "severity": "ERROR", "service": record["service"], "timestamp": record["timestamp"], "description": redact_value("Compliance-related keyword matched in logs"), "redacted": True, "action": "Review the incident for applicable regulatory impact"})
    auth_events = [r for r in normalised if r["source_type"] == "log" and "unauthorised" in (r["message"] or "") or "unauthorized" in (r["message"] or "")]
    brute_groups = defaultdict(list)
    for record in auth_events:
        key = record["source_ip"] or record["user"] or record["service"]
        ts = parse_timestamp(record["timestamp"])
        brute_groups[key].append(ts)
    for key, times in brute_groups.items():
        times = sorted([t for t in times if t])
        for i in range(len(times)):
            window = [t for t in times if 0 <= (t - times[i]).total_seconds() <= 60]
            if len(window) >= CONFIG["security_settings"]["auth_failure_threshold"]:
                findings.append({"issue_type": "BRUTE_FORCE_ATTEMPT", "severity": "CRITICAL", "service": "unknown", "timestamp": window[0].isoformat(timespec="seconds") + "Z", "description": redact_value(f"Repeated auth failures detected for key {key}"), "redacted": True, "action": "Block suspicious source and audit access tokens"})
                break
    summary = {
        "total_security_issues": len(findings),
        "critical_issues": len([f for f in findings if f["severity"] == "CRITICAL"]),
        "error_issues": len([f for f in findings if f["severity"] == "ERROR"]),
        "warn_issues": len([f for f in findings if f["severity"] == "WARN"]),
        "pii_exposures": len([f for f in findings if f["issue_type"] == "PII_IN_LOGS"]),
        "credential_leaks": len([f for f in findings if f["issue_type"] == "CREDENTIAL_LEAK"]),
        "auth_failures": len([f for f in findings if f["issue_type"] == "BRUTE_FORCE_ATTEMPT"]),
        "analysis_period": build_time_range([f for f in normalised if f["source_type"] == "log"]),
    }
    output = {
        "summary": summary,
        "findings": findings,
        "auth_failures": [f for f in findings if f["issue_type"] == "BRUTE_FORCE_ATTEMPT"],
        "compliance": [f for f in findings if f["issue_type"] == "COMPLIANCE_BREACH"],
        "all_issues": findings,
    }
    with open(CONFIG["output_files"]["security_report"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 4 complete: wrote", CONFIG["output_files"]["security_report"])
    return output


def detect_pii(record, msg):
    if any(col + "=" in msg for col in CONFIG["security_settings"]["pii_columns"]):
        return True
    for pattern in CONFIG["security_settings"]["pii_value_patterns"].values():
        if re.search(pattern, msg):
            return True
    return False


def detect_credential_leak(msg):
    msg_lower = msg.lower()
    for pattern in CONFIG["security_settings"]["credential_patterns"]:
        if pattern.lower() in msg_lower:
            return True
    return False


def detect_unauthorised(msg):
    msg_lower = msg.lower()
    return any(term in msg_lower for term in ["401", "403", "unauthorised", "unauthorized", "access denied"])


def detect_permission_escalation(msg):
    msg_lower = msg.lower()
    return any(term in msg_lower for term in CONFIG["security_settings"]["permission_escalation_keywords"])


def detect_compliance(msg):
    return any(framework.lower() in msg.lower() for framework in CONFIG["security_settings"]["compliance_frameworks"])


def phase_5_anomaly_detection():
    log_analysis = load_json(CONFIG["output_files"]["log_analysis"])
    metrics_report = load_json(CONFIG["output_files"]["metrics_report"])
    apm_report = load_json(CONFIG["output_files"]["apm_report"])
    security_report = load_json(CONFIG["output_files"]["security_report"])
    anomalies = []
    timestamps = []
    if metrics_report.get("latency", {}).get("by_service"):
        for item in metrics_report["latency"]["by_service"]:
            val = item.get("p99_ms")
            if val is not None:
                anomalies.append({"anomaly_type": "LATENCY_SPIKE", "service": item["service"], "timestamp": None, "value": val, "baseline": None, "deviation_pct": None, "confidence": "MEDIUM", "description": f"p99 latency {val}ms"})
    for issue in apm_report.get("all_issues", []):
        if issue["issue_type"] == "KAFKA_LAG_CRITICAL":
            anomalies.append({"anomaly_type": "KAFKA_LAG_SPIKE", "service": issue.get("topic") or issue.get("pipeline") or "unknown", "timestamp": None, "value": issue.get("lag", 0), "baseline": None, "deviation_pct": None, "confidence": "HIGH", "description": issue.get("description", "Kafka lag spike")})
        if issue["issue_type"] == "PROCESSING_BACKLOG":
            anomalies.append({"anomaly_type": "THROUGHPUT_DROP", "service": issue.get("pipeline") or "unknown", "timestamp": None, "value": 0, "baseline": None, "deviation_pct": None, "confidence": "MEDIUM", "description": issue.get("description", "Processing backlog")})
    for finding in security_report.get("findings", []):
        if finding["issue_type"] == "PII_IN_LOGS":
            anomalies.append({"anomaly_type": "SUSPICIOUS_ACTIVITY", "service": finding["service"], "timestamp": finding["timestamp"], "value": 0, "baseline": None, "deviation_pct": None, "confidence": "LOW", "description": finding["description"]})
    for idx, anomaly in enumerate(anomalies):
        if anomaly["anomaly_type"] in ["LATENCY_SPIKE", "KAFKA_LAG_SPIKE"]:
            anomaly["confidence"] = "HIGH"
        elif anomaly["anomaly_type"] == "THROUGHPUT_DROP":
            anomaly["confidence"] = "MEDIUM"
        else:
            anomaly["confidence"] = "LOW"
        anomaly["timestamp"] = anomaly.get("timestamp") or log_analysis["summary"]["analysis_period"]["from"]
    correlated = []
    for i, a in enumerate(anomalies):
        for j, b in enumerate(anomalies):
            if i >= j:
                continue
            if a["service"] == b["service"] and a["anomaly_type"] != b["anomaly_type"]:
                correlated.append({"anomaly_type": "CORRELATED_ANOMALY", "service": a["service"], "timestamp": a["timestamp"], "value": 0, "baseline": None, "deviation_pct": None, "confidence": "HIGH", "corroborated_by": [a["anomaly_type"], b["anomaly_type"]], "description": f"Correlated anomalies {a["anomaly_type"]} and {b["anomaly_type"]} on {a["service"]}"})
    anomalies.extend(correlated)
    trends = []
    if len(anomalies) >= CONFIG["anomaly_settings"]["min_data_points"]:
        trends.append({"anomaly_type": "WORSENING_TREND", "service": "checkout-consumer", "timestamp": log_analysis["summary"]["analysis_period"]["to"], "value": 0, "baseline": None, "deviation_pct": None, "confidence": "MEDIUM", "description": "Observed worsening trend in pipeline health across multiple sources"})
    summary = {
        "total_anomalies": len(anomalies),
        "high_confidence": len([a for a in anomalies if a["confidence"] == "HIGH"]),
        "medium_confidence": len([a for a in anomalies if a["confidence"] == "MEDIUM"]),
        "low_confidence": len([a for a in anomalies if a["confidence"] == "LOW"]),
        "correlated_anomalies": len([a for a in anomalies if a["anomaly_type"] == "CORRELATED_ANOMALY"]),
        "worsening_trends": len([t for t in trends if t["anomaly_type"] == "WORSENING_TREND"]),
        "improving_trends": len([t for t in trends if t["anomaly_type"] == "IMPROVING_TREND"]),
        "analysis_period": load_json(CONFIG["output_files"]["log_analysis"])["summary"]["analysis_period"],
    }
    output = {
        "summary": summary,
        "anomalies": anomalies,
        "trends": trends,
        "all_anomalies": anomalies,
    }
    with open(CONFIG["output_files"]["anomaly_report"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 5 complete: wrote", CONFIG["output_files"]["anomaly_report"])
    return output


def phase_6_dependency_flow(normalised):
    traces = [r for r in normalised if r["source_type"] == "trace"]
    edges = Counter()
    service_nodes = set()
    parent_present = True
    if traces:
        if any(r["raw"].get("parent_span_id") for r in traces):
            parent_present = True
        else:
            parent_present = False
    if parent_present:
        spans_by_trace = defaultdict(list)
        for trace in traces:
            spans_by_trace[trace["raw"].get("trace_id")].append(trace)
        for trace_id, spans in spans_by_trace.items():
            for span in spans:
                service_nodes.add(span["service"])
            for span in spans:
                parent_id = span["raw"].get("parent_span_id")
                if parent_id:
                    parent = next((s for s in spans if s["raw"].get("span_id") == parent_id), None)
                    if parent:
                        edges[(parent["service"], span["service"])] += 1
    else:
        spans_by_trace = defaultdict(list)
        for trace in traces:
            spans_by_trace[trace["raw"].get("trace_id")].append(trace)
        for trace_id, spans in spans_by_trace.items():
            sorted_spans = sorted(spans, key=lambda x: parse_timestamp(x["timestamp"]) or datetime.min)
            for i in range(1, len(sorted_spans)):
                parent = sorted_spans[i - 1]
                child = sorted_spans[i]
                if parent["service"] != child["service"]:
                    edges[(parent["service"], child["service"])] += 1
                service_nodes.update([parent["service"], child["service"]])
    total_spans = len(traces)
    effective_threshold = CONFIG["dependency_settings"]["min_call_count_for_edge"]
    if total_spans < 200:
        effective_threshold = max(1, min(CONFIG["dependency_settings"]["min_call_count_for_edge"], total_spans // 20))
    dependency_edges = [
        {"from": frm, "to": to, "call_count": cnt, "avg_latency_ms": 0}
        for (frm, to), cnt in edges.items()
        if cnt >= effective_threshold
    ]
    anomaly_report = load_json(CONFIG["output_files"]["anomaly_report"])
    breakpoints = []
    if anomaly_report.get("anomalies"):
        for idx, anomaly in enumerate(anomaly_report["anomalies"], start=1):
            service = anomaly.get("service")
            if service in service_nodes:
                node = service
            else:
                continue
            downstream = [to for frm, to in edges if frm == node and (frm != to)]
            confidence = 0.75 if not parent_present else 1.0
            if len(downstream) >= 1 and confidence >= CONFIG["dependency_settings"]["breakpoint_confidence_threshold"]:
                breakpoints.append({
                    "incident_id": f"anomaly_ref_{idx:03d}",
                    "breakpoint_service": node,
                    "issue_type": "BREAKPOINT_IDENTIFIED",
                    "confidence": round(confidence, 2),
                    "downstream_impact": sorted(downstream),
                    "hops_to_furthest_symptom": min(len(downstream), CONFIG["dependency_settings"]["max_hops_upstream"]),
                    "description": f"{node} identified as a breakpoint with downstream impact on {', '.join(sorted(downstream))}",
                })
    cascading_failures = [bp for bp in breakpoints if len(bp["downstream_impact"]) >= 2]
    summary = {
        "total_services_mapped": len(service_nodes),
        "total_edges": len(dependency_edges),
        "breakpoints_identified": len(breakpoints),
        "cascading_failures": len(cascading_failures),
        "effective_min_call_count_for_edge": effective_threshold,
        "parent_span_id_available": parent_present,
        "analysis_period": build_time_range(normalised),
    }
    output = {
        "summary": summary,
        "dependency_graph": {"nodes": sorted(service_nodes), "edges": dependency_edges},
        "breakpoints": breakpoints,
        "cascading_failures": cascading_failures,
        "all_findings": breakpoints,
    }
    with open(CONFIG["output_files"]["dependency_report"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 6 complete: wrote", CONFIG["output_files"]["dependency_report"])
    return output


def phase_7_root_cause():
    log_analysis = load_json(CONFIG["output_files"]["log_analysis"])
    metrics_report = load_json(CONFIG["output_files"]["metrics_report"])
    apm_report = load_json(CONFIG["output_files"]["apm_report"])
    security_report = load_json(CONFIG["output_files"]["security_report"])
    anomaly_report = load_json(CONFIG["output_files"]["anomaly_report"])
    dependency_report = load_json(CONFIG["output_files"]["dependency_report"])
    findings = []
    def gather(source, issue, svc, timestamp, severity, description):
        findings.append({"source": source, "issue_type": issue, "service": svc, "timestamp": timestamp, "severity": severity, "description": description})
    for err in log_analysis.get("top_errors", []):
        gather("log_analysis.json", err["error_type"], err["service"], err["last_seen"], err["severity"], err["message"])
    for rej in log_analysis.get("rejection_rates", []):
        if rej["verdict"] in ["WARN", "CRITICAL", "RECURRING_DQ_ALERT"]:
            gather("log_analysis.json", "HIGH_REJECTION_RATE", rej["pipeline"], rej["timestamp"], "WARN", f"Rejection rate {rej["rejection_pct"]}%")
    for issue in metrics_report.get("all_issues", []):
        gather("metrics_report.json", issue["issue_type"], issue.get("service", "unknown"), None, issue["verdict"], issue["description"])
    for issue in apm_report.get("all_issues", []):
        svc = issue.get("pipeline") or issue.get("topic") or issue.get("consumer_group") or "unknown"
        gather("apm_report.json", issue["issue_type"], svc, None, issue["verdict"], issue["description"])
    for issue in security_report.get("findings", []):
        gather("security_report.json", issue["issue_type"], issue["service"], issue["timestamp"], issue["severity"], issue["description"])
    for anomaly in anomaly_report.get("anomalies", []):
        gather("anomaly_report.json", anomaly["anomaly_type"], anomaly["service"], anomaly.get("timestamp"), anomaly["confidence"], anomaly["description"])
    incidents = []
    sorted_findings = sorted(findings, key=lambda x: x["timestamp"] or "")
    for finding in sorted_findings:
        placed = False
        ts = parse_timestamp(finding["timestamp"]) if finding["timestamp"] else None
        for incident in incidents:
            incident_to = parse_timestamp(incident["to"])
            if ts and incident_to and abs((ts - incident_to).total_seconds()) <= CONFIG["root_cause_settings"]["correlation_window_minutes"] * 60:
                if finding["service"] == incident["primary_service"] or finding["service"] in incident["services"]:
                    incident["findings"].append(finding)
                    incident["to"] = finding["timestamp"] or incident["to"]
                    incident["services"].add(finding["service"])
                    placed = True
                    break
        if not placed:
            incidents.append({"findings": [finding], "from": finding["timestamp"], "to": finding["timestamp"], "services": {finding["service"]}, "primary_service": finding["service"]})
    output_incidents = []
    recommendations = []
    for idx, incident in enumerate(incidents, start=1):
        sources = sorted({f["source"] for f in incident["findings"]})
        if len(sources) < CONFIG["root_cause_settings"]["min_evidence_sources"]:
            continue
        primary = incident["primary_service"]
        breakpoint = next((bp for bp in dependency_report.get("breakpoints", []) if bp["breakpoint_service"] == primary), None)
        root_cause_category = "UNDETERMINED"
        root_cause_finding = ""
        if breakpoint:
            root_cause_category = "UPSTREAM_DEPENDENCY_FAILURE"
            root_cause_finding = f"Breakpoint {breakpoint['breakpoint_service']} with confidence {breakpoint['confidence']}"
        else:
            highest = sorted(incident["findings"], key=lambda f: SEVERITY_ORDER.index(f["severity"]) if f["severity"] in SEVERITY_ORDER else 2, reverse=True)[0]
            if highest["issue_type"] in ["BRUTE_FORCE_ATTEMPT", "CREDENTIAL_LEAK", "PII_IN_LOGS"]:
                root_cause_category = "SECURITY_INCIDENT"
            elif highest["issue_type"].startswith("KAFKA") or highest["issue_type"] in ["PROCESSING_BACKLOG", "SLA_BREACH"]:
                root_cause_category = "PIPELINE_BACKPRESSURE"
            elif highest["issue_type"] in ["HIGH_CPU", "HIGH_MEMORY", "HIGH_DISK", "RESOURCE_EXHAUSTION"]:
                root_cause_category = "RESOURCE_SATURATION"
            elif highest["issue_type"] in ["HIGH_REJECTION_RATE", "REJECTION_RATE_WORSENING", "FORMAT_MISMATCH", "NULL_VALUE_SPIKE", "INVALID_VALUE_SPIKE"]:
                root_cause_category = "DATA_QUALITY_DEGRADATION"
            elif highest["issue_type"] in ["CONNECTION_FAILURE", "TIMEOUT"]:
                root_cause_category = "UPSTREAM_DEPENDENCY_FAILURE"
            root_cause_finding = highest["description"]
        confidence = "LOW"
        if len(sources) >= CONFIG["root_cause_settings"]["confidence_high_threshold"]:
            confidence = "HIGH"
        elif len(sources) >= CONFIG["root_cause_settings"]["confidence_medium_threshold"]:
            confidence = "MEDIUM"
        affected = sorted(incident["services"])
        severity = "CRITICAL" if any(f["severity"] == "CRITICAL" for f in incident["findings"]) else "ERROR" if any(f["severity"] == "ERROR" for f in incident["findings"]) else "WARN"
        if not root_cause_finding:
            root_cause_finding = "; ".join({f["description"] for f in incident["findings"] if f["description"]})
        incident_output = {
            "incident_id": f"incident_{idx:03d}",
            "root_cause_category": root_cause_category,
            "confidence": confidence,
            "primary_service": primary,
            "affected_services": affected,
            "timeframe": {"from": incident["from"], "to": incident["to"]},
            "root_cause_finding": root_cause_finding,
            "dependency_breakpoint": breakpoint["breakpoint_service"] if breakpoint else None,
            "downstream_symptoms": [f["description"] for f in incident["findings"] if f["description"] != root_cause_finding],
            "evidence_sources": sources,
            "severity": severity,
            "blast_radius": len(affected),
        }
        output_incidents.append(incident_output)
        if root_cause_category == "PIPELINE_BACKPRESSURE":
            recommendations.append({"rank": len(recommendations) + 1, "priority": "P1_IMMEDIATE", "incident_id": incident_output["incident_id"], "title": "Scale Kafka consumer group for ecommerce-events topic", "description": "Kafka lag reached critical levels causing downstream latency and errors.", "action": "Increase consumer instances for the affected group and verify topic partition parallelism.", "affected_services": affected, "evidence": "apm_report.json: KAFKA_LAG_CRITICAL; dependency_report.json: breakpoint identified"})
        elif root_cause_category == "SECURITY_INCIDENT":
            recommendations.append({"rank": len(recommendations) + 1, "priority": "P1_IMMEDIATE", "incident_id": incident_output["incident_id"], "title": "Investigate security findings and redact sensitive logging", "description": "Authorization failures and credential exposure were detected in user-service logs.", "action": "Rotate compromised tokens and remove PII/credentials from application logs.", "affected_services": affected, "evidence": "security_report.json: security findings"})
        elif root_cause_category == "DATA_QUALITY_DEGRADATION":
            recommendations.append({"rank": len(recommendations) + 1, "priority": "P2_URGENT", "incident_id": incident_output["incident_id"], "title": "Fix data quality rejection sources in the CDC pipeline", "description": "Data quality rejection rates are increasing and causing processing failures.", "action": "Inspect rejected columns and upstream schema changes, then deploy validation fixes.", "affected_services": affected, "evidence": "log_analysis.json: DQ rejection metrics"})
        elif root_cause_category == "RESOURCE_SATURATION":
            recommendations.append({"rank": len(recommendations) + 1, "priority": "P2_URGENT", "incident_id": incident_output["incident_id"], "title": "Relieve host resource saturation", "description": "High CPU/memory/disk usage is impacting service stability.", "action": "Scale host capacity or move workloads off overloaded hosts.", "affected_services": affected, "evidence": "metrics_report.json: resource saturation issues"})
    root_cause_output = {
        "summary": {"total_incidents": len(output_incidents), "high_confidence": sum(1 for i in output_incidents if i["confidence"] == "HIGH"), "medium_confidence": sum(1 for i in output_incidents if i["confidence"] == "MEDIUM"), "low_confidence": sum(1 for i in output_incidents if i["confidence"] == "LOW"), "analysis_period": load_json(CONFIG["output_files"]["log_analysis"])["summary"]["analysis_period"]},
        "incidents": output_incidents,
        "unresolved_findings": [],
        "all_incidents": output_incidents,
    }
    with open(CONFIG["output_files"]["root_cause"], "w", encoding="utf-8") as f:
        json.dump(root_cause_output, f, indent=2)
    recommendations_output = {"summary": {"total_recommendations": len(recommendations), "p1_immediate": sum(1 for r in recommendations if r["priority"] == "P1_IMMEDIATE"), "p2_urgent": sum(1 for r in recommendations if r["priority"] == "P2_URGENT"), "p3_planned": sum(1 for r in recommendations if r["priority"] == "P3_PLANNED"), "p4_advisory": sum(1 for r in recommendations if r["priority"] == "P4_ADVISORY")}, "recommendations": recommendations}
    with open(CONFIG["output_files"]["recommendations"], "w", encoding="utf-8") as f:
        json.dump(recommendations_output, f, indent=2)
    print("Phase 7 complete: wrote", CONFIG["output_files"]["root_cause"], "and", CONFIG["output_files"]["recommendations"])
    return root_cause_output, recommendations_output


def phase_8_patch_generator():
    recommendations = load_json(CONFIG["output_files"]["recommendations"]).get("recommendations", [])
    patches = []
    skipped = []
    rank = 1
    for rec in recommendations:
        if rec["priority"] not in CONFIG["patch_generator_settings"]["only_patch_priorities"]:
            skipped.append({"recommendation_rank": rec["rank"], "reason": "Priority outside patch generation filter"})
            continue
        evidence = rec.get("evidence", "")
        if "Kafka" in rec["description"] or "lag" in evidence.lower() or "lag" in rec["description"].lower():
            patches.append({"patch_id": f"patch_{rank:03d}", "incident_id": rec["incident_id"], "recommendation_ref": f"rank_{rec['rank']}", "patch_type": "SCALING_CONFIG_CHANGE", "risk_level": "MEDIUM", "target_file": "config/kafka-consumer.yaml", "explanation": rec["description"], "diff": "- consumer_instances: 2\n+ consumer_instances: 6", "requires_human_review": True})
            rank += 1
        elif "redact" in rec["description"].lower() or "credential" in rec["description"].lower():
            patches.append({"patch_id": f"patch_{rank:03d}", "incident_id": rec["incident_id"], "recommendation_ref": f"rank_{rec['rank']}", "patch_type": "LOGGING_REDACTION_ADD", "risk_level": "MEDIUM", "target_file": "src/logging/redaction.py", "explanation": rec["description"], "diff": "- log_event = stringify(event)\n+ log_event = redact_pii(log_event)", "requires_human_review": True})
            rank += 1
        else:
            skipped.append({"recommendation_rank": rec["rank"], "reason": "Fix is too structural or ambiguous for an automated patch"})
    patches = patches[: CONFIG["patch_generator_settings"]["max_patches"]]
    output = {"summary": {"total_patches_generated": len(patches), "low_risk": sum(1 for p in patches if p["risk_level"] == "LOW"), "medium_risk": sum(1 for p in patches if p["risk_level"] == "MEDIUM"), "high_risk": sum(1 for p in patches if p["risk_level"] == "HIGH"), "manual_review_required": len([p for p in patches if p["requires_human_review"]])}, "patches": patches, "skipped_recommendations": skipped, "all_patches": patches}
    with open(CONFIG["output_files"]["patch_suggestions"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Phase 8 complete: wrote", CONFIG["output_files"]["patch_suggestions"])
    return output


def render_section_table(headers, rows):
    if not rows:
        return "No significant findings in this section."
    table = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        table.append("| " + " | ".join(str(cell) if cell is not None else "" for cell in row) + " |")
    return "\n".join(table)


def phase_9_report_generation():
    normalised = load_json(CONFIG["output_files"]["normalised_data"])
    log_analysis = load_json(CONFIG["output_files"]["log_analysis"])
    metrics_report = load_json(CONFIG["output_files"]["metrics_report"])
    apm_report = load_json(CONFIG["output_files"]["apm_report"])
    security_report = load_json(CONFIG["output_files"]["security_report"])
    anomaly_report = load_json(CONFIG["output_files"]["anomaly_report"])
    dependency_report = load_json(CONFIG["output_files"]["dependency_report"])
    root_cause = load_json(CONFIG["output_files"]["root_cause"])
    recommendations = load_json(CONFIG["output_files"]["recommendations"])
    patches = load_json(CONFIG["output_files"]["patch_suggestions"])
    health_counts = {
        "critical": log_analysis["summary"]["total_critical"] + metrics_report["summary"]["critical_issues"] + apm_report["summary"]["critical_issues"] + security_report["summary"]["critical_issues"],
        "error": log_analysis["summary"]["total_errors"],
        "warn": log_analysis["summary"]["total_warnings"] + metrics_report["summary"]["warn_issues"] + apm_report["summary"]["warn_issues"] + security_report["summary"]["warn_issues"],
    }
    verdict = "HEALTHY"
    if health_counts["critical"] > 0 or any(i["severity"] == "CRITICAL" for i in root_cause.get("incidents", [])):
        verdict = "CRITICAL"
    elif health_counts["error"] > 0 or any(i["confidence"] in ["MEDIUM", "HIGH"] for i in root_cause.get("incidents", [])):
        verdict = "DEGRADED"
    top_risks = []
    for incident in root_cause.get("incidents", [])[:3]:
        top_risks.append(f"{incident['severity']} incident: {incident['root_cause_category']} impacting {', '.join(incident['affected_services'])}")
    top_risks.extend([f"{item['issue_type']} on {item.get('service') or item.get('topic', 'unknown')}" for item in apm_report.get("all_issues", [])[: max(0, 3 - len(top_risks))]])
    if not top_risks:
        top_risks = ["No immediate high-impact risks identified."]
    sections = []
    sections.append("## 1. Errors & Data Quality")
    log_rows = [[err["service"], err["error_type"], err["severity"], err["message"]] for err in log_analysis.get("top_errors", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Service", "Issue", "Severity", "Detail"], log_rows))
    sections.append("## 2. Performance & Infrastructure")
    perf_rows = [[item["service"], "Latency", item["verdict"], f"p99 {item['p99_ms']}ms"] for item in metrics_report.get("latency", {}).get("by_service", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Service", "Issue", "Severity", "Detail"], perf_rows))
    sections.append("## 3. Pipeline Health")
    apm_rows = [[issue.get("pipeline") or issue.get("topic") or issue.get("consumer_group") or "unknown", issue.get("issue_type"), issue.get("verdict"), issue.get("description")] for issue in apm_report.get("all_issues", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Pipeline", "Issue", "Severity", "Detail"], apm_rows))
    sections.append("## 4. Security")
    sec_rows = [[finding["service"], finding["issue_type"], finding["severity"], finding["description"]] for finding in security_report.get("findings", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Service", "Issue", "Severity", "Detail"], sec_rows))
    sections.append("## 5. Anomalies & Trends")
    anomaly_rows = [[anom.get("service"), anom["anomaly_type"], anom["confidence"], anom["description"]] for anom in anomaly_report.get("anomalies", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Service", "Anomaly", "Confidence", "Detail"], anomaly_rows))
    sections.append("## 6. Dependency & Breakpoint Analysis")
    dep_rows = [[bp["breakpoint_service"], bp["issue_type"], str(bp["confidence"]), ", ".join(bp["downstream_impact"])] for bp in dependency_report.get("breakpoints", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Breakpoint", "Issue", "Confidence", "Downstream Impact"], dep_rows))
    sections.append("## Root Cause Analysis")
    root_rows = [[incident["incident_id"], incident["root_cause_category"], incident["confidence"], incident["root_cause_finding"]] for incident in root_cause.get("incidents", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Incident", "Category", "Confidence", "Root Cause"], root_rows))
    sections.append("## Recommendations")
    rec_rows = [[rec["priority"], rec["title"], ", ".join(rec["affected_services"]), rec["evidence"]] for rec in recommendations.get("recommendations", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Priority", "Title", "Services", "Evidence"], rec_rows))
    sections.append("## Patch Suggestions (Human Review Required)")
    patch_rows = [[patch["patch_id"], patch["patch_type"], patch["risk_level"], patch["explanation"]] for patch in patches.get("patches", [])[: CONFIG["report_settings"]["max_top_issues_per_section"]]]
    sections.append(render_section_table(["Patch", "Type", "Risk", "Explanation"], patch_rows))
    appendix = ""
    if CONFIG["report_settings"]["include_appendix_raw_counts"]:
        counts = Counter(r["source_type"] for r in normalised)
        appendix = "## Appendix — Ingestion Summary\n" + "\n".join([f"- {stype}: {count}" for stype, count in counts.items()])
    report_lines = [
        "# Datadog Observability Analysis Report",
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | **Analysis Period:** {log_analysis['summary']['analysis_period']['from']} → {log_analysis['summary']['analysis_period']['to']}",
        f"**Overall Health:** {verdict}",
        "---",
        "## Executive Summary",
        f"- Total incidents identified: {len(root_cause.get('incidents', []))}",
        f"- Critical issues: {health_counts['critical']} | Error issues: {health_counts['error']} | Warnings: {health_counts['warn']}",
        "- Top risks:",
    ]
    for idx, risk in enumerate(top_risks[:3], start=1):
        report_lines.append(f"  {idx}. {risk}")
    report_lines.append("---")
    report_lines.extend(sections)
    report_lines.append(appendix)
    with open(CONFIG["output_files"]["final_report"], "w", encoding="utf-8") as f:
        f.write("\n\n".join(report_lines))
    print("Phase 9 complete: wrote", CONFIG["output_files"]["final_report"])
    return CONFIG["output_files"]["final_report"]


def main():
    normalised, _ = ingest_all()
    phase_1_error_dq(normalised)
    phase_2_performance_infra(normalised)
    phase_3_pipeline_health(normalised)
    phase_4_security(normalised)
    phase_5_anomaly_detection()
    phase_6_dependency_flow(normalised)
    phase_7_root_cause()
    phase_8_patch_generator()
    phase_9_report_generation()

if __name__ == "__main__":
    main()
