import { renderClarification } from "./analysis-tools.mjs";
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "interrupted", "timed_out", "suspended"]);
const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
export const STATUS = { queued: "排队中", running: "执行中", cancelling: "正在取消", succeeded: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断", timed_out: "已超时", suspended: "已挂起 · 等待确认" };

export function renderSuspension(data) {
  if (data.suspension?.kind === "clarification") return renderClarification(data);
  if (data.suspension?.kind === "plan_review") return `<section class="suspension-card plan-review-card"><span class="suspension-badge">需要你确认 · 当前未在执行</span><h2>计划已就绪，点击后开始执行</h2><p>你开启了“执行前确认计划”。当前流程已暂停，等待确认后才会查询数据、生成报告；继续等待不会自动执行。</p><div class="suspension-actions"><button class="primary" data-analysis-resume="${esc(data.history_id)}" data-resume-action="retry">确认计划，立即执行</button><button class="text-button" data-analysis-resume="${esc(data.history_id)}" data-resume-action="abort">终止分析</button></div><p class="suspension-hint">需要调整条件时，展开下方“修改计划筛选条件”，预览后再确认。</p><p class="resume-error" role="alert"></p></section>`;
  const info = data.suspension ?? {}, id = esc(data.history_id), options = new Set(info.options ?? []);
  const steps = info.step_results ?? [];
  return `<section class="suspension-card" role="status"><span class="suspension-badge">核心任务熔断 · 已保存断点</span><h2>分析已挂起，等待你确认</h2><p>${esc(info.message)}</p><p class="step-error">${esc(info.reason ?? "")}</p><div class="suspension-summary"><span>保留 ${steps.filter((item) => item.status === "succeeded").length} 个成功步骤</span><span>阻止 ${steps.filter((item) => item.status === "blocked").length} 个后续步骤</span><span>已续跑 ${Number(info.resume_round ?? 0)} / ${Number(info.max_resume_rounds ?? 3)} 次</span></div><p>继续时会复用已完成结果，只恢复未完成步骤。等待确认期间不占用查询执行线程。</p>${options.has("clarify") ? `<label class="clarification-label">补充分析条件<textarea data-analysis-clarification maxlength="1000" rows="3" placeholder="补充时间范围、筛选条件或字段含义"></textarea></label><p class="suspension-hint">${esc(info.clarification_hint)}</p>` : ""}<div class="suspension-actions">${options.has("retry") ? `<button type="button" class="primary" data-analysis-resume="${id}" data-resume-action="retry">从失败步骤重试</button>` : ""}${options.has("clarify") ? `<button type="button" class="text-button" data-analysis-resume="${id}" data-resume-action="clarify">补充条件后继续</button>` : ""}<button type="button" class="text-button" data-analysis-resume="${id}" data-resume-action="abort">终止本次分析</button></div><p class="suspension-hint">切换指标口径需重新查询；指标、代码或权限变更后，旧断点会拒绝续跑。</p></section>`;
}

export async function historyApi(path = "", options = {}, fetchImpl = fetch) {
  const response = await fetchImpl(path.startsWith("/api/") ? path : `/api/v1/history${path}`, options);
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.message ?? data.detail ?? "请求失败"), { data });
  return data;
}

export function renderProgress(events) {
  return `<div class="live-progress" role="status" aria-live="polite"><h3>分析执行进度</h3><ol>${events.slice(-30).map((event) => `<li><span>${esc(event.at ? new Date(event.at).toLocaleTimeString() : "")}</span><strong>${esc(event.detail?.message ?? STATUS[event.stage] ?? event.stage)}</strong>${event.detail?.step_id ? `<small>${esc(event.detail.step_id)}</small>` : ""}</li>`).join("")}</ol></div>`;
}

