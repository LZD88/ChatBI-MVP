import test from "node:test";
import assert from "node:assert/strict";
import { buildChartOption, renderVisualizations, validSpec } from "../chatbi/web/charts.mjs";
import { renderMetricList, renderVersions } from "../chatbi/web/metrics.mjs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const echarts = require("../chatbi/web/vendor/echarts-5.6.0.min.js");

const spec = { version: 1, type: "bar", title: "产品线收入", columns: ["产品线", "收入", "毛利率"],
  dimension: "产品线", metrics: ["收入", "毛利率"], rows: [["甲", 100, .2], ["乙", null, .3]], note: "只代表返回数据" };

test("charts use independent axes, preserve nulls and use canvas tooltip", () => {
  const option = buildChartOption(spec);
  assert.equal(option.yAxis.length, 2);
  assert.equal(option.series[1].yAxisIndex, 1);
  assert.equal(option.series[0].data[1], null);
  assert.equal(option.tooltip.renderMode, "richText");
});
test("chart spec rejects arbitrary JavaScript types and nonfinite data", () => {
  assert.equal(validSpec({ ...spec, type: "<script>" }), false);
  assert.equal(validSpec({ ...spec, rows: [["甲", Infinity, .2]] }), false);
  assert.equal(buildChartOption({ ...spec, metrics: ["不存在"] }), null);
});
test("chart labels and KPI cards escape database values", () => {
  const output = renderVisualizations([{ ...spec, type: "kpi", title: '<img onerror="alert(1)">', metrics: ["收入"], rows: [["甲", 100, .2]] }]);
  assert.ok(!output.includes("<img"));
  assert.match(output, /&lt;img/);
  assert.match(output, /100/);
});
test("stacked series respects dimension pair and missing pair stays null", () => {
  const option = buildChartOption({ ...spec, type: "stacked_bar", columns: ["地区", "产品线", "收入"], dimension: "地区", series_dimension: "产品线", metrics: ["收入"], rows: [["华北", "甲", 1], ["华北", "乙", 2], ["华南", "甲", 3]] });
  assert.deepEqual(option.series[1].data, [2, null]);
});
test("catalog list and version history escape input and hide stale retirement", () => {
  const output = renderMetricList([{ id: "1", name: "<script>", owner: "负责人", status: "draft", version: 1 }]);
  assert.ok(!output.includes("<script>"));
  assert.match(output, /&lt;script&gt;/);
  const versions = renderVersions({ versions: [1, 2].map((version) => ({ version, status: "published", effective_at: "2026", owner: "张三", change_note: "升级" })) });
  assert.equal((versions.match(/data-decision="retire"/g) ?? []).length, 1);
});
test("waterfall stores signed cumulative endpoints across zero", () => {
  const option = buildChartOption({ ...spec, type: "waterfall", columns: ["原因", "收入变动"], dimension: "原因", metrics: ["收入变动"], rows: [["下降", -100], ["回升", 30], ["增长", 200]] });
  assert.deepEqual(option.series[0].data, [[0, 0, -100, -100], [1, -100, -70, 30], [2, -70, 130, 200]]);
});

test("locally bundled ECharts renders every chart option to SVG", () => {
  const inputs = [spec, { ...spec, type: "line" },
    { ...spec, type: "pie", metrics: ["收入"], rows: [["甲", 100, .2], ["乙", 200, .3]] },
    { ...spec, type: "stacked_bar", columns: ["地区", "产品线", "收入"], dimension: "地区", series_dimension: "产品线", metrics: ["收入"], rows: [["华北", "甲", 1], ["华北", "乙", 2]] },
    { ...spec, type: "waterfall", columns: ["原因", "收入变动"], dimension: "原因", metrics: ["收入变动"], rows: [["下降", -100], ["回升", 30], ["增长", 200]] }];
  for (const input of inputs) {
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 900, height: 600 });
    try {
      chart.setOption(buildChartOption(input));
      const svg = chart.renderToSVGString();
      assert.match(svg, /<svg/);
      assert.ok(!svg.includes("NaN"), input.type);
      assert.ok(svg.includes("<path") || svg.includes("<rect"), input.type);
    } finally { chart.dispose(); }
  }
});
