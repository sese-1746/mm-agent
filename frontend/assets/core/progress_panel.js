/* core/progress_panel.js —— FR-8 进度守卫（`GET /progress/status`，**只读**）。
 *
 * 挂在 `#/deliver` 页：该页心智就是「跑到哪了」，进度守卫回答同一个问题；另开第 7 页会被
 * 「恰好 6 个 hash」机检判红。不编中文映射表：`warnings[*].message/action` 是后端按阈值算好的
 * 人读句，前端再包一层措辞就与 CLI 不一致；本模块只对**顶层键**加标题。
 *
 * 三个必须照真实键写的地方（`.agent-runs/2026-10-21/task-006/response-keys.md`）：
 * 1. 无 `deadline` 时 `remaining_hours=null` 且 `deadline_missing=true`——显示「未设截止」，
 *    **不许**画成 `0 h`（那是在说「正好到点」）；
 * 2. `stage` 取不到就是 `null`，后端明确「不猜、不填默认值」——这里也不补 INTAKE；
 * 3. `actionable_ratio` 无预警时定义为 `1.0`，不能读成「一切正常」。
 * 全部 `p-` 前缀 id 由本模块自己持有（`test_14` 只扫 views）。
 */
import { apiGet, errorHtml } from "./api.js";
import { esc, qs, setBusy, setOut } from "./dom.js";

/** `datetime-local` 的本地时间 → 带 `Z` 的 UTC ISO。
 * 后端 `_parse_ts` 把 **naive 时间当 UTC**（`progress_tool.py:108`），
 * 若把本地钟点原样送过去，东八区会平白多出 8 小时——故这里先换算。 */
