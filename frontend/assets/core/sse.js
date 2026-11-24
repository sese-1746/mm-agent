/* core/sse.js —— SSE 切帧解析器（**纯函数**：零 I/O、零 DOM、不 import 任何东西）。
 *
 * 为什么单独一个文件：
 * 1. `POST /stream/run` 是 POST + JSON body，`EventSource` 只支持 GET 且不能带请求体，
 *    所以只能手写 fetch + resp.body.getReader() 自己切帧；切帧这件事**必须能在 node 里单测**
 *    （tests/test_frontend_sse_parser.py 用 `node --input-type=module -e` 真跑本文件）。
 * 2. 取流（含发起网络请求）只许出现在 core/api.js（机检 test_10 的唯一出网点），
 *    故解析器与取流分居两个文件：本文件不碰网络、不碰 DOM，输入输出都是普通值。
 *
 * 契约：`createSseParser()` → `{ push(chunkText), flush() }`
 * - `push` 接收**任意切分**的文本块（可以在一帧中间、一个 UTF-8 字符中间之外任意断开），
 *   返回本次已完整的帧数组（可能 0 帧，也可能是 2 帧以上）；
 * - `flush` 交出缓冲区里最后一帧**未以空行结尾**的残帧（服务端断开时常见），
 *   **返回数组**（0 或 1 帧），与 `push` 同形——两处调用点因此可以走同一个渲染循环。
 * - 帧形状：`{ event: <event 字段，缺省 "message">, data: <JSON.parse 后的值；解析失败则原字符串> }`
 *
 * 兼容的口径（按 SSE 规范 + 本项目后端的实际输出）：
 * - 帧分隔符 `\n\n`；`\r\n` 与 `\r` 都归一成 `\n`（Windows 代理/网关改写换行很常见）；
 * - 同一帧内多行 `data:` 用 `\n` 连接（规范如此；后端目前只发单行，但解析器不假设）；
 * - `:` 开头的注释行（心跳）忽略；没有 `data` 字段的帧忽略；`data` 为空串的帧忽略；
 * - `id:` / `retry:` 等其他字段**不消费也不报错**（本端点不发，保留了也不会误判成帧）。
 */

/** 把一行 `field: value` 拆成 [field, value]；无冒号时整行是 field。 */
function splitLine(line) {
  const colon = line.indexOf(":");
  if (colon < 0) return [line, ""];
  let value = line.slice(colon + 1);
  if (value.charAt(0) === " ") value = value.slice(1);
  return [line.slice(0, colon), value];
}

/** 解析一帧原文；返回帧对象，或 null（注释帧 / 无 data / 空 data）。 */
function parseFrame(raw) {
  let event = "message";
  let hasData = false;
  const datas = [];
  raw.split("\n").forEach((line) => {
    if (!line || line.charAt(0) === ":") return;
    const [field, value] = splitLine(line);
    if (field === "event") {
      if (value) event = value;
      return;
    }
    if (field === "data") {
      hasData = true;
      datas.push(value);
    }
  });
  if (!hasData) return null;
  const text = datas.join("\n");
  if (!text) return null;
  try {
    return { event, data: JSON.parse(text) };
  } catch (_e) {
    /* 非法 JSON 不抛：把原文交出去，由调用方决定怎么显示（不许静默丢弃）。 */
    return { event, data: text };
  }
}

export function createSseParser() {
  let buf = "";

  return {
    push(chunk) {
      /* 换行归一化在缓冲区上做：\r\n 优先于 \r，故先替换两字符序列。
       * 归一化后缓冲区里不可能残留 \r，下一次 push 拼接时也不会再产生组合序列。 */
      buf += (chunk === null || chunk === undefined) ? "" : String(chunk);
      buf = buf.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

      const frames = [];
      let cut = buf.indexOf("\n\n");
      while (cut >= 0) {
        const raw = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        const frame = parseFrame(raw);
        if (frame) frames.push(frame);
        cut = buf.indexOf("\n\n");
      }
      return frames;
    },

    flush() {
      const raw = buf;
      buf = "";
      const frame = parseFrame(raw);
      return frame ? [frame] : [];
    },
  };
}
