# lead-crosscheck.md — 队长 Horizon 独立复算（task-002 Phase 4）

- 日期：2026-10-14｜仓库 `H:\mm-agent`｜解释器 `.venv\Scripts\python.exe`（Python 3.13.14）
- 原则：**不采信任何纸面结论**；关键数字由队长亲自复算，且尽量**绕过应用层**（原生 `sqlite3`）做旁证。

---

## 1. 冻结基线 8 文件 md5（磁盘实测）

| 文件 | task-001 基线 | 现磁盘值 | 判定 |
|---|---|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` | 同 | ✅ 未变 |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` | 同 | ✅ 未变 |
| `README.md` | `2175bb71faab5b4618373e8ca618fc92` | `361afccc9e0d0262604d5c4887ff454a` | ⚠️ **有意变更** |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` | 同 | ✅ 未变 |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` | 同 | ✅ 未变 |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` | 同 | ✅ 未变 |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` | 同 | ✅ 未变 |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` | 同 | ✅ 未变 |

**7/8 一字未变**；`README.md` 为 task-002 DoD#6 的有意文档更新（新增人机回环小节、Python 版本 3.13.12→3.13.14、章节顺延）。
`README.md` 未出现在 `git status` 中 → 工作区版本 == HEAD（已提交）。

## 2. 全量测试（队长复跑）

```
$ .venv\Scripts\python.exe -m pytest tests -q -rxX -p no:cacheprovider --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp_lead"
253 passed, 1 xfailed in 195.12s (0:03:15)
PYTEST_EXIT=0
```

口径自洽（collect-only 逐文件复算）：
- task-001 基线（`--ignore` 两个新文件）→ **190 collected** = 189 passed + 1 xfailed
- `tests/test_nfr5_recovery.py` → **32**
- `tests/test_hitl_decision_flow.py` → **32**
- 合计 190+32+32 = 254 collected → **253 passed / 1 xfailed / 0 failed**

> **重要澄清**：直接 `pytest tests -q > file` 会得到 `EXIT=1`，但**并非测试失败**——进度条全是 `.`/`x`（无 `F`/`E`），
> 是 pytest 会话结束清理系统临时目录（747 个文件）时触发了沙箱的**批量删除确认守卫**，导致退出码被污染、汇总行丢失。
> 改用**仓库内 `--basetemp`** 后 `EXIT=0` 且汇总行正常。已固化为复现注意项。
> 唯一 xfail = task-001 遗留 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`。

## 3. git 跟踪与离线守卫

- `git check-ignore -v data/checkpoints.db` → `.gitignore:20:data/*.db`（命中）；`git ls-files data/` → **空** ⇒ 检查点库未被跟踪 ✅
- `netguard-check` → `[netguard-check] 通过：未发现 requests/socket/urllib/ftplib 直连`，**EXIT=0** ✅
- 依赖：`langgraph==1.2.15`、`langgraph-checkpoint-sqlite==3.1.2`（`pip show` 实测已装，`requirements.txt` 与之锁定一致）

## 4. 真实「挂起 → os._exit(0) 硬杀 → 重启 → 恢复 DONE」（探针 `probe/lead_verify_core.py` / `.txt`）

```
STEP 1  硬杀子进程 rc==0（os._exit(0)）        # 不 close、不清理 WAL
STEP 2  原生 sqlite3 直读 ckpt：tables=['checkpoints','writes'] rows=3 thread_id=['crash-1']   # 绕过应用层
STEP 3  新进程仅凭同一 ckpt 续跑：
        STATUS INTAKE True caliber_conflict
        R1 SOLVE True sanity_blocking
        R2 ASSEMBLE True pre_export_compliance
        R3 DONE False True
        LOG [... 'gate:pre_export_compliance=approve', 'stage:export']
STEP 4  非法裁定：None / 空 dict / 投错 gate → 均 InvalidGateDecision 且 isNameError=False（非 UnboundLocalError）
STEP 5  对照（绕过 _validate_decision 直连 langgraph）：Command(resume=None) → UnboundLocalError  ⇒ 守卫确有必要
STEP 6  hitl.gate_paused 每 (run_id,gate) 恰好 1 条；crash-1 恰 1 条 run_finished
==> LEAD-CORE RESULT: PASS
```

## 5. 并发对抗（探针 `probe/lead_concurrency.py`，真实双进程）

```
4 轮：跨台命中=0（期望 0）；裸 traceback(rc=1)=0（期望 0）
各轮：两进程同时投 caliber_conflict → 合规卡点【未】被口径形状裁定跨过（crossed_compliance=False）
```

### 5.1 残留观察（低危，非本轮 DoD）
并发**双进程同时恢复同一 run** 时，`pre_export_compliance` 的 `hitl.gate_paused` 会被写 **2 条**（4 轮中 3 轮命中）：
```
r0.db [('race-0','caliber_conflict',1), ('race-0','pre_export_compliance',2)]
r1.db [('race-1','caliber_conflict',1), ('race-1','pre_export_compliance',2)]
r2.db [('race-2','caliber_conflict',1), ('race-2','pre_export_compliance',1)]
r3.db [('race-3','caliber_conflict',1), ('race-3','pre_export_compliance',2)]
```
- 性质：**顺序单次恢复**下「每卡点恰好 1 条」契约成立（§4 STEP 6 已证）；并发重复投递属超设计场景，第二次 invoke 又落回合规卡点致 driver 再写一条。
- 影响：仅留痕重复（audit 冗余），**未**导致卡点被跨过、无数据损坏；本项目单机单人，触发需两进程毫秒级竞争。
- 建议：后续可把 `hitl.gate_paused` 去重键设为 `(run_id, gate)` 或加「仅当该 gate 首次挂起才写」判别。列入 KNOWN_ISSUES。

## 6. 结论

**PASS**。task-002 的 NFR-5 跨进程恢复、三卡点契约、非法裁定拦截（无 `UnboundLocalError`）、留痕单条、冻结基线 7/8 不变、检查点不入库、零外网守卫——**全部由队长独立复算通过**；仅存 1 条并发留痕重复的低危观察项（§5.1）。
