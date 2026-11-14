# ChatBI MVP

ChatBI 是一个本地、单用户的自然语言转 MySQL 查询 MVP。用户在浏览器输入业务问题，系统依次完成指标知识注入、Schema 选择、模型生成 SQL、AST 安全校验和只读数据库执行，并展示生成 SQL 与表格结果。

当前 MVP 固定使用 MySQL 8.0 和 OpenAI 兼容的 Chat API。服务默认只监听 `127.0.0.1`，不应直接暴露到公网。

查询链路在 Text-to-SQL 之前使用阿里云百炼 `qwen-plus` 识别问题复杂度。简单问题不等于单表问题：普通 JOIN 仍可走简单链路，并通过 Chroma 完成指标、表和字段召回以及 Join 路径推断。复杂问题进入三 Agent Plan-and-Execute 链路：规划 Agent 输出经过目录、Schema 和 DAG 校验的结构化计划；执行 Agent 按依赖逐步生成 SQL，并强制经过 AST 只读安全网和数据库入口；总结 Agent 只能引用真实执行结果中的具体单元格，后端反查证据值并渲染中文分析报告。百炼暂时不可用时系统回退到本地规则，并在 API 与页面中标记 `intent_source=fallback`。

复杂查询 API 返回 `plan`、`step_results`、`summary` 和 `report`，同时提供 `analysis_report`、`report_markdown`、`report_source`、`agent_mode` 和 `agent_timings_ms`。原有 `sql`、`columns` 和 `rows` 映射到计划指定的最终结果步骤；简单查询的 Agent 扩展字段为空。

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

初始目录包含 **61 个指标**，覆盖销售与利润、采购、库存、生产、质量、设备、物流、客户回款、供应商付款、预算、人力和基础经营规模。指标数量不等于表数量：事实表提供金额、数量和比率指标，维度表主要作为分析维度，仅在存在明确经营含义时提供规模类指标。运行时指标目录通过下方的管理页面维护，JSON 仅用于首次导入和固定基准。

### 指标管理

访问 `/metrics`，可搜索指标、查看口径和上下游血缘、创建草稿、提交审核、发布、驳回、放弃草稿和废弃指标。每次修改创建新版本，记录责任人、变更说明、操作人、发布时间及操作记录；发布立即生效，不支持预约发布。页面允许按历史版本填充新草稿，已有指标保持原名称。

管理目录保存在独立的 SQLite 控制库 `METRIC_REGISTRY_PATH`，默认 `.runtime/metrics.sqlite3`，与只读 MySQL 业务库分开。首次启动幂等导入 61 个指标，随后重启不会覆盖已发布口径；备份时请保留该文件，勿提交到 Git。发布检查名称/别名冲突、未知依赖和循环依赖，废弃会检查下游指标。旧生效版本在新草稿审核期间持续服务。

新查询加载已发布目录的快照，响应中的 `catalog_revision` 是口径内容 Hash。同一请求的规划、检索和执行使用同一份快照，Chroma 按内容 Hash 刷新，因此同数量的定义修改也会生效。当前单进程通过锁串行使用目录快照；多进程部署尚需共享索引刷新协调。后端未配置模型时，指标管理仍可使用。

当前管理 API 仅限本机、同源操作；操作人与审核人是单用户审计标签，不是经过认证的多人审批。此功能适用于现有本机单用户部署。

## SQL 主链路

运行时顺序固定为：意图识别 → 指标检索 → Schema/字段/Join 链接 → 动态组装 Schema、RULES、ERROR_GUARDS、角色与 Few-shot → 模型生成并识别 SQL → MySQL AST 只读安全网 → 行级权限条件注入 → 明细结果上限 → 连接池取连接并执行 → 慢查询 EXPLAIN 或异常翻译 → API 状态码映射。

简单查询的动态 Schema 不可用时会回退到受治理的静态 Schema。复杂查询在问题级和步骤级均使用 Chroma 精简检索，包含指标依赖、字段和 Join 路径；Chroma 不可用时使用本地治理检索，标记 `governed_local`，不调用旧 Embedding 接口，也不自动注入全部 30 张表。复杂查询上下文默认最多 12 张表，超限会明确要求缩小范围。`pipeline_stages` 会返回本次查询经过的阶段；本地单用户管理员模式没有范围条件，因此显示 `row_level_policy_skipped`。配置了 `UserContext.row_filters` 后，条件由 AST 注入而不是字符串拼接。

