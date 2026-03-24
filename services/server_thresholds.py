from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from extensions import db
from models.server_threshold_config import ServerThresholdConfig


PRIMARY_HEALTH_METRICS = ("cpu_usage_pct", "memory_usage_pct", "disk_usage_pct")
HEALTH_SCORE_WEIGHTS: dict[str, dict[str, float]] = {
    "cpu_usage_pct": {"warning": 15.0, "critical": 30.0},
    "memory_usage_pct": {"warning": 15.0, "critical": 30.0},
    "disk_usage_pct": {"warning": 15.0, "critical": 30.0},
    "zombie_count": {"warning": 5.0, "critical": 10.0},
}

METRIC_CATALOG: dict[str, dict[str, Any]] = {
    "cpu_usage_pct": {"label": "CPU Usage", "unit": "%", "category": "cpu", "default_enabled": True, "default_warning": 80.0, "default_critical": 95.0},
    "memory_usage_pct": {"label": "Memory Usage", "unit": "%", "category": "memory", "default_enabled": True, "default_warning": 85.0, "default_critical": 95.0},
    "disk_usage_pct": {"label": "Disk Usage", "unit": "%", "category": "disk", "default_enabled": True, "default_warning": 80.0, "default_critical": 95.0},
    "cpu_iowait_pct": {"label": "CPU IO Wait", "unit": "%", "category": "cpu", "default_enabled": False, "default_warning": 20.0, "default_critical": 40.0},
    "cpu_steal_pct": {"label": "CPU Steal", "unit": "%", "category": "cpu", "default_enabled": False, "default_warning": 10.0, "default_critical": 20.0},
    "network_in_mb_per_sec": {"label": "Network In", "unit": "MB/s", "category": "network", "default_enabled": False, "default_warning": 250.0, "default_critical": 500.0},
    "network_out_mb_per_sec": {"label": "Network Out", "unit": "MB/s", "category": "network", "default_enabled": False, "default_warning": 250.0, "default_critical": 500.0},
    "load_avg_1min": {"label": "Load Avg 1m", "unit": "", "category": "cpu", "default_enabled": False, "default_warning": 4.0, "default_critical": 8.0},
    "load_avg_5min": {"label": "Load Avg 5m", "unit": "", "category": "cpu", "default_enabled": False, "default_warning": 4.0, "default_critical": 8.0},
    "load_avg_15min": {"label": "Load Avg 15m", "unit": "", "category": "cpu", "default_enabled": False, "default_warning": 4.0, "default_critical": 8.0},
    "swap_percent": {"label": "Swap Usage", "unit": "%", "category": "memory", "default_enabled": False, "default_warning": 50.0, "default_critical": 80.0},
    "page_faults_per_sec": {"label": "Page Faults / sec", "unit": "/s", "category": "memory", "default_enabled": False, "default_warning": 2000.0, "default_critical": 5000.0},
    "disk_busy_pct": {"label": "Disk Busy", "unit": "%", "category": "disk", "default_enabled": False, "default_warning": 70.0, "default_critical": 90.0},
    "disk_read_latency_ms": {"label": "Disk Read Latency", "unit": "ms", "category": "disk", "default_enabled": False, "default_warning": 20.0, "default_critical": 50.0},
    "disk_write_latency_ms": {"label": "Disk Write Latency", "unit": "ms", "category": "disk", "default_enabled": False, "default_warning": 20.0, "default_critical": 50.0},
    "tcp_retransmits_delta": {"label": "TCP Retransmits Delta", "unit": "", "category": "network", "default_enabled": False, "default_warning": 20.0, "default_critical": 100.0},
    "network_connections_total": {"label": "Total Connections", "unit": "", "category": "network", "default_enabled": False, "default_warning": 5000.0, "default_critical": 15000.0},
    "network_connections_established": {"label": "Established Connections", "unit": "", "category": "network", "default_enabled": False, "default_warning": 2000.0, "default_critical": 8000.0},
    "network_connections_unique_ips": {"label": "Unique Remote IPs", "unit": "", "category": "network", "default_enabled": False, "default_warning": 250.0, "default_critical": 1000.0},
    "process_count": {"label": "Process Count", "unit": "", "category": "process", "default_enabled": False, "default_warning": 1000.0, "default_critical": 2000.0},
    "zombie_count": {"label": "Zombie Count", "unit": "", "category": "process", "default_enabled": False, "default_warning": 1.0, "default_critical": 5.0},
    "context_switches_per_sec": {"label": "Context Switches / sec", "unit": "/s", "category": "process", "default_enabled": False, "default_warning": 50000.0, "default_critical": 100000.0},
    "fd_percent": {"label": "File Descriptor Usage", "unit": "%", "category": "process", "default_enabled": False, "default_warning": 70.0, "default_critical": 90.0},
}

