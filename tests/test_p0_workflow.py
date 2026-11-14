from __future__ import annotations

import json
from decimal import Decimal

import pytest
import sqlglot
from sqlglot import exp

from chatbi.application.result_bindings import bind_results
from chatbi.domain.complex_analysis import (
    ExpectedColumn, InputBinding, ResultArtifact, StepExecutionResult, StepStatus,
    SummaryAgentOutput,
)
from chatbi.domain.errors import ChatBIError
from chatbi.domain.schema import SchemaSelection, SimpleSemanticSelection
from chatbi.domain.security import QuerySecurityManager, UserContext
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from chatbi.infrastructure.complex_semantic import ComplexSemanticResolver
from indicator_knowledge import IndicatorKnowledge
from tests.test_complex_agents import FakeDatabase, QueueLLM, plan, settings, step
from tests.test_query_service import FakeIntent, make_service


def column(name="产品线", role="dimension", data_type="string"):
    return ExpectedColumn(name=name, role=role, data_type=data_type, description="测试结果契约")


def source_result(rows=None, truncated=False):
    return StepExecutionResult(
        step_id="step_1", step_name="筛选产品线", status=StepStatus.SUCCEEDED,
        columns=["产品线", "毛利率"], rows=rows if rows is not None else [["甲产品线", Decimal("0.2")], ["乙产品线", Decimal("0.5")]],
        row_count=2, truncated=truncated, result="查询成功", result_reference="step_1:rows",
        result_artifact=ResultArtifact(
            artifact_id="request_only_artifact", columns=[column(), column("毛利率", "metric", "percentage")],
            row_count=2, complete=not truncated,
        ),
    )


def bound_step():
    return step("step_2", depends_on=["step_1"]).model_copy(update={
        "input_bindings": [InputBinding(source_step_id="step_1", name="input_1", columns=["产品线", "毛利率"])],
        "required_tables": [], "required_fields": [],
        "expected_columns": [column(), column("毛利率", "metric", "percentage")],
    })


class ChromaStub:
    def __init__(self, broken=False):
        self.broken = broken
        self.calls = []

    def resolve(self, question):
        self.calls.append(question)
        if self.broken:
            raise RuntimeError("chroma stopped")
        return SimpleSemanticSelection(
            schema=SchemaSelection("表：sales_orders", "semantic", ("sales_orders",)),
            indicator_text="", indicators=("毛利率",), fields=(), joins=(), mode="chroma",
        )


@pytest.mark.parametrize("broken, mode", [(False, "chroma"), (True, "governed_local")])
def test_complex_context_expands_governed_sources_without_chat_embeddings(broken, mode):
    resolver = ComplexSemanticResolver(settings(), IndicatorKnowledge(), ChromaStub(broken))
    context = resolver.resolve("比较各产品线的收入毛利率并给出建议")
    assert context.mode == mode
    assert {"sales_orders", "dim_products", "exchange_rates"} <= set(context.schema.tables)
    assert len(context.schema.tables) < 12
    assert "equipment" not in context.schema.tables
    assert "dim_products.product_line" in context.fields
    assert "sales_orders.order_status" in context.fields


def test_complex_query_service_bypasses_legacy_embedding_provider():
    service = make_service(intent=FakeIntent("complex"))
    class ForbiddenProvider:
        def select(self, question):
            raise AssertionError("legacy embedding path must not be used")
    service.schema_provider = ForbiddenProvider()
    service.complex_semantic = ComplexSemanticResolver(settings(), IndicatorKnowledge(), ChromaStub())
    result = service.query("比较各产品线毛利率")
    assert result.semantic.mode == "chroma"
    assert len(result.semantic.tables) < 12


def test_query_service_keeps_designated_table_after_auxiliary_validation():
    from types import SimpleNamespace
    first = source_result()
    validation = first.model_copy(update={
        "step_id": "step_2", "columns": ["校验行数"], "rows": [[2]], "row_count": 1,
    })
    query_plan = plan(step(), step("step_2", depends_on=["step_1"]))
    query_plan.final_result_step_id = "step_1"
    report = SummaryAgent(QueueLLM(texts=["{}"]), settings()).summarize("复核产品线", query_plan, [first])
    class Workflow:
        def run(self, **kwargs):
            return SimpleNamespace(
                plan=query_plan, step_results=[first, validation], report=report,
                timings_ms={}, status="succeeded",
            )
    service = make_service(intent=FakeIntent("complex"))
    service.settings = settings()
    service.plan_execute = Workflow()
    result = service.query("比较各产品线毛利率")
    assert result.columns == tuple(first.columns)
    assert result.rows == tuple(tuple(row) for row in first.rows)


