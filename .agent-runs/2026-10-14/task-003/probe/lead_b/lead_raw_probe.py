"""队长独立探针：抓一次真实 coder raw，判定「ctx 截断」与「strict=False 是否对症」。

不需应用层：直接 httpx 打 /api/chat，用真实 coder system prompt + 含长 prev_code 的
regen 场景 user，num_predict=8192（= coder max_tokens）。记录 done_reason 等原始字段。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from jinja2 import Template

REPO = Path(r"H:\mm-agent")
OUT = REPO / ".agent-runs" / "2026-10-14" / "task-003" / "probe" / "lead_b"

PREV_CODE = '''"""上一版求解脚本（修复轮回传的完整源码）。"""
import json
import xlrd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

wb = xlrd.open_workbook("data/att1.xls")
sh = wb.sheet_by_index(0)
nrows, ncols = sh.nrows, sh.ncols
print("dims", nrows, ncols)
header0 = [sh.cell_value(0, c) for c in range(ncols)]
header1 = [sh.cell_value(1, c) for c in range(ncols)]
print("h0", header0)
print("h1", header1)
labels = [sh.cell_value(r, 0) for r in range(2, nrows)]
series = {}
for c in range(1, ncols):
    key = sh.cell_value(1, c)
    series[str(key)] = [sh.cell_value(r, c) for r in range(2, nrows)]
fig, ax = plt.subplots(figsize=(10, 6))
for k, v in series.items():
    ax.plot(range(len(v)), v, marker="o", label=k)
ax.set_title("各门店月度满意度")
ax.set_xlabel("月份")
ax.set_ylabel("满意度")
ax.legend()
fig.savefig("figures/satisfaction_line.png", dpi=300)
Path("manifest.json").write_text(json.dumps({"files": ["figures/satisfaction_line.png"]}), encoding="utf-8")
'''


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tpl = (REPO / "mmagent" / "agents" / "prompts" / "coder.system.j2").read_text(encoding="utf-8")
    system = Template(tpl).render(role="coder")
    spec = {
        "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
        "inputs": ["data/att1.xls"],
        "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
        "data_schema": {"table_id": "att1_satisfaction", "source_file": "att1.xls",
                        "header_row": 1, "data_start_row": 2,
                        "columns": [{"name": "月份门店", "col": 0}, {"name": "1.0", "col": 1}]},
        "prev_code": PREV_CODE,
        "prev_error": ("上一次生成的脚本执行失败或未通过静态守卫。请**基于 prev_code 修正**"
                       "（只改必要之处），并在 code 字段给出修正后的**完整可运行源码**：\n"
                       "[执行 stderr 尾部]\nTypeError: argument of type 'float' is not iterable"),
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(spec, ensure_ascii=False)},
    ]
    print(f"system 字符数={len(system)}  user 字符数={len(messages[1]['content'])}", flush=True)

    payload = {
        "model": "qwen3.5:latest",
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 8192},
        "format": "json",
        "think": False,
    }
    with httpx.Client(base_url="http://localhost:11434", timeout=600) as cli:
        resp = cli.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    raw = (data.get("message") or {}).get("content") or ""
    raw_thinking = (data.get("message") or {}).get("thinking") or ""
    rec = {
        "done_reason": data.get("done_reason"),
        "eval_count": data.get("eval_count"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "total_duration_ns": data.get("total_duration"),
        "content_len": len(raw),
        "thinking_len": len(raw_thinking),
        "content_tail80": raw[-80:],
        "content_head80": raw[:80],
        "ends_with_brace": raw.rstrip().endswith("}"),
    }
    print("done_reason      :", rec["done_reason"], flush=True)
    print("eval_count       :", rec["eval_count"], flush=True)
    print("prompt_eval_count:", rec["prompt_eval_count"], flush=True)
    print("content_len      :", rec["content_len"], flush=True)
    print("thinking_len     :", rec["thinking_len"], flush=True)
    print("ends_with_brace  :", rec["ends_with_brace"], flush=True)
    print("content_tail80   :", repr(rec["content_tail80"]), flush=True)

    for strict in (True, False):
        try:
            json.loads(raw, strict=strict)
            verdict = "OK"
        except Exception as exc:  # noqa: BLE001
            verdict = f"{type(exc).__name__}: {exc}"
        rec[f"json_loads_strict_{strict}"] = verdict
        print(f"json.loads(strict={strict!s:5s}) -> {verdict}", flush=True)

    (OUT / "lead_raw_probe.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved:", OUT / "lead_raw_probe.json", flush=True)


if __name__ == "__main__":
    sys.exit(main())
