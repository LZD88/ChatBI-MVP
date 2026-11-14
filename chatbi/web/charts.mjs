const TYPES = new Set(["kpi", "line", "bar", "stacked_bar", "pie", "waterfall"]);
const html = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const COLORS = ["#4f63e9", "#0e9384", "#f79009", "#9e77ed", "#ee6b83", "#36bffa", "#6172f3", "#98a2b3"];

export function validSpec(spec) {
  return spec?.version === 1 && TYPES.has(spec.type) && Array.isArray(spec.columns)
    && new Set(spec.columns).size === spec.columns.length && Array.isArray(spec.metrics)
    && spec.metrics.length > 0 && spec.metrics.length <= 4
    && spec.metrics.every((key) => spec.columns.includes(key))
    && (spec.type === "kpi" || spec.columns.includes(spec.dimension))
    && (spec.type !== "stacked_bar" || spec.columns.includes(spec.series_dimension))
    && (spec.type !== "pie" && spec.type !== "waterfall" || spec.metrics.length === 1)
    && Array.isArray(spec.rows) && spec.rows.length > 0 && spec.rows.length <= 100
    && spec.rows.every((row) => Array.isArray(row) && row.length === spec.columns.length
      && spec.metrics.every((key) => row[spec.columns.indexOf(key)] === null || Number.isFinite(row[spec.columns.indexOf(key)])))
    && (spec.type !== "waterfall" || spec.rows.every((row) => Number.isFinite(row[spec.columns.indexOf(spec.metrics[0])])));
}

export function renderVisualizations(specs = []) {
  return specs.map((spec, index) => {
    if (!validSpec(spec)) return "";
    const kind = { kpi: "指标卡", line: "趋势", bar: "对比", stacked_bar: "堆叠对比", pie: "构成", waterfall: "变动瀑布" }[spec.type];
    const values = spec.type === "kpi" ? `<div class="business-kpis">${spec.metrics.map((key) => `<div><span>${html(key)}</span><strong>${html(spec.rows[0][spec.columns.indexOf(key)] ?? "暂无数据")}</strong></div>`).join("")}</div>`
      : `<div class="chart-canvas" data-chart="${index}" style="height:${spec.type === "pie" ? 350 : 230 * spec.metrics.length + 60}px" role="img" aria-label="${html(spec.title)}：${html(spec.metrics.join("、"))}"></div>`;
    return `<section class="chart-card"><div class="chart-heading"><div><span>${html(kind)}${spec.source_step_id ? ` · ${html(spec.source_step_id)}` : ""}</span><h3>${html(spec.title)}</h3></div>${spec.type !== "kpi" ? `<button class="text-button" type="button" data-export-chart="${index}">导出 PNG</button>` : ""}</div>${values}<p class="chart-scope">${html(spec.note)}</p></section>`;
  }).join("");
}

