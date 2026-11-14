"""Bounded, explicit conversation scope and pre-query business clarification."""
from __future__ import annotations

import re
from typing import Any

from chatbi.application.failure_control import AnalysisSuspended


def conversation_context(question: str, parent: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = (parent or {}).get("response") or {}
    context = previous.get("analysis_context") or {}
    turns = list(context.get("turns") or [])
    if parent and not turns:
        turns = [{"question": parent["question"], "query_id": parent["id"]}]
    elif parent and turns:
        turns[-1] = {**turns[-1], "query_id": parent["id"]}
    if len(turns) >= 8:
        raise ValueError("连续分析最多 8 轮，请开启新分析。")
    turns.append({"question": question})
    # History is data, not instructions or previously generated SQL. Each new
    # query is regenerated and secured against the current catalog and policy.
    return {"turns": turns, "parent_query_id": (parent or {}).get("id"),
            "scope_rule": "沿用此前明确的指标、时间与筛选；新条件替换同类旧条件，其余保留。"}


def effective_question(question: str, context: dict[str, Any]) -> str:
    turns = context.get("turns") or []
    if len(turns) <= 1:
        return question
    previous = "\n".join(f"第{i + 1}轮：{turn['question']}" for i, turn in enumerate(turns[:-1]))
    return (f"连续分析上下文：\n{previous}\n当前追问：{question}\n"
            "沿用此前明确的时间范围、指标和筛选；当前追问替换同类条件，其余保留。"
            "此前轮次中已解决的任务无需重复执行，只回答当前追问。若要求解释原因，查询相关构成数据再分析，禁止编造因果。")


def ambiguity_questions(question: str) -> list[dict[str, Any]]:
    questions = []
    if re.search(r"(?<!净)(?<!毛)利润(?!率)", question) and not any(word in question for word in ("毛利", "净利润", "利润口径已确认")):
        questions.append({"id": "metric", "question": "“利润”使用哪个指标口径？", "options": ["毛利", "净利润"]})
    if re.search(r"近期|最近|这段时间", question) and not re.search(r"最近\s*\d+\s*(?:天|周|个?月)|20\d{2}[-年/]", question):
        questions.append({"id": "time", "question": "请明确时间范围（日期区间或年份与季度）。", "options": []})
    if re.search(r"最好|最差|表现好|表现差", question) and not re.search(r"按.+(?:排序|排名)|以.+为(?:准|标准)|排名口径已确认", question):
        questions.append({"id": "ranking", "question": "按什么标准评价表现？", "options": ["收入最高", "毛利率最高", "净利润最高"]})
    return questions


def require_clarification(question: str) -> None:
    questions = ambiguity_questions(question)
    if questions:
        raise AnalysisSuspended(
            {"version": 1, "kind": "clarification", "question": question, "questions": questions,
             "resume_round": 0, "max_resume_rounds": 3},
            {"kind": "clarification", "message": "查询前需要确认业务含义，尚未调用数据库。", "questions": questions,
             "options": ["clarify", "abort"], "resume_round": 0, "max_resume_rounds": 3},
        )


def apply_answers(question: str, questions: list[dict[str, Any]], answers: dict[str, str]) -> str:
    for item in questions:
        answer = answers.get(item["id"], "").strip()
        if not answer or len(answer) > 160 or (item["options"] and answer not in item["options"]):
            raise ValueError(f"请回答：{item['question']}")
        if item["id"] == "time":
            if not re.search(r"20\d{2}|最近\s*\d+\s*(?:天|周|个?月)", answer):
                raise ValueError("时间范围应包含年份或明确的最近天数/月数。")
            question = re.sub(r"近期|最近(?!\d)|这段时间", answer, question)
        elif item["id"] == "metric":
            question = re.sub(r"(?<!净)(?<!毛)利润(?!率)", answer, question)
        else:
            question += f"；按{answer}排序，排名口径已确认"
    return question
