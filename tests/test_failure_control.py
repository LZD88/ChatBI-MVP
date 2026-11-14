from datetime import date, datetime
from decimal import Decimal
from threading import Event

import pytest
from fastapi.testclient import TestClient

from chatbi.api.app import create_app
from chatbi.application.failure_control import AnalysisSuspended, encode_state, decode_state, reflection
from chatbi.application.plan_execute_service import PlanAndExecuteService
from chatbi.application.progress import resumed_state
from chatbi.application.query_jobs import QueryJobs
from chatbi.domain.complex_analysis import ComplexQueryPlan, StepStatus
from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import QuerySecurityManager, UserContext
from chatbi.infrastructure.agents.executor import ExecutionAgent
from chatbi.infrastructure.agents.planner import PlannerAgent
from chatbi.infrastructure.agents.summarizer import SummaryAgent
from chatbi.infrastructure.query_history import QueryHistory, ResumeConflict
from chatbi.settings import Settings
from database import QueryExecutionError
from indicator_knowledge import IndicatorKnowledge
from tests.test_complex_agents import QueueLLM, FakeDatabase, step, plan, settings
from tests.test_query_jobs import wait_until
from tests.test_api import FakeService, FakeDatabase as HealthDatabase
from tests.test_query_service import make_service, FakeIntent
from chatbi.domain.complex_analysis import InputBinding


VALID_SQL = 'SELECT SUM(net_amount) AS `收入` FROM sales_orders'


def executor(llm, db=None, **options):
    return ExecutionAgent(llm, QuerySecurityManager(), db or FakeDatabase(), settings(**options), UserContext.demo_admin())


def test_final_and_all_ancestors_are_high_even_if_model_requests_low():
    payload = plan(step(), step('step_2', depends_on=['step_1']), step('step_3')).model_dump()
    payload['final_result_step_id'] = 'step_2'
    for item in payload['steps']:
        item['importance'] = 'low'
    validated = ComplexQueryPlan.model_validate(payload)
    assert [item.importance for item in validated.steps] == ['high', 'high', 'low']


def test_high_failure_exhausts_budget_then_blocks_all_unstarted_calls():
    query_plan = plan(step(), step('step_2', depends_on=['step_1']), step('step_3'))
    query_plan.steps[0].importance = 'high'
    llm = QueueLLM(sqls=['SELECT LOAD_FILE("secret") FROM sales_orders'] * 4 + [VALID_SQL])
    db = FakeDatabase()
    results = executor(llm, db, complex_core_max_retries=3).execute('分析', query_plan, 'sales_orders', '', 'req')
    assert [item.status for item in results] == [StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.BLOCKED]
    assert results[0].attempts == 4 and len(results[0].attempt_errors) == 4
    assert len(llm.sqls) == 1 and db.calls == 0
    assert results[1].recovery_action == 'circuit_blocked'


def test_auxiliary_failure_soft_skips_and_report_names_missing_analysis():
    query_plan = plan(step(), step('step_2', depends_on=['step_1']), step('step_3'))
    query_plan.steps[0].importance = 'low'
    query_plan.steps[1].importance = 'low'
    llm = QueueLLM(sqls=['SELECT LOAD_FILE("secret") FROM sales_orders'] * 2 + [VALID_SQL])
    db = FakeDatabase(rows=[(['收入'], [(100,)])])
    results = executor(llm, db, complex_aux_max_retries=1).execute('分析', query_plan, 'sales_orders', '', 'req')
    assert [item.status.value for item in results] == ['skipped', 'skipped', 'succeeded']
    assert results[0].attempts == 2 and results[0].recovery_action == 'soft_skip'
    assert db.calls == 1
    report = SummaryAgent(QueueLLM(texts=['{}']), settings()).summarize('分析', query_plan, results)
    assert any('辅助分析缺失' in text and query_plan.steps[0].step_name in text for text in report.caveats)


