"""FR-6 出图测试：dpi300 PNG 渲染 + figure_manifest.csv 契约 + 论文出图风格。

被测模块：mmagent/pipeline/render.py、mmagent/plotstyle.py
覆盖 success_criteria：
- render_figures 出 PNG（文件存在、非空）、PNG 元数据 dpi == 300
- manifest csv 表头固定、行数等于图数、路径与落盘文件一致
- 4 种图型（line/bar/scatter/hist）可用；非法 kind / 缺表报明确异常
- 论文风格：savefig.dpi=300、负号 ASCII、中文字体 fallback 查询可用
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
import pandas as pd
import pytest
from PIL import Image

from mmagent.pipeline.render import SUPPORTED_KINDS, render_figures
from mmagent.plotstyle import apply_paper_style, available_cjk_font

MANIFEST_COLUMNS = ["figure_id", "path", "kind", "source_table", "dpi"]


@pytest.fixture()
def tables() -> dict:
    return {
        "score": pd.DataFrame({
            "月份": ["1月", "2月", "3月", "4月"],
            "满意度": [88.0, 90.5, 92.0, 89.5],
        }),
        "profit": pd.DataFrame({
            "门店": ["A1", "A2", "A3"],
            "利润": [10.0, -2.5, 7.5],
        }),
    }


def _read_manifest(out_dir: Path) -> list[dict]:
    with (out_dir / "figure_manifest.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _png_dpi(path: Path) -> int:
    with Image.open(path) as im:
        assert im.format == "PNG"
        dpi = im.info.get("dpi", (0, 0))
    return int(round(dpi[0]))


# ------------------------------------------------------------------ 正常路径

def test_折线图产出PNG文件(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"kind": "line", "table": "score",
                                        "x": "月份", "y": "满意度"}], out_dir)

    assert len(manifest.figures) == 1
    path = Path(manifest.figures[0]["path"])
    assert path.exists()
    assert path.stat().st_size > 0


def test_PNG元数据dpi为300(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"figure_id": "fig_score", "kind": "line",
                                        "table": "score", "x": "月份",
                                        "y": "满意度"}], out_dir)

    assert manifest.figures[0]["dpi"] == 300
    assert _png_dpi(out_dir / "fig_score.png") == 300, "PNG pHYs 元数据应为 300dpi"


def test_manifest_csv表头固定且每图一行(tables, tmp_path):
    out_dir = tmp_path / "figs"

    render_figures(tables, [{"figure_id": "fig_a", "kind": "line", "table": "score",
                             "x": "月份", "y": "满意度"},
                            {"figure_id": "fig_b", "kind": "bar", "table": "profit",
                             "x": "门店", "y": "利润"}], out_dir)

    with (out_dir / "figure_manifest.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == MANIFEST_COLUMNS
        rows = list(reader)
    assert len(rows) == 2
    assert {r["figure_id"] for r in rows} == {"fig_a", "fig_b"}
    assert all(r["dpi"] == "300" for r in rows)


def test_manifest中路径与实际落盘文件一致(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"kind": "line", "table": "score",
                                        "x": "月份", "y": "满意度"}], out_dir)

    rows = _read_manifest(out_dir)
    assert [r["path"] for r in rows] == [f["path"] for f in manifest.figures]
    assert all(Path(r["path"]).exists() for r in rows)


def test_未指定figure_id时按顺序编号(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"kind": "line", "table": "score",
                                        "x": "月份", "y": "满意度"},
                                       {"kind": "bar", "table": "profit",
                                        "x": "门店", "y": "利润"}], out_dir)

    assert [f["figure_id"] for f in manifest.figures] == ["fig01", "fig02"]
    assert (out_dir / "fig01.png").exists()
    assert (out_dir / "fig02.png").exists()


def test_输出目录不存在时自动创建(tables, tmp_path):
    out_dir = tmp_path / "deep" / "nested" / "figs"

    render_figures(tables, [{"kind": "line", "table": "score",
                            "x": "月份", "y": "满意度"}], out_dir)

    assert (out_dir / "figure_manifest.csv").exists()


def test_四种图型都支持(tables, tmp_path):
    out_dir = tmp_path / "figs"
    specs = [
        {"figure_id": "line", "kind": "line", "table": "score", "x": "月份", "y": "满意度"},
        {"figure_id": "bar", "kind": "bar", "table": "profit", "x": "门店", "y": "利润"},
        {"figure_id": "scatter", "kind": "scatter", "table": "score",
         "x": "月份", "y": "满意度"},
        {"figure_id": "hist", "kind": "hist", "table": "score", "x": "满意度", "bins": 4},
    ]

    manifest = render_figures(tables, specs, out_dir)

    assert set(SUPPORTED_KINDS) == {"line", "bar", "scatter", "hist"}
    assert {f["figure_id"] for f in manifest.figures} == {"line", "bar", "scatter", "hist"}
    assert all(Path(f["path"]).exists() for f in manifest.figures)
    assert all(f["dpi"] == 300 for f in manifest.figures)


def test_hist只需x列(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"kind": "hist", "table": "score",
                                        "x": "满意度"}], out_dir)

    assert manifest.figures[0]["kind"] == "hist"
    assert Path(manifest.figures[0]["path"]).exists()


def test_图题写入且不报错(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"kind": "line", "table": "score",
                                        "x": "月份", "y": "满意度",
                                        "title": "各月满意度趋势"}], out_dir)

    assert Path(manifest.figures[0]["path"]).stat().st_size > 0


def test_manifest含图型与来源表字段(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [{"figure_id": "fig_x", "kind": "bar",
                                        "table": "profit", "x": "门店",
                                        "y": "利润"}], out_dir)

    row = _read_manifest(out_dir)[0]
    assert row["kind"] == "bar"
    assert row["source_table"] == "profit"
    assert row["figure_id"] == "fig_x"


def test_零规格时仍产出带表头的manifest(tables, tmp_path):
    out_dir = tmp_path / "figs"

    manifest = render_figures(tables, [], out_dir)

    assert manifest.figures == []
    rows = _read_manifest(out_dir)
    assert rows == []


# ------------------------------------------------------------------ 异常路径

def test_不支持的图型抛ValueError(tables, tmp_path):
    with pytest.raises(ValueError) as exc:
        render_figures(tables, [{"kind": "pie", "table": "score", "x": "月份"}],
                       tmp_path / "figs")
    assert "pie" in str(exc.value)
    assert all(k in str(exc.value) for k in SUPPORTED_KINDS)


def test_结果表中无该表时抛KeyError(tables, tmp_path):
    with pytest.raises(KeyError) as exc:
        render_figures(tables, [{"kind": "line", "table": "missing",
                                 "x": "月份", "y": "满意度"}], tmp_path / "figs")
    assert "missing" in str(exc.value)


def test_列名不存在时抛KeyError(tables, tmp_path):
    with pytest.raises(KeyError):
        render_figures(tables, [{"kind": "line", "table": "score",
                                 "x": "不存在的列", "y": "满意度"}], tmp_path / "figs")


def test_首图异常时不产出后续图(tables, tmp_path):
    out_dir = tmp_path / "figs"
    specs = [{"kind": "pie", "table": "score", "x": "月份"},
             {"figure_id": "never", "kind": "line", "table": "score",
              "x": "月份", "y": "满意度"}]

    with pytest.raises(ValueError):
        render_figures(tables, specs, out_dir)

    assert not (out_dir / "never.png").exists()


# ------------------------------------------------------------------ plotstyle

def test_论文风格设置dpi300与负号ASCII():
    apply_paper_style()

    assert matplotlib.rcParams["savefig.dpi"] == 300
    assert matplotlib.rcParams["figure.dpi"] == 300
    assert matplotlib.rcParams["axes.unicode_minus"] is False


def test_论文风格幂等():
    apply_paper_style()
    apply_paper_style()

    assert matplotlib.rcParams["savefig.dpi"] == 300


def test_中文字体查询返回字符串或None():
    font = available_cjk_font()

    assert font is None or isinstance(font, str)


def test_中文字体候选列表含宋体黑体():
    from mmagent.plotstyle import CJK_FONT_CANDIDATES

    assert "SimSun" in CJK_FONT_CANDIDATES
    assert "SimHei" in CJK_FONT_CANDIDATES
