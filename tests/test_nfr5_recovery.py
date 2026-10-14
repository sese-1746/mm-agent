"""NFR-5 跨进程恢复 + FR-5/HITL 三卡点裁定契约测试（task-002）。

被测模块：``mmagent/agents/coordinator.py``、``mmagent/cli.py``（run 子命令）

覆盖需求：
- **NFR-5**（可恢复性）：人工卡点暂停后，**换一个新进程**仍能凭 SQLite 检查点续跑到 ``DONE``；
  含异常退出（``os._exit`` 硬崩溃，不 close、不清理 WAL）后的恢复；含 `get_state_history` 时间旅行。
- **FR-5 / HITL 卡点契约（冻结）**：三卡点（``caliber_conflict`` / ``sanity_blocking`` /
  ``pre_export_compliance``）的合法裁定取值域、``log`` 推进顺序、``paused``/``done`` 不变式。
- **非法输入拦截**：``resume()`` 收到 None / 空 dict / 非 dict / 缺键 / 越界值 / 投错 gate
  一律抛 ``InvalidGateDecision``，且**不得**触发 langgraph 1.2.15 的 ``UnboundLocalError``，
  状态不得被污染；CLI 层同一场景 ``returncode == 2``。
- **事件留痕不重复**：resume 会重跑节点，故同一 ``(run_id, gate)`` 的 ``hitl.gate_paused`` 必须恰好 1 条。
- **台账库未初始化**：CLI ``run start`` / ``run resume`` 退出码 3 + 明确提示，绝不静默 exit 0。
- **升级路径回归**：``HumanGateRequired``（同阶段连续失败达阈值转人工）与三卡点路径互不混淆。
- **并发边界**：两个进程同时恢复同一挂起点时的竞态（xfail，见 ISSUES-NFR5-01）。

设计约束（见 brief-test-writer.md）：
- 只读被测实现，不修改 ``mmagent/``；
- 跨进程用例一律用 ``tmp_path`` 隔离检查点库与台账库，不碰 ``data/`` 与 ``artifacts/``；
- 子进程用 ``sys.executable`` 绝对路径 + 超时保护。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from mmagent.agents.coordinator import (
    GATE_CALIBER,
    GATE_COMPLIANCE,
    GATE_SANITY,
    MAX_CONSECUTIVE_FAILS,
    Coordinator,
    CoordinatorState,
    HumanGateRequired,
    InvalidGateDecision,
    Stage,
)
from mmagent.store.db import init_db

REPO_ROOT = Path(__file__).resolve().parents[1]

# 单个子进程超时（langgraph 冷启动 + SQLite 写盘，实测 < 10s；留足余量防挂死）
PROC_TIMEOUT = 180

GATE_LEGAL_DECISION = {
    GATE_CALIBER: {"resolution": "use_mapping", "note": "按建议口径合并"},
    GATE_SANITY: {"decision": "accept", "note": "已人工复核阻断项"},
    GATE_COMPLIANCE: {"decision": "approve", "note": "合规声明已确认"},
}

FULL_LOG_WITH_THREE_GATES = [
    "stage:intake",
    "gate:caliber_conflict=use_mapping",
    "stage:profile",
    "stage:plan",
    "stage:solve",
    "gate:sanity_blocking=accept",
    "stage:render",
    "stage:assemble",
    "gate:pre_export_compliance=approve",
    "stage:export",
]


class Sandbox(NamedTuple):
    db: Path          # 事件台账库（已按 schema.sql 初始化）
    ckpt: Path        # langgraph SqliteSaver 检查点库


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    """临时沙箱：台账库**显式初始化**（不依赖全局状态），检查点库独立。"""
    db = tmp_path / "mm.db"
    init_db(db)
    return Sandbox(db=db, ckpt=tmp_path / "ckpt.db")


# ------------------------------------------------------------------ 子进程基础设施

_CLI_DRIVER_TMPL = '''\
"""子进程入口：打印真实 pid 后调用真实 CLI main()。"""
import os, sys

sys.path.insert(0, __REPO__)
print("__PID__=%d" % os.getpid(), flush=True)
from mmagent.cli import main

raise SystemExit(main(sys.argv[1:]))
'''

_CRASH_DRIVER_TMPL = '''\
"""子进程入口：真实 Coordinator.start 挂起后 **os._exit(0) 硬崩溃**（不 close、不清理）。"""
import json, os, sys

sys.path.insert(0, __REPO__)
from mmagent.agents.coordinator import Coordinator

db_path, ckpt_path, run_id = sys.argv[1], sys.argv[2], sys.argv[3]
coord = Coordinator(db_path=db_path, ckpt_path=ckpt_path)
status = coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
print("__PID__=%d" % os.getpid(), flush=True)
print("__START__=" + json.dumps(status.to_dict(), ensure_ascii=False), flush=True)
os._exit(0)  # 硬崩溃：不 close、不清理 WAL
'''


def _child_env() -> dict:
    """子进程环境：显式把仓库根塞进 PYTHONPATH，避免 PATH/cwd 依赖。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _write_driver(tmp_path: Path, name: str, template: str) -> Path:
    path = tmp_path / name
    path.write_text(template.replace("__REPO__", repr(str(REPO_ROOT))), encoding="utf-8")
    return path


