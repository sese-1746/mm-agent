# brief-test-writer.md — 测试工程师 Tess（task-002）

- **role_name**: 测试工程师 Tess
- **mission**: 为 task-002 的 LangGraph 人机回环实现编写 pytest 用例，重点证明 **NFR-5 跨进程恢复**真的成立，并覆盖三卡点裁定契约、非法输入拦截与事件留痕不重复。
- **allowed_edit_scope**: **仅** `H:\mm-agent\tests\`（含 `conftest.py`）。**禁止修改 `mmagent/` 下任何源码**——发现缺陷写进报告，不要改源码。
- **output_path**: `H:\mm-agent\.agent-runs\2026-10-14\task-002\test-report.md`
- **success_criteria**: 新增用例全绿（或对真缺陷标 xfail 并附证据），且 task-001 的 190 例保持「189 passed / 1 xfailed」不退化。

---

## 环境与起点

- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`（**实测 Python 3.13.14**）
- 跑测：`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q`
- 已有依赖：`langgraph==1.2.15`、`langgraph-checkpoint-sqlite==3.1.2`（已装好）
- 起点基线：**189 passed / 1 xfailed**（唯一 xfail 是 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`，task-001 遗留，本轮不动）
- 实现报告（接口与坑清单）：`.agent-runs/2026-10-14/task-002/implementer-report.md`

---

## 被测实现（已交付，接口冻结）

`mmagent/agents/coordinator.py`（487 行）：
```python
DEFAULT_CKPT_PATH = "data/checkpoints.db"

@dataclass
class RunStatus:
    run_id: str; stage: str; paused: bool
    gate: str | None; question: str | None; done: bool; log: list[str]

class InvalidGateDecision(Exception): ...

class Coordinator:
    def __init__(self, db_path=DEFAULT_DB_PATH, ckpt_path=DEFAULT_CKPT_PATH): ...
    def start(self, run_id: str, context: dict) -> RunStatus: ...
    def resume(self, run_id: str, gate: str, decision: dict) -> RunStatus: ...
    def status(self, run_id: str) -> RunStatus: ...
    def close(self) -> None: ...
```
图：`START → intake → gate_caliber → profile → plan → solve → gate_sanity → render → assemble → gate_compliance → export → END`（10 节点线性）。检查点用 `SqliteSaver`，`thread_id = run_id`。

**三个卡点的裁定契约（冻结，请精确断言）**：
| gate | 触发条件 | 合法裁定 |
|---|---|---|
| `caliber_conflict` | `context["caliber_conflicts"]=True` | `{"resolution":"use_mapping"\|"keep_raw"\|"human_override","note":str}` |
| `sanity_blocking` | `context["sanity_blocking"]=True` | `{"decision":"accept"\|"fix_and_rerun"\|"abort","note":str}` |
| `pre_export_compliance` | 因 `compliance_confirmed` 默认 False → **任何 run 都会在此暂停** | `{"decision":"approve"\|"reject","note":str}` |

CLI：`mmagent.cli run start|resume|status`（`--run-id`/`--gate`/`--decision`(JSON)/`--ckpt`/`--db`）。挂起 → **exit 0**；裁定非法 → **exit 2**。
事件表（`events`）：`hitl.run_started` / `hitl.gate_paused` / `hitl.gate_resumed` / `hitl.run_finished` / `coordinator.transition`。

---

## 必写用例（建议文件 `tests/test_nfr5_recovery.py`）

### A. 跨进程恢复（本轮核心，必须真跨进程）
1. **子进程挂起 → 新子进程恢复**：用 `subprocess` 以同一解释器起进程 A（`Coordinator.start`，注入口径冲突，挂起后正常退出），再起进程 B 投卡点 1、进程 C 投卡点 3，最后进程 D 断言 `done=True / stage=DONE`。**断言进程间无内存传递**（每步都是新的 python 进程）。
2. **硬崩溃恢复**：子进程在挂起后用 `os._exit(0)` 硬崩溃（不 close、不清理），再用新子进程恢复 → 必须仍能推到 `DONE`。这是 NFR-5 的落地形式。
3. **时间旅行**：`get_state_history(cfg)` 快照数 ≥ 节点推进次数，且能取到挂起那一刻的快照。

### B. 三卡点裁定契约
4. 三个 gate 用**合法裁定**各走一遍 → 都能恢复；`log` 顺序正确（`stage:intake` → `gate:caliber_conflict=…` → `stage:profile` → …）。
5. `paused=True` 时 `gate`/`question` 非空；`done=True` 时 `paused=False` 且 `gate is None`。

### C. 非法输入必须被拦（**不得出现 langgraph 的 `UnboundLocalError`**）
6. `resume()` 传 `None`、`{}`、非 dict、缺必需键、取值域外 → 全部抛 `InvalidGateDecision`。
7. **投错 gate**（当前挂起 `sanity_blocking` 却投 `caliber_conflict`）→ 抛 `InvalidGateDecision`，且**状态不被污染**（再次 `status()` 仍停在原卡点）。
8. CLI 层：非法裁定 → `returncode == 2`，输出含明确错误信息。

### D. 事件留痕不重复
9. 同一卡点 `hitl.gate_paused` **恰好 1 条**（跨多次 resume 不膨胀）——这是"resume 会重跑节点"那个坑的验收点。用初始化过的临时台账库。
10. `hitl.gate_resumed` 次数 == 实际投递次数；`hitl.run_started` == 1。

### E. 台账库未初始化（**并发修复中，按修复后行为写**）
11. 台账库未初始化时跑 CLI `run start` → **exit 3** + stderr 含明确提示（指向 `db init --db <path>`），**不得静默 exit 0 且零事件**。
    （实现方正并行落这条修复；若你先跑到红，等其落地后重跑，并在报告中记录先后顺序。）

### F. 回归
12. 全量 `pytest tests -q` → 保持 **189 passed / 1 xfailed**，不新增 failed / skipped。

---

## 约束（必须遵守）

- **只写 `tests/`**；`mmagent/` 一字不改。发现源码缺陷 → 报告「ISSUES」节写 `文件:行号` + 现象 + 推测原因，不要改源码。
- **不要弱化断言**去将就错误行为。若某用例确实因源码缺陷无法通过，标 `xfail` 并在报告中附证据（本地真实执行失败的输出），不要 skip。
- **跨进程测试必须用 `tmp_path`** 隔离检查点库与台账库，**不要**碰 `data/mm.db` 或 `data/checkpoints.db`（那是运行态的），也不要污染 `artifacts/`。
- 子进程调用请用 `sys.executable`（即 `.venv` 的解释器）绝对路径，避免 PATH 串味；测试要有超时保护，避免挂死。
- 测试文件头部注释标注对应需求编号（NFR-5 / FR-5 卡点契约）。
- 不要改 `config/*.yaml`、不要改 `.gitignore`。
- 台账相关用例请显式调用 `db init`（或直接读 `store.schema` 建表）而不是依赖全局状态。

---

## 报告要求

`test-report.md` 含：用例清单（文件 + 用例名 + 覆盖需求编号）、`pytest -q` 真实输出摘要（passed / failed / xfailed 计数）、跨进程用例的**真实子进程输出片段**（证明是两个不同 pid）、ISSUES 节（若有，带 file:line）、以及一句明确结论：NFR-5 是否「有测试用例支撑」。

完成后回传**简短状态块**：PASS/FAIL 数、覆盖的需求编号、跨进程用例是否真跨进程执行、ISSUES 要点、报告路径。**不要把报告全文倒回主会话。**