def test_reflection_passes_safe_failure_and_masked_sql_to_repair():
    captured = []
    class Model(QueueLLM):
        def generate_sql(self, system_msg, prompt):
            captured.append(prompt)
            return super().generate_sql(system_msg, prompt)
    llm = Model(sqls=["SELECT net_amount AS `错误列` FROM sales_orders WHERE order_no='private-order-value'", VALID_SQL])
    db = FakeDatabase(rows=[(['错误列'], [(1,)]), (['收入'], [(100,)])])
    result = executor(llm, db, complex_core_max_retries=1).execute('分析', plan(), 'sales_orders', '', 'req')[0]
    assert result.status is StepStatus.SUCCEEDED and result.recovery_action == 'repaired'
    assert result.attempts == 2 and '输出缺少契约字段' in captured[1]
    assert 'private-order-value' not in captured[1] and '__value__' in captured[1]


def test_vendor_error_and_permission_denial_never_leak_or_bypass_policy():
    diagnostic = reflection(QueryExecutionError('execution_error', 'credential-secret', {'error_code': 1054, 'raw_error': 'credential-secret'}), VALID_SQL, 1)
    assert '不存在的列' in diagnostic['message'] and 'credential-secret' not in str(diagnostic)
    class Denied(FakeDatabase):
        def execute(self, query):
            self.calls += 1
            raise QueryExecutionError('permission_denied', 'credential-secret')
    db = Denied()
    result = executor(QueueLLM(sqls=[VALID_SQL] * 4), db, complex_core_max_retries=3).execute('分析', plan(), 'sales_orders', '', 'req')[0]
    assert db.calls == 1 and result.attempts == 1
    assert 'credential-secret' not in result.model_dump_json()


def test_suspend_resume_reuses_successful_steps_without_replanning():
    query_plan = plan(step(), step('step_2', depends_on=['step_1']))
    llm = QueueLLM(texts=[query_plan.model_dump_json()], sqls=[VALID_SQL, 'DROP TABLE sales_orders'])
    db = FakeDatabase(rows=[(['收入'], [(Decimal('123.45'),)]), (['收入'], [(Decimal('123.45'),)])])
    config = settings()
    workflow = PlanAndExecuteService(PlannerAgent(llm, config, IndicatorKnowledge()), executor(llm, db), SummaryAgent(QueueLLM(texts=['{}']), config))
    with pytest.raises(AnalysisSuspended) as raised:
        workflow.run(question='分析', indicator_knowledge='', schema_text='sales_orders', detected_indicators=[], request_id='req')
    suspended = raised.value
    assert db.calls == 1 and suspended.public['failed_step_id'] == 'step_2'
    saved = decode_state(encode_state(suspended.state))
    assert saved['cached_results'][0]['rows'][0][0] == Decimal('123.45')
    saved['clarification'] = '仅使用已完成订单'
    llm.sqls.append(VALID_SQL)
    outcome = workflow.run(question='分析', indicator_knowledge='', schema_text='sales_orders', detected_indicators=[], request_id='req', resume_state=saved)
    assert db.calls == 2 and outcome.status == 'succeeded'
    assert outcome.step_results[0].recovery_action == 'reused'
    assert not llm.texts
    assert any('时间一致性' in text for text in outcome.report.caveats)


def test_checkpoint_preserves_numeric_and_date_types():
    data = {'values': [Decimal('1234567890.00123'), date(2026, 1, 2), datetime(2026, 1, 2, 3, 4)]}
    assert decode_state(encode_state(data)) == data


def test_summary_targets_use_original_evidence_indexes_and_dimension_contract():
    from tests.test_p0_workflow import source_result
    source = source_result()
    prompt = SummaryAgent(QueueLLM(), settings())._prompt('分析产品线', plan(), [source])
    import json
    controlled = json.loads(prompt.rsplit('<untrusted-results>', 1)[1].split('</untrusted-results>')[0])
    targets = controlled[0]['eligible_targets']
    assert targets[0] == {'step_id': 'step_1', 'row_index': 0, 'column': '产品线', 'value': '甲产品线'}
    assert all(item['column'] == '产品线' for item in targets)


def minimal_state():
    return {'question': '分析', 'resume_round': 0, 'max_resume_rounds': 2,
            'cached_results': [{'rows': [[Decimal('1.20')]]}]}


def minimal_public():
    return {'message': '核心任务失败', 'options': ['retry', 'clarify', 'abort'], 'resume_round': 0, 'max_resume_rounds': 2}


