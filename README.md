# mmagent —— 数学建模竞赛协作 Agent（全本地、零云支出）

面向 72 小时数模竞赛的本地协作 Agent：附件体检 → 口径确认 → 建模规划 → 求解执行 →
结果理智性检查 → 论文级出图 → 合规披露，全流程本地推理（Ollama），无任何云端 API 调用。

## 1. 环境

- Python：**3.13.12**（本机无 CPython 3.11，故使用 3.13，已记入 KNOWN_ISSUES）
- 虚拟环境：`H:\mm-agent\.venv\Scripts\python.exe`
- 安装依赖：

```bat
H:\mm-agent\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Ollama 前置

- 服务地址：`http://localhost:11434`（只允许本机回环，禁止外网）
- 模型（写入 `config/models.yaml`，代码中禁止硬编码模型名）：
  - 生成模型：`qwen3.5:latest`
  - 降级模型：`qwen3.5:0.8b`
  - 向量模型：`qwen3-embedding`
- 确认在线与模型列表：

```bat
H:\mm-agent\.venv\Scripts\python.exe -m mmagent.cli env-attest --out artifacts\reports\env_attest.json
```

## 3. 快速开始

```bat
cd /d H:\mm-agent

:: 1) 初始化数据库（WAL + 触发器）
.venv\Scripts\python.exe -m mmagent.cli db init --db data\mm.db

:: 2) 附件体检（数据目录只读参考：C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题）
.venv\Scripts\python.exe -m mmagent.cli intake --dataset "C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题" --out artifacts\intake

:: 3) 静态合规检查（仓库内 *.py 不得直连外网）
.venv\Scripts\python.exe -m mmagent.cli netguard-check

:: 4) 启动服务
.venv\Scripts\python.exe -m uvicorn mmagent.app:app --host 127.0.0.1 --port 8000
```

启动后可访问 `http://127.0.0.1:8000/docs` 查看接口。

## 4. HTTP 接口（/api/v1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/intake/run` | 附件体检（`{"dataset_dir":..,"out_dir":..}`） |
| POST | `/api/v1/solve/run` | 守卫 + 执行脚本 |
| GET | `/api/v1/profile/{profile_id}` | 读取档案版本（`?version=last`） |
| POST | `/api/v1/compliance/disclosure` | 生成 AI 使用披露（md + docx） |

统一错误体：`{"error_code": ..., "message": ..., "detail": ..., "trace_id": ...}`。

## 5. 架构与依赖方向

```
api → agents → tools → pipeline → store → db
```

- `store/`、`checks/` 不得 import `agents/` 或 `api/`；
- `tools/` 为纯确定性薄封装，**不得** import `agents/`；
- 全本地：任何模块不得 import `requests/socket/urllib/ftplib`，Ollama 仅走 `localhost:11434`；
- `mmagent/rag/adapter.py` 是全仓库唯一允许引用 `RAG_ROOT` 的模块（当前占位未启用）。

## 6. 已知偏差（KNOWN_ISSUES）

- 本机无 `qwen3:4b` → 降级链改为 `qwen3.5:latest` → `qwen3.5:0.8b`；
- 本机无 CPython 3.11 → 使用 3.13.12（托管运行时）；
- `repair.py`、`intake_llm.py`、`rag/adapter.py`、各角色 `agents/*.py` 为骨架，
  待后续阶段接入 LLM 与 RAG。

## 7. 静态守卫（FR-5）— import 白名单

LLM 生成的脚本执行前必过 `pipeline/guard.py` 的 AST 检查，采用 **import 白名单**
（而非黑名单，避免 `from os import system` / `importlib.import_module` 等绕过）：

- 白名单顶层模块：`pandas / numpy / matplotlib / math / json / csv / sqlite3 /
  pathlib / statistics / itertools / collections / datetime / re / random`；
- 危险调用黑名单（补网）：`eval / exec / __import__ / compile`、
  `os.system / os.popen / subprocess.* / importlib.import_module`；
- 禁止相对导入。违规抛 `ScriptGuardError`（含全部违规行号）。

## 8. 数据解析纪律与已知数据问题

1. **跨表汇总必须按门店 id `join`，禁止按行号对齐**：附件1 的门店顺序与其他附件不同
   （乱序），附件2~6 为 `A1..A5,B1..B7` 顺序。
2. 附件4/5 表标题明写「空格处为0」：空缺即 0（`fillna_rule`，非缺失）。
3. 附件6 的「利润定额」存在逐行全等的月份组（`def_1_2`/`def_3_11`/`def_7_8`），
   「评级」另有一组（`qual_1_2_7_8_10`）——建模时勿去重，注意共线。
4. 附件3 有 17 处文本混入数值列（`'97.3.4'`，见 `dirty_cells.json`），
   只留痕不改值，需业务确认。
5. 附件1 的 `N` 列为派生列（各月得分算术均值），见 `unmapped_columns.json`。
6. 输入白名单见 `config/intake_scope.yaml`：只吃 `总表.xlsx` + `附件1~6`，
   `预处理*.xlsx` 记入 `skipped_files`。
7. `table_id` 采用文档口径：文档仅**显式定义** `att1_satisfaction` / `att5_complaint` /
   `att6_profit` 三个；`att2_service` / `att3_clean` / `att4_complaint` 为**按同构规则外推**。
   旧/外推名（`att6_profit_quota`、`att5_problem`）见 `config/field_aliases.yaml` 的 `legacy_table_ids` 兼容映射。
