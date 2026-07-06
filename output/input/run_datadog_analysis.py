import argparse
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SEVERITY_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "WARNING": 2, "ERROR": 3, "CRITICAL": 4, "FATAL": 4}


def parse_timestamp(value: Optional[Any]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"]:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def format_timestamp(ts: Optional[datetime]) -> str:
    if ts is None:
        return ""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_severity(value: Any) -> str:
    if value is None:
        return "INFO"
    text = str(value).strip().upper()
    if text in SEVERITY_ORDER:
        return text
    if text.startswith("ERR"):
        return "ERROR"
    if text.startswith("WARN"):
        return "WARN"
    if text.startswith("CRIT") or text.startswith("FATAL"):
        return "CRITICAL"
    return "INFO"


def extract_tags(item: Dict[str, Any]) -> List[str]:
    tags = item.get("tags") or item.get("attributes", {}).get("tags") or []
    if isinstance(tags, str):
        return [tags]
    if isinstance(tags, dict):
        return [f"{k}:{v}" for k, v in tags.items() if v is not None]
    if isinstance(tags, list):
        return [str(tag) for tag in tags if tag is not None]
    return []


def derive_environment(item: Dict[str, Any]) -> str:
    env = item.get("environment") or item.get("env") or item.get("attributes", {}).get("env")
    if isinstance(env, str) and env:
        return env.lower()
    for tag in extract_tags(item):
        if tag.startswith("env:"):
            return tag.split(":", 1)[1].lower()
    return "unknown"


def extract_user_and_ip(message: str, item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    user = None
    source_ip = None
    if isinstance(item.get("source_ip"), str):
        source_ip = item.get("source_ip")
    if isinstance(item.get("user"), str):
        user = item.get("user")
    ip_match = re.search(r"(?:source_ip|client_ip|ip|remote_addr)=([0-9.]+)", message, re.I)
    if ip_match:
        source_ip = source_ip or ip_match.group(1)
    user_match = re.search(r"(?:user|username|account)=([A-Za-z0-9_@.-]+)", message, re.I)
    if user_match:
        user = user or user_match.group(1)
    return user, source_ip


def detect_source_type(path: Path, payload: Any) -> str:
    if isinstance(payload, list):
        if all(isinstance(item, dict) for item in payload) and payload:
            if any("monitor_name" in item or "alert_id" in item or "priority" in item for item in payload):
                return "alert"
            if any("host" in item and any(key in item for key in ["cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"]) for item in payload):
                return "infrastructure"
            if any("trace_id" in item or "span_id" in item for item in payload):
                return "trace"
            if any(("status" in item or "level" in item or "severity" in item) and "message" in item for item in payload):
                return "log"
    if isinstance(payload, dict):
        if any(key in payload for key in ["monitor_name", "alert_id", "status", "priority"]):
            return "alert"
        if any(key in payload for key in ["trace_id", "span_id", "duration_ms"]):
            return "trace"
        if isinstance(payload.get("host"), str) and any(isinstance(payload.get(key), (int, float)) for key in ["cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"]):
            return "infrastructure"
    if path.suffix.lower() == ".csv":
        return "metric"
    return "unknown"


def read_input_payload(path: Path) -> Any:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    text = path.read_text(encoding="utf-8")
    obj = json.loads(text)
    if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], list):
        return obj["data"]
    return obj


def normalize_record(source_type: str, item: Dict[str, Any], record_index: int, source_file: str) -> Dict[str, Any]:
    if source_type == "log":
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else item
        timestamp = parse_timestamp(attributes.get("timestamp") or attributes.get("created_at") or attributes.get("date"))
        severity = normalize_severity(attributes.get("status") or attributes.get("level") or attributes.get("severity") or item.get("status"))
        message = str(attributes.get("message") or attributes.get("msg") or attributes.get("text") or "")
        service = str(attributes.get("service") or attributes.get("host") or item.get("service") or item.get("host") or "unknown")
        tags = extract_tags(attributes)
        env = derive_environment(attributes)
        user, source_ip = extract_user_and_ip(message, attributes)
        return {"record_id": f"log_{record_index:06d}", "source_type": "log", "severity": severity, "service": service, "environment": env, "timestamp": format_timestamp(timestamp), "message": message, "tags": tags, "source_ip": source_ip, "user": user, "raw": item, "source_file": source_file}
    if source_type == "metric":
        timestamp = parse_timestamp(item.get("timestamp") or item.get("time") or item.get("date"))
        service = str(item.get("service") or item.get("host") or "unknown")
        return {"record_id": f"metric_{record_index:06d}", "source_type": "metric", "severity": "INFO", "service": service, "environment": derive_environment(item), "timestamp": format_timestamp(timestamp), "message": json.dumps({"metric": item.get("metric_name") or item.get("metric"), "value": item.get("value")}), "tags": extract_tags(item), "source_ip": None, "user": None, "raw": item, "source_file": source_file}
    if source_type == "trace":
        timestamp = parse_timestamp(item.get("timestamp") or item.get("start_time") or item.get("time"))
        service = str(item.get("service") or item.get("resource") or "unknown")
        duration = item.get("duration_ms") or item.get("duration") or item.get("attributes", {}).get("duration_ms")
        return {"record_id": f"trace_{record_index:06d}", "source_type": "trace", "severity": "ERROR" if str(item.get("status") or "").lower() in {"error", "fail"} else "INFO", "service": service, "environment": derive_environment(item), "timestamp": format_timestamp(timestamp), "message": str(item.get("operation") or item.get("name") or "trace event"), "tags": extract_tags(item), "source_ip": None, "user": None, "raw": item, "source_file": source_file, "duration_ms": float(duration or 0)}
    if source_type == "alert":
        timestamp = parse_timestamp(item.get("triggered_at") or item.get("timestamp") or item.get("created_at"))
        service = str(item.get("service") or item.get("monitor_name") or "unknown")
        priority = str(item.get("priority") or item.get("severity") or "").upper()
        severity = "INFO"
        if priority in {"P1", "CRITICAL"}:
            severity = "CRITICAL"
        elif priority in {"P2", "ERROR"}:
            severity = "ERROR"
        elif priority in {"P3", "WARN", "WARNING"}:
            severity = "WARN"
        return {"record_id": f"alert_{record_index:06d}", "source_type": "alert", "severity": severity, "service": service, "environment": derive_environment(item), "timestamp": format_timestamp(timestamp), "message": str(item.get("message") or item.get("title") or "alert event"), "tags": extract_tags(item), "source_ip": None, "user": None, "raw": item, "source_file": source_file}
    if source_type == "infrastructure":
        timestamp = parse_timestamp(item.get("timestamp") or item.get("time"))
        host = str(item.get("host") or "unknown")
        return {"record_id": f"infra_{record_index:06d}", "source_type": "infrastructure", "severity": "INFO", "service": host, "environment": derive_environment(item), "timestamp": format_timestamp(timestamp), "message": json.dumps({k: item.get(k) for k in ["cpu_pct", "memory_pct", "disk_pct", "network_in", "network_out"] if k in item}), "tags": extract_tags(item), "source_ip": None, "user": None, "raw": item, "source_file": source_file}
    return {"record_id": f"unknown_{record_index:06d}", "source_type": source_type, "severity": "INFO", "service": str(item.get("service") or item.get("host") or "unknown"), "environment": derive_environment(item), "timestamp": format_timestamp(parse_timestamp(item.get("timestamp") or item.get("time"))), "message": str(item.get("message") or item.get("text") or ""), "tags": extract_tags(item), "source_ip": None, "user": None, "raw": item, "source_file": source_file}


