"""Run real-model simple and complex benchmarks; fail closed on quality gates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from chatbi.api.app import build_query_service
from chatbi.application.quality_evaluation import aggregate_quality, assess_complex
from chatbi.domain.errors import ChatBIError
from chatbi.settings import Settings
from chatbi.infrastructure.metric_registry import MetricRegistry
from evaluator import Evaluator
from chatbi.infrastructure.runtime_version import source_version
from chatbi.application.failure_control import AnalysisSuspended


def evaluate(settings: Settings) -> dict[str, Any]:
    execution_version = source_version()
    revision, _ = MetricRegistry(settings.metric_registry_path).snapshot()
    service = build_query_service(settings)
    evaluator = Evaluator(service.database, service.security, model_config={
        "model": settings.llm_model, "intent_model": settings.intent_model,
        "temperature": settings.llm_temperature, "max_tokens": settings.llm_max_tokens,
    })
    try:
        simple_benchmark = evaluator.load_benchmark("test_cases.json")
        simple_results = []
        for case in simple_benchmark["cases"]:
            started = perf_counter()
            record = evaluator.evaluate_one(case, lambda question: service.query(question).sql)
            record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
            record["domain"] = {"cost": "销售成本", "gross_profit": "盈利分析", "expenses": "期间费用", "profit": "盈利分析"}.get(case.get("category"), "销售收入")
            simple_results.append(record)
        simple = evaluator.build_report(simple_results, benchmark_version=simple_benchmark["version"], database_snapshot=simple_benchmark["database_snapshot"])
        complex_benchmark = json.loads(Path("benchmarks/complex_cases.json").read_text(encoding="utf-8"))
        records = []
        for case in complex_benchmark["cases"]:
            started = perf_counter()
            record: dict[str, Any] = {"id": case["id"]}
            try:
                result = service.query(case["question"])
                record.update(assess_complex(result, case))
                expected_columns, expected_rows = service.database.execute(service.security.secure(case["expected_sql"]))
                record["execution_match"] = evaluator._results_equivalent(
                    list(result.columns), [tuple(row) for row in result.rows], expected_columns, expected_rows,
                )
                # Keep controlled evidence and plan for manual review; no secrets or raw exceptions.
                record.update(plan=result.plan, report=result.analysis_report, step_results=list(result.step_results))
            except AnalysisSuspended as exc:
                record.update(error_code=exc.code.value, suspension=exc.public)
            except ChatBIError as exc:
                record["error_code"] = exc.code.value
            except Exception:
                record["error_code"] = "evaluation_failed"
            record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
            records.append(record)
        complex_report = {"benchmark_version": complex_benchmark["version"], **aggregate_quality(records), "results": records}
        usage = {}
        primary = getattr(service.intent_classifier, "primary", None)
        encoder = getattr(service.simple_semantic, "encoder", None)
        for name, provider in (("chat", service.llm), ("intent", primary), ("embedding", encoder)):
            counts = getattr(provider, "usage", {})
            complete = bool(counts.get("calls") and counts.get("calls") == counts.get("reported_calls"))
            usage[name] = {**counts, "complete": complete,
                           "mean_tokens_per_question": (counts.get("prompt_tokens", 0) + counts.get("completion_tokens", 0))
                           / (len(simple_results) + len(records)) if complete else None}
        # Simple and complex combined token totals; unavailable usage remains explicitly incomplete.
        source_changed = execution_version != source_version()
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "kind": "live_model", "source_version": execution_version,
                "source_changed_during_evaluation": source_changed, "catalog_revision": revision,
                "simple": simple, "complex": complex_report, "token_usage": usage,
                "passed": simple["passed_mvp_threshold"] and complex_report["passed"] and not source_changed}
    finally:
        service.database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/release-quality.json"))
    args = parser.parse_args()
    try:
        report = evaluate(Settings())
    except Exception as exc:
        # A missing dependency is an explicit failed preflight, never an empty
        # passing evaluation or a leaked vendor exception/credential.
        report = {"generated_at": datetime.now(timezone.utc).isoformat(), "kind": "live_model",
                  "source_version": source_version(), "passed": False, "status": "preflight_failed",
                  "error_type": type(exc).__name__, "message": "验收未启动：请通过 scripts/chatbi.ps1 evaluate 配置密钥并检查数据库和 Chroma。"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "status": report.get("status", "completed"),
                      "simple": report.get("simple", {}).get("metrics"), "complex": report.get("complex", {}).get("metrics"),
                      "message": report.get("message")}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
