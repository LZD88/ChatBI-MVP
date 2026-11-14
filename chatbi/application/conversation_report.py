"""Compile immutable prior reports with query-specific evidence identities."""
from copy import deepcopy
from typing import Any, Callable


def compile_report(context: dict[str, Any], read: Callable) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    rows: list[list[Any]] = []
    markdown = ["# 连续分析汇总报告"]
    caveats = ["各轮分析可能采用不同条件或查询时间；本报告逐轮汇总，不能把各轮金额直接相加。"]
    seen = set()
    for turn in context["turns"]:
        query_id = turn.get("query_id")
        if not query_id or query_id in seen:
            continue
        seen.add(query_id)
        item = read(query_id)
        if not item or item["status"] != "succeeded" or not item.get("response") or item["response"].get("history_payload_omitted"):
            caveats.append("部分历史结果已删除或超过保存上限，未纳入汇总。")
            continue
        response = item["response"]
        if response.get("agent_mode") == "conversation_report":
            continue
        report = response.get("analysis_report") or {}
        summary = report.get("executive_summary") or f"返回 {response.get('row_count', 0)} 行，详见原始证据。"
        rows.append([len(rows) + 1, item["question"], summary])
        markdown.extend([f"\n## 第 {len(rows)} 轮：{item['question']}", f"来源查询：{query_id}", response.get("report_markdown") or summary])
        entries = deepcopy(report.get("findings") or [])
        if not entries:
            entries = [{"title": item["question"][:80], "statement": summary,
                        "evidence": [{"step_id": "main", "step_name": "原始查询", "row_index": 0,
                                      "column": response["columns"][0], "value": response["rows"][0][0]}] if response.get("rows") else []}]
        actions = deepcopy(report.get("recommendations") or [])
        for entry in entries + actions:
            for ref in entry.get("evidence", []):
                ref["query_id"] = query_id
        findings.extend(entries)
        recommendations.extend(actions)
        caveats.extend(report.get("caveats") or [])
    return {"columns": ["分析轮次", "问题", "摘要"], "rows": rows, "row_count": len(rows),
            "analysis_report": {"title": "连续分析汇总报告", "source": "conversation_report",
                "executive_summary": f"已汇总 {len(rows)} 轮分析，保留各轮发现、业务建议及原始证据链接。",
                "findings": findings, "recommendations": recommendations,
                "recommendation_reason": "以下建议来自各轮已保存报告，需结合各轮条件阅读。", "caveats": list(dict.fromkeys(caveats))},
            "report_markdown": "\n".join(markdown)}
