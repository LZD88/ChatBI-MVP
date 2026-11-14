const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

export function renderChatTurn(question) {
  return `<div class="user-message"><span class="message-author">你</span><p>${esc(question)}</p></div><div class="assistant-message"><div class="assistant-heading"><span class="assistant-avatar" aria-hidden="true">✦</span><strong>ChatBI</strong><span class="turn-state">正在理解问题</span></div><div class="turn-progress"></div><div class="turn-result" aria-live="polite"><p class="assistant-pending">正在识别你的意图<span class="thinking-dots" aria-hidden="true"> ···</span></p></div></div>`;
}

export function renderChatReply(data, key) {
  if (data.query_type === "non_query") return `<p class="assistant-copy">${esc(data.message ?? "请描述你想了解的业务数据。")}</p>`;
  const report = data.analysis_report;
  const summary = report?.executive_summary || data.summary || (data.row_count ? `已完成查询，返回 ${Number(data.row_count)} 行数据。你可以打开结果查看图表、明细和查询依据，也可以继续追问。` : "查询已完成，当前条件下没有匹配数据。你可以调整时间范围或筛选条件后继续提问。");
  const title = report?.title || (data.query_type === "complex" ? "业务分析结果" : "数据查询结果");
  const facts = (report?.findings ?? []).slice(0, 2).map((item) => `<li>${esc(item.title)}</li>`).join("");
  const metrics = !report && data.rows?.length === 1 && data.columns?.length <= 4 ? `<div class="chat-inline-metrics">${data.columns.map((column, index) => `<div><span>${esc(column)}</span><strong>${esc(data.rows[0][index] ?? "暂无数据")}</strong></div>`).join("")}</div>` : "";
  const preview = summary.length > 360 ? `${summary.slice(0, 360)}…完整结论可打开分析结果查看。` : summary;
  return `<p class="assistant-copy">${esc(preview)}</p>${metrics}${facts ? `<ul class="chat-findings">${facts}</ul>` : ""}<button type="button" class="artifact-card" data-open-artifact="${esc(key)}"><span class="artifact-card-icon" aria-hidden="true">${report ? "▤" : "▥"}</span><span class="artifact-card-copy"><strong>${esc(title)}</strong><small>${report ? "分析报告 · " : "数据图表 · "}${Number(data.row_count ?? 0)} 行结果 · ${data.query_type === "complex" ? "复杂分析" : "简单查询"}</small></span><span class="artifact-card-open">打开 ↗</span></button><div class="chat-answer-actions"><button class="text-button" type="button" data-open-artifact="${esc(key)}" data-open-tab="process">查看执行过程</button><span>可继续追问本次结果</span></div>`;
}

export function renderTurnProgress(events, expanded = false) {
  const latest = events.at(-1);
  if (!latest) return "";
  const labels = { queued: "等待处理", running: "开始处理", succeeded: "已完成", failed: "执行失败", cancelled: "已停止", suspended: "等待你确认", timed_out: "执行超时", interrupted: "执行已中断" };
  const message = latest.detail?.message ?? labels[latest.stage] ?? latest.stage;
  return `<details class="turn-trace" ${expanded ? "open" : ""}><summary><span class="trace-dot"></span><span>${esc(message)}</span><small>${events.length} 个事件</small></summary><ol>${events.slice(-30).map((event) => `<li><time>${esc(event.at ? new Date(event.at).toLocaleTimeString() : "")}</time><span>${esc(event.detail?.message ?? labels[event.stage] ?? event.stage)}</span></li>`).join("")}</ol></details>`;
}

export function readConversationIds(storage) {
  try {
    const ids = JSON.parse(storage.getItem("chatbi-conversation") || "[]");
    return Array.isArray(ids) ? [...new Set(ids.filter((id) => typeof id === "string" && /^[a-f0-9]{32}$/.test(id)))].slice(-20) : [];
  } catch { return []; }
}
