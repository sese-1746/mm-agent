"""FR-5 沙箱执行：在独立子进程中跑脚本并收集结果。

成功判据：returncode == 0 且产物清单非空且声明的文件齐全。
产物清单（manifest.json）约定：{"files": ["out/fig1.png", ...]}（相对 workdir）。

task-008（P7）：`manifest.json` 的内容是**完全确定的**（就是产物清单），不该让 LLM 手写
`json.dumps`（实测 3/10 run 因漏 `import json` 崩在写 manifest 前）。故：
- `expected_outputs`（keyword-only，默认 None）：调用方（`solve_tool.solve`）把 coder 的
  `declared_outputs` 传进来；脚本**未**写出合法 manifest 时，由 runner **确定性地**写出
  `{"files": [...], "written_by": "runner"}`。
- 判定顺序：脚本自写合法 manifest（`manifest_source="script"`）→ 以它为准；
  否则 `expected_outputs` 非空 → runner 生成（`"runner"`）；都没有 → `"absent"`（沿用旧行为）。
- `expected_outputs=None` 时行为与历史**逐字一致**（既有调用点不受影响）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

TAIL_CHARS = 4000  # stdout/stderr 截尾长度


def run_script(script_path: str | Path, workdir: str | Path,
               timeout_s: int = 300, *,
               expected_outputs: list[str] | None = None) -> dict:
    """以当前解释器在 workdir 下运行脚本，返回执行报告 dict。

    `expected_outputs`（keyword-only，默认 None）：调用方声明的产物相对路径清单。脚本未写
    出合法 `manifest.json` 时，runner 用它确定性地生成 manifest（`manifest_source="runner"`）；
    为 None 时行为与历史一致（不改动既有调用点）。
    """
    script_path, workdir = Path(script_path), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    # 必须绝对化：subprocess 的 cwd=workdir 会让相对 script_path 被二次拼接
    script_path = script_path.resolve()
    workdir = workdir.resolve()

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(workdir), capture_output=True, text=True,
            timeout=timeout_s, encoding="utf-8", errors="replace")
        returncode, timed_out = proc.returncode, False
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode, timed_out = -1, True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr += f"\n[runner] 超时 {timeout_s}s，进程已终止"
    duration = round(time.monotonic() - start, 3)

    # 产物清单判定（task-008 P7 重写；语义保持「未声明任何产物 → 不判成功」）：
    #   ① 脚本自写**合法** manifest.json → 以它为准（manifest_source="script"）；
    #   ② 否则若 expected_outputs 非空 → runner 确定性写出 manifest（"runner"，write_manifest）；
    #   ③ 都没有 → "absent"，沿用旧 manifest_missing 行为。
    # task-003 修订保留：manifest 缺失/解析失败必须体现在 manifest_missing 里，否则
    # 「rc==0 但零产物」会被误报成功——正是要消灭的静默错误。
    manifest_path = workdir / "manifest.json"
    manifest_files: list[str] = []
    manifest_missing: list[str] = []
    manifest_source = "absent"
    parse_error: str | None = None

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_files = list(manifest.get("files", []))
            manifest_source = "script"
        except (json.JSONDecodeError, OSError) as exc:
            parse_error = f"<manifest 解析失败: {exc}>"

    expected = list(expected_outputs) if expected_outputs else []
    if manifest_source != "script" and expected:
        # 脚本没交出可用清单 → 由 runner 确定性地写（内容 = 声明的产物清单）
        manifest_path.write_text(
            json.dumps({"files": expected, "written_by": "runner"},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        manifest_files = expected
        manifest_source = "runner"

    if manifest_source == "absent":
        manifest_missing = [parse_error] if parse_error is not None \
            else ["manifest.json（脚本未按契约声明产物，无法确认是否真的产出）"]
    else:
        manifest_missing = [f for f in manifest_files if not (workdir / f).exists()]

    manifest_present = manifest_path.exists()
    # 「产物清单非空」也是必需项：空清单 = 脚本什么都没声明 → 不判成功。
    ok = (not timed_out) and returncode == 0 and bool(manifest_files) \
        and not manifest_missing
    return {
        "returncode": returncode,
        "ok": ok,
        "timed_out": timed_out,
        "stdout_tail": stdout[-TAIL_CHARS:],
        "stderr_tail": stderr[-TAIL_CHARS:],
        "duration_s": duration,
        "produced_files": manifest_files if manifest_files else _scan_outputs(workdir),
        "manifest_missing": manifest_missing,
        "manifest_present": manifest_present,
        # task-008 新增：产物清单来源（script / runner / absent）；既有键全保留
        "manifest_source": manifest_source,
    }


def _scan_outputs(workdir: Path) -> list[str]:
    """无 manifest 时兜底：列出 workdir 下常见产物文件（相对路径）。"""
    exts = {".png", ".csv", ".json", ".xlsx", ".md", ".txt", ".docx"}
    return sorted(str(p.relative_to(workdir))
                  for p in workdir.rglob("*")
                  if p.is_file() and p.suffix.lower() in exts)
