"""run.py — 一条命令拉起 mmagent 全部服务。

用法（在仓库根目录）：
    python run.py                  # 建库(幂等) + 启动 API 与静态前端（默认 127.0.0.1:8000）
    python run.py --port 8010      # 换端口
    python run.py --no-db-init     # 跳过建库
    python run.py --allow-lan      # 允许非回环监听（透传给 cli serve，默认禁止）
    python run.py --no-static      # 只起 API，不挂载 frontend/

说明：
- 解释器优先用 .venv（H:\\mm-agent\\.venv\\Scripts\\python.exe）；不存在或启动失败就用当前解释器。
- 建库是幂等的（`cli db init` 内部为 IF NOT EXISTS），重复执行安全。
- 回环守卫与 frontend/ 存在性检查都复用 `cli serve` 的实现，本脚本不重复造规则。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def _usable_python(candidate: Path) -> bool:
    """确认 Windows 虚拟环境启动器没有指向已删除的基础解释器。"""
    if not candidate.exists():
        return False
    try:
        probe = subprocess.run(
            [str(candidate), "-c", "import sys; print(sys.executable)"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


if _usable_python(VENV_PYTHON):
    PYTHON = str(VENV_PYTHON)
else:
    PYTHON = sys.executable
    if VENV_PYTHON.exists():
        print("[run.py] .venv 当前不可用，回退到启动本脚本的解释器；请检查 .venv/pyvenv.cfg。",
              file=sys.stderr, flush=True)


def run_step(cmd: list[str], *, check: bool = True) -> int:
    """跑一步子命令，实时回显，返回退出码。"""
    print(f"[run.py] $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if check and proc.returncode != 0:
        print(f"[run.py] 该步骤退出码 {proc.returncode}，终止。", file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="一条命令拉起 mmagent（建库 + API + 静态前端）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="默认仅回环；非回环须配合 --allow-lan")
    ap.add_argument("--db", default="data/mm.db")
    ap.add_argument("--ckpt", default="data/checkpoints.db")
    ap.add_argument("--no-db-init", action="store_true", help="跳过建库（默认幂等执行）")
    ap.add_argument("--allow-lan", action="store_true", help="允许非回环监听（透传 cli serve）")
    ap.add_argument("--no-static", action="store_true", help="不挂载 frontend/，只起 API")
    args = ap.parse_args()

    print(f"[run.py] 仓库根：{REPO_ROOT}")
    print(f"[run.py] 解释器：{PYTHON}")
    if not (REPO_ROOT / "mmagent").is_dir():
        print("[run.py] 未找到 mmagent/ 包：请确认本脚本位于仓库根目录。", file=sys.stderr)
        return 2

    # ① 建库（幂等；失败即停，避免 serve 起来后 run/audit 路由才报错）
    if not args.no_db_init:
        run_step([PYTHON, "-m", "mmagent.cli", "db", "init", "--db", args.db])

    # ② 起服务（回环守卫 / frontend 存在性检查都由 cli serve 负责）
    cmd = [PYTHON, "-m", "mmagent.cli", "serve",
           "--host", args.host, "--port", str(args.port),
           "--db", args.db, "--ckpt", args.ckpt]
    if args.allow_lan:
        cmd.append("--allow-lan")
    if args.no_static:
        cmd.append("--no-static")
    print("[run.py] 启动服务（Ctrl+C 停止）…")
    return run_step(cmd, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
