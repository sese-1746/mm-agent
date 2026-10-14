"""验证员 Vic — Part B：根因最小复现（需要 Ollama）。

B1: 用与 coder 完全一致的 system prompt + 一个含长 prev_code 的 regen 载荷，
    直接打 /api/chat（format=json, num_predict=8192, think=false），
    **拿原始 HTTP 响应**，记录 done_reason / eval_count / content 长度 / 是否闭合 / json 解析错误。
B2: 若 B1 出现截断，把 num_predict 提到 16384 复打，形成对照。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"H:\mm-agent")
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402
from jinja2 import Template  # noqa: E402

from mmagent.tools import solve_tool  # noqa: E402

OUT = Path(__file__).with_name("verify_b_result.json")
SCHEMA_REPORT = REPO / "artifacts" / "intake" / "schema_report.json"
BASE_URL = "http://localhost:11434"

# 一段"上一版完整源码"——模拟修复轮回传的长代码（含 xlrd 读取 + 绘图 + manifest）
PREV_CODE = '''\
import json
import xlrd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

Path("figures").mkdir(parents=True, exist_ok=True)
wb = xlrd.open_workbook("data/att1.xls")
sh = wb.sheet_by_index(0)
header_row = 1
names = [str(sh.cell_value(header_row, c)) for c in range(sh.ncols)]
rows = []
for r in range(header_row + 1, sh.nrows):
    rows.append([sh.cell_value(r, c) for c in range(sh.ncols)])
plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False
fig, ax = plt.subplots(figsize=(10, 6))
for idx in range(1, min(len(rows), 8)):
    series = [float(v) if isinstance(v, float) else 0.0 for v in rows[idx][1:13]]
    ax.plot(range(1, len(series) + 1), series, marker="o", label=str(rows[idx][0]))
ax.set_xlabel("月份")
ax.set_ylabel("满意度综合得分")
ax.set_title("各门店月度客户满意度综合得分")
ax.legend()
fig.savefig("figures/satisfaction_line.png", dpi=300)
Path("manifest.json").write_text(
    json.dumps({"files": ["figures/satisfaction_line.png"]}, ensure_ascii=False),
    encoding="utf-8")
print("done")
'''


def render_system(role: str = "coder", ctx: dict | None = None) -> str:
    src = (REPO / "mmagent" / "agents" / "prompts" / f"{role}.system.j2").read_text(
        encoding="utf-8")
    return Template(src).render(role=role, **(ctx or {}))


def build_payload(num_predict: int) -> dict:
    spec = {
        "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
        "inputs": ["data/att1.xls"],
        "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
        "data_schema": solve_tool.table_schema_digest(SCHEMA_REPORT, "att1_satisfaction"),
        "prev_code": PREV_CODE,
        "prev_error": (
            "上一次生成的脚本执行失败或未通过静态守卫。请**基于 prev_code 修正**"
            "（只改必要之处），并在 code 字段给出修正后的**完整可运行源码**：\n"
            "[执行 stderr 尾部]\nTraceback (most recent call last):\n"
            '  File "solve.py", line 13, in <module>\n'
            '    wb = xlrd.open_workbook("data/附件 1.xls")\n'
            "FileNotFoundError: [Errno 2] No such file or directory: 'data/附件 1.xls'"),
    }
    return {
        "model": "qwen3.5:latest",
        "messages": [
            {"role": "system", "content": render_system()},
            {"role": "user", "content": json.dumps(spec, ensure_ascii=False)},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict},
        "format": "json",
        "think": False,
    }


def probe(num_predict: int, label: str) -> dict:
    payload = build_payload(num_predict)
    rec: dict = {"label": label, "num_predict": num_predict,
                 "system_chars": len(payload["messages"][0]["content"]),
                 "user_chars": len(payload["messages"][1]["content"])}
    try:
        with httpx.Client(timeout=300.0) as cli:
            resp = cli.post(f"{BASE_URL}/api/chat", json=payload)
        rec["http_status"] = resp.status_code
        data = resp.json()
        rec["response_top_keys"] = sorted(data.keys())
        rec["done_reason"] = data.get("done_reason")
        rec["done"] = data.get("done")
        rec["eval_count"] = data.get("eval_count")
        rec["prompt_eval_count"] = data.get("prompt_eval_count")
        msg = data.get("message", {}) or {}
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        rec["content_chars"] = len(content)
        rec["thinking_chars"] = len(thinking)
        rec["content_tail_80"] = content[-80:]
        rec["content_endswith_brace"] = content.rstrip().endswith("}")
        rec["content_head_120"] = content[:120]
        try:
            obj = json.loads(content, strict=False)
            rec["json_parse"] = "OK"
            rec["json_keys"] = sorted(obj.keys()) if isinstance(obj, dict) else None
            rec["json_code_chars"] = len(str(obj.get("code", ""))) if isinstance(obj, dict) else None
        except Exception as exc:  # noqa: BLE001
            rec["json_parse"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def main() -> None:
    out = {"B1_8192": probe(8192, "num_predict=8192"),
           "B2_16384": None}
    b1 = out["B1_8192"]
    # 只要 B1 出现截断迹象就做对照
    if b1.get("done_reason") == "length" or "Unterminated" in str(b1.get("json_parse")):
        out["B2_16384"] = probe(16384, "num_predict=16384")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
