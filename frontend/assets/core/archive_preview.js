/* core/archive_preview.js —— 档案库全量浏览（「查看所有」面板，task-003 起建，波次 3 自
 * `views/profile.js:20-294` **整块纯移动**到此）。
 *
 * 为什么单独成文件：`views/problem.js` 有 400 行机检上限，而波次 3 要在 `#/problem` 上再接
 * `POST /problem/parse` 的入口；归档浏览与档案版本链是两块互不相干的功能，搬出来才放得下。
 * 搬迁口径：函数名、`a-*` id、文案与判定逻辑**逐行未改**，只补两个对外入口。
 *
 * 三条容易被悄悄做丢的性质（`tests/test_frontend_archive_view.py` 钉住）：
 * 1. 「查看所有」必须是 `#/problem` 页**内**的面板，不能变成第 7 个页面（hash 路由锁死 6 条）。
 * 2. 筛选必须交给后端（`GET archive/list?year=&kind=`），不能在 JS 里全量过滤。
 *    （第 32 轮：本文件里端点路径一律**不写前导斜杠**。`_api_tokens()` 是纯文本正则，
 *    注释里带斜杠的路径会被当成真实调用点、伪造覆盖证据并让 `test_8c` 的反向检查失效。
 *    已在 D-104 于收集器层根治（`_strip_comment_lines`），此处仍不写，双保险。）
 * 3. 档案自带的 `source_url` 是外站地址，只能当纯文本显示，绝不进 href/src（零外站机检）。
 *
 * 全部 `a-` 前缀 id 由本模块自己持有（`test_14` 只扫 views）。
 */
import { API, apiGet } from "./api.js";
import { esc, qs, rawDetails, setBusy, setErr, setOut, stats, table } from "./dom.js";

/* ------------------------------------------------------------ 展示纯函数 */

/** 档案类型 → 中文（未登记的类型原样显示，不隐藏）。 */
const KIND_CN = { exam_problem: "真题", paper: "论文" };
const kindCn = (k) => KIND_CN[k] || k || "—";

/** 字节数 → 人类可读（档案里最大的是几十 MB 的扫描件）。 */
function humanBytes(n) {
  const b = Number(n);
  if (!Number.isFinite(b) || b <= 0) return "—";
  if (b >= 1024 * 1024) return `${(b / 1024 / 1024).toFixed(2)} MB`;
  return `${Math.max(1, Math.round(b / 1024))} KB`;
}

/** 选中态用 primary、未选中用 ghost——复用既有按钮样式，不为筛选另造一套。 */
const btnCls = (on) => `btn ${on ? "primary" : "ghost"}`;

/* -------------------------------------------------------------- 面板状态 */

/* 切换路由时 render() 会重置，避免上一次的筛选条件 / 预览页残留到下一次进入。 */
let curYear = "";
let curKind = "";
let listRows = [];
let pvRow = null;
let pvPage = 1;

/* ---------------------------------------------------------------- 统计卡 */

