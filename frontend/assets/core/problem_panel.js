/* core/problem_panel.js —— FR-1 题面解析（`POST /api/v1/problem/parse`，**调模型、不写档案**）。
 *
 * 挂在 `#/problem`：解析结果要落成档案，落点正是本页的建档表单（`POST /profile/write`）。
 *
 * 四条按后端代码写、最容易说错的口径：
 * 1. **本接口不写档案**（`agents/archivist.py:266-267`：`profile_id`/`actor` 只为调用方可读性
 *    保留，函数体无 store 调用）。写 draft 的是 `write_profile_from_parse`（`:313-338`），
 *    **HTTP 未暴露**，只有 CLI `problem parse` 调。故文案绝不能说「档案已建立」。
 *    另：路由注释的「不写库」只在「不写 profile_doc」这层成立——`RoleRunner` 仍会写
 *    `metric_obs` 与 `artifacts(kind='llm_output')`（默认 dashscope 还会写 `net_audit`）。
 * 2. **`dropped` 在本接口是 `int` 计数**（`archivist.py:303`：被降级的子问题条数），
 *    **不是条目清单**——与 `POST plan/build` 的同名键 `list[str]` 同名不同型，别共用渲染函数。
 *    （第 32 轮：本文件里端点路径一律**不写前导斜杠**。`_api_tokens()` 是纯文本正则，
 *    注释里带斜杠的路径会被当成真实调用点，替未接线的端点伪造覆盖。已在 D-104 于收集器层
 *    根治（`_strip_comment_lines`），此处仍保持不写，双保险。）
 * 3. `low_confidence_quotes` 是 `int`；标 low 的是「逐字命中但非空白字符 < 8」的引用
 *    （`archivist.py:244-251`），单列出来交人工核对。
 * 4. 响应**没有** `profile_id` / `version_id` ⇒ 不能直接喂 `paper/build`，也不能直接喂
 *    `POST profile/write`（patch 要按 `write_profile_from_parse` 的映射自己拼）。
 * 全部 `pr-` 前缀 id 由本模块自己持有（`test_14` 只扫 views）。
 */
import { apiPost, errorHtml } from "./api.js";
import { esc, rawDetails, setBusy, setOut } from "./dom.js";

/** 题面解析卡（id 全 `pr-` 前缀；`actor` / `db_path` 不暴露——仅记账、走默认库）。 */
export function problemPanelHtml() {
  return `<fieldset class="flags" id="pr-box">
    <legend>题面解析（FR-1）· POST <code>/api/v1/problem/parse</code>（调模型，不写档案）</legend>
    <label class="field">题面原文 <code>problem_text</code>（必填）
      <textarea id="pr-text" rows="6" placeholder="整段粘赛题原文；超过 6000 字符会被后端截断，并在 open_questions 里记一条"></textarea></label>
    <label class="field">关联档案 id <code>profile_id</code>（可留空）
      <input id="pr-profile" placeholder="留空 = 不关联档案（后端不生成 id，任意新 id 自成一档）"></label>
    <div class="btn-row">
      <button type="button" id="pr-parse" class="btn primary">解析题面</button>
      <span class="hint">调模型，通常 20~60 秒；本接口<strong>只返回结构化子问题，不写档案</strong></span>
    </div>
    <div class="result" id="pr-result"></div>
  </fieldset>`;
}

/** 绑定解析。题面为空时**本地拦下**（后端 `min_length=1`，422 也这么判）。 */
export function bindProblemPanel() {
  const btn = document.getElementById("pr-parse");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const text = document.getElementById("pr-text").value.trim();
    if (!text) {
      setOut("pr-result", `<div class="alert warn"><strong>题面为空</strong>
        <p><code>problem_text</code> 是必填项（后端 <code>min_length=1</code>，空串直接 422）。本地先拦下，省一次无效请求。</p></div>`);
      return;
    }
    const body = { problem_text: text };
    const profile = document.getElementById("pr-profile").value.trim();
    if (profile) body.profile_id = profile;   // 空串不送：null 与 "" 语义不同（后端只透传）
    /* 无取消、无超时（`core/api.js:47-68` 的 `callApi` 冻结契约里没有 signal），
       所以只做「说明性忙态 + 按钮置灰」，不加 spinner / 进度条 / 取消按钮。 */
    btn.disabled = true;
    setBusy("pr-result", "正在调用模型解析题面，通常 20~60 秒，请勿刷新…");
    try {
      setOut("pr-result", renderParse(await apiPost("/problem/parse", body)));
    } catch (err) {
      /* 只上屏后端给的 error_code / message / trace_id（`errorHtml`）。
         **不按 status 猜成因**：500 既可能是未捕获异常也可能是库被锁，
         LLM 侧又有 502/503/499 四档，兜底处方必给错方向。 */
      setOut("pr-result", errorHtml(err));
    } finally {
      btn.disabled = false;
    }
  });
}

