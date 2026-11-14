"""Validated plan patches with deterministic transitive invalidation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from chatbi.domain.complex_analysis import ComplexQueryPlan


def affected_steps(plan: ComplexQueryPlan, roots: set[str]) -> set[str]:
    affected = set(roots)
    for step in plan.steps:
        if set(step.depends_on) & affected:
            affected.add(step.step_id)
    return affected


def preview_edits(state: dict[str, Any], edits: dict[str, list[str]]) -> dict[str, Any]:
    if state.get("kind") not in {None, "failure", "plan_review"} or "plan" not in state:
        raise ValueError("当前断点没有可编辑计划。")
    plan = ComplexQueryPlan.model_validate(state["plan"])
    completed = {row["step_id"] for row in state.get("cached_results", []) if row["status"] == "succeeded"}
    known = {step.step_id for step in plan.steps}
    if not edits or set(edits) - known or set(edits) & completed:
        raise ValueError("只能修改计划中未完成步骤的筛选条件。")
    if any(len(filters) > 12 or any(not item.strip() or len(item) > 240 for item in filters) for filters in edits.values()):
        raise ValueError("每步最多 12 条条件，每条条件应为 1～240 字。")
    draft = plan.model_dump()
    for step in draft["steps"]:
        if step["step_id"] in edits:
            step["filters"] = [text.strip() for text in edits[step["step_id"]]]
    candidate = ComplexQueryPlan.model_validate(draft)
    affected = affected_steps(plan, set(edits))
    body = {"plan": candidate.model_dump(mode="json"), "affected_steps": sorted(affected),
            "reused_steps": sorted(completed - affected), "edits": edits,
            "warning": "变更条件仅作用于指定步骤及下游；上游已完成结果的范围保持原样。不同时间执行的数据不保证同一快照。"}
    body["preview_token"] = hashlib.sha256(json.dumps(
        {"state": state, "preview": body}, sort_keys=True, ensure_ascii=False, default=str,
    ).encode()).hexdigest()
    return body
