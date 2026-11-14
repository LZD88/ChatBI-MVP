"""Planning agent for complex analytical questions."""

from __future__ import annotations

import json
import re
import logging

from pydantic import ValidationError

from chatbi.application.ports import LLMPort
from chatbi.domain.complex_analysis import ComplexQueryPlan, ExpectedColumn, PlanStep
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import DEFAULT_SCHEMA
from chatbi.settings import Settings
from indicator_knowledge import IndicatorKnowledge
from chatbi.application.progress import progress

logger = logging.getLogger("chatbi.planner")


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
            progress("planning_attempt", attempt=attempt + 1, message="规划 Agent 正在生成或修复计划")
            current = prompt if attempt == 0 else self._repair_prompt(prompt, last_error)
            try:
                raw = self.llm.generate_text(self._system_message(), current)
                plan = ComplexQueryPlan.model_validate_json(extract_json_object(raw))
                plan = plan.model_copy(update={"source": "model" if attempt == 0 else "repaired"})
                self._validate_catalog(plan, detected_indicators, schema_text)
                return plan
            except (ValidationError, ValueError, KeyError) as exc:
                last_error = self._safe_error(exc)
                logger.warning("plan_contract_repair attempt=%s reason=%s", attempt + 1, last_error)
            except ChatBIError:
                break
        return self._fallback(question, detected_indicators, schema_text)

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
6. 每步 expected_columns 为强制输出契约，不可为空：声明中文列名、维度/指标及真实数据类型。
7. 后续步骤需要使用前一步筛选出的对象或指标值时，声明 input_bindings，包含 source_step_id、
   name（input_1 等）和 columns（上游 expected_columns 中的中文列名），并同时声明 depends_on。
   上游只输出下游所需结果，避免全量明细和超大输入。只做上游结果计算时 required_tables 可为空。
8. 例如先筛出低毛利产品线，再查询这些产品线的成本：第二步必须绑定第一步的产品线字段。
9. steps 只计算可验证的数据。业务建议、原因推断、复核意见和报告均由总结 Agent 生成，
   不得为建议额外规划 SQL 步骤，也不得在 expected_columns 中加入建议或报告文本列。
10. final_result_step_id 必须填写直接回答用户数据问题的步骤 ID。该步骤只输出用户请求的
    维度和指标，严格保持列顺序，不附加中间计算列；后续校验不得替换该数据表。
11. 每步声明 importance 与 importance_reason：直接回答核心问题或提供必要依赖的步骤为 high；
    缺失也不影响核心结果的补充解释、辅助对比为 low。系统会强制提升最终结果及全部依赖为 high。
    max_retries 是额外重试次数：核心最多 {self.settings.complex_core_max_retries} 次，辅助最多 {self.settings.complex_aux_max_retries} 次。
12. 保持最小计划，不添加用户未要求的重复校验或分析。已使用默认值的可选字段可省略，
    名称与 description 简短；task_type 只能选择 Schema 枚举，先汇总再排名仍可使用 metric_query 或 dimension_comparison。
13. 当用户要求核对总计与明细，使用 reconciliation_checks 声明总计步骤、明细步骤及两列名称。
    两步必须同口径、同筛选，总计一行，明细覆盖完整分区；比例和平均值不得直接加总。
输出必须满足此 JSON Schema：{schema}
"""

    @staticmethod
    def _repair_prompt(original: str, error: str) -> str:
        return f"{original}\n上次输出校验失败：{error}。请修复后仅返回完整 JSON。"

    def _validate_catalog(self, plan: ComplexQueryPlan, detected: list[str], schema_text: str) -> None:
        if len(plan.steps) > self.settings.complex_plan_max_steps:
            raise ValueError("计划步骤超过配置上限")
        known_indicators = set(self.indicators.definitions)
        used_indicators: set[str] = set()
        allowed_tables = self.settings.allowed_tables & frozenset(DEFAULT_SCHEMA)
        context_tables = {table for table in allowed_tables if re.search(rf"\b{table}\b", schema_text)}
        for step in plan.steps:
            unknown_indicators = set(step.indicators) - known_indicators
            if unknown_indicators:
                raise ValueError("计划引用未知指标")
            used_indicators.update(step.indicators)
            if set(step.required_tables) - allowed_tables:
                raise ValueError("计划引用未知或未授权表")
            if set(step.required_tables) - context_tables:
                raise ValueError("计划引用了当前精简 Schema 以外的表")
            if not step.expected_columns:
                raise ValueError("计划必须声明 expected_columns 输出契约")
            if any(column.data_type == "string" and re.search(
                r"(?:建议|结论|分析报告|复核意见)$", column.name
            ) for column in step.expected_columns):
                raise ValueError("业务建议和报告文本必须由总结 Agent 生成，SQL 步骤仅返回业务数据")
            for field in step.required_fields:
                match = re.fullmatch(r"([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)", field)
                if not match:
                    raise ValueError("字段必须使用 table.field 格式")
                table, column = match.groups()
                if table not in step.required_tables or column not in DEFAULT_SCHEMA.get(table, ()):
                    raise ValueError("计划引用未知字段")
        if set(detected) - used_indicators:
            raise ValueError("计划未覆盖用户明确提出的治理指标")

    def _fallback(self, question: str, detected: list[str], schema_text: str) -> ComplexQueryPlan:
        if not detected:
            raise ChatBIError(
                ErrorCode.MODEL_OUTPUT_INVALID,
                "规划输出不可用，且没有明确指标可建立输出契约，请明确分析指标后重试。",
            )
        tables = [
            table for table in DEFAULT_SCHEMA
            if table in self.settings.allowed_tables and re.search(rf"\b{table}\b", schema_text)
        ]
        if not tables:
            raise ValueError("当前精简 Schema 中没有可用的业务表")
        step = PlanStep(
            step_id="step_1",
            step_name="完成核心分析",
            objective=f"围绕用户问题核算并返回可验证的数据结果：{question}",
            indicators=detected,
            required_tables=tables,
            expected_columns=[ExpectedColumn(
                name=name, role="metric", data_type="decimal", description=f"治理指标{name}的计算结果"
            ) for name in detected],
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
        if isinstance(exc, ValidationError):
            return "; ".join(f"{item['loc']}: {item['type']}" for item in exc.errors())[:600]
        return str(exc)[:600]
