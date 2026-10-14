# baseline-repro.md — FR-5 端到端可运行率基线复现（task-003）

- 时间：2026-10-14 19:50–21:00｜探针：`probe/att3_baseline.py`｜明细：`probe/baseline/att3_baseline.json`
- 解释器：`.venv\Scripts\python.exe`（Python 3.13.14）｜模型：`qwen3.5:latest`（Ollama 本地）

---

## 1. 方法

同一任务规格（读客户满意度表 → 画各门店月度折线 → 落 `manifest.json`），两种输入命名各跑 **3 次**：

| 模式 | 输入相对路径 |
|---|---|
| `cjk` | `data/附件1 各门店客户满意度综合得分-A题.xls`（原名） |
| `ascii` | `data/att1.xls`（ASCII 化） |

链路用真实 `tools.solve_tool.solve`（generate → guard → run → repair → rerun），
判据统一为 `runner.run_script` 的 `ok`（未超时 且 `rc==0` 且 manifest 声明的产物都存在）。
每次生成失败最多 1 轮修复。工作区逐次新建，产物落 `probe/ws_<mode>_<i>/`。

---

## 2. 结果：两模式均 **0/3**，且失败原因**不是**先前记录的那两条

```
cjk   : 0/3 ok   失败分类=['exception', 'exception', 'exception']
ascii : 0/3 ok   失败分类=['exception', 'exception', 'exception']
```

6 次全部在**生成阶段**就抛 `ScriptGenerationError`，`rc=None` —— **脚本一次都没被真正执行**。
典型错误（6/6 中 5 次同时命中两条）：

```
第4行：import xlrd 不在白名单（仅允许：collections, csv, datetime, itertools, json,
        math, matplotlib, numpy, pandas, pathlib, random, re, sqlite3, statistics）
第6行：禁止 import os（os 在禁止名单）
```

### 2.1 根因：守卫白名单与任务要求自相矛盾（P0）

`mmagent/pipeline/guard.py:23-27` 的 `ALLOWED_IMPORTS` **不含 `xlrd`**，而：

- FR-5 的任务本体就是读 `附件1~6`（全部是 `.xls`）；
- `mmagent/agents/prompts/coder.system.j2` 明确教模型「读 .xls 用 xlrd」。

→ **提示词和守卫互相打架，任务在结构上不可能通过守卫**。这不是模型质量问题。

另有一处**自相矛盾**：`BANNED_IMPORTS`（guard.py:29-32）把 `os` 整块禁掉，但
`BANNED_ATTR_CALLS`（:36-41）又专门列了 `("os","system")`/`("os","popen")`/`("os","execv")`/`("os","spawnv")`
—— 设计原意是「允许 os、只禁危险调用」，结果 `os` 进了禁止名单，那 4 条属性级规则**永远不可能命中**（死规则）。

守卫是 task-001 修复轮为了堵 `from os import system` / `importlib.import_module` 绕过漏洞而从黑名单改成白名单的（方向正确），但**白名单收得过窄，收掉了任务必需的库**，属"加固沙箱反过来把正常业务打死"的典型回归。

### 2.2 修正先前的归因

`PROGRESS.md` / `implementer-report.md` 把 0/3 归因为①CJK 空格 quirk ②模型 xlrd 用法混淆。这两条**是真实存在的**（`artifacts/runs/smoke5/solve.py:13` 确有 `'data/附件 1.xls'`），但那是 **P3 轮（10-13 19:50）** 的观测；当时的守卫还是**黑名单版**，所以脚本能走到执行阶段才报 `FileNotFoundError`。
**白名单版是 20:01 之后才落地的**（见 md5/mtime 记录），此后所有含 `xlrd`/`os` 的脚本**在守卫处即被拦死** —— 归因已经换了，旧结论不能再用。

### 2.3 对照实测（守卫放行矩阵）

| 脚本形态 | 守卫结果 |
|---|---|
| `import xlrd` + 直读 | **BLOCK** |
| `pd.read_excel("a.xls")` | PASS |
| `pathlib.Path.mkdir` | PASS |
| `import os` | **BLOCK** |
| `pandas + matplotlib` | PASS |