THRESHOLD_ALLOWED_FIELDS = {"enabled", "warning", "critical"}


class ThresholdValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MetricEvaluation:
    metric_key: str
    value: float | None
    state: str
    threshold: dict[str, Any]


@dataclass(frozen=True)
class HealthSummary:
    score: int
    state: str
    penalties: list[dict[str, Any]]
    evaluations: dict[str, MetricEvaluation]


def build_default_thresholds() -> dict[str, Any]:
    metrics = {}
    for metric_key, meta in METRIC_CATALOG.items():
        metrics[metric_key] = {
            "enabled": bool(meta.get("default_enabled", False)),
            "warning": float(meta.get("default_warning", 0.0)),
            "critical": float(meta.get("default_critical", 0.0)),
            "unit": meta.get("unit") or "",
            "label": meta.get("label") or metric_key,
            "category": meta.get("category") or "other",
            "operator": ">=",
        }
    return {"metrics": metrics}


def _merge_saved_thresholds(saved_thresholds: dict[str, Any] | None) -> dict[str, Any]:
    merged = build_default_thresholds()
    metrics_payload = {}
    if isinstance(saved_thresholds, dict):
        metrics_payload = saved_thresholds.get("metrics") if isinstance(saved_thresholds.get("metrics"), dict) else {}
    for metric_key, payload in metrics_payload.items():
        if metric_key not in merged["metrics"] or not isinstance(payload, dict):
            continue
        target = merged["metrics"][metric_key]
        if "enabled" in payload:
            target["enabled"] = bool(payload.get("enabled"))
        if "warning" in payload and payload.get("warning") is not None:
            target["warning"] = float(payload.get("warning"))
        if "critical" in payload and payload.get("critical") is not None:
            target["critical"] = float(payload.get("critical"))
    return merged


def ensure_threshold_config_row() -> ServerThresholdConfig:
    row = db.session.get(ServerThresholdConfig, 1)
    if row is None:
        row = ServerThresholdConfig(
            id=1,
            version=1,
            thresholds_json=build_default_thresholds(),
        )
        db.session.add(row)
        db.session.flush()
    elif not isinstance(row.thresholds_json, dict) or "metrics" not in row.thresholds_json:
        row.thresholds_json = build_default_thresholds()
        if not row.version:
            row.version = 1
        db.session.flush()
    return row


def get_threshold_config_row() -> ServerThresholdConfig:
    row = db.session.get(ServerThresholdConfig, 1)
    if row is None:
        row = ensure_threshold_config_row()
        db.session.commit()
    return row


def get_merged_thresholds() -> dict[str, Any]:
    row = get_threshold_config_row()
    return _merge_saved_thresholds(row.thresholds_json)