复杂查询固定执行：意图识别 → 指标与 Schema 上下文 → 规划 Agent → 计划契约与目录校验 → 执行 Agent 逐步生成和安全执行 → 总结 Agent 固定 JSON → 证据反查 → 确定性中文报告。子任务采用下述三级失败管控。

### 子任务三级失败管控

1. **局部自愈**：安全拒绝、SQL 错误和输出契约失败时，将受控错误类别、去除字面值的失败 SQL 结构和当前契约交回执行模型。所有修复仍经过 AST 安全与原有权限检查；权限拒绝、数据库断连等不适合改写 SQL 的问题不进行无效循环。默认核心任务最多额外重试 3 次，辅助任务 1 次；模型声明的重试次数只能降低，不能突破系统上限。
2. **权重分级**：规划声明 `importance=high|low` 和原因。最终结果、显式高权重任务及其全部前置依赖由系统强制提升为高权重。低权重任务最终失败后标记 `skipped/soft_skip`，依赖它的辅助步骤跳过，其余步骤继续；报告强制注明缺失的分析和依据。不会因模型把核心步骤标为低权重而静默丢失主结果。
3. **级联熔断与挂起**：高权重任务最终失败，立刻停止本次分析后续调度，未执行步骤标记 `blocked/circuit_blocked`，不调用总结模型生成假完整报告。历史记录进入 `suspended`，保存原计划、已完成产物、精确数值与日期类型、Schema 上下文、失败状态、指标版本及执行策略指纹。

当前任务按顺序执行，因此熔断可以阻止所有尚未开始的分支；不存在正在并行运行的多个子任务。取消与超时仍为协作控制，不承诺强行中断已经发出的外部调用或固定两秒响应。

前端显示步骤权重、尝试次数、自愈记录、熔断状态，并提供“从失败步骤重试”“补充条件后继续”“终止本次分析”。恢复接口：`POST /api/v1/query-jobs/{id}/resume`，请求为 `{"action":"retry|clarify|abort","clarification":"补充条件"}`。补充仅作用于未完成步骤，不自动切换指标定义或重算已完成结果；需要修改上游口径时应重新查询。恢复保持原查询 ID，复用已完成产物，不重新规划或重复查询成功步骤，默认最多人工续跑 3 轮。

断点持久化在现有 `.runtime/query-history.sqlite3` 的独立表中，无需 Redis。服务重启后挂起任务仍可查看和确认恢复；续跑超时或异常时保留原断点。等待人工确认不占用执行线程；暂停时间不计入下一轮 240 秒预算。重复提交恢复会被事务拒绝；口径、代码或权限配置发生变化时拒绝旧断点。成功或主动终止后清理断点；批量清理历史保护挂起任务，显式删除单条记录会连同断点删除。单个断点上限为 8 MB。

```dotenv
COMPLEX_CORE_MAX_RETRIES=3
COMPLEX_AUX_MAX_RETRIES=1
COMPLEX_MAX_RESUME_ROUNDS=3
```

`COMPLEX_STEP_RETRY` 仅为旧配置兼容字段，新节点预算由上述分级参数控制。当前是 SQLite 单进程实现，尚未提供 Redis 分布式断点和并发分支取消。

### 步骤间的数据依赖

`depends_on` 声明执行顺序；需要读取上游数据时，必须同时声明 `input_bindings`。例如第二步读取第一步的产品线和毛利率：

```json
{
  "depends_on": ["step_1"],
  "input_bindings": [
    {"source_step_id": "step_1", "name": "input_1", "columns": ["产品线", "毛利率"]}
  ]
}
```

绑定列必须存在于上游 `expected_columns` 输出契约。模型根据输入表结构生成 `SELECT ... FROM input_1`；后端从本次请求中取得真实结果，以 SQL AST 构造只读 CTE，并再次执行原有 SQL 安全校验。纯上游结果计算允许 `required_tables=[]`。输出列名和数据类型不符合契约时，该步骤失败，依赖步骤跳过。

成功步骤返回 `result_artifact`；下游通过 `consumed_artifacts` 标明实际消费的产物。产物仅在当前请求内使用，包含列契约、行数和完整性。截断、不完整、超出依赖行数或单元格上限的结果不能作为完整输入。空结果仍然是零行输入，不会被替换成猜测值。

规划失败时，仅在存在明确治理指标时建立带输出契约的单步回退；没有明确指标则返回可解释错误，要求明确分析指标。

```dotenv
COMPLEX_SCHEMA_MAX_TABLES=12
COMPLEX_DEPENDENCY_MAX_ROWS=200
COMPLEX_DEPENDENCY_MAX_CELLS=4000
```

### 有证据的业务建议