export function buildChartOption(spec) {
  if (!validSpec(spec) || spec.type === "kpi") return null;
  const get = (row, key) => row[spec.columns.indexOf(key)];
  const labels = [...new Set(spec.rows.map((row) => String(get(row, spec.dimension))))];
  const common = { animation: false, color: COLORS, textStyle: { fontFamily: "Microsoft YaHei, sans-serif" },
    tooltip: { trigger: "axis", renderMode: "richText", confine: true }, aria: { enabled: true } };
  if (spec.type === "pie") return { ...common, tooltip: { trigger: "item", renderMode: "richText", confine: true },
    legend: { bottom: 0, type: "scroll" }, series: [{ type: "pie", radius: ["35%", "65%"], center: ["50%", "45%"],
      label: { formatter: "{b}: {d}%" }, data: spec.rows.map((row) => ({ name: String(get(row, spec.dimension)), value: get(row, spec.metrics[0]) })) }] };
  const grids = spec.metrics.map((_, index) => ({ left: 88, right: 35, top: 40 + 230 * index, height: 155, containLabel: false }));
  const axes = spec.metrics.map((metric, index) => ({ type: "value", name: metric, gridIndex: index,
    axisLabel: { width: 70, overflow: "truncate" }, splitLine: { lineStyle: { color: "#eef0f5" } } }));
  const xaxes = spec.metrics.map((_, index) => ({ type: "category", gridIndex: index, data: labels,
    axisLabel: { hideOverlap: true, width: 90, overflow: "truncate" } }));
  let series = spec.metrics.map((metric, index) => ({ name: metric, type: spec.type === "line" ? "line" : "bar",
    xAxisIndex: index, yAxisIndex: index, barMaxWidth: 50, connectNulls: false,
    data: spec.rows.map((row) => get(row, metric)), itemStyle: { color: COLORS[index] } }));
  if (spec.type === "stacked_bar") {
    const groups = [...new Set(spec.rows.map((row) => String(get(row, spec.series_dimension))))];
    series = groups.map((group) => ({ name: group, type: "bar", stack: "total", barMaxWidth: 50,
      data: labels.map((label) => { const row = spec.rows.find((item) => String(get(item, spec.dimension)) === label && String(get(item, spec.series_dimension)) === group); return row ? get(row, spec.metrics[0]) : null; }) }));
  }
  if (spec.type === "waterfall") {
    let cumulative = 0;
    const changes = [];
    for (const [index, row] of spec.rows.entries()) {
      const change = get(row, spec.metrics[0]);
      const next = cumulative + change;
      changes.push([index, cumulative, next, change]);
      cumulative = next;
    }
    // Floating rectangles work across zero; ordinary stacked bars split signs
    // and would misrepresent cumulative totals below zero.
    series = [{ type: "custom", name: spec.metrics[0], encode: { x: 0, y: [1, 2] }, data: changes,
      renderItem: (_params, api) => {
        const start = api.coord([api.value(0), api.value(1)]), end = api.coord([api.value(0), api.value(2)]);
        const width = Math.min(api.size([1, 0])[0] * .55, 50), change = api.value(3);
        return { type: "group", children: [
          { type: "rect", shape: { x: start[0] - width / 2, y: Math.min(start[1], end[1]), width, height: Math.max(Math.abs(start[1] - end[1]), 1) },
            style: { fill: change >= 0 ? "#0e9384" : "#ee6b83" } },
          { type: "text", style: { text: String(change), x: end[0], y: Math.min(start[1], end[1]) - 8, align: "center", verticalAlign: "bottom", fill: "#344054", fontSize: 12 } }
        ] };
      } }];
    common.tooltip.show = false;
  }
  return { ...common, grid: grids, xAxis: xaxes, yAxis: axes, series,
    legend: spec.type === "stacked_bar" ? { top: 0, type: "scroll" } : undefined,
    dataZoom: labels.length > 12 ? [{ type: "inside", xAxisIndex: xaxes.map((_, index) => index), filterMode: "none" }] : [] };
}

let current = [];
let observer;
export function disposeCharts() {
  observer?.disconnect();
  for (const chart of current) chart.dispose();
  current = [];
}

export function mountCharts(root, specs, echarts = globalThis.echarts, onDrilldown = null) {
  if (!echarts) return;
  observer = new ResizeObserver(() => current.forEach((chart) => chart.resize()));
  root.querySelectorAll("[data-chart]").forEach((element) => {
    const spec = specs[Number(element.dataset.chart)];
    const option = buildChartOption(spec);
    if (!option) return;
    const chart = echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(option);
    if (onDrilldown && spec.dimension) {
      chart.on("click", (event) => {
        const value = spec.rows.find((row) => String(row[spec.columns.indexOf(spec.dimension)]) === String(event.name))?.[spec.columns.indexOf(spec.dimension)];
        if (value !== undefined) onDrilldown(spec, value);
      });
      element.title = "点击分类查看原筛选范围内的业务明细";
    }
    observer.observe(element);
    current.push(chart);
    root.querySelector(`[data-export-chart="${element.dataset.chart}"]`)?.addEventListener("click", () => {
      const link = document.createElement("a");
      link.download = "ChatBI-chart.png";
      link.href = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#fff" });
      link.click();
    });
  });
}
