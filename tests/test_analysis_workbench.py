from dataclasses import replace
from decimal import Decimal
import json

from fastapi.testclient import TestClient
import pytest

from chatbi.api.app import create_app
from chatbi.application.analysis_context import ambiguity_questions, apply_answers, conversation_context, effective_question
from chatbi.application.analysis_control import preview_edits
from chatbi.application.conversation_report import compile_report
from chatbi.application.drilldown import build_detail_sql
from chatbi.application.failure_control import AnalysisSuspended
from chatbi.application.observability import evaluation_dashboard
from chatbi.application.progress import resumed_state
from chatbi.domain.complex_analysis import ComplexQueryPlan, ReconciliationCheck, StepExecutionResult, StepStatus
from chatbi.domain.security import QuerySecurityManager, UserContext
from chatbi.infrastructure.agents.validator import ResultValidationAgent, reconcile
from chatbi.infrastructure.chinese_retrieval import rerank
from tests.test_api import FakeService, FakeDatabase as HealthDatabase, settings
from tests.test_complex_agents import QueueLLM, FakeDatabase, plan, step, settings as agent_settings
from tests.test_failure_control import executor, VALID_SQL
from tests.test_query_jobs import wait_until
from chatbi.application.plan_execute_service import PlanAndExecuteService
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from indicator_knowledge import IndicatorKnowledge


def test_ambiguity_requires_specific_metric_time_and_ranking_before_execution():
    questions = ambiguity_questions("最近哪些产品利润最好？")
    assert {item["id"] for item in questions} == {"metric", "time", "ranking"}
    resolved = apply_answers("最近哪些产品利润最好？", questions, {"metric": "毛利", "time": "2026年第三季度", "ranking": "毛利率最高"})
    assert not ambiguity_questions(resolved)
    with pytest.raises(ValueError):
        apply_answers("最近利润", questions, {"metric": "随便编", "time": "昨天", "ranking": "随便"})
    assert not ambiguity_questions("2026年各产品线毛利率，按收入排序")


def test_context_preserves_explicit_history_and_tracks_parent_ids():
    first = conversation_context("2026年第三季度各产品线毛利率")
    parent = {"id": "a" * 32, "question": first["turns"][0]["question"], "response": {"analysis_context": first}}
    second = conversation_context("只看华东", parent)
    assert second["turns"][0]["query_id"] == parent["id"]
    assert "第三季度" in effective_question("只看华东", second)
    assert "替换同类条件" in effective_question("只看华东", second)
    assert first["turns"] == [{"question": "2026年第三季度各产品线毛利率"}]


def test_plan_preview_invalidates_only_descendants_and_token_binds_checkpoint():
    p = plan(step(), step("step_2", depends_on=["step_1"]), step("step_3", depends_on=["step_2"]), step("step_4"))
    state = {"plan": p.model_dump(), "cached_results": [{"step_id": "step_1", "status": "succeeded"}], "resume_round": 0}
    preview = preview_edits(state, {"step_2": ["只看华东"]})
    assert preview["affected_steps"] == ["step_2", "step_3"]
    assert preview["reused_steps"] == ["step_1"]
    assert preview["preview_token"] != preview_edits({**state, "resume_round": 1}, {"step_2": ["只看华东"]})["preview_token"]
    with pytest.raises(ValueError):
        preview_edits(state, {"step_1": []})
    with pytest.raises(ValueError):
        preview_edits(state, {"step_9": []})


def test_validator_detects_semantic_errors_and_reports_unavailable_review():
    validator = ResultValidationAgent(QueueLLM(texts=['{"issues":["按技术路线分组，未按要求的产品线分组"],"checks":{"筛选与维度":"错误"}}']))
    review = validator.validate(question="各产品线收入", sql="SELECT tech_route,SUM(net_amount) FROM sales_orders GROUP BY tech_route", columns=["技术路线", "收入"], rows=[["LFP", 20]], knowledge="收入口径")
    assert review["status"] == "rejected"
    unavailable = ResultValidationAgent(QueueLLM()).validate(question="收入", sql=VALID_SQL, columns=["收入"], rows=[[1]], knowledge="")
    assert unavailable["status"] == "review_unavailable"
    assert "不可用" in str(unavailable["limitations"])
    assert ResultValidationAgent.structural_checks("SELECT SUM(o.net_amount) FROM sales_orders o JOIN finance_expenses f ON o.order_date=f.expense_date")
    assert not ResultValidationAgent.structural_checks("WITH a AS (SELECT SUM(net_amount) AS n FROM sales_orders), b AS (SELECT SUM(rd_expense) AS f FROM finance_expenses) SELECT a.n-b.f FROM a CROSS JOIN b")
    assert ResultValidationAgent.arithmetic_checks(["收入", "销售成本", "毛利"], [[100, 40, 80]])