报告返回 `recommendation_status` 和 `recommendation_reason`。`actionable` 时，每条建议包含真实对象、问题、具体动作、责任角色、优先级、预期影响、适用前提、风险、置信度及证据。后端核对对象单元格，并要求数值证据；模型不能提交自行填写的证据值。缺少业务对象或可比指标时返回 `insufficient_data` 和空建议列表，页面显示不足原因。

总结输入包含全部返回行的描述统计，以及保留原始行号的抽样数据；抽样优先覆盖数值极值。统计只代表已经返回的数据，均值未经加权。部分失败、截断和抽样限制会由后端补充到报告。模型输出校验失败时，确定性回退只能生成有可比数据支持的复核建议，不能宣称原因或承诺收益。证据有效性校验不等于验证模型正文中所有业务判断，真实模型效果仍需单独验收。

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

本机 Windows 环境优先使用统一启动器（PowerShell 7）：

```powershell
.\scripts\chatbi.ps1 start
.\scripts\chatbi.ps1 status
.\scripts\chatbi.ps1 doctor
.\scripts\chatbi.ps1 restart
.\scripts\chatbi.ps1 stop
```

`start` 检查并启动项目 MySQL 和 VirtualBox Chroma，检查演示库 30 张表及只读账号，启动 API、等待就绪并核对源码版本，最后打开浏览器；`-NoBrowser` 可关闭自动打开。已运行且版本匹配时不会重复启动。`stop` 只停止经 Python 路径和模块命令验证的项目 API，保留数据库和虚拟机。旧 `start_secure_runtime.ps1` 入口转发到 `restart`。

默认沿用现有实例：MySQL `127.0.0.1:33307`、数据目录 `C:\chatbi-mysql-data`、虚拟机 `agent-eval-dev`、Chroma `127.0.0.1:18081`、API `8000`。可在忽略的 `.runtime/launcher.json` 覆盖 `ApiPort`、`MySqlPort`、`ChromaPort`、`VmName`、`MySqlBin`、`MySqlData`、`VBoxManage`。数据目录必须已初始化；脚本不会创建或覆盖一个未知的 MySQL 数据目录。虚拟机需已配置 Chroma 自启和端口转发。

首次安全输入两个模型 Key；Windows DPAPI 将它们连同专用只读密码加密保存到 `.runtime/launcher-secrets.clixml`，仅当前 Windows 用户可解密。启动器会优先使用当前进程环境中的凭据；`-ForgetSecrets` 删除本机加密缓存并重新输入。不要提交或跨机器复制该文件。需要初始化演示库或修复只读账号时才询问管理员凭据，管理员密码不会缓存，也不会被 API 子进程继承。API/SQL 仍使用 `chatbi_reader`。

`status`/`doctor` 不调用模型，返回依赖状态和加载版本是否匹配。`/api/v1/runtime` 返回进程加载的源码 Hash、磁盘 Hash 和 `restart_required`，首页发现旧后端时提示重启。就绪检查验证配置和数据库，不能替代模型调用验收。

通用 Python 启动方式仍然可用：

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

页面会显示生成 SQL、意图识别结果、Schema 模式、Chroma 语义召回详情、结果行数和表格。查询历史在本机持久保存，刷新后可查看原结果和报告。

## 查询进度、取消与历史

浏览器提交 `POST /api/v1/query-jobs` 后立即取得任务 ID，通过 `GET /api/v1/query-jobs/{id}/events` 的 SSE 接收真实阶段事件：意图识别、检索、规划、每步 SQL、数据库执行和总结报告。事件有连续序号，支持 `Last-Event-ID` 或 `after` 重放；断线时前端查询保存状态作为回退。当前页的任务 ID 保存在 sessionStorage，刷新后恢复跟踪，关闭页面不会自动取消后台查询。

任务由单工作线程执行，默认最多等待 8 项；队列满返回 429。同一请求使用同一指标快照。排队任务可立即取消；运行中的任务为协作取消，在当前外部调用返回后停止，并阻止后续步骤，界面显示“正在取消”。已有同步模型和数据库调用不能被强行终止，取消不承诺立即中断外部服务器操作。总时限默认 240 秒，包含排队时间；模型调用受剩余时限约束，数据库受查询超时约束。服务重启把未完成任务标为 `interrupted`，不会自动重放模型调用。

