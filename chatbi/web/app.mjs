import { disposeCharts, mountCharts, renderVisualizations } from "./charts.mjs";
import { renderValidation, renderPlanConsole, collectEdits, renderEvidenceDialog } from "./analysis-tools.mjs";
import { historyApi, JobClient, renderFeedback, renderPersistentHistory, renderSuspension } from "./query-jobs.mjs";
import { mountWorkspaceNavigation } from "./workspace-navigation.mjs";
import { renderChatTurn, renderChatReply, renderTurnProgress, readConversationIds } from "./conversation.mjs";

const ERROR_MESSAGES = {
  input_invalid: "请输入有效的业务问题。",
  input_too_long: "问题过长，请缩短后重试。",
  model_unavailable: "模型服务暂时不可用，请稍后重试。",
  model_output_invalid: "模型没有生成可用查询，请换一种方式描述问题。",
  schema_unavailable: "数据库结构信息暂时不可用。",
  sql_rejected: "查询未通过只读安全检查，已阻止执行。请缩小范围或重新表述。",
  database_unavailable: "演示数据库暂时不可用，请稍后重试。",
  database_timeout: "查询超时，请缩小时间或数据范围。",
  database_query_failed: "查询执行失败，请调整问题后重试。",
  internal_error: "系统暂时无法处理该问题，请稍后重试。",
};

const PIPELINE_LABELS = {
  intent_recognition: "意图识别",
  indicator_retrieval: "指标召回",
  schema_linking: "Schema 链接",
  dynamic_prompt: "动态 Prompt",
  sql_generation_and_recognition: "SQL 生成",
  sql_ast_safety: "AST 安全检查",
  row_level_policy_applied: "行级权限已注入",
  row_level_policy_skipped: "行级权限无需注入",
  result_limit_applied: "结果上限已应用",
  result_limit_not_required: "结果上限无需应用",
  connection_pool_execution: "连接池执行",
  slow_query_explain: "慢查询 EXPLAIN",
  slow_query_explain_skipped: "无需 EXPLAIN",
  complex_planning_agent: "规划 Agent",
  complex_execution_agent: "执行 Agent",
  complex_summary_agent: "总结 Agent",
  evidence_validation: "证据校验",
  deterministic_report_rendering: "报告渲染",
};

const ICONS = {
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>',
  alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 9v4m0 4h.01M10.3 4.5 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.5a2 2 0 0 0-3.4 0Z"/></svg>',
};

