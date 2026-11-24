/* views/deliver.js —— 阶段 05 合规交付（#/deliver）。
 *
 * 本页是链路的最后一步：把整个流程跑到「导出交付包」，并生成 AI 使用披露（FR-13）。
 *
 * 三件容易被误解、所以写在最前面的事：
 * 1. **导出只有一条通路**：启动编排时带上 `export_params`。HTTP 层没有独立的导出路由，
 *    所以「交付」这个动作只能由编排器触发——这也是编排面板放在本页（05）而不是单独一页的理由。
 * 2. **四个卡点分布在全链路**（caliber_conflict / sanity_blocking / pre_export_compliance /
 *    plan_selection），本页是唯一的裁定入口。在前面几步被卡住时，回到本页裁定即可。
 * 3. 编排放 05 是「往前跳」，与作业方向一致；若放在 02，用户想导出就得往回跳。
 *
 * 裁定键与 agents/coordinator.py::GATE_DECISION_SPEC 逐字一致（四个卡点，不是一个子集）；
 * 前端不猜裁定、不自动填充 note（后端要求 note 为非空字符串）。
 *
 * 三条「看不到就等于没有」的东西，本页显式上屏：
 * 1. `plan_selection` 卡点（PLAN → SOLVE 之间选建模方法）。下拉里少了它，流程停在这里时
 *    页面无从恢复，只能 CLI/curl；
 * 2. `review`（待审材料）。它由 coordinator 从 `context.conflicts / sanity_items /
 *    missing_disclosure` 取，前端不送就恒显示「（无待审材料）」——等于让人盲裁。故本页把
 *    三个材料做成 JSON 输入框，只送非空值；
 * 3. `export_skipped`。「走到 export 但没给导出参数」时后端返回 DONE + export_skipped=true，
 *    不显示就会让人以为交付目录已生成。
 */
import { apiGet, apiPost } from "../core/api.js";
import {
  esc, ic, iv, num, panel, qs, rawDetails, setBusy, setErr, setOut, stats, tag,
} from "../core/dom.js";
import { bindLiveStream, liveHtml } from "../core/run_stream.js";
import { bindProgress, progressHtml } from "../core/progress_panel.js";

// 与 agents/coordinator.py::GATE_DECISION_SPEC 逐字一致（四卡点）。
const GATE_SPEC = {
  caliber_conflict: { key: "resolution", options: ["use_mapping", "keep_raw", "human_override"] },
  sanity_blocking: { key: "decision", options: ["accept", "fix_and_rerun", "abort"] },
  pre_export_compliance: { key: "decision", options: ["approve", "reject"] },
  plan_selection: { key: "decision", options: ["accept", "reselect", "abort"] },
};

// 造成「不可撤回的后果」的裁定值：中止 / 驳回 / 重选。
const DESTRUCTIVE = new Set(["abort", "reject", "reselect"]);

const EXPORT_SAMPLE = '{"paper_docx":"artifacts/paper/paper.docx",'
  + '"out_dir":"artifacts/deliverable/run-001","reviewer":"张三"}';

/** 本地时间的 `run-YYYYMMDD-HHMMSS`：断点续跑（`/run/resume`）与台账对号都靠这个名字。 */
function defaultRunId() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `run-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
    + `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** 读一个「可选 JSON」输入框：空 → undefined；非法 → 抛错（绝不静默吞掉）。 */
function readJson(id, label) {
  const raw = iv(id);
  if (!raw) return undefined;
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error(`${label} 不是合法 JSON：${e.message}`);
  }
}

/** 三个待审材料 → context 里的键（只收非空的）。 */
function readMaterials(out) {
  [["t-m-conflicts", "conflicts", "口径冲突明细"],
    ["t-m-sanity_items", "sanity_items", "合理性阻断项"],
    ["t-m-missing_disclosure", "missing_disclosure", "缺失披露项"]].forEach(([id, key, label]) => {
    const v = readJson(id, label);
    if (v !== undefined) out[key] = v;
  });
  return out;
}

