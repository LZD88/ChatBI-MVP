# Proposal

## Why

当前仓库已具备 Text-to-SQL、指标知识、Schema 召回、数据库访问和评估等独立模块，但入口仍是脚手架，且存在依赖不完整、关键安全模块缺失、无样例数据库与无用户界面等问题，无法形成可演示、可验证的端到端产品。需要将这些模块收敛为一个本地可运行、面向单用户的 ChatBI MVP，以验证“自然语言提问到可信查询结果”的完整价值链。

## What Changes

- 提供一个本地单用户 Web 聊天界面，支持提交业务问题并展示处理状态、生成 SQL、表格结果及可理解的错误信息。
- 提供 FastAPI 查询接口和统一应用服务，编排问题校验、指标知识、Schema 召回、Prompt 构建、LLM 生成、安全校验和数据库执行。
- 补齐 SQL 安全边界：仅允许单条只读查询、限制可访问表、强制结果行数上限、拒绝危险语句，并使用只读数据库账号。
- 将现有表级召回与字段匹配接入主链路；召回不可用时允许受控回退到静态 Schema，并明确记录回退状态。
- 提供可复现的本地演示环境，包括完整依赖、环境变量模板、MySQL 建表与种子数据、索引初始化和一键启动说明。
- 建立最小回归测试与 Text-to-SQL 验收集，覆盖核心业务口径、API、SQL 安全和端到端演示场景。
- 清理或替换当前缺失的 `security.py`、`schema_linker.py` 契约，重写无实际功能的 `main.py`，并补充 README。
- MVP 不包含登录、多租户、生产数据库接入、云部署、复杂图表、自然语言多轮追问和企业级权限体系。

## Capabilities

### New Capabilities

- `chatbi-query`: 接收自然语言问题，编排指标知识、Schema 召回、SQL 生成与执行，并返回结构化查询结果和可诊断状态。
- `sql-query-safety`: 对模型生成的 SQL 实施只读、单语句、表白名单、结果上限和执行超时等安全约束。
- `web-query-experience`: 为本地单用户提供可操作的 Web 提问界面，展示 SQL、结果表格、加载状态和错误反馈。
- `local-mvp-runtime`: 提供可复现的依赖、配置、演示数据库、索引初始化、启动与健康检查能力。
- `text2sql-quality-gates`: 通过单元、集成和基准用例验证业务口径、SQL 安全、接口行为与执行正确率。

### Modified Capabilities

无。当前项目尚无已发布的 OpenSpec capability。

## Impact

- 现有 `config.py`、`prompt_builder.py`、`llm_client.py`、`database.py`、`table_retriever.py`、`field_matcher.py`、`indicator_knowledge.py` 和 `evaluator.py` 将被接入统一应用链路。
- 新增 FastAPI 后端、静态 Web 前端、SQL 安全模块、应用服务层、数据库初始化资产和自动化测试。
- `pyproject.toml` 将补齐 FastAPI、MySQL、环境配置、LangChain/Chroma 和测试依赖，并生成可复现的锁文件。
- 外部依赖包括一个 OpenAI 兼容模型/Embedding 接口和本地 MySQL；演示数据与配置不得包含真实凭据或生产数据。
- 对外形成稳定的本地 HTTP API；现有教学脚本可保留为示例，但不再作为产品入口。
