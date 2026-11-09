"""安全、可复现的 Text-to-SQL 基准评估器。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import sqlglot

from chatbi.domain.errors import ChatBIError, ErrorCode
from chatbi.domain.security import QuerySecurityManager, SecuredQuery
from database import DatabaseClient, QueryExecutionError


FAILURE_GENERATION = "generation_failed"
FAILURE_SECURITY = "security_rejected"
FAILURE_DATABASE = "database_execution_failed"
FAILURE_EXPECTED_SQL = "expected_sql_invalid"
FAILURE_MISMATCH = "result_mismatch"


class Evaluator:
    """通过安全边界执行期望 SQL 与生成 SQL，并比较结果等价性。"""

    def __init__(self, db_client: DatabaseClient | Any | None = None,
                 security: QuerySecurityManager | None = None, *,
                 model_config: dict[str, Any] | None = None) -> None:
        self.db = db_client or DatabaseClient()
        self.security = security or QuerySecurityManager()
        self.model_config = model_config or {}

    def load_benchmark(self, path: str | Path = "test_cases.json") -> dict[str, Any]:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
            raise ValueError("基准文件必须是包含 cases 数组的 JSON 对象")
        return document

    def load_test_cases(self, path: str | Path = "test_cases.json") -> list[dict[str, Any]]:
        """保留旧教学入口，但统一读取版本化基准文档。"""
        return self.load_benchmark(path)["cases"]

    def evaluate_one(self, case: dict[str, Any],
                     sql_generator: Callable[[str], str]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": case.get("id", "unknown"), "category": case.get("category", "unknown"),
            "difficulty": case.get("difficulty", "unknown"), "question": case["question"],
            "expected_sql": case["expected_sql"], "generated_sql": None,
            "exact_match": False, "execution_match": False, "execution_failed": False,
            "failure_class": None, "error_code": None, "error": None, "detail": {},
        }
        try:
            expected_query = self.security.secure(case["expected_sql"])
            expected_columns, expected_rows = self.db.execute(expected_query)
        except ChatBIError as exc:
            return self._fail(result, FAILURE_EXPECTED_SQL, exc.code.value, exc.public_message)
        except QueryExecutionError as exc:
            return self._fail(result, FAILURE_EXPECTED_SQL, exc.error_type, str(exc))
        except Exception:
            return self._fail(result, FAILURE_EXPECTED_SQL, "unexpected_error", "预期 SQL 无法执行")

        try:
            generated_sql = sql_generator(case["question"])
            result["generated_sql"] = generated_sql
            result["exact_match"] = self._normalize_sql(generated_sql) == self._normalize_sql(case["expected_sql"])
        except ChatBIError as exc:
            return self._fail(result, FAILURE_GENERATION, exc.code.value, exc.public_message, execution_failed=True)
        except Exception:
            return self._fail(result, FAILURE_GENERATION, "generation_error", "SQL 生成失败", execution_failed=True)

        try:
            generated_query = self.security.secure(generated_sql)
        except ChatBIError as exc:
            failure = FAILURE_SECURITY if exc.code is ErrorCode.SQL_REJECTED else FAILURE_GENERATION
            return self._fail(result, failure, exc.code.value, exc.public_message, execution_failed=True)
        try:
            generated_columns, generated_rows = self.db.execute(generated_query)
        except QueryExecutionError as exc:
            return self._fail(result, FAILURE_DATABASE, exc.error_type, str(exc), execution_failed=True)
        except Exception:
            return self._fail(result, FAILURE_DATABASE, "unexpected_error", "生成 SQL 执行失败", execution_failed=True)

        result["execution_match"] = self._results_equivalent(
            generated_columns, generated_rows, expected_columns, expected_rows
        )
        result["detail"] = {
            "expected_columns": list(expected_columns), "expected_row_count": len(expected_rows),
            "generated_columns": list(generated_columns), "generated_row_count": len(generated_rows),
        }
        if not result["execution_match"]:
            result["failure_class"] = FAILURE_MISMATCH
        return result

    def evaluate_all(self, cases: list[dict[str, Any]],
                     sql_generator: Callable[[str], str]) -> list[dict[str, Any]]:
        return [self.evaluate_one(case, sql_generator) for case in cases]

    def build_report(self, results: list[dict[str, Any]], *,
                     benchmark_version: str = "unknown",
                     database_snapshot: str = "unknown") -> dict[str, Any]:
        total = len(results)
        execution_correct = sum(bool(item["execution_match"]) for item in results)
        exact_correct = sum(bool(item["exact_match"]) for item in results)
        execution_failures = sum(bool(item["execution_failed"]) for item in results)
        denominator = total or 1
        failure_classes = Counter(item["failure_class"] for item in results if item["failure_class"])
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "benchmark_version": benchmark_version, "database_snapshot": database_snapshot,
            "model_config": self.model_config,
            "metrics": {
                "total": total, "execution_correct": execution_correct,
                "execution_accuracy": execution_correct / denominator,
                "exact_match_correct": exact_correct, "exact_match_accuracy": exact_correct / denominator,
                "execution_failures": execution_failures,
                "execution_failure_rate": execution_failures / denominator,
            },
            "failure_classes": dict(sorted(failure_classes.items())),
            "passed_mvp_threshold": total > 0 and execution_correct / denominator >= 0.80
            and execution_failures / denominator <= 0.10,
            "results": results,
        }

    def generate_report(self, results: list[dict[str, Any]], *,
                        benchmark_version: str = "unknown",
                        database_snapshot: str = "unknown") -> str:
        report = self.build_report(results, benchmark_version=benchmark_version,
                                   database_snapshot=database_snapshot)
        metrics = report["metrics"]
        lines = [
            "=" * 60, "ChatBI Text-to-SQL 评估报告", "=" * 60,
            f"生成时间：{report['generated_at']}", f"基准版本：{benchmark_version}",
            f"数据快照：{database_snapshot}",
            f"模型配置：{json.dumps(self.model_config, ensure_ascii=False, sort_keys=True)}",
            f"Execution Accuracy：{metrics['execution_correct']}/{metrics['total']} = {metrics['execution_accuracy']:.1%}",
            f"Exact Match：{metrics['exact_match_correct']}/{metrics['total']} = {metrics['exact_match_accuracy']:.1%}",
            f"执行失败率：{metrics['execution_failures']}/{metrics['total']} = {metrics['execution_failure_rate']:.1%}",
            f"MVP 门槛：{'通过' if report['passed_mvp_threshold'] else '未通过'}",
            f"失败分类：{json.dumps(report['failure_classes'], ensure_ascii=False, sort_keys=True)}",
            "", "详细结果：",
        ]
        for item in results:
            status = "通过" if item["execution_match"] else item["failure_class"] or "不匹配"
            lines.append(f"[{item['id']}] {status} | {item['question']}")
            if item["error"]:
                lines.append(f"  {item['error_code']}: {item['error']}")
        return "\n".join(lines)

    @staticmethod
    def _fail(result: dict[str, Any], failure_class: str, error_code: str,
              message: str, *, execution_failed: bool = False) -> dict[str, Any]:
        result.update(failure_class=failure_class, error_code=error_code, error=message,
                      execution_failed=execution_failed)
        return result

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        try:
            return sqlglot.parse_one(sql, read="mysql").sql(dialect="mysql", pretty=False).lower()
        except Exception:
            return " ".join(sql.lower().split())

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        if isinstance(value, Decimal):
            return ("number", value.normalize())
        if isinstance(value, float):
            return ("number", round(value, 9))
        if isinstance(value, (int, str, bytes, type(None), bool)):
            return (type(value).__name__, value)
        return (type(value).__name__, str(value))

    def _results_equivalent(self, generated_columns: list[str],
                            generated_rows: list[tuple[Any, ...]],
                            expected_columns: list[str],
                            expected_rows: list[tuple[Any, ...]]) -> bool:
        if len(generated_columns) != len(expected_columns) or len(generated_rows) != len(expected_rows):
            return False

        def normalized(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
            values = [tuple(self._normalize_value(value) for value in row) for row in rows]
            return sorted(values, key=repr)

        # 执行等价性以列位置和值为准；工具性别名不同不应降低 Execution Accuracy。
        return normalized(generated_rows) == normalized(expected_rows)


def run_evaluation(sql_generator: Callable[[str], str], test_cases_path: str = "test_cases.json",
                   *, model_config: dict[str, Any] | None = None) -> dict[str, Any]:
    evaluator = Evaluator(model_config=model_config)
    benchmark = evaluator.load_benchmark(test_cases_path)
    results = evaluator.evaluate_all(benchmark["cases"], sql_generator)
    print(evaluator.generate_report(results, benchmark_version=benchmark["version"],
                                    database_snapshot=benchmark["database_snapshot"]))
    return evaluator.build_report(results, benchmark_version=benchmark["version"],
                                  database_snapshot=benchmark["database_snapshot"])


if __name__ == "__main__":
    import argparse
    from chatbi.settings import get_settings
    from indicator_knowledge import IndicatorKnowledge
    from llm_client import LLMClient
    from prompt_builder import build_prompt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", nargs="?", default="test_cases.json")
    parser.add_argument("--output", type=Path, help="将完整 JSON 报告写入此路径")
    arguments = parser.parse_args()

    settings = get_settings()
    llm = LLMClient(settings)
    indicators = IndicatorKnowledge()

    def generate_sql(question: str) -> str:
        system_message, prompt = build_prompt(
            question, use_rules=settings.feature_rules, use_guards=settings.feature_guards,
            indicator_knowledge=indicators.build_knowledge_block(question),
        )
        return llm.generate_sql(system_message, prompt)

    report = run_evaluation(generate_sql, arguments.benchmark, model_config={
        "provider": "openai-compatible", "base_url": settings.openai_base_url,
        "model": settings.llm_model, "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    })
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"完整 JSON 报告已保存：{arguments.output}")
