import assert from "node:assert/strict";
import test from "node:test";
import { HistoryStore, QueryController, escapeHtml, messageForError, renderAnalysisReport, renderAgentWorkflow, renderHistoryItems, renderPipeline, renderResult, requestQuery, validateQuestion } from "../chatbi/web/app.mjs";

test("validates empty and valid questions", () => {
  assert.equal(validateQuestion("  ").ok, false);
  assert.deepEqual(validateQuestion("  销售额  "), { ok: true, question: "销售额" });
});

test("escapes untrusted result values", () => {
  assert.equal(escapeHtml('<script>"x"</script>'), "&lt;script&gt;&quot;x&quot;&lt;/script&gt;");
});

test("renders columns in order and truncated notice", () => {
  const html = renderResult({ sql: "SELECT x", columns: ["b", "a"], rows: [["<x>", 2]], row_count: 1, truncated: true, schema_mode: "semantic", query_type: "complex", intent_confidence: 0.91, intent_source: "qwen", intent_reason: "多指标对比", semantic_mode: "chroma", recalled_indicators: ["收入"], recalled_tables: ["sales_orders", "exchange_rates"], recalled_fields: ["sales_orders.net_amount"], inferred_joins: ["sales_orders.currency = exchange_rates.currency"], pipeline_stages: ["sql_ast_safety", "connection_pool_execution"], row_level_applied: false });
  assert.ok(html.indexOf(">b</th>") < html.indexOf(">a</th>"));
  assert.match(html, /&lt;x&gt;/);
  assert.match(html, /结果达到服务端上限/);
  assert.match(html, /复杂查询/);
  assert.match(html, /qwen · 91%/);
  assert.match(html, /多指标对比/);
  assert.match(html, /语义召回详情/);
  assert.match(html, /sales_orders/);
  assert.match(html, /完整安全执行链路/);
  assert.match(html, /AST 安全检查/);
});

test("renders an explicit empty-result message", () => {
  assert.match(renderResult({ sql: "SELECT 1", columns: [], rows: [], row_count: 0, schema_mode: "semantic" }), /没有匹配数据/);
});

test("ordinary conversation renders an escaped reply without data analysis UI", () => {
  const html = renderResult({ query_type: "non_query", message: "你好<script>alert(1)</script>", intent_reason: "普通问候" });
  assert.match(html, /普通对话/);
  assert.match(html, /本轮未进入数据分析/);
  assert.match(html, /你好&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|复杂查询|分析完成|SQL|规划 Agent|没有匹配数据/);
});

test("renders complex agents and escapes report evidence", () => {
  const data = {
    query_type: "complex",
    agent_mode: "plan_execute",
    plan: { analysis_goal: "比较收入与毛利", final_deliverable: "中文报告", steps: [{ step_id: "step_1", step_name: "核算收入", objective: "汇总收入" }] },
    step_results: [{ step_id: "step_1", status: "succeeded", row_count: 1, duration_ms: 12, columns: ["收入"], sql: "SELECT 1" }],
  };
  const workflow = renderAgentWorkflow(data);
  assert.match(workflow, /规划 Agent/);
  assert.match(workflow, /执行 Agent/);
  assert.match(workflow, /总结 Agent/);

  const report = renderAnalysisReport({
    title: "收入分析", executive_summary: "分析完成", source: "summary_agent",
    findings: [{ title: "结果", statement: "发现", evidence: [{ step_id: "step_1", step_name: "核算收入", row_index: 0, column: "收入", value: "<script>x</script>" }] }],
    caveats: [], recommendations: [],
  });
  assert.doesNotMatch(report, /<script>x/);
  assert.match(report, /&lt;script&gt;x&lt;\/script&gt;/);
});

test("makes legacy complex execution visibly different from three-agent execution", () => {
  const legacy = renderAgentWorkflow({ query_type: "complex", pipeline_stages: ["sql_ast_safety"] });
  assert.match(legacy, /未进入三 Agent/);
  assert.match(legacy, /旧版单 SQL 链路/);
  assert.match(legacy, /规划 Agent/);

  const pipeline = renderPipeline({
    query_type: "complex",
    pipeline_stages: ["complex_planning_agent", "complex_execution_agent", "complex_summary_agent", "evidence_validation"],
  });
  assert.match(pipeline, /完整安全执行链路/);
  assert.match(pipeline, /4 个阶段已通过/);
  assert.match(pipeline, /证据校验/);
});

