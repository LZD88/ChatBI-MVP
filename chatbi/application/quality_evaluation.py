"""Automated evidence/structure checks, distinct from human business judgement."""

from __future__ import annotations

from typing import Any
from decimal import Decimal, InvalidOperation

from chatbi.application.analysis_evidence import numeric
from chatbi.domain.models import QueryResult


def same_value(left: Any, right: Any) -> bool:
    a, b = numeric(left), numeric(right)
    return a == b if a is not None and b is not None else str(left) == str(right)


def assess_complex(result: QueryResult, case: dict[str, Any]) -> dict[str, Any]:
    steps = {step["step_id"]: step for step in result.step_results}
    plan = result.plan or {}
    report = result.analysis_report or {}
    evidence_count = valid_count = 0

    def valid_evidence(ref: dict[str, Any]) -> bool:
        step = steps.get(ref.get("step_id"))
        try:
            index = ref["row_index"]
            return bool(step and step["status"] == "succeeded" and isinstance(index, int) and not isinstance(index, bool)
                        and 0 <= index < len(step["rows"]) and ref["column"] in step["columns"]
                        and same_value(step["rows"][index][step["columns"].index(ref["column"])], ref["value"]))
        except (KeyError, IndexError, TypeError):
            return False

    for item in [*report.get("findings", []), *report.get("recommendations", [])]:
        for ref in item.get("evidence", []):
            evidence_count += 1
            valid_count += valid_evidence(ref)
    recommendations = report.get("recommendations", [])
    def metric_evidence(ref: dict[str, Any]) -> bool:
        step = steps.get(ref.get("step_id"), {})
        contract = (step.get("result_artifact") or {}).get("columns")
        if contract is not None and not any(col["name"] == ref.get("column") and col["role"] == "metric" for col in contract):
            return False
        value = ref.get("value")
        if numeric(value) is not None:
            return True
        if contract is not None and isinstance(value, str):
            try:
                return Decimal(value).is_finite()
            except InvalidOperation:
                return False
        return False

    grounded = 0
    for item in recommendations:
        refs = item.get("evidence", [])
        grounded += bool(refs and all(valid_evidence(ref) for ref in refs)
                         and any(str(ref.get("value")) == item.get("target") for ref in refs)
                         and any(metric_evidence(ref) for ref in refs)
                         and all(item.get(key) for key in ("problem", "action", "owner_role", "priority", "expected_impact", "precondition", "risk", "confidence")))
    dependency_count = consumed_count = 0
    for step in plan.get("steps", []):
        for binding in step.get("input_bindings", []):
            dependency_count += 1
            source = steps.get(binding["source_step_id"], {})
            target = steps.get(step["step_id"], {})
            artifact = source.get("result_artifact") or {}
            consumed_count += artifact.get("artifact_id") in target.get("consumed_artifacts", [])
    return {
        "planning_success": bool(result.agent_mode == "plan_execute" and plan.get("source") in {"model", "repaired"}
                                 and len(plan.get("steps", [])) >= case.get("min_steps", 2)),
        "planned_steps": len(plan.get("steps", [])),
        "successful_steps": sum(step.get("status") == "succeeded" for step in steps.values()),
        "fully_succeeded": result.agent_status == "succeeded",
        "partial": result.agent_status == "partial",
        "evidence_count": evidence_count, "valid_evidence_count": valid_count,
        "recommendation_count": len(recommendations), "grounded_recommendation_count": grounded,
        "dependency_ok": (dependency_count > 0 and consumed_count == dependency_count) if case.get("requires_binding") else True,
        "report_source": result.report_source,
        "summary_model_success": result.report_source == "summary_agent",
        "business_effectiveness": "requires_human_review",
    }


def aggregate_quality(cases: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(cases)
    def rate(numerator: str, denominator: str | None = None) -> float:
        total = sum(case.get(denominator, 0) for case in cases) if denominator else count
        return sum(case.get(numerator, 0) for case in cases) / total if total else 0.0
    metrics: dict[str, Any] = {
        "case_count": count, "planning_success_rate": rate("planning_success"),
        "sql_step_success_rate": rate("successful_steps", "planned_steps"),
        "full_success_rate": rate("fully_succeeded"), "partial_success_rate": rate("partial"),
        "evidence_reference_accuracy": rate("valid_evidence_count", "evidence_count"),
        "recommendation_grounding_rate": rate("grounded_recommendation_count", "recommendation_count"),
        "result_accuracy": rate("execution_match"), "dependency_success_rate": rate("dependency_ok"),
        "summary_model_success_rate": rate("summary_model_success"),
        "mean_latency_ms": rate("latency_ms"),
        "business_effectiveness": "requires_human_review",
    }
    passed = bool(count and metrics["planning_success_rate"] >= .8 and metrics["sql_step_success_rate"] >= .8
                  and metrics["full_success_rate"] >= .8 and metrics["result_accuracy"] >= .8
                  and metrics["evidence_reference_accuracy"] == 1 and metrics["recommendation_grounding_rate"] == 1
                  and metrics["dependency_success_rate"] == 1)
    passed = passed and metrics["summary_model_success_rate"] >= .8
    return {"metrics": metrics, "passed": passed}
