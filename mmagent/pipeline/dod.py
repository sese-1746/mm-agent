"""FR-5 求解的 **DoD 判据**（规划书 §4.2.2 断言 / §8.3 coder 契约）。

职责边界（与 `runner.ok` **物理分离**，两者不是同一判据）：
- `runner.run_script()` 的 `ok` 只校验「脚本 **manifest.json 声明过** 的文件是否存在」
  （`mmagent/pipeline/runner.py:60`）——脚本不声明 `summary.csv` 就**永远抓不到**，
  缺口会被静默放过。
- 本模块**直接检查 workdir 的实际产物**，对应规划书 §4.2.2 的 P0-2 DoD：
    ① `results/summary.csv` 存在且非空（结果表，供 FR-6 `re-plot` 消费）；
    ② `figures/` 下至少一张 `*.png`；
    ③ 这些 PNG 的 `dpi ≈ 300`（规划书 §8.3：`savefig(dpi=300)`）。

输出形如 `{"ok": bool, "checks": {...}, "missing": [...]}`。
**缺 `summary.csv` 时 `ok=False`，即使 `run.ok=True`。**

PNG 的 dpi 由 `pHYs` chunk 计算（`dpi = 像素/米 × 0.0254`），用标准库 `struct` 解析，
**不引入新依赖**（不使用 Pillow）。缺 `pHYs` → dpi 不可核 → 判**不达标**（strict）。
"""
from __future__ import annotations

import struct
from pathlib import Path