function renderRunStatus(d) {
  const review = d.review;
  const reviewHtml = (review === null || review === undefined)
    ? `<p class="muted">（无待审材料：context 里没送 conflicts / sanity_items / missing_disclosure）</p>`
    : `<pre>${esc(JSON.stringify(review, null, 2))}</pre>`;
  const log = d.log || [];
  const skipped = Boolean(d.export_skipped);
  return `
    ${stats([
      ["run_id", d.run_id],
      ["stage", d.stage],
      ["paused", d.paused],
      ["gate", d.gate === null ? "（无）" : d.gate],
      ["done", d.done],
      ["halted", d.halted],
      ["export_skipped", skipped],
    ])}
    <p>${d.paused ? tag("已停在人工卡点", "warn") : ""}
       ${d.done ? tag("已完成 DONE", "ok") : ""}
       ${d.halted ? tag("已按裁定中止 HALTED", "err") : ""}</p>
    ${d.done && skipped ? `<div class="alert err"><b>DONE 但没有导出</b>：
      这次跑完没生成交付目录（未给 <code>export_params</code>，后端记
      <code>export_skipped=true</code>）。需要交付物请填「导出参数」后重跑。</div>` : ""}
    ${d.plan_candidates_missing ? `<div class="alert warn"><b>PLAN 卡点被跳过</b>：
      <code>artifacts</code> 里没有 <code>kind='plan'</code> 产物，没有可选的建模方法候选。
      到 <b>02 建模方案</b> 页（<a href="#/plan">#/plan</a>）生成方案卡片（等价 <code>cli plan build</code>）后重跑。</div>` : ""}
    <h4>问题 question</h4>
    <p>${esc(d.question || "（无）")}</p>
    <h4>待审材料 review</h4>
    ${reviewHtml}
    <h4>执行日志 log</h4>
    ${log.length ? `<ul>${log.map((x) => `<li><code>${esc(x)}</code></li>`).join("")}</ul>`
      : `<p class="muted">（空）</p>`}
    ${rawDetails(d, "RunStatus")}
  `;
}

/** 卡点名 → 裁定键 + 值域（下拉与 key 标签）。 */
function syncDecisionOptions() {
  const sel = document.getElementById("d-gate");
  if (!sel) return;
  const spec = GATE_SPEC[sel.value];
  if (!spec) return;
  const opts = spec.options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
  const keyBox = document.getElementById("d-value-key");
  const valBox = document.getElementById("d-value");
  if (keyBox) keyBox.textContent = spec.key;
  if (valBox) valBox.innerHTML = opts;
}

/** 按当前 status 决定「能不能提交裁定」，并把卡点下拉同步到实际停的位置。 */
function syncGateForm(d) {
  const sel = document.getElementById("d-gate");
  const btn = document.getElementById("b-run-resume");
  const hint = document.getElementById("d-gate-hint");
  if (!sel) return;
  const paused = Boolean(d && d.paused && d.gate && GATE_SPEC[d.gate]);
  if (paused) sel.value = d.gate;
  if (btn) btn.disabled = !paused;
  if (hint) {
    hint.textContent = paused
      ? `当前停在 ${d.gate}：选裁定值 + 必填 note 后提交`
      : "仅在 paused=true 时可提交（未暂停时后端返回 409 INVALID_GATE_DECISION）";
  }
  syncDecisionOptions();
}

export function render(root) {
  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">本页做什么</h3>
      <p class="muted card-note">启动<strong>全链路编排</strong>，把前面四步的产出跑到「导出交付包」：
        填好 <code>export_params</code> 再启动，编排器会在 export 阶段生成 <code>deliverable/</code>。
        填了导出参数才会真的交付；不填则走到 export 也只会 DONE + <code>export_skipped</code>。</p>
      <p class="muted card-note">四个卡点（<code>caliber_conflict</code> / <code>sanity_blocking</code> /
        <code>pre_export_compliance</code> / <code>plan_selection</code>）分布在全链路，
        <strong>本页是唯一裁定入口</strong>——在前面几步被卡住时，回到本页的人工裁定表单里作答。</p>
    </article>

    ${panel("全链路编排与人机回环（HITL）", `
      <form id="f-run-start">
        <div class="grid">
          <label class="field">运行编号 <code>run_id</code>（这次运行的唯一名字：中断后用同一个编号接着跑，台账里也靠它对上号）
            <input id="i-run_id" placeholder="run-20261022-101500" required>
          </label>
          <label class="field">业务库 <code>db</code>（可选）
            <input id="i-run_db" placeholder="默认 data/mm.db">
          </label>
          <label class="field">检查点库 <code>ckpt</code>（可选）
            <input id="i-run_ckpt" placeholder="默认 data/checkpoints.db">
          </label>
        </div>
        <fieldset class="flags">
          <legend>上下文 context（决定是否会停在卡点）</legend>
          <label><input type="checkbox" id="c-caliber_conflicts"> caliber_conflicts</label>
          <label><input type="checkbox" id="c-conflicts_confirmed"> conflicts_confirmed</label>
          <label><input type="checkbox" id="c-sanity_blocking"> sanity_blocking</label>
          <label><input type="checkbox" id="c-compliance_confirmed"> compliance_confirmed</label>
          <label><input type="checkbox" id="c-plan_selection_confirmed"> plan_selection_confirmed</label>
        </fieldset>
        <fieldset class="flags">
          <legend>待审材料 materials（JSON，可留空；只影响卡点里显示什么）</legend>
          <label class="field">口径冲突明细 <code>conflicts</code>
            <textarea id="t-m-conflicts" rows="2"
              placeholder='[{"field":"unit","raw":"万元","suggest":"元"}]'></textarea>
          </label>
          <label class="field">合理性阻断项 <code>sanity_items</code>
            <textarea id="t-m-sanity_items" rows="2"
              placeholder='[{"code":"RANGE_BREACH","detail":"库存量 12 亿吨"}]'></textarea>
          </label>
          <label class="field">缺失披露项 <code>missing_disclosure</code>
            <textarea id="t-m-missing_disclosure" rows="2"
              placeholder='["模型版本","提示词版本"]'></textarea>
          </label>
        </fieldset>
        <fieldset class="flags">
          <legend>导出参数 export_params（留空 → 走到 export 也只会 DONE + export_skipped）</legend>
          <label class="field">导出参数 <code>export_params</code>
            <textarea id="t-export_params" rows="2"
              placeholder='${esc(EXPORT_SAMPLE)}'></textarea>
          </label>
        </fieldset>
        <div class="btn-row">
          <button type="submit" class="btn primary">启动 / 重新启动</button>
          <button type="button" id="b-run-status" class="btn">查询状态</button>
          <span class="hint">POST <code>/api/v1/run/start</code>　·　GET <code>/api/v1/run/status</code></span>
        </div>
        ${liveHtml()}
      </form>
      ${progressHtml()}
      <hr class="rule">

      <h4>人工裁定（仅当 paused=true 时提交）</h4>
      <form id="f-run-resume">
        <div class="grid">
          <label class="field">卡点 <code>gate</code>
            <select id="d-gate">
              <option value="caliber_conflict">caliber_conflict</option>
              <option value="sanity_blocking">sanity_blocking</option>
              <option value="pre_export_compliance">pre_export_compliance</option>
              <option value="plan_selection">plan_selection</option>
            </select>
          </label>
          <label class="field">裁定键 <code><span id="d-value-key">resolution</span></code>
            <select id="d-value"></select>
          </label>
          <label class="field">说明 <code>note</code>（必填）
            <input id="d-note" placeholder="人类裁定理由">
          </label>
        </div>
        <div class="btn-row">
          <button type="submit" id="b-run-resume" class="btn primary">提交裁定并恢复</button>
          <span class="hint" id="d-gate-hint">先点「查询状态」确认 paused=true</span>
        </div>
      </form>
    `, "r-run")}

    ${panel("AI 使用披露（FR-13 / NFR-5）", `
      <form id="f-disc">
        <div class="grid">
          <label class="field">档案 id <code>profile_id</code>
            <input id="i-d-profile_id" placeholder="例如 A2024" required>
          </label>
          <label class="field">版本号 <code>version_id</code>（可选）
            <input id="i-d-version_id" type="number" placeholder="留空表示 last">
          </label>
          <label class="field">输出目录 <code>output_dir</code>
            <input id="i-d-output_dir" placeholder="例如 artifacts/compliance" required>
          </label>
          <label class="field">人工修改占比 <code>human_edit_ratio</code>（可选，0~1）
            <input id="i-d-human_edit_ratio" type="number" step="0.01" min="0" max="1" placeholder="留空">
          </label>
        </div>
        <div class="btn-row">
          <button type="submit" class="btn primary">生成 AI 使用披露</button>
          <span class="hint">POST <code>/api/v1/compliance/disclosure</code></span>
        </div>
      </form>`, "r-disc")}
  `;

  const gateSel = document.getElementById("d-gate");
  if (gateSel) gateSel.addEventListener("change", syncDecisionOptions);
  syncDecisionOptions();
  syncGateForm(null);

  /* run_id 只在页面加载时预填一次（`run-年月日-时分秒`，本地时间），之后一律以用户输入为准。 */
  const runIdBox = document.getElementById("i-run_id");
  if (runIdBox && !runIdBox.value) runIdBox.value = defaultRunId();

  const runBody = () => {
    const b = { run_id: iv("i-run_id") };
    if (iv("i-run_db")) b.db = iv("i-run_db");
    if (iv("i-run_ckpt")) b.ckpt = iv("i-run_ckpt");
    return b;
  };

  const startForm = document.getElementById("f-run-start");
  if (startForm) startForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const b = runBody();
    if (!b.run_id) { setOut("r-run", `<div class="alert warn">请先填写 run_id。</div>`); return; }
    let context;
    try {
      context = readMaterials({
        caliber_conflicts: ic("c-caliber_conflicts"),
        conflicts_confirmed: ic("c-conflicts_confirmed"),
        sanity_blocking: ic("c-sanity_blocking"),
        compliance_confirmed: ic("c-compliance_confirmed"),
        plan_selection_confirmed: ic("c-plan_selection_confirmed"),
      });
      const exp = readJson("t-export_params", "导出参数");
      if (exp !== undefined) context.export_params = exp;
    } catch (err) {
      setOut("r-run", `<div class="alert err">${esc(err.message)}</div>`);
      return;
    }
    b.context = context;
    const noExport = context.export_params === undefined;
    if (!window.confirm(`将启动 run_id=${b.run_id}`
      + "（同名运行的检查点会被复用；已存在的产物可能被覆盖）。"
      + (noExport ? "\n\n未填导出参数：流程走到 export 时会 DONE + export_skipped（不生成交付目录）。" : "")
      + "\n\n确认启动？")) return;
    setBusy("r-run", "正在启动运行…");
    try {
      const d = await apiPost("/run/start", b);
      setOut("r-run", renderRunStatus(d));
      syncGateForm(d);
    } catch (err) {
      setErr("r-run", err);
    }
  });

  const statusBtn = document.getElementById("b-run-status");
  if (statusBtn) statusBtn.addEventListener("click", async () => {
    const b = runBody();
    if (!b.run_id) { setOut("r-run", `<div class="alert warn">请先填写 run_id。</div>`); return; }
    setBusy("r-run", "正在查询状态…");
    try {
      const d = await apiGet("/run/status" + qs(b));
      setOut("r-run", renderRunStatus(d));
      syncGateForm(d);
    } catch (err) {
      setErr("r-run", err);
    }
  });

  /* 实时跟随的 id 全在 core/run_stream.js 里；本视图只交出 run_id 的读取方式。 */
  bindLiveStream(() => iv("i-run_id"));

  /* 进度守卫（只读）同上：id 全在 core/progress_panel.js，不依赖本页输入框。 */
  bindProgress();

  const resumeForm = document.getElementById("f-run-resume");
  if (resumeForm) resumeForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const gate = document.getElementById("d-gate").value;
    const spec = GATE_SPEC[gate];
    if (!spec) { setOut("r-run", `<div class="alert err">未知卡点：${esc(gate)}</div>`); return; }
    const value = document.getElementById("d-value").value;
    const body = runBody();
    if (!body.run_id) { setOut("r-run", `<div class="alert warn">请先填写 run_id。</div>`); return; }
    const note = iv("d-note");
    if (!note) {
      setOut("r-run", `<div class="alert warn">note 为必填（后端要求 note 为非空字符串）。</div>`);
      return;
    }
    body.gate = gate;
    body.decision = { [spec.key]: value, note };
    const warn = DESTRUCTIVE.has(value)
      ? `\n\n注意：${value} 是不可撤回的终态/重跑，流程会中止或从头再来。`
      : "";
    if (!window.confirm(`确认在卡点 ${gate} 提交 ${spec.key}=${value} 吗？${warn}`)) return;
    setBusy("r-run", "正在提交裁定…");
    try {
      const d = await apiPost("/run/resume", body);
      setOut("r-run", renderRunStatus(d));
      syncGateForm(d);
    } catch (err) {
      setErr("r-run", err);
    }
  });

  const discForm = document.getElementById("f-disc");
  if (discForm) discForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setBusy("r-disc", "正在生成披露…");
    try {
      const body = {
        profile_id: iv("i-d-profile_id"),
        output_dir: iv("i-d-output_dir"),
      };
      if (iv("i-d-version_id")) body.version_id = num(iv("i-d-version_id"), null);
      if (iv("i-d-human_edit_ratio")) body.human_edit_ratio = num(iv("i-d-human_edit_ratio"), null);
      const d = await apiPost("/compliance/disclosure", body);
      setOut("r-disc", `
        <div class="alert ok">披露文件已生成。</div>
        <h4>产物路径</h4>
        <ul>
          <li>Markdown：<code>${esc(d.md_path)}</code></li>
          <li>Word：<code>${esc(d.docx_path)}</code></li>
        </ul>
        ${rawDetails(d, "compliance/disclosure")}
      `);
    } catch (err) {
      setErr("r-disc", err);
    }
  });
}
