/* core/profile_form.js —— P4 建档写入（FR-1：给档案追加一个 draft 版本）。
 *
 * 后端契约（`mmagent/api/routes_profile.py` 的 `POST /profile/write`，逐字对齐）：
 *   body  {"profile_id": str(必填), "patch": dict(必填), "actor": str(必填),
 *          "upstream_refs": list|null(可选)}
 *   200 → {"profile_id": str, "version_id": str}
 *   404 → KeyError(detail)（写入前置条件不满足时由 store 抛）
 *   422 → pydantic 校验失败（缺字段 / patch 不是对象）
 *
 * 为什么本模块**自己拥有全部元素 id**、自己 `document.getElementById(...)`：
 * 机检 test_14 只扫 `views/*.js`，要求「视图里 `getElementById` / `iv` / `ic` 用到的 id
 * 必须在该视图自己的模板里 `id="..."` 定义过」。若让 `views/profile.js` 去 `iv("t-pw-patch")`，
 * 而 id 定义在本核心里，test_14 会立刻判红。故本模块对外只导出「一段 HTML + 一个绑定函数」。
 *
 * 只 import 自 `./api.js` 与 `./dom.js`，不碰网络以外的任何副作用。
 */
import { apiPost } from "./api.js";
import { esc, setBusy, setErr, setOut } from "./dom.js";

/** 预填的示例 patch：一个**真能提交**的对象（空对象也能提交，但看不出结构长什么样）。 */
const PATCH_SAMPLE = `{
  "title": "2024 A 题 示例档案",
  "caliber": {"unit": "万元", "period": "2024"},
  "sources": ["archive:2024a-A"]
}`;

/** 读输入框原值（不 trim，便于把 JSON 里的缩进原样交给 JSON.parse）。 */
function readRaw(id) {
  const el = document.getElementById(id);
  return el ? el.value : "";
}

/** 解析一个 JSON 输入框；空 → `undefined`。非法时抛出**带字段名**的错误（绝不静默当空）。 */
function parseJsonInput(id, label, whenBlank) {
  const raw = readRaw(id).trim();
  if (!raw) return whenBlank;
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error(`${label} 不是合法 JSON：${e.message}`);
  }
}

/** 建档卡（`<article class="card">`；id 前缀 `i-/t-/b-/r-pw` 由本模块独占）。 */
export function writeFormHtml() {
  return `<article class="card">
    <h3 class="card-title">建档写入（FR-1）</h3>
    <p class="hint">POST <code>/api/v1/profile/write</code>：给上方的 <code>profile_id</code>
      追加一个 <code>draft</code> 版本。版本链**只追加、不改写**——这次写入不会覆盖任何既有版本。</p>
    <div class="grid">
      <label class="field">操作人 <code>actor</code>（必填）
        <input id="i-pw-actor" placeholder="例如 张三">
      </label>
      <label class="field">上游引用 <code>upstream_refs</code>（JSON 数组，可留空）
        <input id="i-pw-refs" placeholder='["archive:2024a-A"]'>
      </label>
    </div>
    <label class="field">档案内容 <code>patch</code>（JSON 对象，必填）
      <textarea id="t-pw-patch" rows="7">${esc(PATCH_SAMPLE)}</textarea>
    </label>
    <div class="btn-row">
      <button type="button" id="b-pw-write" class="btn primary">写入 draft 版本</button>
      <span class="hint">成功后返回 <code>version_id</code>；把它填进上方「版本」框即可读回</span>
    </div>
    <div class="result" id="r-pw"></div>
  </article>`;
}

/**
 * 绑定提交。`getProfileId()` 由视图提供（复用页面顶部的 `i-profile_id`），本模块不复制一个 id。
 */
export function bindProfileWrite(getProfileId) {
  const btn = document.getElementById("b-pw-write");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    const profileId = String(getProfileId() || "").trim();
    if (!profileId) {
      setOut("r-pw", `<div class="alert warn">请先在上方填写档案 id（<code>profile_id</code>）。</div>`);
      return;
    }
    const actor = readRaw("i-pw-actor").trim();
    if (!actor) {
      setOut("r-pw", `<div class="alert warn">操作人 <code>actor</code> 为必填（后端要求非空）。</div>`);
      return;
    }

    let patch;
    let refs;
    try {
      patch = parseJsonInput("t-pw-patch", "档案内容 patch", undefined);
      if (patch === undefined) {
        throw new Error("档案内容 patch 不能为空（至少写一个 {} 这样的对象）");
      }
      if (patch === null || typeof patch !== "object" || Array.isArray(patch)) {
        throw new Error('档案内容 patch 必须是 JSON 对象（形如 {"k":"v"}），不能是数组或单个值');
      }
      refs = parseJsonInput("i-pw-refs", "上游引用 upstream_refs", null);
      if (refs !== null && !Array.isArray(refs)) {
        throw new Error("上游引用 upstream_refs 必须是 JSON 数组，或留空");
      }
    } catch (e) {
      /* 非法 JSON 一律拦下：不静默丢弃、不当空对象送出去。 */
      setOut("r-pw", `<div class="alert err"><strong>输入不是合法 JSON，本次未提交</strong>
        <p>${esc(e.message)}</p></div>`);
      return;
    }

    const body = { profile_id: profileId, patch, actor };
    if (refs) body.upstream_refs = refs;

    setBusy("r-pw", "正在写入 draft 版本…");
    try {
      const d = await apiPost("/profile/write", body);
      setOut("r-pw", `<div class="alert ok"><strong>已写入一个 draft 版本</strong>
        <p><code>profile_id</code>：${esc(d.profile_id)}　·　<code>version_id</code>：${esc(d.version_id)}</p>
        <p>下一步：把 <code>${esc(d.version_id)}</code> 填到上方「版本 <code>version</code>」框，
          再点「读取档案」即可看到这一版内容（不带版本时 <code>last</code> 只取最新 adopted）。</p></div>`);
    } catch (err) {
      setErr("r-pw", err);
    }
  });
}
