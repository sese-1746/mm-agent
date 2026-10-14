"""队长独立探针（二）：把 prompt 塞满 ctx(4096) 后复现线上「Unterminated string」。

假设：coder 的 num_predict=8192 > Ollama 模型 context_length=4096；
修复轮 prompt（长 prev_code + 满额错误上下文）挤压生成空间 →
done_reason="length"、输出极短且在 code 字段中途被截断 → json 报 Unterminated string。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from jinja2 import Template

REPO = Path(r"H:\mm-agent")
OUT = REPO / ".agent-runs" / "2026-10-14" / "task-003" / "probe" / "lead_b"

# 约 5k 字符的「上一版完整源码」，模拟首轮生成的长脚本被整段回传
_BODY = "\n".join(
    f'    col_{i} = [sh.cell_value(r, {i}) for r in range(2, sh.nrows)]  # 读取第 {i} 列'
    for i in range(1, 160)
)
PREV_CODE = (
    '"""上一版求解脚本（修复轮回传的完整源码，长版本）。"""\n'
    "import json\nimport xlrd\nimport matplotlib\nmatplotlib.use(\"Agg\")\n"
    "import matplotlib.pyplot as plt\nfrom pathlib import Path\n\n"
    'wb = xlrd.open_workbook("data/att1.xls")\nsh = wb.sheet_by_index(0)\n'
    "nrows, ncols = sh.nrows, sh.ncols\n"
    'header1 = [sh.cell_value(1, c) for c in range(ncols)]\n'
    "series = {}\n" + _BODY + "\n"
    "fig, ax = plt.subplots(figsize=(10, 6))\n"
    "ax.set_title(\"各门店月度满意度\")\n"
    'fig.savefig("figures/satisfaction_line.png", dpi=300)\n'
    'Path("manifest.json").write_text(json.dumps({"files": ["figures/satisfaction_line.png"]}), encoding="utf-8")\n'
)
# 满额错误上下文（repair.MAX_STDERR_CHARS = 2000）
ERR = ("[执行 stderr 尾部]\n" + (
    "Traceback (most recent call last):\n  File \"solve.py\", line 46, in <module>\n"
    "    if '满意度' in h or 'score' in h_str:\nTypeError: argument of type 'float' is not iterable\n"
) * 20)[:2000]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tpl = (REPO / "mmagent" / "agents" / "prompts" / "coder.system.j2").read_text(encoding="utf-8")
    system = Template(tpl).render(role="coder")
    spec = {
        "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
        "inputs": ["data/att1.xls"],
        "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
        "data_schema": {"table_id": "att1_satisfaction", "header_row": 1, "data_start_row": 2,
                        "columns": [{"name": "月份门店", "col": 0}]},
        "prev_code": PREV_CODE,
        "prev_error": ("上一次生成的脚本执行失败或未通过静态守卫。请**基于 prev_code 修正**"
                       "（只改必要之处），并在 code 字段给出修正后的**完整可运行源码**：\n" + ERR),
    }
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(spec, ensure_ascii=False)}]
    print(f"system={len(system)}ch  user={len(messages[1]['content'])}ch"
          f"  prev_code={len(PREV_CODE)}ch", flush=True)

    payload = {"model": "qwen3.5:latest", "messages": messages, "stream": False,
               "options": {"temperature": 0.1, "num_predict": 8192},
               "format": "json", "think": False}
    with httpx.Client(base_url="http://localhost:11434", timeout=600) as cli:
        resp = cli.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    raw = (data.get("message") or {}).get("content") or ""
    rec = {"case": "long_prompt_ctx_saturation",
           "done_reason": data.get("done_reason"), "eval_count": data.get("eval_count"),
           "prompt_eval_count": data.get("prompt_eval_count"),
           "content_len": len(raw), "ends_with_brace": raw.rstrip().endswith("}"),
           "content_head120": raw[:120], "content_tail120": raw[-120:]}
    print("done_reason      :", rec["done_reason"], flush=True)
    print("prompt_eval_count:", rec["prompt_eval_count"], flush=True)
    print("eval_count       :", rec["eval_count"], flush=True)
    print("content_len      :", rec["content_len"], flush=True)
    print("ends_with_brace  :", rec["ends_with_brace"], flush=True)
    print("tail120          :", repr(rec["content_tail120"]), flush=True)
    for strict in (True, False):
        try:
            json.loads(raw, strict=strict); v = "OK"
        except Exception as exc:  # noqa: BLE001
            v = f"{type(exc).__name__}: {exc}"
        rec[f"json_loads_strict_{strict}"] = v
        print(f"json.loads(strict={strict!s:5s}) -> {v}", flush=True)

    (OUT / "lead_raw_probe_long.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved:", OUT / "lead_raw_probe_long.json", flush=True)


if __name__ == "__main__":
    sys.exit(main())