function summaryHtml(sm) {
  const byKind = sm.by_kind || {};
  const byYear = Array.isArray(sm.by_year) ? sm.by_year : [];
  const years = Array.isArray(sm.years) ? sm.years : [];
  const missing = Number(sm.file_missing) || 0;
  const missIds = Array.isArray(sm.file_missing_ids) ? sm.file_missing_ids : [];
  /* 附件是档案主体之外的另一层：69 个附件全丢时 file_missing 仍可能报 0。
   * 只看主体就会给出「0 缺失」的假绿灯，所以两种缺失分别统计、分别告警。 */
  const attTotal = Number(sm.attachment_total) || 0;
  const attMissing = Number(sm.attachment_missing) || 0;
  const attIds = Array.isArray(sm.attachment_missing_ids) ? sm.attachment_missing_ids : [];
  /* 论文**页级**是第三层：目录在、少一页时 file_missing 与附件都还是 0。
   * 1301 页里少一页，只看前两层会得到「齐备」的结论——所以单列一层数。 */
  const pgTotal = Number(sm.page_total) || 0;
  const pgMissing = Number(sm.page_missing) || 0;
  const pgIds = Array.isArray(sm.page_missing_ids) ? sm.page_missing_ids : [];

  /* 三层缺一都必须出 warn（主体 / 论文页 / 附件）：静默显示 0 或只灰一行，
   * 用户会以为「就是没有这篇」。论文页这层最容易漏——目录在、少一页时前两个数都还是 0。 */
  const incomplete = missing > 0 || pgMissing > 0
    || (Number(sm.attachment_missing) || 0) > 0;

  const parts = [];
  if (missing > 0) {
    parts.push(`<p><strong>档案主体缺失 ${esc(missing)} 条</strong></p>
      <p>库里有记录但磁盘上找不到文件，可能素材未抓取或被清理。受影响 archive_id：
        <code>${esc(missIds.join("、") || "（后端未给出 id）")}</code></p>`);
  }
  if (pgMissing > 0) {
    parts.push(`<p><strong>论文页缺失 ${esc(pgMissing)} 页</strong>（共声明 ${esc(pgTotal)} 页）</p>
      <p>论文目录还在但页图少了，逐页浏览会在缺页处 404。受影响页：
        <code>${esc(pgIds.join("、") || "（后端未给出 id）")}</code></p>`);
  }
  if (attMissing > 0) {
    parts.push(`<p><strong>附件缺失 ${esc(attMissing)} 项</strong></p>
      <p>库里有记录但磁盘上找不到附件（例如 2025 C 题的唯一数据附件），可能素材未抓取或被清理。受影响项：
        <code>${esc(attIds.join("、") || "（后端未给出 id）")}</code></p>`);
  }

  const missAlert = incomplete
    ? `<div class="alert warn"><strong>素材不完整</strong>
        ${parts.join("")}
        <p>下列相关行会整行标灰；补齐前 <code>/archive/file</code> 与 <code>/archive/page</code>
          会返回 <code>ARCHIVE_FILE_MISSING</code>。</p>
      </div>`
    : `<div class="alert ok"><strong>文件齐备</strong>
        <p>本次统计下主体 <code>file_missing = 0</code>、论文页 <code>page_missing = 0</code>
          （共 ${esc(pgTotal)} 页）、附件 <code>attachment_missing = 0</code>：
          每条档案的主体、页图与附件在磁盘上都能找到。</p></div>`;

  const yearTable = table(byYear, [
    { label: "年份", key: "year" },
    { label: "合计", key: "total" },
    { label: "真题", key: "exam_problem" },
    { label: "论文", key: "paper" },
  ]);

  const yearBtns = [`<button type="button" class="${btnCls(curYear === "")}" data-year="">全部年份</button>`]
    .concat(years.map((y) => `<button type="button" class="${btnCls(curYear === String(y))}" data-year="${esc(y)}">${esc(y)}</button>`))
    .join("");

  const kindBtns = [["", "全部类型"], ["exam_problem", "真题"], ["paper", "论文"]]
    .map(([k, label]) => `<button type="button" class="${btnCls(curKind === k)}" data-kind="${esc(k)}">${esc(label)}</button>`)
    .join("");

  return `
    ${stats([
      ["档案总数", sm.total],
      ["真题", byKind.exam_problem],
      ["论文", byKind.paper],
      ["文件缺失", missing],
      /* 论文页是第三层：目录在、少一页时上面两个数都还是 0，所以必须有自己的一格。 */
      ["论文页缺失", pgMissing],
      /* 附件总数是**项数**不是档案数：真题下可挂多个附件，别读成 69 个档案。 */
      ["附件项数", attTotal],
      ["附件缺失", attMissing],
    ])}
    <h4>按年份分组</h4>
    ${yearTable}
    ${missAlert}
    <p class="hint">清单来源：<code>${esc(sm.manifest_path || "（后端未提供 manifest 路径）")}</code>　·
      manifest ${sm.manifest_present ? "存在" : "缺失"}。</p>
    <h4>筛选（条件发给后端，不在前端全量过滤）</h4>
    <div class="btn-row" id="a-filters">
      <span class="hint">年份</span>${yearBtns}
      <span class="hint">类型</span>${kindBtns}
    </div>
    <div id="a-list"></div>
    <div id="a-preview"></div>
  `;
}

