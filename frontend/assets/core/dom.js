/* core/dom.js —— DOM/字符串工具层。
 *
 * 只做「拼字符串 + 写 DOM」，不碰网络、不碰主题、不碰路由。
 * 所有插入 innerHTML 的动态值都必须经 esc()，这是本文件的唯一安全约定。
 *
 * 唯一的对外依赖是 core/api.js 的 errorHtml（setErr 三态之一要渲染错误体）。
 * 与 api.js 构成**有意的函数级循环引用**：两侧都只有 function 声明，
 * ES 模块的函数提升保证求值顺序无关。
 */
import { errorHtml } from "./api.js";

/** HTML 转义（所有插入 DOM 的字符串都经此，避免 XSS）。 */
export function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/** 取值安全版（用于结果渲染）。 */
export const val = (o, k) => (o === null || o === undefined ? undefined : o[k]);

/** 状态标签。 */
export function tag(text, cls) {
  return `<span class="tag ${cls || ""}">${esc(text)}</span>`;
}

/** 布尔标签：真→ok、假→warn（沿用原口径）。 */
export function boolTag(flag, textTrue, textFalse) {
  return flag ? tag(textTrue || "是", "ok") : tag(textFalse || "否", "warn");
}

/** 关键指标卡片行。 */
export function stats(pairs) {
  const cells = pairs.map(([k, v]) => (
    `<div class="stat"><span class="stat-val">${esc(v)}</span><span class="stat-key">${esc(k)}</span></div>`
  )).join("");
  return `<div class="stat-row">${cells}</div>`;
}

/** 明细表。columns = [{label, key, fmt?, get?}]；fmt 返回「已 HTML 安全」的片段。 */
export function table(rows, columns, rowClass) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return `<p class="muted">（无数据）</p>`;
  }
  const head = columns.map((c) => `<th>${esc(c.label)}</th>`).join("");
  const body = rows.map((r) => {
    const cls = rowClass ? rowClass(r) : "";
    const tds = columns.map((c) => {
      const raw = c.get ? c.get(r) : r[c.key];
      const cell = c.fmt ? c.fmt(raw, r) : esc(raw);
      return `<td>${cell}</td>`;
    }).join("");
    return `<tr class="${esc(cls)}">${tds}</tr>`;
  }).join("");
  return `<figure><table role="grid"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></figure>`;
}

/** 折叠的原始 JSON（不留死角，但不作为主展示）。 */
export function rawDetails(obj, label) {
  return `<details class="raw"><summary>原始响应 JSON：${esc(label || "")}</summary>
    <pre>${esc(JSON.stringify(obj, null, 2))}</pre></details>`;
}

/** 查询串拼装（跳过空值）。 */
export function qs(params) {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** 读取输入框值（trim）。 */
export function iv(id) { const e = document.getElementById(id); return e ? e.value.trim() : ""; }
/** 读取勾选框状态。 */
export function ic(id) { const e = document.getElementById(id); return e ? e.checked : false; }

/** 通用卡片骨架（标题 + 内容 + 结果挂载点）。 */
export function panel(title, bodyHtml, resultId) {
  return `<article class="card">
    <h3 class="card-title">${esc(title)}</h3>
    ${bodyHtml}
    <div class="result" id="${resultId}"></div>
  </article>`;
}

/** 写结果区。 */
export function setOut(id, html) {
  const e = document.getElementById(id);
  if (e) e.innerHTML = html;
}

/** 三态之一：加载中。 */
export function setBusy(id, msg) {
  setOut(id, `<p class="muted">${esc(msg || "执行中…")}</p>`);
}

/** 三态之一：失败（统一走 errorHtml，保证 error_code / message / trace_id 上屏）。 */
export function setErr(id, err) {
  setOut(id, errorHtml(err));
}

/** 数字兜底转换。 */
export function num(v, dft) {
  const n = Number(v);
  return Number.isFinite(n) ? n : dft;
}
