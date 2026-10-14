"""验证员 Vic — Part A：绕过应用层的静态/对抗验证（不需要 Ollama）。

覆盖 A1(guard 放行矩阵) / A2(白名单-禁名单集合运算) / A3(runner 新判据三态)
     / A4(stage_inputs) / A5(make_regen_fn 回传 prev_code + 依赖方向)。
只读产品代码，不改 mmagent/**。产物落 probe/verify_vic/。
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(r"H:\mm-agent")
sys.path.insert(0, str(REPO))

from mmagent.pipeline.guard import (  # noqa: E402
    ALLOWED_IMPORTS, BANNED_ATTR_CALLS, BANNED_CALLS, BANNED_IMPORTS,
    ScriptGuardError, validate_script_ast)
from mmagent.pipeline.runner import run_script  # noqa: E402
from mmagent.tools import solve_tool  # noqa: E402

SRC_XLS = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题"
               r"\附件1 各门店客户满意度综合得分-A题.xls")
OUT = Path(__file__).with_name("verify_a_result.json")
result: dict = {}


def guard_case(src: str) -> dict:
    try:
        validate_script_ast(src)
        return {"verdict": "PASS", "violations": []}
    except ScriptGuardError as exc:
        return {"verdict": "BLOCK", "violations": list(exc.violations)}


# ------------------------------------------------------------------ A1
A1_CASES = {
    "import xlrd": "import xlrd\nwb = xlrd.open_workbook('data/att1.xls')\n",
    "import openpyxl": "import openpyxl\nwb = openpyxl.load_workbook('data/a.xlsx')\n",
    "import os": "import os\n",
    "from os import system; system('dir')": "from os import system\nsystem('dir')\n",
    "import importlib": "import importlib\n",
    "importlib.import_module('subprocess')": (
        "import importlib\nimportlib.import_module('subprocess')\n"),
    "import subprocess": "import subprocess\n",
    "import httpx": "import httpx\n",
    "import http.client": "import http.client\n",
    "eval('1')": "eval('1')\n",
    "os.system (先 import os)": "import os\nos.system('dir')\n",
    "干净脚本 pandas+matplotlib": textwrap.dedent('''
        import pandas as pd
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from pathlib import Path
        Path("out").mkdir(exist_ok=True)
        df = pd.DataFrame({"m": [1, 2], "s": [1.0, 2.0]})
        fig, ax = plt.subplots()
        ax.plot(df["m"], df["s"])
        fig.savefig("out/f.png", dpi=300)
    '''),
    # 额外对抗：xlrd 是否被"顺手"放行了危险库
    "openpyxl 后跟 os.system": "import openpyxl\nos.system('dir')\n",
    "from xlrd import open_workbook": "from xlrd import open_workbook\n",
}

# ------------------------------------------------------------------ A2
a2 = {
    "ALLOWED": sorted(ALLOWED_IMPORTS),
    "BANNED": sorted(BANNED_IMPORTS),
    "intersection": sorted(ALLOWED_IMPORTS & BANNED_IMPORTS),
    "banned_not_in_allowed": sorted(BANNED_IMPORTS - ALLOWED_IMPORTS),
    "allowed_not_banned": sorted(ALLOWED_IMPORTS - BANNED_IMPORTS),
    "dangerous_top_level_in_allowed": sorted(
        ALLOWED_IMPORTS & {"os", "sys", "shutil", "importlib",
                           "subprocess", "urllib", "socket", "http",
                           "requests", "httpx", "ftplib", "pty", "signal"}),
    "banned_calls": sorted(BANNED_CALLS),
    "banned_attr_calls": sorted(".".join(t) for t in BANNED_ATTR_CALLS),
}
result["A2_sets"] = a2

# ------------------------------------------------------------------ A3
S_NO_MANIFEST = "from pathlib import Path\nPath('result.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\nprint('no-manifest rc0')\n"
S_MANIFEST_OK = (
    "import json\nfrom pathlib import Path\n"
    "Path('out').mkdir(exist_ok=True)\n"
    "Path('out/f.csv').write_text('a\\n1\\n', encoding='utf-8')\n"
    "Path('manifest.json').write_text(json.dumps({'files':['out/f.csv']}), encoding='utf-8')\n")
S_MANIFEST_MISSING = (
    "import json\nfrom pathlib import Path\n"
    "Path('manifest.json').write_text(json.dumps({'files':['out/never.csv']}), encoding='utf-8')\n"
    "print('declared but not produced')\n")
S_CRASH = "raise RuntimeError('boom')\n"


def runner_case(name: str, code: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sp = td / "s.py"
        sp.write_text(code, encoding="utf-8")
        rep = run_script(sp, td / "wd", timeout_s=60)
        return {
            "name": name,
            "rc": rep["returncode"], "ok": rep["ok"],
            "manifest_present": rep["manifest_present"],
            "manifest_missing": rep["manifest_missing"],
            "produced_files": rep["produced_files"],
            "timed_out": rep["timed_out"],
        }


a3 = [
    runner_case("rc=0 且无 manifest", S_NO_MANIFEST),
    runner_case("rc=0 且 manifest 齐全", S_MANIFEST_OK),
    runner_case("rc=0 且 manifest 缺件", S_MANIFEST_MISSING),
    runner_case("rc!=0 崩溃(无 manifest)", S_CRASH),
]
result["A3_runner"] = a3

# ------------------------------------------------------------------ A4
a4: dict = {}
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    staged = solve_tool.stage_inputs([SRC_XLS], td)
    a4["cjk_staged_return"] = staged
    a4["cjk_file_exists"] = [str((td / p).exists()) for p in staged]
    ascii_src = td / "already_ascii.xls"
    ascii_src.write_bytes(b"x")
    a4["ascii_kept"] = solve_tool.stage_inputs([ascii_src], td / "wd2")
    # 不存在的输入应抛 FileNotFoundError
    try:
        solve_tool.stage_inputs([td / "nope.xls"], td / "wd3")
        a4["missing_raises"] = False
    except FileNotFoundError:
        a4["missing_raises"] = True
result["A4_stage_inputs"] = a4

# ------------------------------------------------------------------ A5
class StubR:
    def __init__(self):
        self.payloads = []

    def run(self, payload, temperature=None):
        self.payloads.append(dict(payload))
        return {"module": "m", "filename": "solve.py", "purpose": "p",
                "declared_outputs": [], "code": "print('new')\n"}


from mmagent.agents.coder import make_regen_fn  # noqa: E402

stub = StubR()
old_src = "print('old')\n# 上一版完整源码\n"
regen = make_regen_fn(runner=stub, base_spec={"inputs": ["data/att1.xls"]})
new_src = regen(old_src, "BOOM-error-context")
a5 = {
    "regen_return_type": type(new_src).__name__,
    "regen_return": new_src,
    "captured_payload_keys": sorted(stub.payloads[0].keys()),
    "prev_code_in_payload": stub.payloads[0].get("prev_code"),
    "prev_code_equals_old_src": stub.payloads[0].get("prev_code") == old_src,
    "inputs_preserved": stub.payloads[0].get("inputs"),
    "prev_error_has_context": "BOOM-error-context"
                             in str(stub.payloads[0].get("prev_error", "")),
}
# 依赖方向：solve_tool.py 的 AST 里不得出现 import agents
st = ast.parse((REPO / "mmagent" / "tools" / "solve_tool.py").read_text(encoding="utf-8"))
imported_mods: list[str] = []
for node in ast.walk(st):
    if isinstance(node, ast.Import):
        imported_mods += [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        imported_mods.append(node.module or "")
a5["solve_tool_imports"] = imported_mods
a5["solve_tool_imports_agents"] = [m for m in imported_mods
                                   if "agents" in m.split(".")]
result["A5_regen"] = a5

# ------------------------------------------------------------------ A1 执行
result["A1_guard_matrix"] = {k: guard_case(v) for k, v in A1_CASES.items()}

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

# ------------------------------------------------------------------ 打印
print("=" * 74)
print("A1 guard 放行矩阵")
for k, v in result["A1_guard_matrix"].items():
    print(f"  [{v['verdict']:5s}] {k}")
    for vio in v["violations"]:
        print(f"          └ {vio}")
print("=" * 74)
print("A2 集合运算")
print(f"  intersection(白∩禁)         = {a2['intersection']}")
print(f"  危险库误入白名单            = {a2['dangerous_top_level_in_allowed']}")
print(f"  白名单({len(a2['ALLOWED'])}) = {a2['ALLOWED']}")
print(f"  禁名单({len(a2['BANNED'])}) = {a2['BANNED']}")
print(f"  banned_not_in_allowed      = {a2['banned_not_in_allowed']}")
print("=" * 74)
print("A3 runner 三态")
for r in a3:
    print(f"  {r['name']:28s} rc={r['rc']} ok={r['ok']} "
          f"manifest_present={r['manifest_present']} missing={r['manifest_missing']}")
print("=" * 74)
print("A4 stage_inputs")
print(f"  cjk staged      = {a4['cjk_staged_return']} exists={a4['cjk_file_exists']}")
print(f"  ascii kept      = {a4['ascii_kept']}")
print(f"  missing raises  = {a4['missing_raises']}")
print("=" * 74)
print("A5 make_regen_fn")
print(f"  prev_code == old_src ? {a5['prev_code_equals_old_src']}")
print(f"  payload keys   = {a5['captured_payload_keys']}")
print(f"  inputs preserved = {a5['inputs_preserved']}")
print(f"  prev_error 含上下文 = {a5['prev_error_has_context']}")
print(f"  solve_tool imports = {a5['solve_tool_imports']}")
print(f"  solve_tool 是否 import agents = {a5['solve_tool_imports_agents']}")
print("=" * 74)
print(f"\n结果 JSON: {OUT}")
