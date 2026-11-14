"""Qwen-backed query complexity classification with a local fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from chatbi.domain.models import QueryIntent
from chatbi.settings import Settings, get_settings
from chatbi.application.progress import checkpoint, remaining_timeout


logger = logging.getLogger("chatbi.intent")

INTENT_SYSTEM_PROMPT = """你是 ChatBI 的查询复杂度路由器。你只负责分类，不生成 SQL。

分类依据是“分析目标数量与推理步骤”，不是表数量：联表查询也可以是 simple。

将用户问题分类为：
- non_query：问候、感谢、帮助、产品能力咨询、闲聊或不涉及业务数据的问题，例如“你好”“你是谁”“今天天气如何”。不要生成查询任务。
- simple：分析目标单一，指标、维度和过滤条件明确，一次查询与一次聚合即可完成。允许使用多个表及 JOIN；例如“按供应商统计采购金额”虽然联表，仍是 simple。
- complex：存在多个分析目标或多阶段计算，例如多指标组合、同比/环比、多个时间窗口、排名后再算占比、贡献拆解、归因、异常诊断，通常需要先聚合再计算、CTE/子查询或分步骤规划。

不要仅因出现 JOIN、多个表名或跨表关联就判为 complex。
输入若包含 current_question 和 analysis_context，必须只判断 current_question 的当前目标。
analysis_context 仅用于补齐省略的指标、范围和对象，不把历史分析目标或历史“分步骤”要求当作本轮任务。
analysis_context 按时间从旧到新排列；省略指标的追问应优先继承最近一轮明确的业务目标，不恢复更早已被替换的目标。
“只看华东”“再按月份拆开”“改成2026年”属于业务追问：若历史存在明确业务指标，必须补齐该指标再分类，不能因为本轮省略指标就判为 non_query。
例如历史最后一轮是“按区域统计2026年订单数”，本轮“只看华东”应为 simple；更早历史出现过复杂分析不改变此判断。
若最近业务目标本身仍涉及多个指标或同比归因，添加筛选后的追问仍可为 complex。
问候和闲聊即使带有历史业务上下文，也必须判为 non_query；“你好，按产品线查收入”仍是数据查询。
用户明确要求“分步骤”“从上游结果”，或“先…再…”的多阶段计算时必须判为 complex，
即使这些计算能够合并为一条 SQL 也应进入规划与执行流程。