→ 走 pandas 路径其实能过（运行时 pandas 内部仍会用 xlrd，禁令属"安全剧场"），
但提示词偏偏指向 xlrd。**要么放行 xlrd，要么改提示词**，不能两边各说一套。

---

## 3. 修法（已定，按此实施）

### F1（P0）守卫白名单补上 `.xls/.xlsx` 解析库
`guard.py` `ALLOWED_IMPORTS` 增补 **`xlrd`、`openpyxl`**（均为纯本地文件解析库：无网络、无执行能力，
与"零外网 + 无危险调用"的守卫目的不冲突；`附件1~6` 是 `.xls`、`总表` 是 `.xlsx`）。同步更新模块 docstring。

### F2 不清除 `os` 禁令，改提示词对齐
`os` 整块禁止是**刻意的白名单硬化**（`tests/test_fr5_guard_runner.py` 明确断言
"即便只用 `os.makedirs` 也一律拒绝，这是黑名单→白名单的核心收益"），且 `pathlib` 完全可替代 → **保留禁令**。
改为在提示词里明确告知沙箱边界。顺带注明 `BANNED_ATTR_CALLS` 里的 `os.*` 规则是纵深防御（当前不可达）。

### F3 修正提示词中关于 xlrd 的**事实错误**
`coder.system.j2` 原写「`sheet.row(i)` / `sheet.cell_value(r,c)` 返回的是 **Cell 对象**」——
**后半句错**：`cell_value()` 直接返回值、不返回 Cell。这很可能是 `'Cell' object has no attribute 'strip'` 的源头。

### F4 提示词显式给出可 import 白名单 + 禁止清单
把 `ALLOWED_IMPORTS` 原文与 `os/sys/subprocess/shutil/importlib/requests/httpx/socket…` 禁止清单写进提示词，
并说明"建目录用 `pathlib.Path(...).mkdir(parents=True, exist_ok=True)`"。消除提示词与守卫的信息差。

### F5 补齐 `make_regen_fn`
`tools/solve_tool.py` docstring 承诺「regen_fn 见 `make_regen_fn`」，但全仓库**零实现** → 在 `agents/coder.py` 提供
`make_regen_fn(...)`，回传**上一版完整源码** + 错误上下文，返回纯源码字符串；保持 `tools` 不 import `agents` 的依赖方向。

---

## 4. 边界

- 未改 `os` 禁令（见 F2 理由）；未放宽 `BANNED_ATTR_CALLS` 中真正危险的属性调用。
- 本轮**不追求**把可运行率刷到某个数字：F1~F4 落地后按探针**如实记录**，跑不通就写清失败分类。

---

## 5. 补充：修复轮实测与**完整因果链**（2026-10-14 20:00–21:00）

### 5.1 结论先行（诚实版）

**本轮实测的可运行率仍然很低（最好的一次是 ascii 1/3），但失败原因被彻底换掉了** —— 从"任务在结构上过不了守卫"变成"模型生成的 JSON 偶尔畸形 + 少数语义误判"。
**七条根因里没有一条是"模型不够聪明"**，全部是管道自身问题。上一轮把 0/3 归因为"模型输出质量"是**错的**。

### 5.2 实测矩阵（每轮 3 次 × 2 模式，真跑 `qwen3.5:latest`）

| 轮次 | 配置 | cjk | ascii | 主要失败分类 |
|---|---|---|---|---|
| R1 | 修复前基线 | 0/3 | 0/3 | 6/6 `exception`（守卫拦 `xlrd`/`os`） |
| R2 | 仅放行 xlrd（`ok` 仍为旧语义） | 3/3* | 2/3* | *数字**作废**：`ok` 假阳性，产物目录里其实空无一物 |
| R3 | 收紧 `ok` + 禁吞异常 + 表头提示 | 0/3 | 1/3 | `filename_quirk`(2)、`manifest_missing`(2)、1 次**真成功**（PNG **203,595 字节**） |
| R4 | 再加 schema 交接 | 0/3 | 0/3 | 6/6 `exception`（JSON `Unterminated string`） |
| R5 | 再加 `strict=False` + 保留原始片段 | 0/3 | 0/3 | 5 `exception`、1 `manifest_missing` |

