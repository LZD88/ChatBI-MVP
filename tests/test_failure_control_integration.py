"""Read-only MySQL + VM Chroma + persistent API checkpoint fault injection."""
import os
from decimal import Decimal

import pymysql
import pytest
from fastapi.testclient import TestClient

from chatbi.api.app import create_app
from chatbi.application.plan_execute_service import PlanAndExecuteService
from chatbi.application.query_service import ChatBIQueryService
from chatbi.domain.complex_analysis import InputBinding
from chatbi.domain.security import QuerySecurityManager, UserContext
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from chatbi.infrastructure.complex_semantic import ComplexSemanticResolver
from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
from database import DatabaseClient
from indicator_knowledge import IndicatorKnowledge
from tests.test_complex_agents import QueueLLM, plan, settings, step
from tests.test_query_service import FakeIntent, FakeSchema
from tests.test_query_jobs import wait_until

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('CHATBI_P0_INTEGRATION') != '1', reason='requires demo MySQL and VM Chroma',
)]


def test_real_services_suspend_restart_and_consume_restored_decimal_artifact(tmp_path):
    first = step(); first.indicators = ['收入']
    second = step('step_2', depends_on=['step_1']).model_copy(update={
        'required_tables': [], 'required_fields': [],
        'input_bindings': [InputBinding(source_step_id='step_1', name='input_1', columns=['收入'])],
    })
    llm = QueueLLM(texts=[plan(first, second).model_dump_json()], sqls=[
        "SELECT SUM(net_amount) AS `收入` FROM sales_orders WHERE order_status='completed'",
        'DROP TABLE sales_orders',  # AST rejection; never reaches MySQL.
        'SELECT `收入` FROM input_1',
    ])
    config = settings(app_env='test', query_history_path=tmp_path/'history.sqlite',
                      metric_registry_path=tmp_path/'metrics.sqlite', simple_chroma_dir=tmp_path/'semantic')
    generated = []
    class TrackingModel:
        def generate_text(self, system_msg, prompt):
            return llm.generate_text(system_msg, prompt)
        def generate_sql(self, system_msg, prompt):
            sql = llm.generate_sql(system_msg, prompt); generated.append(sql); return sql
    model = TrackingModel()

    def connect():
        connection = pymysql.connect(
            host=os.getenv('CHATBI_P0_DB_HOST', '127.0.0.1'),
            port=int(os.getenv('CHATBI_P0_DB_PORT', '33307')),
            user=os.getenv('CHATBI_P0_DB_USER', 'root'), password=os.getenv('CHATBI_P0_DB_PASSWORD', ''),
            database='chatbi_mvp', charset='utf8mb4', autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
        return connection

    def factory(_):
        indicators = IndicatorKnowledge()
        chroma = ChromaSimpleSemanticPipeline(config, indicators)
        semantic = ComplexSemanticResolver(config, indicators, chroma)
        database = DatabaseClient(connection_factory=connect)
        security, user = QuerySecurityManager(), UserContext.demo_admin()
        workflow = PlanAndExecuteService(PlannerAgent(model, config, indicators),
            ExecutionAgent(model, security, database, config, user, semantic), SummaryAgent(QueueLLM(texts=['{}']), config))
        return ChatBIQueryService(llm=model, schema_provider=FakeSchema(), security=security, database=database,
            settings=config, indicators=indicators, intent_classifier=FakeIntent('complex'),
            simple_semantic=chroma, complex_semantic=semantic, plan_execute=workflow, user_context=user)

    app = create_app(config, service_factory=factory)
    with TestClient(app) as client:
        query_id = client.post('/api/v1/query-jobs', json={'question': '分步骤核算收入'}).json()['id']
        wait_until(lambda: client.get('/api/v1/history/'+query_id).json()['status'] == 'suspended', timeout=10)
        snapshot = app.state.history.checkpoint(query_id)
        value = snapshot['cached_results'][0]['rows'][0][0]
        assert isinstance(value, Decimal) and value > 0
        assert len(generated) == 2
    app = create_app(config, service_factory=factory)
    with TestClient(app) as client:
        assert client.get('/api/v1/history/'+query_id).json()['status'] == 'suspended'
        assert client.post(f'/api/v1/query-jobs/{query_id}/resume', json={'action': 'retry'}).status_code == 202
        wait_until(lambda: client.get('/api/v1/history/'+query_id).json()['status'] == 'succeeded', timeout=10)
        result = client.get('/api/v1/history/'+query_id).json()['response']
        assert Decimal(result['rows'][0][0]) == value and len(generated) == 3
        assert result['step_results'][0]['recovery_action'] == 'reused'
        assert result['step_results'][1]['consumed_artifacts'] == [result['step_results'][0]['result_artifact']['artifact_id']]
        assert app.state.history.checkpoint(query_id) is None
