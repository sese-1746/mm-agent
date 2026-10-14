"""HITL 裁定语义回归（修复轮 2）——裁定驱动控制流 / review payload / 受控错误码。

被测模块：``mmagent/agents/coordinator.py``、``mmagent/cli.py``（``run`` 子命令）

覆盖需求编号：
- **HITL 裁定语义（FR-5 卡点契约的加强）**：裁定**驱动控制流**，不再"只记录不消费"
  - `sanity_blocking`: `accept` → render；`fix_and_rerun` → 回边重入 solve（上限 `MAX_RERUN`）；`abort` → halt
  - `pre_export_compliance`: `approve` → export；`reject` → halt
  - `caliber_conflict`: 三种合法裁定都只描述"如何解决" → 一律继续到 profile
  - `halt` 是**正常终态**：`stage=HALTED`、`halted=True`、`done=False`，且**绝不产出 export 产物**
- **F2 review payload**：`interrupt()` payload 恒含 `gate`/`stage`/`question`/**`review`** 四键；
  `review` 携带待审材料，无材料时为空容器（**绝不为 None**）
- **F4 退出码契约**：`run status` 未知 run_id → **exit 4**；2=裁定非法 / 3=台账未初始化或检查点库不可用
- **F3 受控错误**：检查点库不可用（只读/非文件）→ **exit 3** + 明确提示，**无原始 traceback**

约束：只读被测实现，不改 `mmagent/`；CLI 用例用 `tmp_path` 隔离 `--ckpt`/`--db`；
子进程用 `sys.executable` 绝对路径 + 超时保护。

> 负向断言说明：`assert "stage:export" not in log` 用的是 **list 成员判断（精确相等）**，
> 不是子串匹配 —— 否则 `"gate:pre_export_compliance=reject"` 里的 `export` 会造成误判。
> 本文件对关键路径另外用**整表相等**断言，从根上避免该陷阱。
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from mmagent.agents.coordinator import (
    GATE_CALIBER,
    GATE_COMPLIANCE,
    GATE_SANITY,
    MAX_RERUN,
    CheckpointUnavailable,
    Coordinator,
    UnknownRunError,
)
from mmagent.store.db import init_db

REPO_ROOT = Path(__file__).resolve().parents[1]
PROC_TIMEOUT = 180

# ---------------------------------------------------------------- 期望 log（整表相等，最严格）

LOG_SKIP_TO_ASSEMBLE = [
    "stage:intake", "gate:caliber_conflict=skip", "stage:profile", "stage:plan", "stage:solve",
    "gate:sanity_blocking=skip", "stage:render", "stage:assemble",
]
LOG_REJECT = LOG_SKIP_TO_ASSEMBLE + ["gate:pre_export_compliance=reject", "stage:halt"]
LOG_APPROVE = LOG_SKIP_TO_ASSEMBLE + ["gate:pre_export_compliance=approve", "stage:export"]
LOG_ABORT = [
    "stage:intake", "gate:caliber_conflict=skip", "stage:profile", "stage:plan", "stage:solve",
    "gate:sanity_blocking=abort", "stage:halt",
]
LOG_RERUN_1 = [
    "stage:intake", "gate:caliber_conflict=skip", "stage:profile", "stage:plan", "stage:solve",
    "gate:sanity_blocking=fix_and_rerun", "rerun:solve", "stage:solve",
]
LOG_RERUN_2_HALT = LOG_RERUN_1 + ["gate:sanity_blocking=fix_and_rerun", "stage:halt"]
LOG_FULL_DONE = [
    "stage:intake", "gate:caliber_conflict=use_mapping", "stage:profile", "stage:plan",
    "stage:solve", "gate:sanity_blocking=accept", "stage:render", "stage:assemble",
    "gate:pre_export_compliance=approve", "stage:export",
]


class Sandbox(NamedTuple):
    db: Path
    ckpt: Path


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    """临时沙箱：台账库显式初始化，检查点库独立（不碰仓库 data/）。"""
    db = tmp_path / "mm.db"
    init_db(db)
    return Sandbox(db=db, ckpt=tmp_path / "ckpt.db")


@pytest.fixture()
def coord(sandbox: Sandbox):
    c = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------- 子进程基础设施

_CLI_DRIVER_TMPL = '''\
"""子进程入口：调用真实 CLI main()（不打印 pid，本文件不需要 pid 证据）。"""
import sys

sys.path.insert(0, __REPO__)
from mmagent.cli import main

raise SystemExit(main(sys.argv[1:]))
'''


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _cli(sandbox: Sandbox, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """以独立进程调用真实 CLI。"""
    driver = tmp_path / "drv_cli.py"
    driver.write_text(_CLI_DRIVER_TMPL.replace("__REPO__", repr(str(REPO_ROOT))), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(driver), *args],
        cwd=str(REPO_ROOT), env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=PROC_TIMEOUT,
    )


def _run_args(sandbox: Sandbox, run_id: str) -> tuple[str, ...]:
    return ("--run-id", run_id, "--ckpt", str(sandbox.ckpt), "--db", str(sandbox.db))


def _cli_json(proc: subprocess.CompletedProcess) -> dict:
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"子进程未输出 JSON：stdout={proc.stdout!r} stderr={proc.stderr!r}")


def _events(db: Path, event_type: str) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT json_extract(payload, '$.run_id') AS run_id, payload"
            " FROM events WHERE event_type = ?", (event_type,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _interrupt_payload(coord_: Coordinator, run_id: str) -> dict:
    """取当前挂起卡点的原始 interrupt payload（用于校验「恒含四键」）。"""
    snap = coord_._graph.get_state({"configurable": {"thread_id": run_id}})
    for task in (snap.tasks or ()):
        for iv in (task.interrupts or ()):
            return iv.value
    raise AssertionError(f"run {run_id!r} 当前没有挂起的 interrupt")


# ================================================================== F1 裁定驱动控制流

class TestDecisionDrivenFlow:
    """裁定必须真正改变去向：否定裁定 → halt（不产出 export），肯定裁定 → DONE。"""

    def test_compliance_reject_终止于HALTED且不走到export(self, coord: Coordinator):
        run_id = "reject-1"
        coord.start(run_id, {})                              # 无口径冲突/无 sanity 阻断
        st = coord.resume(run_id, GATE_COMPLIANCE, {"decision": "reject", "note": "披露不全"})

        assert st.stage == "HALTED"
        assert st.halted is True
        assert st.done is False, "halt 是正常终态，但不得被认作 done"
        assert st.paused is False
        assert st.gate is None
        assert st.log == LOG_REJECT, f"log 与契约不符：{st.log}"
        # list 成员判断 = 精确相等（非子串），避免 '...pre_export_compliance=reject' 误判
        assert "stage:export" not in st.log, "reject 后不得执行 export 节点"

    def test_sanity_abort_终止于HALTED且不进入render与assemble(self, coord: Coordinator):
        run_id = "abort-1"
        coord.start(run_id, {"sanity_blocking": True})
        st = coord.resume(run_id, GATE_SANITY, {"decision": "abort", "note": "中止本轮"})

        assert (st.stage, st.halted, st.done, st.paused) == ("HALTED", True, False, False)
        assert st.log == LOG_ABORT, f"log 与契约不符：{st.log}"
        assert "stage:render" not in st.log
        assert "stage:assemble" not in st.log
        assert "stage:export" not in st.log

    def test_halt写hitl_run_halted事件且不写run_finished(self, coord: Coordinator, sandbox: Sandbox):
        run_id = "reject-evt-1"
        coord.start(run_id, {})
        coord.resume(run_id, GATE_COMPLIANCE, {"decision": "reject", "note": "n"})

        halted = [e for e in _events(sandbox.db, "hitl.run_halted") if e["run_id"] == run_id]
        assert len(halted) == 1, "halt 必须留下 1 条 hitl.run_halted"
        payload = json.loads(halted[0]["payload"])
        assert payload["gate"] == GATE_COMPLIANCE
        assert payload["decision"] == {"decision": "reject", "note": "n"}
        assert payload["stage"] == "HALTED"
        assert _events(sandbox.db, "hitl.run_finished") == [], "halt 不得被记为 run_finished"

    @pytest.mark.parametrize(
        "context,steps",
        [
            pytest.param({"sanity_blocking": True},
                         [(GATE_SANITY, {"decision": "accept", "note": "已复核"}),
                          (GATE_COMPLIANCE, {"decision": "approve", "note": "合规确认"})],
                         id="sanity-accept"),
            pytest.param({},
                         [(GATE_COMPLIANCE, {"decision": "approve", "note": "合规确认"})],
                         id="compliance-approve"),
        ],
    )
    def test_肯定裁定仍正常到达DONE(self, coord: Coordinator, context: dict, steps: list):
        """防止「修否定出口」把正常路径一起断掉。

        注意 `compliance_confirmed` 默认 False → 卡点 3 一定会停，故 accept 之后仍需
        一次 approve 才到 DONE。
        """
        run_id = "ok-" + "-".join(gate for gate, _ in steps)
        coord.start(run_id, context)

        status = None
        for gate, decision in steps:
            status = coord.resume(run_id, gate, decision)
        assert status is not None
        assert (status.done, status.halted, status.paused, status.stage) == (True, False, False, "DONE")
        assert status.log[-1] == "stage:export"
        assert "stage:halt" not in status.log

    def test_三卡点全流程肯定裁定到达DONE(self, coord: Coordinator):
        run_id = "full-ok-1"
        coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
        coord.resume(run_id, GATE_CALIBER, {"resolution": "use_mapping", "note": "n"})
        coord.resume(run_id, GATE_SANITY, {"decision": "accept", "note": "n"})
        st = coord.resume(run_id, GATE_COMPLIANCE, {"decision": "approve", "note": "n"})
        assert (st.done, st.stage) == (True, "DONE")
        assert st.log == LOG_FULL_DONE

    @pytest.mark.parametrize("resolution", ["use_mapping", "keep_raw", "human_override"])
    def test_caliber三种合法裁定均继续推进(self, coord: Coordinator, resolution: str):
        """口径裁定只有「如何解决」语义 → 三种取值都应推进到下一个卡点。"""
        run_id = f"cal-{resolution}"
        coord.start(run_id, {"caliber_conflicts": True})
        st = coord.resume(run_id, GATE_CALIBER, {"resolution": resolution, "note": "n"})
        assert (st.paused, st.gate) == (True, GATE_COMPLIANCE), "应推进到卡点 3"
        assert f"gate:caliber_conflict={resolution}" in st.log
        assert "stage:halt" not in st.log


class TestRerunBound:
    """`fix_and_rerun` 回边上限（防无限循环）。"""

    def test_MAX_RERUN常量为1(self):
        assert MAX_RERUN == 1, "回边阈值必须存在且为 1（防止图无限循环）"

    def test_fix_and_rerun首次重入solve后再次挂起(self, coord: Coordinator):
        run_id = "rerun-1"
        coord.start(run_id, {"sanity_blocking": True})
        st = coord.resume(run_id, GATE_SANITY, {"decision": "fix_and_rerun", "note": "重算"})

        assert (st.paused, st.gate, st.stage) == (True, GATE_SANITY, "SOLVE"), "应回到 sanity 卡点再评审"
        assert st.done is False and st.halted is False
        assert st.log == LOG_RERUN_1, f"log 与契约不符：{st.log}"
        assert st.log.count("rerun:solve") == 1, "首次回边只重入一次"
        assert st.log.count("stage:solve") == 2, "solve 阶段应被重入一次"

    def test_fix_and_rerun第二次超上限转HALTED(self, coord: Coordinator):
        run_id = "rerun-2"
        coord.start(run_id, {"sanity_blocking": True})
        coord.resume(run_id, GATE_SANITY, {"decision": "fix_and_rerun", "note": "第一次"})
        st = coord.resume(run_id, GATE_SANITY, {"decision": "fix_and_rerun", "note": "第二次"})

        assert (st.stage, st.halted, st.done, st.paused) == ("HALTED", True, False, False)
        assert st.log == LOG_RERUN_2_HALT, f"log 与契约不符：{st.log}"
        assert st.log.count("rerun:solve") == 1, "超限时不得再重入 solve"
        assert "stage:render" not in st.log

    def test_回边后接受裁定可以正常走完(self, coord: Coordinator):
        """回边不是死路：重入 solve 后 accept 仍应到 DONE。"""
        run_id = "rerun-3"
        coord.start(run_id, {"sanity_blocking": True})
        coord.resume(run_id, GATE_SANITY, {"decision": "fix_and_rerun", "note": "重算"})
        st = coord.resume(run_id, GATE_SANITY, {"decision": "accept", "note": "复核通过"})
        assert (st.paused, st.gate) == (True, GATE_COMPLIANCE)
        st = coord.resume(run_id, GATE_COMPLIANCE, {"decision": "approve", "note": "n"})
        assert (st.done, st.stage) == (True, "DONE")
        assert st.log.count("rerun:solve") == 1


# ================================================================== F2 review payload

class TestReviewPayload:
    """`interrupt()` payload 的四键契约与待审材料透传。"""

    @pytest.mark.parametrize(
        "gate,context",
        [(GATE_CALIBER, {"caliber_conflicts": True}),
         (GATE_SANITY, {"sanity_blocking": True}),
         (GATE_COMPLIANCE, {})],
        ids=["卡点1", "卡点2", "卡点3"],
    )
    def test_挂起payload恒含四键且review非None(self, coord: Coordinator, gate: str, context: dict):
        run_id = f"rev-keys-{gate}"
        st = coord.start(run_id, context)
        assert st.paused and st.gate == gate

        payload = _interrupt_payload(coord, run_id)
        assert set(payload) == {"gate", "stage", "question", "review"}, (
            f"payload 必须恒含 gate/stage/question/review 四键，实际 {sorted(payload)}")
        assert payload["review"] is not None, "无材料时也必须是空容器，绝不为 None"
        assert payload["review"] == {}, "无材料时应为空容器"
        assert st.review is not None, "RunStatus.review 在挂起时不得为 None"
        assert st.review == {}

    @pytest.mark.parametrize(
        "gate,context,material",
        [
            (GATE_CALIBER, {"caliber_conflicts": True, "conflicts": [{"field": "评级"}]},
             [{"field": "评级"}]),
            (GATE_SANITY, {"sanity_blocking": True, "sanity_items": [{"check": "unit_mismatch"}]},
             [{"check": "unit_mismatch"}]),
            (GATE_COMPLIANCE, {"missing_disclosure": ["AI 使用声明", "数据来源"]},
             ["AI 使用声明", "数据来源"]),
        ],
        ids=["口径材料", "sanity材料", "合规材料"],
    )
    def test_有材料时review携带明细(self, coord: Coordinator, gate: str, context: dict, material):
        run_id = f"rev-mat-{gate}"
        st = coord.start(run_id, context)
        assert st.paused and st.gate == gate
        assert st.review == material, f"{gate} 的 review 应透传待审材料"
        assert _interrupt_payload(coord, run_id)["review"] == material

    def test_非挂起状态review为None(self, coord: Coordinator):
        """边界：跑到 DONE / halt 之后没有待审材料 → review 为 None（不是空容器）。"""
        done_run = "rev-none-done"
        coord.start(done_run, {"compliance_confirmed": True})
        done = coord.status(done_run)
        assert done.done and done.review is None

        halt_run = "rev-none-halt"
        coord.start(halt_run, {})
        halted = coord.resume(halt_run, GATE_COMPLIANCE, {"decision": "reject", "note": "n"})
        assert halted.halted and halted.review is None

    def test_CLI_start_conflicts时review能看到冲突明细(self, sandbox: Sandbox, tmp_path: Path):
        proc = _cli(sandbox, tmp_path, "run", "start", *_run_args(sandbox, "rev-cli-1"), "--conflicts")
        assert proc.returncode == 0, proc.stderr
        st = _cli_json(proc)
        assert st["paused"] is True and st["gate"] == GATE_CALIBER
        assert isinstance(st["review"], list) and st["review"], (
            f"--conflicts 时应给出非空冲突明细，实际 {st['review']!r}")

    def test_CLI_start_conflict_detail透传自定义材料(self, sandbox: Sandbox, tmp_path: Path):
        detail = json.dumps([{"field": "评级", "reason": "自定义口径"}], ensure_ascii=False)
        proc = _cli(sandbox, tmp_path, "run", "start", *_run_args(sandbox, "rev-cli-2"),
                    "--conflicts", "--conflict-detail", detail)
        assert proc.returncode == 0, proc.stderr
        assert _cli_json(proc)["review"] == [{"field": "评级", "reason": "自定义口径"}]

    def test_CLI_start_conflict_detail非法JSON返回码2(self, sandbox: Sandbox, tmp_path: Path):
        proc = _cli(sandbox, tmp_path, "run", "start", *_run_args(sandbox, "rev-cli-3"),
                    "--conflicts", "--conflict-detail", "{不是JSON")
        assert proc.returncode == 2, f"实际 {proc.returncode}；stderr={proc.stderr!r}"
        assert "不是合法 JSON" in proc.stderr


# ================================================================== F4 未知 run_id

class TestUnknownRunStatus:
    """`run status` 对不存在的 run_id 必须显式失败（exit 4），不得静默 exit 0。"""

    def test_未知run_id退出码4并给出明确提示(self, sandbox: Sandbox, tmp_path: Path):
        proc = _cli(sandbox, tmp_path, "run", "status", *_run_args(sandbox, "never-existed"))
        assert proc.returncode == 4, (
            f"未知 run_id 应 exit 4，实际 {proc.returncode}；stdout={proc.stdout!r}")
        assert "未知 run_id" in proc.stderr, f"stderr 须明确原因，实际 {proc.stderr!r}"
        assert not any(line.strip().startswith("{") for line in proc.stdout.splitlines()), \
            "不得输出看似正常的 JSON 快照"

    def test_api层未知run_id抛UnknownRunError(self, coord: Coordinator):
        with pytest.raises(UnknownRunError, match="未知 run_id"):
            coord.status("never-existed")

    def test_已存在run的status退出码0(self, sandbox: Sandbox, tmp_path: Path):
        started = _cli(sandbox, tmp_path, "run", "start", *_run_args(sandbox, "exists-1"), "--conflicts")
        assert started.returncode == 0, started.stderr
        proc = _cli(sandbox, tmp_path, "run", "status", *_run_args(sandbox, "exists-1"))
        assert proc.returncode == 0, proc.stderr
        st = _cli_json(proc)
        assert (st["paused"], st["gate"]) == (True, GATE_CALIBER)

    def test_终止后run的status退出码0且halted为真(self, sandbox: Sandbox, tmp_path: Path):
        _cli(sandbox, tmp_path, "run", "start", *_run_args(sandbox, "halted-1"))
        rejected = _cli(sandbox, tmp_path, "run", "resume", *_run_args(sandbox, "halted-1"),
                        "--gate", GATE_COMPLIANCE, "--decision",
                        json.dumps({"decision": "reject", "note": "n"}, ensure_ascii=False))
        assert rejected.returncode == 0, rejected.stderr
        proc = _cli(sandbox, tmp_path, "run", "status", *_run_args(sandbox, "halted-1"))
        assert proc.returncode == 0
        st = _cli_json(proc)
        assert (st["halted"], st["done"], st["stage"]) == (True, False, "HALTED")

    def test_台账未初始化时exit3优先于exit4(self, sandbox: Sandbox, tmp_path: Path):
        """前置校验顺序：台账未初始化（3）先于 run_id 不存在（4）。"""
        missing_db = tmp_path / "nope" / "mm.db"
        proc = _cli(sandbox, tmp_path, "run", "status", "--run-id", "never-existed",
                    "--ckpt", str(sandbox.ckpt), "--db", str(missing_db))
        assert proc.returncode == 3, f"实际 {proc.returncode}；stderr={proc.stderr!r}"
        assert "db init" in proc.stderr


# ================================================================== F3 检查点库不可用

class TestCheckpointUnavailable:
    """检查点库不可用（只读 / 非文件）→ 受控 exit 3 + 明确提示，无原始 traceback。"""

    @staticmethod
    def _make_readonly(path: Path) -> None:
        os.chmod(str(path), stat.S_IREAD)

    @staticmethod
    def _make_writable(path: Path) -> None:
        if path.exists():
            os.chmod(str(path), stat.S_IREAD | stat.S_IWRITE)

    def test_检查点库只读时CLI_start退出码3且无traceback(self, sandbox: Sandbox, tmp_path: Path):
        ro = tmp_path / "ro_ckpt.db"
        ro.write_bytes(b"")
        self._make_readonly(ro)
        try:
            proc = _cli(sandbox, tmp_path, "run", "start", "--run-id", "ro-1",
                        "--ckpt", str(ro), "--db", str(sandbox.db))
            assert proc.returncode == 3, (
                f"只读检查点库应 exit 3，实际 {proc.returncode}；stdout={proc.stdout!r}")
            assert "Traceback" not in proc.stderr, f"不得抛原始 traceback：{proc.stderr!r}"
            assert "Traceback" not in proc.stdout
            assert "检查点库" in proc.stderr, f"stderr 须有明确提示：{proc.stderr!r}"
        finally:
            self._make_writable(ro)

    def test_检查点库只读时CLI_resume退出码3且无traceback(self, sandbox: Sandbox, tmp_path: Path):
        started = _cli(sandbox, tmp_path, "run", "start", *_run_args(sandbox, "ro-2"), "--conflicts")
        assert started.returncode == 0, started.stderr

        touched = [sandbox.ckpt, Path(f"{sandbox.ckpt}-wal"), Path(f"{sandbox.ckpt}-shm")]
        for path in touched:
            if path.exists():
                self._make_readonly(path)
        try:
            proc = _cli(sandbox, tmp_path, "run", "resume", *_run_args(sandbox, "ro-2"),
                        "--gate", GATE_CALIBER, "--decision",
                        json.dumps({"resolution": "keep_raw", "note": "n"}, ensure_ascii=False))
            assert proc.returncode == 3, (
                f"只读检查点库应 exit 3，实际 {proc.returncode}；stdout={proc.stdout!r}")
            assert "Traceback" not in proc.stderr, f"不得抛原始 traceback：{proc.stderr!r}"
            assert "检查点库不可用" in proc.stderr, f"stderr 须有明确提示：{proc.stderr!r}"
        finally:
            for path in touched:
                self._make_writable(path)

    def test_检查点路径不是数据库文件时退出码3(self, sandbox: Sandbox, tmp_path: Path):
        """把 --ckpt 指向一个**目录**：连不上 SQLite → 受控 exit 3。"""
        as_dir = tmp_path / "ckpt_is_a_dir"
        as_dir.mkdir()
        proc = _cli(sandbox, tmp_path, "run", "start", "--run-id", "dir-1",
                    "--ckpt", str(as_dir), "--db", str(sandbox.db))
        assert proc.returncode == 3, f"实际 {proc.returncode}；stdout={proc.stdout!r}"
        assert "Traceback" not in proc.stderr, f"不得抛原始 traceback：{proc.stderr!r}"

    def test_检查点不可用时API抛CheckpointUnavailable(self, sandbox: Sandbox, tmp_path: Path):
        as_dir = tmp_path / "ckpt_dir_2"
        as_dir.mkdir()
        with pytest.raises(CheckpointUnavailable):
            Coordinator(db_path=sandbox.db, ckpt_path=as_dir)
