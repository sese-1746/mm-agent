# lead-crosscheck.md — task-003 队长独立核对（Horizon）

- 时间：2026-10-14 20:33–…（本轮自动化，与另一交互会话**并行**作业）
- 仓库：`H:\mm-agent`（`master`）｜解释器 `.venv\Scripts\python.exe`（Python 3.13.14）
- 角色判定：`task-003/` 已存在（`plan.md` + `baseline-repro.md`），且 `mmagent/**` 关键文件 mtime 很新、
  工作区处于未提交的 M 状态、探针目录 `probe/ws2_*` 正在被写入 → **判定交互会话正在同一批文件上工作**。
  依硬约束「同一任务目录不要让两个执行体并行改同一批文件」，本轮**不重复实现**，
  转为**独立验证与复算**：只读产品代码 + 自行跑真实端到端 + 独立复算数字 + 核对已声称的改进是否真生效。
  **本轮未修改 `mmagent/**` 任何文件。**

---

## 1. git 核实（顺带项）

| 项 | 结果 |
|---|---|
| 本地 `git log --oneline -3` 顶端 | `cc181a1 docs(task-002): 校准 KNOWN_ISSUES#1 的可复现性描述` |
| `git ls-remote origin master` | `cc181a181452ac6a57562fd7008879d8b3610983` |
| 结论 | **本地 HEAD 与 `origin/master` 一致** → task-002 的提交**已成功推送**，本轮**无需 push**（本环境现可解析 gitee.com，与上一轮「无外网 DNS」不同） |

---

## 2. 冻结基线核对（8 项，独立复算 md5）

