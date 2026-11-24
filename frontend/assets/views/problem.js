/* views/problem.js —— 阶段 01 题面与附件（#/problem）。
 *
 * 链路第 1 步，按作业顺序排：
 *   上传题面/附件（`POST /upload`，base64 JSON）→ 题面解析（FR-1）→ 附件体检（FR-2）
 *   → 结构化建档（FR-9 写入）→ 字段级溯源（FR-9 读取）。
 * 产出：子问题结构 + 结构化附件表 + 档案 v1；下一步去 #/plan 为每个子问题生成建模方法卡片。
 *
 * 第 35 轮改的是**入口重量**：原来「档案 id / 版本 / 数据集目录」三个框要人手填，
 * 现在点一次「选择文件」即可——档案编号由服务端生成（`P` + 年月日-时分秒），
 * 题面 PDF 的文字也由服务端抽好直接回填进「题面文本」。三个输入框随之删除。
 * 上传仍走 `core/api.js` 的 `apiUpload`——视图里不发任何网络原语，出网点仍只有那一个文件。
 *
 * 两处沿用重构前的裁定（不是随手放在本页）：
 * 1. 「查看所有」做成**本页内的面板**而不是再开一个页面：hash 路由锁死在 6 条 + 外壳首页，
 *    新增路由会直接判红；而「看全部真题档案」本身是题面阶段的延伸，放在同一张卡里语义也顺。
 *    （面板实现、筛选与预览已下沉到 core/archive_preview.js，本页只留按钮。）
 * 2. 筛选为什么必须交给后端：archive/list 支持 year/kind 参数；前端全量拉回来再本地过滤虽然也能跑，
 *    但那样「后端筛选」在代码里就看不见了，档案库长大后会先烂在带宽上。
 *
 * 档案版本链只追加、不改写；页面不做任何本地缓存或乐观合并。
 * 上传结果只往 `sessionStorage` 写一份**给 #/solve 用**的会话态（题面/编号/数据文件路径）。
 */
import { apiGet, apiPost, apiUpload, fileToB64 } from "../core/api.js";
import { openArchive, resetArchive } from "../core/archive_preview.js";
import { esc, iv, panel, qs, rawDetails, setBusy, setErr, setOut, stats, table } from "../core/dom.js";
import { bindProblemPanel, problemPanelHtml } from "../core/problem_panel.js";
import { bindProfileWrite, writeFormHtml } from "../core/profile_form.js";
import { bindTrace, traceHtml } from "../core/profile_trace.js";

/* 最近一次上传结果。**会话态**（`sessionStorage`）里也存一份：`i-dataset_dir` 已按规格删除后，
 * 切页或刷新再回来就没有第二处能补「数据集目录 / 档案编号」，只能靠它恢复。
 * `problem_text` 一并存，仅为回填「题面文本」框；题面仍以框里的内容为准（用户可能改过）。 */
let SAVED = {};
try {
  SAVED = JSON.parse(sessionStorage.getItem("mmagent.upload") || "{}") || {};
} catch (_e) {
  SAVED = {};   /* 会话态损坏或不可读：按「未上传」处理，不影响建页 */
}
const UP = {
  profile_id: SAVED.profile_id || "",
  dataset_dir: SAVED.dataset_dir || "",
  dataFiles: Array.isArray(SAVED.dataFiles) ? SAVED.dataFiles : [],
};

/** 上传/未上传的展示哨兵：`o-profile_id` 不可编辑，未上传时显示它而不是空串。 */
const NO_UPLOAD = "（未上传）";

/* ---------------------------------------------------------------- 视图 */

