/* core/api.js —— 唯一出网点（本机 /api/v1）。
 *
 * 前端页面只与本机服务（127.0.0.1）通信；模型调用由后端按名单制白名单出网。
 * 不引任何外部地址、不读任何 CDN。
 * 冻结契约：失败体固定三要素 error_code / message / trace_id（外加可选 detail）。
 *
 * 注：与 core/dom.js 之间是**有意的函数级循环引用**——dom 的 setErr 需要这里的
 * errorHtml，errorHtml 需要 dom 的 esc。两侧都只有 function 声明，ES 模块的
 * 函数提升保证求值顺序无关，浏览器与 node 均可直接加载。
 */
import { esc } from "./dom.js";
import { createSseParser } from "./sse.js";

/** 冻结的 API 前缀（全站唯一）。 */
export const API = "/api/v1";

/** 统一错误体在页面上必须显示 error_code + message + trace_id。 */
export function errorHtml(err) {
  if (err && err.kind === "network") {
    return `<div class="alert err">
      <strong>无法连接本地后端</strong>
      <p>这个<strong>页面</strong>只与本机服务（<code>127.0.0.1</code>）通信；模型调用由<strong>后端</strong>按名单制白名单出网（默认 DashScope），也可切回本机 Ollama。请先在<strong>仓库根目录</strong>启动服务：</p>
      <pre>python -m mmagent.cli serve</pre>
      <p>启动成功后终端会打印一行可访问地址（默认端口），再用该地址打开本页即可。</p>
      <p class="muted">技术细节：网络请求失败（后端未启动、端口不通，或页面不是由该服务托管）。</p>
    </div>`;
  }
  const st = err && err.status;
  const b = (err && err.body) || {};
  const detail = b.detail === undefined
    ? ""
    : `<details class="raw"><summary>detail</summary><pre>${esc(JSON.stringify(b.detail, null, 2))}</pre></details>`;
  return `<div class="alert err">
    <strong>请求失败（HTTP ${esc(st)}）</strong>
    <ul>
      <li>error_code：<code>${esc(b.error_code || "（后端未提供）")}</code></li>
      <li>message：${esc(b.message || "（后端未提供）")}</li>
      <li>trace_id：<code>${esc(b.trace_id || "（后端未提供）")}</code></li>
    </ul>
    ${detail}
  </div>`;
}

/**
 * 调用后端接口。成功返回解析后的 JSON；失败抛 {kind:"network"} 或 {kind:"http",status,body}。
 */
export async function callApi(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(API + path, init);
  } catch (_e) {
    throw { kind: "network" };
  }
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_e) {
    data = { raw: text };
  }
  if (!resp.ok) throw { kind: "http", status: resp.status, body: data };
  return data;
}

export const apiGet = (path) => callApi("GET", path);
export const apiPost = (path, body) => callApi("POST", path, body);

/* 上传（`POST /upload`）——**base64 JSON**，不是 `multipart/form-data`。
 *
 * 为什么：本机没装 `python-multipart`（禁装包、无外网、wheelhouse 里也没有），
 * 后端收不了表单文件；故前端把文件读成 base64 后随 JSON 一起送，服务端自己解码落盘。
 * 出网点仍只在本文件（view 里不许出现 `fetch(`）。files 形状：`[{name, b64}]`。
 */
export function apiUpload(path, files) {
  return apiPost(path, { files: Array.isArray(files) ? files : [] });
}

/** `File` → 纯 base64 字符串（去掉 `data:<mime>;base64,` 前缀）。用 FileReader，不读二进制流。 */
export function fileToB64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || "");
      const i = s.indexOf(",");
      resolve(i >= 0 ? s.slice(i + 1) : s);
    };
    /* 本地读盘失败不是 HTTP 错误体，故抛普通 Error 由调用方本地上屏，不走 errorHtml。 */
    reader.onerror = () => reject(new Error(`本地读取文件失败：${(file && file.name) || ""}`));
    reader.readAsDataURL(file);
  });
}

/**
 * 流式 POST（SSE，`POST /stream/run`）：逐帧回调 `onEvent({event, data})`。
 *
 * 为什么这里必须手写：`EventSource` 只支持 GET 且不能带请求体，本端点是 POST + JSON body。
 * 为什么切帧不写在这里：切帧逻辑放在 `core/sse.js` 的纯函数里，才能被 node 单测真跑；
 * 本函数只负责「取流 + 解码 + 把文本块喂给解析器」。
 *
 * 失败形状与 `callApi` 一致：网络异常 → `{kind:"network"}`；非 2xx → `{kind:"http",status,body}`。
 * **调用方主动 abort 不算错误**：`opts.signal.aborted` 时安静返回，不抛。
 */
export async function apiStream(path, body, onEvent, opts) {
  const opt = opts || {};
  const init = { method: "POST", headers: { Accept: "text/event-stream" } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (opt.signal) init.signal = opt.signal;

  const aborted = () => Boolean(opt.signal && opt.signal.aborted);

  let resp;
  try {
    resp = await fetch(API + path, init);
  } catch (_e) {
    if (aborted()) return;
    throw { kind: "network" };
  }
  if (!resp.ok) {
    /* 与 callApi 同形状：先取文本再试 JSON，非 JSON 体落到 {raw:...}。 */
    const bad = await resp.text();
    let data = null;
    try {
      data = bad ? JSON.parse(bad) : null;
    } catch (_e) {
      data = { raw: bad };
    }
    throw { kind: "http", status: resp.status, body: data };
  }
  if (!resp.body || typeof resp.body.getReader !== "function") {
    throw { kind: "network" };
  }

  const emit = (frames) => {
    frames.forEach((frame) => { if (typeof onEvent === "function") onEvent(frame); });
  };
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();

  for (;;) {
    let step;
    try {
      step = await reader.read();
    } catch (_e) {
      if (aborted()) return;
      throw { kind: "network" };
    }
    if (step.done) break;
    emit(parser.push(decoder.decode(step.value, { stream: true })));
  }
  emit(parser.push(decoder.decode()));
  emit(parser.flush());
}