def normalize_threshold_patch(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    body = payload if isinstance(payload, dict) else {}
    metrics = body.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ThresholdValidationError("metrics payload is required")

    normalized: dict[str, dict[str, Any]] = {}
    for metric_key, config in metrics.items():
        if metric_key not in METRIC_CATALOG:
            raise ThresholdValidationError(f"Unknown metric key: {metric_key}")
        if not isinstance(config, dict):
            raise ThresholdValidationError(f"Metric config must be an object for {metric_key}")
        unknown_fields = set(config.keys()) - THRESHOLD_ALLOWED_FIELDS
        if unknown_fields:
            raise ThresholdValidationError(
                f"Unknown threshold fields for {metric_key}: {', '.join(sorted(unknown_fields))}"
            )

        normalized_metric: dict[str, Any] = {}
        if "enabled" in config:
            normalized_metric["enabled"] = bool(config.get("enabled"))
        if "warning" in config:
            warning = config.get("warning")
            if warning is None:
                raise ThresholdValidationError(f"warning is required when supplied for {metric_key}")
            normalized_metric["warning"] = float(warning)
        if "critical" in config:
            critical = config.get("critical")
            if critical is None:
                raise ThresholdValidationError(f"critical is required when supplied for {metric_key}")
            normalized_metric["critical"] = float(critical)
        normalized[metric_key] = normalized_metric
    return normalized


def apply_threshold_patch(base_thresholds: dict[str, Any], patch_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = _merge_saved_thresholds(base_thresholds)
    for metric_key, patch in patch_metrics.items():
        target = merged["metrics"][metric_key]
        target.update(patch)
        if float(target["critical"]) <= float(target["warning"]):
            raise ThresholdValidationError(f"critical must be greater than warning for {metric_key}")
    return merged


def serialize_threshold_profile(row: ServerThresholdConfig | None = None, thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    config_row = row or get_threshold_config_row()
    merged = thresholds or _merge_saved_thresholds(config_row.thresholds_json)
    return {
        "scope": "global",
        "version": int(config_row.version or 1),
        "updated_at": config_row.updated_at.isoformat() if config_row.updated_at else None,
        "updated_by": config_row.updated_by,
        "change_reason": config_row.change_reason,
        "metrics": deepcopy(merged.get("metrics", {})),
    }


def extract_latest_metrics(log) -> dict[str, float | None]:
    if log is None:
        return {metric_key: None for metric_key in METRIC_CATALOG}
    network_in = getattr(log, "network_in_bps", None)
    network_out = getattr(log, "network_out_bps", None)
    divisor = 1024.0 * 1024.0
    return {
        "cpu_usage_pct": getattr(log, "cpu_usage", None),
        "memory_usage_pct": getattr(log, "memory_usage", None),
        "disk_usage_pct": getattr(log, "disk_usage", None),
        "cpu_iowait_pct": getattr(log, "cpu_iowait_percent", None),
        "cpu_steal_pct": getattr(log, "cpu_steal_percent", None),
        "network_in_mb_per_sec": (float(network_in) / divisor) if network_in is not None else None,
        "network_out_mb_per_sec": (float(network_out) / divisor) if network_out is not None else None,
        "load_avg_1min": getattr(log, "load_avg_1min", None),
        "load_avg_5min": getattr(log, "load_avg_5min", None),
        "load_avg_15min": getattr(log, "load_avg_15min", None),
        "swap_percent": getattr(log, "swap_percent", None),
        "page_faults_per_sec": getattr(log, "page_faults_per_sec", None),
        "disk_busy_pct": getattr(log, "disk_busy_percent", None),
        "disk_read_latency_ms": getattr(log, "disk_read_latency_ms", None),
        "disk_write_latency_ms": getattr(log, "disk_write_latency_ms", None),
        "tcp_retransmits_delta": getattr(log, "tcp_retransmits_delta", None),
        "network_connections_total": getattr(log, "network_connections_total", None),
        "network_connections_established": getattr(log, "network_connections_established", None),
        "network_connections_unique_ips": getattr(log, "network_connections_unique_ips", None),
        "process_count": getattr(log, "process_count", None),
        "zombie_count": getattr(log, "zombie_count", None),
        "context_switches_per_sec": getattr(log, "context_switches_per_sec", None),
        "fd_percent": getattr(log, "fd_percent", None),
    }


def evaluate_metric(metric_key: str, value: float | None, thresholds: dict[str, Any]) -> MetricEvaluation:
    metric_threshold = thresholds.get("metrics", {}).get(metric_key)
    if not metric_threshold:
        raise KeyError(metric_key)
    if value is None:
        return MetricEvaluation(metric_key=metric_key, value=None, state="unknown", threshold=metric_threshold)
    numeric_value = float(value)
    if not metric_threshold.get("enabled"):
        return MetricEvaluation(metric_key=metric_key, value=numeric_value, state="disabled", threshold=metric_threshold)
    if numeric_value >= float(metric_threshold["critical"]):
        return MetricEvaluation(metric_key=metric_key, value=numeric_value, state="critical", threshold=metric_threshold)
    if numeric_value >= float(metric_threshold["warning"]):
        return MetricEvaluation(metric_key=metric_key, value=numeric_value, state="warning", threshold=metric_threshold)
    return MetricEvaluation(metric_key=metric_key, value=numeric_value, state="healthy", threshold=metric_threshold)


def evaluate_metrics_for_log(log, thresholds: dict[str, Any] | None = None) -> dict[str, MetricEvaluation]:
    merged_thresholds = thresholds or get_merged_thresholds()
    latest_metrics = extract_latest_metrics(log)
    return {
        metric_key: evaluate_metric(metric_key, latest_metrics.get(metric_key), merged_thresholds)
        for metric_key in METRIC_CATALOG
    }


def determine_overall_health(log, thresholds: dict[str, Any] | None = None) -> str:
    return summarize_health(log, thresholds).state


def summarize_health(log, thresholds: dict[str, Any] | None = None) -> HealthSummary:
    evaluations = evaluate_metrics_for_log(log, thresholds)
    if any(evaluations[metric_key].state == "unknown" for metric_key in PRIMARY_HEALTH_METRICS):
        return HealthSummary(score=0, state="Offline", penalties=[], evaluations=evaluations)

    score = 100.0
    penalties: list[dict[str, Any]] = []
    for metric_key, penalty_weights in HEALTH_SCORE_WEIGHTS.items():
        evaluation = evaluations.get(metric_key)
        if not evaluation or evaluation.state not in {"warning", "critical"}:
            continue
        penalty = float(penalty_weights.get(evaluation.state, 0.0))
        if penalty <= 0:
            continue
        score -= penalty
        penalties.append(
            {
                "metric_key": metric_key,
                "label": metric_display_name(metric_key),
                "state": evaluation.state,
                "value": evaluation.value,
                "unit": evaluation.threshold.get("unit") or "",
                "penalty": penalty,
            }
        )

    score = int(max(0, min(100, round(score))))
    if score < 70:
        state = "Critical"
    elif score < 90:
        state = "Warning"
    else:
        state = "Healthy"
    return HealthSummary(score=score, state=state, penalties=penalties, evaluations=evaluations)


def build_chart_threshold_bands(metric_key: str, thresholds: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    merged_thresholds = thresholds or get_merged_thresholds()
    metric_threshold = merged_thresholds.get("metrics", {}).get(metric_key, {})
    if not metric_threshold.get("enabled"):
        return []
    warning = float(metric_threshold["warning"])
    critical = float(metric_threshold["critical"])
    return [
        {"from": 0.0, "to": warning, "color": "rgba(0, 255, 136, 0.08)"},
        {"from": warning, "to": critical, "color": "rgba(255, 170, 0, 0.12)"},
        {"from": critical, "to": critical * 1.1, "color": "rgba(255, 59, 92, 0.16)"},
    ]


def metric_display_name(metric_key: str) -> str:
    return str(METRIC_CATALOG.get(metric_key, {}).get("label") or metric_key)


def alert_metric_name(metric_key: str) -> str:
    return f"health_{metric_key}"


def diff_changed_metric_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changed = []
    before_metrics = before.get("metrics", {})
    after_metrics = after.get("metrics", {})
    for metric_key in METRIC_CATALOG:
        if before_metrics.get(metric_key) != after_metrics.get(metric_key):
            changed.append(metric_key)
    return changed


def save_threshold_config(*, payload: dict[str, Any], actor: str | None, expected_version: int, change_reason: str | None) -> tuple[ServerThresholdConfig, dict[str, Any], list[str]]:
    row = ensure_threshold_config_row()
    current_version = int(row.version or 1)
    if int(expected_version) != current_version:
        raise RuntimeError("CONFLICT_VERSION")

    before = _merge_saved_thresholds(row.thresholds_json)
    patch = normalize_threshold_patch(payload)
    merged = apply_threshold_patch(before, patch)
    changed_metric_keys = diff_changed_metric_keys(before, merged)

    row.thresholds_json = merged
    row.version = current_version + 1
    row.updated_at = datetime.utcnow()
    row.updated_by = actor
    row.change_reason = change_reason
    db.session.flush()
    return row, merged, changed_metric_keys
