/* 文献综述模块：多个 Word/PDF 文献 → 简易综述 Markdown + Word 产物。 */
import { apiPost, errorHtml, fileToB64 } from "../core/api.js";
import { esc, setBusy, setOut } from "../core/dom.js";
import { bindEvaluation, evaluationHtml } from "../core/evaluation_panel.js";

const MAX_DOCUMENTS = 20;
const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024;
const MAX_TOTAL_DOCUMENT_BYTES = 80 * 1024 * 1024;
const MAX_TITLE_LENGTH = 200;

function validateFiles(files, title) {
  const cleanTitle = String(title || "").trim();
  if (cleanTitle.length > MAX_TITLE_LENGTH) return `综述标题不能超过 ${MAX_TITLE_LENGTH} 个字符。`;
  if (!files.length) return "请先选择至少一篇 .docx 或 .pdf 文献。";
  if (files.length > MAX_DOCUMENTS) return `一次最多上传 ${MAX_DOCUMENTS} 篇文献。`;
  const names = new Set();
  let total = 0;
  for (const file of files) {
    const name = String(file.name || "");
    const folded = name.trim().toLocaleLowerCase();
    if (!folded || /[\\/\x00-\x1f\x7f]/.test(name) || name.includes("..") || /[. ]$/.test(name)) {
      return `文件名不可用：${name || "（空）"}。`;
    }
    if (!/\.(docx|pdf)$/i.test(name)) return `仅支持 .docx 或 .pdf 文件：${name}。`;
    if (names.has(folded)) return `上传文档不能重名：${name}。`;
    names.add(folded);
    if (file.size > MAX_DOCUMENT_BYTES) return `${name} 超过 20 MB 单文件限制。`;
    total += file.size || 0;
  }
  if (total > MAX_TOTAL_DOCUMENT_BYTES) return "本次上传文档总大小不能超过 80 MB。";
  return "";
}

function markdownHtml(markdown) {
  return String(markdown || "").split("\n").map((line) => {
    const safe = esc(line);
    if (line.startsWith("### ")) return `<h4>${safe.slice(4)}</h4>`;
    if (line.startsWith("## ")) return `<h3>${safe.slice(3)}</h3>`;
    if (line.startsWith("# ")) return `<h2>${safe.slice(2)}</h2>`;
    if (/^\[\d+\]/.test(line)) return `<p class="ref-line">${safe}</p>`;
    return line ? `<p>${safe}</p>` : "";
  }).join("");
}

function resultHtml(data) {
  const docs = (data.documents || []).map((doc) => `<li>${esc(doc.title || doc.name)}（${esc(String(doc.format || "文献").toUpperCase())}，${esc(doc.paragraphs)} 段）</li>`).join("");
  const keywords = (data.keywords || []).map((x) => `<span class="tag">${esc(x)}</span>`).join(" ");
  const ledgerWarning = data.ledger_warning ? `<div class="alert warn"><strong>台账提示</strong>：${esc(data.ledger_warning)}</div>` : "";
  return `<div class="alert ok"><strong>文献综述已生成</strong>：共处理 ${esc(data.document_count)} 篇文献。</div>${ledgerWarning}
    <div class="stat-row"><div class="stat"><span class="stat-val">${esc(data.document_count)}</span><span class="stat-key">文献数</span></div>
    <div class="stat"><span class="stat-val">${esc((data.keywords || []).length)}</span><span class="stat-key">关键词</span></div></div>
    <p><strong>关键词：</strong>${keywords || "（未提取到稳定关键词）"}</p>
    <p><strong>Word 产物：</strong><code>${esc(data.output_docx || "")}</code></p>
    <h3>综述预览</h3><div class="review-preview">${markdownHtml(data.markdown)}</div>
    <details class="raw"><summary>本次文献</summary><ul>${docs}</ul></details>`;
}

export function render(root) {
  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">从 Word/PDF 文献生成简易综述</h3>
      <p class="muted card-note">选择一篇或多篇 <code>.docx</code> 或 <code>.pdf</code> 文献后提交。后端使用纯 Python 提取文字层、归纳高频主题并生成可继续编辑的 Word 文件，不调用模型、不联网。</p>
      <form id="review-form">
        <div class="grid">
          <label class="field">综述标题<input id="review-title" value="数模论文文献综述"></label>
          <label class="field">Word/PDF 文献（可多选）<input class="file-picker" id="review-files" type="file" accept=".docx,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf" multiple required></label>
        </div>
        <p class="hint">单次最多 20 篇，每篇不超过 20 MB；支持文字层 PDF，扫描 PDF 需要人工补充正文。</p>
        <div class="btn-row"><button type="submit" class="btn primary upload-action" id="review-submit">生成文献综述</button><span class="hint">POST <code>/api/v1/review/generate</code></span></div>
      </form>
      <div class="result" id="review-result"></div>
    </article>
    ${evaluationHtml("literature_review")}`;

  const form = document.getElementById("review-form");
  bindEvaluation("literature_review");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("review-files");
    const files = Array.from(input.files || []);
    const validationError = validateFiles(files, document.getElementById("review-title").value);
    if (validationError) {
      setOut("review-result", `<div class="alert warn">${esc(validationError)}</div>`);
      return;
    }
    setBusy("review-result", "正在读取文献并生成综述 Word 文件…");
    document.getElementById("review-submit").disabled = true;
    try {
      const documents = [];
      for (const file of files) documents.push({ name: file.name, b64: await fileToB64(file) });
      const data = await apiPost("/review/generate", {
        title: document.getElementById("review-title").value.trim() || "数模论文文献综述",
        documents,
      });
      setOut("review-result", resultHtml(data));
    } catch (err) {
      setOut("review-result", errorHtml(err));
    } finally {
      document.getElementById("review-submit").disabled = false;
    }
  });
}
