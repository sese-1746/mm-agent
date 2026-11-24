/* views/solve.js —— 阶段 03 求解脚本（#/solve）。
 *
 * 本页四件事，顺序不能反：
 *   ⓪ 「一句话任务」→ 自动生成 spec → 生成脚本 → 守卫 → 执行 —— `POST /solve/generate`
 *   ① 静态守卫 + 沙箱执行（FR-5）—— `POST /solve/run`（只跑已有脚本）
 *   ② 查产物清单 —— `GET /artifacts/list`（执行完到底落了哪些文件）
 *   ③ 结果可视化出图（FR-6）—— `POST /figures/render`，每张图必须 300 dpi
 *
 * ⓪ 与 ① 的分工：①只执行**已经写好的** `script_path`；要「从任务描述开始」就走 ⓪——
 * 它自己生成 spec（落盘到 `artifacts/specs/<run_id>.json`）、生成脚本、跑守卫、再执行。
 * ⓪ 的判定有两个口径，**必须分开显示**：`run.ok`（脚本执行成不成）与 `dod.ok`（交付判据齐不齐），
 * 合并成一个绿灯会掩盖「跑通了但结果表/图没落齐」这种最常见的半成品。
 *
 * 守卫违规经统一错误体返回（error_code=SCRIPT_GUARD_VIOLATION，detail 含 violations），
 * 走 setErr → errorHtml，三要素照旧上屏。
 * 出图前先本地解析 figure_specs（不合法则不发请求）；dpi 非 300 的行标红。
 */
import { apiGet, apiPost, errorHtml } from "../core/api.js";
import {
  esc, iv, num, panel, qs, rawDetails, setBusy, setErr, setOut, stats, table, tag,
} from "../core/dom.js";

const FIG_SPEC_PLACEHOLDER =
  '[{"figure_id":"fig01","kind":"line","table":"summary","x":"t","y":"y"}]';

/** 读 #/problem 页上传后留下的会话态（同一标签页内有效）；没有就按「无附件纯计算题」处理。 */
function readUpload() {
  try {
    const raw = sessionStorage.getItem("mmagent.upload");
    const d = raw ? JSON.parse(raw) : null;
    return {
      profile_id: (d && d.profile_id) || "",
      dataFiles: d && Array.isArray(d.dataFiles) ? d.dataFiles : [],
    };
  } catch (_e) {
    return { profile_id: "", dataFiles: [] };
  }
}

/** `dod.missing` 的一条：后端给的是中文句子（如「无 `results/summary.csv`」「PNG dpi 不达标…」）
 *  就原样显示；只给裸路径时才补「缺」字（规格 §2.3 的措辞），避免出现「缺 无 …」这种双否。 */
function missingItem(m) {
  return /[\u4e00-\u9fa5]/.test(m) ? `<li>${esc(m)}</li>` : `<li>缺 <code>${esc(m)}</code></li>`;
}