/* ------------------------------------------------------------ 列表与筛选 */

function columns() {
  return [
    { label: "年份", key: "year" },
    { label: "类型", key: "kind", fmt: (v) => esc(kindCn(v)) },
    { label: "题号", key: "problem_code", fmt: (v) => (v ? esc(v) : "—") },
    { label: "编号", key: "tag", fmt: (v) => (v ? esc(v) : "—") },
    { label: "标题", key: "title" },
    { label: "页数", key: "page_count", fmt: (v) => (v ? esc(v) : "—") },
    { label: "大小", key: "bytes", fmt: (v) => esc(humanBytes(v)) },
    {
      label: "文件在否",
      key: "file_present",
      fmt: (v) => (v ? `<span class="tag ok">在</span>` : `<span class="tag warn">缺失</span>`),
    },
    {
      /* 页级缺失是第三层：论文目录在、少一页时「文件在否」仍是「在」。 */
      label: "缺页",
      key: "pages_missing",
      fmt: (v) => (Number(v) > 0 ? `<span class="tag err">${esc(v)}</span>` : "—"),
    },
    {
      /* 附件缺失是主体之外的另一层：0 时用「—」压掉噪声，>0 才给红数字。 */
      label: "附件缺失",
      key: "attachments_missing",
      fmt: (v) => (Number(v) > 0 ? `<span class="tag err">${esc(v)}</span>` : "—"),
    },
    {
      label: "操作",
      key: "archive_id",
      fmt: (_v, r) => `<button type="button" class="btn ghost" data-archive="${esc(r.archive_id)}">查看</button>`,
    },
  ];
}

/** 只改 class，不重建 DOM——重建会把已绑定的监听一起丢掉。 */
function paintFilters() {
  document.querySelectorAll("#a-filters [data-year]").forEach((b) => {
    b.className = btnCls((b.getAttribute("data-year") || "") === curYear);
  });
  document.querySelectorAll("#a-filters [data-kind]").forEach((b) => {
    b.className = btnCls((b.getAttribute("data-kind") || "") === curKind);
  });
}

function bindFilters() {
  document.querySelectorAll("#a-filters [data-year]").forEach((b) => {
    b.addEventListener("click", () => {
      curYear = b.getAttribute("data-year") || "";
      paintFilters();
      loadList();
    });
  });
  document.querySelectorAll("#a-filters [data-kind]").forEach((b) => {
    b.addEventListener("click", () => {
      curKind = b.getAttribute("data-kind") || "";
      paintFilters();
      loadList();
    });
  });
}

async function loadList() {
  setBusy("a-list", "正在按当前筛选加载档案…");
  try {
    /* limit=200：库内全量 33 条，一次取完即可；不做无限滚动。 */
    const d = await apiGet("/archive/list" + qs({ year: curYear, kind: curKind, limit: 200, offset: 0 }));
    listRows = Array.isArray(d.rows) ? d.rows : [];
    const scope = `${curYear ? `${curYear} 年` : "全部年份"} · ${curKind ? kindCn(curKind) : "全部类型"}`;
    setOut("a-list", `
      <p class="hint">筛选：${esc(scope)}　·　本次返回 <strong>${esc(d.count)}</strong> 条，
        库内符合条件合计 <strong>${esc(d.total)}</strong> 条。</p>
      ${table(listRows, columns(), (r) => (
        r.file_present && !(Number(r.attachments_missing) > 0)
          && !(Number(r.pages_missing) > 0) ? "" : "row-missing"
      ))}
    `);
    bindViewButtons();
  } catch (err) {
    setErr("a-list", err);
  }
}

function bindViewButtons() {
  document.querySelectorAll("#a-list [data-archive]").forEach((b) => {
    b.addEventListener("click", () => {
      const id = b.getAttribute("data-archive");
      const row = listRows.find((x) => x.archive_id === id);
      if (!row) return;
      pvRow = row;
      pvPage = 1;
      renderPreview();
    });
  });
}

/* ------------------------------------------------------------ 预览面板 */

