/* core/consistency_panel.js —— FR-10 一致性校验（`POST /consistency/check`，**只读**）。
 *
 * 挂在 `#/paper` 页（该页已是论文装配的落点）；另开第 7 页会被「恰好 6 个 hash」判红。
 * 四个按真实响应写、最容易写错的点（`.agent-runs/2026-10-21/task-006/response-keys.md`）：
 * 1. **`tampered_tables` 是 `list[int]`（表格下标），不是整数** —— 张数必须取 `.length`；
 *    当数字渲染会打出 `0` / `[object]`。另三个 `checked_tables`/`registered_tables`/`table_cells_checked` 才是 int。
 * 2. **`unmatched` 是 `list[dict]`**，条数 = 不同 token 数；顺序是**文档遍历序**，不是严重度序。
 * 3. 表格项的 `chapter=""` / `paragraph_index=null` 是为兼容 CLI 保留的字段（**不是缺键**），
 *    按 `kind` 分支渲染，否则会打出「第 null 段」。
 * 4. **`consistent` 三值**（`ok→true` / `mismatch→false` / 其余 `null`）：用 `false` 兜底 `null`
 *    会把「无法判定」画成「不通过」。全部 `ck-` 前缀 id 由本模块自己持有（`test_14` 只扫 views）。
 */
import { apiPost, errorHtml } from "./api.js";
import { esc, setBusy, setOut } from "./dom.js";

/** `status` 四态 → 文案 + 颜色类（`no_data` 不许显示成绿）。 */
const STATUS_TEXT = { ok: ["通过", "ok"], mismatch: ["不一致", "err"], stale_sidecar: ["校验基础过期", "warn"], no_data: ["无数据可校验", "warn"] };

/** `status` 四态 + `consistent` 三值：null 单独一档，绝不当 false。 */
function statusHtml(d) {
  const [text, cls] = STATUS_TEXT[d.status] || [`未知状态 ${d.status}`, "warn"];
  const verdict = d.consistent === null || d.consistent === undefined
    ? `<span class="tag warn">无法判定 consistent=null</span>`
    : (d.consistent ? `<span class="tag ok">通过</span>` : `<span class="tag err">不通过</span>`);
  return `<div class="alert ${cls}"><strong>${esc(text)}</strong>（status=<code>${esc(d.status)}</code>） ${verdict}
    <p>正文数字 <strong>${esc(d.paper_numbers)}</strong> 个，<strong>${esc(d.matched)}</strong> 个在结果表里找到出处，有 <strong>${esc((d.unmatched || []).length)}</strong> 个对不上。${d.loose_matched
      ? `另有 ${esc(d.loose_matched)} 个「松散匹配」（如只差一个百分号，不阻断但要看见）。` : ""}</p></div>`;
}

/** 表格诊断：三个 int + 一个 list，混了就打出 [object Object]。 */
function tableHtml(d) {
  const bad = Array.isArray(d.tampered_tables) ? d.tampered_tables : [];
  const idx = (xs) => (Array.isArray(xs) && xs.length ? xs.map((i) => `<code>#${esc(i)}</code>`).join(" ") : `<span class="muted">（无）</span>`);
  /* ⚠️ 被改动的是「下标」：张数取 .length，同时把下标列出来才对得上 sidecar。 */
  const badText = bad.length ? `<strong>${esc(bad.length)}</strong> 张表被改动：${idx(bad)}` : `无表格被改动`;
  return `<div class="stat-row">
      <div class="stat"><span class="stat-val">${esc(d.checked_tables)}</span><span class="stat-key">参与校验 checked_tables</span></div><div class="stat"><span class="stat-val">${esc(d.registered_tables)}</span><span class="stat-key">已登记排除 registered_tables</span></div>
      <div class="stat"><span class="stat-val">${esc(bad.length)}</span><span class="stat-key">被改动数 tampered_tables.length</span></div><div class="stat"><span class="stat-val">${esc(d.table_cells_checked)}</span><span class="stat-key">逐格校验 table_cells_checked</span></div>
    </div>
    <p class="hint">表格诊断：${badText}　·　排版变化（展平后相等，不参与校验）${idx(d.format_changed_tables)}
      （<code>format_changed_tables.length</code> = <code>${esc((d.format_changed_tables || []).length)}</code>）</p>`;
}

/** `unmatched` 一项：按 `kind` 分支；段落项的 `chapter` 可能是空串，不硬拼章节。 */
function oneUnmatched(u) {
  const where = u.kind === "table"
    ? `表格 <code>#${esc(u.table)}</code> 第 ${esc(u.row)} 行第 ${esc(u.col)} 列`
    : `第 ${esc(u.paragraph_index)} 段${u.chapter ? `（${esc(u.chapter)}）` : ""}`;
  return `<li><code>${esc(u.token)}</code> · ${where}<p class="hint">${esc(u.preview || "")}</p></li>`;
}

