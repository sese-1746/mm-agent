"""论文与文献综述的确定性评测对照工具。"""
from __future__ import annotations

import math
from collections import defaultdict

HIGHER_IS_BETTER = {
    "accuracy", "acc", "precision", "recall", "f1", "r2", "auc", "收益", "准确率", "召回率",
}
LOWER_IS_BETTER = {
    "mae", "mse", "rmse", "mape", "误差", "损失", "耗时", "时间", "成本", "偏差",
}


def _direction(metric: str, explicit: bool | None) -> str:
    if explicit is not None:
        return "higher" if explicit else "lower"
    key = metric.strip().lower().replace(" ", "")
    if key in HIGHER_IS_BETTER or any(x in key for x in ("accuracy", "precision", "recall", "f1", "auc", "准确", "收益")):
        return "higher"
    if key in LOWER_IS_BETTER or any(x in key for x in ("rmse", "mape", "mae", "误差", "损失", "成本", "耗时")):
        return "lower"
    return "higher"


def compare(scope: str, rows: list[dict]) -> dict:
    """按指标分组，对比基线、最佳值和每个方法的差值。"""
    if scope not in {"paper", "literature_review"}:
        raise ValueError("scope 只能是 paper 或 literature_review")
    if not rows:
        raise ValueError("至少提供一行评测数据")
    groups: dict[str, list[dict]] = defaultdict(list)
    for index, row in enumerate(rows, 1):
        method = str(row.get("method") or "").strip()
        metric = str(row.get("metric") or "").strip()
        if not method or not metric:
            raise ValueError(f"第 {index} 行缺少 method 或 metric")
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 行 value 不是数字") from exc
        if not math.isfinite(value):
            raise ValueError(f"第 {index} 行 value 必须是有限数字")
        item = {"method": method, "metric": metric, "value": value,
                "unit": str(row.get("unit") or ""),
                "source": str(row.get("source") or ""),
                "baseline": bool(row.get("baseline", False)),
                "higher_is_better": row.get("higher_is_better")}
        groups[metric].append(item)

    metrics = []
    conclusions = []
    for metric, items in groups.items():
        direction = _direction(metric, next((x["higher_is_better"] for x in items if x["higher_is_better"] is not None), None))
        baseline = next((x for x in items if x["baseline"]), items[0])
        best = (max if direction == "higher" else min)(items, key=lambda x: x["value"])
        enriched = []
        for item in items:
            delta = item["value"] - baseline["value"]
            improvement = (delta / abs(baseline["value"]) * 100) if baseline["value"] else None
            if direction == "lower":
                improvement = -improvement if improvement is not None else None
            enriched.append({**item, "delta_vs_baseline": delta,
                             "improvement_pct": improvement})
        unit = next((x["unit"] for x in items if x["unit"]), "")
        metrics.append({"metric": metric, "unit": unit, "direction": direction,
                        "baseline": {"method": baseline["method"], "value": baseline["value"]},
                        "best": {"method": best["method"], "value": best["value"]},
                        "rows": enriched})
        relation = "高于" if direction == "higher" else "低于"
        conclusions.append(f"{metric}：{best['method']} 最优（{best['value']}{unit}），{relation}基线 {baseline['method']} 的方向符合预期。")

    return {"scope": scope, "metric_count": len(metrics), "row_count": len(rows),
            "metrics": metrics, "conclusion": " ".join(conclusions)}