function examBody(r, aid, closeBtn) {
  return `<div class="btn-row">
      <a class="btn primary" href="${API}/archive/file/${aid}" target="_blank" rel="noopener">新标签打开 PDF</a>
      ${closeBtn}
    </div>
    <p class="hint">下面直接内嵌同源 PDF（本机 <code>${API}/archive/file/{archive_id}</code>）；
      浏览器不支持内嵌时，点上面的按钮。</p>
    <iframe class="archive-frame" src="${API}/archive/file/${aid}" title="真题 PDF：${esc(r.archive_id)}"></iframe>`;
}

function paperBody(r, aid, closeBtn) {
  const total = Math.max(1, Number(r.page_count) || 1);
  if (pvPage > total) pvPage = total;
  if (pvPage < 1) pvPage = 1;
  return `<div class="btn-row">
      <button type="button" id="a-pv-prev" class="btn"${pvPage <= 1 ? " disabled" : ""}>上一页</button>
      <span class="hint" id="a-pv-no">第 <strong>${pvPage}</strong> / ${total} 页</span>
      <button type="button" id="a-pv-next" class="btn"${pvPage >= total ? " disabled" : ""}>下一页</button>
      ${closeBtn}
    </div>
    <p class="hint">逐页取本机 <code>${API}/archive/page/{archive_id}/{page_no}</code> 的 JPEG：
      只请求当前页，不为这 ${total} 页预加载全部图片。</p>
    <img id="a-pv-img" class="archive-page" src="${API}/archive/page/${aid}/${pvPage}"
      alt="${esc(r.title || r.archive_id)} 第 ${pvPage} 页">`;
}

function renderPreview() {
  const r = pvRow;
  if (!r) { setOut("a-preview", ""); return; }
  const aid = encodeURIComponent(r.archive_id);
  const closeBtn = `<button type="button" id="a-pv-close" class="btn ghost">收起预览</button>`;
  const body = r.kind === "paper" ? paperBody(r, aid, closeBtn) : examBody(r, aid, closeBtn);

  /* source_url 是外站地址，只作纯文本展示，绝不进 href/src（零外站机检）。 */
  setOut("a-preview", `<div class="archive-pv">
    <h4>预览：${esc(r.title || r.archive_id)}</h4>
    <p class="hint">archive_id <code>${esc(r.archive_id)}</code>　·　${esc(kindCn(r.kind))}　·　${
      r.file_present ? "文件在磁盘上" : "文件缺失（后端会返回 ARCHIVE_FILE_MISSING）"}${
      Number(r.pages_missing) > 0 ? `　·　缺 ${esc(r.pages_missing)} 页` : ""}${
      Number(r.attachments_missing) > 0 ? `　·　附件缺失 ${esc(r.attachments_missing)} 项` : ""}</p>
    ${body}
    <p class="hint">来源（仅文本，不跳转）：<code>${esc(r.source_url || "（无）")}</code></p>
    <p class="hint">本地路径：<code>${esc(r.local_path || "（无）")}</code></p>
    ${rawDetails(r, "archive/list 行")}
  </div>`);
  bindPreview();
}

function bindPreview() {
  const close = document.getElementById("a-pv-close");
  if (close) close.addEventListener("click", () => { pvRow = null; pvPage = 1; setOut("a-preview", ""); });
  const prev = document.getElementById("a-pv-prev");
  if (prev) prev.addEventListener("click", () => { pvPage -= 1; renderPreview(); });
  const next = document.getElementById("a-pv-next");
  if (next) next.addEventListener("click", () => { pvPage += 1; renderPreview(); });
}

/* ---------------------------------------------------------------- 对外入口 */

/** 切路由 / 重开面板时清空上一次的筛选条件与预览页（原 `render` 首行的重置）。 */
export function resetArchive() {
  curYear = ""; curKind = ""; listRows = []; pvRow = null; pvPage = 1;
}

/** 「查看所有」入口：拉统计 → 铺面板 → 绑筛选 → 拉列表（原 `b-archive-all` 处理器体）。 */
export async function openArchive(resultId) {
  resetArchive();
  setBusy(resultId, "正在读取档案库统计…");
  try {
    const sm = await apiGet("/archive/summary");
    setOut(resultId, summaryHtml(sm));
    bindFilters();
    await loadList();
  } catch (err) {
    setErr(resultId, err);
  }
}
