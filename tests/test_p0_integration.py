"""Opt-in read-only test against the existing demo MySQL and Chroma services."""

import os
from decimal import Decimal

import pymysql
import pytest

from chatbi.application.plan_execute_service import PlanAndExecuteService
from chatbi.application.query_service import ChatBIQueryService
from chatbi.domain.complex_analysis import ComplexQueryPlan, ExpectedColumn, InputBinding, PlanStep
from chatbi.domain.security import QuerySecurityManager, UserContext
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from chatbi.infrastructure.complex_semantic import ComplexSemanticResolver
from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
from chatbi.settings import Settings
from database import DatabaseClient
from indicator_knowledge import IndicatorKnowledge
from tests.test_complex_agents import QueueLLM
from tests.test_query_service import FakeIntent


pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("CHATBI_P0_INTEGRATION") != "1", reason="requires demo MySQL and VM Chroma",
)]


def test_real_chroma_mysql_dependent_cte_workflow(tmp_path):
    config = Settings(_env_file=None, complex_step_retry=0, complex_plan_repair_attempts=0,
                      simple_chroma_dir=tmp_path / "semantic")
    indicators = IndicatorKnowledge()
    semantic = ComplexSemanticResolver(config, indicators, ChromaSimpleSemanticPipeline(config))
    expected = [ExpectedColumn(
        name=name, role="dimension" if name == "产品线" else "metric",
        data_type="string" if name == "产品线" else "decimal", description="同口径三季度分析结果",
    ) for name in ["产品线", "收入", "毛利", "毛利率"]]
    first = PlanStep(
        step_id="step_1", step_name="核算产品线表现", objective="按产品线核算三季度收入毛利及毛利率",
        required_tables=["sales_orders", "dim_products", "exchange_rates"],
        indicators=["收入", "毛利", "毛利率"], expected_columns=expected,
        success_criteria="返回全部产品线可比指标",
    )
    second = PlanStep(
        step_id="step_2", step_name="筛选低毛利产品线", objective="从上游结果筛选毛利率低于百分之八十的产品线",
        depends_on=["step_1"], required_tables=[], expected_columns=expected,
        input_bindings=[InputBinding(source_step_id="step_1", name="input_1", columns=[item.name for item in expected])],
        success_criteria="只返回符合阈值的真实产品线",
    )
    third = PlanStep(
        step_id="step_3", step_name="复核目标产品线成本", objective="只查询筛选出的产品线成本构成",
        depends_on=["step_2"], required_tables=["sales_orders", "dim_products"],
        input_bindings=[InputBinding(source_step_id="step_2", name="input_1", columns=["产品线"])],
        expected_columns=[expected[0], ExpectedColumn(name="销售成本", role="metric", data_type="decimal", description="人民币成本")],
        success_criteria="成本结果仅包含上游筛选出的产品线",
    )
    query_plan = ComplexQueryPlan(
        analysis_goal="比较第三季度各产品线表现并复核低毛利产品线",
        steps=[first, second, third], final_deliverable="提供有证据的业务建议",
        report_sections=["执行摘要", "核心发现", "业务建议"],
    )
    llm = QueueLLM(texts=[query_plan.model_dump_json(), "{}"], sqls=[
        "SELECT p.product_line AS `产品线`, SUM(o.net_amount*r.rate_to_cny) AS `收入`, "
        "SUM(o.net_amount*r.rate_to_cny-o.quantity*(p.material_cost+p.labor_cost)) AS `毛利`, "
        "SUM(o.net_amount*r.rate_to_cny-o.quantity*(p.material_cost+p.labor_cost))/NULLIF(SUM(o.net_amount*r.rate_to_cny),0) AS `毛利率` "
        "FROM sales_orders o JOIN dim_products p ON o.product_id=p.product_id "
        "JOIN exchange_rates r ON o.order_date=r.rate_date AND o.currency=r.currency "
        "WHERE o.order_status='completed' AND o.order_date>='2026-07-01' AND o.order_date<'2026-10-01' GROUP BY p.product_line",
        "SELECT `产品线`, `收入`, `毛利`, `毛利率` FROM input_1 WHERE `毛利率`<0.8",
        "SELECT i.`产品线`, SUM(o.quantity*(p.material_cost+p.labor_cost)) AS `销售成本` "
        "FROM sales_orders o JOIN dim_products p ON o.product_id=p.product_id "
        "JOIN input_1 i ON p.product_line=i.`产品线` "
        "WHERE o.order_status='completed' AND o.order_date>='2026-07-01' AND o.order_date<'2026-10-01' GROUP BY i.`产品线`",
    ])

    def connect():
        connection = pymysql.connect(
            host=os.getenv("CHATBI_P0_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("CHATBI_P0_DB_PORT", "33307")),
            user=os.getenv("CHATBI_P0_DB_USER", "root"),
            password=os.getenv("CHATBI_P0_DB_PASSWORD", ""),
            database="chatbi_mvp", charset="utf8mb4", autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        return connection

    database = DatabaseClient(connection_factory=connect)
    security = QuerySecurityManager()
    user = UserContext.demo_admin()
    orchestrator = PlanAndExecuteService(
        PlannerAgent(llm, config, indicators), ExecutionAgent(llm, security, database, config, user, semantic),
        SummaryAgent(llm, config),
    )
    class ForbiddenEmbeddingProvider:
        def select(self, question):
            raise AssertionError("complex path must not call legacy embeddings")
    service = ChatBIQueryService(
        llm=llm, database=database, security=security, settings=config, indicators=indicators,
        schema_provider=ForbiddenEmbeddingProvider(), complex_semantic=semantic,
        intent_classifier=FakeIntent("complex"), plan_execute=orchestrator,
    )
    result = service.query("比较第三季度各产品线收入、毛利和毛利率，复核低毛利产品线成本并给出建议")
    assert result.agent_status == "succeeded"
    assert result.semantic.mode == "chroma"
    assert len(result.semantic.tables) < 12
    assert len(result.step_results) == 3
    first_result, selected, cost = result.step_results
    assert selected["consumed_artifacts"] == [first_result["result_artifact"]["artifact_id"]]
    assert cost["consumed_artifacts"] == [selected["result_artifact"]["artifact_id"]]
    assert {row[0] for row in cost["rows"]} == {row[0] for row in selected["rows"]}
    assert all(Decimal(str(row[3])) < Decimal("0.8") for row in selected["rows"])
    assert result.analysis_report["recommendations"]
    assert result.analysis_report["recommendations"][0]["target"] in {row[0] for row in first_result["rows"]}
    assert "业务建议" in result.report_markdown