def test_context_limit_fails_explicitly_instead_of_silently_dropping_required_tables():
    resolver = ComplexSemanticResolver(settings(complex_schema_max_tables=1), IndicatorKnowledge(), ChromaStub())
    with pytest.raises(ChatBIError):
        resolver.resolve("各产品线毛利率")


def test_planner_repairs_plan_outside_compact_schema_and_unknown_binding_column():
    invalid = plan(step()).model_dump(mode="json")
    invalid["steps"][0]["required_tables"] = ["equipment"]
    repaired = plan(step()).model_dump_json()
    result = PlannerAgent(
        QueueLLM(texts=[json.dumps(invalid), repaired]),
        settings(complex_plan_repair_attempts=1), IndicatorKnowledge(),
    ).plan("分析订单", "", "表：sales_orders", [])
    assert result.source == "repaired"
    producer = step().model_copy(update={"expected_columns": [column()]})
    with pytest.raises(ValueError, match="输出契约"):
        plan(producer, bound_step())


def test_result_binding_injects_real_values_as_ast_literals_and_tracks_artifact():
    payload = "产品'); DROP TABLE sales_orders; -- \\ ' <script>"
    source = source_result([[payload, Decimal("0.25")]])
    sql, consumed = bind_results(
        "SELECT `产品线`, `毛利率` FROM input_1", bound_step(), {"step_1": source}, settings(),
    )
    parsed = sqlglot.parse(sql, read="mysql")
    assert len(parsed) == 1
    assert not list(parsed[0].find_all(exp.Drop))
    assert payload in [node.this for node in parsed[0].find_all(exp.Literal) if node.is_string]
    secured = QuerySecurityManager().secure(sql)
    assert secured.tables == ()
    assert consumed == [source.result_artifact.artifact_id]


def test_failed_planning_without_indicators_cannot_execute_an_uncontracted_fallback():
    planner = PlannerAgent(QueueLLM(texts=["{}"]), settings(), IndicatorKnowledge())
    with pytest.raises(ChatBIError, match="输出契约"):
        planner.plan("分析业务表现", "", "表：sales_orders", [])


@pytest.mark.parametrize("failure", ["truncated", "missing", "large", "shadowed", "unused"])
def test_dependency_failure_prevents_partial_or_unbound_execution(failure):
    source = source_result(truncated=failure == "truncated")
    previous = {} if failure == "missing" else {"step_1": source}
    sql = "SELECT `产品线`, `毛利率` FROM input_1"
    if failure == "shadowed":
        sql = "WITH input_1 AS (SELECT 1) SELECT * FROM input_1"
    if failure == "unused":
        sql = "SELECT 1"
    config = settings(complex_dependency_max_rows=1) if failure == "large" else settings()
    with pytest.raises(ValueError):
        bind_results(sql, bound_step(), previous, config)


def test_execution_consumes_artifact_and_preserves_safe_dependency_values():
    producer = step().model_copy(update={
        "expected_columns": [column(), column("毛利率", "metric", "percentage")],
    })
    class InspectingLLM(QueueLLM):
        def generate_sql(self, system_msg, prompt):
            if len(self.sqls) == 1:
                assert "input_1" in prompt
                assert "甲产品线" not in prompt
            return super().generate_sql(system_msg, prompt)
    llm = InspectingLLM(sqls=[
        "SELECT region AS `产品线`, SUM(net_amount) AS `毛利率` FROM sales_orders GROUP BY region",
        "SELECT `产品线`, `毛利率` FROM input_1 ORDER BY `毛利率`",
    ])
    db = FakeDatabase(rows=[
        (["产品线", "毛利率"], [("甲产品线", Decimal("0.2")), ("乙产品线", Decimal("0.5"))]),
        (["产品线", "毛利率"], [("甲产品线", Decimal("0.2")), ("乙产品线", Decimal("0.5"))]),
    ])
    results = ExecutionAgent(llm, QuerySecurityManager(), db, settings(), UserContext.demo_admin()).execute(
        "分析产品线", plan(producer, bound_step()), "sales_orders", "", "req",
    )
    assert all(item.status is StepStatus.SUCCEEDED for item in results)
    assert results[1].consumed_artifacts == [results[0].result_artifact.artifact_id]
    assert "甲产品线" in results[1].sql