export function render(root) {
  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">本页做什么</h3>
      <p class="muted card-note">先写一句话说明<strong>要做什么</strong>，由服务端自动生成 spec、脚本并跑完
        （<code>POST /solve/generate</code>）；已有脚本时也可以用下面第二张卡直接过<strong>静态守卫</strong>再<strong>沙箱执行</strong>（FR-5）。
        最后把结果表渲染成 <strong>300 dpi</strong> 配图（FR-6）。</p>
      <p class="muted card-note">产出：<code>results/*.csv</code> + <code>figures/*.png</code>（dpi=300）。
        下一步去 <a href="#/paper">#/paper</a> 装配论文骨架并把图表接进正文。</p>
    </article>

    ${panel("从「一句话任务」到「跑完」：自动生成 spec 并运行（POST /solve/generate）", `
      <form id="f-generate">
        <label class="field">任务描述 <code>problem</code>（必填）
          <textarea id="i-problem" rows="4" required
            placeholder="例如：对 att1.xls 的月度数据做趋势拟合，输出残差统计表与趋势图"></textarea>
        </label>
        <div class="grid">
          <label class="field">工作目录 <code>workdir</code>（可留空）
            <input id="i-gen_workdir" placeholder="留空则自动：artifacts/work/&lt;档案编号&gt;">
          </label>
          <label class="field">表头行 <code>header_row</code>（0-based，可留空）
            <input id="i-header_row" type="number" min="0" placeholder="留空则由模型判断">
          </label>
        </div>
        <p class="hint" id="d-upload-hint"></p>
        <div class="btn-row">
          <button type="submit" class="btn primary">生成并运行</button>
          <span class="hint">POST <code>/api/v1/solve/generate</code>　·　spec 自动落盘
            <code>artifacts/specs/&lt;run_id&gt;.json</code></span>
        </div>
      </form>`, "r-generate")}

    ${panel("代码守卫与沙箱执行（FR-5）", `
      <form id="f-solve">
        <label class="field">脚本路径 <code>script_path</code>
          <input id="i-script_path" placeholder="例如 artifacts/work/solve.py" required>
        </label>
        <div class="grid">
          <label class="field">工作目录 <code>workdir</code>
            <input id="i-workdir" placeholder="例如 artifacts/work" required>
          </label>
          <label class="field">超时秒数 <code>timeout_s</code>
            <input id="i-timeout_s" type="number" min="1" value="300">
          </label>
        </div>
        <div class="btn-row">
          <button type="submit" class="btn primary">守卫 + 执行</button>
          <button type="button" id="b-artifacts" class="btn">列出产物清单</button>
          <span class="hint">POST <code>/api/v1/solve/run</code>　·　GET <code>/api/v1/artifacts/list</code></span>
        </div>
      </form>`, "r-solve")}

    ${panel("结果可视化出图（FR-6）", `
      <form id="f-figures">
        <label class="field">结果表 <code>result_tables</code>（每行一项，写成 <code>表名=路径</code>；不写表名则取文件名去扩展名）
          <textarea id="i-result_tables" rows="3"
            placeholder="summary=artifacts/results/summary.csv&#10;artifacts/results/other.csv"></textarea>
        </label>
        <label class="field">图规格 <code>figure_specs</code>（JSON 数组；kind ∈ line/bar/scatter/hist）
          <textarea id="i-figure_specs" rows="4">${esc(FIG_SPEC_PLACEHOLDER)}</textarea>
        </label>
        <label class="field">输出目录 <code>out_dir</code>
          <input id="i-fig_out_dir" placeholder="例如 artifacts/figures" required>
        </label>
        <div class="btn-row">
          <button type="submit" class="btn primary">渲染图片</button>
          <span class="hint">POST <code>/api/v1/figures/render</code></span>
        </div>
      </form>`, "r-figures")}
  `;

  /* 附件来源：#/problem 页上传后写进 sessionStorage 的会话态（写不进去就按「无附件」）。 */
  const up = readUpload();
  const hint = document.getElementById("d-upload-hint");
  if (hint) {
    hint.textContent = up.dataFiles.length
      ? `附件 inputs：${up.dataFiles.length} 个（来自 #/problem 最近一次上传，档案编号 ${up.profile_id || "（无）"}）`
      : "附件 inputs：空列表（没在 #/problem 上传过，按纯计算题提交）";
  }

  const genForm = document.getElementById("f-generate");
  if (genForm) genForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const problem = iv("i-problem");
    if (!problem) {
      setOut("r-generate", `<div class="alert warn">任务描述 <code>problem</code> 是必填项
        （后端 <code>min_length=1</code>，去空白后为空直接 422）。本地先拦下，省一次无效请求。</div>`);
      return;
    }
    const body = { problem, inputs: up.dataFiles, max_attempts: 2 };
    if (iv("i-gen_workdir")) body.workdir = iv("i-gen_workdir");
    if (iv("i-header_row")) body.header_row = num(iv("i-header_row"), 0);
    if (up.profile_id) body.profile_id = up.profile_id;
    setBusy("r-generate", "正在生成 spec、生成脚本并执行（调模型，可能数分钟，请勿刷新）…");
    try {
      const d = await apiPost("/solve/generate", body);
      const run = d.run || {};
      const dod = d.dod || {};
      const missing = Array.isArray(dod.missing) ? dod.missing : [];
      const warns = d.warnings || [];
      setOut("r-generate", `
        <div class="alert ${d.ok ? "ok" : "err"}">总判定 <code>ok</code>：
          ${d.ok ? tag("ok", "ok") : tag("未通过", "err")}
          <span class="hint">口径 = <code>run.ok</code> 且 <code>dod.ok</code>；两个口径下面分开列，不合并成一个绿灯</span></div>
        <div class="alert ${run.ok ? "ok" : "err"}">run.ok：
          ${run.ok ? tag("脚本执行成功", "ok") : tag("脚本执行失败", "err")}
          <span class="hint">returncode ${esc(run.returncode)}　·　manifest 来源 ${esc(run.manifest_source)}</span></div>
        <div class="alert ${dod.ok ? "ok" : "err"}">dod.ok：
          ${dod.ok ? tag("交付判据齐备", "ok") : tag("交付判据缺项", "err")}
          ${missing.length ? `<ul>${missing.map(missingItem).join("")}</ul>`
            : `<span class="hint">missing 为空</span>`}</div>
        <p>自动生成的 spec：<code>${esc(d.spec_path)}</code>（服务端写出来的文件，不是人手填的）</p>
        <p>工作目录：<code>${esc(d.workdir)}</code>　·　脚本：<code>${esc(d.script_path)}</code></p>
        ${stats([["data_schema_status", d.data_schema_status], ["warnings", warns.length]])}
        ${warns.length ? `<h4>warnings</h4><ul>${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
        <h4>stdout（尾部）</h4><pre>${esc(run.stdout_tail || "（空）")}</pre>
        <h4>stderr（尾部）</h4><pre>${esc(run.stderr_tail || "（空）")}</pre>
        ${rawDetails(d, "solve/generate")}
      `);
    } catch (err) {
      /* 502 SCRIPT_GENERATION_FAILED 容易和「脚本有安全问题」混为一谈（那是 422
         SCRIPT_GUARD_VIOLATION），故额外给一行处置建议；三要素仍由 errorHtml 原样上屏
         （**trace_id 不许丢**，报障靠它定位）。 */
      const code = err && err.body ? err.body.error_code : "";
      setOut("r-generate", `${errorHtml(err)}${code === "SCRIPT_GENERATION_FAILED"
        ? `<div class="alert warn"><strong>这不是「你的脚本有安全问题」</strong>
            <p>安全问题（静态守卫违规）回的是 <code>SCRIPT_GUARD_VIOLATION</code>；本错误表示模型两次都没产出
              可执行的脚本。可换个说法重述任务，或把附件信息写具体些（表名 / 列名 / 想要的输出长什么样）后重试。</p></div>`
        : ""}`);
    }
  });

  const form = document.getElementById("f-solve");
  if (form) form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setBusy("r-solve", "正在做静态守卫并执行…");
    try {
      const d = await apiPost("/solve/run", {
        script_path: iv("i-script_path"),
        workdir: iv("i-workdir"),
        timeout_s: num(iv("i-timeout_s"), 300),
      });
      const run = d.run || d;
      const files = run.produced_files || [];
      const missing = run.manifest_missing || [];
      setOut("r-solve", `
        <div class="alert ${run.ok ? "ok" : "err"}">
          执行结论：${run.ok ? tag("ok", "ok") : tag("失败", "err")}
          ${run.timed_out ? tag("超时", "err") : ""}
        </div>
        ${stats([
          ["returncode", run.returncode],
          ["用时(秒) duration_s", run.duration_s],
          ["产物清单来源 manifest_source", run.manifest_source],
          ["产物数 produced_files", files.length],
          ["缺件 manifest_missing", missing.length],
        ])}
        <p>脚本路径：<code>${esc(d.script_path || iv("i-script_path"))}</code></p>
        <h4>产物清单 produced_files</h4>
        ${table(files.map((f) => ({ f })), [{ label: "相对路径", key: "f" }])}
        <h4>缺件 manifest_missing</h4>
        ${table(missing.map((f) => ({ f })), [{ label: "缺失项", key: "f" }])}
        <h4>stdout（尾部）</h4><pre>${esc(run.stdout_tail || "（空）")}</pre>
        <h4>stderr（尾部）</h4><pre>${esc(run.stderr_tail || "（空）")}</pre>
        ${rawDetails(d, "solve/run")}
      `);
    } catch (err) {
      setErr("r-solve", err);
    }
  });

  const btn = document.getElementById("b-artifacts");
  if (btn) btn.addEventListener("click", async () => {
    setBusy("r-solve", "正在查询产物清单…");
    try {
      const d = await apiGet("/artifacts/list" + qs({ limit: 50, offset: 0 }));
      setOut("r-solve", `
        ${stats([
          ["本次返回 count", d.count],
          ["过滤后总数 total", d.total],
        ])}
        ${table(d.rows, [
          { label: "id", key: "id" },
          { label: "ts", key: "ts" },
          { label: "kind", key: "kind" },
          { label: "path", key: "path" },
          { label: "profile_id", key: "profile_id" },
          { label: "version_id", key: "version_id" },
          { label: "producer", key: "producer" },
          { label: "model_tag", key: "model_tag" },
        ])}
        ${rawDetails(d, "artifacts/list")}
      `);
    } catch (err) {
      setErr("r-solve", err);
    }
  });

  const figForm = document.getElementById("f-figures");
  if (figForm) figForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    let specs;
    try {
      specs = JSON.parse(iv("i-figure_specs") || "[]");
    } catch (e) {
      setOut("r-figures", `<div class="alert err"><strong>figure_specs 不是合法 JSON</strong>
        <pre>${esc(String(e))}</pre><p class="muted">请修正后重试（此错误发生在本地解析阶段，未发出请求）。</p></div>`);
      return;
    }
    const tables = (iv("i-result_tables") || "")
      .split("\n").map((s) => s.trim()).filter(Boolean);
    setBusy("r-figures", "正在渲染…");
    try {
      const d = await apiPost("/figures/render", {
        result_tables: tables,
        figure_specs: specs,
        out_dir: iv("i-fig_out_dir"),
      });
      const figs = d.figures || [];
      const warns = d.warnings || [];
      setOut("r-figures", `
        <div class="alert ${d.dpi_ok ? "ok" : "err"}">
          dpi 校验：${d.dpi_ok ? tag("全部 300 dpi", "ok") : tag("存在非 300 dpi（已标红）", "err")}
        </div>
        <p>输出目录：<code>${esc(d.out_dir)}</code>；清单：<code>${esc(d.manifest_path)}</code></p>
        ${stats([["图数量 figures", figs.length], ["warnings", warns.length]])}
        <h4>图片清单</h4>
        ${table(figs, [
          { label: "figure_id", key: "figure_id" },
          { label: "kind", key: "kind" },
          { label: "dpi", key: "dpi", fmt: (v) => (v === 300 ? esc(v) : `<strong>${esc(v)}</strong>`) },
          { label: "source_table", key: "source_table" },
          { label: "path", key: "path" },
        ], (r) => (r.dpi === 300 ? "" : "dpi-bad"))}
        <h4>warnings</h4>
        ${warns.length
          ? `<ul>${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
          : `<p class="muted">（无）</p>`}
        ${rawDetails(d, "figures/render")}
      `);
    } catch (err) {
      setErr("r-figures", err);
    }
  });
}
