/* core/run_stream.js —— 运行过程的实时跟随控件（`POST /api/v1/stream/run`，SSE）。
 *
 * 后端契约（`mmagent/api/sse.py`）：body `{run_id, timeout_s, max_events}`——后两者在 wire 上
 * **不可空**（后端默认 10.0 / 200），故本模块始终送数字、不送 null。数据帧
 * `data: {"seq":..,"stage":..,"state":..,"message":..,"artifact_id":null}`；收尾帧
 * `event: end` + `data: {"events": <已推条数>}`。
 *
 * 为什么不用 `EventSource`：它只支持 GET、不能带请求体，而本端点是 POST + JSON body。
 * 取流走 `core/api.js::apiStream`（全站**唯一**发起网络请求的地方），切帧走
 * `core/sse.js` 的纯函数；本模块只做「把帧渲染成行 + 管住开始/停止」。
 * 元素 id 全部由本模块独占（理由同 `core/profile_form.js`：机检 test_14 只扫 views）。
 */
import { apiStream } from "./api.js";
import { esc, setErr, setOut } from "./dom.js";

/** 数字输入：非法/空 → `dft`（wire 上两个字段不可空，必须送数字）。 */
function readNum(id, dft) {
  const el = document.getElementById(id);
  const n = Number(el ? el.value.trim() : "");
  return Number.isFinite(n) ? n : dft;
}

/** 运行跟随控件（放在「查询状态」那张 form 内；两个按钮都是 type=button，不触发提交）。 */
export function liveHtml() {
  return `<fieldset class="flags" id="f-rt-live">
    <legend>实时跟随 <code>POST /api/v1/stream/run</code>（SSE，逐条推事件）</legend>
    <div class="grid">
      <label class="field">最长等待 <code>timeout_s</code>（秒，后端夹到 0~60）
        <input id="i-rt-timeout" value="10">
      </label>
      <label class="field">最多事件数 <code>max_events</code>（后端夹到 0~1000）
        <input id="i-rt-max_events" value="200">
      </label>
    </div>
    <div class="btn-row">
      <button type="button" id="b-rt-start" class="btn primary">开始实时跟随</button>
      <button type="button" id="b-rt-stop" class="btn" disabled>停止</button>
      <span class="hint" id="d-rt-state">未开始</span>
    </div>
    <p class="hint">事件源只有 <code>events</code> 表（按 <code>run_id</code> 过滤），收尾固定发一帧
      <code>event: end</code>。事件体的 <code>artifact_id</code> 恒为 <code>null</code>——后端无此来源，前端不伪造。</p>
    <ol id="t-rt-list"></ol>
    <div class="result" id="r-rt"></div>
  </fieldset>`;
}

/** 一帧 → 一行（先求值再 esc，避免把 null 显示成 "null"）。 */
function frameLine(frame, ordinal) {
  const d = (frame.data !== null && typeof frame.data === "object") ? frame.data : {};
  const seq = d.seq === null || d.seq === undefined ? ordinal : d.seq;
  const stage = d.stage === null || d.stage === undefined ? "—" : d.stage;
  const state = d.state === null || d.state === undefined ? "—" : d.state;
  let msg = d.message;
  if (msg === null || msg === undefined) msg = typeof frame.data === "string" ? frame.data : "（无）";
  return `<li><code>#${esc(seq)}</code> stage=${esc(stage)} state=${esc(state)} ${esc(msg)}</li>`;
}

/** 绑定开始/停止。`getRunId()` 由视图提供（复用页面顶部的 `i-run_id`）。 */
export function bindLiveStream(getRunId) {
  const startBtn = document.getElementById("b-rt-start");
  const stopBtn = document.getElementById("b-rt-stop");
  if (!startBtn || !stopBtn) return;

  /* 三态：ctrl 是当前连接；stopped 区分「主动停」与「真失败」；rendered 是本地已渲染条数。 */
  let ctrl = null;
  let stopped = false;
  let rendered = 0;

  const setState = (text) => { const e = document.getElementById("d-rt-state"); if (e) e.textContent = text; };
  const appendLine = (html) => {
    const box = document.getElementById("t-rt-list");
    if (box) box.insertAdjacentHTML("beforeend", html);
  };
  /* 「停止」：主动 abort，**不是错误**；页面上必须说清这一点，否则用户会以为流坏了。 */
  stopBtn.addEventListener("click", () => {
    if (!ctrl) return;
    stopped = true;
    ctrl.abort();
    setState("已手动停止（不是错误）");
    setOut("r-rt", `<div class="alert warn"><strong>已手动停止</strong>
      <p>连接由本页主动断开（<code>AbortController.abort()</code>），<strong>这不是错误</strong>，
        也不代表服务端已经推完。本次已渲染 <strong>${esc(rendered)}</strong> 条事件。</p></div>`);
  });

  startBtn.addEventListener("click", async () => {
    const runId = String(getRunId() || "").trim();
    if (!runId) {
      setOut("r-rt", `<div class="alert warn">请先在上方填写运行 id（<code>run_id</code>）。</div>`);
      return;
    }
    if (ctrl) return;

    const list = document.getElementById("t-rt-list");
    if (list) list.innerHTML = "";
    rendered = 0;
    stopped = false;
    setOut("r-rt", "");

    const body = {
      run_id: runId,
      timeout_s: readNum("i-rt-timeout", 10),
      max_events: readNum("i-rt-max_events", 200),
    };

    const mine = new AbortController();
    ctrl = mine;
    startBtn.disabled = true;
    stopBtn.disabled = false;
    setState(`正在跟随 ${runId}…`);

    try {
      await apiStream("/stream/run", body, (frame) => {
        if (frame.event === "end") {
          const total = (frame.data !== null && typeof frame.data === "object") ? frame.data.events : "（后端未给出）";
          setState(`已推 ${total} 条并结束`);
          return;
        }
        if (frame.event === "message") {
          rendered += 1;
          appendLine(frameLine(frame, rendered));
        }
      }, { signal: mine.signal });

      if (!stopped) {
        setOut("r-rt", `<div class="alert ok"><strong>跟随结束</strong>
          <p>已收到收尾帧 <code>event: end</code>；本页共渲染 <strong>${esc(rendered)}</strong> 条事件。</p></div>`);
      }
    } catch (err) {
      /* abort 已被 apiStream 安静吞掉；能走到这里的都是真错误。 */
      if (stopped) return;
      setErr("r-rt", err);
      setState("已中断（错误体见下方）");
    } finally {
      if (ctrl === mine) ctrl = null;
      startBtn.disabled = false;
      stopBtn.disabled = true;
    }
  });
}