def _run_child(script: Path, *args: str, timeout: int = PROC_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _stdout_pid(proc: subprocess.CompletedProcess) -> int:
    for line in proc.stdout.splitlines():
        if line.startswith("__PID__="):
            return int(line.split("=", 1)[1])
    raise AssertionError(f"子进程未输出 __PID__，stdout={proc.stdout!r} stderr={proc.stderr!r}")


def _stdout_json(proc: subprocess.CompletedProcess, prefix: str = "") -> dict:
    """取子进程 stdout 里最后一行 ``prefix`` 开头的 JSON（CLI 输出 / 驱动脚本输出）。"""
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith(prefix) and line[len(prefix):].startswith("{"):
            return json.loads(line[len(prefix):])
    raise AssertionError(f"子进程未输出 {prefix!r} JSON，stdout={proc.stdout!r}")


def _cli_run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return _run_child(_write_driver(tmp_path, "drv_cli.py", _CLI_DRIVER_TMPL), *args)


def _query_events(db: Path, event_type: str) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT json_extract(payload, '$.run_id') AS run_id,"
            "       json_extract(payload, '$.gate')   AS gate,"
            "       json_extract(payload, '$.decision') AS decision,"
            "       payload"
            " FROM events WHERE event_type = ?", (event_type,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _count_events(db: Path, event_type: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM events WHERE event_type = ?",
                            (event_type,)).fetchone()[0]
    finally:
        conn.close()


# ================================================================== A. 跨进程恢复（NFR-5）

class TestCrossProcessRecovery:
    """NFR-5：暂停态必须落在磁盘上，任何新进程都能续跑。"""

    def test_五个独立进程依次投递三卡点最终到达DONE(self, tmp_path: Path):
        """每个步骤都是**全新 python 进程**，进程间无内存传递。

        进程 1 start（挂起）→ 进程 2 status（只读回挂起态）→ 进程 3/4/5 依次投三卡点
        → 进程 6 status 断言 DONE。
        """
        db = tmp_path / "mm.db"
        init_db(db)
        ckpt = tmp_path / "ckpt.db"
        run_id = "xp-full-1"
        base = ("--run-id", run_id, "--ckpt", str(ckpt), "--db", str(db))

        pids: list[int] = []
        evidence: list[str] = []

        # --- 进程 1：start，命中卡点 1 挂起
        p1 = _cli_run(tmp_path, "run", "start", *base, "--conflicts", "--sanity-blocking")
        assert p1.returncode == 0, f"start 应 exit 0（挂起属正常态），stderr={p1.stderr}"
        s1 = _stdout_json(p1)
        pids.append(_stdout_pid(p1))
        evidence.append(f"P1 start   pid={pids[-1]} rc={p1.returncode} {json.dumps(s1, ensure_ascii=False)}")
        assert (s1["paused"], s1["gate"], s1["stage"]) == (True, GATE_CALIBER, "INTAKE")
        assert s1["done"] is False and s1["question"]

        # --- 进程 2：status，**只靠 SQLite** 读回同一挂起态（证明无内存传递）
        p2 = _cli_run(tmp_path, "run", "status", *base)
        assert p2.returncode == 0, p2.stderr
        s2 = _stdout_json(p2)
        pids.append(_stdout_pid(p2))
        evidence.append(f"P2 status  pid={pids[-1]} rc={p2.returncode} paused={s2['paused']} gate={s2['gate']}")
        assert (s2["paused"], s2["gate"]) == (True, GATE_CALIBER), "新进程必须从检查点库读回挂起态"

        # --- 进程 3/4/5：依次投三卡点
        steps = [
            ("P3 resume", (GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER], GATE_SANITY)),
            ("P4 resume", (GATE_SANITY, GATE_LEGAL_DECISION[GATE_SANITY], GATE_COMPLIANCE)),
            ("P5 resume", (GATE_COMPLIANCE, GATE_LEGAL_DECISION[GATE_COMPLIANCE], None)),
        ]
        for label, (gate, decision, next_gate) in steps:
            proc = _cli_run(tmp_path, "run", "resume", *base,
                            "--gate", gate, "--decision", json.dumps(decision, ensure_ascii=False))
            assert proc.returncode == 0, f"{label} 失败：stderr={proc.stderr}"
            st = _stdout_json(proc)
            pids.append(_stdout_pid(proc))
            evidence.append(f"{label} pid={pids[-1]} rc={proc.returncode} "
                            f"paused={st['paused']} gate={st['gate']} stage={st['stage']}")
            if next_gate is None:
                assert st["done"] is True and st["paused"] is False and st["gate"] is None
                assert st["stage"] == "DONE"
            else:
                assert (st["paused"], st["gate"]) == (True, next_gate)

        # --- 进程 6：status 断言终态
        p6 = _cli_run(tmp_path, "run", "status", *base)
        assert p6.returncode == 0, p6.stderr
        s6 = _stdout_json(p6)
        pids.append(_stdout_pid(p6))
        evidence.append(f"P6 status  pid={pids[-1]} rc={p6.returncode} done={s6['done']} stage={s6['stage']}")
        assert (s6["done"], s6["stage"], s6["paused"]) == (True, "DONE", False)
        assert s6["log"] == FULL_LOG_WITH_THREE_GATES, "跨进程续跑的 log 顺序必须与单进程一致"

        # --- 跨进程证据：每个 pid 都不是 pytest 进程，且至少出现 2 个不同 pid
        for line in evidence:
            print("[XP] " + line)
        print(f"[XP] pytest pid={os.getpid()} child pids={pids}")
        assert all(pid != os.getpid() for pid in pids), "子进程必须与 pytest 是不同进程"
        assert len(set(pids)) >= 2, f"至少应观察到 2 个不同 pid，实际 {pids}"
        assert ckpt.exists(), "检查点库必须落盘"

    def test_硬崩溃os_exit后新进程仍能恢复至DONE(self, tmp_path: Path):
        """NFR-5 的硬核形式：进程 A 挂起后 ``os._exit(0)``（不 close、不清理 WAL），
        进程 B/C/D 必须仍能凭同一检查点库续跑到 DONE。
        """
        db = tmp_path / "mm.db"
        init_db(db)
        ckpt = tmp_path / "ckpt.db"
        run_id = "xp-crash-1"
        driver = _write_driver(tmp_path, "drv_crash.py", _CRASH_DRIVER_TMPL)

        # --- 进程 A：start 后硬崩溃
        pa = _run_child(driver, str(db), str(ckpt), run_id)
        pid_a = _stdout_pid(pa)
        assert pa.returncode == 0, f"os._exit(0) 应返回码 0，stderr={pa.stderr}"
        start_state = _stdout_json(pa, "__START__=")
        print(f"[XP-CRASH] A pid={pid_a} rc={pa.returncode} "
              f"paused={start_state['paused']} gate={start_state['gate']}")
        assert (start_state["paused"], start_state["gate"]) == (True, GATE_CALIBER)
        assert pid_a != os.getpid(), "崩溃进程必须与 pytest 是不同进程"
        assert ckpt.exists(), "硬崩溃前检查点必须已落盘"

        # --- 进程 B/C/D：新进程续跑（每个都是独立 subprocess）
        base = ("--run-id", run_id, "--ckpt", str(ckpt), "--db", str(db))
        pids_after: list[int] = []
        for gate in (GATE_CALIBER, GATE_SANITY, GATE_COMPLIANCE):
            proc = _cli_run(tmp_path, "run", "resume", *base,
                            "--gate", gate,
                            "--decision", json.dumps(GATE_LEGAL_DECISION[gate], ensure_ascii=False))
            assert proc.returncode == 0, f"崩溃后 resume {gate} 失败：stderr={proc.stderr}"
            pid = _stdout_pid(proc)
            pids_after.append(pid)
            st = _stdout_json(proc)
            print(f"[XP-CRASH] resume {gate}: pid={pid} rc={proc.returncode} "
                  f"stage={st['stage']} paused={st['paused']} gate={st['gate']}")

        final = _cli_run(tmp_path, "run", "status", *base)
        assert final.returncode == 0, final.stderr
        status = _stdout_json(final)
        print(f"[XP-CRASH] final pid={_stdout_pid(final)} done={status['done']} stage={status['stage']}")
        assert (status["done"], status["stage"], status["paused"]) == (True, "DONE", False)
        assert status["log"] == FULL_LOG_WITH_THREE_GATES, "无重复阶段（resume 重跑节点不得污染 log）"
        assert all(pid != os.getpid() for pid in pids_after)

    def test_时间旅行_get_state_history可回溯到挂起那一刻(self, sandbox: Sandbox):
        """`get_state_history` 快照数 ≥ 已推进节点数，且能取到挂起那一刻的快照。"""
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "tt-1"
            st = coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
            assert (st.paused, st.gate) == (True, GATE_CALIBER)

            cfg = {"configurable": {"thread_id": run_id}}
            history = list(coord._graph.get_state_history(cfg))
            # start 已执行 intake + gate_caliber 两个节点
            nodes_exercised = 2
            assert len(history) >= nodes_exercised, (
                f"快照数 {len(history)} 少于已推进节点数 {nodes_exercised}")

            paused_snaps = [
                snap for snap in history
                if any(iv for task in (snap.tasks or ()) for iv in (task.interrupts or ()))
            ]
            assert paused_snaps, "应能取到挂起那一刻的快照（含 interrupt payload）"
            payload = paused_snaps[0].tasks[0].interrupts[0].value
            assert payload["gate"] == GATE_CALIBER
            assert payload["stage"] == "INTAKE"

            # 继续推进后快照数单调增长
            coord.resume(run_id, GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER])
            history2 = list(coord._graph.get_state_history(cfg))
            assert len(history2) > len(history), "恢复推进后应产生新快照"
        finally:
            coord.close()