# 规划书 §8.3 硬契约：结果表路径、出图目录、PNG dpi
SUMMARY_RELPATH = "results/summary.csv"
FIGURES_SUBDIR = "figures"
TARGET_DPI = 300.0
# dpi 容差：matplotlib 以 pHYs(11811 px/m) 落盘 → 反算 299.9994，需容差
DPI_TOLERANCE = 1.0

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _read_png_dpi(path: Path) -> tuple[float, float] | None:
    """读 PNG `pHYs` chunk，返回 `(dpi_x, dpi_y)`；无 `pHYs`/非 PNG → None。

    `pHYs` 数据 = x_ppm(4) y_ppm(4) unit(1)；unit==1 表示「像素/米」。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw.startswith(_PNG_SIGNATURE):
        return None
    pos = len(_PNG_SIGNATURE)
    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end + 4 > len(raw):     # 长度越界（截断/损坏）→ 放弃
            return None
        if ctype == b"pHYs" and length >= 9:
            x_ppm, y_ppm, unit = struct.unpack(">IIB", raw[data_start:data_start + 9])
            if unit != 1:               # unit==0：未知单位（比例），无法换算 dpi
                return None
            return round(x_ppm * 0.0254, 4), round(y_ppm * 0.0254, 4)
        if ctype == b"IEND":
            break
        pos = data_end + 4              # 跳过数据 + 4 字节 CRC
    return None


def _check_summary_csv(workdir: Path) -> dict:
    """结果表判据：存在 + 非空 + **至少含表头行 + 1 行数据**（task-008 N-GK-3 对齐提示词）。

    阈值口径：非空白行数 `>= 2`（第 1 行视为表头，其后至少 1 行数据）。
    仅表头（如 `month,score\\n`）= 1 行 → **不达标**。
    """
    path = workdir / SUMMARY_RELPATH
    exists = path.is_file()
    size = path.stat().st_size if exists else 0
    data_lines = 0
    reason = None
    if not exists:
        reason = f"{SUMMARY_RELPATH} 不存在（规划书 §4.2.2/§8.3 要求产出结果表）"
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            data_lines = sum(1 for ln in text.splitlines() if ln.strip())
        except OSError as exc:
            reason = f"{SUMMARY_RELPATH} 读取失败：{exc}"
        if reason is None:
            if size == 0 or data_lines == 0:
                reason = f"{SUMMARY_RELPATH} 为空文件（存在但无内容）"
            elif data_lines < 2:
                reason = (f"{SUMMARY_RELPATH} 只有表头、无数据行"
                          f"（要求 ≥1 行数据；当前非空白行 {data_lines}）")
    return {
        "ok": exists and size > 0 and data_lines >= 2,
        "path": SUMMARY_RELPATH,
        "exists": exists,
        "bytes": size if exists else None,
        "nonempty_lines": data_lines,
        "reason": reason,
    }


def _check_figures_png(workdir: Path) -> dict:
    fig_dir = workdir / FIGURES_SUBDIR
    files = sorted(str(p.relative_to(workdir)).replace("\\", "/")
                   for p in fig_dir.rglob("*.png")) if fig_dir.is_dir() else []
    reason = None if files else f"{FIGURES_SUBDIR}/ 下未找到任何 *.png"
    return {
        "ok": bool(files),
        "dir": FIGURES_SUBDIR,
        "count": len(files),
        "files": files,
        "reason": reason,
    }


def _check_png_dpi(workdir: Path, files: list[str]) -> dict:
    per_file: dict[str, dict] = {}
    all_ok = bool(files)
    for rel in files:
        dpi = _read_png_dpi(workdir / rel)
        if dpi is None:
            per_file[rel] = {"dpi": None, "ok": False,
                             "reason": "无 pHYs（dpi 不可核）"}
            all_ok = False
        else:
            within = abs(dpi[0] - TARGET_DPI) <= DPI_TOLERANCE \
                and abs(dpi[1] - TARGET_DPI) <= DPI_TOLERANCE
            per_file[rel] = {"dpi": [dpi[0], dpi[1]], "ok": within,
                             "reason": None if within
                             else f"dpi={dpi[0]:.4g} ≠ {TARGET_DPI:g}±{DPI_TOLERANCE:g}"}
            all_ok = all_ok and within
    return {
        "ok": all_ok,
        "target": TARGET_DPI,
        "tolerance": DPI_TOLERANCE,
        "per_file": per_file,
        "reason": None if all_ok else "存在 dpi≠300 或 dpi 不可核的 PNG",
    }


def check_dod(workdir: str | Path) -> dict:
    """检查 workdir 的**实际产物**是否满足规划书 §4.2.2 / §8.3 的 P0-2 DoD。

    返回 `{"ok": bool, "scope": "artifacts_only", "note": str,
    "checks": {"summary_csv": …, "figures_png": …, "png_dpi": …}, "missing": [...]}`；
    `missing` 为未达标项的可读说明（达标时为空列表）。

    ⚠️ **本判据作用域仅限产物**（`scope="artifacts_only"`）：它**不纳入本次 run 是否成功**
    （不计 `rc`/超时）。「脚本写出产物后崩溃（rc≠0）」也会 `ok=True`（实测 5/10）。
    故**成功判据必须是 `run.ok ∧ dod.ok`**（`cli solve` 已按此取退出码）；`dod.ok` 不得单独引用。
    """
    workdir = Path(workdir)
    summary = _check_summary_csv(workdir)
    figures = _check_figures_png(workdir)
    png_dpi = _check_png_dpi(workdir, figures["files"])

    missing: list[str] = []
    if not summary["ok"]:
        missing.append(summary["reason"] or f"{SUMMARY_RELPATH} 不达标")
    if not figures["ok"]:
        missing.append(figures["reason"] or f"{FIGURES_SUBDIR}/ 下无 PNG")
    elif not png_dpi["ok"]:
        missing.append(f"PNG dpi 不达标（要求 {TARGET_DPI:g}±{DPI_TOLERANCE:g}）：{png_dpi['reason']}")

    return {
        "ok": summary["ok"] and figures["ok"] and png_dpi["ok"],
        # N-GK-1（task-008）：显式标注作用域，避免被单独当成功判据引用。
        "scope": "artifacts_only",
        "note": ("本判据只核对 workdir **产物**（results/summary.csv 非空且有数据行 + "
                 "figures/ 下 dpi≈300 的 PNG），**不纳入本次 run 是否成功**"
                 "（未计 returncode/超时）——「写出产物后崩溃」也会 ok=True。"
                 "成功判据必须是 `run.ok ∧ dod.ok`（cli solve 已按此取退出码）。"),
        "checks": {"summary_csv": summary, "figures_png": figures, "png_dpi": png_dpi},
        "missing": missing,
    }
