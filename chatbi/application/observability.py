"""Honest dashboard projections of recorded evaluations and runtime queries."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any


def evaluation_dashboard(path: Path, current_version: str) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False, "message": "尚无真实模型评测报告，请运行 scripts/chatbi.ps1 evaluate。", "domains": []}
    report = json.loads(path.read_text(encoding="utf-8"))
    records = report.get("simple", {}).get("results", []) + report.get("complex", {}).get("results", [])
    groups: dict[str, list] = {}
    failures: Counter[str] = Counter()
    for record in records:
        domain = record.get("domain") or {"revenue": "销售收入", "cost": "销售成本", "gross_profit": "盈利分析", "expenses": "期间费用",
            "profit": "盈利分析"}.get(record.get("category")) or {"low-margin-product-cost": "盈利分析", "over-budget-centers": "预算分析",
                                           "maintenance-cost-ranking": "设备维护"}.get(record.get("id"), "未分类")
        groups.setdefault(domain, []).append(record)
        if not record.get("execution_match"):
            failures[record.get("error_code") or record.get("failure_class") or "result_mismatch"] += 1
    domains = [{"name": name, "samples": len(items), "correct": sum(bool(item.get("execution_match")) for item in items),
                "accuracy": sum(bool(item.get("execution_match")) for item in items) / len(items),
                "mean_latency_ms": sum(float(item.get("latency_ms", item.get("duration_ms", 0))) for item in items) / len(items)} for name, items in groups.items()]
    return {"available": True, "generated_at": report.get("generated_at"), "passed": report.get("passed"),
            "source_version": report.get("source_version"), "current_version": current_version,
            "stale": report.get("source_version") != current_version, "domains": domains,
            "token_usage": report.get("token_usage", {}), "failures": dict(failures),
            "simple_metrics": report.get("simple", {}).get("metrics", {}),
            "complex_metrics": report.get("complex", {}).get("metrics", {}),
            "note": "正确率来自固定基准 SQL 结果比对；小样本不代表真实业务总体准确率。"}