export function renderPersistentHistory(items) {
  if (!items.length) return '<div class="history-empty">查询记录会保存在本机，刷新后仍可查看。</div>';
  return items.map((item) => `<article class="history-item persistent-item"><div class="history-main"><div class="history-copy"><strong class="history-question">${esc(item.question)}</strong><div class="history-meta"><span>${esc(STATUS[item.status] ?? item.status)}</span><span>${esc(new Date(item.created_at).toLocaleString())}</span>${item.favorite ? "<span>已收藏</span>" : ""}${item.feedback ? `<span>反馈：${esc({ useful: "有帮助", incorrect: "结果不对", needs_data: "缺少数据" }[item.feedback])}</span>` : ""}</div></div></div><div class="persistent-actions"><button class="text-button" data-history-open="${esc(item.id)}">${TERMINAL.has(item.status) ? "查看结果与报告" : "查看进度"}</button><button class="text-button" data-history-rerun="${esc(item.id)}">再次运行</button><button class="text-button" data-history-star="${esc(item.id)}" data-star="${item.favorite ? "0" : "1"}">${item.favorite ? "取消收藏" : "收藏"}</button><button class="text-button" data-history-delete="${esc(item.id)}">删除</button></div></article>`).join("");
}

export function renderFeedback(id) {
  return `<div class="query-feedback"><span>这次分析是否有帮助？</span><button class="text-button" data-feedback="useful" data-query-id="${esc(id)}">有帮助</button><button class="text-button" data-feedback="incorrect" data-query-id="${esc(id)}">结果不对</button><button class="text-button" data-feedback="needs_data" data-query-id="${esc(id)}">缺少数据</button></div>`;
}

export class JobClient {
  constructor({ fetchImpl = fetch, eventSource = (url) => new EventSource(url), onProgress = () => {}, onJob = () => {} } = {}) {
    this.fetch = fetchImpl; this.eventSource = eventSource; this.onProgress = onProgress; this.onJob = onJob;
    this.id = null; this.stream = null; this.stages = []; this.cursor = 0; this.pollTimer = null;
  }
  async run(question, options = {}) {
    const job = await historyApi("/api/v1/query-jobs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question, ...options }) }, this.fetch);
    this.onJob(job.id); return this.watch(job.id);
  }
  watch(id) {
    this.id = id; this.stages = []; this.cursor = 0;
    return new Promise((resolve, reject) => {
      let settled = false, polling = false;
      const cleanup = () => { settled = true; this.stream?.close(); this.stream = null; clearTimeout(this.pollTimer); this.pollTimer = null; this.id = null; };
      const receive = (event) => {
        if (event.sequence <= this.cursor) return;
        this.cursor = event.sequence; this.stages.push(event); this.onProgress(this.stages);
      };
      const check = async () => {
        try {
          const item = await historyApi(`/${id}`, {}, this.fetch);
          if (settled) return;
          for (const event of item.events ?? []) receive(event);
          if (!TERMINAL.has(item.status)) return;
          cleanup();
          if (item.status === "suspended" && item.response?.suspension) resolve({ ...item.response, history_id: id, query_status: "suspended" });
          else if (item.status === "succeeded" && item.response && !item.response.history_payload_omitted) resolve({ ...item.response, history_id: id });
          else reject(Object.assign(new Error(item.error?.message ?? item.response?.message ?? "查询未完成"), { data: item.error ?? { code: item.status } }));
        } catch (error) { if (!settled) { cleanup(); reject(error); } }
      };
      const poll = async () => { if (settled) return; await check(); if (!settled) this.pollTimer = setTimeout(poll, 1500); };
      try {
        this.stream = this.eventSource(`/api/v1/query-jobs/${id}/events`);
        this.stream.addEventListener("progress", (event) => { try { receive(JSON.parse(event.data)); } catch {} });
        this.stream.addEventListener("done", check);
        this.stream.onerror = () => { this.stream?.close(); if (!polling) { polling = true; poll(); } };
        // Reconcile finished jobs even if a terminal SSE packet is lost.
        this.pollTimer = setTimeout(() => { if (!polling) { polling = true; poll(); } }, 10000);
      } catch { polling = true; poll(); }
      check();
    });
  }
  async cancel() {
    if (this.id) return historyApi(`/api/v1/query-jobs/${this.id}/cancel`, { method: "POST" }, this.fetch);
    return { accepted: false };
  }
}