test("renders actionable advice with escaped target, owner and risk", () => {
  const html = renderAnalysisReport({
    title: "产品线报告", executive_summary: "存在差异", source: "summary_agent",
    recommendation_status: "actionable", recommendation_reason: "基于可比结果",
    recommendations: [{
      target: '<img src=x onerror="alert(1)">', problem: "毛利率需要复核", owner_role: "产品线负责人",
      priority: "medium", confidence: "low", action: "复核成本", rationale: "存在横向差异",
      expected_impact: "定位差异", precondition: "同一币种", risk: "不能证明因果", evidence: [],
    }],
  });
  assert.match(html, /业务建议/);
  assert.match(html, /产品线负责人/);
  assert.match(html, /不能证明因果/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img/);
  const empty = renderAnalysisReport({ title: "报告", executive_summary: "空结果", recommendations: [] });
  assert.match(empty, /数据不足/);
});

test("maps stable errors without exposing arbitrary fallback when known", () => {
  assert.match(messageForError("sql_rejected", "secret stack"), /安全检查/);
  assert.doesNotMatch(messageForError("sql_rejected", "secret stack"), /secret/);
  assert.match(messageForError("database_timeout"), /查询超时/);
  assert.match(messageForError("model_unavailable"), /模型服务/);
  assert.match(messageForError("not_a_real_code"), /系统暂时/);
});

test("history keeps newest items and enforces its limit", () => {
  const history = new HistoryStore(2);
  history.add({ question: "one" }); history.add({ question: "two" }); history.add({ question: "three" });
  assert.deepEqual(history.items.map((item) => item.question), ["three", "two"]);
});

test("history hides long SQL behind an expandable wrapped detail", () => {
  const longSql = `SELECT ${"very_long_column, ".repeat(80)}x FROM sales_orders`;
  const html = renderHistoryItems([{ question: "各产品线的毛利率是多少？", status: "成功", summary: "4 行", sql: longSql }]);
  assert.match(html, /查看生成 SQL/);
  assert.match(html, /history-details/);
  assert.match(html, /history-sql/);
  assert.ok(html.length > longSql.length);
});

test("history store can be cleared", () => {
  const history = new HistoryStore(2);
  history.add({ question: "one" });
  assert.deepEqual(history.clear(), []);
  assert.equal(history.items.length, 0);
});

test("request function handles success and API failure", async () => {
  const success = await requestQuery("收入", async () => ({ ok: true, json: async () => ({ row_count: 1 }) }), 100);
  assert.equal(success.row_count, 1);
  await assert.rejects(
    requestQuery("危险", async () => ({ ok: false, json: async () => ({ code: "sql_rejected", message: "raw" }) }), 100),
    /安全检查/,
  );
});

test("request function aborts on timeout", async () => {
  const hangingFetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  });
  await assert.rejects(requestQuery("收入", hangingFetch, 5), { name: "AbortError" });
});

test("controller restores controls after success, API failure, and network failure", async () => {
  for (const request of [
    async () => ({ sql: "SELECT 1", row_count: 1 }),
    async () => { throw Object.assign(new Error("安全拒绝"), { data: { code: "sql_rejected" } }); },
    async () => { throw new TypeError("network failed"); },
  ]) {
    const busyStates = [];
    const events = [];
    const controller = new QueryController({
      readQuestion: () => "  收入  ",
      setBusy: (value) => busyStates.push(value),
      onValidationError: (message) => events.push(["validation", message]),
      onSuccess: (_data, question) => events.push(["success", question]),
      onFailure: (_error, question) => events.push(["failure", question]),
      request,
    });
    await controller.run();
    assert.deepEqual(busyStates, [true, false]);
    assert.equal(controller.busy, false);
    assert.ok(events.some((event) => ["success", "failure"].includes(event[0]) && event[1] === "收入"));
  }
});

test("controller prevents duplicate submission while a request is active", async () => {
  let release;
  let requestCount = 0;
  const pending = new Promise((resolve) => { release = resolve; });
  const controller = new QueryController({
    readQuestion: () => "毛利",
    setBusy: () => {},
    onValidationError: () => {},
    onSuccess: () => {},
    onFailure: () => {},
    request: async () => { requestCount += 1; return pending; },
  });
  const first = controller.run();
  assert.equal(await controller.run(), false);
  assert.equal(requestCount, 1);
  release({ sql: "SELECT 1", row_count: 1 });
  assert.equal(await first, true);
});
