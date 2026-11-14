import test from "node:test";
import assert from "node:assert/strict";
import { renderChatTurn, renderChatReply, renderTurnProgress, readConversationIds } from "../chatbi/web/conversation.mjs";
import { renderResult } from "../chatbi/web/app.mjs";

test("chat separates each question and reply and escapes user content", () => {
  const html = renderChatTurn('<img onerror="alert(1)">');
  assert.match(html, /user-message/);
  assert.match(html, /turn-result/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
  const greeting = renderChatReply({ query_type: "non_query", message: "你好<script>" }, "1");
  assert.match(greeting, /你好&lt;script&gt;/);
  assert.doesNotMatch(greeting, /artifact-card|查看执行过程/);
});

const data = {
  query_type: "simple", sql: "SELECT 8 AS 订单数量", columns: ["订单数量"], rows: [[8]], row_count: 1,
  pipeline_stages: ["sql_ast_safety"], analysis_context: { turns: [{ question: "订单数" }] },
  visualization_spec: [],
};

test("answers show actual scalar values and keep artifact links attached to the originating turn", () => {
  const html = renderChatReply(data, 'turn-7"<');
  assert.match(html, /订单数量/);
  assert.match(html, /<strong>8<\/strong>/);
  assert.match(html, /data-open-artifact="turn-7&quot;&lt;"/);
  assert.doesNotMatch(html, /SELECT 8/);
});

test("artifact tabs isolate report, data, execution and SQL", () => {
  const report = { title: "预算复核", executive_summary: "请复核预算差异", findings: [], recommendations: [] };
  const full = { ...data, analysis_report: report };
  const brief = renderResult(full, "report");
  assert.match(brief, /请复核预算差异/);
  assert.doesNotMatch(brief, /SELECT 8|<table|SQL 执行链路/);
  const table = renderResult(full, "data");
  assert.match(table, /<table/);
  assert.doesNotMatch(table, /请复核预算差异|SELECT 8/);
  const process = renderResult(full, "process");
  assert.match(process, /AST 安全检查/);
  assert.doesNotMatch(process, /SELECT 8|请复核预算差异/);
  assert.match(renderResult(full, "sql"), /SELECT 8/);
});

test("SQL tab retains every executed subtask query", () => {
  const html = renderResult({ ...data, step_results: [{ step_id: "step_1", step_name: "上游", sql: "SELECT '<x>'" }, { step_id: "step_2", step_name: "下游", sql: "SELECT 2" }] }, "sql");
  assert.match(html, /step_1 · 上游/);
  assert.match(html, /step_2 · 下游/);
  assert.match(html, /&lt;x&gt;/);
  assert.doesNotMatch(html, /<x>/);
});

test("progress stays collapsed by default and preserves explicit expansion", () => {
  const events = [{ stage: "intent", detail: { message: "<script>识别" } }];
  assert.doesNotMatch(renderTurnProgress(events), /class="turn-trace" open|<script>/);
  assert.match(renderTurnProgress(events, true), /class="turn-trace" open/);
  assert.match(renderTurnProgress(events), /&lt;script&gt;/);
});

test("conversation restoration accepts only bounded unique server IDs", () => {
  const storage = (value) => ({ getItem: () => value });
  assert.deepEqual(readConversationIds(storage("not json")), []);
  assert.deepEqual(readConversationIds(storage('{"bad":true}')), []);
  const ids = Array.from({ length: 25 }, (_, i) => i.toString(16).padStart(32, "0"));
  assert.deepEqual(readConversationIds(storage(JSON.stringify([...ids, ids[24], "<script>", null]))), ids.slice(-20));
});
