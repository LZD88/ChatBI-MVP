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
  if (data.semantic_mode === "chroma") return "Chroma 动态 Schema";
  if (data.schema_mode === "semantic") return "语义 Schema";
  return "全量 Schema 回退";
}

function renderEvidence(items) {
  return (items ?? []).map((item) => `<span class="evidence-chip" title="${escapeHtml(item.step_name)}">${escapeHtml(item.step_id)} · 第 ${Number(item.row_index) + 1} 行 · ${escapeHtml(item.column)}：${escapeHtml(item.value)}</span>`).join("");
}

export function renderAnalysisReport(report) {
  if (!report) return "";
  const findings = (report.findings ?? []).map((item) => `<article class="report-finding">
    <h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.statement)}</p><div class="evidence-list">${renderEvidence(item.evidence)}</div>
  </article>`).join("");
  const caveats = (report.caveats ?? []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const recommendations = (report.recommendations ?? []).map((item) => `<article class="report-recommendation">
    <strong>${escapeHtml(item.action)}</strong><p>${escapeHtml(item.rationale)}</p><div class="evidence-list">${renderEvidence(item.evidence)}</div>
  </article>`).join("");
  return `<section class="analysis-report">
    <div class="report-header"><div><span>中文分析报告</span><h3>${escapeHtml(report.title)}</h3></div><span class="report-source">${report.source === "summary_agent" ? "总结 Agent" : "安全模板"}</span></div>
    <div class="report-summary"><h4>执行摘要</h4><p>${escapeHtml(report.executive_summary)}</p></div>
    ${findings ? `<div class="report-section"><h4>核心发现</h4>${findings}</div>` : ""}
    ${caveats ? `<div class="report-section"><h4>局限</h4><ul>${caveats}</ul></div>` : ""}
    ${recommendations ? `<div class="report-section"><h4>建议</h4>${recommendations}</div>` : ""}
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
  const states = { succeeded: "成功", failed: "失败", skipped: "跳过" };
  const resultById = new Map((data.step_results ?? []).map((item) => [item.step_id, item]));
  const succeeded = (data.step_results ?? []).filter((item) => item.status === "succeeded").length;
  const total = (data.plan.steps ?? []).length;
  const timings = data.agent_timings_ms ?? {};
  const timing = (name) => Number.isFinite(Number(timings[name])) ? `${Math.round(Number(timings[name]))} ms` : "已完成";
  const overallStatus = data.agent_status === "partial" ? "部分完成" : "执行成功";
  const reportStatus = data.analysis_report ? (data.report_source === "summary_agent" ? "报告已生成" : "安全模板报告") : "等待报告";
  const steps = (data.plan.steps ?? []).map((step, index) => {
    const result = resultById.get(step.step_id) ?? {};
    const columns = (result.columns ?? []).join("、");
    return `<article class="agent-step ${escapeHtml(result.status ?? "pending")}">
      <div class="step-marker">${index + 1}</div><div class="step-content">
        <div class="step-heading"><div><span>${escapeHtml(step.step_id)}</span><h4>${escapeHtml(step.step_name)}</h4></div><span class="step-status">${escapeHtml(states[result.status] ?? "待执行")}</span></div>
        <p>${escapeHtml(step.objective)}</p>
        <div class="step-meta"><span>${Number(result.row_count ?? 0)} 行</span><span>${Math.round(Number(result.duration_ms ?? 0))} ms</span>${columns ? `<span>${escapeHtml(columns)}</span>` : ""}</div>
        ${result.error_message ? `<p class="step-error">${escapeHtml(result.error_message)}</p>` : ""}
        ${result.sql ? `<details><summary>查看本步骤 SQL</summary><pre class="sql"><code>${escapeHtml(result.sql)}</code></pre></details>` : ""}
      </div>
    </article>`;
  }).join("");
  return `<section class="agent-workflow active-agent-workflow" aria-label="复杂查询执行轨迹">
    <div class="workflow-titlebar">
      <div><span class="workflow-eyebrow">PLAN AND EXECUTE</span><h3>复杂分析执行轨迹</h3><p>问题经过规划、分步安全执行和证据化总结</p></div>
      <span class="workflow-status ${data.agent_status === "partial" ? "partial" : "success"}">${overallStatus}</span>
    </div>
    <div class="agent-flow">
      <div class="agent-node completed"><b>1</b><span><strong>规划 Agent</strong><small>${escapeHtml(timing("planning"))} · ${total} 个步骤</small></span><em>已完成</em></div><i></i>
      <div class="agent-node completed"><b>2</b><span><strong>执行 Agent</strong><small>${escapeHtml(timing("execution"))} · ${succeeded}/${total} 成功</small></span><em>已完成</em></div><i></i>
      <div class="agent-node completed"><b>3</b><span><strong>总结 Agent</strong><small>${escapeHtml(timing("summary"))} · ${escapeHtml(reportStatus)}</small></span><em>已完成</em></div>
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

export function renderResult(data) {
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
  const form = document.querySelector("#query-form");
  const questionInput = document.querySelector("#question");
  const submit = document.querySelector("#submit");
  const status = document.querySelector("#status");
  const charCount = document.querySelector("#char-count");
  const inputError = document.querySelector("#input-error");
  const resultPanel = document.querySelector("#current-result");
  const historyRoot = document.querySelector("#history");
  const healthRoot = document.querySelector("#service-health");
  const clearHistory = document.querySelector("#clear-history");
  const history = new HistoryStore();

  const renderHistory = () => { historyRoot.innerHTML = renderHistoryItems(history.items); };
  const updateCharCount = () => { charCount.textContent = `${questionInput.value.length} / 1000`; };

  const setBusy = (value) => {
    submit.disabled = value;
    questionInput.disabled = value;
    submit.lastChild.textContent = value ? " 分析中…" : " 开始分析";
    status.innerHTML = value ? '<i class="ready-dot"></i>正在生成并安全执行 SQL' : '<i class="ready-dot"></i>准备就绪';
  };

  const controller = new QueryController({
    readQuestion: () => questionInput.value,
    setBusy,
    onValidationError: (message) => { inputError.textContent = message; },
    onSuccess: (data, question) => {
      resultPanel.innerHTML = renderResult(data);
      resultPanel.classList.add("visible");
      history.add({ question, status: "成功", summary: `${data.row_count} 行 · ${data.query_type === "complex" ? "复杂查询" : "简单查询"}`, duration: formatDuration(data.timings_ms), sql: data.sql, retry: false });
      renderHistory();
      resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    onFailure: (error, question) => {
      const message = error.name === "AbortError" ? "请求超时，请重试。" : messageForError(error.data?.code, error.message);
      resultPanel.innerHTML = renderFailure(message, question);
      resultPanel.classList.add("visible");
      history.add({ question, status: "失败", summary: message, sql: "", retry: true });
      renderHistory();
      resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    },
  });

  form.addEventListener("submit", (event) => { event.preventDefault(); controller.run(); });
  questionInput.addEventListener("input", updateCharCount);
  questionInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      controller.run();
    }
  });
  document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => {
    questionInput.value = button.dataset.example;
    inputError.textContent = "";
    updateCharCount();
    questionInput.focus();
  }));
  clearHistory.addEventListener("click", () => { history.clear(); renderHistory(); });
  document.addEventListener("click", async (event) => {
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
}