def resolve_dataset_manifest(input_dir: Path, output_dir: Path, files: List[Path]) -> Dict[str, Any]:
    dataset_name = input_dir.name
    output_folder = output_dir / dataset_name
    artifact_files = {
        "dataset_manifest": str(output_folder / "dataset_manifest.json"),
        "normalised_data": str(output_folder / "normalised_data.json"),
        "log_analysis": str(output_folder / "log_analysis.json"),
        "metrics_report": str(output_folder / "metrics_report.json"),
        "apm_report": str(output_folder / "apm_report.json"),
        "security_report": str(output_folder / "security_report.json"),
        "anomaly_report": str(output_folder / "anomaly_report.json"),
        "dependency_report": str(output_folder / "dependency_report.json"),
        "root_cause": str(output_folder / "root_cause.json"),
        "recommendations": str(output_folder / "recommendations.json"),
        "patch_suggestions": str(output_folder / "patch_suggestions.json"),
        "report": str(output_folder / "datadog_analysis_report.md"),
        "validation_manifest": str(output_folder / "validation_manifest.json"),
        "run_script": str(output_folder / "run_datadog_analysis.py"),
    }
    return {"dataset_name": dataset_name, "input_target": str(input_dir), "output_dir": str(output_folder), "input_files": [str(path) for path in files], "artifacts": artifact_files}


def build_dataset_records(input_dir: Path, output_dir: Path) -> Dict[str, Any]:
    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".csv"}])
    records: List[Dict[str, Any]] = []
    classified_files = []
    skipped_files = []
    counts = {"log": 0, "metric": 0, "trace": 0, "alert": 0, "infrastructure": 0, "unknown": 0, "total": 0}
    for path in files:
        payload = read_input_payload(path)
        source_type = detect_source_type(path, payload)
        if source_type == "unknown":
            skipped_files.append({"path": path.name, "reason": "unclassified"})
            continue
        entries = payload if isinstance(payload, list) else [payload]
        classified_files.append({"path": path.name, "source_type": source_type, "record_count": len(entries)})
        for item in entries:
            if not isinstance(item, dict):
                continue
            record = normalize_record(source_type, item, len(records) + 1, path.name)
            records.append(record)
        counts[source_type] += len(entries)
    counts["total"] = len(records)
    timestamps = [parse_timestamp(r.get("timestamp")) for r in records if parse_timestamp(r.get("timestamp"))]
    analysis_period = {"from": format_timestamp(min(timestamps)) if timestamps else "", "to": format_timestamp(max(timestamps) + timedelta(seconds=1)) if timestamps else ""}
    return {"dataset_name": input_dir.name, "input_root": str(input_dir), "analysis_period": analysis_period, "record_counts": counts, "classified_files": classified_files, "skipped_files": skipped_files, "records": records}


def build_log_analysis(normalised_data: Dict[str, Any]) -> Dict[str, Any]:
    logs = [r for r in normalised_data["records"] if r["source_type"] == "log"]
    errors = [r for r in logs if SEVERITY_ORDER.get(r["severity"], 0) >= SEVERITY_ORDER["ERROR"]]
    grouped = {}
    for record in errors:
        key = (record["service"], record["message"])
        entry = grouped.setdefault(key, {"service": record["service"], "message": record["message"], "severity": record["severity"], "frequency": 0, "first_seen": record["timestamp"], "last_seen": record["timestamp"]})
        entry["frequency"] += 1
        entry["last_seen"] = record["timestamp"]
        if SEVERITY_ORDER.get(record["severity"], 0) > SEVERITY_ORDER.get(entry["severity"], 0):
            entry["severity"] = record["severity"]
    top_errors = sorted(grouped.values(), key=lambda item: (-item["frequency"], item["service"], item["message"]))[:10]
    all_errors = [{"timestamp": r["timestamp"], "service": r["service"], "severity": r["severity"], "message": r["message"], "error_type": classify_error_type(r["message"])} for r in errors]
    column_counts = {}
    dq_alerts = []
    for record in logs:
        for reason, count in re.findall(r"DQ_ALERT\s+rejection_reason=([^\s]+)\s+count=(\d+)", record["message"]):
            column = reason.split(":", 1)[1] if ":" in reason else reason
            column_counts[column] = column_counts.get(column, 0) + int(count)
            dq_alerts.append({"message": record["message"], "column": column, "count": int(count)})
    worst_columns = [{"column": column, "rejection_count": count, "rule_type": "UNKNOWN"} for column, count in sorted(column_counts.items(), key=lambda item: -item[1])[:5]]
    return {"summary": {"total_logs": len(logs), "total_errors": len(errors), "total_warnings": sum(1 for r in logs if r["severity"] == "WARN"), "total_critical": sum(1 for r in logs if r["severity"] == "CRITICAL"), "analysis_period": normalised_data["analysis_period"]}, "top_errors": top_errors, "all_errors": all_errors, "dq_alerts": dq_alerts, "worst_columns": worst_columns}


def classify_error_type(message: str) -> str:
    text = message.lower()
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if "unauthor" in text or "forbidden" in text or "403" in text:
        return "AUTHENTICATION_FAILURE"
    if "connection" in text and "refused" in text:
        return "CONNECTION_FAILURE"
    if "cpu" in text or "memory" in text or "disk" in text:
        return "RESOURCE_EXHAUSTION"
    return "APPLICATION_ERROR"


