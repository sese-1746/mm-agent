/* 项目台账：产物、事件和环境审计的只读汇总。 */
import { apiGet, errorHtml } from "../core/api.js";
import { esc, qs, setBusy, setOut } from "../core/dom.js";

function rows(items, key) {
  return (items || []).slice(0, 20).map((item) => `<tr><td>${esc(item[key] || "")}</td><td>${esc(item.ts || item.created_at || "")}</td><td>${esc(item.kind || item.event_type || item.actor || "")}</td></tr>`).join("");
}

function tableOrEmpty(items, key, empty) {
  return rows(items, key) || `<tr><td colspan="3">${esc(empty)}</td></tr>`;
}

export function render(root) {
  root.innerHTML = `<article class="card"><h3 class="card-title">项目台账</h3>
    <p class="muted card-note">这里集中查看论文、综述和运行过程留下的本地记录。页面只读，不修改数据库。</p>
    <div class="grid ledger-controls">
      <label class="field">产物类型
        <select id="ledger-kind"><option value="">全部产物</option><option value="literature_review">literature_review</option><option value="paper">paper</option><option value="figure">figure</option><option value="table">table</option><option value="plan">plan</option></select>
      </label>
      <div class="btn-row ledger-action"><button type="button" id="ledger-refresh" class="btn primary">刷新台账</button></div>
    </div>
    <div class="result" id="ledger-result"></div></article>`;
  document.getElementById("ledger-refresh").addEventListener("click", () => load());
  document.getElementById("ledger-kind").addEventListener("change", () => load());
  load();
}

async function load() {
  const button = document.getElementById("ledger-refresh");
  const kind = document.getElementById("ledger-kind")?.value || "";
  if (button) button.disabled = true;
  setBusy("ledger-result", "正在读取项目台账…");
  try {
    const [artifacts, events, net] = await Promise.all([
      apiGet(`/artifacts/list${qs({ limit: 20, kind })}`),
      apiGet("/audit/events?limit=20"),
      apiGet("/audit/net?limit=20"),
    ]);
    setOut("ledger-result", `<div class="stat-row">
        <div class="stat"><span class="stat-val">${esc(artifacts.total ?? 0)}</span><span class="stat-key">产物总数</span></div>
        <div class="stat"><span class="stat-val">${esc(events.total ?? 0)}</span><span class="stat-key">事件总数</span></div>
        <div class="stat"><span class="stat-val">${esc(net.total ?? 0)}</span><span class="stat-key">网络审计</span></div>
      </div>
      <h3>产物（${esc(artifacts.total ?? 0)}）</h3>
      <figure><table role="grid"><thead><tr><th>路径</th><th>时间</th><th>类型</th></tr></thead><tbody>${tableOrEmpty(artifacts.rows, "path", "暂无产物")}</tbody></table></figure>
      <h3>运行事件（${esc(events.total ?? 0)}）</h3>
      <figure><table role="grid"><thead><tr><th>事件</th><th>时间</th><th>来源</th></tr></thead><tbody>${tableOrEmpty(events.rows, "event_type", "暂无事件")}</tbody></table></figure>
      <details class="raw"><summary>网络审计记录（${esc(net.total ?? 0)}）</summary><pre>${esc(JSON.stringify(net, null, 2))}</pre></details>`);
  } catch (err) {
    setOut("ledger-result", errorHtml(err));
  } finally {
    if (button) button.disabled = false;
  }
}
