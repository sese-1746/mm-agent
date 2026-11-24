/* core/plan_panel.js —— FR-1 方案卡片（`POST /plan/build`，**会调用模型**）。
 *
 * 挂在 `#/plan` 页：候选方法在此产出；PLAN 卡点的裁定在 `#/deliver` 页，另开第 7 页会被
 * 「恰好 6 个 hash」判红。
 * 四个按真实响应写、最容易写错的点（`.agent-runs/2026-10-21/task-008/README.md` §1.1）：
 * 1. **`valid` 与 `ready_for_gate` 是两件事**：前者只答「字段全不全」，后者 = `valid and
 *    fabrication_check == "done"`。把 `ready_for_gate=false` 一律说成「方案不可用」是失实
 *    （`planner.py:402-413`）。
 * 2. **`dropped` 在本接口是 `list[str]`**（被剔除的分句清单）；`problem/parse` 里同名键是
 *    `int` 计数——同名不同型，别共用渲染函数（本条故意不写带前导斜杠的路径字面量：
 *    `tests/test_frontend_modules.py::_api_tokens` 会把「引号/反引号紧跟斜杠」的串当成
 *    真实调用点，写进注释就等于给另一个端点伪造覆盖证据）。
 * 3. 响应里**没有**题面原文回显，也**没有**「卡点是否已采纳」；这两项一律不上屏，不猜。
 * 4. `candidate_count` 是真实响应值（`= len(tasks)`），不写死条数。
 * 全部 `pl-` 前缀 id 由本模块自己持有（`test_14` 只扫 views；`pl-` 全站零占用）。
 */
import { apiPost, errorHtml } from "./api.js";
import { esc, rawDetails, setBusy, setOut, stats } from "./dom.js";

/** 候选的五个必填文本字段（`planner.py:56` 的 `TASK_REQUIRED_FIELDS`）。 */
const TASK_FIELDS = [
  ["method", "方法 method"],
  ["fit", "适配 fit"],
  ["unfit", "不适配 unfit"],
  ["data_needed", "所需数据 data_needed"],
  ["acceptance", "验收标准 acceptance"],
];

/** 空值不写成空白：留空是「被剔除后没补」，不是「本来就没有」。 */
function cell(v) {
  return esc(v) || `<span class="muted">（空）</span>`;
}

/** `valid` / `ready_for_gate` / `fabrication_check` 三键的口径（三态分开，不合并成一句话）。 */
function verdictHtml(d) {
  const inc = Array.isArray(d.incomplete_fields) ? d.incomplete_fields : [];
  if (d.valid === true && d.ready_for_gate === true) {
    return `<div class="alert ok"><strong>可用于 PLAN 卡点</strong>
      <p><code>valid=true</code>（字段齐）且 <code>ready_for_gate=true</code>
      （<code>fabrication_check=done</code>，数字反造假真跑过）。</p></div>`;
  }
  if (d.valid !== true) {
    const list = inc.length
      ? inc.map((x) => `<code>${esc(x)}</code>`).join(" ")
      : `<span class="muted">（incomplete_fields 为空）</span>`;
    return `<div class="alert warn"><strong><code>valid=false</code>：字段不全</strong>
      <p>缺 ${esc(inc.length)} 处：${list}</p>
      <p>这些字段是<strong>整段被反造假清洗剔空后留空的</strong>——后端宁可露个洞也不代笔补写，
      需人工补写后重跑。此时 <code>ready_for_gate</code> 必然为 <code>false</code>：字段不全就不能交给 PLAN 卡点。</p></div>`;
  }
  /* valid=true 但 ready_for_gate=false：只可能是反造假没执行，**不是**「方案不可用」。 */
  return `<div class="alert warn"><strong>字段完整，但反造假校验未执行</strong>
    <p><code>valid=true</code> 而 <code>ready_for_gate=false</code>：
    <code>fabrication_check=${esc(d.fabrication_check)}</code>。题面为空时后端跳过数字反造假
    （没有比对基准），所以这不是「方案不可用」，而是<strong>没验过</strong>——候选里的数字出处需人工核对；
    带上题面重跑才会拿到 <code>ready_for_gate=true</code>。</p></div>`;
}