只输出一个 JSON 对象，不要 Markdown，不要解释性前后缀：
{"query_type":"non_query|simple|complex","confidence":0到1之间的小数,"reason":"不超过40字的中文理由"}
"""


def current_turn(question: str) -> str:
    """Inspect the current turn without treating old instructions as new goals."""
    try:
        payload = json.loads(question)
        if isinstance(payload, dict) and isinstance(payload.get("current_question"), str):
            return payload["current_question"]
    except (ValueError, TypeError):
        pass
    return question


def obvious_non_query(question: str) -> QueryIntent | None:
    text = re.sub(r"[\s，。！？、,.!?~～：:]+", "", current_turn(question)).lower()
    if re.fullmatch(r"(?:你好|您好|嗨|哈喽|hello|hi|早上好|早安|下午好|晚上好|晚安|在吗|谢谢|谢谢你|感谢|再见|拜拜|拜|bye|goodmorning|goodnight|hey)+", text):
        return QueryIntent("non_query", 1.0, "本轮是问候或礼貌交流，不涉及数据查询", "local_guard")
    if text in {"你是谁", "你能做什么", "怎么用", "帮助", "使用帮助", "介绍一下自己", "介绍下自己"}:
        return QueryIntent("non_query", 1.0, "本轮是产品能力或使用帮助咨询", "local_guard")
    return None


def conversational_reply(question: str) -> str:
    text = current_turn(question)
    if re.search(r"谢谢|感谢|thank", text, re.I):
        return "不客气！你可以继续追问业务数据，或开启新的分析。"
    if re.search(r"再见|拜拜|bye|晚安", text, re.I):
        return "再见！需要分析业务数据时，随时来问我。"
    return "你好，我是 ChatBI，可以帮你查询业务数据、对比指标和生成分析报告。你可以问：2026年第三季度各产品线的收入是多少？"


class QwenIntentClassifier:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        if client is None:
            if self.settings.intent_api_key is None:
                raise ValueError("INTENT_API_KEY is required to create QwenIntentClassifier")
            client = OpenAI(
                api_key=self.settings.intent_api_key.get_secret_value(),
                base_url=self.settings.intent_base_url,
                timeout=self.settings.intent_timeout_seconds,
                max_retries=0,
            )
        self.client = client
        self.usage = {"calls": 0, "reported_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def classify(self, question: str) -> QueryIntent:
        try:
            checkpoint()
            response = self.client.chat.completions.create(
                model=self.settings.intent_model,
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                temperature=0,
                max_tokens=180,
                timeout=remaining_timeout(self.settings.intent_timeout_seconds),
            )
            checkpoint()
            self.usage["calls"] += 1
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.usage["reported_calls"] += 1
                self.usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
            content = response.choices[0].message.content
            return self.parse_response(content)
        except (APITimeoutError, APIConnectionError, OpenAIError) as exc:
            raise RuntimeError("qwen_intent_service_unavailable") from exc

    @staticmethod
    def parse_response(content: str | None) -> QueryIntent:
        if not content or not content.strip():
            raise ValueError("empty intent response")
        candidate = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.IGNORECASE | re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid intent JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("intent response must be an object")
        query_type = str(payload.get("query_type", "")).lower()
        if query_type not in {"non_query", "simple", "complex"}:
            raise ValueError("unsupported query_type")
        confidence = float(payload.get("confidence", 0))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence out of range")
        reason = str(payload.get("reason", "")).strip()
        if not reason:
            raise ValueError("intent reason is required")
        return QueryIntent(query_type, confidence, reason[:80], "qwen")


class HeuristicIntentClassifier:
    COMPLEX_SIGNALS = (
        "同比", "环比", "趋势", "排名", "排行", "占比", "贡献", "归因", "原因",
        "对比", "比较", "同时", "分别", "以及", "预测", "异常", "变化", "增长率", "分步骤", "上游结果",
    )

    def classify(self, question: str) -> QueryIntent:
        guard = obvious_non_query(question)
        if guard:
            return guard
        latest = current_turn(question)
        business = re.search(r"订单|收入|毛利|利润|销售|采购|库存|产品|客户|供应商|仓库|生产|良品|不良|质量|质检|设备|维护|成本|预算|费用|回款|付款|员工|部门|发运|发货|物流|汇率|币种|金额|数量|区域|地区|月份", latest)
        contextual = latest != question and re.search(r"只看|再按|改成|换成|继续|这些|上面|上一|拆开|报告|这几轮|重新|为什么|为何|原因|华东|华南|华北", latest)
        if not business and not contextual:
            return QueryIntent("non_query", 0.55, "未识别出业务数据查询目标，请明确指标或分析对象", "fallback")
        question = latest
        matched = [signal for signal in self.COMPLEX_SIGNALS if signal in question]
        query_type = "complex" if matched else "simple"
        reason = (
            f"本地规则命中复杂信号：{matched[0]}"
            if matched
            else "分析目标单一；联表本身不构成复杂查询"
        )
        return QueryIntent(query_type, 0.55, reason, "fallback")


class FallbackIntentClassifier:
    def __init__(self, primary: Any, fallback: Any | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or HeuristicIntentClassifier()
        self.last_error: str | None = None

    def classify(self, question: str) -> QueryIntent:
        guard = obvious_non_query(question)
        if guard:
            self.last_error = None
            return guard
        try:
            result = self.primary.classify(question)
            self.last_error = None
            latest = current_turn(question)
            if result.query_type == "simple" and (
                "分步骤" in latest or "上游结果" in latest
                or re.search(r"先.{2,}再", latest)
            ):
                return QueryIntent("complex", result.confidence,
                                   "用户明确要求多阶段计算，进入规划执行链路", f"{result.source}+stage_guard")
            return result
        except Exception as exc:
            self.last_error = type(exc).__name__
            logger.warning("intent_classifier_fallback reason=%s", type(exc).__name__)
            return self.fallback.classify(question)


class DisabledIntentClassifier:
    def classify(self, question: str) -> QueryIntent:
        return obvious_non_query(question) or QueryIntent("simple", 1.0, "意图路由未启用", "disabled")
