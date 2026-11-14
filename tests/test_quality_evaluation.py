from dataclasses import replace

from chatbi.application.quality_evaluation import aggregate_quality, assess_complex
from chatbi.domain.models import QueryResult


def sample():
    ref = {"step_id": "step_1", "row_index": 0, "column": "收入", "value": 100}
    target = {"step_id": "step_1", "row_index": 0, "column": "产品线", "value": "甲"}
    action = {key: "复核" for key in ("problem", "action", "owner_role", "priority", "expected_impact", "precondition", "risk", "confidence")}
    return QueryResult(
        request_id="test", question="比较收入", sql="SELECT 1", columns=("产品线", "收入"), rows=(("甲", 100),),
        row_count=1, truncated=False, schema_mode="semantic", agent_mode="plan_execute", agent_status="succeeded",
        plan={"source": "model", "steps": [{"step_id": "step_1"}, {"step_id": "step_2", "input_bindings": [{"source_step_id": "step_1"}]}]},
        step_results=({"step_id": "step_1", "status": "succeeded", "columns": ["产品线", "收入"], "rows": [["甲", 100]], "result_artifact": {"artifact_id": "a1"}},
                      {"step_id": "step_2", "status": "succeeded", "consumed_artifacts": ["a1"]}),
        analysis_report={"findings": [{"evidence": [ref]}], "recommendations": [{**action, "target": "甲", "evidence": [ref, target]}]},
        report_source="summary_agent",
    )


def test_quality_gate_tracks_dependencies_and_evidence_without_claiming_effectiveness():
    record = assess_complex(sample(), {"requires_binding": True})
    assert record["dependency_ok"] and record["planning_success"]
    report = aggregate_quality([{**record, "execution_match": True, "latency_ms": 100}])
    assert report["passed"]
    assert report["metrics"]["evidence_reference_accuracy"] == 1
    assert report["metrics"]["business_effectiveness"] == "requires_human_review"


def test_forged_evidence_and_target_fail_gate_even_with_successful_sql():
    result = sample()
    result.analysis_report["findings"][0]["evidence"][0]["value"] = 999
    result.analysis_report["recommendations"][0]["target"] = "不存在的产品"
    record = assess_complex(result, {"requires_binding": True})
    assert record["valid_evidence_count"] < record["evidence_count"]
    assert record["grounded_recommendation_count"] == 0
    assert not aggregate_quality([{**record, "execution_match": True}])["passed"]


def test_missing_outputs_cannot_pass_vacuously():
    assert not aggregate_quality([])["passed"]
    assert not aggregate_quality([{"error_code": "model_unavailable"}])["passed"]
    result = replace(sample(), analysis_report={"recommendations": []})
    record = assess_complex(result, {"requires_binding": True})
    assert not aggregate_quality([{**record, "execution_match": True}])["passed"]


def test_serialized_decimal_evidence_is_counted_only_with_metric_contract():
    result = sample()
    result.step_results[0]["rows"][0][1] = "100.00"
    result.step_results[0]["result_artifact"]["columns"] = [{"name": "收入", "role": "metric"}]
    for item in [*result.analysis_report["findings"], *result.analysis_report["recommendations"]]:
        for ref in item["evidence"]:
            if ref["column"] == "收入":
                ref["value"] = "100.00"
    assert assess_complex(result, {})["grounded_recommendation_count"] == 1
    result.step_results[0]["result_artifact"]["columns"][0]["role"] = "dimension"
    assert assess_complex(result, {})["grounded_recommendation_count"] == 0
