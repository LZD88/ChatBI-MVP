"""Planning agent for complex analytical questions."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from chatbi.application.ports import LLMPort
from chatbi.domain.complex_analysis import ComplexQueryPlan, PlanStep
from chatbi.domain.security import DEFAULT_SCHEMA
from chatbi.settings import Settings
from indicator_knowledge import IndicatorKnowledge


def extract_json_object(raw: str) -> str:
    """Extract one JSON object while tolerating a Markdown code fence."""
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("模型未返回 JSON 对象")
    _value, end = decoder.raw_decode(text[start:])
    return text[start : start + end]


class PlannerAgent:
    """Produces and validates a declarative plan; it never produces SQL."""

    def __init__(self, llm: LLMPort, settings: Settings, indicators: IndicatorKnowledge) -> None:
        self.llm = llm
        self.settings = settings
        self.indicators = indicators

    def plan(
        self,
        question: str,
        indicator_knowledge: str,
        schema_text: str,
        detected_indicators: list[str],
    ) -> ComplexQueryPlan:
        prompt = self._prompt(question, indicator_knowledge, schema_text, detected_indicators)
        last_error = ""
        for attempt in range(self.settings.complex_plan_repair_attempts + 1):
            current = prompt if attempt == 0 else self._repair_prompt(prompt, last_error)
            try:
                raw = self.llm.generate_text(self._system_message(), current)
                plan = ComplexQueryPlan.model_validate_json(extract_json_object(raw))
                plan = plan.model_copy(update={"source": "model" if attempt == 0 else "repaired"})
                self._validate_catalog(plan, detected_indicators)
                return plan
            except (ValidationError, ValueError, KeyError) as exc:
                last_error = self._safe_error(exc)
        return self._fallback(question, detected_indicators)

    @staticmethod
    def _system_message() -> str:
        return (
            "你是 ChatBI 的规划 Agent。只负责把复杂问题拆成可执行的数据分析步骤，"
            "绝不能生成 SQL。只输出一个严格 JSON 对象，不要 Markdown，不要解释。"
        )

    def _prompt(
        self, question: str, knowledge: str, schema_text: str, detected: list[str]
    ) -> str:
        schema = json.dumps(ComplexQueryPlan.model_json_schema(), ensure_ascii=False)
        return f"""用户问题：{question}
已识别治理指标：{json.dumps(detected, ensure_ascii=False)}
指标目录：
{knowledge or '未命中明确指标'}
可用 Schema：
{schema_text}

要求：
1. 最多 {self.settings.complex_plan_max_steps} 步，depends_on 只能引用前序步骤。
2. required_tables 只能使用给出的业务表；required_fields 使用 table.field。
3. 规划文字和字段名用中文，规划中不得出现任何 SQL。
4. 复杂口径先聚合再关联时使用 aggregate_then_join，并填写 grain 和 join_keys。
5. 覆盖所有已识别治理指标。source 固定填写 model。
输出必须满足此 JSON Schema：{schema}
"""

    @staticmethod
    def _repair_prompt(original: str, error: str) -> str:
        return f"{original}\n上次输出校验失败：{error}。请修复后仅返回完整 JSON。"

    def _validate_catalog(self, plan: ComplexQueryPlan, detected: list[str]) -> None:
        if len(plan.steps) > self.settings.complex_plan_max_steps:
            raise ValueError("计划步骤超过配置上限")
        known_indicators = set(self.indicators.definitions)
        used_indicators: set[str] = set()
        allowed_tables = self.settings.allowed_tables & frozenset(DEFAULT_SCHEMA)
        for step in plan.steps:
            unknown_indicators = set(step.indicators) - known_indicators
            if unknown_indicators:
                raise ValueError("计划引用未知指标")
            used_indicators.update(step.indicators)
            if set(step.required_tables) - allowed_tables:
                raise ValueError("计划引用未知或未授权表")
            for field in step.required_fields:
                match = re.fullmatch(r"([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)", field)
                if not match:
                    raise ValueError("字段必须使用 table.field 格式")
                table, column = match.groups()
                if table not in allowed_tables or column not in DEFAULT_SCHEMA[table]:
                    raise ValueError("计划引用未知字段")
        if set(detected) - used_indicators:
            raise ValueError("计划未覆盖用户明确提出的治理指标")

    def _fallback(self, question: str, detected: list[str]) -> ComplexQueryPlan:
        candidate_tables: list[str] = []
        for name in detected:
            definition = self.indicators.definitions.get(name)
            haystack = f"{definition.data_source} {definition.formula}" if definition else ""
            candidate_tables.extend(table for table in DEFAULT_SCHEMA if table in haystack)
        table = next(
            (item for item in candidate_tables if item in self.settings.allowed_tables),
            (
                "sales_orders"
                if "sales_orders" in self.settings.allowed_tables
                else next(iter(sorted(self.settings.allowed_tables & frozenset(DEFAULT_SCHEMA))))
            ),
        )
        step = PlanStep(
            step_id="step_1",
            step_name="完成核心分析",
            objective=f"围绕用户问题核算并返回可验证的数据结果：{question}",
            indicators=detected,
            required_tables=[table],
            success_criteria="返回可用于回答用户问题的中文结果列和真实数据",
        )
        return ComplexQueryPlan(
            analysis_goal=f"回答复杂业务问题：{question}",
            assumptions=["规划模型输出不可用，采用受控单步回退计划"],
            steps=[step],
            final_deliverable="基于真实查询结果生成中文分析报告",
            report_sections=["执行摘要", "核心发现", "局限", "建议"],
            source="fallback",
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # Validation details contain only our contract labels, never secrets or DB errors.
        return str(exc)[:600]
