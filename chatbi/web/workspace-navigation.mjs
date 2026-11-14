import { mountDashboard } from "./observability.mjs";

export function mountWorkspaceNavigation(root = document, browser = window) {
  const analysis = root.querySelector("#analysis-view");
  const dashboard = root.querySelector("#evaluation-dashboard");
  const history = root.querySelector("#history-view");
  if (!analysis || !dashboard) return;
  const links = [...root.querySelectorAll('.nav-list a[href^="#"]')];
  const title = root.querySelector(".topbar h1");
  const label = root.querySelector("[data-workspace-label]");
  const load = mountDashboard(root.querySelector("#evaluation-dashboard-content"), root.querySelector("#refresh-evaluation"));
  const update = () => {
    const isDashboard = browser.location.hash === "#evaluation-dashboard";
    const isHistory = browser.location.hash === "#history-panel";
    analysis.hidden = isDashboard || isHistory;
    if (history) history.hidden = !isHistory;
    dashboard.hidden = !isDashboard;
    const active = links.find((link) => link.hash === browser.location.hash) ?? links.find((link) => link.hash === "#query-studio");
    for (const link of links) {
      link.classList.toggle("active", link === active);
      if (link === active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    title.textContent = isDashboard ? "检索与评测看板" : isHistory ? "查询历史" : "智能数据工作台";
    label.textContent = isDashboard ? "评测看板" : isHistory ? "查询记录" : "智能问数";
    if (isDashboard) { browser.scrollTo(0, 0); load(); }
  };
  browser.addEventListener("hashchange", update);
  update();
  return update;
}
