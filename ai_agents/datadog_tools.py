from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional, Sequence

import requests

from ai_agents.config import get_datadog_keys, get_datadog_site


Site = Literal["us1", "us3", "us5", "eu1", "ap1", "ap2"]


def _site_to_api_host(site: str) -> str:
    
    
    
    
    
    s = site.lower()
    if s in ("us1", "us"):
        return "api.datadoghq.com"
    if s == "eu1":
        return "api.datadoghq.eu"
    if s in ("us3", "us5", "ap1", "ap2"):
        return f"api.{s}.datadoghq.com"
    
    return "api.datadoghq.com"


def _dd_headers(api_key: str, app_key: str) -> dict[str, str]:
    return {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }


def _parse_scope(scope: str | None) -> dict[str, str]:
    
    if not scope:
        return {}
    out: dict[str, str] = {}
    for part in [p.strip() for p in scope.split(",") if p.strip()]:
        if ":" in part:
            k, v = part.split(":", 1)
            out[k] = v
    return out


def datadog_query_v1(
    query: str,
    from_ts: int,
    to_ts: int,
    *,
    site: Optional[str] = None,
    api_key: Optional[str] = None,
    app_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    
    env_api, env_app = get_datadog_keys()
    api_key = api_key or env_api
    app_key = app_key or env_app
    if not api_key or not app_key:
        raise RuntimeError("Missing Datadog keys. Set DD_API_KEY and DD_APP_KEY.")

    site = (site or get_datadog_site()).lower()
    host = _site_to_api_host(site)
    url = f"https://{host}/api/v1/query"
    params = {"from": int(from_ts), "to": int(to_ts), "query": query}
    resp = requests.get(url, headers=_dd_headers(api_key, app_key), params=params, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Datadog API error {resp.status_code}: {resp.text}")
    return resp.json()


MetricName = Literal[
    "cpu_usage",
    "cpu_num_cores",
    "disk_usage",
    "network_total",
    "memory_pct_usable",
    "memory_used_pct",
]


def build_query(
    metric: MetricName,
    *,
    host: Optional[str] = None,
) -> tuple[str, str]:
    
    host_filter = f"host:{host}" if host else "*"

    if metric == "cpu_usage":
        
        return f"100 - avg:system.cpu.idle{{{host_filter}}} by {{host}}", "CPU 使用率 (%)"

    if metric == "cpu_num_cores":
        
        return f"avg:system.cpu.num_cores{{{host_filter}}} by {{host}}", "CPU 核心數 (cores)"

    if metric == "disk_usage":
        
        
        return (
            f"(1 - (avg:system.disk.free{{{host_filter}}} by {{host,device}} / "
            f"avg:system.disk.total{{{host_filter}}} by {{host,device}})) * 100",
            "磁碟使用率 (%) (by host,device)",
        )

    if metric == "network_total":
        return (
            f"avg:system.net.bytes_sent{{{host_filter}}} by {{host}} + "
            f"avg:system.net.bytes_rcvd{{{host_filter}}} by {{host}}",
            "網路總流量 (Bytes/sec)",
        )

    if metric == "memory_pct_usable":
        return f"avg:system.mem.pct_usable{{{host_filter}}} by {{host}}", "記憶體可用比例 (0~1)"

    if metric == "memory_used_pct":
        
        return f"(1 - avg:system.mem.pct_usable{{{host_filter}}} by {{host}}) * 100", "記憶體使用率 (%)"

    raise ValueError("Unsupported metric")


def summarize_series(dd_payload: dict[str, Any]) -> dict[str, Any]:
    
    series = dd_payload.get("series", []) or []
    out_series: list[dict[str, Any]] = []
    all_values: list[float] = []

    for s in series:
        scope_raw = s.get("scope")
        tags = _parse_scope(scope_raw)
        points = s.get("pointlist", []) or []
        cleaned_points: list[dict[str, Any]] = []
        for ts_ms, val in points:
            if val is None:
                continue
            cleaned_points.append({"ts_ms": ts_ms, "value": float(val)})
            all_values.append(float(val))
        out_series.append(
            {
                "metric": s.get("metric"),
                "scope": scope_raw,
                "tags": tags,
                "points": cleaned_points,
            }
        )

    stats: dict[str, Any] = {"count": len(all_values)}
    if all_values:
        stats.update(
            {
                "avg": sum(all_values) / len(all_values),
                "min": min(all_values),
                "max": max(all_values),
            }
        )

    return {
        "status": dd_payload.get("status"),
        "message": dd_payload.get("message"),
        "res_type": dd_payload.get("res_type"),
        "query": dd_payload.get("query"),
        "series_count": len(out_series),
        "series": out_series,
        "stats": stats,
    }


def fetch_metric_summary(
    metric: MetricName,
    from_ts: int,
    to_ts: int,
    host: Optional[str] = None,
    site: Optional[str] = None,
) -> dict[str, Any]:
    
    query, unit_name = build_query(metric, host=host)
    raw = datadog_query_v1(query, from_ts, to_ts, site=site)
    summarized = summarize_series(raw)
    summarized["metric_type"] = metric
    summarized["unit_name"] = unit_name
    summarized["from_ts"] = int(from_ts)
    summarized["to_ts"] = int(to_ts)
    summarized["time_unit"] = "seconds"
    summarized["host"] = host
    return summarized


def fetch_metrics_bundle(
    metrics: Sequence[MetricName],
    from_ts: int,
    to_ts: int,
    host: Optional[str] = None,
    site: Optional[str] = None,
) -> dict[str, Any]:
    
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for m in metrics:
        try:
            results.append(fetch_metric_summary(m, from_ts, to_ts, host=host, site=site))
        except Exception as e:
            errors.append(
                {
                    "metric_type": m,
                    "error": str(e),
                    "from_ts": int(from_ts),
                    "to_ts": int(to_ts),
                    "time_unit": "seconds",
                    "host": host,
                    "site": site,
                }
            )

    return {
        "time_unit": "seconds",
        "from_ts": int(from_ts),
        "to_ts": int(to_ts),
        "host": host,
        "site": site or get_datadog_site(),
        "data": results,
        "errors": errors,
    }