export function validateQuestion(value, maxLength = 1000) {
  const question = String(value ?? "").trim();
  if (!question) return { ok: false, message: ERROR_MESSAGES.input_invalid };
  if (question.length > maxLength) return { ok: false, message: ERROR_MESSAGES.input_too_long };
  return { ok: true, question };
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function messageForError(code, fallback) {
  return ERROR_MESSAGES[code] ?? fallback ?? ERROR_MESSAGES.internal_error;
}

function formatDuration(timings) {
  const duration = Number(timings?.total);
  if (!Number.isFinite(duration)) return "-";
  return duration >= 1000 ? `${(duration / 1000).toFixed(2)} 秒` : `${Math.round(duration)} ms`;
}

function displaySchemaMode(data) {
  if (data.semantic_mode === "governed_local") return "本地治理精简 Schema";
  if (data.semantic_mode === "chroma") return "Chroma 动态 Schema";
  if (data.schema_mode === "semantic") return "语义 Schema";
  return "全量 Schema 回退";
}

function renderEvidence(items) {
  return (items ?? []).map((item) => `<button type="button" class="evidence-chip" data-evidence-step="${escapeHtml(item.step_id)}" data-evidence-row="${Number(item.row_index)}" data-evidence-column="${escapeHtml(item.column)}" data-evidence-query="${escapeHtml(item.query_id ?? "")}" title="查看原始证据：${escapeHtml(item.step_name)}">${escapeHtml(item.step_id)} · 第 ${Number(item.row_index) + 1} 行 · ${escapeHtml(item.column)}：${escapeHtml(item.value)}</button>`).join("");
}

export function renderAnalysisReport(report) {
  if (!report) return "";
  const findings = (report.findings ?? []).map((item) => `<article class="report-finding">
    <h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.statement)}</p><div class="evidence-list">${renderEvidence(item.evidence)}</div>
  </article>`).join("");
  const caveats = (report.caveats ?? []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const levels = { high: "高", medium: "中", low: "低" };
  const recommendations = (report.recommendations ?? []).map((item) => `<article class="report-recommendation">
    <div class="action-meta"><span>对象：${escapeHtml(item.target)}</span><span>责任角色：${escapeHtml(item.owner_role)}</span><span>优先级：${escapeHtml(levels[item.priority] ?? "待确认")}</span><span>置信度：${escapeHtml(levels[item.confidence] ?? "待确认")}</span></div>
    <strong>${escapeHtml(item.action)}</strong><p>${escapeHtml(item.problem)}</p><p>${escapeHtml(item.rationale)}</p>
    <dl class="action-details"><dt>预期影响</dt><dd>${escapeHtml(item.expected_impact)}</dd><dt>适用前提</dt><dd>${escapeHtml(item.precondition)}</dd><dt>风险</dt><dd>${escapeHtml(item.risk)}</dd></dl>
    <div class="evidence-list">${renderEvidence(item.evidence)}</div>
  </article>`).join("");
  return `<section class="analysis-report">
    <div class="report-header"><div><span>中文分析报告</span><h3>${escapeHtml(report.title)}</h3></div><span class="report-source">${report.source === "summary_agent" ? "总结 Agent" : report.source === "conversation_report" ? "历史报告汇总" : "安全模板"}</span></div>
    <div class="report-summary"><h4>执行摘要</h4><p>${escapeHtml(report.executive_summary)}</p></div>
    ${findings ? `<div class="report-section"><h4>核心发现</h4>${findings}</div>` : ""}
    ${caveats ? `<div class="report-section"><h4>局限</h4><ul>${caveats}</ul></div>` : ""}
    <div class="report-section"><h4>业务建议</h4><p>${escapeHtml(report.recommendation_reason ?? "建议基于已校验证据生成。")}</p>${recommendations || '<p class="notice">数据不足，暂不生成具体业务行动项。请补充可比指标、同期或目标数据。</p>'}</div>
    <button class="copy-report text-button" type="button" data-copy-report>复制报告</button>
  </section>`;
}

export function renderAgentWorkflow(data) {
  if (data.query_type !== "complex") return "";
  const active = data.agent_mode === "plan_execute" && data.plan;
  if (!active) {
    return `<section class="agent-workflow legacy-agent-warning" aria-label="复杂查询执行状态">
      <div class="workflow-titlebar">
        <div><span class="workflow-eyebrow">COMPLEX QUERY ORCHESTRATION</span><h3>复杂分析执行轨迹</h3></div>
        <span class="workflow-status warning">未进入三 Agent</span>
      </div>
      <div class="legacy-warning-body">
        <span class="legacy-warning-icon">!</span>
        <div><strong>当前响应仍来自旧版单 SQL 链路</strong><p>后端没有返回规划、分步执行和总结报告数据，因此本次结果不会出现业务建议。请重启新版本服务后重新提交问题。</p></div>
      </div>
      <div class="agent-flow agent-flow-disabled">
        <div class="agent-node"><b>1</b><span><strong>规划 Agent</strong><small>未执行</small></span></div><i></i>
        <div class="agent-node"><b>2</b><span><strong>执行 Agent</strong><small>未执行</small></span></div><i></i>
        <div class="agent-node"><b>3</b><span><strong>总结 Agent</strong><small>未执行</small></span></div>
      </div>
    </section>`;
  }
  const states = { succeeded: "成功", failed: "失败", skipped: "降级跳过", blocked: "熔断阻止" };
  const resultById = new Map((data.step_results ?? []).map((item) => [item.step_id, item]));
  const succeeded = (data.step_results ?? []).filter((item) => item.status === "succeeded").length;
  const total = (data.plan.steps ?? []).length;
  const timings = data.agent_timings_ms ?? {};
  const planReview = data.agent_status === "awaiting_plan";
  const paused = planReview || data.agent_status === "suspended";
  const timing = (name) => Number.isFinite(Number(timings[name])) ? `${Math.round(Number(timings[name]))} ms` : "已完成";
  const overallStatus = planReview ? "等待计划确认" : data.agent_status === "suspended" ? "已挂起" : data.agent_status === "partial" ? "部分完成" : "执行成功";
  const reportStatus = data.analysis_report ? (data.report_source === "summary_agent" ? "报告已生成" : "安全模板报告") : "等待报告";
  const steps = (data.plan.steps ?? []).map((step, index) => {
    const result = resultById.get(step.step_id) ?? {};
    const columns = (result.columns ?? []).join("、");
    return `<article class="agent-step ${escapeHtml(result.status ?? "pending")}">
      <div class="step-marker">${index + 1}</div><div class="step-content">
        <div class="step-heading"><div><span>${escapeHtml(step.step_id)}</span><h4>${escapeHtml(step.step_name)}</h4></div><span class="step-status">${escapeHtml(states[result.status] ?? "待执行")}</span></div>
        <p>${escapeHtml(step.objective)}</p>
        <p class="step-control-meta"><span class="importance-${step.importance === "high" ? "high" : "low"}">${step.importance === "high" ? "高权重 · 核心" : "低权重 · 辅助"}</span><span>尝试 ${Number(result.attempts ?? 0)} 次</span>${result.recovery_action === "reused" ? "<span>复用断点结果</span>" : result.recovery_action === "repaired" ? "<span>局部自愈成功</span>" : ""}</p>
        ${(result.attempt_errors ?? []).length ? `<details><summary>查看局部修复记录</summary><ul>${result.attempt_errors.map((error) => `<li>第 ${Number(error.attempt)} 次：${escapeHtml(error.message)}</li>`).join("")}</ul></details>` : ""}
        <div class="step-meta"><span>${Number(result.row_count ?? 0)} 行</span><span>${Math.round(Number(result.duration_ms ?? 0))} ms</span>${columns ? `<span>${escapeHtml(columns)}</span>` : ""}</div>
        ${(step.input_bindings ?? []).length ? `<p class="step-dependencies">上游数据：${step.input_bindings.map((binding) => `${escapeHtml(binding.source_step_id)} → ${escapeHtml(binding.name)}（${escapeHtml(binding.columns.join("、"))}）`).join("；")} · 已消费 ${Number(result.consumed_artifacts?.length ?? 0)} 个结果产物</p>` : ""}
        ${result.schema_mode ? `<p class="step-dependencies">本步骤 Schema：${result.schema_mode === "chroma" ? "Chroma 动态召回" : "本地治理精简回退"} · ${Number(result.schema_tables?.length ?? 0)} 张表</p>` : ""}
        ${result.error_message ? `<p class="step-error">${escapeHtml(result.error_message)}</p>` : ""}
        ${result.sql ? `<details><summary>查看本步骤 SQL</summary><pre class="sql"><code>${escapeHtml(result.sql)}</code></pre></details>` : ""}
      </div>
    </article>`;
  }).join("");
  return `<section class="agent-workflow active-agent-workflow" aria-label="复杂查询执行轨迹">
    <div class="workflow-titlebar">
      <div><span class="workflow-eyebrow">PLAN AND EXECUTE</span><h3>复杂分析执行轨迹</h3><p>问题经过规划、分步安全执行和证据化总结</p></div>
      <span class="workflow-status ${["partial", "suspended"].includes(data.agent_status) ? "partial" : "success"}">${overallStatus}</span>
    </div>
    <div class="agent-flow">
      <div class="agent-node completed"><b>1</b><span><strong>规划 Agent</strong><small>${escapeHtml(timing("planning"))} · ${total} 个步骤</small></span><em>已完成</em></div><i></i>
      <div class="agent-node ${paused ? "" : "completed"}"><b>2</b><span><strong>执行 Agent</strong><small>${planReview ? "等待计划确认，尚未执行" : `${escapeHtml(timing("execution"))} · ${succeeded}/${total} 成功`}</small></span><em>${planReview ? "未开始" : data.agent_status === "suspended" ? "已熔断" : "已完成"}</em></div><i></i>
      <div class="agent-node ${paused ? "" : "completed"}"><b>3</b><span><strong>总结 Agent</strong><small>${planReview ? "等待计划确认" : paused ? "等待核心数据恢复" : `${escapeHtml(timing("summary"))} · ${escapeHtml(reportStatus)}`}</small></span><em>${paused ? "未开始" : "已完成"}</em></div>
    </div>
    <div class="plan-overview"><span>分析目标</span><h3>${escapeHtml(data.plan.analysis_goal)}</h3><p>${escapeHtml(data.plan.final_deliverable)}</p></div>
    <div class="steps-title"><span>执行计划</span><strong>${succeeded}/${total} 个步骤成功</strong></div>
    <div class="agent-steps">${steps}</div>
  </section>`;
}

export function renderPipeline(data) {
  if (!data.pipeline_stages?.length) return "";
  const stages = data.pipeline_stages.map((stage, index) => `<li><span>${index + 1}</span><strong>${escapeHtml(PIPELINE_LABELS[stage] ?? stage)}</strong><i>${ICONS.check}</i></li>`).join("");
  if (data.query_type === "complex") {
    return `<section class="runtime-pipeline"><div class="runtime-pipeline-heading"><div><span>RUNTIME GUARDRAILS</span><h3>完整安全执行链路</h3></div><strong>${data.pipeline_stages.length} 个阶段已通过</strong></div><ol>${stages}</ol></section>`;
  }
  return `<details class="semantic-details"><summary>SQL 执行链路</summary><div class="details-body"><div class="pipeline-list">${data.pipeline_stages.map((stage) => `<span class="pipeline-step">${escapeHtml(PIPELINE_LABELS[stage] ?? stage)}</span>`).join("")}</div><p><strong>行级权限：</strong>${data.row_level_applied ? "已通过 AST 注入" : "本地管理员模式，无需注入"}</p></div></details>`;
}

export function renderResult(data, view = "all") {
  if (data.query_type === "non_query") {
    return `<div class="result-content conversation-response"><div class="panel-header result-heading"><div><h2>对话回复</h2><p>本轮未进入数据分析</p></div><span class="result-state">普通对话</span></div><p class="conversation-message">${escapeHtml(data.message ?? "请描述你想查询的业务指标或分析目标。")}</p><p class="conversation-note">${escapeHtml(data.intent_reason ?? "")}</p></div>`;
  }
  const sql = escapeHtml(data.sql);
  const queryType = data.query_type === "complex" ? "复杂查询" : "简单查询";
  const confidence = Number.isFinite(Number(data.intent_confidence))
    ? `${Math.round(Number(data.intent_confidence) * 100)}%`
    : "-";
  const intent = data.query_type
    ? `<p class="intent-notice"><strong>${queryType}</strong><span>${escapeHtml(data.intent_source)} · ${confidence}</span><span>${escapeHtml(data.intent_reason ?? "")}</span></p>`
    : "";

  const recalled = data.semantic_mode === "chroma"
    ? `<details class="semantic-details"><summary>语义召回详情</summary>
        <div class="details-body">
          <p><strong>指标：</strong>${escapeHtml((data.recalled_indicators ?? []).join("、") || "未命中")}</p>
          <p><strong>数据表：</strong>${escapeHtml((data.recalled_tables ?? []).join("、") || "未命中")}</p>
          <p><strong>字段：</strong>${escapeHtml((data.recalled_fields ?? []).join("、") || "由指标公式确定")}</p>
          <p><strong>Join：</strong>${escapeHtml((data.inferred_joins ?? []).join("；") || "无需联表")}</p>
        </div>
      </details>`
    : "";

  const pipeline = renderPipeline(data);

  let body = "";
  if (!data.rows?.length) {
    body = '<p class="notice">查询成功，但没有匹配数据。</p>';
  } else {
    const headers = (data.columns ?? []).map((column) => `<th title="${escapeHtml(column)}">${escapeHtml(column)}</th>`).join("");
    const rows = data.rows.map((row) => `<tr>${row.map((cell) => `<td title="${escapeHtml(cell)}">${escapeHtml(cell)}</td>`).join("")}</tr>`).join("");
    body = `<div class="table-wrap"><table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const truncated = data.truncated
    ? `<p class="notice">结果达到服务端上限，当前仅展示前 ${Number(data.row_count)} 行。</p>`
    : "";
  const agentWorkflow = renderAgentWorkflow(data);
  const analysisReport = renderAnalysisReport(data.analysis_report);
  const reportMissing = data.query_type === "complex" && !data.analysis_report
    ? `<section class="report-missing"><strong>分析报告尚未生成</strong><p>${data.agent_mode === "plan_execute" ? "总结 Agent 未返回可展示报告。" : "当前后端响应未包含三 Agent 报告字段，重启新版本服务后重新查询即可显示业务建议。"}</p></section>`
    : "";
  const resultSubtitle = data.query_type === "complex"
    ? (data.agent_mode === "plan_execute" ? "三 Agent 已完成规划、执行与总结" : "已返回查询结果，但未进入三 Agent 分析链路")
    : "SQL 已通过安全检查并成功执行";

  if (view === "report") return `${analysisReport || `<section class="query-brief"><span class="eyebrow">查询摘要</span><h3>${data.query_type === "complex" ? "业务分析结果" : "数据查询结果"}</h3><p>${escapeHtml(data.summary || `本次查询返回 ${Number(data.row_count ?? 0)} 行数据，可在“数据图表”中查看完整结果。`)}</p></section>`}${renderValidation(data)}`;
  if (view === "data") return `<div class="artifact-data-meta"><span>${Number(data.row_count ?? 0)} 行数据</span><span>${escapeHtml(formatDuration(data.timings_ms))}</span></div><div class="visualizations">${renderVisualizations(data.visualization_spec)}</div>${truncated}${body}`;
  if (view === "process") return `${intent}${agentWorkflow}<div class="diagnostic-grid">${recalled}${pipeline}</div><details class="context-history"><summary>本轮分析上下文与口径</summary><ol>${(data.analysis_context?.turns ?? []).map((turn) => `<li>${escapeHtml(turn.question)}</li>`).join("")}</ol><p>${escapeHtml(displaySchemaMode(data))} · 总耗时 ${escapeHtml(formatDuration(data.timings_ms))}</p></details>`;
  if (view === "sql") {
    const queries = data.step_results?.filter((step) => step.sql) ?? [];
    const blocks = queries.length ? queries.map((step) => ({ title: `${step.step_id} · ${step.step_name}`, sql: step.sql })) : data.sql ? [{ title: "只读查询 · MySQL", sql: data.sql }] : [];
    return blocks.map((item) => `<section class="sql-module"><div class="sql-header"><span>${escapeHtml(item.title)}</span><button class="copy-sql" type="button" data-copy-sql>复制 SQL</button></div><pre class="sql"><code>${escapeHtml(item.sql)}</code></pre></section>`).join("") || '<p class="notice">本轮未生成新的 SQL。</p>';
  }

  return `<div class="result-content">
    <div class="panel-header result-heading">
      <div class="result-title"><span>${ICONS.check}</span><div><h2>分析完成</h2><p>${resultSubtitle}</p></div></div>
      <span class="result-state">查询成功</span>
    </div>
    ${agentWorkflow}
    ${reportMissing}
    <div class="result-kpis">
      <div class="result-kpi"><span>返回数据</span><strong>${Number(data.row_count ?? 0)} 行</strong></div>
      <div class="result-kpi"><span>查询类型</span><strong>${queryType}</strong></div>
      <div class="result-kpi"><span>Schema 模式</span><strong>${escapeHtml(displaySchemaMode(data))}</strong></div>
      <div class="result-kpi"><span>总耗时</span><strong>${escapeHtml(formatDuration(data.timings_ms))}</strong></div>
    </div>
    ${intent}
    ${analysisReport}
    ${renderValidation(data)}
    <div class="visualizations">${renderVisualizations(data.visualization_spec)}</div>
    <div class="diagnostic-grid">${recalled}${pipeline}</div>
    <div class="sql-module">
      <div class="sql-header"><span>GENERATED SQL · MYSQL</span><button class="copy-sql" type="button" data-copy-sql>复制 SQL</button></div>
      <pre class="sql"><code>${sql}</code></pre>
    </div>
    ${truncated}${body}
  </div>`;
}

export function renderFailure(message, question) {
  return `<div class="error-state">
    <span class="empty-visual">${ICONS.alert}</span>
    <h2>未能完成查询</h2>
    <p class="error-message">${escapeHtml(message)}</p>
    <button class="retry" type="button" data-retry="${escapeHtml(question)}">重新查询</button>
  </div>`;
}

export function renderHistoryItems(items) {
  if (!items.length) return '<div class="history-empty">完成一次查询后，记录会显示在这里。</div>';
  return items.map((item, index) => `
    <article class="history-item">
      <div class="history-main">
        <span class="history-index">${String(index + 1).padStart(2, "0")}</span>
        <div class="history-copy">
          <strong class="history-question" title="${escapeHtml(item.question)}">${escapeHtml(item.question)}</strong>
          <div class="history-meta"><span class="status-badge ${item.status === "成功" ? "success" : "failure"}">${escapeHtml(item.status)}</span><span>${escapeHtml(item.summary)}</span>${item.duration ? `<span>${escapeHtml(item.duration)}</span>` : ""}</div>
        </div>
        <div class="history-actions">${item.retry ? `<button class="retry" type="button" data-retry="${escapeHtml(item.question)}">重试</button>` : ""}</div>
      </div>
      ${item.sql ? `<details class="history-details"><summary>查看生成 SQL</summary><code class="history-sql">${escapeHtml(item.sql)}</code></details>` : ""}
    </article>`).join("");
}

export class HistoryStore {
  constructor(limit = 8) { this.limit = limit; this.items = []; }
  add(item) { this.items.unshift(item); this.items = this.items.slice(0, this.limit); return this.items; }
  clear() { this.items = []; return this.items; }
}

export class QueryController {
  constructor({
    readQuestion,
    setBusy,
    onValidationError,
    onSuccess,
    onFailure,
    request = requestQuery,
    maxLength = 1000,
  }) {
    this.readQuestion = readQuestion;
    this.setBusy = setBusy;
    this.onValidationError = onValidationError;
    this.onSuccess = onSuccess;
    this.onFailure = onFailure;
    this.request = request;
    this.maxLength = maxLength;
    this.busy = false;
  }

  async run() {
    if (this.busy) return false;
    const validation = validateQuestion(this.readQuestion(), this.maxLength);
    if (!validation.ok) {
      this.onValidationError(validation.message);
      return false;
    }
    this.onValidationError("");
    this.busy = true;
    this.setBusy(true);
    try {
      const data = await this.request(validation.question);
      this.onSuccess(data, validation.question);
      return true;
    } catch (error) {
      this.onFailure(error, validation.question);
      return false;
    } finally {
      this.busy = false;
      this.setBusy(false);
    }
  }
}

export async function requestQuery(question, fetchImpl = fetch, timeoutMs = 45000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl("/api/v1/queries", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(messageForError(data.code, data.message)), { data });
    return data;
  } finally {
    clearTimeout(timer);
  }
}

export async function requestHealth(fetchImpl = fetch) {
  const response = await fetchImpl("/health/ready", { headers: { accept: "application/json" } });
  const data = await response.json();
  return { ok: response.ok, data };
}

if (typeof document !== "undefined") {
  const refreshNavigation = mountWorkspaceNavigation();
  const form = document.querySelector("#query-form");
  const questionInput = document.querySelector("#question");
  const submit = document.querySelector("#submit");
  const status = document.querySelector("#status");
  const charCount = document.querySelector("#char-count");
  const inputError = document.querySelector("#input-error");
  const timeline = document.querySelector("#current-result");
  const messages = document.querySelector("#chat-messages");
  const welcome = document.querySelector("#chat-welcome");
  const jumpButton = document.querySelector("#jump-to-latest");
  const artifact = document.querySelector("#report-workspace");
  const artifactContent = document.querySelector("#artifact-content");
  const turns = new Map();
  let activeTurn = null, resultPanel = null, progressRoot = null;
  let restoring = false, turnSequence = 0;
  let conversationIds = readConversationIds(sessionStorage);
  const historyRoot = document.querySelector("#history");
  const healthRoot = document.querySelector("#service-health");
  const clearHistory = document.querySelector("#clear-history");
  const history = new HistoryStore();
  const cancelButton = document.querySelector("#cancel-query");
  const favoriteFilter = document.querySelector("#history-favorites");
  let displayedResult = null;
  let artifactTab = "report";
  const nearBottom = () => timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 100;
  const followLatest = () => { timeline.scrollTo({ top: timeline.scrollHeight, behavior: "instant" }); };
  const revealTurn = (turn) => { timeline.scrollTo({ top: turn.root.getBoundingClientRect().top - timeline.getBoundingClientRect().top + timeline.scrollTop - 20, behavior: "instant" }); };
  const remember = (id) => {
    if (!id) return;
    if (!conversationIds.includes(id)) conversationIds.push(id);
    conversationIds = conversationIds.slice(-20);
    sessionStorage.setItem("chatbi-conversation", JSON.stringify(conversationIds));
  };
  const selectTurn = (turn) => {
    activeTurn = turn;
    resultPanel = turn.root.querySelector(".turn-result");
    progressRoot = turn.root.querySelector(".turn-progress");
  };
  const beginTurn = (question, id = null) => {
    const existing = id && [...turns.values()].find((turn) => turn.id === id);
    if (existing) { selectTurn(existing); return existing; }
    welcome.hidden = true;
    const root = document.createElement("article");
    const key = `turn-${++turnSequence}`;
    root.className = "chat-turn"; root.dataset.turnKey = key;
    root.innerHTML = renderChatTurn(question);
    messages.append(root);
    const turn = { key, question, id, root, data: null };
    turns.set(key, turn); selectTurn(turn);
    if (!restoring) revealTurn(turn);
    return turn;
  };
  const closeArtifact = () => {
    disposeCharts(); artifact.hidden = true;
    document.querySelector("#analysis-view").classList.remove("has-artifact", "artifact-expanded");
    document.querySelector("#expand-report").setAttribute("aria-pressed", "false");
    document.querySelector("#expand-report").textContent = "展开";
  };
  const openArtifact = (turn, tab = null) => {
    if (!turn?.data || turn.data.query_type === "non_query") return;
    displayedResult = turn.data;
    artifactTab = tab ?? (displayedResult.analysis_report ? "report" : "data");
    artifact.hidden = false; artifact.dataset.queryId = turn.id ?? ""; artifact.dataset.turnKey = turn.key;
    document.querySelector("#analysis-view").classList.add("has-artifact");
    document.querySelector("#artifact-title").textContent = displayedResult.analysis_report?.title || "查询结果与分析";
    document.querySelector("#artifact-question").textContent = turn.question;
    document.querySelectorAll("[data-artifact-tab]").forEach((button) => {
      const selected = button.dataset.artifactTab === artifactTab;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    });
    artifactContent.setAttribute("aria-labelledby", `tab-${artifactTab}`);
    disposeCharts();
    artifactContent.innerHTML = renderResult(displayedResult, artifactTab);
    artifactContent.scrollTop = 0;
    if (artifactTab === "data") mountCharts(artifactContent, displayedResult.visualization_spec ?? [], globalThis.echarts, async (spec, value) => {
      if (!turn.id) return;
      try {
        const detail = await historyApi(`/api/v1/history/${turn.id}/drilldown`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ step_id: spec.source_step_id, dimension: spec.dimension, value }) });
        document.querySelector(".evidence-dialog")?.remove(); document.body.insertAdjacentHTML("beforeend", renderEvidenceDialog(detail));
      } catch (error) { inputError.textContent = error.message; }
    });
  };
  let parentQueryId = sessionStorage.getItem("chatbi-parent-query") || null;
  const contextRoot = document.querySelector("#analysis-context");
  const updateContext = () => {
    contextRoot.innerHTML = parentQueryId ? '<span class="context-indicator"></span><strong>连续分析</strong><span>沿用已有指标与筛选，可直接追问</span>' : '<span class="context-indicator"></span><span>新对话 · 从一个业务问题开始</span>';
  };
  updateContext();
  let historyOffset = 0;
  const loadHistory = async () => {
    try {
      const data = await historyApi(`?limit=30&offset=${historyOffset}&favorites=${favoriteFilter.checked}`);
      historyRoot.innerHTML = renderPersistentHistory(data.items);
      document.querySelector("#history-prev").disabled = historyOffset === 0;
      document.querySelector("#history-next").disabled = data.items.length < 30;
    } catch { historyRoot.textContent = "无法读取历史记录，请确认已启动新版服务。"; }
  };
  const jobs = new JobClient({
    onProgress: (events) => {
      if (!progressRoot) return;
      const follow = nearBottom(), expanded = progressRoot.querySelector("details")?.open;
      progressRoot.innerHTML = renderTurnProgress(events, expanded);
      if (activeTurn) activeTurn.root.querySelector(".turn-state").textContent = events.at(-1)?.detail?.message ?? "正在处理";
      if (follow) followLatest();
    },
    onJob: (id) => {
      sessionStorage.setItem("chatbi-active-job", id);
      if (activeTurn) { activeTurn.id = id; activeTurn.root.dataset.queryId = id; remember(id); }
      loadHistory();
    },
  });

  fetch("/api/v1/metrics").then((response) => response.ok ? response.json() : null).then((data) => {
    if (Number.isInteger(data?.active_count)) document.querySelectorAll("[data-indicator-count]").forEach((node) => { node.textContent = data.active_count; });
  }).catch(() => {});

  const renderHistory = () => { loadHistory(); };
  const updateCharCount = () => {
    charCount.textContent = `${questionInput.value.length} / 1000`;
    questionInput.style.height = "auto";
    questionInput.style.height = `${Math.min(questionInput.scrollHeight, 132)}px`;
  };

  const setBusy = (value) => {
    submit.disabled = value;
    cancelButton.hidden = !value;
    cancelButton.disabled = false;
    submit.querySelector("span").textContent = value ? "处理中" : "发送";
    document.querySelectorAll("[data-new-analysis]").forEach((button) => { button.disabled = value; });
    status.innerHTML = value ? '<i class="ready-dot"></i>Agent 正在处理，可先编辑下一条问题' : '<i class="ready-dot"></i>准备就绪';
    const pending = [...turns.values()].findLast((turn) => turn.data?.suspension);
    const pendingRoot = document.querySelector("#pending-action");
    pendingRoot.hidden = value || !pending;
    if (!value && pending) {
      const planReview = pending.data.suspension.kind === "plan_review";
      status.textContent = planReview ? "等待计划确认 · 尚未执行" : "等待你补充或确认条件";
      pendingRoot.innerHTML = `<span>${planReview ? "等待计划确认，尚未执行" : "有分析等待你确认"}</span>${planReview ? `<button class="primary" type="button" data-analysis-resume="${escapeHtml(pending.id)}" data-resume-action="retry">确认并执行</button>` : `<button class="text-button" type="button" data-locate-pending="${pending.key}">返回确认位置 ↑</button>`}`;
    }
  };

  const controller = new QueryController({
    readQuestion: () => questionInput.value,
    setBusy,
    onValidationError: (message) => { inputError.textContent = message; },
    request: (question) => {
      beginTurn(question);
      questionInput.value = ""; updateCharCount();
      progressRoot.innerHTML = renderTurnProgress([{ stage: "queued", detail: { message: "正在提交问题" } }]);
      return jobs.run(question, { parent_query_id: parentQueryId, review_plan: document.querySelector("#review-plan").checked });
    },
    onSuccess: (data, question) => {
      const turn = data.history_id && [...turns.values()].find((item) => item.id === data.history_id);
      if (turn) selectTurn(turn);
      else if (!activeTurn || activeTurn.data || (activeTurn.id && activeTurn.id !== data.history_id)) beginTurn(question, data.history_id);
      activeTurn.data = data; activeTurn.id = data.history_id ?? activeTurn.id;
      activeTurn.root.dataset.queryId = activeTurn.id ?? "";
      remember(activeTurn.id);
      activeTurn.root.querySelector(".turn-state").textContent = data.suspension ? "等待确认" : data.query_type === "non_query" ? "普通对话" : "已完成";
      if (data.suspension) {
        resultPanel.innerHTML = renderSuspension(data) + renderPlanConsole(data) + (data.suspension.plan ? `<details class="chat-workflow"><summary>查看规划与执行状态</summary>${renderAgentWorkflow({
          query_type: "complex", agent_mode: "plan_execute", agent_status: data.suspension.kind === "plan_review" ? "awaiting_plan" : "suspended", plan: data.suspension.plan,
          step_results: data.suspension.step_results ?? [], agent_timings_ms: {},
        })}</details>` : "");
        if (data.suspension.options?.includes("replan")) resultPanel.querySelector(".suspension-actions")?.insertAdjacentHTML("beforeend", `<button class="text-button" data-analysis-resume="${escapeHtml(data.history_id)}" data-resume-action="replan">局部重规划后继续</button>`);
        resultPanel.classList.add("visible");
        sessionStorage.removeItem("chatbi-active-job");
        if (!restoring) { renderHistory(); revealTurn(activeTurn); }
        return;
      }
      resultPanel.innerHTML = renderChatReply(data, activeTurn.key);
      if (!restoring && data.history_id && data.query_type !== "non_query") { parentQueryId = data.history_id; sessionStorage.setItem("chatbi-parent-query", parentQueryId); updateContext(); }
      resultPanel.classList.add("visible");
      if (data.history_id && data.query_type !== "non_query") resultPanel.insertAdjacentHTML("beforeend", renderFeedback(data.history_id));
      sessionStorage.removeItem("chatbi-active-job");
      history.add({ question, status: "成功", summary: data.query_type === "non_query" ? "普通对话" : `${data.row_count} 行 · ${data.query_type === "complex" ? "复杂查询" : "简单查询"}`, duration: formatDuration(data.timings_ms), sql: data.sql, retry: false });
      if (!restoring) {
        renderHistory();
        if (data.query_type !== "non_query" && window.matchMedia("(min-width: 1221px)").matches) openArtifact(activeTurn);
        revealTurn(activeTurn);
      }
    },
    onFailure: (error, question) => {
      sessionStorage.removeItem("chatbi-active-job");
      if (!activeTurn) beginTurn(question);
      activeTurn.root.querySelector(".turn-state").textContent = "未完成";
      const message = error.name === "AbortError" ? "请求超时，请重试。" : messageForError(error.data?.code, error.message);
      resultPanel.innerHTML = renderFailure(message, question);
      resultPanel.classList.add("visible");
      history.add({ question, status: "失败", summary: message, sql: "", retry: true });
      renderHistory();
      revealTurn(activeTurn);
    },
  });

  form.addEventListener("submit", (event) => { event.preventDefault(); controller.run(); });
  questionInput.addEventListener("input", updateCharCount);
  let composingQuestion = false;
  questionInput.addEventListener("compositionstart", () => { composingQuestion = true; });
  questionInput.addEventListener("compositionend", () => { composingQuestion = false; });
  questionInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || composingQuestion || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!event.repeat) controller.run();
  });
  document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => {
    if (controller.busy) return;
    parentQueryId = null; sessionStorage.removeItem("chatbi-parent-query"); updateContext();
    questionInput.value = button.dataset.example;
    inputError.textContent = "";
    updateCharCount();
    questionInput.focus();
  }));
  cancelButton.addEventListener("click", async () => {
    cancelButton.disabled = true;
    try { const data = await jobs.cancel(); status.textContent = data.accepted ? "正在取消，当前调用返回后停止" : "查询已结束"; }
    catch (error) { inputError.textContent = error.message; cancelButton.disabled = false; }
  });
  clearHistory.addEventListener("click", async () => { try { await historyApi("", { method: "DELETE" }); historyOffset = 0; await loadHistory(); } catch (error) { inputError.textContent = error.message; } });
  favoriteFilter.addEventListener("change", () => { historyOffset = 0; loadHistory(); });
  document.querySelector("#history-prev").addEventListener("click", () => { historyOffset = Math.max(0, historyOffset - 30); loadHistory(); });
  document.querySelector("#history-next").addEventListener("click", () => { historyOffset += 30; loadHistory(); });
  loadHistory();
  timeline.addEventListener("scroll", () => { jumpButton.hidden = nearBottom() || !turns.size; });
  jumpButton.addEventListener("click", followLatest);
  document.querySelector("#close-report").addEventListener("click", () => {
    const key = artifact.dataset.turnKey;
    closeArtifact(); turns.get(key)?.root.querySelector("[data-open-artifact]")?.focus({ preventScroll: true });
  });
  document.querySelector("#expand-report").addEventListener("click", (event) => {
    const expanded = document.querySelector("#analysis-view").classList.toggle("artifact-expanded");
    event.currentTarget.setAttribute("aria-pressed", String(expanded));
    event.currentTarget.textContent = expanded ? "还原" : "展开";
  });
  document.querySelector(".artifact-tabs").addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("[data-artifact-tab]")];
    const index = tabs.indexOf(document.activeElement);
    if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].click(); tabs[next].focus();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !artifact.hidden) document.querySelector("#close-report").click(); });
  document.addEventListener("click", async (event) => {
    if (event.target.closest?.("[data-new-analysis]")) {
      if (controller.busy) return;
      closeArtifact(); turns.clear(); activeTurn = null; resultPanel = null; progressRoot = null;
      messages.replaceChildren(); welcome.hidden = false; conversationIds = [];
      sessionStorage.removeItem("chatbi-conversation"); parentQueryId = null;
      sessionStorage.removeItem("chatbi-parent-query"); updateContext();
      document.querySelector("#pending-action").hidden = true;
      status.innerHTML = '<i class="ready-dot"></i>准备就绪';
      questionInput.value = ""; updateCharCount(); inputError.textContent = "";
      window.location.hash = "query-studio"; refreshNavigation?.(); questionInput.focus(); return;
    }
    const artifactButton = event.target.closest?.("[data-open-artifact]");
    if (artifactButton) { openArtifact(turns.get(artifactButton.dataset.openArtifact), artifactButton.dataset.openTab); document.querySelector(`#tab-${artifactTab}`)?.focus({ preventScroll: true }); return; }
    const tabButton = event.target.closest?.("[data-artifact-tab]");
    if (tabButton) { openArtifact(turns.get(artifact.dataset.turnKey), tabButton.dataset.artifactTab); return; }
    const locatePending = event.target.closest?.("[data-locate-pending]");
    if (locatePending) { const turn = turns.get(locatePending.dataset.locatePending); if (turn) revealTurn(turn); return; }
    if (event.target.closest?.('a[href="#current-result"]')) {
      const latest = [...turns.values()].at(-1);
      if (latest) requestAnimationFrame(() => revealTurn(latest));
    }
    if (event.target.closest?.("[data-plan-preview]")) {
      const consoleRoot = event.target.closest("[data-plan-console]");
      try {
        const edits = collectEdits(consoleRoot);
        const preview = await historyApi(`/api/v1/query-jobs/${consoleRoot.dataset.queryId}/plan-preview`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits }) });
        consoleRoot.dataset.preview = JSON.stringify(preview);
        consoleRoot.querySelector("[data-plan-preview-result]").innerHTML = `<p>重新执行：${escapeHtml(preview.affected_steps.join("、"))}</p><p>复用结果：${escapeHtml(preview.reused_steps.join("、") || "暂无")}</p><p>${escapeHtml(preview.warning)}</p>`;
        consoleRoot.querySelector('[data-resume-action="edit"]').disabled = false;
      } catch (error) { inputError.textContent = error.message; const local = consoleRoot.querySelector(".plan-preview-error"); if (local) local.textContent = error.message; }
      return;
    }
    const evidence = event.target.closest?.("[data-evidence-step]");
    if (evidence) {
      const id = evidence.dataset.evidenceQuery || displayedResult?.history_id;
      if (!id) return;
      try {
        const params = new URLSearchParams({ step_id: evidence.dataset.evidenceStep, row_index: evidence.dataset.evidenceRow, column: evidence.dataset.evidenceColumn });
        const detail = await historyApi(`/api/v1/history/${id}/evidence?${params}`);
        document.querySelector(".evidence-dialog")?.remove(); document.body.insertAdjacentHTML("beforeend", renderEvidenceDialog(detail));
      } catch (error) { inputError.textContent = error.message; }
      return;
    }
    const resumeButton = event.target.closest?.("[data-analysis-resume]");
    if (resumeButton) {
      if (controller.busy) { inputError.textContent = "请先等待当前分析结束或取消。"; return; }
      const turn = turns.get(resumeButton.closest("[data-turn-key]")?.dataset.turnKey) ?? [...turns.values()].find((item) => item.id === resumeButton.dataset.analysisResume);
      if (!turn) return;
      selectTurn(turn);
      const id = resumeButton.dataset.analysisResume, action = resumeButton.dataset.resumeAction;
      const clarification = resultPanel.querySelector("[data-analysis-clarification]")?.value?.trim() ?? "";
      const answers = Object.fromEntries([...resultPanel.querySelectorAll("[data-clarification-answer]")].map((element) => [element.dataset.clarificationAnswer, element.value]));
      const consoleRoot = resultPanel.querySelector("[data-plan-console]");
      const preview = action === "edit" ? JSON.parse(consoleRoot?.dataset.preview ?? "null") : null;
      if (action === "edit" && (!preview || JSON.stringify(collectEdits(consoleRoot)) !== JSON.stringify(preview.edits))) { inputError.textContent = "条件已修改，请先重新预览影响范围。"; return; }
      if (action === "clarify" && !clarification && !Object.keys(answers).length) { inputError.textContent = "请先补充分析条件。"; return; }
      controller.busy = true; setBusy(true); inputError.textContent = "";
      resultPanel.querySelectorAll("[data-analysis-resume]").forEach((button) => { button.disabled = true; });
      let accepted = false;
      try {
        await historyApi(`/api/v1/query-jobs/${id}/resume`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, clarification, answers, edits: preview?.edits ?? {}, preview_token: preview?.preview_token ?? "" }) });
        if (action === "abort") {
          activeTurn.data = null;
          resultPanel.innerHTML = '<div class="empty-state"><h2>本次分析已终止</h2><p>挂起断点已清理，可以重新提问。</p></div>';
          activeTurn.root.querySelector(".turn-state").textContent = "已终止";
        } else {
          accepted = true; jobs.onJob(id);
          activeTurn.data = null;
          activeTurn.root.querySelector(".turn-state").textContent = "执行中";
          resultPanel.innerHTML = '<p class="assistant-pending" role="status">确认已收到，执行 Agent 正在处理，完成后自动展示报告。</p>';
          progressRoot.innerHTML = renderTurnProgress([{ stage: "resume_queued", detail: { message: "确认已收到，正在继续执行" } }]);
          revealTurn(activeTurn);
          const data = await jobs.watch(id);
          controller.onSuccess(data, data.question);
        }
      } catch (error) {
        if (accepted) controller.onFailure(error, activeTurn.question);
        else { inputError.textContent = error.message; const local = resultPanel.querySelector(".resume-error"); if (local) local.textContent = error.message; }
      } finally {
        controller.busy = false; setBusy(false);
        resultPanel.querySelectorAll("[data-analysis-resume]").forEach((button) => { button.disabled = false; });
        loadHistory();
      }
      return;
    }
    const historyButton = event.target.closest?.("[data-history-open],[data-history-rerun],[data-history-star],[data-history-delete],[data-feedback]");
    if (historyButton) {
      try {
        if (historyButton.dataset.historyStar) await historyApi(`/${historyButton.dataset.historyStar}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ favorite: historyButton.dataset.star === "1" }) });
        else if (historyButton.dataset.historyDelete) await historyApi(`/${historyButton.dataset.historyDelete}`, { method: "DELETE" });
        else if (historyButton.dataset.feedback) {
          await historyApi(`/${historyButton.dataset.queryId}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ feedback: historyButton.dataset.feedback }) });
          historyButton.closest(".query-feedback").textContent = "反馈已保存，感谢帮助改进分析。";
        } else {
          const id = historyButton.dataset.historyOpen ?? historyButton.dataset.historyRerun;
          if (controller.busy && historyButton.dataset.historyOpen) { inputError.textContent = "当前分析进行中，请等待完成或取消后查看其他记录。"; return; }
          const item = await historyApi(`/${id}`);
          window.location.hash = "query-studio";
          refreshNavigation?.();
          if (historyButton.dataset.historyRerun) { if (!controller.busy) { parentQueryId = null; sessionStorage.removeItem("chatbi-parent-query"); updateContext(); questionInput.value = item.question; updateCharCount(); controller.run(); } }
          else if (item.response && !item.response.history_payload_omitted) controller.onSuccess({ ...item.response, history_id: id }, item.question);
          else if (["queued", "running", "cancelling"].includes(item.status) && !controller.busy) {
            beginTurn(item.question, id);
            controller.busy = true; setBusy(true); sessionStorage.setItem("chatbi-active-job", id);
            jobs.watch(id).then((data) => controller.onSuccess(data, item.question)).catch((error) => controller.onFailure(error, item.question)).finally(() => { controller.busy = false; setBusy(false); });
          } else {
            beginTurn(item.question, id); remember(id);
            progressRoot.innerHTML = renderTurnProgress(item.events ?? []);
            controller.onFailure({ message: item.error?.message ?? item.response?.message ?? "该记录没有可展示结果。", data: item.error }, item.question);
          }
        }
        await loadHistory();
      } catch (error) { inputError.textContent = error.message; }
      return;
    }
    const retry = event.target.closest?.("[data-retry]");
    if (retry) { questionInput.value = retry.dataset.retry; updateCharCount(); controller.run(); return; }
    const copyButton = event.target.closest?.("[data-copy-sql]");
    if (copyButton) {
      const text = copyButton.closest(".sql-module")?.querySelector("code")?.textContent ?? "";
      try {
        await navigator.clipboard.writeText(text);
        copyButton.textContent = "已复制";
        setTimeout(() => { copyButton.textContent = "复制 SQL"; }, 1500);
      } catch { copyButton.textContent = "复制失败"; }
    }
    const reportButton = event.target.closest?.("[data-copy-report]");
    if (reportButton) {
      const text = reportButton.closest(".analysis-report")?.innerText ?? "";
      try {
        await navigator.clipboard.writeText(text.replace("复制报告", "").trim());
        reportButton.textContent = "已复制";
        setTimeout(() => { reportButton.textContent = "复制报告"; }, 1500);
      } catch { reportButton.textContent = "复制失败"; }
    }
  });

  // Store only server record IDs in this tab; restore answers from persisted history.
  const restoreConversation = async () => {
    const pending = sessionStorage.getItem("chatbi-active-job");
    const previous = conversationIds.length ? conversationIds : parentQueryId ? [parentQueryId] : [];
    const ids = [...new Set([...previous, ...(pending ? [pending] : [])])].slice(-20);
    if (!ids.length) return;
    restoring = true; controller.busy = true; setBusy(true);
    let pendingItem = null;
    const retained = [];
    try {
      for (const id of ids) {
        try {
          const item = await historyApi(`/${id}`);
          retained.push(id);
          beginTurn(item.question, id);
          progressRoot.innerHTML = renderTurnProgress(item.events ?? []);
          if (["queued", "running", "cancelling"].includes(item.status)) {
            pendingItem = item;
          } else if (item.response && !item.response.history_payload_omitted && ["succeeded", "suspended"].includes(item.status)) {
            controller.onSuccess({ ...item.response, history_id: id }, item.question);
          } else {
            resultPanel.innerHTML = renderFailure(item.error?.message ?? "该记录已结束，未保留完整结果。", item.question);
            activeTurn.root.querySelector(".turn-state").textContent = "未完成";
          }
        } catch (error) {
          if (error.data?.detail !== "查询记录不存在") { inputError.textContent = "部分对话记录暂时无法恢复，可在查询历史中重新打开。"; retained.push(id); }
        }
      }
      conversationIds = retained; sessionStorage.setItem("chatbi-conversation", JSON.stringify(retained));
    } finally { restoring = false; controller.busy = false; setBusy(false); followLatest(); }
    if (pendingItem) {
      beginTurn(pendingItem.question, pendingItem.id); controller.busy = true; setBusy(true);
      sessionStorage.setItem("chatbi-active-job", pendingItem.id);
      jobs.watch(pendingItem.id).then((data) => controller.onSuccess(data, pendingItem.question)).catch((error) => controller.onFailure(error, pendingItem.question)).finally(() => { controller.busy = false; setBusy(false); });
    } else sessionStorage.removeItem("chatbi-active-job");
  };
  restoreConversation(); updateCharCount();

  requestHealth().then(({ ok, data }) => {
    const components = data.components ?? {};
    // The simple-query Chroma path is the active semantic layer. The legacy
    // generic schema index may legitimately stay in fallback without making
    // the user-facing workspace degraded.
    const fallback = Object.entries(components).some(([name, component]) => name !== "schema_index" && component.status === "fallback");
    healthRoot.className = `service-health${ok ? (fallback ? " degraded" : "") : " unavailable"}`;
    healthRoot.querySelector("strong").textContent = ok ? (fallback ? "服务可用 · 部分降级" : "所有服务正常") : "服务尚未就绪";
    healthRoot.querySelector("small").textContent = ok ? "DeepSeek · Qwen · Chroma · MySQL" : "请检查模型、数据库与 Chroma 状态";
  }).catch(() => {
    healthRoot.className = "service-health unavailable";
    healthRoot.querySelector("strong").textContent = "无法连接服务";
    healthRoot.querySelector("small").textContent = "请确认本地后端正在运行";
  });
  fetch("/api/v1/runtime").then((response) => response.ok ? response.json() : null).then((data) => {
    if (!data || data.restart_required) {
      inputError.textContent = data ? "后端源码已更新，请重启服务以加载新版本。" : "当前后端版本较旧，请用 scripts/chatbi.ps1 start 启动新版。";
    }
  }).catch(() => {});
}