def test_jobs_suspend_release_worker_restart_and_resume_same_query(tmp_path):
    history = QueryHistory(tmp_path / 'history.sqlite')
    calls = []
    def execute(question, query_id):
        resume = resumed_state()
        calls.append(resume)
        if resume is None:
            raise AnalysisSuspended(minimal_state(), minimal_public())
        assert resume['cached_results'][0]['rows'][0][0] == Decimal('1.20')
        assert resume['clarification'] == '补充筛选条件'
        return {'rows': [[2]]}
    jobs = QueryJobs(history, execute)
    query_id = jobs.submit('分析')
    wait_until(lambda: history.get(query_id)['status'] == 'suspended')
    assert history.clear() == 0  # Bulk cleanup protects awaiting decisions.
    jobs.close()
    jobs = QueryJobs(QueryHistory(history.path), execute)
    try:
        assert history.get(query_id)['status'] == 'suspended'
        jobs.resume(query_id, 'clarify', '补充筛选条件')
        with pytest.raises(ResumeConflict):
            jobs.resume(query_id, 'retry')
        wait_until(lambda: history.get(query_id)['status'] == 'succeeded')
        assert history.checkpoint(query_id) is None and len(calls) == 2
    finally:
        jobs.close()


def test_resume_limit_abort_and_recovery_of_interrupted_resume(tmp_path):
    history = QueryHistory(tmp_path / 'history.sqlite')
    history.create('q', '分析')
    history.suspend('q', minimal_state(), minimal_public())
    history.claim_resume('q', 'retry', '')
    history.recover()
    assert history.get('q')['status'] == 'suspended'
    assert history.get('q')['response']['suspension']['resume_round'] == 1
    history.claim_resume('q', 'retry', '')
    history.recover()
    with pytest.raises(ResumeConflict, match='次数上限'):
        history.claim_resume('q', 'retry', '')
    jobs = QueryJobs(history, lambda q, i: {})
    try:
        assert jobs.cancel('q')
        assert history.checkpoint('q') is None and history.get('q')['status'] == 'cancelled'
    finally:
        jobs.close()


def test_cancel_wins_over_concurrent_suspend(tmp_path):
    history = QueryHistory(tmp_path / 'history.sqlite')
    entered, release = Event(), Event()
    def execute(question, query_id):
        entered.set(); release.wait(2)
        raise AnalysisSuspended(minimal_state(), minimal_public())
    jobs = QueryJobs(history, execute)
    try:
        query_id = jobs.submit('分析'); assert entered.wait(1)
        assert jobs.cancel(query_id); release.set()
        wait_until(lambda: history.get(query_id)['status'] == 'cancelled')
        assert history.checkpoint(query_id) is None
    finally:
        release.set(); jobs.close()


def test_timed_out_continuation_preserves_previous_checkpoint(tmp_path):
    from time import sleep
    from chatbi.application.progress import checkpoint
    history = QueryHistory(tmp_path / 'history.sqlite')
    history.create('q', '分析'); history.suspend('q', minimal_state(), minimal_public())
    def execute(question, query_id):
        sleep(.1); checkpoint()
    jobs = QueryJobs(history, execute, timeout=.04)
    try:
        jobs.resume('q', 'retry')
        wait_until(lambda: history.get('q')['status'] == 'suspended')
        assert history.checkpoint('q')['resume_round'] == 1
        assert '总时限' in history.get('q')['response']['suspension']['reason']
    finally:
        jobs.close()


def test_cancel_cannot_overwrite_finished_cancelled_state_with_cancelling(tmp_path):
    from time import sleep
    from chatbi.application.progress import checkpoint
    class SlowHistory(QueryHistory):
        def status(self, query_id, status):
            if status == 'cancelling':
                sleep(.05)
            super().status(query_id, status)
    history = SlowHistory(tmp_path / 'history.sqlite')
    entered, release = Event(), Event()
    def execute(question, query_id):
        entered.set(); release.wait(2); checkpoint(); return {}
    jobs = QueryJobs(history, execute)
    try:
        query_id = jobs.submit('分析'); assert entered.wait(1)
        original_set = jobs.events[query_id].set
        def signal():
            original_set(); release.set()
        jobs.events[query_id].set = signal
        assert jobs.cancel(query_id)
        wait_until(lambda: jobs.queue.unfinished_tasks == 0)
        assert history.get(query_id)['status'] == 'cancelled'
    finally:
        release.set(); jobs.close()