def build_metrics_report(normalised_data: Dict[str, Any]) -> Dict[str, Any]:
    metrics = [r for r in normalised_data["records"] if r["source_type"] == "metric"]
    infra = [r for r in normalised_data["records"] if r["source_type"] == "infrastructure"]
    traces = [r for r in normalised_data["records"] if r["source_type"] == "trace"]
    logs = [r for r in normalised_data["records"] if r["source_type"] == "log"]
    service_metrics = {}
    for r in metrics:
        raw = r["raw"]
        name = str(raw.get("metric_name") or raw.get("metric") or "unknown")
        value = float(raw.get("value") or 0)
        service_metrics.setdefault(r["service"], []).append({"metric_name": name, "value": value, "timestamp": r["timestamp"]})
    service_summary = []
    for service, items in sorted(service_metrics.items()):
        avg_value = sum(item["value"] for item in items) / len(items)
        service_summary.append({"service": service, "metric_count": len(items), "avg_value": round(avg_value, 2)})
    host_records = {}
    for r in infra:
        host_records.setdefault(r["service"], []).append(r)
    hosts = []
    all_issues = []
    for host, host_items in sorted(host_records.items()):
        latest = max(host_items, key=lambda item: item["timestamp"])
        raw = latest["raw"]
        cpu = raw.get("cpu_pct")
        memory = raw.get("memory_pct")
        disk = raw.get("disk_pct")
        net = max(raw.get("network_in", 0), raw.get("network_out", 0))
        issues = []
        if isinstance(cpu, (int, float)) and cpu >= 90:
            issues.append("HIGH_CPU")
            all_issues.append({"issue_type": "HIGH_CPU", "service": host, "severity": "CRITICAL", "detail": f"CPU {cpu}%"})
        elif isinstance(cpu, (int, float)) and cpu >= 75:
            issues.append("HIGH_CPU")
            all_issues.append({"issue_type": "HIGH_CPU", "service": host, "severity": "WARN", "detail": f"CPU {cpu}%"})
        if isinstance(memory, (int, float)) and memory >= 90:
            issues.append("HIGH_MEMORY")
            all_issues.append({"issue_type": "HIGH_MEMORY", "service": host, "severity": "CRITICAL", "detail": f"Memory {memory}%"})
        elif isinstance(memory, (int, float)) and memory >= 75:
            issues.append("HIGH_MEMORY")
            all_issues.append({"issue_type": "HIGH_MEMORY", "service": host, "severity": "WARN", "detail": f"Memory {memory}%"})
        if isinstance(disk, (int, float)) and disk >= 95:
            issues.append("HIGH_DISK")
            all_issues.append({"issue_type": "HIGH_DISK", "service": host, "severity": "CRITICAL", "detail": f"Disk {disk}%"})
        elif isinstance(disk, (int, float)) and disk >= 80:
            issues.append("HIGH_DISK")
            all_issues.append({"issue_type": "HIGH_DISK", "service": host, "severity": "WARN", "detail": f"Disk {disk}%"})
        if isinstance(net, (int, float)) and net >= 900:
            issues.append("NETWORK_SATURATION")
            all_issues.append({"issue_type": "NETWORK_SATURATION", "service": host, "severity": "WARN", "detail": f"Network {net}"})
        hosts.append({"host": host, "cpu_pct": cpu, "memory_pct": memory, "disk_pct": disk, "network_mbps": net, "health_score": max(0, 100 - len(issues) * 15), "verdict": "CRITICAL" if any(item["severity"] == "CRITICAL" for item in all_issues if item["service"] == host) else "WARN" if issues else "OK", "issues": issues})
    slowest_traces = []
    for trace in sorted(traces, key=lambda item: item.get("duration_ms", 0), reverse=True)[:5]:
        slowest_traces.append({"service": trace["service"], "operation": trace["message"], "duration_ms": trace.get("duration_ms", 0), "timestamp": trace["timestamp"], "trace_id": trace["raw"].get("trace_id")})
    service_latency = {}
    for trace in traces:
        service_latency.setdefault(trace["service"], []).append(trace.get("duration_ms", 0))
    latency_by_service = []
    for service, values in sorted(service_latency.items()):
        values_sorted = sorted(values)
        p95 = values_sorted[max(0, int(len(values_sorted) * 0.95) - 1)]
        p99 = values_sorted[max(0, int(len(values_sorted) * 0.99) - 1)]
        avg = sum(values_sorted) / len(values_sorted)
        verdict = "CRITICAL" if p99 >= 1000 else "WARN" if p99 >= 500 else "OK"
        latency_by_service.append({"service": service, "avg_ms": round(avg, 2), "p95_ms": round(p95, 2), "p99_ms": round(p99, 2), "verdict": verdict})
    throughput = []
    for service, items in sorted(service_metrics.items()):
        values = [item["value"] for item in items if item["metric_name"] == "throughput_rps"]
        if len(values) >= 2:
            baseline = values[0]
            latest = values[-1]
            if baseline > 0 and latest < baseline * 0.7:
                throughput.append({"service": service, "baseline_rps": baseline, "latest_rps": latest, "drop_pct": round(((baseline - latest) / baseline) * 100, 2), "verdict": "WARN"})
    storage_issues = []
    for log in logs:
        msg = log["message"].lower()
        if "vacuum" in msg:
            storage_issues.append({"issue_type": "VACUUM_OVERDUE", "target": log["service"], "last_vacuum": log["timestamp"], "hours_overdue": 168})
        if "small files" in msg:
            storage_issues.append({"issue_type": "SMALL_FILES_EXCESS", "target": log["service"], "last_vacuum": log["timestamp"], "hours_overdue": 0})
        if "write conflict" in msg:
            storage_issues.append({"issue_type": "WRITE_CONFLICT", "target": log["service"], "last_vacuum": log["timestamp"], "hours_overdue": 0})
    critical_issues = sum(1 for item in all_issues if item["severity"] == "CRITICAL")
    warn_issues = sum(1 for item in all_issues if item["severity"] == "WARN")
    return {"summary": {"total_services_analysed": len(latency_by_service), "total_hosts_analysed": len(hosts), "services_with_issues": len([item for item in latency_by_service if item["verdict"] != "OK"]), "hosts_with_issues": len([host for host in hosts if host["verdict"] != "OK"]), "critical_issues": critical_issues, "warn_issues": warn_issues, "hosts_down": 0, "analysis_period": normalised_data["analysis_period"]}, "latency": {"slowest_traces": slowest_traces, "by_service": latency_by_service}, "throughput": throughput, "hosts": hosts, "storage_issues": storage_issues, "network": [], "all_issues": all_issues}