# ------------------------------------------------------- A+. 并发恢复竞态（NFR-5 边界）

class TestConcurrentResumeRace:
    """并发边界：两个进程同时恢复**同一挂起点**时的 TOCTOU 竞态（见 ISSUES-NFR5-01）。

    真机复现（2 个并发 CLI 进程投同一卡点，8 轮）：6 轮「合规卡点被非法值跨过」、
    2 轮受控拒绝（exit 2）。本用例用 monkeypatch 把 `resume()` 内
    「读状态 → invoke」之间的窗口**确定性地**撑开，把该竞态固化成回归用例，
    避免依赖线程/进程调度导致 flaky。
    """

    # 修复轮 2（Max）：原 xfail 标记已按队长授权摘除。
    # 原 xfail 依据：resume() 的「读状态 → invoke」之间无锁保护；两个进程都通过状态检查时，
    # 后到的 Command(resume=...) 会被 langgraph 应用到**下一个**卡点的 interrupt 上，导致
    # pre_export_compliance 被一个对其非法的裁定值（口径形状 dict）静默跨过（fail-open）。
    # 修复：invoke 时用 config 里的 checkpoint_id 钉住已校验快照（见 coordinator.resume）。
    def test_并发陈旧裁定不得串台到下一个卡点(self, sandbox: Sandbox, monkeypatch):
        """陈旧/重复的裁定必须被拒绝，绝不能被下一个卡点消费。"""
        run_id = "race-1"
        coord_a = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        coord_b = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            started = coord_a.start(run_id, {"caliber_conflicts": True})
            assert (started.paused, started.gate) == (True, GATE_CALIBER)

            original_read = coord_b._read_status
            fired = {"done": False}

            def racy_read(rid):
                """模拟交错：B 读到（陈旧的）挂起快照后、invoke 前，A 抢先推进到下一个卡点。"""
                status = original_read(rid)
                if not fired["done"]:
                    fired["done"] = True
                    coord_a.resume(run_id, GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER])
                return status

            monkeypatch.setattr(coord_b, "_read_status", racy_read)
            try:
                coord_b.resume(run_id, GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER])
            except InvalidGateDecision:
                pass  # 期望路径：陈旧裁定被拒

            decisions = dict(coord_b._graph.get_state(
                {"configurable": {"thread_id": run_id}}).values.get("decisions", {}))
            # 修复轮 2（Max）：原断言 `assert (decisions.get(GATE_COMPLIANCE) or {}).get("decision")
            # in {"approve","reject"}` 对**正确**行为不可满足——竞态被修好后，合规卡点仍是"待审"
            # （decisions 里根本没有 pre_export_compliance），故 (or {}).get(...) 恒为 None。
            # 该断言本意是"合规卡点不得被**非合法**裁定跨过"：未被跨过 或 被合法 approve/reject 跨过。
            # 修复前会串台成口径形状（decision 缺失）→ 断言失败；修复后合规卡点保持待审 → 通过。
            crossed = decisions.get(GATE_COMPLIANCE)
            assert crossed is None or (crossed or {}).get("decision") in {"approve", "reject"}, (
                f"合规卡点不得被非合法裁定跨过，实际 decisions={decisions}")
        finally:
            coord_a.close()
            coord_b.close()