function toIsoUtc(local) {
  if (!local) return "";
  const d = new Date(local);                       // 无时区后缀 → 按浏览器本地时区解析
  if (Number.isNaN(d.getTime())) return local;     // 解析不了就原样送，让后端报 422
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** 倒计时块（主视觉）。负值 = 已过截止。 */
function countdownHtml(d) {
  const raw = d.remaining_hours;
  if (d.deadline_missing === true || raw === null || raw === undefined) {
    return `<div class="alert warn"><strong>未设截止时间 deadline</strong>
      <p>没有 deadline 时<strong>时间类规则全部不触发</strong>（倒计时、超时、不做清单都算不出来）。</p></div>`;
  }
  const h = Number(raw);
  const over = h < 0;
  const idle = d.idle_hours === null ? "—" : Number(d.idle_hours).toFixed(1);
  /* 只用既有类名（`.stat-val` / `.tag` / `.alert`）：style.css 不在本波允许改的文件里，故不造新类。 */
  return `<div class="stat-row">
      <div class="stat"><span class="stat-val">${esc(h.toFixed(1))}</span><span class="stat-key">剩余小时 remaining_hours</span></div>
      <div class="stat"><span class="stat-val">${esc(idle)}</span><span class="stat-key">无产出小时 idle_hours</span></div>
      <div class="stat"><span class="stat-val">${esc(d.stage === null ? "—" : d.stage)}</span><span class="stat-key">阶段 stage</span></div>
    </div>
    ${over ? `<div class="alert err"><strong>已超时 ${esc(Math.abs(h).toFixed(1))} h</strong>
      <p>剩余小时为负值 = 已过 <code>deadline</code>，此时「不做清单」按超时口径给出。</p></div>` : ""}`;
}

/** 无产出预警：`level` 当前恒为 warn，但仍按值取样式，不写死。 */
function warningsHtml(list) {
  const rows = Array.isArray(list) ? list : [];
  if (!rows.length) return `<p class="muted">（无预警 warnings 为空）</p>`;
  return `<ul>${rows.map((w) => `<li><span class="tag ${esc(w.level === "error" ? "err" : "warn")}">${esc(w.level || "warn")}</span>
      <code>${esc(w.code)}</code> ${esc(w.message)} <p class="hint">建议动作：${esc(w.action)}</p></li>`).join("")}</ul>`;
}

/** 不做清单：三项三列原样展示（`when` 恒为 `remaining<=4h`，当标签用）。 */
function notDoHtml(d) {
  const rows = Array.isArray(d.not_do_list) ? d.not_do_list : [];
  if (!rows.length) return `<p class="muted">（不做清单为空：剩余时间还没进 4 小时内，或未设截止）</p>`;
  return `<figure><table role="grid"><thead><tr><th>做什么不做 item</th><th>为什么不做 why</th><th>触发条件 when</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${esc(r.item)}</td><td>${esc(r.why)}</td><td><code>${esc(r.when)}</code></td></tr>`).join("")}</tbody></table></figure>
    ${d.not_do_source ? `<p class="hint">口径依据：${esc(d.not_do_source)}</p>` : ""}`;
}

/** 最短可交版本：`source` + `chapters` + `figures`。 */
function minViableHtml(mv) {
  if (!mv || typeof mv !== "object") return `<p class="muted">（最短可交版本为空：剩余时间还没进 6 小时内）</p>`;
  const li = (xs) => (Array.isArray(xs) ? xs : []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>（空）</li>";
  return `<details class="raw"><summary>最短可交版本 min_viable</summary>
      <p class="hint">${esc(mv.source || "（后端未给口径来源）")}</p>
      <h4>P0 章节 chapters</h4><ul>${li(mv.chapters)}</ul>
      <h4>P0 图 figures</h4><ul>${li(mv.figures)}</ul></details>`;
}

/** 整页结果。两个 `unfiltered_*` 只在「过滤后不等于全库」时上屏，用来解释空列表。 */
function renderStatus(d) {
  const filtered = Number(d.unfiltered_event_count) !== Number(d.event_count);
  return `${countdownHtml(d)}
    <p class="hint">数据截至 <code>${esc(d.now)}</code>　·　截止 <code>${esc(d.deadline === null ? "（未设）" : d.deadline)}</code>
      　·　最后产出 <code>${esc(d.last_output_at === null ? "（暂无）" : d.last_output_at)}</code>${d.last_output_source ? `（来源 ${esc(d.last_output_source)}）` : ""}</p>
    <p class="hint">事件 <strong>${esc(d.event_count)}</strong> 条 · 产物 <strong>${esc(d.artifact_count)}</strong> 个${
      filtered ? `（全库事件 ${esc(d.unfiltered_event_count)} 条，列表为空可能只是档案过滤后的结果）` : ""}
      　·　可执行预警占比 <code>${esc(d.actionable_ratio)}</code>（无预警时定义为 1，不代表没问题）</p>
    <h4>无产出预警 warnings（${esc((d.warnings || []).length)} 条）</h4>
    ${warningsHtml(d.warnings)}
    <h4>不做清单 not_do_list</h4>${notDoHtml(d)}
    ${minViableHtml(d.min_viable)}`;
}

/** 进度守卫卡（id 全 `p-` 前缀）。 */
export function progressHtml() {
  return `<fieldset class="flags" id="p-box">
    <legend>进度与时间管理（FR-8）· GET <code>/api/v1/progress/status</code>（只读）</legend>
    <div class="grid">
      <label class="field">截止时间 <code>deadline</code>（可选，本地时区换算成 UTC）
        <input id="p-deadline" type="datetime-local"></label>
      <label class="field">判定用「现在」 <code>now</code>（可选，留空 = 真实当前时刻）
        <input id="p-now" type="datetime-local"></label>
      <label class="field">按档案过滤 <code>profile_id</code>（可选）
        <input id="p-profile_id" placeholder="例如 A2024"></label>
    </div>
    <div class="btn-row">
      <button type="button" id="p-load" class="btn primary">查询进度</button>
      <span class="hint">只读接口：不写库、不写盘</span>
    </div>
    <div class="result" id="p-out"></div>
  </fieldset>`;
}

/** 绑定查询。三个输入都可留空，全空则只查默认库。 */
export function bindProgress() {
  const btn = document.getElementById("p-load");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const q = qs({
      deadline: toIsoUtc(document.getElementById("p-deadline").value),
      now: toIsoUtc(document.getElementById("p-now").value),
      profile_id: document.getElementById("p-profile_id").value.trim(),
    });
    setBusy("p-out", "正在读取进度…");
    try {
      setOut("p-out", renderStatus(await apiGet("/progress/status" + q)));
    } catch (err) {
      /* 404 = 库文件不存在（progress_tool 抛 FileNotFoundError）；错误体仍走统一三要素。 */
      const extra = err && err.status === 404
        ? `<p class="hint">库还没建：先跑一次 <code>intake</code>（或让 <code>cli serve --db</code> 指到已有的库）再来查。</p>`
        : "";
      setOut("p-out", errorHtml(err) + extra);
    }
  });
}