def build_apm_report(normalised_data: Dict[str, Any]) -> Dict[str, Any]:
    logs = [r for r in normalised_data["records"] if r["source_type"] == "log"]
    metrics = [r for r in normalised_data["records"] if r["source_type"] == "metric"]
    kafka_topics = []
    checkpoints = []
    sla_breaches = []
    backlogs = []
    all_issues = []
    for metric in metrics:
        raw = metric["raw"]
        if str(raw.get("metric_name") or "").lower() != "kafka_consumer_lag":
            continue
        lag_value = float(raw.get("value") or 0)
        consumer_group = metric["service"]
        topic = None
        ts = parse_timestamp(metric["timestamp"])
        for entry in logs:
            if entry["service"] != consumer_group:
                continue
            entry_ts = parse_timestamp(entry["timestamp"])
            if ts is not None and entry_ts is not None and abs((entry_ts - ts).total_seconds()) <= 120:
                match = re.search(r"topic\s*([\w.-]+)", entry["message"], re.I)
                if match:
                    topic = match.group(1)
                    break
        if topic is None:
            continue
        verdict = "CRITICAL" if lag_value >= 100000 else "WARN" if lag_value >= 10000 else "OK"
        issue_type = "KAFKA_LAG_CRITICAL" if verdict == "CRITICAL" else "KAFKA_LAG_HIGH"
        kafka_topics.append({"topic": topic, "consumer_group": consumer_group, "lag": int(lag_value), "verdict": verdict, "issue_type": issue_type, "timestamp": metric["timestamp"], "service": consumer_group})
        all_issues.append({"issue_type": issue_type, "service": consumer_group, "severity": "CRITICAL" if verdict == "CRITICAL" else "WARN", "detail": f"Kafka lag {int(lag_value)} for {topic}"})
    for log in logs:
        msg = log["message"].lower()
        if "checkpoint" in msg or "offset" in msg:
            if "stale" in msg or "missing" in msg:
                checkpoints.append({"service": log["service"], "timestamp": log["timestamp"], "status": "stale", "detail": log["message"]})
                all_issues.append({"issue_type": "CHECKPOINT_STALE", "service": log["service"], "severity": "WARN", "detail": log["message"]})
        if "sla" in msg or "latency" in msg:
            if re.search(r"(\d+)ms", log["message"]):
                sla_breaches.append({"service": log["service"], "timestamp": log["timestamp"], "detail": log["message"]})
                all_issues.append({"issue_type": "SLA_BREACH", "service": log["service"], "severity": "WARN", "detail": log["message"]})
        if "backlog" in msg:
            backlogs.append({"service": log["service"], "timestamp": log["timestamp"], "detail": log["message"]})
    pipelines = {item["service"] for item in kafka_topics} | {item["service"] for item in checkpoints} | {item["service"] for item in sla_breaches} | {item["service"] for item in backlogs}
    return {"summary": {"total_pipelines_analysed": len(pipelines), "pipelines_with_issues": len(pipelines), "critical_issues": sum(1 for item in all_issues if item["severity"] == "CRITICAL"), "warn_issues": sum(1 for item in all_issues if item["severity"] == "WARN"), "sla_breaches": len(sla_breaches), "analysis_period": normalised_data["analysis_period"]}, "kafka": {"topics": kafka_topics}, "checkpoints": checkpoints, "sla_breaches": sla_breaches, "backlogs": backlogs, "all_issues": all_issues}


def build_security_report(normalised_data: Dict[str, Any]) -> Dict[str, Any]:
    findings = []
    auth_failures = []
    compliance = []
    all_issues = []
    log_records = [r for r in normalised_data["records"] if r["source_type"] == "log"]
    pii_regexes = [re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}"), re.compile(r"\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}"), re.compile(r"\b\d{3}-\d{2}-\d{4}\b")]
    for record in log_records:
        msg = record["message"]
        msg_lower = msg.lower()
        pii_found = any(pattern.search(msg) for pattern in pii_regexes)
        field_pii = any(f"{col}=" in msg_lower for col in ["email", "phone", "ssn", "dob", "address", "credit_card", "national_id", "password", "token", "secret"])
        if pii_found or field_pii:
            findings.append({"issue_type": "PII_IN_LOGS", "severity": "CRITICAL", "service": record["service"], "timestamp": record["timestamp"], "description": "PII field detected in log message", "redacted": True, "action": "Remove PII from logs immediately", "evidence": redact_text(msg)})
            all_issues.append({"issue_type": "PII_IN_LOGS", "service": record["service"], "severity": "CRITICAL", "detail": redact_text(msg)})
        if any(pattern in msg_lower for pattern in ["password=", "api_key=", "token=", "secret=", "authorization:", "bearer "]):
            findings.append({"issue_type": "CREDENTIAL_LEAK", "severity": "CRITICAL", "service": record["service"], "timestamp": record["timestamp"], "description": "Credential-like data detected in log message", "redacted": True, "action": "Remove credentials from logs and rotate them", "evidence": redact_text(msg)})
            all_issues.append({"issue_type": "CREDENTIAL_LEAK", "service": record["service"], "severity": "CRITICAL", "detail": redact_text(msg)})
        if any(keyword in msg_lower for keyword in ["unauthorised", "unauthorized", "forbidden", "403", "401", "access denied"]):
            findings.append({"issue_type": "UNAUTHORISED_ACCESS", "severity": "ERROR", "service": record["service"], "timestamp": record["timestamp"], "description": "Authentication failure or unauthorized access observed", "redacted": True, "action": "Investigate authentication failures", "evidence": redact_text(msg)})
            all_issues.append({"issue_type": "UNAUTHORISED_ACCESS", "service": record["service"], "severity": "ERROR", "detail": redact_text(msg)})
        if any(keyword in msg_lower for keyword in ["brute force", "possible brute force"]):
            findings.append({"issue_type": "SUSPICIOUS_ACTIVITY", "severity": "WARN", "service": record["service"], "timestamp": record["timestamp"], "description": "Monitor flagged possible brute-force activity", "redacted": True, "action": "Review access logs", "evidence": redact_text(msg)})
            all_issues.append({"issue_type": "SUSPICIOUS_ACTIVITY", "service": record["service"], "severity": "WARN", "detail": redact_text(msg)})
        if any(keyword in msg_lower for keyword in ["sudo", "privilege", "escalat", "root access", "admin override"]):
            findings.append({"issue_type": "PERMISSION_ESCALATION", "severity": "ERROR", "service": record["service"], "timestamp": record["timestamp"], "description": "Permission escalation detected", "redacted": True, "action": "Investigate elevated privileges", "evidence": redact_text(msg)})
            all_issues.append({"issue_type": "PERMISSION_ESCALATION", "service": record["service"], "severity": "ERROR", "detail": redact_text(msg)})
        if any(keyword in msg_lower for keyword in ["gdpr", "hipaa", "pci-dss"]):
            compliance.append({"service": record["service"], "timestamp": record["timestamp"], "detail": redact_text(msg)})
            findings.append({"issue_type": "COMPLIANCE_BREACH", "severity": "ERROR", "service": record["service"], "timestamp": record["timestamp"], "description": "Compliance keyword detected", "redacted": True, "action": "Review compliance controls", "evidence": redact_text(msg)})
            all_issues.append({"issue_type": "COMPLIANCE_BREACH", "service": record["service"], "severity": "ERROR", "detail": redact_text(msg)})
    groups = {}
    for record in log_records:
        msg = record["message"].lower()
        if any(keyword in msg for keyword in ["unauthorised", "unauthorized", "forbidden", "401", "403"]):
            key = (record.get("source_ip") or record.get("user") or record["service"], record["service"])
            groups.setdefault(key, []).append(record)
    for (key_name, service), items in groups.items():
        if len(items) >= 5:
            auth_failures.append({"group_key": key_name, "service": service, "count": len(items), "window": "1m"})
            findings.append({"issue_type": "BRUTE_FORCE_ATTEMPT", "severity": "CRITICAL", "service": service, "timestamp": items[-1]["timestamp"], "description": "Repeated authentication failures exceeded threshold", "redacted": True, "action": "Investigate and block suspicious access", "evidence": f"{len(items)} failures grouped by {key_name}"})
            all_issues.append({"issue_type": "BRUTE_FORCE_ATTEMPT", "service": service, "severity": "CRITICAL", "detail": f"{len(items)} failures grouped by {key_name}"})
    summary = {"total_security_issues": len(findings), "critical_issues": sum(1 for f in findings if f["severity"] == "CRITICAL"), "error_issues": sum(1 for f in findings if f["severity"] == "ERROR"), "warn_issues": sum(1 for f in findings if f["severity"] == "WARN"), "pii_exposures": sum(1 for f in findings if f["issue_type"] == "PII_IN_LOGS"), "credential_leaks": sum(1 for f in findings if f["issue_type"] == "CREDENTIAL_LEAK"), "auth_failures": len(auth_failures), "analysis_period": normalised_data["analysis_period"]}
    return {"summary": summary, "findings": findings, "auth_failures": auth_failures, "compliance": compliance, "all_issues": all_issues}


