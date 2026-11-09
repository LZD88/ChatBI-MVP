# ChatBI MVP

ChatBI 是一个本地、单用户的自然语言转 MySQL 查询 MVP。用户在浏览器输入业务问题，系统依次完成指标知识注入、Schema 选择、模型生成 SQL、AST 安全校验和只读数据库执行，并展示生成 SQL 与表格结果。

当前 MVP 固定使用 MySQL 8.0 和 OpenAI 兼容的 Chat API。服务默认只监听 `127.0.0.1`，不应直接暴露到公网。

查询链路在 Text-to-SQL 之前使用阿里云百炼 `qwen-plus` 识别问题复杂度。简单问题不等于单表问题：普通 JOIN 仍可走简单链路，并通过 Chroma 完成指标、表和字段召回以及 Join 路径推断。复杂问题进入三 Agent Plan-and-Execute 链路：规划 Agent 输出经过目录、Schema 和 DAG 校验的结构化计划；执行 Agent 按依赖逐步生成 SQL，并强制经过 AST 只读安全网和数据库入口；总结 Agent 只能引用真实执行结果中的具体单元格，后端反查证据值并渲染中文分析报告。百炼暂时不可用时系统回退到本地规则，并在 API 与页面中标记 `intent_source=fallback`。

复杂查询 API 会按图中的四类产物返回 `plan`、`step_results`、`summary` 和 `report`，同时提供更适合前端结构化展示的 `analysis_report`、`report_markdown`、`report_source`、`agent_mode` 和 `agent_timings_ms`。原有 `sql`、`columns` 和 `rows` 映射到最后一个成功步骤，因此旧调用方保持兼容；简单查询的 Agent 扩展字段为空。

## 指标定义规范

`indicators.json` 中每个指标必须包含以下六个治理字段，缺失、空值、未知依赖、重复名称或循环依赖都会在加载时被拒绝：

| 字段 | 含义 |
| --- | --- |
| `name` | 指标名称 |
| `definition` | 统一业务定义 |
| `formula` | SQL 计算表达式或依赖指标表达式 |
| `data_source` | 来源表和必要 Join 说明 |
| `depends_on` | 依赖的其他指标名称 |
| `time_field` | 指标使用的时间维度字段 |

`aliases` 和 `filters` 是检索与强制业务口径的扩展字段。派生指标会递归展开全部依赖后再注入 Prompt，但 API 的 `recalled_indicators` 只报告用户实际请求或召回的指标，避免把依赖误报成用户意图。

当前目录包含 **61 个指标**，覆盖销售与利润、采购、库存、生产、质量、设备、物流、客户回款、供应商付款、预算、人力和基础经营规模。指标数量不等于表数量：事实表提供金额、数量和比率指标，维度表主要作为分析维度，仅在存在明确经营含义时提供规模类指标。

## SQL 主链路

运行时顺序固定为：意图识别 → 指标检索 → Schema/字段/Join 链接 → 动态组装 Schema、RULES、ERROR_GUARDS、角色与 Few-shot → 模型生成并识别 SQL → MySQL AST 只读安全网 → 行级权限条件注入 → 明细结果上限 → 连接池取连接并执行 → 慢查询 EXPLAIN 或异常翻译 → API 状态码映射。

动态 Schema 不可用时会回退到受治理的全量 Schema。`pipeline_stages` 会返回本次查询经过的阶段；本地单用户管理员模式没有范围条件，因此显示 `row_level_policy_skipped`。配置了 `UserContext.row_filters` 后，条件由 AST 注入而不是字符串拼接。

复杂查询固定执行：意图识别 → 指标与 Schema 上下文 → 规划 Agent → 计划契约与目录校验 → 执行 Agent 逐步生成和安全执行 → 总结 Agent 固定 JSON → 证据反查 → 确定性中文报告。某一步失败时，其依赖步骤会被标记为 `skipped`，无依赖步骤仍可继续；至少一个步骤成功时返回 `partial` 报告，全部失败时返回现有稳定错误码而不会生成伪报告。

## 前置条件

- Windows、macOS 或 Linux 开发机
- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.9 或更高版本
- MySQL 8.0
- 一个可用的 OpenAI 兼容 Chat API Key
- VirtualBox 中可运行的 Linux 虚拟机；Windows 主机通过本地端口访问虚拟机内的 Chroma
- 一个本地 MySQL 管理员账号，仅在初始化演示库时使用

## 1. 安装依赖

在项目根目录执行：

```powershell
uv sync --frozen
```

若尚未安装 `uv`，可按照官方说明安装，或在 Windows PowerShell 中执行：

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

## 2. 配置环境

复制模板并编辑本地 `.env`：

```powershell
Copy-Item .env.example .env
```