/** 成功态：8 键上屏（subproblems / open_questions / summary / dropped / low_confidence_quotes / source_chars / prompt_version / problem）。 */
function renderParse(d) {
  const subs = Array.isArray(d.subproblems) ? d.subproblems : [];
  const questions = Array.isArray(d.open_questions) ? d.open_questions : [];
  const dropped = Number(d.dropped) || 0;
  const low = Number(d.low_confidence_quotes) || 0;
  return `<div class="alert ok"><strong>解析完成，但<em>未写档案</em></strong>
      <p>本接口只做「题面 → 结构化子问题」这一步，<strong>不写 profile_doc</strong>。
        落档案请走本页的建档表单（<code>POST /api/v1/profile/write</code>）或 CLI <code>problem parse</code>。</p>
      <p>响应里没有 <code>profile_id</code> / <code>version_id</code>，因此这批结果<strong>不能直接</strong>喂
        <code>paper/build</code>：先落档案、再由人工卡点 adopt 出 adopted 版本。</p></div>
    ${statsHtml(subs.length, dropped, low, d)}
    <h4>子问题 subproblems（${esc(subs.length)} 条，三要素 + 原文引用）</h4>
    ${subs.length ? `<ol>${subs.map(subHtml).join("")}</ol>` : `<p class="muted">（没有通过闸门的子问题）</p>`}
    ${low > 0 ? lowHtml(low) : ""}
    <h4>待确认 open_questions（${esc(questions.length)} 条）</h4>
    ${questions.length ? `<ol>${questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>`
      : `<p class="muted">（无）</p>`}
    <h4>摘要 summary</h4>
    <p>${esc(d.summary || "（无）")}</p>
    <details class="raw"><summary>题面回显 problem（后端未截断原文，${esc(d.source_chars)} 字符）</summary>
      <pre>${esc(d.problem || "")}</pre></details>
    <p class="hint">口径：被降级的子问题条数 = <code>dropped</code>（整数计数）；
      降级原因只以字符串形式出现在 <code>open_questions</code> 里。</p>
    ${rawDetails(d, "problem/parse 原始响应")}`;
}

/** 四个计数 + 版本号：全是标量，逐格标明来源键。 */
function statsHtml(n, dropped, low, d) {
  return `<div class="stat-row">
      <div class="stat"><span class="stat-val">${esc(n)}</span><span class="stat-key">子问题条数 subproblems</span></div>
      <div class="stat"><span class="stat-val">${esc(dropped)}</span><span class="stat-key">被降级的子问题条数 dropped（不是条目列表）</span></div>
      <div class="stat"><span class="stat-val">${esc(low)}</span><span class="stat-key">低置信引用 low_confidence_quotes</span></div>
      <div class="stat"><span class="stat-val">${esc(d.source_chars)}</span><span class="stat-key">题面字符数 source_chars</span></div>
    </div>
    <p class="hint">prompt_version：<code>${esc(d.prompt_version || "（无）")}</code>　·　
      <code>dropped</code> 在本接口是<strong>整数计数</strong>（被降级的子问题条数），
      与 <code>plan/build</code> 的同名键（字符串数组）不同型，不共用渲染。</p>`;
}

/** 低置信引用：单独一条 warn，明确要人工核对（不是错误，也不参与是否放行）。 */
function lowHtml(n) {
  return `<div class="alert warn"><strong>低置信引用 ${esc(n)} 条：这些引用需人工核对</strong>
    <p>标为 <code>quote_confidence="low"</code> 的引用虽逐字命中原文，但非空白字符不足 8
      （<code>archivist.SHORT_QUOTE_CHARS</code>）：命中可能是偶然，定位力弱。
      这些引用未达到「可逐字定位」的可信度，请人工核对出处后再采用。</p></div>`;
}

/** 一条子问题：三要素逐项 + `source_quote` 原文引用（**不删改、不省略**）。 */
function subHtml(s, i) {
  const lowFlag = s.quote_confidence === "low"
    ? ` <span class="tag warn">低置信引用（${esc(s.quote_chars)} 字）</span>` : "";
  return `<li><p><strong>${esc(s.label || `子问题 ${i + 1}`)}</strong>${lowFlag}</p>
    <p class="hint">目标 target：${esc(s.target || "（空——三要素缺失，见 open_questions）")}</p>
    <p class="hint">已知 given：${esc(s.given || "（空——三要素缺失，见 open_questions）")}</p>
    <p class="hint">约束 constraints：${esc(s.constraints || "（空——三要素缺失，见 open_questions）")}</p>
    <p class="hint">原文引用 source_quote：</p>
    <pre>${esc(s.source_quote || "（无）")}</pre></li>`;
}