任务、问题、口径 Hash、计划、SQL、步骤结果、报告、错误和进度事件保存在 `QUERY_HISTORY_PATH`（默认 `.runtime/query-history.sqlite3`）。历史支持分页、收藏、反馈、再次运行和删除；清理全部时保留收藏与活动任务。默认保留 200 条未收藏完成记录，收藏不自动淘汰。单条响应超过 4 MB 时明确省略历史结果，保留状态并要求重跑，避免丢部分证据后展示错误报告。页面反馈选项为“有帮助 / 结果不对 / 缺少数据”。所有历史接口沿用本机同源限制，备份管理库时也请备份历史文件。

原 `POST /api/v1/queries` 同步接口兼容保留并保存成功/失败历史；浏览器使用后台任务接口。当前任务队列、锁和重启恢复以单 API 进程为边界，运行时应保持单 worker。

## 图表可视化

查询 API 追加受控的 `visualization_spec` 数组，后端按实际结果自动推荐 KPI 卡片、折线图、柱状图、堆叠图、饼图或瀑布图。复杂查询为各成功步骤单独生成图表；不能可靠识别指标或维度时保留表格。前端使用本地打包的 Apache ECharts 5.6.0，不依赖在线 CDN，也不接受模型输出 JavaScript 或任意 ECharts 配置。

时间序列按时间排序；数值型编号不当作指标；重复维度不会被擅自求和。比例、均值和单价不能堆叠或组成饼图。多指标分开显示数值轴，避免收入与毛利率共用刻度。瀑布仅对问题明确要求的“变动/增减/贡献”结果生成，从零累计变动项，保留正负方向。空值保留为缺失值；图表最多取前 100 行，抽样和结果截断均有提示。支持导出 PNG，原始表格始终保留。

示例问题：按月统计 2026 年销售收入；各产品线销售收入构成；按地区和产品线统计销售收入。

## 测试与质量门禁

仓库新增两条 GitHub Actions 工作流（提交到 GitHub 后生效）：

- `Code quality`：每次 push/PR 运行后端测试、MyPy、所有前端测试、基础密钥扫描和空白检查，保存 JUnit 结果。
- `Integration and release quality`：每天 UTC 18:20 在隔离服务中初始化演示库并运行 MySQL、Chroma、跨步骤依赖及指标版本刷新测试；显式拒绝跳过的集成测试。此 CI 容器不改变本机的虚拟机 Chroma 部署。

发布前可手动运行第二条工作流，勾选 `live_model`。先在 GitHub 配置 Secrets `CHATBI_CHAT_API_KEY`、`CHATBI_INTENT_API_KEY`；可通过 Variables `CHATBI_CHAT_BASE_URL`、`CHATBI_CHAT_MODEL` 指定模型。真实模型评测使用 API，会产生调用费用；默认的每日任务不运行真实模型。工作流和模型门禁未经远端执行时不能标为发布通过。

本地可运行相同的真实模型评测（需要完整查询配置、只读演示库和 Chroma）：

```powershell
uv run python -m scripts.evaluate_release --output reports/release-quality.json
```

Windows 可用统一入口 `./scripts/chatbi.ps1 evaluate`，复用加密凭据并检查依赖。缺少配置或服务时写出 `preflight_failed` 报告并非零退出，不计为模型验收完成。只有报告 `status=completed`（无该字段的旧成功报告亦可）并且 `passed=true` 才表示自动门禁通过；仍需人工审查业务有效性。

评测使用现有简单查询基准及 `benchmarks/complex_cases.json` 中的复杂查询，记录 SQL 结果准确率、规划成功率、步骤成功率、全部/部分成功率、证据引用准确率、建议结构与证据合格率、数据依赖消费、耗时和模型返回的 Token 统计。模型未返回完整 usage 时，Token 均值为 `null` 并标记不完整。报告记录模型配置、基准版本、目录 Hash 和可供人工复核的计划、步骤、报告。仅使用演示数据运行，评测产物可能包含查询结果，不应直接对外分享。

门禁要求简单查询 Execution Accuracy ≥80%、执行失败率 ≤10%；复杂查询规划/步骤/全部成功/结果准确率/总结模型成功率均 ≥80%，证据引用、建议结构与证据、数据依赖消费均为 100%。阈值不满足以非零状态退出；空用例、空证据或空建议不能自动通过。这些结构校验不验证全部业务因果，建议的真实业务有效率保留人工评审。

基础密钥扫描执行 `uv run python scripts/scan_secrets.py`，只输出命中文件和规则名，不输出值。它覆盖常见模型 Key、GitHub Token、AWS Key 和私钥头，仅扫描当前受版本控制及未忽略文件，不替代完整历史扫描。

默认测试不需要 `.env`、真实模型 Key 或运行中的 MySQL，并显式排除 `live_model`：

```powershell
uv run pytest
```

需要验证虚拟机 Chroma 的集成用例时：

