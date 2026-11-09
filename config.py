"""
配置管理模块

收拢环境配置、运行配置和功能开关，减少主链路里的硬编码默认值。
"""

from __future__ import annotations

import copy
from chatbi.settings import get_settings


def _build_default_db_source() -> dict[str, object]:
    settings = get_settings()
    return {
        "driver": settings.db_driver,
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password.get_secret_value() if settings.db_password else None,
        "database": settings.db_name,
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": settings.db_connect_timeout,
        "read_timeout": settings.db_read_timeout,
        "write_timeout": settings.db_write_timeout,
    }


_settings = get_settings()
APP_CONFIG = {
    "environment": _settings.app_env,
    "database": {
        "default_source": "mysql_main",
        "sources": {
            "mysql_main": _build_default_db_source(),
        },
        "runtime": {
            "pool_size": _settings.db_pool_size,
            "max_overflow": _settings.db_pool_max_overflow,
            "pool_timeout": _settings.db_pool_timeout,
            "slow_query_threshold_ms": _settings.db_slow_query_threshold_ms,
        },
    },
    "llm": {
        "api_key": _settings.openai_api_key.get_secret_value() if _settings.openai_api_key else None,
        "base_url": _settings.openai_base_url,
        "model": _settings.llm_model,
        "embedding_model": _settings.embedding_model,
        "temperature": _settings.llm_temperature,
        "max_tokens": _settings.llm_max_tokens,
        "timeout": _settings.llm_timeout_seconds,
    },
    "features": {
        "few_shot": _settings.feature_few_shot,
        "rules": _settings.feature_rules,
        "guards": _settings.feature_guards,
        "indicator_knowledge": _settings.feature_indicator_knowledge,
        "schema_linking": _settings.feature_schema_linking,
        "indicator_rag": False,
    },
}


def get_database_source_config(
    source_id: str | None = None,
    app_config: dict | None = None,
) -> dict[str, object]:
    """返回指定数据源的配置，默认取当前应用默认数据源。"""
    config = app_config or APP_CONFIG
    database_config = config["database"]
    resolved_source_id = source_id or database_config["default_source"]
    source = database_config["sources"].get(resolved_source_id)
    if source is None:
        raise KeyError(f"Unknown database source: {resolved_source_id}")
    return copy.deepcopy(source)


DB_CONFIG = get_database_source_config()
DB_RUNTIME_CONFIG = APP_CONFIG["database"]["runtime"]
LLM_CONFIG = APP_CONFIG["llm"]
