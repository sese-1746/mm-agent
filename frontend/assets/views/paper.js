/* 数模论文模块：把现有题面、方案、求解、装配和交付能力集中到一个页面。 */
import { panel } from "../core/dom.js";
import { bindConsistency, consistencyHtml } from "../core/consistency_panel.js";
import { bindPaper, paperHtml } from "../core/paper_panel.js";
import { bindEvaluation, evaluationHtml } from "../core/evaluation_panel.js";
import { render as problem } from "./problem.js";
import { render as plan } from "./plan.js";
import { render as solve } from "./solve.js";
import { render as deliver } from "./deliver.js";

export async function render(root) {
  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">数模论文统一工作区</h3>
      <p class="muted card-note">题面与附件、建模方案、求解脚本、论文骨架和交付流程已经合并到本页，按从上到下的顺序推进即可。文献阅读请使用旁边的“文献综述”模块。</p>
    </article>
    ${panel("01 · 题面与附件", `<div id="paper-problem"></div>`, "paper-problem-result")}
    ${panel("02 · 建模方案", `<div id="paper-plan"></div>`, "paper-plan-result")}
    ${panel("03 · 求解与出图", `<div id="paper-solve"></div>`, "paper-solve-result")}
    ${panel("04 · 论文骨架与一致性", `${paperHtml()}${consistencyHtml()}`, "paper-result")}
    ${panel("05 · 交付与人工裁定", `<div id="paper-deliver"></div>`, "paper-deliver-result")}
    ${evaluationHtml("paper")}`;

  bindPaper();
  bindConsistency();
  bindEvaluation("paper");
  await problem(document.getElementById("paper-problem"));
  await plan(document.getElementById("paper-plan"));
  await solve(document.getElementById("paper-solve"));
  await deliver(document.getElementById("paper-deliver"));
}
