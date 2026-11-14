import test from "node:test";
import assert from "node:assert/strict";
import { JobClient, renderProgress, renderPersistentHistory, renderSuspension } from "../chatbi/web/query-jobs.mjs";
import { renderAgentWorkflow } from "../chatbi/web/app.mjs";

test("progress and history escape untrusted data", () => {
  assert.ok(!renderProgress([{ stage: "step_sql", detail: { message: "<script>" } }]).includes("<script>"));
  const history = renderPersistentHistory([{ id: "1", question: "<img>", status: "succeeded", created_at: "2026-01-01", favorite: 1 }]);
  assert.ok(history.includes("&lt;img&gt;") && history.includes("查看结果与报告") && history.includes("取消收藏"));
});

test("jobs consume SSE and reconcile stored terminal response", async () => {
  let count = 0;
  const listeners = {}, updates = [];
  const stream = { addEventListener(name, fn) { listeners[name] = fn; }, close() {} };
  const fetchImpl = async (path) => ({ ok: true, json: async () => path === "/api/v1/query-jobs" ? { id: "1" }
    : { status: ++count > 1 ? "succeeded" : "running", response: { rows: [[1]] }, events: [] } });
  const client = new JobClient({ fetchImpl, eventSource: () => stream, onProgress: (events) => updates.push(events.length) });
  const result = client.run("分析");
  await new Promise((resolve) => setTimeout(resolve, 0));
  listeners.progress({ data: JSON.stringify({ sequence: 1, stage: "planning", detail: {} }) });
  listeners.progress({ data: JSON.stringify({ sequence: 1, stage: "planning", detail: {} }) });
  await listeners.done();
  assert.deepEqual((await result).rows, [[1]]);
  assert.deepEqual(updates, [1]);
  assert.equal(client.id, null);
});

test("cancel sends server request and cancelled job does not render success", async () => {
  let cancelled = false;
  const listeners = {};
  const client = new JobClient({ eventSource: () => ({ addEventListener: (name, fn) => { listeners[name] = fn; }, close() {} }),
    fetchImpl: async (path) => ({ ok: true, json: async () => {
      if (path.endsWith("/cancel")) { cancelled = true; return { accepted: true }; }
      return { status: cancelled ? "cancelled" : "running", error: { code: "cancelled", message: "查询已取消" }, events: [] };
    } }) });
  const pending = client.watch("2");
  const assertion = assert.rejects(pending, /查询已取消/);
  await client.cancel(); await listeners.done(); await assertion;
});

test("suspended job closes stream and preserves its clarification state", async () => {
  let closed = false;
  const data = { status: "suspended", events: [], response: { question: "分析", suspension: { message: "已保存断点", options: ["retry", "clarify", "abort"] } } };
  const client = new JobClient({ eventSource: () => ({ addEventListener() {}, close() { closed = true; } }),
    fetchImpl: async () => ({ ok: true, json: async () => data }) });
  const result = await client.watch("q1");
  assert.equal(result.query_status, "suspended");
  assert.equal(result.history_id, "q1");
  assert.ok(closed && client.id === null);
});

test("suspension controls escape text, show budgets and hide exhausted retry", () => {
  const data = { history_id: 'q"<', suspension: { message: "<script>", reason: "<img>", options: ["abort"], resume_round: 3, max_resume_rounds: 3,
    step_results: [{ status: "succeeded" }, { status: "blocked" }] } };
  const html = renderSuspension(data);
  assert.ok(!html.includes("<script>") && !html.includes("<img>"));
  assert.ok(html.includes("保留 1 个成功步骤") && html.includes("阻止 1 个后续步骤"));
  assert.ok(html.includes('data-resume-action="abort"') && !html.includes('data-resume-action="retry"'));
  assert.ok(html.includes("3 / 3"));
});

test("workflow shows weighting repair attempts and suspended summary", () => {
  const html = renderAgentWorkflow({ query_type: "complex", agent_mode: "plan_execute", agent_status: "suspended", plan: { steps: [
    { step_id: "step_1", importance: "high", step_name: "查询", objective: "核算" },
    { step_id: "step_2", importance: "low", step_name: "辅助", objective: "比较" },
  ] }, step_results: [{ step_id: "step_1", status: "failed", attempts: 4, attempt_errors: [{ attempt: 1, message: "<script>" }] }, { step_id: "step_2", status: "blocked" }] });
  assert.ok(html.includes("高权重 · 核心") && html.includes("低权重 · 辅助"));
  assert.ok(html.includes("尝试 4 次") && html.includes("熔断阻止") && html.includes("未开始"));
  assert.ok(!html.includes("<script>"));
});
