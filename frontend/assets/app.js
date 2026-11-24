/* 数模论文工作台 SPA：保留核心工作模块，并提供独立的概念问答入口。 */
import { esc } from "./core/dom.js";
import { initTheme } from "./core/theme.js";
import { render as home } from "./views/home.js";
import { render as paper } from "./views/paper.js";
import { render as review } from "./views/review.js";
import { render as ledger } from "./views/ledger.js";
import { render as concept } from "./views/concept.js";

const HOME = {
  hash: "#/", code: "00", title: "首页", sub: "数学建模论文工作台 · 从资料整理到论文交付",
  view: home,
};

const ROUTES = [
  { hash: "#/paper", code: "01", title: "数模论文", sub: "题面、方案、求解结果与论文骨架的统一入口", view: paper },
  { hash: "#/review", code: "02", title: "文献综述", sub: "上传若干篇 Word/PDF 文献，生成可编辑的简易综述", view: review },
  { hash: "#/ledger", code: "03", title: "项目台账", sub: "查看项目产物、运行事件与环境审计记录", view: ledger },
  { hash: "#/concept", code: "04", title: "概念问答", sub: "基于唯一 RAG 知识库完成文本或图文问答", view: concept },
];

const TABS = [
  { code: "01", label: "数模论文", hash: "#/paper" },
  { code: "02", label: "文献综述", hash: "#/review" },
  { code: "03", label: "项目台账", hash: "#/ledger" },
  { code: "04", label: "概念问答", hash: "#/concept" },
];

const cur = (on) => (on ? ' aria-current="page"' : "");

function paintTabs(hash) {
  const box = document.getElementById("tabs");
  if (!box) return;
  box.innerHTML = TABS.map((tab) => `
    <a class="tab${tab.hash === hash ? " is-on" : ""}" href="${tab.hash}"${cur(tab.hash === hash)}>
      <span class="tab-no">${esc(tab.code)}</span><span class="tab-label">${esc(tab.label)}</span>
    </a>`).join("");
}

function headHtml(meta) {
  return `<section class="sec-head"><div class="sec-no">${esc(meta.code)}</div>
    <h1 class="sec-title">${esc(meta.title)}</h1><p class="sec-sub">${esc(meta.sub)}</p></section>`;
}

async function renderRoute() {
  const hash = location.hash || "#/";
  const route = ROUTES.find((item) => item.hash === hash);
  const meta = route || HOME;
  paintTabs(route ? hash : "#/");
  const app = document.getElementById("app");
  if (!app) return;
  app.innerHTML = headHtml(meta) + `<div id="view"></div>`;
  try {
    await meta.view(document.getElementById("view"));
  } catch (err) {
    app.innerHTML = `<div class="alert err"><strong>页面渲染异常</strong><pre>${esc(String(err?.stack || err))}</pre></div>`;
  }
  window.scrollTo(0, 0);
}

initTheme();
window.addEventListener("hashchange", renderRoute);
renderRoute();