# ================================================================== B. 三卡点裁定契约
class TestGateContract:
    """三卡点契约（冻结）：触发条件、合法取值域、log 顺序、状态不变式。"""

    @pytest.mark.parametrize(
        "gate,context",
        [
            (GATE_CALIBER, {"caliber_conflicts": True}),
            (GATE_SANITY, {"sanity_blocking": True}),
            (GATE_COMPLIANCE, {}),  # compliance_confirmed 默认 False → 任何 run 都停卡点 3
        ],
        ids=["卡点1口径冲突", "卡点2合理性阻断", "卡点3导出前合规"],
    )
    def test_三卡点各自用合法裁定可恢复(self, sandbox: Sandbox, gate: str, context: dict):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = f"gate-{gate}"
            paused = coord.start(run_id, context)
            assert (paused.paused, paused.gate) == (True, gate), "触发条件必须命中该卡点"
            assert paused.question, "挂起时必须给出 question"

            resumed = coord.resume(run_id, gate, GATE_LEGAL_DECISION[gate])
            assert not (resumed.paused and resumed.gate == gate), "合法裁定后必须离开该卡点"
            assert f"gate:{gate}={GATE_LEGAL_DECISION[gate].get('resolution') or GATE_LEGAL_DECISION[gate].get('decision')}" in resumed.log
        finally:
            coord.close()

    def test_三卡点全流程log顺序严格正确(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "seq-1"
            st = coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
            assert st.log == ["stage:intake"]
            st = coord.resume(run_id, GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER])
            assert st.log == ["stage:intake", "gate:caliber_conflict=use_mapping",
                              "stage:profile", "stage:plan", "stage:solve"]
            assert (st.paused, st.gate, st.stage) == (True, GATE_SANITY, "SOLVE")
            st = coord.resume(run_id, GATE_SANITY, GATE_LEGAL_DECISION[GATE_SANITY])
            assert st.log == ["stage:intake", "gate:caliber_conflict=use_mapping",
                              "stage:profile", "stage:plan", "stage:solve",
                              "gate:sanity_blocking=accept", "stage:render", "stage:assemble"]
            assert (st.paused, st.gate, st.stage) == (True, GATE_COMPLIANCE, "ASSEMBLE")
            st = coord.resume(run_id, GATE_COMPLIANCE, GATE_LEGAL_DECISION[GATE_COMPLIANCE])
            assert st.log == FULL_LOG_WITH_THREE_GATES
        finally:
            coord.close()

    def test_paused与done状态不变式(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "inv-1"
            st = coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
            while st.paused:  # 遍历每个挂起点，逐一校验不变式
                assert st.gate, "paused=True 时 gate 必须非空"
                assert st.question, "paused=True 时 question 必须非空"
                assert st.done is False, "paused 与 done 不可同时为真"
                st = coord.resume(run_id, st.gate, GATE_LEGAL_DECISION[st.gate])
            assert st.done is True
            assert st.paused is False, "done=True 时 paused 必须为 False"
            assert st.gate is None, "done=True 时 gate 必须为 None"
            assert st.stage == "DONE"
        finally:
            coord.close()

    def test_context全确认时无卡点直达DONE(self, sandbox: Sandbox):
        """边界：context 全部确认 → 三卡点全部 skip，直接 DONE。"""
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            st = coord.start("no-gate-1", {"compliance_confirmed": True})
            assert (st.done, st.paused, st.stage) == (True, False, "DONE")
            assert st.log == ["stage:intake", "gate:caliber_conflict=skip", "stage:profile",
                              "stage:plan", "stage:solve", "gate:sanity_blocking=skip",
                              "stage:render", "stage:assemble",
                              "gate:pre_export_compliance=skip", "stage:export"]
        finally:
            coord.close()


# ================================================================== C. 非法输入拦截

class TestInvalidDecisionRejected:
    """非法裁定必须在调用 langgraph 之前被 ``InvalidGateDecision`` 拦下。"""

    @pytest.mark.parametrize(
        "decision,reason_fragment",
        [
            pytest.param(None, "不得为 None", id="None"),
            pytest.param({}, "不得为空", id="空dict"),
            pytest.param("use_mapping", "必须是 dict", id="非dict-str"),
            pytest.param([1, 2], "必须是 dict", id="非dict-list"),
            pytest.param({"note": "n"}, "缺必需键", id="缺resolution键"),
            pytest.param({"resolution": "bogus", "note": "n"}, "越界", id="resolution越界"),
            pytest.param({"resolution": "use_mapping"}, "缺必需键 'note'", id="缺note键"),
            pytest.param({"resolution": "use_mapping", "note": 123}, "必须是 str", id="note非str"),
        ],
    )
    def test_非法裁定被拦截且状态不被污染(self, sandbox: Sandbox, decision, reason_fragment: str):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "bad-1"
            before = coord.start(run_id, {"caliber_conflicts": True})
            assert (before.paused, before.gate) == (True, GATE_CALIBER)

            with pytest.raises(InvalidGateDecision) as excinfo:
                coord.resume(run_id, GATE_CALIBER, decision)

            # langgraph 1.2.15 对 resume=None 会抛 UnboundLocalError（NameError 子类）——必须不出现
            assert not isinstance(excinfo.value, NameError), (
                "不得把非法值透传给 langgraph（会抛 UnboundLocalError）")
            assert reason_fragment in str(excinfo.value)

            after = coord.status(run_id)
            assert (after.paused, after.gate, after.stage) == (True, GATE_CALIBER, "INTAKE")
            assert after.log == before.log, "非法裁定不得推进图"
            assert _count_events(sandbox.db, "hitl.gate_resumed") == 0, "被拦下的裁定不得留痕为已恢复"
        finally:
            coord.close()

    def test_投错gate被拦且状态不被污染(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "wrong-gate-1"
            before = coord.start(run_id, {"sanity_blocking": True})
            assert (before.paused, before.gate) == (True, GATE_SANITY)

            with pytest.raises(InvalidGateDecision, match="投错卡点"):
                coord.resume(run_id, GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER])

            after = coord.status(run_id)
            assert (after.paused, after.gate, after.stage) == (True, GATE_SANITY, "SOLVE")
            assert after.log == before.log

            # 仍可用正确 gate 恢复（证明前一次失败没污染检查点）
            resumed = coord.resume(run_id, GATE_SANITY, GATE_LEGAL_DECISION[GATE_SANITY])
            assert (resumed.paused, resumed.gate) == (True, GATE_COMPLIANCE)
        finally:
            coord.close()

    def test_未知卡点名被拦(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            coord.start("unknown-gate-1", {"caliber_conflicts": True})
            with pytest.raises(InvalidGateDecision, match="未知卡点名"):
                coord.resume("unknown-gate-1", "no_such_gate", {"x": "y", "note": "n"})
        finally:
            coord.close()

    def test_未挂起线程投裁定被拦(self, sandbox: Sandbox):
        """边界：跑到 DONE 之后再投裁定 → 明确报「当前未挂起」。"""
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "done-1"
            coord.start(run_id, {"compliance_confirmed": True})
            with pytest.raises(InvalidGateDecision, match="当前未挂起"):
                coord.resume(run_id, GATE_COMPLIANCE, GATE_LEGAL_DECISION[GATE_COMPLIANCE])
        finally:
            coord.close()

    def test_start收到非dict_context被拦(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            with pytest.raises(InvalidGateDecision, match="context 必须是 dict"):
                coord.start("bad-ctx-1", ["caliber_conflicts"])
        finally:
            coord.close()

    def test_CLI非法裁定返回码2(self, tmp_path: Path):
        """CLI 层：非法/投错裁定 → returncode 2 + stderr 明确原因，且不污染检查点。"""
        db = tmp_path / "mm.db"
        init_db(db)
        ckpt = tmp_path / "ckpt.db"
        run_id = "cli-bad-1"
        base = ("--run-id", run_id, "--ckpt", str(ckpt), "--db", str(db))

        started = _cli_run(tmp_path, "run", "start", *base, "--conflicts")
        assert started.returncode == 0, started.stderr
        assert _stdout_json(started)["gate"] == GATE_CALIBER

        cases = [
            ("null", GATE_CALIBER, "不得为 None"),
            ("{}", GATE_CALIBER, "不得为空"),
            ('{"resolution":"bogus","note":"n"}', GATE_CALIBER, "越界"),
            ('{"note":"n"}', GATE_CALIBER, "缺必需键"),
            # 投错 gate：裁定本身对该 gate 合法（否则先被「缺 decision 键」拦下），
            # 校验顺序是「先 decision 合法性，再 gate 一致性」（见 resume() docstring）。
            ('{"decision":"approve","note":"n"}', GATE_COMPLIANCE, "投错卡点"),
            ("这不是JSON", GATE_CALIBER, "不是合法 JSON"),
        ]
        for decision_arg, gate, fragment in cases:
            proc = _cli_run(tmp_path, "run", "resume", *base,
                            "--gate", gate, "--decision", decision_arg)
            assert proc.returncode == 2, (
                f"decision={decision_arg!r} gate={gate} 应 exit 2，实际 {proc.returncode}；"
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            assert fragment in proc.stderr, f"stderr 应含 {fragment!r}，实际 {proc.stderr!r}"

        # 六次非法调用后，合法裁定仍能恢复 → 检查点未被污染
        ok = _cli_run(tmp_path, "run", "resume", *base, "--gate", GATE_CALIBER,
                      "--decision", json.dumps(GATE_LEGAL_DECISION[GATE_CALIBER], ensure_ascii=False))
        assert ok.returncode == 0, ok.stderr
        assert _stdout_json(ok)["gate"] == GATE_COMPLIANCE


# ================================================================== D. 事件留痕不重复

class TestEventLedger:
    """resume 会重跑卡点节点 → 留痕必须在 driver 写，不得膨胀。"""

    def test_同一卡点hitl_gate_paused恰好一条(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "evt-1"
            st = coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
            for _ in range(3):
                assert st.paused
                st = coord.resume(run_id, st.gate, GATE_LEGAL_DECISION[st.gate])
            assert st.done
            # 额外的读操作与失败的 resume 都不该产生新留痕
            coord.status(run_id)
            with pytest.raises(InvalidGateDecision):
                coord.resume(run_id, GATE_COMPLIANCE, GATE_LEGAL_DECISION[GATE_COMPLIANCE])

            rows = [r for r in _query_events(sandbox.db, "hitl.gate_paused") if r["run_id"] == run_id]
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["gate"]] = counts.get(row["gate"], 0) + 1
            assert counts == {GATE_CALIBER: 1, GATE_SANITY: 1, GATE_COMPLIANCE: 1}, (
                f"同一卡点 hitl.gate_paused 必须恰好 1 条，实际 {counts}")
        finally:
            coord.close()

    def test_事件次数与投递次数一致(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            run_id = "evt-2"
            st = coord.start(run_id, {"caliber_conflicts": True, "sanity_blocking": True})
            delivered = 0
            while st.paused:
                st = coord.resume(run_id, st.gate, GATE_LEGAL_DECISION[st.gate])
                delivered += 1

            assert _count_events(sandbox.db, "hitl.run_started") == 1
            assert _count_events(sandbox.db, "hitl.gate_resumed") == delivered == 3
            assert _count_events(sandbox.db, "hitl.gate_paused") == 3
            assert _count_events(sandbox.db, "hitl.run_finished") == 1
        finally:
            coord.close()

    def test_跨进程续跑也不重复留痕(self, tmp_path: Path):
        """跨进程场景下同样只留 1 条 gate_paused（每个 resume 是独立进程）。"""
        db = tmp_path / "mm.db"
        init_db(db)
        ckpt = tmp_path / "ckpt.db"
        run_id = "evt-xp-1"
        base = ("--run-id", run_id, "--ckpt", str(ckpt), "--db", str(db))

        assert _cli_run(tmp_path, "run", "start", *base,
                        "--conflicts", "--sanity-blocking").returncode == 0
        for gate in (GATE_CALIBER, GATE_SANITY, GATE_COMPLIANCE):
            proc = _cli_run(tmp_path, "run", "resume", *base, "--gate", gate,
                            "--decision", json.dumps(GATE_LEGAL_DECISION[gate], ensure_ascii=False))
            assert proc.returncode == 0, proc.stderr

        rows = [r for r in _query_events(db, "hitl.gate_paused") if r["run_id"] == run_id]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["gate"]] = counts.get(row["gate"], 0) + 1
        assert counts == {GATE_CALIBER: 1, GATE_SANITY: 1, GATE_COMPLIANCE: 1}, counts
        assert _count_events(db, "hitl.gate_resumed") == 3


# ================================================================== E. 台账库未初始化

class TestLedgerNotInitialized:
    """台账库未初始化必须显式失败（exit 3），绝不静默 exit 0 且零事件。"""

    def test_台账库不存在时CLI_start退出码3并提示db_init(self, tmp_path: Path):
        db = tmp_path / "nope" / "mm.db"          # 父目录都不存在
        ckpt = tmp_path / "ckpt.db"
        proc = _cli_run(tmp_path, "run", "start", "--run-id", "led-1",
                        "--ckpt", str(ckpt), "--db", str(db))
        assert proc.returncode == 3, (
            f"台账库未初始化应 exit 3，实际 {proc.returncode}；stdout={proc.stdout!r}")
        assert "db init" in proc.stderr, f"stderr 必须给出修复指引，实际 {proc.stderr!r}"
        assert "--db" in proc.stderr
        assert not db.exists(), "不得静默自动建库（那正是「安静地丢数据」）"
        assert not any(line.strip().startswith("{") for line in proc.stdout.splitlines()), \
            f"不得输出「成功」的 JSON，实际 stdout={proc.stdout!r}"

    def test_台账库存在但缺events表时CLI_start退出码3(self, tmp_path: Path):
        db = tmp_path / "empty.db"
        sqlite3.connect(str(db)).close()          # 建了个空库，但没有 events 表
        ckpt = tmp_path / "ckpt.db"
        proc = _cli_run(tmp_path, "run", "start", "--run-id", "led-2",
                        "--ckpt", str(ckpt), "--db", str(db))
        assert proc.returncode == 3, f"实际 {proc.returncode}；stderr={proc.stderr!r}"
        assert "db init" in proc.stderr

    def test_台账库未初始化时CLI_resume同样退出码3(self, tmp_path: Path):
        db = tmp_path / "nope2" / "mm.db"
        ckpt = tmp_path / "ckpt.db"
        proc = _cli_run(tmp_path, "run", "resume", "--run-id", "led-3",
                        "--gate", GATE_CALIBER,
                        "--decision", json.dumps(GATE_LEGAL_DECISION[GATE_CALIBER], ensure_ascii=False),
                        "--ckpt", str(ckpt), "--db", str(db))
        assert proc.returncode == 3, f"实际 {proc.returncode}；stderr={proc.stderr!r}"
        assert "db init" in proc.stderr


# ================================================================== 升级路径（同阶段连续失败）

class TestEscalationPath:
    """`HumanGateRequired` 是**独立于三卡点**的升级路径（同阶段连续失败达阈值转人工）。

    它不属于 Graph 卡点（不抛在 resume 路径上），但同属 `coordinator.py` 对外行为，故补测。
    """

    def test_未达阈值只累计不转人工(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            state = CoordinatorState(stage=Stage.SOLVE)
            coord.record_failure(state, "第1次失败")
            assert state.fail_counts[Stage.SOLVE.value] == 1
            assert state.stage is Stage.SOLVE
            assert state.context["last_error"] == "第1次失败"
            assert _count_events(sandbox.db, "coordinator.stage_failed") == 1
        finally:
            coord.close()

    def test_同阶段连续失败达阈值转人工并置HALTED(self, sandbox: Sandbox):
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            state = CoordinatorState(stage=Stage.SOLVE)
            coord.record_failure(state, "第1次失败")          # 未达阈值：不抛
            with pytest.raises(HumanGateRequired, match="consecutive_failures") as excinfo:
                coord.record_failure(state, "第2次失败")      # 达阈值：抛
            assert excinfo.value.gate == "consecutive_failures"
            assert state.stage is Stage.HALTED
            assert state.fail_counts[Stage.SOLVE.value] == MAX_CONSECUTIVE_FAILS
            assert _count_events(sandbox.db, "coordinator.stage_failed") == MAX_CONSECUTIVE_FAILS
        finally:
            coord.close()

    def test_人机回环卡点不抛HumanGateRequired(self, sandbox: Sandbox):
        """三卡点走 interrupt/RunStatus，**不得**抛 HumanGateRequired（两条路径不得混淆）。"""
        coord = Coordinator(db_path=sandbox.db, ckpt_path=sandbox.ckpt)
        try:
            st = coord.start("no-hgre-1", {"caliber_conflicts": True})
            assert st.paused and st.gate == GATE_CALIBER
            st = coord.resume("no-hgre-1", GATE_CALIBER, GATE_LEGAL_DECISION[GATE_CALIBER])
            assert st.paused and st.gate == GATE_COMPLIANCE
        finally:
            coord.close()