def test_output_contract_failure_skips_downstream_but_independent_step_continues():
    first = step().model_copy(update={"expected_columns": [column(), column("毛利率", "metric", "percentage")]})
    third = step("step_3")
    db = FakeDatabase(rows=[(["wrong_column"], [(123,)]), (["收入"], [(100,)])])
    results = ExecutionAgent(
        QueueLLM(sqls=["SELECT net_amount FROM sales_orders", "SELECT SUM(net_amount) AS `收入` FROM sales_orders"]),
        QuerySecurityManager(), db, settings(), UserContext.demo_admin(),
    ).execute("查询", plan(first, bound_step(), third), "sales_orders", "", "req")
    assert [item.status.value for item in results] == ["skipped", "skipped", "succeeded"]
    assert results[0].recovery_action == "soft_skip"
    assert results[0].error_code == "step_contract_invalid"
    assert db.calls == 2


def test_template_action_uses_extremes_beyond_first_thirty_rows():
    source = source_result([[f"产品线{i}", Decimal(i + 1) / 100] for i in range(60)])
    source.row_count = 60
    source.columns = ["产品线", "毛利率"]
    agent = SummaryAgent(QueueLLM(texts=["invalid json"]), settings())
    prompt = agent._prompt("比较产品线", plan(), [source])
    assert '"row_index": 59' in prompt
    assert '"descriptive_statistics"' in prompt
    report = agent.summarize("比较产品线", plan(), [source])
    assert report.recommendation_status == "actionable"
    assert report.recommendations[0].target == "产品线0"
    assert {ref.row_index for ref in report.recommendations[0].evidence} == {0, 59}
    assert report.recommendations[0].precondition
    assert any("抽样" in text for text in report.caveats)


def test_empty_results_do_not_force_generic_recommendations():
    report = SummaryAgent(QueueLLM(texts=["{}"]), settings()).summarize("查询", plan(), [source_result([])])
    assert report.recommendation_status == "insufficient_data"
    assert not report.recommendations
    assert "数据不足" in report.markdown


def recommendation_draft(target="甲产品线"):
    target_ref = {"step_id": "step_1", "row_index": 0, "column": "产品线"}
    return SummaryAgentOutput.model_validate({
        "title": "产品线报告", "executive_summary": "当前产品线存在毛利率差异。",
        "recommendation_status": "actionable", "recommendation_reason": "基于当前可比数据生成复核动作。",
        "recommendations": [{
            "target": target, "problem": "该产品线毛利率偏低，需要复核。",
            "action": "复核该产品线的成本和折扣记录。", "rationale": "当前横向比较存在差异。",
            "owner_role": "产品线负责人", "priority": "medium", "confidence": "low",
            "expected_impact": "定位差异来源。", "precondition": "确认币种和粒度一致。",
            "risk": "当前数据不能证明因果。", "target_evidence": target_ref,
            "evidence": [target_ref, {"step_id": "step_1", "row_index": 0, "column": "毛利率"}],
        }],
    })


def test_recommendation_target_must_match_real_cell():
    agent = SummaryAgent(QueueLLM(), settings())
    with pytest.raises(ValueError, match="对象"):
        agent._resolve(recommendation_draft("不存在的产品线"), [source_result()])
    report = agent._resolve(recommendation_draft(), [source_result()])
    assert report.recommendations[0].target == "甲产品线"
    assert report.recommendations[0].evidence[1].value == Decimal("0.2")


def test_numeric_dimension_cannot_substitute_for_business_metric_evidence():
    source = source_result()
    source.result_artifact.columns[1].role = "dimension"
    with pytest.raises(ValueError, match="数值指标"):
        SummaryAgent(QueueLLM(), settings())._resolve(recommendation_draft(), [source])


def test_summary_rejects_unseen_row_evidence_in_sample():
    source = source_result([[f"产品线{i}", Decimal(i + 1) / 100] for i in range(60)])
    draft = recommendation_draft()
    # With two visible rows only the extrema are shown to the model.
    agent = SummaryAgent(QueueLLM(), settings(complex_summary_rows_per_step=2))
    draft.recommendations[0].target = "产品线20"
    draft.recommendations[0].target_evidence.row_index = 20
    draft.recommendations[0].evidence[0].row_index = 20
    with pytest.raises(ValueError, match="无效证据"):
        agent._resolve(draft, [source])


def test_model_report_includes_truncation_warning_without_trusting_model_caveats():
    source = source_result(truncated=True)
    draft = recommendation_draft()
    agent = SummaryAgent(QueueLLM(texts=[draft.model_dump_json()]), settings())
    report = agent.summarize("分析产品线", plan(), [source])
    assert report.source == "summary_agent"
    assert any("不能视作全库" in text for text in report.caveats)