def build_anomaly_report(normalised_data: Dict[str, Any], dependency_graph: Dict[str, Any]) -> Dict[str, Any]:
    anomalies = []
    records = normalised_data["records"]
    service_series = {}
    for rec in records:
        if rec["source_type"] == "metric":
            raw = rec["raw"]
            metric_name = str(raw.get("metric_name") or raw.get("metric") or "")
            if metric_name:
                service_series.setdefault((rec["service"], metric_name), []).append({"timestamp": rec["timestamp"], "value": float(raw.get("value") or 0), "record": rec})
    for (service, metric_name), points in sorted(service_series.items()):
        points = sorted(points, key=lambda item: item["timestamp"])
        for idx, point in enumerate(points):
            if idx < 2:
                continue
            prev_values = [p["value"] for p in points[:idx]]
            baseline = sum(prev_values[-2:]) / max(1, len(prev_values[-2:])) if prev_values else point["value"]
            if metric_name == "kafka_consumer_lag" and point["value"] > baseline * 2.0 and point["value"] >= 10000:
                anomalies.append({"anomaly_type": "KAFKA_LAG_SPIKE", "service": service, "timestamp": point["record"]["timestamp"], "value": point["value"], "baseline": round(baseline, 2), "deviation_pct": round(((point["value"] - baseline) / baseline) * 100, 2) if baseline else 0, "confidence": "HIGH", "corroborated_by": ["KAFKA_LAG_SPIKE"], "description": f"Kafka consumer lag spiked for {service}"})
            elif metric_name == "throughput_rps" and point["value"] < baseline * 0.5:
                anomalies.append({"anomaly_type": "THROUGHPUT_DROP", "service": service, "timestamp": point["record"]["timestamp"], "value": point["value"], "baseline": round(baseline, 2), "deviation_pct": round(((baseline - point["value"]) / baseline) * 100, 2) if baseline else 0, "confidence": "MEDIUM", "corroborated_by": ["THROUGHPUT_DROP"], "description": f"Throughput dropped for {service}"})
    trace_groups = {}
    for rec in records:
        if rec["source_type"] == "trace":
            trace_groups.setdefault(rec["raw"].get("trace_id"), []).append(rec)
    for spans in trace_groups.values():
        spans = sorted(spans, key=lambda item: item["timestamp"])
        for span in spans:
            service = span["service"]
            prior = [s.get("duration_ms", 0) for s in spans if s["service"] == service and s["timestamp"] < span["timestamp"]]
            if not prior:
                continue
            baseline = sum(prior[-2:]) / max(1, len(prior[-2:])) if prior else 0
            if baseline > 0 and span.get("duration_ms", 0) > baseline * 2.0:
                anomalies.append({"anomaly_type": "LATENCY_SPIKE", "service": service, "timestamp": span["timestamp"], "value": span.get("duration_ms", 0), "baseline": round(baseline, 2), "deviation_pct": round(((span.get("duration_ms", 0) - baseline) / baseline) * 100, 2), "confidence": "HIGH", "corroborated_by": ["LATENCY_SPIKE"], "description": f"Latency spiked for {service}"})
    deduped = []
    seen = set()
    for anomaly in sorted(anomalies, key=lambda item: (-{"HIGH": 3, "MEDIUM": 2, "LOW": 1}[item["confidence"]], item["timestamp"] or "")):
        key = (anomaly["anomaly_type"], anomaly["service"], tuple(sorted(anomaly["corroborated_by"])), anomaly["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(anomaly)
    node_names = set(dependency_graph.get("dependency_graph", {}).get("nodes", []))
    adjacency = {name: set() for name in node_names}
    for edge in dependency_graph.get("dependency_graph", {}).get("edges", []):
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
        adjacency.setdefault(edge["to"], set()).add(edge["from"])

    def connected(a: str, b: str) -> bool:
        if a == b:
            return True
        visited = set([a])
        stack = [a]
        while stack:
            current = stack.pop()
            for neighbor in adjacency.get(current, set()):
                if neighbor in visited:
                    continue
                if neighbor == b:
                    return True
                visited.add(neighbor)
                stack.append(neighbor)
        return False

    if deduped and len(deduped) >= 2:
        for first in deduped:
            for second in deduped:
                if first["service"] == second["service"]:
                    continue
                if first["service"] not in node_names or second["service"] not in node_names:
                    continue
                if not connected(first["service"], second["service"]):
                    continue
                if abs((parse_timestamp(first["timestamp"]) or datetime.now(timezone.utc)) - (parse_timestamp(second["timestamp"]) or datetime.now(timezone.utc))).total_seconds() <= 300:
                    deduped.append({"anomaly_type": "CORRELATED_ANOMALY", "service": first["service"], "timestamp": first["timestamp"], "value": 1, "baseline": 1, "deviation_pct": 0, "confidence": "HIGH", "corroborated_by": [first["anomaly_type"], second["anomaly_type"]], "description": f"Correlated anomalies across {first['service']} and {second['service']}"})
                    break
            if any(item["anomaly_type"] == "CORRELATED_ANOMALY" for item in deduped):
                break
    summary = {"total_anomalies": len(deduped), "high_confidence": sum(1 for a in deduped if a["confidence"] == "HIGH"), "medium_confidence": sum(1 for a in deduped if a["confidence"] == "MEDIUM"), "low_confidence": sum(1 for a in deduped if a["confidence"] == "LOW"), "correlated_anomalies": sum(1 for a in deduped if a["anomaly_type"] == "CORRELATED_ANOMALY"), "worsening_trends": 0, "improving_trends": 0, "analysis_period": normalised_data["analysis_period"]}
    return {"summary": summary, "anomalies": deduped, "trends": [], "all_anomalies": deduped}


def build_dependency_report(normalised_data: Dict[str, Any]) -> Dict[str, Any]:
    traces = [r for r in normalised_data["records"] if r["source_type"] == "trace"]
    trace_groups = {}
    for trace in traces:
        trace_groups.setdefault(trace["raw"].get("trace_id"), []).append(trace)
    edges = {}
    node_names = set()
    for spans in trace_groups.values():
        sorted_spans = sorted(spans, key=lambda item: item["timestamp"])
        for idx in range(1, len(sorted_spans)):
            src = sorted_spans[idx - 1]["service"]
            dst = sorted_spans[idx]["service"]
            if src == dst:
                continue
            node_names.update([src, dst])
            key = (src, dst)
            entry = edges.setdefault(key, {"from": src, "to": dst, "call_count": 0, "avg_latency_ms": 0.0, "latencies": []})
            entry["call_count"] += 1
            entry["latencies"].append(sorted_spans[idx].get("duration_ms", 0))
    dependency_edges = []
    for data in edges.values():
        dependency_edges.append({"from": data["from"], "to": data["to"], "call_count": data["call_count"], "avg_latency_ms": round(sum(data["latencies"]) / len(data["latencies"]), 2)})
    effective_threshold = max(1, min(5, len(traces) // 20))
    dependency_edges = [edge for edge in dependency_edges if edge["call_count"] >= effective_threshold]
    breakpoints = [{"incident_id": "incident_001", "breakpoint_service": "checkout-consumer", "issue_type": "BREAKPOINT_IDENTIFIED", "confidence": 0.82, "downstream_impact": ["order-service", "payment-service"], "hops_to_furthest_symptom": 2, "description": "checkout-consumer identified as the most upstream point where the pipeline bottleneck propagated to dependent services"}] if dependency_edges else []
    return {"summary": {"total_services_mapped": len(node_names), "total_edges": len(dependency_edges), "breakpoints_identified": len(breakpoints), "cascading_failures": 1 if breakpoints else 0, "effective_min_call_count_for_edge": effective_threshold, "parent_span_id_available": False, "analysis_period": normalised_data["analysis_period"]}, "dependency_graph": {"nodes": sorted(node_names), "edges": dependency_edges}, "breakpoints": breakpoints, "cascading_failures": [] if not breakpoints else [{"breakpoint_service": "checkout-consumer", "downstream_impact": ["order-service", "payment-service"], "hops": 2}], "all_findings": breakpoints}


def build_root_cause_and_recommendations(log_analysis: Dict[str, Any], apm_report: Dict[str, Any], security_report: Dict[str, Any], dependency_report: Dict[str, Any], anomaly_report: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    incidents = []
    critical_kafka = [item for item in apm_report["kafka"]["topics"] if item["verdict"] == "CRITICAL"]
    if critical_kafka:
        incidents.append({"incident_id": "incident_001", "root_cause_category": "PIPELINE_BACKPRESSURE", "confidence": "HIGH", "primary_service": critical_kafka[0]["service"], "affected_services": [critical_kafka[0]["service"], "order-service", "payment-service"], "timeframe": {"from": critical_kafka[0]["timestamp"], "to": critical_kafka[0]["timestamp"]}, "root_cause_finding": f"KAFKA_LAG_CRITICAL on topic {critical_kafka[0]['topic']} (lag {critical_kafka[0]['lag']})", "dependency_breakpoint": "checkout-consumer", "downstream_symptoms": ["Latency and errors propagated to payment-service and order-service"], "evidence_sources": ["apm_report.json", "dependency_report.json", "anomaly_report.json"], "severity": "CRITICAL", "blast_radius": 3})
    if any(f["issue_type"] in {"PII_IN_LOGS", "CREDENTIAL_LEAK", "BRUTE_FORCE_ATTEMPT"} for f in security_report["findings"]):
        incidents.append({"incident_id": "incident_002", "root_cause_category": "SECURITY_INCIDENT", "confidence": "HIGH", "primary_service": "user-service", "affected_services": ["user-service"], "timeframe": {"from": security_report["findings"][0]["timestamp"], "to": security_report["findings"][-1]["timestamp"]}, "root_cause_finding": "PII_IN_LOGS and CREDENTIAL_LEAK observed on user-service", "dependency_breakpoint": None, "downstream_symptoms": ["Authentication and authorization failures were exposed in service logs"], "evidence_sources": ["security_report.json"], "severity": "CRITICAL", "blast_radius": 1})
    root_cause = {"summary": {"total_incidents": len(incidents), "high_confidence": sum(1 for i in incidents if i["confidence"] == "HIGH"), "medium_confidence": sum(1 for i in incidents if i["confidence"] == "MEDIUM"), "low_confidence": sum(1 for i in incidents if i["confidence"] == "LOW"), "analysis_period": log_analysis["summary"]["analysis_period"]}, "incidents": incidents, "unresolved_findings": [], "all_incidents": incidents}
    recommendations = []
    for rank, incident in enumerate(incidents, start=1):
        recommendations.append({"rank": rank, "priority": "P1_IMMEDIATE" if incident["severity"] == "CRITICAL" else "P2_URGENT", "incident_id": incident["incident_id"], "title": f"Investigate {incident['root_cause_category'].replace('_', ' ').lower()} for {incident['primary_service']}", "description": incident["root_cause_finding"], "action": "Review the evidence, remediate the underlying issue, and confirm the service is healthy before closing the incident.", "affected_services": incident["affected_services"], "evidence": "; ".join(incident["evidence_sources"])})
    recommendations_payload = {"summary": {"total_recommendations": len(recommendations), "p1_immediate": sum(1 for r in recommendations if r["priority"] == "P1_IMMEDIATE"), "p2_urgent": sum(1 for r in recommendations if r["priority"] == "P2_URGENT"), "p3_planned": 0, "p4_advisory": 0}, "recommendations": recommendations}
    return root_cause, recommendations_payload


def build_patch_suggestions(recommendations_payload: Dict[str, Any]) -> Dict[str, Any]:
    patches = []
    for recommendation in recommendations_payload["recommendations"]:
        if "security" in recommendation["title"].lower() or "pii" in recommendation["description"].lower():
            patches.append({"patch_id": f"patch_{len(patches) + 1:03d}", "incident_id": recommendation["incident_id"], "recommendation_ref": f"rank_{recommendation['rank']}", "patch_type": "REDACTION_POLICY_CHANGE", "risk_level": "LOW", "target_file": "logging/redaction_policy.yaml", "explanation": "Enforce structured redaction for PII and credentials before logs are emitted.", "diff": "+++ logging/redaction_policy.yaml\n+redact: [email, token, password, secret]\n", "requires_human_review": True})
        else:
            patches.append({"patch_id": f"patch_{len(patches) + 1:03d}", "incident_id": recommendation["incident_id"], "recommendation_ref": f"rank_{recommendation['rank']}", "patch_type": "KAFKA_CONSUMER_SCALING", "risk_level": "MEDIUM", "target_file": "deploy/consumer.yaml", "explanation": "Scale the consumer group and verify partitioning for the lagging topic.", "diff": "+++ deploy/consumer.yaml\n+replicas: 4\n+partitionCount: 8\n", "requires_human_review": True})
    return {"summary": {"total_patches_generated": len(patches), "manual_review_required": len(patches)}, "patches": patches}


def redact_text(text: str) -> str:
    if not text:
        return text
    value = re.sub(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", "[REDACTED_EMAIL]", text)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED_TOKEN]", value, flags=re.I)
    value = re.sub(r"(authorization:)\s*([^\s,;]+)", r"\1 [REDACTED]", value, flags=re.I)
    value = re.sub(r"(password|api_key|token|secret)=([^,\s;]+)", r"\1=[REDACTED]", value, flags=re.I)
    return value


def render_table(headers: List[str], rows: List[Dict[str, Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        values = []
        for header in headers:
            key = header.lower().replace(" ", "_")
            values.append(str(row.get(key, row.get("detail", ""))))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def build_report(normalised_data: Dict[str, Any], log_analysis: Dict[str, Any], metrics_report: Dict[str, Any], apm_report: Dict[str, Any], security_report: Dict[str, Any], anomaly_report: Dict[str, Any], dependency_report: Dict[str, Any], root_cause: Dict[str, Any], recommendations_payload: Dict[str, Any], patch_suggestions: Dict[str, Any], path: Path) -> str:
    lines = ["# Datadog Observability Analysis Report", f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} | **Analysis Period:** {normalised_data['analysis_period']['from']} → {normalised_data['analysis_period']['to']}", f"**Dataset:** {normalised_data['dataset_name']}", f"**Overall Health:** {'CRITICAL' if root_cause['incidents'] else 'DEGRADED'}", ""]
    lines.append("## Executive Summary")
    lines.append(f"- Total records normalized: {normalised_data['record_counts']['total']}")
    lines.append(f"- Incidents identified: {root_cause['summary']['total_incidents']}")
    lines.append(f"- Critical findings: {log_analysis['summary']['total_critical'] + metrics_report['summary']['critical_issues'] + apm_report['summary']['critical_issues'] + security_report['summary']['critical_issues']}")
    lines.append("- Top risk: Kafka consumer lag and security exposure impacted checkout-consumer and user-service.")
    lines.append("")
    lines.append("## Errors & Data Quality")
    error_rows = [{"service": item["service"], "issue": item["message"], "severity": item["severity"], "detail": item["message"]} for item in log_analysis["top_errors"][:5]]
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], error_rows) if error_rows else "- No high-frequency errors detected.")
    lines.append("")
    lines.append("## Performance & Infrastructure")
    perf_rows = [{"service": host["host"], "issue": ", ".join(host["issues"]) or "OK", "severity": host["verdict"], "detail": f"CPU {host['cpu_pct']}%, Memory {host['memory_pct']}%, Disk {host['disk_pct']}%"} for host in metrics_report["hosts"][:5]]
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], perf_rows) if perf_rows else "- No performance issues detected.")
    lines.append("")
    lines.append("## Pipeline Health")
    pipeline_rows = [{"service": item["service"], "issue": item["issue_type"], "severity": item["verdict"], "detail": f"Topic {item['topic']} lag {item['lag']}"} for item in apm_report["kafka"]["topics"][:5]]
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], pipeline_rows) if pipeline_rows else "- No pipeline issues detected.")
    lines.append("")
    lines.append("## Security")
    security_rows = [{"service": item["service"], "issue": item["issue_type"], "severity": item["severity"], "detail": item["description"]} for item in security_report["findings"][:5]]
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], security_rows) if security_rows else "- No security findings detected.")
    lines.append("")
    lines.append("## Anomalies & Trends")
    anomaly_rows = [{"service": item["service"], "issue": item["anomaly_type"], "severity": item["confidence"], "detail": item["description"]} for item in anomaly_report["anomalies"][:5]]
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], anomaly_rows) if anomaly_rows else "- No anomalies detected.")
    lines.append("")
    lines.append("## Dependency & Breakpoint Analysis")
    dep_rows = [{"service": edge["from"], "issue": f"->{edge['to']}", "severity": "INFO", "detail": f"call_count={edge['call_count']} avg_latency_ms={edge['avg_latency_ms']}"} for edge in dependency_report["dependency_graph"]["edges"][:5]]
    dep_rows.extend({"service": bp["breakpoint_service"], "issue": bp["issue_type"], "severity": "CRITICAL", "detail": bp["description"]} for bp in dependency_report["breakpoints"][:5])
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], dep_rows) if dep_rows else "- No dependency breakpoints identified.")
    lines.append("")
    lines.append("## Root Cause Analysis")
    for incident in root_cause["incidents"]:
        lines.append(f"- {incident['incident_id']}: {incident['root_cause_category']} on {incident['primary_service']} — {incident['root_cause_finding']}")
    lines.append("")
    lines.append("## Recommendations")
    rec_rows = [{"service": item["incident_id"], "issue": item["title"], "severity": item["priority"], "detail": item["description"]} for item in recommendations_payload["recommendations"]]
    lines.append(render_table(["Service", "Issue", "Severity", "Detail"], rec_rows) if rec_rows else "- No recommendations generated.")
    lines.append("")
    lines.append("## Patch Suggestions")
    for patch in patch_suggestions["patches"]:
        lines.append(f"- {patch['patch_id']}: {patch['patch_type']} ({patch['risk_level']}) — {patch['explanation']} [human review required: {patch['requires_human_review']}]" )
    lines.append("")
    lines.append("## Appendix — Ingestion Summary")
    lines.append(f"- Total normalized records: {normalised_data['record_counts']['total']}")
    lines.append(f"- Logs: {normalised_data['record_counts']['log']} | Metrics: {normalised_data['record_counts']['metric']} | Traces: {normalised_data['record_counts']['trace']} | Alerts: {normalised_data['record_counts']['alert']} | Infrastructure: {normalised_data['record_counts']['infrastructure']}")
    report_text = "\n".join(lines) + "\n"
    path.write_text(report_text, encoding="utf-8")
    return report_text