def test_api_suspension_same_query_resume_and_version_conflict(tmp_path):
    class Service(FakeService):
        def query(self, question, request_id=None):
            if resumed_state() is None:
                raise AnalysisSuspended(minimal_state(), minimal_public())
            return super().query(question, request_id)
    config = Settings(_env_file=None, app_env='test', query_history_path=tmp_path/'history.sqlite', metric_registry_path=tmp_path/'metrics.sqlite')
    factory = lambda _: Service(HealthDatabase())
    app = create_app(config, service_factory=factory)
    with TestClient(app) as client:
        query_id = client.post('/api/v1/query-jobs', json={'question': '分析'}).json()['id']
        wait_until(lambda: client.get('/api/v1/history/'+query_id).json()['status'] == 'suspended')
        assert 'event: done' in client.get(f'/api/v1/query-jobs/{query_id}/events').text
        assert client.get('/health/live').status_code == 200
        assert client.post(f'/api/v1/query-jobs/{query_id}/resume', json={'action': 'clarify'}).status_code == 409
    app = create_app(config, service_factory=factory)
    with TestClient(app) as client:
        assert client.get('/api/v1/history/'+query_id).json()['status'] == 'suspended'
        original_version = app.state.source_version
        app.state.source_version = 'new-source'
        assert client.post(f'/api/v1/query-jobs/{query_id}/resume', json={'action': 'retry'}).status_code == 409
        app.state.source_version = original_version
        assert client.post(f'/api/v1/query-jobs/{query_id}/resume', json={'action': 'clarify', 'clarification': '补充条件'}).status_code == 202
        wait_until(lambda: client.get('/api/v1/history/'+query_id).json()['status'] == 'succeeded')
        assert client.get('/api/v1/history/'+query_id).json()['response']['rows'] == [[7]]
        assert client.post(f'/api/v1/query-jobs/{query_id}/resume', json={'action': 'retry'}).status_code == 409


def test_full_api_query_service_recovers_typed_cte_dependency_without_replanning(tmp_path):
    first = step()
    first.indicators = ['收入']
    second = step('step_2', depends_on=['step_1']).model_copy(update={
        'required_tables': [], 'required_fields': [],
        'input_bindings': [InputBinding(source_step_id='step_1', name='input_1', columns=['收入'])],
    })
    payload = plan(first, second, step('step_3')).model_dump()
    payload['final_result_step_id'] = 'step_2'
    query_plan = ComplexQueryPlan.model_validate(payload)
    llm = QueueLLM(texts=[query_plan.model_dump_json()], sqls=[VALID_SQL, 'DROP TABLE sales_orders'])
    db = FakeDatabase(rows=[(['收入'], [(Decimal('123.45'),)]), (['收入'], [(Decimal('123.45'),)]), (['收入'], [(1,)])])
    config = settings(app_env='test', query_history_path=tmp_path/'history.sqlite', metric_registry_path=tmp_path/'metrics.sqlite')
    service = make_service(llm=llm, database=db, intent=FakeIntent('complex'))
    service.settings = config
    service.plan_execute = PlanAndExecuteService(PlannerAgent(llm, config, IndicatorKnowledge()), executor(llm, db), SummaryAgent(QueueLLM(texts=['{}']), config))
    with TestClient(create_app(config, service_factory=lambda _: service)) as client:
        query_id = client.post('/api/v1/query-jobs', json={'question': '分析收入'}).json()['id']
        wait_until(lambda: client.get('/api/v1/history/'+query_id).json()['status'] == 'suspended')
        assert db.calls == 1
        llm.sqls.extend(['SELECT `收入` FROM input_1', VALID_SQL])
        response = client.post(f'/api/v1/query-jobs/{query_id}/resume', json={'action': 'retry'})
        assert response.status_code == 202
        wait_until(lambda: client.get('/api/v1/history/'+query_id).json()['status'] in {'failed', 'succeeded', 'suspended'})
        item = client.get('/api/v1/history/'+query_id).json()
        assert item['status'] == 'succeeded', item
        assert db.calls == 3 and not llm.texts
        result = item['response']
        assert result['rows'] == [['123.45']]
        assert result['step_results'][0]['recovery_action'] == 'reused'
        assert result['step_results'][1]['consumed_artifacts']
