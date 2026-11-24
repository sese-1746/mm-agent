/* core/paper_panel.js —— FR-7 论文装配（`POST /paper/build`，**不调 LLM、纯确定性装配**）。
 *
 * 挂在 `#/paper` 页：装配产出的 `*.manifest.json` 正是一致性校验要的 sidecar
 * （`consistency_tool._resolve_sidecar` 缺省找同名文件），同页形成「先装配 → 再校验」闭环。
 *
 * 五个按后端代码写、最容易写错的点（`tools/paper_tool.py:612-620` 的 `return`）：
 * 1. **`placeholders` 不是「待撰写处数」**：章标题/二级标题/题注的 origin 恒为 placeholder
 *    （`heading()` / `caption()` 走的就是这条），它等于「占位来源段落数」。本工具从不撰写正文。
 * 2. `paragraphs` 只数 `doc.paragraphs`（标题/题注在内，表格单元格另记），不含表格。
 * 3. `out_path` 是后端按 CWD 解析后的落点，**原样回显返回值**，不回显用户输入。
 * 4. `out_path` 后端**无默认值**（必填），此处预填 `artifacts/paper/paper.docx` 只是前端约定；
 *    预填固定前缀 ≠ 有围栏：6 条能力路由零路径约束（D-97 挂账），hint 必须写明不校验范围。
 * 5. 「11 章」不是响应值：`template_coverage` 只在 manifest 文件里。全部 id 用 `pp-` 前缀。
 */
import { apiPost, errorHtml } from "./api.js";
import { esc, iv, setBusy, setOut } from "./dom.js";

/** 前端约定值（后端无默认；故意做成可编辑，不做只读）。 */
const DEFAULT_OUT = "artifacts/paper/paper.docx";

/** 占位段落的解释句：口径与 `paper_tool.ORIGIN_PLACEHOLDER` 一致，不许写成「缺稿」。 */
function placeholderNote(n) {
  return `<p class="hint">占位来源段落 <strong>${esc(n)}</strong> 段：章标题、二级标题与题注的 origin
    恒为 <code>placeholder</code>（结构位本身），并非正文缺稿。本工具从不撰写正文，正文一律人工补写。</p>`;
}

/** `figures=0` 时补一句归因（否则读者会以为图丢了）。 */
function figureNote(n) {
  return Number(n) === 0 ? `<p class="hint">figures/ 没有图 → 没有图题（不是渲染失败）。</p>` : "";
}

/** 附录 A 是「贴代码」还是「留空」：两态都要说清下一步。 */
function appendixNote(flag) {
  return flag ? "附录 A 已贴代码（results/solve.py 已存在）"
    : "代码位留空，先跑 FR-5 生成 results/solve.py 再重新装配";
}

/** 论文装配卡（id 全 `pp-` 前缀；`actor` / `db_path` 不暴露——仅记账、走默认库）。 */
export function paperHtml() {
  return `<fieldset class="flags" id="pp-box">
    <legend>论文装配（FR-7）· POST <code>/api/v1/paper/build</code>（不调 LLM）</legend>
    <div class="grid">
      <label class="field">档案 id <code>profile_id</code>（必填）
        <input id="pp-profile" placeholder="例如 A2024"></label>
      <label class="field">版本 <code>version</code><input id="pp-version" value="last"></label>
      <label class="field">输出 docx <code>out_path</code>（必填）
        <input id="pp-out" value="${DEFAULT_OUT}"></label>
    </div>
    <p class="hint"><code>version</code> 填 <code>last</code> 或整数 version_id（不做下拉：版本号是任意整数）。
      输出路径相对<strong>仓库根目录</strong>；后端当前<strong>不校验</strong>路径范围，预填只是习惯用法。</p>
    <details class="raw"><summary>高级：图目录 / 结果目录（留空走后端默认）</summary>
      <div class="grid">
        <label class="field">图目录 <code>figures_dir</code><input id="pp-figures" placeholder="留空 = figures/"></label>
        <label class="field">结果目录 <code>results_dir</code><input id="pp-results" placeholder="留空 = results/"></label>
      </div>
    </details>
    <div class="btn-row">
      <button type="button" id="pp-build" class="btn primary">装配 docx</button>
      <span class="hint">写盘接口：产出 <code>.docx</code> 与同名 <code>.manifest.json</code></span>
    </div>
    <div class="result" id="pp-result"></div>
  </fieldset>`;
}