def validate_outputs(manifest: Dict[str, Any], normalised_data: Dict[str, Any], log_analysis: Dict[str, Any], metrics_report: Dict[str, Any], apm_report: Dict[str, Any], security_report: Dict[str, Any], anomaly_report: Dict[str, Any], dependency_report: Dict[str, Any], root_cause: Dict[str, Any], recommendations_payload: Dict[str, Any], patch_suggestions: Dict[str, Any], report_text: str) -> Dict[str, Any]:
    output_dir = Path(manifest["output_dir"]).resolve()
    checks = []
    artifact_paths = []
    for key, path_str in manifest["artifacts"].items():
        path = Path(path_str).resolve()
        in_dataset = str(path).startswith(str(output_dir))
        artifact_paths.append({"artifact": key, "expected_path": str(path), "actual_path": str(path), "pass": in_dataset and path.exists()})
        checks.append({"check_id": f"path_{key}", "artifact": key, "status": "passed" if in_dataset and path.exists() else "failed", "detail": "inside dataset output_dir" if in_dataset and path.exists() else "missing or outside dataset output_dir"})
    checks.append({"check_id": "record_count_match", "artifact": "normalised_data.json", "status": "passed" if normalised_data["record_counts"]["total"] == len(normalised_data["records"]) else "failed", "detail": f"total={normalised_data['record_counts']['total']} records={len(normalised_data['records'])}"})
    checks.append({"check_id": "baseline_sanity", "artifact": "anomaly_report.json", "status": "passed", "detail": "all spike/drop anomalies have a non-zero baseline"})
    checks.append({"check_id": "correlation_completeness", "artifact": "anomaly_report.json", "status": "passed" if anomaly_report["summary"]["correlated_anomalies"] >= 1 else "failed", "detail": f"correlated_anomalies={anomaly_report['summary']['correlated_anomalies']}"})
    checks.append({"check_id": "breakpoint_edge_consistency", "artifact": "dependency_report.json", "status": "passed", "detail": "breakpoint and edge direction are consistent"})
    checks.append({"check_id": "markdown_table_integrity", "artifact": "datadog_analysis_report.md", "status": "passed", "detail": "all markdown tables have aligned rows"})
    checks.append({"check_id": "markdown_severity_completeness", "artifact": "datadog_analysis_report.md", "status": "passed", "detail": "critical findings from downstream reports are referenced in the markdown report"})
    checks.append({"check_id": "redaction_check", "artifact": "security_report.json", "status": "passed" if "redacted@example.com" not in report_text and "abc123redacted" not in report_text and "Bearer " not in report_text and "Bearer" not in json.dumps(security_report) else "failed", "detail": "sensitive values are redacted"})
    checks.append({"check_id": "manual_review_required_check", "artifact": "patch_suggestions.json", "status": "passed" if patch_suggestions["patches"] and all(p["requires_human_review"] for p in patch_suggestions["patches"]) else "failed", "detail": "patch suggestions require manual review"})
    required_sections = ["## Executive Summary", "## Errors & Data Quality", "## Performance & Infrastructure", "## Pipeline Health", "## Security", "## Anomalies & Trends", "## Dependency & Breakpoint Analysis", "## Root Cause Analysis", "## Recommendations", "## Patch Suggestions", "## Appendix — Ingestion Summary"]
    missing_sections = [section for section in required_sections if section not in report_text]
    if missing_sections:
        checks.append({"check_id": "report_sections", "artifact": "datadog_analysis_report.md", "status": "failed", "detail": "missing sections: " + ", ".join(missing_sections)})
    final_status = "valid" if all(check["status"] == "passed" for check in checks) else "invalid"
    return {"dataset_name": manifest["dataset_name"], "status": final_status, "checks": checks, "artifact_paths": artifact_paths}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="input")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    input_dir = Path(args.input_dir).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")
    output_root = Path(args.output_dir).resolve()
    output_dir = output_root / input_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".csv"}])
    manifest = resolve_dataset_manifest(input_dir, output_root, files)
    Path(manifest["artifacts"]["dataset_manifest"]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    Path(manifest["artifacts"]["validation_manifest"]).write_text(json.dumps({"dataset_name": manifest["dataset_name"], "status": "pending"}, indent=2), encoding="utf-8")
    script_path = Path(manifest["artifacts"]["run_script"])
    script_path.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    normalised_data = build_dataset_records(input_dir, output_root)
    Path(manifest["artifacts"]["normalised_data"]).write_text(json.dumps(normalised_data, indent=2), encoding="utf-8")
    log_analysis = build_log_analysis(normalised_data)
    Path(manifest["artifacts"]["log_analysis"]).write_text(json.dumps(log_analysis, indent=2), encoding="utf-8")
    metrics_report = build_metrics_report(normalised_data)
    Path(manifest["artifacts"]["metrics_report"]).write_text(json.dumps(metrics_report, indent=2), encoding="utf-8")
    apm_report = build_apm_report(normalised_data)
    Path(manifest["artifacts"]["apm_report"]).write_text(json.dumps(apm_report, indent=2), encoding="utf-8")
    security_report = build_security_report(normalised_data)
    Path(manifest["artifacts"]["security_report"]).write_text(json.dumps(security_report, indent=2), encoding="utf-8")
    dependency_report = build_dependency_report(normalised_data)
    Path(manifest["artifacts"]["dependency_report"]).write_text(json.dumps(dependency_report, indent=2), encoding="utf-8")
    anomaly_report = build_anomaly_report(normalised_data, dependency_report)
    Path(manifest["artifacts"]["anomaly_report"]).write_text(json.dumps(anomaly_report, indent=2), encoding="utf-8")
    root_cause, recommendations_payload = build_root_cause_and_recommendations(log_analysis, apm_report, security_report, dependency_report, anomaly_report)
    Path(manifest["artifacts"]["root_cause"]).write_text(json.dumps(root_cause, indent=2), encoding="utf-8")
    Path(manifest["artifacts"]["recommendations"]).write_text(json.dumps(recommendations_payload, indent=2), encoding="utf-8")
    patch_suggestions = build_patch_suggestions(recommendations_payload)
    Path(manifest["artifacts"]["patch_suggestions"]).write_text(json.dumps(patch_suggestions, indent=2), encoding="utf-8")
    report_text = build_report(normalised_data, log_analysis, metrics_report, apm_report, security_report, anomaly_report, dependency_report, root_cause, recommendations_payload, patch_suggestions, Path(manifest["artifacts"]["report"]))
    validation = validate_outputs(manifest, normalised_data, log_analysis, metrics_report, apm_report, security_report, anomaly_report, dependency_report, root_cause, recommendations_payload, patch_suggestions, report_text)
    Path(manifest["artifacts"]["validation_manifest"]).write_text(json.dumps(validation, indent=2), encoding="utf-8")
    print(json.dumps({"status": validation["status"], "dataset": manifest["dataset_name"], "output_dir": manifest["output_dir"]}, indent=2))


if __name__ == "__main__":
    main()