| 文件 | 基线值（来源） | 本轮实测 md5 | 判定 |
|---|---|---|---|
| `README.md` | `361afccc9e0d0262604d5c4887ff454a`（task-002 §10 新基线） | 同 | ✅ 一致 |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` | 同 | ✅ **一字未动** |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` | 同 | ✅ 未变 |
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` | 同 | ✅ 未变 |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` | **`00f172c113637251ea4bcf3e4269e4cd`** | ⚠️ **漂移（本轮有意修改）** |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` | 同 | ✅ 未变 |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` | 同 | ✅ 未变 |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` | 同 | ✅ 未变 |

**结论：7/8 未变，仅 `guard.py` 漂移。**

- ⚠️ **缺口（队长标记，但不代发）**：`guard.py` 属 task-001 冻结基线（8 项之一），修改后**按约定必须
  重发布基线并通知测试方重跑**（`baseline-repro.md` §3 自己也写明这一点），但截至本轮核对，
  `task-003/` 下**尚无**基线重发布文件。
  **队长不代发**：产品代码此刻仍在被另一执行体改动（`base.py` 于 20:36 仍在变），此时发布的基线会立即
  过期、反而制造混乱。**应由实现方在代码冻结后发布**，并在此处回填生效值。
- 其余 `config/*.yaml`、`.gitignore`：`git status` 未显示改动。
- **硬约束核对通过**：`mmagent/store/schema.sql`、`mmagent/agents/coordinator.py`、`mmagent/cli.py`
  **均无 diff**（task-002 冻结语义未被触碰）。

---

## 3. 已声称改进的独立复算（A 项，绕过应用层）

下列结论由**队长**与**独立验证成员 Vic**（`probe/verify_vic/verify_a_result.json`）**各自独立跑出、逐条一致**：

| 验证项 | 期望 | 实测（队长 / Vic 一致） | 判定 |
|---|---|---|---|
| `import xlrd` / `import openpyxl` | 放行 | **PASS** | ✅ F1 生效 |
| `import os` / `from os import system; system('dir')` / `os.makedirs` | 拦 | **BLOCK**（"os 在禁止名单"） | ✅ F2 保留整块禁令 |
| `import importlib` / `importlib.import_module('subprocess')` | 拦 | **BLOCK** | ✅ 未因补 xlrd 放水 |
| `import subprocess` / `httpx` / `http.client` / `socket` / `shutil` | 拦 | **BLOCK** | ✅ |
| `eval('1')` | 拦 | **BLOCK** | ✅ |
| 干净脚本（pandas+matplotlib / xlrd+pathlib） | 放行 | **PASS** | ✅ |
| `ALLOWED_IMPORTS ∩ BANNED_IMPORTS` | 空集 | **`[]`** | ✅ 无交集 |

- **A3 runner 判据（新）**：`rc=0 且无 manifest` → `ok=False`（`manifest_missing` 给出说明）；
  `rc=0 且 manifest 齐全` → `ok=True`；`rc=0 但 manifest 缺件` → `ok=False`。
  → 该改动是**加严**（旧实现「manifest 不存在 ⇒ `manifest_missing=[]` ⇒ `ok=True`」是零产物假成功）。
  **它使本轮成功率与历史数字（旧判据）不可直接横比**：新判据更严，故本轮数字一经修正反而更保守。
- **A4 `stage_inputs`**：中文名 `附件1 各门店客户满意度综合得分-A题.xls` → `data/att1.xls`（55296 B 一致）；
  已是 ASCII 名者保持原名 → ✅
- **A5 `make_regen_fn`**：注入 stub runner 后捕获 payload，`prev_code` **逐字等于**传入的上一版完整源码；
  `prev_error` 含错误上下文；`base_spec["inputs"]` 保留；返回 `str`。
  `mmagent/tools/solve_tool.py` **未 import `mmagent.agents`**（仅注释提到）→ ✅ 依赖方向单向保持。

---

## 4. 「Unterminated string」根因的独立复算（本轮关键分歧）

### 4.1 实现方的诊断与修法
交互会话在 `mmagent/agents/base.py`（md5 `62c13416de11c0b7b429a88b5942426d`）改为：
`json.loads(strip_code_fence(raw), strict=False)`，注释理由是「qwen3.5 生成 code 字段时把 `\n` 写成真实换行，
严格解析直接 `JSONDecodeError`」。

### 4.2 队长的独立判定：**`strict=False` 与实测症状不对症**

本地实验（`.venv` python，无需 Ollama）三例对照：

| 输入形态 | `strict=True` | `strict=False` |
|---|---|---|
| 仅含**未转义真实换行**、字符串闭合 | `Invalid control character at: …` | **OK** |
| **字符串在 EOF 前未闭合**（截断） | `Invalid control character …` | **`Unterminated string starting at: …`** |
| 截断 + 含真实换行 | `Invalid control character …` | **`Unterminated string starting at: …`** |

→ **只有「未转义控制字符」这一种病因能被 `strict=False` 治愈；「字符串未闭合」治不了。**
而线上 6/6 的报错文本**恰恰是** `Unterminated string starting at: line 6 column 11 (char 176~236)`
（`att3_baseline.json` @20:34:49），**不是** `Invalid control character`。
故 `strict=False` **大概率不能解决当前 6/6 失败**。

**实测裁决（队长，20:55–21:01，`probe/lead_c/lead_c_result.json`）**：
在 `base.py` **已改为 `strict=False` 的当前代码**上，队长用自己的端到端抽查（真实 `solve_tool.solve`
链路，`gen_fn=generate_script` + `regen_fn=make_regen_fn`，CJK/ASCII 各 1 次）**复现了同一故障**：

| 模式 | 结果 | 异常（含 `base.py` 新增的 `raw[:500]` 片段） |
|---|---|---|
| `cjk` | **ok=False** | `LLMCallError: … Unterminated string starting at: line 6 column 11 (char 199)` |
| `ascii` | **ok=False** | `LLMCallError: … Unterminated string starting at: line 6 column 11 (char 213)` |

- 原始片段显示 `"code": "import matplotlib\nmatplotlib.use('Agg')` 含**真实换行**，且输出**中途被切断**
  （字符串未闭合）。
- → **`strict=False` 实测未能修复该故障**：错误文本**一字未变**（仍是 `Unterminated string`，
  **不是** `strict=False` 本应治愈的 `Invalid control character`）。
  这在事实上**否定了「裸换行是根因」的归因**，与 §4.3 的截断假设一致。
- 同时说明：该故障**可复现**（队长 0/2），但**非必现**（Vic 受控实验 9/9 正常）→ 属**压力相关的间歇截断**。

### 4.3 结构性根因：`num_predict` 超出 Ollama 上下文窗口

- `config/models.yaml`：`roles.coder.max_tokens: 8192`（即 `num_predict=8192`）。
- Ollama `/api/ps` 实测：`qwen3.5:latest`、`qwen3.5:0.8b` 的 **`context_length` 均为 `4096`**。
- → **`num_predict`(8192) > 模型 ctx(4096)**。当 prompt 较长（修复轮回传**完整源码** `prev_code`
  + 满额错误上下文 `MAX_STDERR_CHARS=2000`）时，**可用生成空间被挤压**，输出在 JSON `code` 字段中途
  被硬截断 → 字符串未闭合 → `Unterminated string`。这解释了「首轮生成成功、修复轮必崩」的位置特征
  （首轮无 `prev_code`，prompt 短）。
- 旁证：`att3_baseline.json` 6 条的报错位置**集中且极小**（char 176/185/189/196/196/236），
  与「输出刚开始就被切断」一致，而非「输出很长后被切断」。
- **对照实验（队长，21:02–21:04，`probe/lead_b/lead_ctx_compare.json`）**：用**与真实链路同构**的 spec
  （含 13 列 `data_schema`），各跑 1 次：

  | 场景 | `done_reason` | `prompt_eval_count` | `eval_count` | 输出闭合 | strict=True/False |
  |---|---|---|---|---|---|
  | A 现状（`num_predict=8192`，ctx 默认） | **stop** | 1597 | 1139 | ✅ | OK / OK |
  | B 提高 `num_ctx=16384` | **stop** | 1597 | 1080 | ✅ | OK / OK |

  → `1597 + 1139 = 2736 < 4096`，**未触顶**，A/B 无差别。
  **故「`num_predict` > ctx 必然截断」不成立**：Ollama 会把 `num_predict` 有效限制在剩余空间内，
  只有**输出需求超过剩余空间**时才会截断 → 截断是**条件性、间歇性**的，不是确定必现。
  （该结论亦与 Vic 的受控实验 9/9 正常相互印证。）

### 4.4 队长亲自抓取的两组真实 raw（`probe/lead_b/`）

| 实验 | prompt 规模 | `prompt_eval_count` | `eval_count` | `done_reason` | 输出闭合 | strict=True/False |
|---|---|---|---|---|---|---|
| ① 短 prev_code（1.5k 字符） | system 1998ch + user 1520ch | 1449 | 556 | **stop** | ✅ `}` 闭合 | **OK / OK** |
| ② 长 prev_code（12.8k 字符）+ 满额错误上下文 | system 1998ch + user 15434ch | **2050** | 851 | **stop** | ✅ `}` 闭合 | **OK / OK** |

- 结论 1：**正常/中等 prompt 下能稳定返回合法 JSON** → 「6/6 必崩」不成立，**失败是间歇性的**。
- 结论 2：实验 ② 的 `prompt_eval_count` 仅 2050（远小于 17432 字符应有的 token 量）
  → **Ollama 会静默截断超长 prompt（ctx=4096）**，属另一个需记录的行为（可能悄悄丢掉指令片段）。
- 结论 3（**重要**）：两次抓取都**没能复现** `Unterminated string` → 说明其触发依赖**具体输出长度**，
  是「压力下截断」而非确定性命中，**不能把单次观测写成确定命中率**。

### 4.5 该分歧的处置建议（**未代为改码**）
`strict=False` 属**可无损容忍的格式噪声**防线，本身无害，可保留；但它**不足以**解释与修复当前失败。
对症方向应为：**提高 `num_ctx`（如 8192/16384）或把 `num_predict` 降到 ctx 之内**，
并**收短修复轮输入输出**（回传源码应改为「只回传必要片段/差异」或要求「只输出 code」）——
否则「回传完整源码」这一 F5 改动会**加剧** prompt 挤压。**归属：实现方决策，队长不代改。**

---

## 5. 并发污染（本轮**最严重的环境风险**）

本轮多次观测到**至少三个执行体同时作业**：

| 执行体 | 证据（PID / 文件） |
|---|---|
| 交互会话探针 | `python.exe .agent-runs/2026-10-14/task-003/probe/att3_baseline.py`（PID 38044、16040，**两实例**） |
| 另一个 `pytest -q` 全量测试 | `python.exe -m pytest -q`（PID 30792、39172，**两实例**） |
| 队长验证成员 Vic | `python.exe …/probe/verify_vic/verify_b.py`（PID 34904 等） |

**后果（已实测）**：
- 队长跑 `pytest tests -q --basetemp=artifacts/tmp/bt` → **`54 passed, 1 xfailed, 199 errors in 57.93s`**；
  而**同一文件单独复跑** `pytest tests/test_nfr5_recovery.py -q` → **`32 passed in 208.84s`**。
  → 199 errors 系**并发干扰**（跨进程子测试与并发探针争资源），**非代码缺陷**。
- 三方并发争 Ollama（单机串行推理）→ 生成排队/变慢，**任何"可运行率"数字都会被环境污染**。

**结论（诚实性要求）**：本轮**所有**可运行率数字（含交互会话第二轮 `6/6 全崩`、本人探针、
Vic 的 C 项、全量测试计数）均在三执行体并发下取得，**不具备独占可复现性**。
`6/6 全崩` 不能直接当作"真实可运行率"，须在**独占环境**下复跑方可定论。

**补充复现（队长，20:48–20:53）**：

| 条件 | 全量测试结果 |
|---|---|
| 有活跃探针/pytest 并发（两次） | `54 passed, 1 xfailed, 199 errors`（两次**逐字相同**） |
| 并发退去后（一次） | **`253 passed, 1 xfailed in 351.34s`**（与 task-002 基线**逐字相符**） |

→ **代码未退化**（253/1 通过）；`199 errors` 是高负载下 subprocess 类用例被拖垮的**环境噪声**，
可由独占复跑消除。**独立验证成员 Vic 的 B1/B2 实验同样在非极端负载下 9/9 返回合法 JSON**
（`probe/verify_vic/verify_b_result.json`、`verify_b2_result.json`，含降级模型 0.8b 与真实 regen 路径，
`truncated_count=0`）→ **`Unterminated string` 无法在受控条件下复现**，其成因**不是**"模型必崩"，
亦**不能**归因于"strict 模式"。

---

## 6. 队长本轮**补做**的收尾项

1. **补发布基线**：`guard.py` 漂移后按约定重发 → 见本目录 `baseline-republish.md`（追加，不覆盖既有）。
2. **全量测试**：**未采信并发下的 199 errors**；将在独占窗口复跑后以最终计数为准（见 §7）。

---

## 7. 未决与待补

- [x] **独占复跑全量测试** → **`253 passed / 1 xfailed`**（并发下的 `199 errors` 作废，见 §5）。
- [ ] **Vic 的 C 项**（独立端到端可运行率，CJK/ASCII 各 3 次）回报后并入本报告 §8。
- [ ] 修复轮 ctx 截断问题（§4.3/§4.5）——属实现方决策。
- [ ] `strict=False` 的最终去留 —— 建议保留但明确其**不足以**修复截断。
