from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chatbi.domain.security import QuerySecurityManager
from database import DatabaseClient


BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "test_cases.json"
REQUIRED_CATEGORIES = {
    "simple_aggregation",
    "dynamic_time",
    "multi_table_join",
    "revenue",
    "cost",
    "gross_profit",
    "expenses",
}


def load_benchmark() -> dict:
    return json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))


def test_benchmark_structure_and_expected_sql_are_valid() -> None:
    document = load_benchmark()
    assert document["version"]
    assert document["database_snapshot"]
    cases = document["cases"]
    assert len(cases) >= 7
    assert {case["category"] for case in cases} >= REQUIRED_CATEGORIES
    assert len({case["id"] for case in cases}) == len(cases)

    security = QuerySecurityManager()
    for case in cases:
        assert all(
            isinstance(case[field], str) and case[field].strip()
            for field in ("id", "question", "category", "difficulty", "business_definition", "expected_sql")
        )
        assert case["difficulty"] in {"simple", "medium", "complex"}
        security.secure(case["expected_sql"])


@pytest.mark.integration
def test_expected_sql_executes_on_demo_mysql() -> None:
    if os.getenv("CHATBI_BENCHMARK_DB_ENABLED") != "1":
        pytest.skip("set CHATBI_BENCHMARK_DB_ENABLED=1 for the demo MySQL smoke test")

    client = DatabaseClient(
        db_config={
            "host": os.getenv("CHATBI_BENCHMARK_DB_HOST", "127.0.0.1"),
            "port": int(os.getenv("CHATBI_BENCHMARK_DB_PORT", "3306")),
            "user": os.getenv("CHATBI_BENCHMARK_DB_USER", "chatbi_reader"),
            "password": os.getenv("CHATBI_BENCHMARK_DB_PASSWORD", ""),
            "database": os.getenv("CHATBI_BENCHMARK_DB_NAME", "chatbi_mvp"),
            "charset": "utf8mb4",
            "connect_timeout": 5,
            "read_timeout": 10,
            "write_timeout": 10,
            "autocommit": True,
        }
    )
    security = QuerySecurityManager()
    try:
        for case in load_benchmark()["cases"]:
            columns, rows = client.execute(security.secure(case["expected_sql"]))
            assert columns, case["id"]
            # Dynamic windows can legitimately be empty as the fixed seed ages.
            if case["category"] != "dynamic_time":
                assert rows, case["id"]
    finally:
        client.close()
