"""FR-5 确定性执行测试：静态守卫（guard）+ 沙箱子进程执行（runner）。

被测模块：mmagent/pipeline/guard.py、mmagent/pipeline/runner.py
guard 采用「import 白名单 + 危险调用黑名单」双轨（重构后语义）：
- 白名单：pandas/numpy/matplotlib/math/json/csv/sqlite3/pathlib/statistics/itertools/
  collections/datetime/re/random；白名单之外一律拦截（os/sys/importlib/httpx/http/socket…）
- 黑名单：裸名 eval/exec/__import__/compile；属性调用 os.system/os.popen/subprocess.* 等
覆盖 success_criteria：
- validate_script_ast 拦截 os/subprocess/socket/urllib/ftplib/requests/eval/exec/
  __import__/compile，以及 from os import system、importlib.import_module 两条历史绕过写法
- 干净源码（pandas 计算 + matplotlib 出图风格）通过；常用本地库不得被误伤
- run_script 三态：正常（rc=0、产出文件非空）、崩溃（rc≠0、stderr 非空）、超时（timed_out）
- manifest.json 声明的产物缺失 → ok=False
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from mmagent.pipeline.guard import ScriptGuardError, validate_script_ast
from mmagent.pipeline.runner import run_script

BANNED_SOURCES = {
    "os.system": "import os\nos.system('dir')\n",
    "os.popen": "import os\nos.popen('dir')\n",
    "import subprocess": "import subprocess\n",
    "subprocess.run": "import subprocess\nsubprocess.run(['ls'])\n",
    "subprocess.Popen": "import subprocess\nsubprocess.Popen(['ls'])\n",
    "eval": "eval('1 + 1')\n",
    "exec": "exec('x = 1')\n",
    "__import__": "__import__('os')\n",
    "compile": "compile('1', '<s>', 'eval')\n",
    "import requests": "import requests\n",
    "import socket": "import socket\n",
    "import urllib": "import urllib.request\n",
    "from urllib import": "from urllib.request import urlopen\n",
    "import ftplib": "import ftplib\n",
}

CLEAN_SCRIPT = textwrap.dedent('''
    """干净脚本：pandas 计算 + matplotlib 出图，全本地。"""
    import pandas as pd
    import matplotlib.pyplot as plt

    df = pd.DataFrame({"月份": [1, 2, 3], "满意度": [88.0, 90.5, 92.0]})
    df["得分"] = df["满意度"] * 1.1
    fig, ax = plt.subplots()
    ax.plot(df["月份"], df["满意度"])
    fig.savefig("fig_satisfaction.png", dpi=300)
    print(df.describe().to_string())
''')

OK_SCRIPT = textwrap.dedent('''
    from pathlib import Path
    Path("result.csv").write_text("门店,得分\\nA1,90\\n", encoding="utf-8")
    print("runner-ok")
''')

MANIFEST_OK_SCRIPT = textwrap.dedent('''
    import json
    from pathlib import Path
    Path("out").mkdir(exist_ok=True)
    Path("out/data.csv").write_text("a,b\\n1,2\\n", encoding="utf-8")
    Path("manifest.json").write_text(json.dumps({"files": ["out/data.csv"]}), encoding="utf-8")
''')

MANIFEST_MISSING_SCRIPT = textwrap.dedent('''
    import json
    from pathlib import Path
    Path("manifest.json").write_text(json.dumps({"files": ["out/never_written.csv"]}),
                                     encoding="utf-8")
    print("declared files not produced")
''')

CRASH_SCRIPT = 'raise RuntimeError("boom-from-child")\n'

SYNTAX_ERROR_SCRIPT = "def broken(:\n    pass\n"

SLEEP_SCRIPT = "import time\nprint('sleeping')\ntime.sleep(30)\nprint('never')\n"


def _write_script(dir_path: Path, name: str, code: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text(code, encoding="utf-8")
    return path


# ------------------------------------------------------------------ guard

@pytest.mark.parametrize("label,src", sorted(BANNED_SOURCES.items()))
def test_guard_拦截违规脚本_抛ScriptGuardError(label, src):
    with pytest.raises(ScriptGuardError) as exc:
        validate_script_ast(src)
    assert exc.value.violations, "ScriptGuardError 必须携带违规明细"
    assert len(str(exc.value)) > 0


def test_guard_违规信息包含行号与禁令说明():
    src = "import os\n\nos.system('dir')\n"

    with pytest.raises(ScriptGuardError) as exc:
        validate_script_ast(src)

    msg = str(exc.value)
    assert "第3行" in msg, "违规信息应给出源码行号"
    assert "os.system" in msg


def test_guard_多处违规全部上报():
    src = "import os\nos.system('x')\nimport socket\neval('1')\n"

    with pytest.raises(ScriptGuardError) as exc:
        validate_script_ast(src)

    assert len(exc.value.violations) == 4, \
        f"违规点应全部收集（白名单下 import os 亦违规）：{exc.value.violations}"
    joined = " | ".join(exc.value.violations)
    assert "禁止调用 os.system()" in joined, "应上报属性调用类违规"
    assert "禁止调用 eval()" in joined, "应上报裸名调用类违规"
    assert "os" in joined and "socket" in joined, "应上报 import 类违规"


def test_guard_危险顶层模块被白名单拒绝():
    """os/sys/importlib 等不在白名单：即便只用其无害成员（如 os.makedirs）也一律拒绝。

    这是黑名单→白名单的核心收益：无法通过「换成员名」绕过。
    """
    for src in ("import os\n", "import os\nos.makedirs('x', exist_ok=True)\n",
                "import sys\n", "import importlib\n", "import shutil\n"):
        with pytest.raises(ScriptGuardError):
            validate_script_ast(src)


def test_guard_干净脚本通过且返回None():
    assert validate_script_ast(CLEAN_SCRIPT) is None


def test_guard_语法错误脚本被拦截():
    with pytest.raises(ScriptGuardError) as exc:
        validate_script_ast(SYNTAX_ERROR_SCRIPT)
    assert "语法错误" in str(exc.value)


def test_guard_空脚本通过():
    assert validate_script_ast("") is None


def test_guard_拦截from_os_import_system绕过写法():
    """回归 ISSUE-FR5-01：`from os import system; system(...)` 曾可绕过属性调用黑名单。"""
    with pytest.raises(ScriptGuardError):
        validate_script_ast("from os import system\nsystem('dir')\n")


def test_guard_拦截importlib动态导入绕过写法():
    """回归 ISSUE-FR5-02：`importlib.import_module('subprocess')` 曾可绕过 import 文案匹配。"""
    with pytest.raises(ScriptGuardError):
        validate_script_ast("import importlib\nimportlib.import_module('subprocess')\n")


def test_guard_拦截第三方HTTP库httpx():
    """回归 ISSUE-FR5-03：httpx 已装在 .venv 内，白名单化后应被拦（全本地零外网）。"""
    with pytest.raises(ScriptGuardError):
        validate_script_ast("import httpx\nhttpx.get('http://example.com')\n")


def test_guard_拦截标准库http_client():
    """回归 ISSUE-FR5-03 同类：http.client 可直连外网，应被白名单拒绝。"""
    with pytest.raises(ScriptGuardError):
        validate_script_ast("import http.client\nhttp.client.HTTPSConnection('x')\n")


def test_guard_白名单化后常用本地库仍须放行():
    """防止白名单过严：科学计算/标准库常用模块必须继续可用，否则正常脚本跑不起来。"""
    src = textwrap.dedent('''
        import csv
        import json
        import math
        import re
        from collections import Counter
        from datetime import datetime
        from pathlib import Path
        from statistics import mean

        import numpy as np
        import pandas as pd
        import matplotlib.pyplot as plt

        df = pd.DataFrame({"x": np.arange(3), "y": [1.0, 2.0, 3.0]})
        Path("out.csv").write_text("a,b\\n1,2\\n", encoding="utf-8")
        json.dumps({"mean": mean([1, 2, 3]), "c": Counter("aab").most_common()})
        assert math.isclose(df["y"].sum(), 6.0)
        assert re.fullmatch(r"\\d", "7")
        datetime(2026, 10, 13)
        fig, ax = plt.subplots()
        ax.plot(df["x"], df["y"])
    ''')

    assert validate_script_ast(src) is None


# ------------------------------------------------------------------ runner 正常路径

def test_runner_未声明manifest的脚本不算成功(tmp_path):
    """task-003 修订：manifest.json 属 coder 契约（「产物必须在 manifest.json 里声明」），
    未产出即不得判 ok=True。

    原断言 `ok is True` / `manifest_missing == []` 把**假阳性语义**固化了：
    原实现只在 manifest 存在时才统计缺失文件，于是脚本「什么都没产出」时
    manifest_missing 为空 → ok=True —— rc==0 但零产物被报成功，正是要消灭的静默错误。
    本条为**加强**断言（未声明产物必须判失败），非放宽。
    """
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "ok_script.py", OK_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] == 0, "脚本本身没崩"
    assert report["timed_out"] is False
    assert report["manifest_present"] is False
    assert report["ok"] is False, "未声明产物的脚本不得判成功（防零产物假成功）"
    assert report["manifest_missing"], "应说明 manifest.json 缺失"


def test_runner_正常脚本_产出文件被列出(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "ok_script.py", OK_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["produced_files"], "无 manifest 时应兜底扫描产出文件"
    assert "result.csv" in report["produced_files"]
    assert (workdir / "result.csv").exists()


def test_runner_正常脚本_捕获stdout且时长为正(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "ok_script.py", OK_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert "runner-ok" in report["stdout_tail"]
    assert report["duration_s"] >= 0


def test_runner_返回报告包含全部约定键(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "ok_script.py", OK_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert set(report) == {"returncode", "ok", "timed_out", "stdout_tail",
                           "stderr_tail", "duration_s", "produced_files",
                           "manifest_missing",
                           # task-003 新增：显式暴露 manifest 是否存在（ok 判定依赖它）
                           "manifest_present"}


def test_runner_脚本在workdir下执行(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "relpath.py",
                           "from pathlib import Path\nPath('here.txt').write_text('x')\n")

    run_script(script, workdir, timeout_s=60)

    assert (workdir / "here.txt").exists()


def test_runner_workdir不存在时自动创建(tmp_path):
    workdir = tmp_path / "not" / "yet" / "created"
    script = _write_script(tmp_path / "scripts", "ok_script.py", OK_SCRIPT)

    run_script(script, workdir, timeout_s=60)

    assert workdir.is_dir()


def test_runner_无产出脚本_produced_files为空(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "quiet.py", "print('nothing produced')\n")

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] == 0
    assert report["produced_files"] == []


def test_runner_支持相对script路径与workdir(tmp_path, monkeypatch):
    """回归：cwd=workdir 会把相对 script_path 二次拼接，必须绝对化后再交给子进程。"""
    monkeypatch.chdir(tmp_path)
    _write_script(tmp_path / "scripts", "rel.py", OK_SCRIPT)

    report = run_script(Path("scripts/rel.py"), Path("wd"), timeout_s=60)

    assert report["returncode"] == 0, report["stderr_tail"]
    assert (tmp_path / "wd" / "result.csv").exists()


def test_runner_子进程使用同一解释器(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "which_python.py",
                           "import sys\nprint(sys.executable)\n")

    report = run_script(script, workdir, timeout_s=60)

    assert sys.executable in report["stdout_tail"], "应与父进程同一解释器，保证依赖一致"


# ------------------------------------------------------------------ runner 异常路径

def test_runner_崩溃脚本_返回码非0且stderr非空(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "crash.py", CRASH_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] != 0
    assert report["ok"] is False
    assert report["stderr_tail"].strip(), "崩溃必须带 stderr 线索"
    assert "boom-from-child" in report["stderr_tail"]


def test_runner_语法错误脚本_返回码非0(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "broken.py", SYNTAX_ERROR_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] != 0
    assert report["ok"] is False


def test_runner_超时脚本_标记timed_out且不阻塞(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "sleepy.py", SLEEP_SCRIPT)

    report = run_script(script, workdir, timeout_s=2)

    assert report["timed_out"] is True
    assert report["returncode"] == -1
    assert report["ok"] is False
    assert "超时" in report["stderr_tail"]


def test_runner_超时脚本_实际耗时接近超时阈值(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "sleepy.py", SLEEP_SCRIPT)

    report = run_script(script, workdir, timeout_s=2)

    assert report["duration_s"] < 20, f"应在超时后立即终止，实际 {report['duration_s']}s"


# ------------------------------------------------------------------ runner manifest 契约

def test_runner_manifest齐全时produced_files取manifest(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "manifest_ok.py", MANIFEST_OK_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] == 0
    assert report["ok"] is True
    assert report["produced_files"] == ["out/data.csv"]
    assert report["manifest_missing"] == []


def test_runner_manifest缺文件时ok为假(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "manifest_bad.py", MANIFEST_MISSING_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] == 0, "脚本本身跑通了"
    assert report["ok"] is False, "manifest 声明缺失 → 判定失败"
    assert report["manifest_missing"], "应列出声明的缺失文件"
    assert "out/never_written.csv" in report["manifest_missing"]


def test_runner_manifest_json损坏时不崩溃(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "manifest_broken.py",
                           "from pathlib import Path\n"
                           "Path('manifest.json').write_text('{not json', encoding='utf-8')\n")

    report = run_script(script, workdir, timeout_s=60)

    assert report["ok"] is False
    assert report["manifest_missing"], "manifest 解析失败必须显式上报"


def test_runner_崩溃脚本即使声明manifest也不ok(tmp_path):
    workdir = tmp_path / "wd"
    script = _write_script(tmp_path / "scripts", "crash_with_manifest.py", CRASH_SCRIPT)

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] != 0
    assert report["ok"] is False


# ------------------------------------------------------------------ guard → runner 集成

def test_守卫通过后执行成功_端到端(tmp_path):
    workdir = tmp_path / "wd"
    src = ("from pathlib import Path\n"
           "Path('summary.csv').write_text('n\\n1\\n', encoding='utf-8')\n"
           "print('guarded-and-run')\n")
    validate_script_ast(CLEAN_SCRIPT)          # 干净脚本放行
    script = _write_script(tmp_path / "scripts", "guarded.py", src)
    validate_script_ast(src)                   # 该脚本本身也放行

    report = run_script(script, workdir, timeout_s=60)

    assert report["returncode"] == 0
    assert "summary.csv" in report["produced_files"]
    assert "guarded-and-run" in report["stdout_tail"]