export function render(root) {
  resetArchive();

  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">本页做什么</h3>
      <p class="muted card-note">把「赛题」变成可计算的输入：<strong>点一次上传</strong>选好题面 PDF 与附件，
        服务端落盘并抽出题面文字（同时生成档案编号），再依次跑<strong>题面解析</strong>（子问题结构）、
        <strong>附件体检</strong>（字段数 / 口径冲突 / 脏单元格），最后用建档表单落成<strong>档案 v1</strong>（FR-9 写入）。</p>
      <p class="muted card-note">产出：子问题结构 + 结构化附件表 + 档案 v1。做完去
        <a href="#/plan">#/plan</a> 生成建模方法卡片。</p>
    </article>

    ${panel("① 上传题面与附件（POST /upload，base64 JSON）", `
      <label class="field">选择文件 <code>files</code>（可多选：题面 PDF + 附件）
        <input class="file-picker" type="file" id="i-files" multiple accept=".pdf,.xls,.xlsx">
      </label>
      <p class="hint">第一个 PDF 当题面；只收 <code>.pdf</code> / <code>.xls</code> / <code>.xlsx</code>
        ——其它扩展名本地拦下，不发请求。</p>
      <div class="btn-row">
        <button type="button" id="b-upload" class="btn primary upload-action">上传</button>
        <span class="hint">POST <code>/api/v1/upload</code>　·　落盘 <code>data/uploads/&lt;档案编号&gt;/</code></span>
      </div>
      <label class="field">题面文本 <code>problem_text</code>（上传成功后自动填入，可改）
        <textarea id="i-problem_text" rows="8"
          placeholder="上传题面 PDF 后自动填入；扫描件抽不到文字层时请人工转录后粘贴到这里">${esc(SAVED.problem_text || "")}</textarea>
      </label>
      <p>档案编号 <code>profile_id</code>：<code id="o-profile_id">${esc(UP.profile_id || NO_UPLOAD)}</code>
        <span class="hint">（服务端生成：字面 <code>P</code> + 年月日-时分秒；不可编辑）</span></p>
    `, "r-upload")}

    ${panel("赛题档案：读取 / 版本链 / 全量浏览（FR-9 · FR-14）", `
      <p class="hint">档案 id 取上方「档案编号」；版本固定 <code>last</code>（最新 adopted 版）。</p>
      <div class="btn-row">
        <button type="button" id="b-pread" class="btn primary">读取档案</button>
        <button type="button" id="b-pversions" class="btn">列出全部版本</button>
        <button type="button" id="b-archive-all" class="btn">查看所有</button>
        <span class="hint">GET <code>/api/v1/profile/{id}</code>　·　GET <code>/api/v1/profile/{id}/versions</code>　·　
          「查看所有」改用 GET <code>/api/v1/archive/summary</code> + GET <code>/api/v1/archive/list</code></span>
      </div>
    `, "r-profile")}

    ${panel("题面解析（FR-1）：题面原文 → 子问题结构", problemPanelHtml(), "r-parse")}

    ${panel("附件体检（FR-2）：附件目录 → 结构化附件表", `
      <form id="f-intake">
        <div class="grid">
          <label class="field">数据集目录 <code>dataset_dir</code>（取自上传结果）
            <input id="i-dataset_show" value="${esc(UP.dataset_dir || NO_UPLOAD)}" readonly>
          </label>
          <label class="field">体检产物目录 <code>out_dir</code>（默认 <code>artifacts/intake</code>）
            <input id="i-out_dir" placeholder="artifacts/intake" value="artifacts/intake">
          </label>
        </div>
        <div class="btn-row">
          <button type="submit" class="btn primary">运行体检</button>
          <span class="hint">POST <code>/api/v1/intake/run</code>　·　只认 <code>.xls</code> / <code>.xlsx</code></span>
        </div>
      </form>`, "r-intake")}

    ${writeFormHtml()}

    ${panel("字段级溯源（FR-9 读取）：每个字段是谁写的、来自哪一版", traceHtml(), "r-trace-wrap")}
  `;

  const upBtn = document.getElementById("b-upload");
  if (upBtn) upBtn.addEventListener("click", async () => {
    const input = document.getElementById("i-files");
    const picked = input && input.files ? Array.from(input.files) : [];
    if (!picked.length) {
      setOut("r-upload", `<div class="alert warn">请先点「选择文件」选好题面 PDF 与附件。</div>`);
      return;
    }
    const bad = picked.filter((f) => !/\.(pdf|xls|xlsx)$/i.test(f.name)).map((f) => f.name);
    if (bad.length) {
      setOut("r-upload", `<div class="alert warn"><strong>扩展名不在白名单内，本次未发出请求</strong>
        <p>只收 <code>.pdf</code> / <code>.xls</code> / <code>.xlsx</code>；已拦下：
          ${bad.map((n) => `<code>${esc(n)}</code>`).join("、")}</p></div>`);
      return;
    }
    /* 题面必须是 PDF：只选 Excel 时后端仍会 200（xls 被冠以 problem_file、problem_text 为空、零 warning），
       用户会拿到「上传成功但没有题面」的状态。体验层先拦下（后端另有硬校验，curl 绕过也躲不过）。 */
    const isPdf = (f) => /\.pdf$/i.test(f.name);
    if (!picked.some(isPdf)) {
      setOut("r-upload", `<div class="alert warn"><strong>题面必须是 PDF，本次未发出请求</strong>
        <p>你只选了 Excel 附件（${picked.map((f) => `<code>${esc(f.name)}</code>`).join("、")}）。
          请把题面 PDF 一起选上：题面文字由 PDF 抽出，Excel 只做数值体检。</p></div>`);
      return;
    }
    /* 大小预检（与后端 MAX_FILE_BYTES 20 MB / MAX_TOTAL_BYTES 40 MB 同口径）。
       不预检的话 FileReader 会先把 60 MB 整个读进内存再 base64 编码，然后才被后端 413 打回。 */
    const MB = 1024 * 1024;
    /* 大小要能看出「超了多少」：20 MB + 1 字节若只写「20.0 MB」，用户会问「这不是没超吗」。
       故一律附字节数，且「与上限相差 < 0.05 MB」时再补一句「超 N 字节」。 */
    const mb = (n, limit) => {
      const over = n - limit;
      const bytes = n.toLocaleString("en-US");
      return `${(n / MB).toFixed(1)} MB（${bytes} 字节${over > 0 && over < 0.05 * MB ? `，超 ${over} 字节` : ""}）`;
    };
    const totalBytes = picked.reduce((s, f) => s + (f.size || 0), 0);
    const tooBig = picked.filter((f) => f.size > 20 * MB);
    if (tooBig.length || totalBytes > 40 * MB) {
      setOut("r-upload", `<div class="alert warn"><strong>文件太大，本次未发出请求</strong>
        <p>单文件上限 20 MB、合计上限 40 MB（本次合计 ${esc(mb(totalBytes, 40 * MB))}）。</p>
        ${tooBig.length ? `<p>超限文件：
          ${tooBig.map((f) => `<code>${esc(f.name)}</code>（${esc(mb(f.size, 20 * MB))}）`).join("、")}</p>` : ""}</div>`);
      return;
    }
    /* 题面取「第一个 PDF」：后端缺省拿 files[0] 当题面，故把 PDF 排到最前，
       否则先选中的 .xls 会被当题面（problem_text 会回落成空）。 */
    const ordered = picked.filter(isPdf).concat(picked.filter((f) => !isPdf(f)));
    setBusy("r-upload", "正在上传并抽取题面文字…");
    upBtn.disabled = true;
    try {
      const files = [];
      for (const f of ordered) files.push({ name: f.name, b64: await fileToB64(f) });
      const d = await apiUpload("/upload", files);
      const list = d.files || [];
      const warns = d.warnings || [];
      UP.profile_id = d.profile_id || "";
      UP.dataset_dir = d.dataset_dir || "";
      UP.dataFiles = list.filter((x) => x.kind === "data")
        .map((x) => `${UP.dataset_dir}/${x.saved_as || x.name}`);
      const textBox = document.getElementById("i-problem_text");
      if (textBox) textBox.value = d.problem_text || "";
      const pidBox = document.getElementById("o-profile_id");
      if (pidBox) pidBox.textContent = UP.profile_id || "（后端未返回）";
      const dsBox = document.getElementById("i-dataset_show");
      if (dsBox) dsBox.value = UP.dataset_dir || "（后端未返回）";
      /* 交给 #/solve：题面/编号/数据文件路径（同一标签页内有效；写不进去不阻断上传）。 */
      try {
        sessionStorage.setItem("mmagent.upload", JSON.stringify({
          profile_id: UP.profile_id, dataset_dir: UP.dataset_dir, dataFiles: UP.dataFiles,
          problem_text: d.problem_text || "",
        }));
      } catch (_e) { /* 隐私模式禁止写 sessionStorage：忽略 */ }
      syncParseInputs(true);   /* 上传成功：强制回填（明确要覆盖） */
      setOut("r-upload", `
        <div class="alert ok">上传完成：题面文字已填入下方「题面文本」框（可改），档案编号已生成。</div>
        ${stats([
          ["档案编号 profile_id", d.profile_id],
          ["数据目录 dataset_dir", d.dataset_dir],
          ["题面文件 problem_file", d.problem_file],
          ["题面字符数", (d.problem_text || "").length],
          ["warnings", warns.length],
        ])}
        <h4>文件清单 files</h4>
        ${table(list, [
          { label: "name", key: "name" },
          { label: "kind", key: "kind" },
          { label: "bytes", key: "bytes" },
          { label: "text_chars", key: "text_chars" },
          { label: "text_layer", key: "text_layer" },
        ])}
        <h4>warnings</h4>
        ${warns.length ? `<ul>${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
          : `<p class="muted">（无）</p>`}
        ${warns.length ? `<div class="alert warn">上面 ${esc(warns.length)} 条要人工看一眼：
          扫描件需手工转录题面；附件 PDF 只抽文本、不做数值体检（体检仅支持 .xls/.xlsx）。</div>` : ""}
        ${rawDetails(d, "upload")}
      `);
    } catch (err) {
      /* 本地读盘失败是普通 Error（没有 kind），不进 errorHtml 那套 HTTP 三要素。 */
      if (err && err.kind) setErr("r-upload", err);
      else setOut("r-upload", `<div class="alert err">${esc((err && err.message) || String(err))}</div>`);
    } finally {
      upBtn.disabled = false;
    }
  });

  /** 档案编号：上传过就用手里的，否则读展示位（`NO_UPLOAD` 视作没有）。 */
  function profileId() {
    if (UP.profile_id) return UP.profile_id;
    const box = document.getElementById("o-profile_id");
    const t = box ? box.textContent.trim() : "";
    return t === NO_UPLOAD ? "" : t;
  }

  /* 解析卡的两个输入框由 core/problem_panel.js 持有（本视图不复制它的 id，`test_14` 只扫 view 模板），
     故按结构定位。`force` 区分两种意图，**不许无条件覆盖**：
     - 上传成功 → `force=true`：上传就是明确要回填，覆盖是对的；
     - 点「解析题面」→ `force=false`：只在框还空着时补，否则会把用户粘进来的赛题原文、
       人工订正的题面、手填的关联档案 id 一并擦掉（「粘文本 → 解析」这条主路径会被打断）。 */
  function syncParseInputs(force) {
    const box = document.querySelector("#pr-box");
    if (!box) return;
    const text = box.querySelector("textarea");
    const pid = box.querySelector("input");
    if (text && (force || !text.value.trim())) text.value = iv("i-problem_text");
    if (pid && (force || !pid.value.trim())) pid.value = profileId();
  }
  const parseBtn = document.querySelector("#pr-box button");
  if (parseBtn) parseBtn.addEventListener("click", () => syncParseInputs(false));

  const readBtn = document.getElementById("b-pread");
  if (readBtn) readBtn.addEventListener("click", async () => {
    const id = profileId();
    if (!id) { setOut("r-profile", `<div class="alert warn">请先在上方上传，拿到档案编号。</div>`); return; }
    setBusy("r-profile", "正在读取档案…");
    try {
      const d = await apiGet(`/profile/${encodeURIComponent(id)}` + qs({ version: "last" }));
      setOut("r-profile", `
        ${stats([
          ["profile_id", d.profile_id],
          ["version_id", d.version_id],
          ["status", d.status],
          ["actor", d.actor],
          ["supersedes", d.supersedes === null ? "（无）" : d.supersedes],
        ])}
        <p>创建时间 created_at：<code>${esc(d.created_at)}</code></p>
        <h4>档案内容 patch</h4>
        <pre>${esc(JSON.stringify(d.patch || {}, null, 2))}</pre>
        ${rawDetails(d, "profile/{id}")}
      `);
    } catch (err) {
      setErr("r-profile", err);
    }
  });

  const verBtn = document.getElementById("b-pversions");
  if (verBtn) verBtn.addEventListener("click", async () => {
    const id = profileId();
    if (!id) { setOut("r-profile", `<div class="alert warn">请先在上方上传，拿到档案编号。</div>`); return; }
    setBusy("r-profile", "正在读取版本链…");
    try {
      const d = await apiGet(`/profile/${encodeURIComponent(id)}/versions`);
      const vs = d.versions || [];
      setOut("r-profile", `
        <p>档案 <code>${esc(d.profile_id)}</code> 共 <strong>${vs.length}</strong> 个版本（按 version_id 降序）。</p>
        ${table(vs, [
          { label: "version_id", key: "version_id" },
          { label: "status", key: "status" },
          { label: "actor", key: "actor" },
          { label: "created_at", key: "created_at" },
          { label: "supersedes", key: "supersedes" },
        ])}
        <p class="muted">要在建档/读取里用到某一版，把它的 <code>version_id</code> 记下来，
          溯源卡（下方）可按字段查每一版是谁写的。</p>
        ${rawDetails(d, "profile/{id}/versions")}
      `);
    } catch (err) {
      setErr("r-profile", err);
    }
  });

  const allBtn = document.getElementById("b-archive-all");
  if (allBtn) allBtn.addEventListener("click", () => { openArchive("r-profile"); });

  const intakeForm = document.getElementById("f-intake");
  if (intakeForm) intakeForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!UP.dataset_dir) {
      setOut("r-intake", `<div class="alert warn">还没有附件目录：请先在上方上传附件（<code>.xls</code> / <code>.xlsx</code>）。</div>`);
      return;
    }
    setBusy("r-intake", "正在体检…");
    try {
      const d = await apiPost("/intake/run", {
        dataset_dir: UP.dataset_dir,
        out_dir: iv("i-out_dir") || "artifacts/intake",
      });
      const skipped = d.skipped_files || [];
      setOut("r-intake", `
        ${stats([
          ["字段总数 field_count", d.field_count],
          ["口径冲突 conflict_count", d.conflict_count],
          ["未映射列 unmapped_count", d.unmapped_count],
          ["脏单元格 dirty_cell_count", d.dirty_cell_count],
          ["跳过文件 skipped_files", skipped.length],
        ])}
        <p>产物目录：<code>${esc(d.out_dir)}</code></p>
        <h4>各表行数（三口径）</h4>
        ${table(d.tables, [
          { label: "table_id", key: "table_id" },
          { label: "文件", key: "source_file" },
          { label: "sheet", key: "sheet_name" },
          { label: "物理行", key: "physical_rows" },
          { label: "有效行", key: "effective_rows" },
          { label: "空行", key: "blank_rows" },
          { label: "字段数", key: "field_count" },
        ])}
        <h4>口径冲突清单</h4>
        ${table(d.conflicts, [
          { label: "conflict_id", key: "conflict_id" },
          { label: "类型", key: "conflict_type" },
          { label: "字段", key: "field_base" },
          { label: "建议口径", key: "suggested_caliber" },
          { label: "说明", key: "message" },
        ])}
        <h4>输入白名单跳过</h4>
        ${table(skipped, [
          { label: "文件", key: "file" },
          { label: "原因", key: "reason" },
        ])}
        ${rawDetails(d, "intake/run")}
      `);
    } catch (err) {
      setErr("r-intake", err);
    }
  });

  /* 建档写入的 id 全在 core/profile_form.js 里；本视图只交出 profile_id 的读取方式。 */
  bindProfileWrite(() => profileId());
  /* 字段级溯源（FR-9，只读）：id 全在 core/profile_trace.js 里，本视图只交出 id 读取方式。 */
  bindTrace(() => profileId());
  /* 题面解析（FR-1）：id 全在 core/problem_panel.js 里；该接口不写档案，结果只上屏。 */
  bindProblemPanel();
}
