"""Qwen-backed query complexity classification with a local fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from chatbi.domain.models import QueryIntent
from chatbi.settings import Settings, get_settings


logger = logging.getLogger("chatbi.intent")

INTENT_SYSTEM_PROMPT = """你是 ChatBI 的查询复杂度路由器。你只负责分类，不生成 SQL。

分类依据是“分析目标数量与推理步骤”，不是表数量：联表查询也可以是 simple。

将用户问题分类为：
- simple：分析目标单一，指标、维度和过滤条件明确，一次查询与一次聚合即可完成。允许使用多个表及 JOIN；例如“按供应商统计采购金额”虽然联表，仍是 simple。
- complex：存在多个分析目标或多阶段计算，例如多指标组合、同比/环比、多个时间窗口、排名后再算占比、贡献拆解、归因、异常诊断，通常需要先聚合再计算、CTE/子查询或分步骤规划。

不要仅因出现 JOIN、多个表名或跨表关联就判为 complex。

只输出一个 JSON 对象，不要 Markdown，不要解释性前后缀：
{"query_type":"simple|complex","confidence":0到1之间的小数,"reason":"不超过40字的中文理由"}
"""


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
            )
        self.client = client

    def classify(self, question: str) -> QueryIntent:
        try:
            response = self.client.chat.completions.create(
                model=self.settings.intent_model,
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                temperature=0,
                max_tokens=180,
            )
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
        if query_type not in {"simple", "complex"}:
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
        "对比", "比较", "同时", "分别", "以及", "预测", "异常", "变化", "增长率",
    )

    def classify(self, question: str) -> QueryIntent:
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
        try:
            result = self.primary.classify(question)
            self.last_error = None
            return result
        except Exception as exc:
            self.last_error = type(exc).__name__
            logger.warning("intent_classifier_fallback reason=%s", type(exc).__name__)
            return self.fallback.classify(question)


class DisabledIntentClassifier:
    def classify(self, question: str) -> QueryIntent:
        return QueryIntent("simple", 1.0, "意图路由未启用", "disabled")