def test_validator_rejection_enters_existing_local_repair_loop():
    from chatbi.infrastructure.agents.executor import ExecutionAgent
    llm = QueueLLM(sqls=[VALID_SQL, VALID_SQL], texts=['{"issues":["筛选缺失"],"checks":{}}', '{"issues":[],"checks":{"指标":"通过"}}'])
    db = FakeDatabase(rows=[(["收入"], [(2,)]), (["收入"], [(1,)])])
    agent = ExecutionAgent(llm, QuerySecurityManager(), db, agent_settings(complex_core_max_retries=1), UserContext.demo_admin(), validator=ResultValidationAgent(llm))
    result = agent.execute("收入", plan(), "sales_orders", "", "req")[0]
    assert result.status == "succeeded" and result.attempts == 2
    assert result.validation["status"] == "reviewed" and db.calls == 2


def test_reconciliation_uses_decimal_and_refuses_incomplete_partitions():
    p = plan(step(), step("step_2"))
    p.reconciliation_checks = [ReconciliationCheck(total_step_id="step_1", detail_step_id="step_2", total_column="收入", detail_column="收入")]
    results = [StepExecutionResult(step_id="step_1", step_name="总收入", status=StepStatus.SUCCEEDED, columns=["收入"], rows=[[Decimal("0.3")]], result="成功", result_reference="rows"),
               StepExecutionResult(step_id="step_2", step_name="分收入", status=StepStatus.SUCCEEDED, columns=["收入"], rows=[[Decimal("0.1")], [Decimal("0.2")]], result="成功", result_reference="rows")]
    assert reconcile(p, results)[0]["status"] == "passed"
    results[1].rows[1][0] = Decimal("0.3")
    assert reconcile(p, results)[0]["status"] == "rejected"
    results[1].truncated = True
    assert reconcile(p, results)[0]["status"] == "unverified"


def test_local_replan_preserves_success_and_rejects_scope_changes():
    p = plan(step(), step("step_2", depends_on=["step_1"]))
    revised = p.steps[1].model_dump()
    revised["objective"] = "按同一订单粒度分别聚合后核算收入"
    llm = QueueLLM(texts=[p.model_dump_json(), json.dumps({"steps": [revised]}, ensure_ascii=False)], sqls=[VALID_SQL, "DROP TABLE sales_orders", VALID_SQL])
    db = FakeDatabase(rows=[(["收入"], [(1,)]), (["收入"], [(2,)])])
    workflow = PlanAndExecuteService(PlannerAgent(llm, agent_settings(), IndicatorKnowledge()), executor(llm, db), SummaryAgent(QueueLLM(), agent_settings()))
    result = workflow.run(question="分析收入", indicator_knowledge="", schema_text="sales_orders", detected_indicators=[], request_id="req")
    assert db.calls == 2 and result.step_results[0].recovery_action == "reused"
    assert result.plan.steps[1].objective == revised["objective"]
    bad = {**revised, "filters": ["更换时间范围"]}
    workflow.planner.llm = QueueLLM(texts=[json.dumps({"steps": [bad]}, ensure_ascii=False)])
    with pytest.raises(ValueError, match="业务范围"):
        workflow._replan(p, {"failed_step_id": "step_2", "cached_results": [], "detected_indicators": []}, "sales_orders", "")


def test_drilldown_preserves_original_scope_and_quotes_values_as_literals():
    source = {"columns": ["产品线", "收入"], "rows": [["x' OR 1=1 --", 10]],
        "sql": "SELECT p.product_line AS product_line,SUM(o.net_amount) AS revenue FROM sales_orders o JOIN dim_products p ON o.product_id=p.product_id WHERE o.order_status='completed' AND o.order_date>='2026-07-01' GROUP BY p.product_line"}
    sql = build_detail_sql(source, "产品线", "x' OR 1=1 --")
    secured = QuerySecurityManager().secure(sql)
    assert "2026-07-01" in secured.sql and "completed" in secured.sql
    assert "GROUP BY" not in secured.sql and "SUM(" not in secured.sql
    with pytest.raises(ValueError):
        build_detail_sql(source, "收入", 10)
    with pytest.raises(ValueError):
        build_detail_sql(source, "产品线", "任意未返回对象")


def test_reranker_raises_chinese_dimension_candidate_over_irrelevant_vector_hit():
    metas, scores = rerank("按产品线统计收入", {"metadatas": [[{"field": "tech_route"}, {"field": "product_line"}]],
        "documents": [["电池技术路线类型", "产品线 业务线 产品线分组"]], "distances": [[0.1, 0.5]]}, 2)
    assert metas[0]["field"] == "product_line" and scores[0]["score"] > scores[1]["score"]


def test_rollup_drilldown_retains_leaf_time_and_refuses_extra_intermediate_filters():
    source = {"columns": ["产品线", "收入"], "rows": [["工商业", 10]], "sql":
        "WITH a AS (SELECT o.product_id,SUM(o.net_amount) n FROM sales_orders o JOIN dim_products p ON o.product_id=p.product_id WHERE o.order_date>='2026-07-01' GROUP BY o.product_id) SELECT p.product_line,SUM(a.n) FROM a JOIN dim_products p ON a.product_id=p.product_id GROUP BY p.product_line"}
    detail = build_detail_sql(source, "产品线", "工商业")
    assert "2026-07-01" in detail and "工商业" in detail and "SUM(" not in detail
    source["sql"] = source["sql"].replace("GROUP BY p.product_line", "WHERE a.n>10 GROUP BY p.product_line")
    with pytest.raises(ValueError):
        build_detail_sql(source, "产品线", "工商业")


