import test from "node:test";
import assert from "node:assert/strict";
import { renderClarification, renderPlanConsole, renderValidation, renderEvidenceDialog } from "../chatbi/web/analysis-tools.mjs";
import { renderDashboard } from "../chatbi/web/observability.mjs";
import { renderAgentWorkflow } from "../chatbi/web/app.mjs";
import { renderSuspension } from "../chatbi/web/query-jobs.mjs";

test("clarification and console expose actionable controls with escaped scope", () => {
  const data = { history_id: "id", suspension: { message: "<script>", questions: [{ id: "metric", question: "利润口径", options: ["毛利", "净利润"] }],
    plan: { steps: [{ step_id: "step_1", step_name: "收入", depends_on: [], indicators: ["收入"], filters: ["<条件>"] }] }, step_results: [] } };
  assert.match(renderClarification(data), /data-clarification-answer="metric"/);
  assert.ok(!renderClarification(data).includes("<script>"));
  assert.match(renderPlanConsole(data), /预览影响范围/);
  assert.match(renderPlanConsole(data), /&lt;条件&gt;/);
});
test("validation uncertainty and historical evidence remain visible", () => {
  assert.match(renderValidation({ validation: { status: "review_unavailable", limitations: ["需要人工复核"] } }), /需要人工复核/);
  assert.match(renderEvidenceDialog({ columns: ["<产品>"], rows: [["<script>"]], note: "历史证据" }), /&lt;script&gt;/);
});
test("dashboard displays stale version and measured domain accuracy", () => {
  const html = renderDashboard({ evaluation: { available: true, stale: true, domains: [{ name: "销售", samples: 2, correct: 1, accuracy: .5, mean_latency_ms: 100 }], token_usage: {}, failures: {} }, runtime: { retained_samples: 0, statuses: {} } });
  assert.match(html, /50.0%/); assert.match(html, /需要重新验收/);
});
test("plan review shows unstarted execution and summary instead of circuit failure", () => {
  const html = renderAgentWorkflow({ query_type: "complex", agent_mode: "plan_execute", agent_status: "awaiting_plan", plan: { steps: [] }, step_results: [] });
  assert.match(html, /等待计划确认，尚未执行/); assert.ok(!html.includes("已熔断"));
});

test("plan review explicitly waits for a primary action and keeps optional editing collapsed", () => {
  const data = { history_id: "id", suspension: { kind: "plan_review", plan: { steps: [] } } };
  const card = renderSuspension(data);
  assert.match(card, /当前未在执行/);
  assert.match(card, /继续等待不会自动执行/);
  assert.match(card, /data-resume-action="retry">确认计划，立即执行/);
  const editor = renderPlanConsole(data);
  assert.match(editor, /<details class="plan-console"/);
  assert.doesNotMatch(editor, /<details[^>]* open/);
  assert.match(editor, /修改计划筛选条件（可选）/);
});
