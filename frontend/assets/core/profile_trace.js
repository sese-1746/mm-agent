/* core/profile_trace.js —— FR-9 字段级溯源（`GET /profile/{profile_id}/trace`，**只读**）。
 *
 * 只挂在 `#/problem` 页：只有这页手里有 `profile_id` 上下文（页面顶部那个输入框）。
 *
 * 三个照真实键写、最容易写错的地方（`.agent-runs/2026-10-21/task-006/response-keys.md`）：
 * 1. **查询参数叫 `version`，返回行里的键叫 `version_id`** —— 两者不是一个东西，混用会读空；
 * 2. **两个时间键**：`produced_at` 是「这个字段什么时候被产出」，`ts` 是「这条溯源什么时候落库」。
 *    两个都上屏并各带标签，不许只显示一个还笼统叫「时间」；
 * 3. **来源是两个键**：`producer`（角色，可能是空串）+ `upstream_refs`（数组，恒为 list）。
 *    只显示 `producer` 会把上游链整段丢掉。
 *
 * 行内其余键（`id` / `profile_id` / `trace_id`）不上主表：`id` 只做行 key，`trace_id` 是
 * 「同批写入」分组用，主表放不下，需要时看下方折叠的原始 JSON。
 *
 * 全部 `tr-` 前缀 id 由本模块自己持有（`test_14` 只扫 views）。
 */
import { apiGet, errorHtml } from "./api.js";
import { esc, qs, rawDetails, setBusy, setOut, table } from "./dom.js";

/** 版本徽章：`version_id` 是整数版本号。 */
const verBadge = (v) => (v === null || v === undefined ? "—" : `<span class="tag">v${esc(v)}</span>`);

/** 上游来源：恒为 list；空数组要显示「（无）」而不是空白。 */
function refsOf(v) {
  const xs = Array.isArray(v) ? v : (v ? [v] : []);
  if (!xs.length) return `<span class="muted">（无）</span>`;
  return xs.map((x) => `<span class="tag">${esc(x)}</span>`).join(" ");
}

function columns() {
  return [
    { label: "版本 version_id", key: "version_id", fmt: (v) => verBadge(v) },
    { label: "字段路径 field_path", key: "field_path", fmt: (v) => `<code>${esc(v)}</code>` },
    { label: "来源角色 producer", key: "producer", fmt: (v) => (v ? `<span class="tag">${esc(v)}</span>` : `<span class="muted">（空）</span>`) },
    { label: "上游 upstream_refs", key: "upstream_refs", fmt: (v) => refsOf(v) },
    /* 两个时间分开两列，标签写全：混成一列就把「产出」和「落库」画错了。 */
    { label: "生成时刻 produced_at", key: "produced_at" },
    { label: "落库时刻 ts", key: "ts" },
  ];
}

function renderTrace(profileId, d) {
  const rows = Array.isArray(d.rows) ? d.rows : [];
  const more = Number(d.total) > Number(d.count)
    ? `<p class="hint">共 <strong>${esc(d.total)}</strong> 条，本次只返回 <strong>${esc(d.count)}</strong> 条
        （受 <code>limit</code> 限制）——调大 limit 或加 <code>field_path</code> 收窄。</p>`
    : "";
  return `<p class="hint">档案 <code>${esc(profileId)}</code>：本页 <strong>${esc(d.count)}</strong> 条，
      合计 <strong>${esc(d.total)}</strong> 条（按 id 升序 = 写入顺序）。</p>
    ${more}
    ${table(rows, columns())}
    ${rows.length ? rawDetails(d, "profile/{profile_id}/trace") : ""}`;
}

/** 溯源卡（id 全 `tr-` 前缀）。 */
export function traceHtml() {
  return `<fieldset class="flags" id="tr-box">
    <legend>字段级溯源（FR-9）· GET <code>/api/v1/profile/{profile_id}/trace</code>（只读）</legend>
    <div class="grid">
      <label class="field">版本 <code>version</code>（可选，留空 = last）
        <input id="tr-version" placeholder="last 或具体版本号"></label>
      <label class="field">字段路径 <code>field_path</code>（可选，如 <code>paper.title</code>）
        <input id="tr-field_path" placeholder="留空 = 全部字段"></label>
      <label class="field">条数上限 <code>limit</code>（默认 1000）
        <input id="tr-limit" value="1000"></label>
    </div>
    <div class="btn-row">
      <button type="button" id="tr-load" class="btn primary">查询溯源</button>
      <span class="hint">只读接口；档案或版本不存在会返回 404</span>
    </div>
    <div class="result" id="tr-out"></div>
  </fieldset>`;
}

/** 绑定查询。`getProfileId()` 由视图提供（复用页面顶部的 `i-profile_id`）。 */
export function bindTrace(getProfileId) {
  const btn = document.getElementById("tr-load");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const profileId = String(getProfileId() || "").trim();
    if (!profileId) {
      setOut("tr-out", `<div class="alert warn">请先在上方填写档案 id（<code>profile_id</code>）——溯源必须有档案上下文。</div>`);
      return;
    }
    const limit = Number(document.getElementById("tr-limit").value.trim());
    const q = qs({
      version: document.getElementById("tr-version").value.trim(),
      field_path: document.getElementById("tr-field_path").value.trim(),
      limit: Number.isFinite(limit) && limit >= 0 ? limit : 1000,
    });
    setBusy("tr-out", "正在读取字段溯源…");
    try {
      const d = await apiGet(`/profile/${encodeURIComponent(profileId)}/trace` + q);
      setOut("tr-out", renderTrace(profileId, d));
    } catch (err) {
      setErrLike(err);
    }
  });
}

/** 404 大多不是「错」而是「这条链还没写过」：在三要素错误体后面补一句可操作的说明。 */
function setErrLike(err) {
  const extra = err && err.status === 404
    ? `<p class="hint">档案或版本不存在：先读一次档案（<code>GET /profile/{id}</code>）确认
       <code>profile_id</code> 与 <code>version</code> 都对，再看溯源。</p>`
    : "";
  setOut("tr-out", errorHtml(err) + extra);
}
