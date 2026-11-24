"""求解工具：生成 → 守卫 → 执行 → 单轮修复 的确定性串联。

依赖方向：tools 只调 pipeline（guard/runner/repair），**不 import agents**。
因此「生成脚本」与「重新生成」由上层（agents 层）以回调注入：
- gen_fn(task_spec) -> dict            ，约定返回 {"filename","code","declared_outputs",...}
- regen_fn(prev_src, error_context) -> str
`agents.coder.generate_script` 可直接作为 gen_fn；regen_fn 见 `make_regen_fn`。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

from ..pipeline import dod, guard, repair, runner

# FR-2 体检产物的默认位置（供 solve 自动装配 data_schema）；可被调用方覆盖。
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_REPORT_PATH = REPO_ROOT / "artifacts" / "intake" / "schema_report.json"
# 表级别名表（table_id 推导用）；只读，本模块**不 import intake.py**（避免耦合）。
ALIASES_PATH = REPO_ROOT / "config" / "field_aliases.yaml"


def validate_and_run(script_path: str | Path, workdir: str | Path,
                     timeout_s: int = 300) -> dict:
    """先做 AST 守卫再执行脚本。

    违规时抛 pipeline.guard.ScriptGuardError；否则返回 runner.run_script 的执行报告。
    """
    path = Path(script_path)
    guard.validate_script_ast(path.read_text(encoding="utf-8"))
    return runner.run_script(path, workdir, timeout_s=timeout_s)


def validate_only(src: str) -> None:
    """仅做静态守卫（供 API 预检脚本用），违规抛 ScriptGuardError。"""
    guard.validate_script_ast(src)


def solve(task_spec: dict, workdir: str | Path,
          gen_fn: Callable[[dict], dict] | None = None,
          regen_fn: Callable[[str, str], str] | None = None,
          timeout_s: int = 300, repair_rounds: int = 1, *,
          auto_schema: bool = True,
          schema_report_path: str | Path | None = None) -> dict:
    """串起 generate → guard → run → （失败则）repair 一轮。

    返回 {"generated", "run", "repaired", "script_path",
          "data_schema_status", "warnings", "dod"}。
    gen_fn 缺失时抛 ValueError（提示由上层注入 agents.coder.generate_script）。

    自 task-007 起（P4）：返回值新增 `dod`（= `pipeline.dod.check_dod(workdir)`），
    直接检查 workdir **实际产物**是否满足规划书 §4.2.2 / §8.3 的 P0-2 DoD
    （`results/summary.csv` 非空 + `figures/` 有 `dpi≈300` 的 PNG）。
    `dod.ok` 与 `run.ok` **是两个口径**：`run.ok` 只校验 manifest 声明过的文件，
    缺 `summary.csv` 时仍可能为 True；此字段**新增**，既有键一律不变（向后兼容）。

    自 task-008 起（P7）：`generated["declared_outputs"]` 会作为 `expected_outputs` 传给
    `runner.run_script`——脚本不必手写 `manifest.json`，由 runner 确定性生成
    （`run.manifest_source` = `"script"|"runner"|"absent"`）。

    自 task-004 起：`solve` 会在开头**自动装配** FR-2 体检产物的 data_schema
    （`auto_schema=True` 默认）；`data_schema_status` 显式回传装配结果，缺件**不抛错、不静默**。
    `auto_schema` / `schema_report_path` 为 keyword-only，向后兼容既有调用点。

    自 task-005 起（修正 N1）：`repair_rounds <= 0` 表示**不启用修复轮**——首轮失败时
    **不再调用 regen_fn**，直接以 `run.ok=False` 返回（守卫失败则原样抛出），
    从而保证「首轮失败」不被修复轮掩盖成 `ok=True`。
    """
    if gen_fn is None:
        raise ValueError(
            "solve 需要注入 gen_fn（如 agents.coder.generate_script）；"
            "tools 层不 import agents，以保持依赖方向单向")

    warnings: list[str] = []
    if auto_schema:
        task_spec, schema_status = attach_data_schema(task_spec, schema_report_path)
    else:
        schema_status = "skipped:disabled"
    if schema_status.startswith("skipped:") and schema_status != "skipped:already_present":
        warnings.append(
            f"data_schema 未装配（{schema_status}）：coder 将按语义猜列名，可能失败")

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    generated = gen_fn(task_spec)
    filename = generated.get("filename") or "solve.py"
    script_path = workdir / filename
    code = str(generated.get("code", ""))
    script_path.write_text(code, encoding="utf-8")
    # task-008（P7）：把 coder 的 declared_outputs 交给 runner——脚本不必再手写 manifest.json，
    # runner 会在必要时确定性地生成（manifest_source="runner"）。非 list/tuple 则按未提供处理
    # （等价于历史行为，不改既有调用语义）。修复轮沿用同一份 declared_outputs（regen 只返回 code）。
    declared = generated.get("declared_outputs")
    expected_outputs = list(declared) if isinstance(declared, (list, tuple)) else None
    repaired = False
    # repair_rounds<=0（如 cli --max-attempts 1）→ 不启用修复轮：不进修复分支
    can_repair = regen_fn is not None and int(repair_rounds) > 0

    def _run_node() -> dict:
        """跑脚本前清掉上一轮遗留的 `manifest.json`。

        否则修复轮重跑时，**上一轮**（或 runner 生成的）旧清单会被当成「脚本自写合法 manifest」
        （`manifest_source` 误判为 `script`），甚至用陈旧清单判定本轮产物 → 结论失真。
        manifest.json 是派生产物，每轮必须由本轮脚本或 runner 重新产生。
        """
        (workdir / "manifest.json").unlink(missing_ok=True)
        return runner.run_script(script_path, workdir, timeout_s=timeout_s,
                                 expected_outputs=expected_outputs)

    # 第一道闸：静态守卫。不过则同样走 repair 一轮（repair 内部含守卫复检）；
    # 未启用修复轮时直接抛出守卫错误（**不**把失败吞掉）。
    try:
        guard.validate_script_ast(code)
        report = _run_node()
    except guard.ScriptGuardError:
        if not can_repair:
            raise
        code = repair.repair_once(code, "", regen_fn, max_rounds=repair_rounds)
        script_path.write_text(code, encoding="utf-8")
        report = _run_node()
        repaired = True

    # 第二道闸：执行失败（rc≠0 / 超时 / manifest 缺件）→ repair 一轮后重跑；
    # 未启用修复轮时保持 run.ok=False 原样返回（首轮失败不得被报成成功）。
    if not report["ok"] and can_repair and not repaired:
        code = repair.repair_once(code, report.get("stderr_tail", ""),
                                  regen_fn, max_rounds=repair_rounds)
        script_path.write_text(code, encoding="utf-8")
        report = _run_node()
        repaired = True

    # P4（task-007）：DoD 判据与 runner.ok 分离——直接核对 workdir 实际产物。
    dod_report = dod.check_dod(workdir)

    return {"generated": generated, "run": report, "repaired": repaired,
            "script_path": str(script_path),
            "data_schema_status": schema_status, "warnings": warnings,
            "dod": dod_report}


# --------------------------------------------------------------------------
# task-003：把「输入稳健化」与「schema 交接」做成 solve 的前置工具
#
# 实测动机（.agent-runs/2026-10-14/task-003/baseline-repro.md）：
# ① 参赛附件的文件名是「附件1 各门店客户满意度综合得分-A题.xls」这种中文+空格，
#    LLM 生成脚本时**稳定地**插入多余空格（写作 `data/附件 1 …-A 题.xls`）→ FileNotFoundError。
# ② 附件1 的真实列名是 `月份门店` + `1.0`…`12.0`（月份列名就是数值），
#    表头还在第 1 行（第 0 行是合并大标题）—— 模型无从猜出，只会报「未找到包含'满意度'的列」。
#    FR-2 体检产物（schema_report.json / field_mapping.csv）里这些信息**都有**，此前没交接给 coder。
# --------------------------------------------------------------------------

_ASCII_SAFE = set("abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def stage_inputs(inputs: Sequence[str | Path], workdir: str | Path,
                 subdir: str = "data") -> list[str]:
    """把输入文件复制进 workdir 并改用**纯 ASCII 文件名**，返回模型可见的相对路径列表。

    纯 ASCII 且无空格的文件名保持原名；否则重命名为 `att1<后缀>`、`att2<后缀>`…
    （按输入顺序编号）。这样可消除「模型给中文名插空格」这一稳定失败模式。
    """
    workdir = Path(workdir)
    dest_dir = workdir / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for i, raw in enumerate(inputs, start=1):
        src = Path(raw)
        if not src.exists():
            raise FileNotFoundError(f"输入文件不存在：{src}")
        name = src.name
        if all(ch in _ASCII_SAFE for ch in name):
            alias = name
        else:
            alias = f"att{i}{src.suffix.lower()}"
        shutil.copy2(src, dest_dir / alias)
        staged.append(f"{subdir}/{alias}")
    return staged


def _excel_col_letter(index: int) -> str:
    """0-based 列序号 → Excel 列字母（0→A, 25→Z, 26→AA）。"""
    if not isinstance(index, int) or index < 0:
        return ""
    s = ""
    n = index
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def _excel_col_index(source_column: Any, fallback: int) -> int:
    """把 `source_column` 规范成 0-based 列序号。

    兼容两种上游写法：Excel 列字母（`'A'`）或整数序号（`0`）；无法解析时用 fallback。
    """
    if isinstance(source_column, bool):      # bool 是 int 子类，显式排除
        return fallback
    if isinstance(source_column, int):
        return source_column
    if isinstance(source_column, str):
        s = source_column.strip().upper()
        if s.isalpha():
            idx = 0
            for ch in s:
                idx = idx * 26 + (ord(ch) - ord("A") + 1)
            return idx - 1
        try:
            return int(s)
        except ValueError:
            return fallback
    return fallback


def table_schema_digest(schema_report_path: str | Path,
                        table_id: str,
                        *,
                        runtime_inputs: Sequence[str | Path] | None = None
                        ) -> dict[str, Any]:
    """从 FR-2 体检产物 `schema_report.json` 抽出可直接喂给 coder 的**精简 schema**。

    返回：
      table_id / source_file / source_file_role / runtime_inputs / sheet_name /
      header_row(0-based) / data_start_row(0-based) / title /
      columns[{col, index, name, raw, canonical, dtype}] /
      mapped_fields{count, meaning, forbidden} / locating_contract / note

    **列定位契约（task-005 修正 N7，task-006 收口 P1/P2）**：
      - `columns[]` 中**唯一可用于定位物理列的「列坐标」键**是 `col`（Excel 列字母，等价 `index`）；
      - **完整定位三角 = (sheet_name, col, header_row)** —— 同一 `table_id` 可能对应多个 sheet；
      - `name` / `raw` / `canonical` 一律**不得**用于与物理表头做字符串匹配
        （物理表头可能含前导/尾随空格、可能是数值、可能有空列；`raw` 是 FR-2 派生值，非物理真值）；
      - **禁止 `sh.ncols == mapped_fields.count` 之类自检**：已映射字段数 ≠ Excel 物理列数
        （实测同一文件：`xlrd` 默认 `ncols=14`、`formatting_info=True` 为 `25`、已映射 `13`）；
        越界保护请用 `col`→0-based `index` 的**显式映射** + 边界处理。

    `runtime_inputs`：运行时**实际可用**的输入路径（真值来源与 `stage_inputs()` **一致**，不另起命名规则）。
    读文件请用 `runtime_inputs`，**不要**用 `source_file`（后者是原始附件名，仅溯源）。
    """
    data = json.loads(Path(schema_report_path).read_text(encoding="utf-8"))
    tables = data["tables"] if isinstance(data, dict) else data
    table = next((t for t in tables if t.get("table_id") == table_id), None)
    if table is None:
        raise KeyError(f"schema_report 中找不到 table_id={table_id!r}")
    fields = [f for f in (data.get("fields") or [])
              if f.get("table_id") == table_id]
    header_rows = int(table.get("header_rows") or 1)

    columns = []
    for i, f in enumerate(fields):
        idx = _excel_col_index(f.get("source_column"), i)
        columns.append({
            "col": _excel_col_letter(idx),      # Excel 列字母：**唯一**列坐标定位键
            "index": idx,                       # 0-based 列序号（与 col 同一坐标）
            "name": f.get("field_name"),        # 规范化列名（已 strip），语义参考
            "raw": f.get("field_raw"),          # intake 派生值，**非物理真值**，仅参考
            "canonical": f.get("canonical"),    # 下游语义名，**不是表头**
            "dtype": f.get("dtype"),
        })

    runtime_paths = [str(p) for p in (runtime_inputs or [])]

    return {
        "table_id": table_id,
        "source_file": table.get("source_file"),
        "source_file_role": ("原始附件名（仅供溯源）；**非**运行时路径，"
                             "禁止用于 open()/拼接读取路径"),
        "runtime_inputs": runtime_paths,
        "runtime_inputs_note": ("运行时**实际可用**路径（真值来源与 stage_inputs() 一致）；"
                                "读文件请用这些，不要用 source_file。"
                                + ("" if runtime_paths
                                   else "（本 digest 未注入 runtime_inputs，调用方需自行提供）")),
        "sheet_name": table.get("sheet_name"),
        "title": table.get("table_title"),
        # 真列名所在行（0-based）：header_rows 是"表头占几行"，其最后一行才是列名行
        "header_row": max(0, header_rows - 1),
        "data_start_row": header_rows,
        "mapped_fields": {
            "count": len(columns),
            "meaning": ("本 digest **已映射字段数**（= len(columns)）；"
                        "**不是** Excel 物理列数"),
            "forbidden": ("禁止与 `xlrd` 的 sh.ncols / openpyxl max_column 做等值自检"
                          "（同一文件实测：xlrd 默认 ncols=14、formatting_info=True 为 25、"
                          "已映射 13，三者都不等）"),
        },
        "columns": columns,
        "locating_contract": {
            "sole_key": "col",
            "sole_key_scope": "column_coordinate",
            "usage": ("列定位**必须**用 columns[].col（Excel 列字母；A=0,B=1,…，"
                      "与 columns[].index 等价）配合 header_row 读取该列表头；"
                      "**禁止**用 name/raw/canonical 去和物理表头做字符串比较。"
                      "越界保护请用 col→index 的显式映射 + 边界处理，"
                      "**不要**做 sh.ncols == mapped_fields.count 之类自检。"),
            "full_locating_tuple": ["sheet_name", "col", "header_row"],
            "not_usable_for_header_match": {
                "name": "规范化列名（已 strip），仅语义参考，非物理表头",
                "raw": "intake.field_raw 的派生/规范化值（已 strip/字符串化），非物理真值，仅参考",
                "canonical": "下游语义名，不是表头",
            },
            "physical_header_warning": ("物理表头可能含前导/尾随空格、可能为数值（如 1.0）、"
                                        "可能存在空列（''）；不要对物理表头做字符串等值匹配。"),
            "forbidden_self_checks": [
                "sh.ncols == mapped_fields.count",
                "len(columns) == sh.ncols",
                "任何以 name/raw/canonical 做表头字符串等值匹配的写法（含 str()/strip() 后再查表）",
            ],
            "boundaries": {
                "multi_sheet_same_table_id":
                    ("同一 table_id 可能对应多个 sheet（实测 summary_master ×2：'A题 (2)'/'A题'）；"
                     "此时仅凭 col 不足以定位，**必须**同时用本 digest 的 sheet_name 选择 sheet。"),
                "composite_header":
                    ("复合表头（header_rows>1，如实测 att6_profit=3）下 header_row 指向**子表头行**，"
                     "不是整表标题行；完整列身份需合并多行表头（columns[].name 是合并后的语义名，仅供人读）。"),
                "physical_column_range":
                    ("本 digest 只覆盖**已映射字段**（mapped_fields.count 个）；Excel 物理列范围取决于"
                     "读取器口径（xlrd 默认 vs formatting_info=True 不同），尾部可能有未映射空列；"
                     "**禁止**用 sh.ncols 与 mapped_fields.count 做等值自检。"),
            },
        },
        "note": ("列定位以 columns[].col（Excel 列字母）+ header_row 为准（完整定位还需 sheet_name）；"
                 "不要用 columns[].name/raw/canonical 去匹配物理表头"
                 "（name 是给人看的语义名，物理单元格可能是 float，如 name='1月' 而物理='1.0'；"
                 "表头还可能含前导空格/存在空列）；"
                 "**禁止** sh.ncols == mapped_fields.count 之类自检（已映射字段数 ≠ 物理列数，尾部可能有空列）；"
                 "读文件请用 runtime_inputs，不要用 source_file（原始名，仅溯源）；"
                 "第 0 行通常是合并大标题，不是列名。"),
    }


def resolve_table_id(source_file: str | Path,
                     aliases_path: str | Path | None = None) -> str | None:
    """按 `config/field_aliases.yaml` 的 `tables[].match`（子串）推导 table_id；无命中返回 None。

    语义与 `pipeline/intake.py:_table_meta`（256-261）对齐，但本函数**不 import intake**，
    只重复那几行匹配逻辑（避免新增同层耦合）。
    ⚠️ 只应对**原始文件名**（如 `附件1 各门店客户满意度综合得分-A题.xls`）调用：
    `stage_inputs()` 会把输入改名成 `att1.xls`，届时 `match:"附件1"` 不再命中 → 返回 None。
    """
    name = Path(str(source_file)).name
    try:
        data = yaml.safe_load(
            Path(aliases_path or ALIASES_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    for item in data.get("tables", []) or []:
        m = item.get("match")
        if m and m in name:
            return item.get("table_id")
    return None


def attach_data_schema(task_spec: dict,
                       schema_report_path: str | Path | None = None,
                       table_id: str | None = None) -> tuple[dict, str]:
    """把 FR-2 体检产物的精简 schema 注入 `task_spec["data_schema"]`（原地 + 返回）。

    返回 `(task_spec, status)`，status ∈
    `{'attached','skipped:already_present','skipped:no_schema_report',
      'skipped:no_table_id','skipped:digest_failed'}`。
    降级**必须显式**：任一环节缺件都**不抛错**，只回传 `skipped:*`（由调用方写 warnings/落库）。

    task-006（P2）：把 `task_spec["inputs"]`（= `stage_inputs()` 的**运行时产物路径**）
    作为 `runtime_inputs` 传给 digest，真值来源与 `stage_inputs()` **一致**（不另起命名规则），
    使 coder 读到的是**运行时实际可用路径**而非原始附件名。
    """
    if task_spec.get("data_schema"):
        return task_spec, "skipped:already_present"
    tid = table_id or task_spec.get("table_id")
    if not tid:
        src = task_spec.get("source_file")
        if src:
            tid = resolve_table_id(src)
    if not tid:
        # N7：只给了 staging 后的名字（att1.xls）时走到这里 → 显式 skipped，**不**退化成 T{n}/KeyError
        return task_spec, "skipped:no_table_id"
    report = Path(schema_report_path) if schema_report_path \
        else Path(task_spec.get("schema_report_path") or SCHEMA_REPORT_PATH)
    if not report.exists():
        return task_spec, "skipped:no_schema_report"
    try:
        task_spec["data_schema"] = table_schema_digest(
            report, tid, runtime_inputs=task_spec.get("inputs"))
    except Exception:  # noqa: BLE001  缺 table/文件损坏 → 降级，不阻断求解
        return task_spec, "skipped:digest_failed"
    return task_spec, "attached"
