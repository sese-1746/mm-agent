/* 概念问答：问题、可选图片与唯一 RAG 知识库在同一页面完成。 */
import { apiPost, errorHtml, fileToB64 } from "../core/api.js";
import { esc, setBusy, setOut } from "../core/dom.js";

const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

function validateImages(files) {
  if (files.length > MAX_IMAGES) return `一次最多上传 ${MAX_IMAGES} 张图片。`;
  let total = 0;
  for (const file of files) {
    if (!IMAGE_TYPES.has(String(file.type || "").toLowerCase())) return `不支持的图片类型：${file.name}。`;
    if (file.size > MAX_IMAGE_BYTES) return `${file.name} 超过 8 MB 单文件限制。`;
    total += file.size || 0;
  }
  return total > MAX_TOTAL_IMAGE_BYTES ? "本次图片总大小不能超过 20 MB。" : "";
}

function answerHtml(data) {
  const hits = (data.hits || []).map((hit, index) => `<li><strong>[片段${index + 1}]</strong> ${esc(hit.source || hit.path || "未知来源")}（相似度 ${esc(hit.score ?? "-")}）<p class="hint">${esc(hit.text || hit.preview || "")}</p></li>`).join("");
  const images = (data.images_used || []).map((item) => `<span class="tag">${esc(item.name)} · ${esc(item.mime)}</span>`).join(" ");
  return `<div class="alert ok"><strong>概念问答已完成</strong>：${esc(data.model || "模型")}，命中 ${esc((data.hits || []).length)} 个知识库片段。</div>
    <div class="answer-box"><h3>回答</h3><p>${esc(data.answer || "")}</p></div>
    <p><strong>本次图片：</strong>${images || "未附图片"}</p>
    <details class="raw" open><summary>检索依据（唯一知识库）</summary><ul>${hits || "<li>没有检索到片段</li>"}</ul><p class="hint">索引：<code>${esc(data.knowledge_base || "artifacts/rag/index")}</code></p></details>`;
}

export function render(root) {
  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">概念问答</h3>
      <p class="muted card-note">问题会先检索项目唯一的 RAG 知识库，再由配置中的模型回答。可在同一次问答中附带图片，图片不会另建知识库或单独保存。</p>
      <form id="concept-form">
        <label class="field">问题<textarea id="concept-question" rows="5" maxlength="10000" placeholder="例如：本项目采用哪些建模方法？请给出依据。" required></textarea></label>
        <label class="field">图片（可选，最多 4 张）<input class="file-picker" id="concept-images" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label>
        <p class="hint">支持 PNG/JPEG/WebP/GIF；单张不超过 8 MB，总大小不超过 20 MB。回答会标注 [片段N] 作为检索依据。</p>
        <div class="btn-row"><button type="submit" class="btn primary upload-action" id="concept-submit">提交概念问答</button><span class="hint">POST <code>/api/v1/rag/ask</code></span></div>
      </form>
      <div class="result" id="concept-result"></div>
    </article>`;

  document.getElementById("concept-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = document.getElementById("concept-question").value.trim();
    const files = Array.from(document.getElementById("concept-images").files || []);
    if (!question) {
      setOut("concept-result", `<div class="alert warn">问题不能为空。</div>`);
      return;
    }
    const imageError = validateImages(files);
    if (imageError) {
      setOut("concept-result", `<div class="alert warn">${esc(imageError)}</div>`);
      return;
    }
    setBusy("concept-result", "正在检索知识库并生成回答…");
    document.getElementById("concept-submit").disabled = true;
    try {
      const images = [];
      for (const file of files) images.push({ name: file.name, mime: file.type, b64: await fileToB64(file) });
      const data = await apiPost("/rag/ask", { question, k: 5, images });
      setOut("concept-result", answerHtml(data));
    } catch (err) {
      setOut("concept-result", errorHtml(err));
    } finally {
      document.getElementById("concept-submit").disabled = false;
    }
  });
}