/** 对不上的数字：默认渲染前 10 条，余量给条数（不重算口径，用 `unmatched.length`）。 */
function unmatchedHtml(d) {
  const rows = Array.isArray(d.unmatched) ? d.unmatched : [];
  if (!rows.length) return `<p class="muted">（没有对不上的数字）</p>`;
  const head = rows.slice(0, 10);
  const kind = rows.reduce((a, u) => { a[u.kind] = (a[u.kind] || 0) + 1; return a; }, {});
  return `<p class="hint">按「文档遍历序」（先正文、后表格）排列，<strong>不是</strong>按严重度。
      正文 ${esc(kind.paragraph || 0)} 条 / 表格 ${esc(kind.table || 0)} 条。</p>
    <ol>${head.map(oneUnmatched).join("")}</ol>
    ${rows.length > head.length ? `<p class="hint">还有 <strong>${esc(rows.length - head.length)}</strong> 条未展示（复制下方原始 JSON 可拿全）。</p>` : ""}`;
}

/** 其余诊断：`excluded` 按后端实际桶数**原样**遍历（注释说 8 键已陈旧，代码是 9）。 */
function restHtml(d) {
  const ex = (d.excluded && typeof d.excluded === "object")
    ? Object.entries(d.excluded).map(([k, v]) => `<span class="tag">${esc(k)}=${esc(v)}</span>`).join(" ") : "";
  const idx = (xs) => (Array.isArray(xs) && xs.length ? xs.map((x) => `<code>#${esc(x)}</code>`).join(" ") : `<span class="muted">（无）</span>`);
  return `<details class="raw"><summary>其余诊断（excluded / 段落下标 / 口径开关）</summary>
    <p>排除分桶 excluded（<code>${esc(ex ? Object.keys(d.excluded).length : 0)}</code> 桶）：${ex || "（无）"}</p>
    <p>未登记段落 unrecorded_paragraphs：${idx(d.unrecorded_paragraphs)}　·　被改写段落 tampered_paragraphs：${idx(d.tampered_paragraphs)}</p>
    <p>含中文数字（抽不出 token，属口径提示非错误）cjk_numeral_paragraphs：${idx(d.cjk_numeral_paragraphs)}　·　结果文件 results_files：${(d.results_files || []).map((x) => `<code>${esc(x)}</code>`).join(" ") || "（无）"}</p>
    <p>口径：sidecar_used=<code>${esc(d.sidecar_used)}</code>　legacy_sidecar=<code>${esc(d.legacy_sidecar)}</code>　stale_sidecar=<code>${esc(d.stale_sidecar)}</code>　appendix_skipped=<code>${esc(d.appendix_skipped)}</code></p>
  </details>`;
}

/** 一致性校验卡（id 全 `ck-` 前缀；只有只读按钮）。 */
export function consistencyHtml() {
  return `<fieldset class="flags" id="ck-box">
    <legend>正文数字一致性（FR-10）· POST <code>/api/v1/consistency/check</code>（只读）</legend>
    <div class="grid">
      <label class="field">论文 docx <code>paper_docx</code>（必填）<input id="ck-docx" placeholder="artifacts/paper/paper.docx"></label>
      <label class="field">结果目录 <code>results_dir</code>（必填）<input id="ck-results" placeholder="artifacts/results"></label>
      <label class="field">sidecar（可选）<input id="ck-sidecar" placeholder="留空 = 走无 sidecar 启发式"></label>
      <label class="field">豁免 token <code>ignore_tokens</code>（逗号分隔）<input id="ck-ignore" placeholder="例如 2024,3.14"></label>
    </div>
    <label><input type="checkbox" id="ck-include_appendix"> 附录也算进去（<code>include_appendix</code>，默认不算）</label>
    <div class="btn-row">
      <button type="button" id="ck-check" class="btn primary">开始校验</button>
      <span class="hint">只读接口：读 docx 与 CSV，不写库、不写盘</span>
    </div>
    <div class="result" id="ck-out"></div>
  </fieldset>`;
}

/** 绑定校验。两个必填为空时**本地拦下**（否则每次都 422，还看不出是漏填）。 */
export function bindConsistency() {
  const btn = document.getElementById("ck-check");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const docx = document.getElementById("ck-docx").value.trim();
    const results = document.getElementById("ck-results").value.trim();
    if (!docx || !results) {
      setOut("ck-out", `<div class="alert warn"><strong>两个必填项没填全</strong>
        <p><code>paper_docx</code> 与 <code>results_dir</code> 都是必填（后端 422 也这么判）。本地先拦下，省一次无效请求。</p></div>`);
      return;
    }
    const body = {
      paper_docx: docx, results_dir: results,
      include_appendix: document.getElementById("ck-include_appendix").checked,
      ignore_tokens: document.getElementById("ck-ignore").value.split(",").map((s) => s.trim()).filter(Boolean),
    };
    const sidecar = document.getElementById("ck-sidecar").value.trim();
    if (sidecar) body.sidecar = sidecar;      // 空串不送：null 与 "" 在底层语义不同
    setBusy("ck-out", "正在比对正文数字与结果表…");
    try {
      setOut("ck-out", renderCheck(await apiPost("/consistency/check", body)));
    } catch (err) {
      const extra = err && err.status === 404
        ? `<p class="hint">docx 或结果 CSV 不存在：先确认路径相对**仓库根目录**，且论文已装配（<code>cli paper build</code>）。</p>`
        : "";
      setOut("ck-out", errorHtml(err) + extra);
    }
  });
}

function renderCheck(d) {
  return `${statusHtml(d)}${tableHtml(d)}
    <h4>对不上的数字 unmatched（${esc((d.unmatched || []).length)} 条）</h4>${unmatchedHtml(d)}${restHtml(d)}`;
}