def test_api_clarification_survives_restart_and_followup_uses_resolved_context(tmp_path):
    seen = []
    class Service(FakeService):
        def query(self, question, request_id=None):
            seen.append(question)
            return super().query(question, request_id)
    config = settings(app_env="test", query_history_path=tmp_path / "history.sqlite", metric_registry_path=tmp_path / "metrics.sqlite")
    with TestClient(create_app(config, service_factory=lambda _: Service(HealthDatabase()))) as client:
        query_id = client.post("/api/v1/query-jobs", json={"question": "最近利润最好的是哪些产品线"}).json()["id"]
        wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] == "suspended")
        assert seen == []
        assert client.post(f"/api/v1/query-jobs/{query_id}/resume", json={"action": "retry"}).status_code == 409
    with TestClient(create_app(config, service_factory=lambda _: Service(HealthDatabase()))) as client:
        response = client.post(f"/api/v1/query-jobs/{query_id}/resume", json={"action": "clarify", "answers": {"metric": "毛利", "time": "2026年第三季度", "ranking": "毛利率最高"}})
        assert response.status_code == 202, response.text
        wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] == "succeeded")
        second = client.post("/api/v1/query-jobs", json={"question": "只看华东", "parent_query_id": query_id}).json()["id"]
        wait_until(lambda: client.get(f"/api/v1/history/{second}").json()["status"] == "succeeded")
        assert "2026年第三季度" in seen[-1] and "只看华东" in seen[-1]
        assert client.get(f"/api/v1/history/{second}").json()["response"]["analysis_context"]["parent_query_id"] == query_id


def test_api_plan_preview_token_prevents_unreviewed_or_stale_mutations(tmp_path):
    captured = []
    class Service(FakeService):
        def query(self, question, request_id=None):
            state = resumed_state()
            if not state:
                p = plan(step(), step("step_2", depends_on=["step_1"]))
                raise AnalysisSuspended({"kind": "plan_review", "question": question, "plan": p.model_dump(), "cached_results": [],
                    "resume_round": 0, "max_resume_rounds": 3}, {"kind": "plan_review", "plan": p.model_dump(mode="json"), "message": "检查计划", "options": ["retry", "abort"]})
            captured.append(state)
            return super().query(question, request_id)
    config = settings(app_env="test", query_history_path=tmp_path / "history.sqlite", metric_registry_path=tmp_path / "metrics.sqlite")
    with TestClient(create_app(config, service_factory=lambda _: Service(HealthDatabase()))) as client:
        query_id = client.post("/api/v1/query-jobs", json={"question": "分析收入"}).json()["id"]
        wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] == "suspended")
        path = f"/api/v1/query-jobs/{query_id}"
        edits = {"step_1": ["2026年第三季度"]}
        preview = client.post(path + "/plan-preview", json={"edits": edits})
        assert preview.status_code == 200
        assert preview.json()["affected_steps"] == ["step_1", "step_2"]
        assert client.post(path + "/resume", json={"action": "edit", "edits": edits, "preview_token": "bad"}).status_code == 409
        assert client.post(path + "/resume", json={"action": "edit", "edits": edits, "preview_token": preview.json()["preview_token"]}).status_code == 202
        wait_until(lambda: client.get(f"/api/v1/history/{query_id}").json()["status"] == "succeeded")
        assert captured[0]["plan"]["steps"][0]["filters"] == ["2026年第三季度"]


def test_conversation_report_keeps_query_identity_and_dashboard_marks_stale(tmp_path):
    item = {"id": "a" * 32, "status": "succeeded", "question": "收入", "response": {"columns": ["收入"], "rows": [[20]], "row_count": 1}}
    report = compile_report({"turns": [{"query_id": item["id"]}]}, lambda _: item)
    assert report["analysis_report"]["findings"][0]["evidence"][0]["query_id"] == item["id"]
    path = tmp_path / "report.json"
    assert not evaluation_dashboard(path, "v2")["available"]
    path.write_text(json.dumps({"source_version": "v1", "passed": False, "complex": {"results": [
        {"id": "maintenance-cost-ranking", "execution_match": True, "latency_ms": 30},
        {"id": "maintenance-cost-ranking", "execution_match": False, "latency_ms": 50, "error_code": "sql_rejected"}]}}), encoding="utf-8")
    dashboard = evaluation_dashboard(path, "v2")
    assert dashboard["stale"] and dashboard["domains"][0]["accuracy"] == .5
    assert dashboard["domains"][0]["mean_latency_ms"] == 40 and dashboard["failures"] == {"sql_rejected": 1}
