"""FR-5 沙箱执行：在独立子进程中跑脚本并收集结果。

成功判据：returncode == 0 且（若存在 manifest.json）其声明的文件齐全。
manifest.json 约定：{"files": ["out/fig1.png", ...]}（相对 workdir）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

TAIL_CHARS = 4000  # stdout/stderr 截尾长度


def run_script(script_path: str | Path, workdir: str | Path,
               timeout_s: int = 300) -> dict:
    """以当前解释器在 workdir 下运行脚本，返回执行报告 dict。"""
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

    # manifest 校验（若脚本产出 manifest.json）
    manifest_files: list[str] = []
    manifest_missing: list[str] = []
    manifest_path = workdir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_files = list(manifest.get("files", []))
            manifest_missing = [f for f in manifest_files
                                if not (workdir / f).exists()]
        except (json.JSONDecodeError, OSError) as exc:
            manifest_missing = [f"<manifest 解析失败: {exc}>"]

    ok = (not timed_out) and returncode == 0 and not manifest_missing
    return {
        "returncode": returncode,
        "ok": ok,
        "timed_out": timed_out,
        "stdout_tail": stdout[-TAIL_CHARS:],
        "stderr_tail": stderr[-TAIL_CHARS:],
        "duration_s": duration,
        "produced_files": manifest_files if manifest_files else _scan_outputs(workdir),
        "manifest_missing": manifest_missing,
    }


def _scan_outputs(workdir: Path) -> list[str]:
    """无 manifest 时兜底：列出 workdir 下常见产物文件（相对路径）。"""
    exts = {".png", ".csv", ".json", ".xlsx", ".md", ".txt", ".docx"}
    return sorted(str(p.relative_to(workdir))
                  for p in workdir.rglob("*")
                  if p.is_file() and p.suffix.lower() in exts)