```powershell
$env:CHATBI_CHROMA_TEST = "1"
uv run pytest tests/test_simple_semantic.py
```

P0 三步数据依赖联测使用真实 Chroma、真实演示 MySQL 和固定模型输出，验证检索、上游结果筛选、回查业务表及报告回退；它不代表真实模型质量验收：

```powershell
$env:CHATBI_P0_INTEGRATION = "1"
$env:CHATBI_P0_DB_HOST = "127.0.0.1"
$env:CHATBI_P0_DB_PORT = "33307"
$env:CHATBI_P0_DB_USER = "chatbi_reader"
$env:CHATBI_P0_DB_PASSWORD = "<只读账号密码>"
uv run pytest tests/test_p0_integration.py -q
```

测试会设置数据库会话为只读。可通过 `CHATBI_P0_DB_*` 指向现有演示实例；本地测试默认连接 `127.0.0.1:33307/chatbi_mvp`。测试不会初始化数据库或修改业务数据。

前端原生 JavaScript 测试需要 Node.js 20 或更高版本：

```powershell
node --test tests/*.test.mjs
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

当前版本不包含登录、多租户、RBAC/SSO、生产数据源接入、云部署、高并发 SLA、分布式任务队列、通用查询缓存和企业级审计。已支持连续追问、本机后台任务、断点复用、进度、取消、持久化历史及受控图表。第一版仅支持 MySQL 8.0；其他 SQL 方言不在兼容范围内。

## 连续分析与质量控制

- **结果校验 Agent**：简单查询与复杂节点执行后，独立复核指标公式、时间/筛选、维度和关联粒度；明确错误进入有上限的 SQL 修复流程。确定性规则检查部分事实表关联放大、重复维度以及收入/成本/毛利等数值关系。模型不可用时明确标记复核缺失。模型复核不代表数据正确性证明。
- **总分一致性**：规划可以通过 `reconciliation_checks` 声明同口径总计与明细的数值列；用 Decimal 加总比对。比例、均值不直接加总，缺失/截断结果标记未验证，差异超过阈值则挂起并停止生成报告。未声明的总分关系不会被标记为已验证。
- **连续追问**：成功后进入当前结果的分析上下文，最多 8 轮；界面展示继承的问题，新条件替换同类旧条件。可点击“开启新分析”清空上下文。提交“把这几轮分析整理成报告”汇总历史报告，各证据携带原查询 ID，避免不同轮次的 `step_1` 混淆；各轮金额不自动累加。
- **主动澄清**：“利润”“最近”“最好/最差”等未明确口径先进入业务澄清卡片。选择指标、时间和评价标准后恢复查询；等待期间不调用数据库、不占用工作线程，服务重启仍保留断点。
- **局部重规划**：可重试的核心失败在节点自愈耗尽后尝试调整失败节点和下游的执行策略，最多两次；有效成功前缀保留。禁止自动更改指标、业务筛选、维度、依赖及输出契约，修订须通过 DAG、目录、字段和安全校验。权限/断连等不适宜重写的问题保留人工处理入口。
- **分析控制台**：勾选“复杂分析执行前确认计划”，规划完成后暂停；可编辑未完成节点的筛选条件。预览显示失效节点及下游、可复用结果；确认令牌绑定当前断点和修改内容，条件变化或断点版本变化后必须重新预览。确认后同一任务继续。
- **图表与证据**：点击图表分类，以历史成功 SQL 为来源构建 AST 下钻，保留原始时间和筛选，再经过当前安全策略和脱敏。支持直接查询及受限的两层聚合 CTE；无法证明范围一致的跨步骤/嵌套计算拒绝自动展开，仍可点报告证据查看原始结果行。明细读取当前数据库，历史证据读取原保存结果，二者时间点可能不同。
- **中文检索与评测**：`CHINESE_EMBEDDING_PROVIDER=auto` 复用百炼配置，使用 `text-embedding-v3` 的 1024 维向量；失败回退本地词法向量并标记。远程与本地使用独立集合，召回候选经过中文 BM25 与向量得分重排，精确治理指标优先。[检索与评测看板](http://127.0.0.1:8000/observability)展示业务域/用例正确率、耗时、聊天/意图/向量 Token、失败分类、检索候选得分和源码版本。缺少报告或报告过期会明确提示，不补造统计。

使用 `scripts/chatbi.ps1 evaluate -NoBrowser` 运行真实模型验收；报告位于 `reports/release-quality.json`。运行中源码发生变化会使验收失败。自动化回归不等于业务人员对建议的有效性验收。

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