至少填写：

```dotenv
OPENAI_API_KEY=<你的模型服务 Key>
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small

INTENT_API_KEY=<阿里云百炼 API Key>
INTENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
INTENT_MODEL=qwen-plus

DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=chatbi_reader
DB_PASSWORD=<本地只读账号密码>
DB_NAME=chatbi_mvp
```

`.env` 已被 Git 忽略。不要把真实 API Key、管理员密码或只读账号密码提交到仓库。

## 3. 初始化演示数据库

先启动本机 MySQL 8.0，再通过进程环境变量传递管理员凭据和将要创建的只读账号密码：

```powershell
$env:DB_ADMIN_HOST = "127.0.0.1"
$env:DB_ADMIN_PORT = "3306"
$env:DB_ADMIN_USER = "root"
$env:DB_ADMIN_PASSWORD = "<本地管理员密码>"
$env:CHATBI_READER_PASSWORD = "<与 .env 中 DB_PASSWORD 相同的密码>"
uv run python -m scripts.init_database
```

命令会幂等创建 `chatbi_mvp`、30 张演示业务表、虚构种子数据，以及仅拥有 `SELECT` 权限的 `chatbi_reader`。重复执行不会重复追加种子数据。

可用只读账号验证基准收入：

```powershell
uv run python -c "from chatbi.domain.security import QuerySecurityManager; from database import DatabaseClient; print(DatabaseClient().execute(QuerySecurityManager().secure(\"SELECT SUM(o.net_amount * r.rate_to_cny) AS revenue_cny FROM sales_orders o JOIN exchange_rates r ON o.order_date = r.rate_date AND o.currency = r.currency WHERE o.order_status = 'completed'\")))"
```

演示数据的基准结果为 `462752.000000`。需要清理项目创建的演示对象时，请先阅读并手工核对 `db/rollback.sql`；它只应针对明确命名的 `chatbi_mvp` 和演示账号执行。

## 4. 启动虚拟机 Chroma

当前 Windows 环境不在 API 进程内运行原生 Chroma。`agent-eval-dev` 虚拟机中的 Chroma 监听 guest `8000`，VirtualBox 把它映射到仅本机可访问的 `127.0.0.1:18081`。虚拟机启动后，Chroma 通过 `@reboot` 自动启动；也可在虚拟机内手工执行：

```bash
/home/agentdev/run_chroma_vm.sh
```

