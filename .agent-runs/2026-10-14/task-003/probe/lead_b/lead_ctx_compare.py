"""队长独立探针（三）：对照 `num_ctx`，判定截断假设并验证修法。

观察到：base.py 已改 `json.loads(..., strict=False)` 后，真实端到端仍报
`Unterminated string`（lead_c_result.json）。本实验用**与真实链路同构的 spec**
（含 data_schema），对照两种 options：
  A) 现状：num_predict=8192（Ollama 模型 ctx 实测 4096）
  B) 提高：num_predict=8192 + num_ctx=16384
预期：A 若截断（done_reason=length / 字符串未闭合），B 恢复正常 →
      证明「num_predict 超出 ctx 导致截断」，并给出**可验证的修法**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from jinja2 import Template

REPO = Path(r"H:\mm-agent")
OUT = REPO / ".agent-runs" / "2026-10-14" / "task-003" / "probe" / "lead_b"

BASE_SPEC = {
    "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
    "inputs": ["data/att1.xls"],
    "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
    "notes": [
        "用 xlrd 打开 .xls",
        "列名与表头行以 data_schema 为准（不要按语义猜列名）",
        "matplotlib 用 Agg 后端，中文图题，savefig(dpi=300)",
        '末尾把产出清单写入 manifest.json（内容形如 {"files": ["figures/satisfaction_line.png"]}）',
        "不要用 try/except 吞掉异常后正常退出；失败必须以非 0 退出码结束",
    ],
    "data_schema": {
        "table_id": "att1_satisfaction", "source_file": "att1.xls", "sheet_name": "Sheet1",
        "title": "各门店客户满意度综合得分", "header_row": 1, "data_start_row": 2,
        "columns": [{"name": "月份门店", "raw": "月份门店", "col": 0,
                     "canonical": None, "dtype": "str"}]
        + [{"name": f"{m}.0", "raw": float(m), "col": m, "canonical": None, "dtype": "float"}
           for m in range(1, 13)],
        "note": "列名以 columns[].name 为准；第 0 行通常是合并大标题，不是列名。",
    },
}

SCENARIOS = [
    ("A_现状_num_predict8192_ctx默认", {"temperature": 0.1, "num_predict": 8192}),
    ("B_提高_num_ctx16384", {"temperature": 0.1, "num_predict": 8192, "num_ctx": 16384}),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tpl = (REPO / "mmagent" / "agents" / "prompts" / "coder.system.j2").read_text(encoding="utf-8")
    system = Template(tpl).render(role="coder")
    user = json.dumps(BASE_SPEC, ensure_ascii=False)
    print(f"system={len(system)}ch user={len(user)}ch", flush=True)

    records = []
    with httpx.Client(base_url="http://localhost:11434", timeout=900) as cli:
        for name, options in SCENARIOS:
            payload = {"model": "qwen3.5:latest",
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                       "stream": False, "options": options, "format": "json", "think": False}
            resp = cli.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw = (data.get("message") or {}).get("content") or ""
            rec = {"scenario": name, "options": options,
                   "done_reason": data.get("done_reason"),
                   "prompt_eval_count": data.get("prompt_eval_count"),
                   "eval_count": data.get("eval_count"),
                   "content_len": len(raw),
                   "ends_with_brace": raw.rstrip().endswith("}"),
                   "content_tail100": raw[-100:]}
            for strict in (True, False):
                try:
                    json.loads(raw, strict=strict)
                    v = "OK"
                except Exception as exc:  # noqa: BLE001
                    v = f"{type(exc).__name__}: {exc}"
                rec[f"json_strict_{strict}"] = v
            print(f"[{name}] done_reason={rec['done_reason']} "
                  f"prompt={rec['prompt_eval_count']} eval={rec['eval_count']} "
                  f"len={rec['content_len']} brace={rec['ends_with_brace']}", flush=True)
            print(f"    strict=True -> {rec['json_strict_True']}", flush=True)
            print(f"    strict=False -> {rec['json_strict_False']}", flush=True)
            records.append(rec)

    (OUT / "lead_ctx_compare.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved:", OUT / "lead_ctx_compare.json", flush=True)


if __name__ == "__main__":
    sys.exit(main())
