const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

export function renderClarification(data) {
  const info = data.suspension;
  return `<section class="suspension-card"><span class="suspension-badge">业务澄清 · 尚未查询</span><h2>先确认这几个条件</h2><p>${esc(info.message)}</p>${(info.questions ?? []).map((item) => `<label class="clarification-label">${esc(item.question)}${item.options?.length ? `<select data-clarification-answer="${esc(item.id)}">${item.options.map((answer) => `<option>${esc(answer)}</option>`).join("")}</select>` : `<input data-clarification-answer="${esc(item.id)}" maxlength="160" placeholder="例如：2026年第三季度">`}</label>`).join("")}<div class="suspension-actions"><button class="primary" data-analysis-resume="${esc(data.history_id)}" data-resume-action="clarify">确认条件后分析</button><button class="text-button" data-analysis-resume="${esc(data.history_id)}" data-resume-action="abort">终止分析</button></div></section>`;
}

export function renderPlanConsole(data) {
  const info = data.suspension, done = new Set((info.reconciliations ? [] : info.step_results ?? []).filter((row) => row.status === "succeeded").map((row) => row.step_id));
  if (!info.plan) return "";
  return `<details class="plan-console" data-plan-console data-query-id="${esc(data.history_id)}"><summary>修改计划筛选条件（可选）</summary><p>无需修改可直接点击上方“确认计划，立即执行”。修改条件后，每行一条，先预览影响范围，再确认执行。</p>${info.plan.steps.map((step) => `<article><strong>${esc(step.step_id)} · ${esc(step.step_name)} ${done.has(step.step_id) ? "· 已完成，结果保留" : ""}</strong><p>依赖：${esc(step.depends_on.join("、") || "无")} · 指标：${esc(step.indicators.join("、"))}</p><textarea rows="2" maxlength="3000" data-plan-step="${esc(step.step_id)}" data-original="${esc(JSON.stringify(step.filters))}" ${done.has(step.step_id) ? "disabled" : ""}>${esc(step.filters.join("\n"))}</textarea></article>`).join("")}<button class="text-button" data-plan-preview>预览影响范围</button><div data-plan-preview-result aria-live="polite"></div><p class="plan-preview-error" role="alert"></p><button class="primary" data-analysis-resume="${esc(data.history_id)}" data-resume-action="edit" disabled>确认修改并继续</button></details>`;
}

export function collectEdits(root) {
  const edits = {};
  for (const input of root.querySelectorAll("[data-plan-step]:not(:disabled)")) {
    const filters = input.value.split("\n").map((value) => value.trim()).filter(Boolean);
    if (JSON.stringify(filters) !== input.dataset.original) edits[input.dataset.planStep] = filters;
  }
  return edits;
}

export function renderValidation(data) {
  const validation = data.validation ?? {}, steps = validation.steps ?? { main: validation };
  if (!Object.keys(validation).length) return "";
  return `<details class="semantic-details validation-panel" open><summary>结果校验 Agent · ${esc({ reviewed: "已复核", partial_review: "部分复核", review_unavailable: "复核不可用", historical_evidence: "历史证据汇总" }[validation.status] ?? "待核验")}</summary><div class="details-body">${Object.entries(steps).map(([id, result]) => `<article><strong>${esc(id)}</strong>${Object.entries(result.checks ?? {}).map(([key, value]) => `<p>${esc(key)}：${esc(value)}</p>`).join("")}<ul>${[...(result.issues ?? []), ...(result.limitations ?? [])].map((item) => `<li>${esc(item)}</li>`).join("")}</ul></article>`).join("")}</div></details>`;
}

export function renderEvidenceDialog(data) {
  return `<dialog class="evidence-dialog" open><form method="dialog"><button class="text-button" aria-label="关闭明细">关闭</button></form><h3>数据证据与明细</h3><p>${esc(data.note)}</p><div class="table-wrap"><table><thead><tr>${(data.columns ?? []).map((column) => `<th>${esc(column)}</th>`).join("")}</tr></thead><tbody>${(data.rows ?? []).map((row) => `<tr>${row.map((value) => `<td>${esc(value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div><details><summary>查看来源 SQL</summary><pre class="sql">${esc(data.sql)}</pre></details></dialog>`;
}