/** 一条候选（title / method / fit / unfit / data_needed / acceptance 逐条上屏）。 */
function oneTask(t) {
  const rows = TASK_FIELDS.map(([k, label]) => `<p><strong>${esc(label)}</strong>：${cell(t[k])}</p>`).join("");
  const inc = Array.isArray(t.incomplete_fields) ? t.incomplete_fields : [];
  /* `from_archive` 的真实语义：方法名能否在题面里定位（`planner.py:392-397`），不是「来自档案」。 */
  const fromArchive = t.from_archive === true
    ? `<p class="hint"><code>from_archive=true</code>：方法名能在题面里定位到，有题面依据。</p>`
    : (t.from_archive === false
      ? `<p class="hint"><code>from_archive=false</code>：方法名没在题面里出现，按「模型建议」对待（不冒充题面依据）。</p>`
      : "");
  return `<li><h4>${esc(t.id || "?")} · ${esc(t.title || "（无标题）")}</h4>
    ${rows}${fromArchive}
    ${inc.length ? `<p class="hint">本条缺字段：${inc.map((x) => `<code>${esc(x)}</code>`).join(" ")}</p>` : ""}</li>`;
}

/** `dropped` 是 **`list[str]`**（不是 int）：按数组逐条列出。 */
function droppedHtml(d) {
  const rows = Array.isArray(d.dropped) ? d.dropped : [];
  if (!rows.length) return `<p class="muted">（没有分句被剔除）</p>`;
  return `<ul>${rows.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

function listHtml(rows, emptyText) {
  const xs = Array.isArray(rows) ? rows : [];
  if (!xs.length) return `<p class="muted">${esc(emptyText)}</p>`;
  return `<ul>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

function renderPlan(d) {
  const tasks = Array.isArray(d.tasks) ? d.tasks : [];
  const dropped = Array.isArray(d.dropped) ? d.dropped : [];
  const inc = Array.isArray(d.incomplete_fields) ? d.incomplete_fields : [];
  const pid = (d.profile_id === null || d.profile_id === undefined) ? "（未关联档案）" : d.profile_id;
  return `${verdictHtml(d)}
    ${stats([
      ["候选方法 candidate_count", d.candidate_count],
      ["字段完整 valid", d.valid],
      ["可用 ready_for_gate", d.ready_for_gate],
      ["反造假 fabrication_check", d.fabrication_check],
      ["题面字符 source_chars", d.source_chars],
      ["prompt_version", d.prompt_version],
    ])}
    <p>产物路径 <code>plan_path</code>：<code>${esc(d.plan_path) || "（后端未返回）"}</code>　·　
      artifacts.id <code>artifact_id</code>：<code>${esc(d.artifact_id)}</code></p>
    <p class="hint">run_id <code>${esc(d.run_id)}</code>　·　profile_id <code>${esc(pid)}</code>　·　
      题面字符数为 <strong>${esc(d.source_chars)}</strong>（题面原文不回显）</p>
    <p class="hint">本接口只产出候选并落盘：<strong>不</strong>自动流入 SOLVE，
      <strong>也不返回</strong>「是否已被 PLAN 卡点采纳」——采纳与否在 <code>#/deliver</code> 的
      <code>plan_selection</code> 卡点裁定。</p>
    <h4>候选方法 tasks（${esc(tasks.length)} 条）</h4>
    ${tasks.length ? `<ol>${tasks.map(oneTask).join("")}</ol>` : `<p class="muted">（tasks 为空）</p>`}
    <h4>被剔除的分句 dropped（${esc(dropped.length)} 条，字符串数组）</h4>
    <p class="hint">这些分句含题面里不存在的数字，被数字反造假剔除；剔除后整段为空会在
      <code>incomplete_fields</code> 里露出洞，系统不代笔补写。</p>
    ${droppedHtml(d)}
    <h4>缺字段汇总 incomplete_fields（${esc(inc.length)} 处）</h4>
    ${inc.length ? `<p>${inc.map((x) => `<span class="tag warn">${esc(x)}</span>`).join(" ")}</p>`
      : `<p class="muted">（无：所有候选字段齐全）</p>`}
    <h4>待定问题 open_questions</h4>
    ${listHtml(d.open_questions, "（无）")}
    ${rawDetails(d, "PlanBuild")}`;
}

/** 方案卡片（id 全 `pl-` 前缀；挂载点在 run.js 的两个 `<form>` 之外）。 */
export function planHtml() {
  return `<fieldset class="flags" id="pl-box">
    <legend>方案卡片（FR-1）· POST <code>/api/v1/plan/build</code>（会调用模型）</legend>
    <label class="field">题面 <code>problem_text</code>（必填）
      <textarea id="pl-problem" rows="4" placeholder="整段贴入建模题目与数据条件：数字反造假以它为比对基准，空题面会跳过校验"></textarea>
    </label>
    <div class="grid">
      <label class="field">关联档案 <code>profile_id</code>（可选）
        <input id="pl-profile_id" placeholder="可留空；留空 = 不关联档案"></label>
      <label class="field">运行 id <code>run_id</code>（可选）
        <input id="pl-run_id" placeholder="留空 = 后端新生成"></label>
    </div>
    <details class="raw"><summary>高级：产物目录 out_dir</summary>
      <label class="field">输出目录 <code>out_dir</code>
        <input id="pl-out_dir" placeholder="artifacts/plan"></label>
      <p class="hint">相对<strong>后端进程的当前工作目录</strong>解析，目录不存在会自动新建；留空 = 后端默认
        <code>artifacts/plan</code>。</p>
    </details>
    <div class="btn-row">
      <button type="button" id="pl-build" class="btn primary">生成方案卡片</button>
      <span class="hint">调用模型通常 20~60 秒；成功后写 <code>artifacts/plan/&lt;run_id&gt;.json</code>
        并登记 <code>artifacts</code> 表。只产出候选，不自动流入 SOLVE。</span>
    </div>
    <div class="result" id="pl-out"></div>
  </fieldset>`;
}

/** 绑定点按：必填在**本地先拦**（后端 422 看不出是漏填），空串可选字段不送。 */
export function bindPlan() {
  const btn = document.getElementById("pl-build");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const problem = document.getElementById("pl-problem").value.trim();
    if (!problem) {
      setOut("pl-out", `<div class="alert warn"><strong>题面 problem_text 没填</strong>
        <p>后端是 <code>min_length=1</code>，空串直接 422。题面同时是数字反造假的比对基准，
        不填就拿不到 <code>ready_for_gate=true</code>。</p></div>`);
      return;
    }
    const body = { problem_text: problem };
    const pid = document.getElementById("pl-profile_id").value.trim();
    if (pid) body.profile_id = pid;      // 空串不送：null 与 "" 在产物里语义不同
    const rid = document.getElementById("pl-run_id").value.trim();
    if (rid) body.run_id = rid;
    const outDir = document.getElementById("pl-out_dir").value.trim();
    if (outDir) body.out_dir = outDir;

    btn.disabled = true;
    setBusy("pl-out", "正在调用模型生成方案卡片，通常 20~60 秒，请勿刷新…");
    try {
      setOut("pl-out", renderPlan(await apiPost("/plan/build", body)));
    } catch (err) {
      /* 只对**已知**成因给处方：写盘失败才提 out_dir；其余错误只上屏真实 error_code，不按状态码猜。 */
      const code = err && err.body ? err.body.error_code : undefined;
      const extra = code === "PLAN_WRITE_FAILED"
        ? `<p class="hint">处方：检查 <code>out_dir</code> 指向的目录是否存在且可写——
          该路径不能是一个已存在的文件，也不能是只读目录 / 只读盘。</p>`
        : "";
      setOut("pl-out", errorHtml(err) + extra);
    } finally {
      btn.disabled = false;
    }
  });
}