主机健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:18081/api/v2/heartbeat
```

应用配置：

```dotenv
SIMPLE_CHROMA_HOST=127.0.0.1
SIMPLE_CHROMA_PORT=18081
SIMPLE_CHROMA_SSL=false
FEATURE_SIMPLE_CHROMA=true
```

首次启动应用时会幂等创建 `indicators`、`tables`、`fields` 三个集合。Chroma 数据持久化在虚拟机的 `/home/agentdev/chatbi-chroma/data`。

## 5. 构建通用 Schema 索引（可选）

在 Chat 与 Embedding 配置都有效时执行：

```powershell
uv run chatbi-build-index
```

需要强制重建时：

```powershell
uv run chatbi-build-index --force
```

索引默认写入 `chroma_db/`。索引缺失、版本不一致、Embedding 异常或召回为空时，应用仍可启动，并明确以 `schema_mode=fallback` 使用静态 Schema；`/health/ready` 会把索引状态报告为 `fallback`。

## 6. 启动应用

```powershell
uv run chatbi
```

然后访问：

- Web 界面：<http://127.0.0.1:8000/>
- OpenAPI：<http://127.0.0.1:8000/docs>
- 存活检查：<http://127.0.0.1:8000/health/live>
- 就绪检查：<http://127.0.0.1:8000/health/ready>

也可以直接调用 API：

```powershell
$body = @{ question = "各产品线的毛利是多少？" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/queries -ContentType application/json -Body $body
```

## 示例问题

- 全部已完成订单的销售额是多少人民币？
- 最近三个月各地区的销售额是多少？
- 各产品线的毛利是多少？
- 2026 年第三季度的期间费用是多少？
- 按客户类型统计已完成订单数量。

页面会显示生成 SQL、意图识别结果、Schema 模式、Chroma 语义召回详情、结果行数和表格。查询历史只保存在当前页面内存中，刷新后清空。

## 测试与质量门禁

默认测试不需要 `.env`、真实模型 Key 或运行中的 MySQL，并显式排除 `live_model`：

```powershell
uv run pytest
```

需要验证虚拟机 Chroma 的集成用例时：

```powershell
$env:CHATBI_CHROMA_TEST = "1"
uv run pytest tests/test_simple_semantic.py
```

前端原生 JavaScript 测试需要 Node.js 20 或更高版本：

```powershell
node --test tests/web.test.mjs
```

显式连接已初始化的演示库，验证固定基准中的全部预期 SQL：

```powershell
$env:CHATBI_BENCHMARK_DB_ENABLED = "1"
$env:CHATBI_BENCHMARK_DB_HOST = "127.0.0.1"
$env:CHATBI_BENCHMARK_DB_PORT = "3306"
$env:CHATBI_BENCHMARK_DB_USER = "chatbi_reader"
$env:CHATBI_BENCHMARK_DB_PASSWORD = "<只读账号密码>"
uv run pytest tests/test_benchmark_cases.py -m integration
```

运行真实模型基准并保存报告：

```powershell
New-Item -ItemType Directory -Force reports | Out-Null
uv run python evaluator.py test_cases.json --output reports/live-model-$(Get-Date -Format yyyyMMdd-HHmmss).json
```

报告记录时间、模型配置、用例版本、Execution Accuracy、Exact Match、执行失败率和稳定失败分类。MVP 门槛为 Execution Accuracy 不低于 80%，且执行失败率不高于 10%。固定模型或配置未显式设置时，不应把该次运行作为发布验收。

## 常见故障

### `/health/live` 正常，但 `/health/ready` 返回 503

进程已启动，但模型或数据库未就绪。查看响应中的 `database`、`model` 和 `schema_index` 状态，检查 `.env`、MySQL 服务和只读账号。

### 提示 `OPENAI_API_KEY`、`DB_USER` 或 `DB_PASSWORD` 缺失

确认 `.env` 位于项目根目录且字段名与 `.env.example` 一致。应用会保持存活检查可用，但查询端点在必填配置补齐前不可用。

### 数据库拒绝访问或连接失败

重新运行数据库初始化，确认 `.env` 中 `DB_PASSWORD` 与初始化时 `CHATBI_READER_PASSWORD` 相同，并确认 `DB_HOST/DB_PORT` 指向 MySQL 8.0 实例。运行时账号必须是 `chatbi_reader`，不要使用管理员账号。

### Chroma 简单查询语义层不可用

确认 `agent-eval-dev` 正在运行，并检查 `http://127.0.0.1:18081/api/v2/heartbeat`。应用无法访问 Chroma 时，查询会回退到原有 Schema Provider，并将 `semantic_mode` 标记为 `fallback`。

### 通用 Schema 索引显示 `fallback`

这是可用的降级状态，不会阻止静态 Schema 查询。若希望启用语义召回，请检查 Embedding 配置后运行 `uv run chatbi-build-index --force`。

### SQL 被安全策略拒绝

系统只允许一条只读 `SELECT` 或 `WITH ... SELECT`，并限制表、字段、函数和结果数量。重新表述问题，使其只涉及演示业务表；不要要求写入、删除、系统元数据或文件操作。

### 查询超时

缩短时间范围、减少维度或返回列。不要通过提高超时绕过慢查询问题；评估器和产品查询都使用同一安全执行路径。

## 安全边界

- 模型生成 SQL 必须经过 `sqlglot` MySQL AST 解析、单语句只读检查、表/字段白名单、系统 Schema 拒绝、危险函数拒绝和结果上限处理。
- `DatabaseClient.execute()` 只接受安全层产生的 `SecuredQuery`，不接受原始字符串。
- MySQL 使用仅有 30 张演示业务表 `SELECT` 权限的 `chatbi_reader`，形成数据库侧第二道防线。
- 明细查询最大行数、模型超时、连接/读取/写入超时和语句执行超时均由环境配置控制。
- 日志和 API 错误不会返回 API Key、数据库密码、连接串或原始堆栈。
- 服务默认绑定 `127.0.0.1`。本项目没有认证能力，不得直接暴露到公网或不可信局域网。
- 演示数据均为虚构数据；不要把生产数据或生产凭据复制到本项目。

## MVP 非目标

当前版本不包含登录、多租户、RBAC/SSO、生产数据源接入、云部署、高并发 SLA、多轮追问、异步任务、结果缓存、复杂图表和企业级审计。第一版仅支持 MySQL 8.0；其他 SQL 方言不在兼容范围内。

## 项目结构

```text
chatbi/api              FastAPI 路由、响应模型、静态页面托管
chatbi/application      查询编排服务
chatbi/domain           领域模型、错误与 SQL 安全策略
chatbi/infrastructure   Schema Provider 与索引生命周期
chatbi/web              原生 HTML/CSS/JavaScript 单页界面
db                      MySQL Schema、种子、授权和回滚资产
scripts                 数据库初始化入口
tests                   确定性测试、集成测试和安全负例
test_cases.json         版本化 Text-to-SQL 基准集
```
