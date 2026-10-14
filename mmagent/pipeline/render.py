"""FR-6 出图：按 figure_specs 从结果表渲染 PNG（dpi300），写 figure_manifest.csv。

figure_specs 条目：{figure_id?, kind: line/bar/scatter/hist, table, x, y?, title?}
- hist 只需 x；其余需要 x+y；table 为 result_tables 的键。
- 返回 FigureManifest；每张图校验 PNG 元数据 dpi == 300。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..plotstyle import apply_paper_style, available_cjk_font

SUPPORTED_KINDS = ("line", "bar", "scatter", "hist")


@dataclass
class FigureManifest:
    """出图清单：figures 为 {figure_id, path, kind, source_table, dpi} 字典列表。"""
    figures: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_csv(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=["figure_id", "path", "kind", "source_table", "dpi"])
            writer.writeheader()
            writer.writerows(self.figures)
        return path


def _png_dpi(path: Path) -> tuple[int, int]:
    """读 PNG 元数据 dpi（PIL 为 matplotlib 传递依赖，必然可用）。"""
    from PIL import Image

    with Image.open(path) as im:
        dpi = im.info.get("dpi", (0, 0))
    return int(round(dpi[0])), int(round(dpi[1]))


def render_figures(result_tables: dict, figure_specs: list[dict],
                   out_dir: str | Path) -> FigureManifest:
    """按规格出图，写 figure_manifest.csv 并返回 manifest 对象。"""
    import matplotlib.pyplot as plt

    apply_paper_style()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = FigureManifest()

    if available_cjk_font() is None:
        manifest.warnings.append("未找到中文字体（SimSun/SimHei/微软雅黑），图题可能出现缺字形")

    for i, spec in enumerate(figure_specs, 1):
        kind = spec.get("kind", "line")
        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"不支持的图型 kind={kind!r}，可选 {SUPPORTED_KINDS}")
        table_name = spec["table"]
        if table_name not in result_tables:
            raise KeyError(f"result_tables 中无表 {table_name!r}")
        df = result_tables[table_name]
        figure_id = spec.get("figure_id") or f"fig{i:02d}"

        fig, ax = plt.subplots()
        if kind == "hist":
            ax.hist(df[spec["x"]].dropna(), bins=spec.get("bins", 20))
            ax.set_xlabel(str(spec["x"]))
            ax.set_ylabel("频数")
        else:
            x, y = df[spec["x"]], df[spec["y"]]
            if kind == "line":
                ax.plot(x, y)
            elif kind == "bar":
                ax.bar(x, y)
            else:
                ax.scatter(x, y, s=spec.get("s", 16))
            ax.set_xlabel(str(spec["x"]))
            ax.set_ylabel(str(spec["y"]))
        if spec.get("title"):
            ax.set_title(str(spec["title"]))

        path = out_dir / f"{figure_id}.png"
        fig.savefig(path, dpi=300)
        plt.close(fig)

        dpi_x, _ = _png_dpi(path)
        if dpi_x != 300:
            manifest.warnings.append(f"{figure_id}: PNG dpi 元数据={dpi_x}，期望 300")
        manifest.figures.append({
            "figure_id": figure_id, "path": str(path), "kind": kind,
            "source_table": table_name, "dpi": dpi_x,
        })

    manifest.to_csv(out_dir / "figure_manifest.csv")
    return manifest