> R2 的 `ok=True` 是**假阳性**：脚本什么都没产出却被判成功。**改进数字必须落到真实产物上核对**，只看布尔标志会得出完全错误的结论。

### 5.3 七条根因（全部已修，除最后一条待跟进）

| # | 层 | 问题 | 处置 |
|---|---|---|---|
| 1 | **守卫** | `ALLOWED_IMPORTS` 漏收 `xlrd`/`openpyxl`，而任务就是读 `.xls`、提示词还教用 xlrd → **任务在结构上过不了守卫** | 白名单补入两者；`os` 保留禁令并注明是刻意整块禁止 |
| 2 | **判据** | `runner.ok = rc==0 and not manifest_missing`，而 `manifest_missing` 只在 manifest **存在时**才统计 → **零产物被判成功**（假阳性） | `ok` 增加 `manifest_present` 前置条件 + 新增同名报告字段 |
| 3 | **提示词** | 称 `cell_value()` 返回 Cell 对象（**事实错误**，它直接返回值）——很可能是 `'Cell' object has no attribute 'strip'` 的源头 | 改为最小正确示例 |
| 4 | **提示词** | 未禁止吞异常 → 模型写 `except Exception: print(e)`，**失败伪装成 rc=0** | 明确"失败必须以非 0 退出码结束"；实测生效：失败脚本改回 `raise`，`rc=1` |
| 5 | **命名** | 模型稳定给中文名插入空格（`data/附件 1 …-A 题.xls`）→ `FileNotFoundError` | 新增 `solve_tool.stage_inputs()`：输入复制进 workdir 并 ASCII 化 |
| 6 | **模块交接（本轮最大发现）** | 附件1 真实列名是 `月份门店` + `1.0`…`12.0`、表头在第 1 行（第 0 行是合并大标题）。**FR-2 体检早已算出这些，却从未传给 FR-5** → 模型只会报"未找到包含'满意度'的列"（合理反应） | 新增 `solve_tool.table_schema_digest()`：从 `schema_report.json` 抽出表头行 + 真列名 + canonical + dtype，放进 `task_spec["data_schema"]` |
| 7 | **解析（待跟进）** | 模型间歇性产出畸形 JSON（`Unterminated string starting at line 6 column 11`，六次报错位置完全一致 → `code` 值内部断裂）。同一提示词下**时而全好时而全坏**，属格式噪声而非能力缺陷 | 已用 `json.loads(..., strict=False)` 容忍裸控制字符；**并保留失败原始输出片段**（原实现直接丢弃，导致此类问题无法定位）。**仍需抓完整原始文本定位断点** |

### 5.4 未解决的观察（留给续作）

- **模型会"自报错误"代替产出**：cjk#2 返回 `{"error": "文件不存在", "code": "FileNotFoundError: …"}`，完全不符合 schema。属模型行为，schema 校验正确拦下；可考虑在提示词中明确"不得返回 error 对象"。
- **JSON 断裂点未定位**：需要把**完整** raw 落盘（当前只留 500 字符）。
- **FR-2 → FR-5 的 schema 交接尚未产品化**：目前由调用方（探针）手工调用 `table_schema_digest`。应由流水线在 solve 前自动装配（读 `artifacts/intake/schema_report.json`）。
- 采样量小（每格 3 次），结论只作**可复现性描述**，不当作稳定命中率。

### 5.5 本轮改动的冻结基线影响

- `mmagent/pipeline/guard.py`（**在冻结基线内**）→ md5 变更，须重发布基线并让测试方重跑；
- `README.md` 亦为基线内文件（本轮未改，沿用 task-002 已发布的新值）；
- 其余改动文件（`runner.py`/`base.py`/`coder.py`/`solve_tool.py`/`prompts/coder.system.j2`）**不在**基线内。
