const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
export function renderDashboard(data) {
  const report = data.evaluation, retrieval = data.retrieval ?? {};
  return `<section class="panel dashboard-section"><h2>当前检索链路</h2><p>向量编码：${esc(retrieval.encoder)} · 重排：${esc(retrieval.reranker ?? "未启用")}</p>${retrieval.fallback ? `<p class="notice">${esc(retrieval.fallback_reason ?? "使用回退检索")}</p>` : ""}<details><summary>最近一次候选及重排得分</summary><pre>${esc(JSON.stringify(retrieval.candidates ?? {}, null, 2))}</pre></details></section><section class="panel dashboard-section"><h2>真实模型验收</h2>${report.available ? `<p>${esc(report.generated_at)} · ${report.passed ? "通过质量门槛" : "未通过质量门槛"}</p><p class="${report.stale ? "notice" : ""}">报告版本 ${esc(report.source_version)} · 当前版本 ${esc(report.current_version)}${report.stale ? " · 源码已更新，需要重新验收" : ""}</p><p>${esc(report.note)}</p><div class="table-wrap"><table><thead><tr><th>业务域 / 用例类型</th><th>样本</th><th>正确数</th><th>结果正确率</th><th>平均耗时</th></tr></thead><tbody>${report.domains.map((domain) => `<tr><td>${esc(domain.name)}</td><td>${domain.samples}</td><td>${domain.correct}</td><td>${(domain.accuracy * 100).toFixed(1)}%</td><td>${domain.mean_latency_ms ? `${Math.round(domain.mean_latency_ms)} ms` : "未记录"}</td></tr>`).join("")}</tbody></table></div><h3>Token 消耗</h3>${Object.entries(report.token_usage).map(([name, usage]) => `<p>${esc(name)}：输入 ${Number(usage.prompt_tokens ?? 0)} / 输出 ${Number(usage.completion_tokens ?? 0)} · ${usage.complete ? `每题平均 ${Math.round(usage.mean_tokens_per_question ?? 0)}` : "统计不完整"}</p>`).join("")}<h3>失败分类</h3><p>${Object.entries(report.failures).map(([name, count]) => `${esc(name)}：${count}`).join("；") || "本次报告没有结果失败"}</p>` : `<p>${esc(report.message)}</p>`}</section><section class="panel dashboard-section"><h2>本机运行记录</h2><p>当前保留 ${data.runtime.retained_samples} 条记录（受清理和收藏策略影响）</p><p>${Object.entries(data.runtime.statuses).map(([status, count]) => `${esc(status)}：${count}`).join(" · ") || "暂无记录"}</p></section>`;
}
export function mountDashboard(root, refreshButton, fetchImpl = fetch) {
  let loading = false;
  const load = async () => {
    if (loading) return;
    loading = true;
    refreshButton.disabled = true;
    root.setAttribute("aria-busy", "true");
    try { const response = await fetchImpl("/api/v1/observability"); if (!response.ok) throw new Error("无法读取看板，请点击刷新重试。"); root.innerHTML = renderDashboard(await response.json()); }
    catch (error) { root.textContent = error.message; }
    finally { loading = false; refreshButton.disabled = false; root.removeAttribute("aria-busy"); }
  };
  refreshButton.addEventListener("click", load);
  return load;
}
if (typeof document !== "undefined" && document.querySelector("#dashboard") && document.querySelector("#refresh")) {
  mountDashboard(document.querySelector("#dashboard"), document.querySelector("#refresh"))();
}
