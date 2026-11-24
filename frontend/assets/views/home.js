/* 首页：说明核心模块和当前后端状态。 */
import { apiGet, errorHtml } from "../core/api.js";
import { esc, setOut } from "../core/dom.js";

const MODULES = [
  ["01", "数模论文", "#/paper", "整理题面、建模方案、求解结果，装配论文骨架并做一致性校验。"],
  ["02", "文献综述", "#/review", "批量上传 Word 文献，生成简易综述和可编辑 Word 产物。"],
  ["03", "项目台账", "#/ledger", "集中查看产物、运行事件和环境审计记录。"],
  ["04", "概念问答", "#/concept", "用唯一 RAG 知识库回答概念问题，可在同一次问答中附带图片。"],
];

export function render(root) {
  root.innerHTML = `<article class="card hero-card"><h2>数模论文工作台</h2>
    <p class="lead">把题面资料、建模过程、文献阅读和论文交付放在同一个本地项目里。</p>
    <div class="chip-row">${MODULES.map(([code, title, hash]) => `<a class="chip" href="${hash}"><span>${code}</span>${esc(title)}</a>`).join("")}</div>
  </article>
  <article class="card"><h3 class="card-title">功能模块</h3><div class="module-list">
    ${MODULES.map(([code, title, hash, desc]) => `<a class="module-item" href="${hash}"><strong>${code} · ${esc(title)}</strong><span>${esc(desc)}</span></a>`).join("")}
  </div></article>
  <article class="card"><h3 class="card-title">本地服务状态</h3><div id="home-status" class="result"><p class="muted">检查中…</p></div></article>`;
  apiGet("/health").then((data) => setOut("home-status", `<div class="alert ok"><strong>后端在线</strong>：${esc(data.service || "mmagent")}</div>`))
    .catch((err) => setOut("home-status", errorHtml(err)));
}
