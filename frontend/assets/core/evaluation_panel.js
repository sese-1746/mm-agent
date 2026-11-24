/* 论文/文献综述共用的评测对照面板。 */
import { apiPost, errorHtml } from "./api.js";
import { esc, setBusy, setOut } from "./dom.js";

const EXAMPLE = JSON.stringify([
  { method: "本文模型", metric: "RMSE", value: 1.2, unit: "", baseline: false },
  { method: "基线模型", metric: "RMSE", value: 1.8, unit: "", baseline: true },
  { method: "本文模型", metric: "R2", value: 0.91, unit: "", baseline: false },
  { method: "基线模型", metric: "R2", value: 0.84, unit: "", baseline: true },
], null, 2);

function prefix(scope) { return scope === "literature_review" ? "lit-eval" : "paper-eval"; }

export function evaluationHtml(scope) {
  const p = prefix(scope);
  const title = scope === "literature_review" ? "文献方法评测对照" : "论文模型评测对照";
  return `<article class="card evaluation-card"><h3 class="card-title">${title}</h3>
    <p class="muted card-note">按“方法 + 指标 + 数值”输入评测结果，勾选基线后自动计算最佳方法、相对基线差值和改进比例。指标名含 RMSE/MAE/误差时默认越低越好，含 Accuracy/R²/F1/收益时默认越高越好；也可用 <code>higher_is_better</code> 明确指定。</p>
    <label class="field">评测数据 JSON
      <textarea id="${p}-rows" rows="10">${esc(EXAMPLE)}</textarea>
    </label>
    <div class="btn-row"><button type="button" id="${p}-run" class="btn primary">生成评测对照</button><span class="hint">POST <code>/api/v1/evaluation/compare</code></span></div>
    <div class="result" id="${p}-out"></div></article>`;
}

function metricHtml(metric) {
  const rows = (metric.rows || []).map((row) => `<tr><td>${esc(row.method)}</td><td>${esc(row.value)}${esc(metric.unit)}</td><td>${esc(row.delta_vs_baseline)}</td><td>${row.improvement_pct === null ? "—" : `${esc(Number(row.improvement_pct).toFixed(2))}%`}</td><td>${row.method === metric.best.method ? '<span class="tag ok">最佳</span>' : row.method === metric.baseline.method ? '<span class="tag">基线</span>' : ""}</td></tr>`).join("");
  const direction = metric.direction === "higher" ? "越高越好" : "越低越好";
  return `<h4>${esc(metric.metric)} <span class="hint">（${direction}，基线：${esc(metric.baseline.method)}，最佳：${esc(metric.best.method)}）</span></h4>
    <figure><table role="grid"><thead><tr><th>方法</th><th>数值</th><th>相对基线差值</th><th>改进比例</th><th>标记</th></tr></thead><tbody>${rows}</tbody></table></figure>`;
}

function resultHtml(data) {
  return `<div class="alert ok"><strong>评测对照完成</strong>：${esc(data.metric_count)} 个指标，${esc(data.row_count)} 行数据。</div>
    ${(data.metrics || []).map(metricHtml).join("")}
    <p><strong>综合结论：</strong>${esc(data.conclusion || "暂无结论")}</p>`;
}

function validateRows(rows) {
  if (!rows.length) return "至少提供一行评测数据。";
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i];
    if (!row || typeof row !== "object") return `第 ${i + 1} 行必须是对象。`;
    if (!String(row.method || "").trim() || !String(row.metric || "").trim()) {
      return `第 ${i + 1} 行缺少 method 或 metric。`;
    }
    const value = Number(row.value);
    if (!Number.isFinite(value)) return `第 ${i + 1} 行 value 必须是有限数字。`;
  }
  return "";
}

export function bindEvaluation(scope) {
  const p = prefix(scope);
  const button = document.getElementById(`${p}-run`);
  if (!button) return;
  button.addEventListener("click", async () => {
    let rows;
    try {
      rows = JSON.parse(document.getElementById(`${p}-rows`).value || "[]");
      if (!Array.isArray(rows)) throw new Error("根节点必须是 JSON 数组");
    } catch (err) {
      setOut(`${p}-out`, `<div class="alert warn"><strong>评测数据 JSON 无效</strong><p>${esc(err.message)}</p></div>`);
      return;
    }
    const validationError = validateRows(rows);
    if (validationError) {
      setOut(`${p}-out`, `<div class="alert warn"><strong>评测数据无效</strong><p>${esc(validationError)}</p></div>`);
      return;
    }
    setBusy(`${p}-out`, "正在计算评测对照…");
    button.disabled = true;
    try {
      const data = await apiPost("/evaluation/compare", { scope, rows });
      setOut(`${p}-out`, resultHtml(data));
    } catch (err) {
      setOut(`${p}-out`, errorHtml(err));
    } finally {
      button.disabled = false;
    }
  });
}