/** 绑定装配。两个必填为空时本地拦下（否则每次都 422，还看不出是漏填）。 */
export function bindPaper() {
  const btn = document.getElementById("pp-build");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const profile = iv("pp-profile");
    const out = iv("pp-out");
    if (!profile || !out) {
      setOut("pp-result", `<div class="alert warn"><strong>两个必填项没填全</strong>
        <p><code>profile_id</code> 与 <code>out_path</code> 都是必填（后端 422 也这么判）。本地先拦下，省一次无效请求。</p></div>`);
      return;
    }
    const rawVersion = iv("pp-version");
    /* `version` 只接受 `last` 或纯整数：后端 `store/profile.py` 直接 `int(version)`，
       非整数会抛 ValueError，而路由只捕 KeyError/OSError → 落成裸 500（应 422）。
       本地先拦，省一次必错且难以解释的请求（D-99 登记了后端这处缺口）。 */
    if (rawVersion && rawVersion !== "last" && !/^\d+$/.test(rawVersion)) {
      setOut("pp-result", `<div class="alert warn"><strong><code>version</code> 格式不对</strong>
        <p>只能是 <code>last</code> 或整数 <code>version_id</code>：后端按 <code>int()</code> 解析，
        非整数会直接报 500 而不是 422。本地先拦下。</p></div>`);
      return;
    }
    const body = { profile_id: profile, out_path: out, version: rawVersion || "last" };
    const figures = iv("pp-figures");
    if (figures) body.figures_dir = figures;   // 空串不送 → 后端回落 figures/
    const results = iv("pp-results");
    if (results) body.results_dir = results;
    setBusy("pp-result", "正在装配 docx（确定性装配，不调 LLM）…");
    try {
      setOut("pp-result", renderPaper(await apiPost("/paper/build", body)));
    } catch (err) {
      const status = err && err.status;
      /* 错误对象形状见 `api.js:66`：`{kind:"http", status, body}`，故 `body.error_code` 取得到。
         500 有**两条**来源，归因不能只看状态码：
         · 写盘失败 → 路由显式给 `PAPER_WRITE_FAILED`，可以给「查目录」的处方；
         · 未捕获异常（如 `int(version)` 抛 ValueError）→ 裸 500，`error_code` 是类名。
         后者让用户「去查目录权限」既是错归因也是错处方，所以只有见到明确码才补 hint。 */
      const code = err && err.body && err.body.error_code;
      const extra = status === 404
        ? `<p class="hint">档案或版本不存在：先在 <a href="#/problem">#/problem</a> 页确认 <code>profile_id</code> 与版本。</p>`
        : (code === "PAPER_WRITE_FAILED"
          ? `<p class="hint">写盘失败：确认 <code>out_path</code> 的上级目录存在且可写。</p>`
          : "");
      setOut("pp-result", errorHtml(err) + extra);
    }
  });
}

/** 成功态：7 键（out_path / manifest_path / paragraphs / figures / placeholders / appendix_code / profile_version_id）。 */
function renderPaper(d) {
  return `<div class="alert ok"><strong>装配完成</strong>：docx 与 sidecar manifest 已落盘。</div>
    <div class="stat-row">
      <div class="stat"><span class="stat-val">${esc(d.paragraphs)}</span><span class="stat-key">段落数（含标题/题注，不含表格）</span></div>
      <div class="stat"><span class="stat-val">${esc(d.figures)}</span><span class="stat-key">图题数</span></div>
      <div class="stat"><span class="stat-val">${esc(d.placeholders)}</span><span class="stat-key">占位来源段落</span></div>
    </div>
    <h4>产物路径（后端返回值，原样）</h4>
    <ul>
      <li>Word：<code>${esc(d.out_path)}</code></li>
      <li>sidecar：<code>${esc(d.manifest_path)}</code></li>
    </ul>
    ${placeholderNote(d.placeholders)}${figureNote(d.figures)}
    <p class="hint">实际装配版本 v${esc(d.profile_version_id)}　·　${esc(appendixNote(d.appendix_code))}</p>
    <p class="hint">下一步：把上面的 docx 路径填到本页下方「一致性校验」的 <code>paper_docx</code>
      （sidecar 会自动找同名 <code>.manifest.json</code>）。</p>`;
}
