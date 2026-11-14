const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
export const STATUS = { draft: "草稿", in_review: "待审核", published: "已发布", retired: "已废弃", rejected: "已驳回" };
export function renderMetricList(items, query = "", selected = null) {
  return items.filter((item) => `${item.name} ${item.owner} ${STATUS[item.status]}`.toLowerCase().includes(query.toLowerCase()))
    .map((item) => `<button class="metric-list-item${item.id === selected ? " selected" : ""}" data-metric-id="${esc(item.id)}" type="button"><strong>${esc(item.name)}</strong><span>v${item.version} · ${esc(STATUS[item.status])} · ${item.active ? "有生效口径" : "未生效"}</span><small>${esc(item.owner)}</small></button>`).join("") || '<p class="notice">没有匹配指标。</p>';
}
export function renderVersions(detail) {
  const effective = Math.max(0, ...detail.versions.filter((item) => item.effective_at).map((item) => item.version));
  return detail.versions.map((item) => {
    const actions = { draft: [["submit", "提交审核"], ["discard", "放弃草稿"]], in_review: [["publish", "审核通过并发布"], ["reject", "驳回"]],
      published: item.version === effective ? [["retire", "废弃指标"]] : [] }[item.status] ?? [];
    return `<article class="metric-version"><div><strong>v${item.version} · ${esc(STATUS[item.status])}</strong><span>${esc(item.owner)} · ${esc(item.created_at)}</span></div><p>${esc(item.change_note)}</p>${item.effective_at ? `<p>生效时间：${esc(item.effective_at)} · 审核人：${esc(item.approved_by)}</p>` : ""}<div class="version-actions"><button class="text-button" type="button" data-load-version="${item.version}">查看 / 以此新建版本</button>${actions.map(([action, label]) => `<button type="button" class="text-button" data-decision="${action}" data-version="${item.version}">${label}</button>`).join("")}</div></article>`;
  }).join("");
}

if (typeof document !== "undefined") {
  const list = document.querySelector("#metric-list"), form = document.querySelector("#metric-form");
  const search = document.querySelector("#metric-search"), message = document.querySelector("#catalog-message");
  let items = [], detail = null, selected = null, busy = false;
  const tell = (text, error = false) => { message.textContent = text; message.className = error ? "catalog-error" : "catalog-success"; };
  const renderList = () => { list.innerHTML = renderMetricList(items, search.value, selected); };
  async function api(path, payload) {
    const response = await fetch(`/api/v1/metrics${path}`, payload ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) } : {});
    const result = await response.json();
    if (!response.ok) throw new Error(result.message ?? result.detail ?? "操作失败，请检查必填字段和版本状态。");
    return result;
  }
  async function reload() {
    const data = await api(""); items = data.items;
    document.querySelector("#metric-count").textContent = data.active_count;
    renderList();
  }
  function fill(version) {
    for (const [key, value] of Object.entries(version.payload)) {
      if (form.elements.namedItem(key)) form.elements.namedItem(key).value = Array.isArray(value) ? value.join("\n") : value;
    }
    form.elements.namedItem("owner").value = version.owner;
    form.elements.namedItem("change_note").value = "";
    form.elements.namedItem("name").readOnly = true;
    document.querySelector("#editor-title").textContent = `${detail.name} · 新版本草稿`;
    document.querySelector("#editor-subtitle").textContent = `当前查看 v${version.version}，保存会创建新版本。`;
    document.querySelector("#metric-lineage").textContent = `上游：${version.payload.depends_on.join("、") || "无"}；下游生效指标：${detail.downstream.join("、") || "无"}；来源：${version.payload.data_source}`;
  }
  async function open(id) {
    const data = await api(`/${encodeURIComponent(id)}`);
    detail = data; selected = id; renderList(); fill(detail.versions[0]);
    document.querySelector("#metric-versions").innerHTML = renderVersions(detail);
    document.querySelector("#metric-events").innerHTML = detail.events.map((item) => `<p>v${item.version} · ${esc(item.action)} · ${esc(item.actor)} · ${esc(item.at)}<br>${esc(item.note)}</p>`).join("") || "系统导入的初始版本。";
  }
  async function run(work) {
    if (busy) return;
    busy = true;
    document.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try { await work(); } catch (error) { tell(String(error.message), true); }
    finally { busy = false; document.querySelectorAll("button").forEach((button) => { button.disabled = false; }); }
  }
  search.addEventListener("input", renderList);
  list.addEventListener("click", (event) => { const id = event.target.closest("[data-metric-id]")?.dataset.metricId; if (id) run(() => open(id)); });
  document.querySelector("#new-metric").addEventListener("click", () => {
    selected = null; detail = null; form.reset(); form.elements.namedItem("name").readOnly = false; renderList();
    document.querySelector("#editor-title").textContent = "新建指标草稿";
    document.querySelector("#editor-subtitle").textContent = "填写定义和治理信息，保存后提交审核。";
    document.querySelector("#metric-versions").textContent = "保存草稿后可以提交审核。";
    document.querySelector("#metric-lineage").textContent = "保存后可以查看依赖。";
    document.querySelector("#metric-events").textContent = "暂无操作记录。";
    tell("");
  });
  form.addEventListener("submit", (event) => { event.preventDefault(); run(async () => {
    const payload = Object.fromEntries(new FormData(form));
    for (const key of ["depends_on", "aliases", "filters"]) payload[key] = String(payload[key]).split("\n").map((value) => value.trim()).filter(Boolean);
    const result = await api(selected ? `/${selected}/versions` : "", payload);
    await reload(); await open(result.id); tell("草稿已保存。请填写操作人和说明后提交审核。");
  }); });
  document.querySelector("#metric-versions").addEventListener("click", (event) => {
    const load = event.target.closest("[data-load-version]");
    if (load) { fill(detail.versions.find((item) => item.version === Number(load.dataset.loadVersion))); return; }
    const decision = event.target.closest("[data-decision]");
    if (!decision) return;
    const actor = document.querySelector("#review-actor").value.trim(), note = document.querySelector("#review-note").value.trim();
    if (!actor || !note) { tell("请填写本次操作人和操作说明。", true); return; }
    run(async () => { await api(`/${selected}/versions/${decision.dataset.version}/${decision.dataset.decision}`, { actor, note });
      await reload(); await open(selected); tell("操作成功。已发布口径将在后续查询中生效。"); });
  });
  run(reload);
}
