"""Typed runtime settings for the ChatBI MVP."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings with safe, non-secret defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    metric_registry_path: Path = Path(".runtime/metrics.sqlite3")
    query_history_path: Path = Path(".runtime/query-history.sqlite3")
    history_retention: int = Field(default=200, ge=10, le=2000)
    query_job_capacity: int = Field(default=8, ge=1, le=32)
    query_total_timeout_seconds: int = Field(default=240, ge=1, le=1800)

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=2000, ge=1, le=32000)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    intent_api_key: SecretStr | None = None
    intent_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    intent_model: str = "qwen-plus"
    intent_timeout_seconds: float = Field(default=10.0, gt=0, le=120)

    db_driver: str = "mysql"
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_user: str | None = None
    db_password: SecretStr | None = None
    db_name: str = "chatbi_mvp"
    db_connect_timeout: int = Field(default=5, ge=1, le=60)
    db_read_timeout: int = Field(default=8, ge=1, le=300)
    db_write_timeout: int = Field(default=8, ge=1, le=300)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_pool_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout: float = Field(default=3.0, gt=0, le=60)
    db_slow_query_threshold_ms: float = Field(default=200.0, ge=0)

    query_max_length: int = Field(default=1000, ge=1, le=10000)
    query_max_rows: int = Field(default=200, ge=1, le=5000)
    query_timeout_seconds: int = Field(default=10, ge=1, le=300)
    query_allowed_tables: str = (
        "dim_customers,dim_products,sales_orders,exchange_rates,finance_expenses,"
        "dim_regions,dim_countries,dim_departments,dim_employees,dim_suppliers,"
        "dim_warehouses,dim_sales_channels,dim_currencies,dim_calendar,dim_cost_centers,"
        "purchase_orders,purchase_order_items,sales_order_items,inventory_balances,"
        "inventory_transactions,production_orders,production_outputs,quality_inspections,"
        "equipment,equipment_maintenance,shipments,shipment_items,customer_payments,"
        "supplier_payments,budgets"
    )
    query_sensitive_fields: str = ""

    chroma_persist_dir: Path = Path("chroma_db")
    schema_index_version: str = "1"
    simple_chroma_dir: Path = Path("chroma_db/simple_semantic")
    simple_chroma_host: str = "127.0.0.1"
    simple_chroma_port: int = Field(default=18081, ge=1, le=65535)
    simple_chroma_ssl: bool = False
    simple_schema_index_version: str = "3"
    chinese_embedding_provider: str = "auto"
    simple_indicator_max_distance: float = Field(default=0.80, ge=0.0, le=2.0)
    simple_table_top_k: int = Field(default=5, ge=1, le=15)
    simple_field_top_k: int = Field(default=12, ge=1, le=50)

    feature_few_shot: bool = True
    feature_rules: bool = True
    feature_guards: bool = True
    feature_indicator_knowledge: bool = True
    feature_schema_linking: bool = True
    feature_intent_router: bool = True
    feature_simple_chroma: bool = True
    feature_complex_plan_execute: bool = True
    complex_plan_max_steps: int = Field(default=6, ge=1, le=8)
    complex_plan_repair_attempts: int = Field(default=1, ge=0, le=2)
    complex_summary_rows_per_step: int = Field(default=30, ge=1, le=100)
    complex_summary_repair_attempts: int = Field(default=1, ge=0, le=2)
    complex_step_retry: int = Field(default=1, ge=0, le=2, description="旧配置兼容；节点重试由 core/aux_max_retries 管理")
    complex_core_max_retries: int = Field(default=3, ge=0, le=3)
    complex_aux_max_retries: int = Field(default=1, ge=0, le=3)
    complex_max_resume_rounds: int = Field(default=3, ge=1, le=10)
    complex_schema_max_tables: int = Field(default=12, ge=1, le=20)
    complex_dependency_max_rows: int = Field(default=200, ge=1, le=500)
    complex_dependency_max_cells: int = Field(default=4000, ge=1, le=10000)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard Python logging level")
        return normalized

    @property
    def allowed_tables(self) -> frozenset[str]:
        return frozenset(item.strip().lower() for item in self.query_allowed_tables.split(",") if item.strip())

    @property
    def sensitive_fields(self) -> frozenset[str]:
        return frozenset(item.strip().lower() for item in self.query_sensitive_fields.split(",") if item.strip())

    def validate_query_runtime(self) -> None:
        """Fail fast when dependencies required by the query endpoint are absent."""
        missing: list[str] = []
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
            missing.append("OPENAI_API_KEY")
        if self.feature_intent_router and (
            self.intent_api_key is None or not self.intent_api_key.get_secret_value()
        ):
            missing.append("INTENT_API_KEY")
        if not self.db_user:
            missing.append("DB_USER")
        if self.db_password is None or not self.db_password.get_secret_value():
            missing.append("DB_PASSWORD")
        if missing:
            raise ValueError(f"Missing required runtime settings: {', '.join(missing)}")

    def safe_snapshot(self) -> dict[str, Any]:
        """Return diagnostic configuration without any secret material."""
        data = self.model_dump(exclude={"openai_api_key", "intent_api_key", "db_password"}, mode="json")
        data["openai_api_key_configured"] = self.openai_api_key is not None
        data["intent_api_key_configured"] = self.intent_api_key is not None
        data["db_password_configured"] = self.db_password is not None
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
