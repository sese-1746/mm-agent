/* views/plan.js —— 阶段 02 建模方案（#/plan）。宿主是 core/plan_panel.js。
 *
 * 本页只做一件事：为每个子问题生成建模方法卡片（FR-3）——方法 / 假设 / 评价指标 / 适用条件，
 * 产出 kind=plan 产物。
 *
 * 方案定稿**不在这里**：人工卡点 plan_selection 在「05 合规交付」页的全链路编排面板里裁定。
 * 本页只负责把候选做出来；选哪一个由人决定，页面不替人拍板。
 */
import { panel } from "../core/dom.js";
import { bindPlan, planHtml } from "../core/plan_panel.js";

export function render(root) {
  root.innerHTML = `
    <article class="card">
      <h3 class="card-title">本页做什么</h3>
      <p class="muted card-note">贴入题面后生成<strong>建模方法卡片</strong>：每个子问题一张卡，
        写明方法、假设、评价指标、适用条件（FR-3）。产出登记为 <code>kind=plan</code> 产物，
        供交付页的 <code>plan_selection</code> 卡点挑选。</p>
      <p class="muted card-note">注意：这里只产出<strong>候选</strong>。方案定稿发生在人工卡点
        <code>plan_selection</code>，该卡点在「05 合规交付」页的全链路编排面板里裁定
        （<a href="#/deliver">#/deliver</a>）。</p>
    </article>

    ${panel("建模方法卡片（FR-3）", planHtml(), "r-plan")}
  `;

  bindPlan();
}
