"""验证员 Vic — Part B2：多次采样，判定 "Unterminated string" 是否由 num_predict 截断引起。

B2a: 与 B1 相同的 /api/chat 载荷，采样 N 次，逐次记录 done_reason/eval_count/内容长度/是否闭合/解析结果。
B2b: 走真实 `make_regen_fn`（RoleRunner 链路，含三档降级），尝试复现 LLMCallError，
     并利用 base.py 新增的「原始输出片段」定位失败归因（主模型 or 0.8b 降级模型）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(r"H:\mm-agent")
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402
from jinja2 import Template  # noqa: E402

from mmagent.agents.coder import make_regen_fn, make_runner  # noqa: E402
from mmagent.agents.base import LLMCallError  # noqa: E402
from mmagent.tools import solve_tool  # noqa: E402

HERE = Path(__file__).parent
OUT = HERE / "verify_b2_result.json"
SCHEMA_REPORT = REPO / "artifacts" / "intake" / "schema_report.json"
BASE_URL = "http://localhost:11434"
MODELS = {"generation": "qwen3.5:latest", "fallback": "qwen3.5:0.8b"}
N_SAMPLE = 3

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


def render_system() -> str:
    src = (REPO / "mmagent/agents/prompts/coder.system.j2").read_text(encoding="utf-8")
    return Template(src).render(role="coder")


def build_payload(num_predict: int, model: str) -> dict:
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
        "model": model, "messages": [
            {"role": "system", "content": render_system()},
            {"role": "user", "content": json.dumps(spec, ensure_ascii=False)}],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict},
        "format": "json", "think": False,
    }


def one_call(num_predict: int, model: str) -> dict:
    rec: dict = {"model": model, "num_predict": num_predict}
    try:
        with httpx.Client(timeout=300.0) as cli:
            r = cli.post(f"{BASE_URL}/api/chat", json=build_payload(num_predict, model))
        d = r.json()
        content = (d.get("message", {}) or {}).get("content") or ""
        rec.update({"done_reason": d.get("done_reason"), "eval_count": d.get("eval_count"),
                    "content_chars": len(content),
                    "endswith_brace": content.rstrip().endswith("}")})
        try:
            obj = json.loads(content, strict=False)
            rec["json_parse"] = "OK"
            rec["code_chars"] = len(str(obj.get("code", "")))
        except Exception as exc:  # noqa: BLE001
            rec["json_parse"] = f"{type(exc).__name__}: {exc}"
            rec["content_head_200"] = content[:200]
            rec["content_tail_200"] = content[-200:]
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def real_regen_once() -> dict:
    spec = {
        "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
        "inputs": ["data/att1.xls"],
        "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
        "data_schema": solve_tool.table_schema_digest(SCHEMA_REPORT, "att1_satisfaction"),
    }
    runner = make_runner()
    regen = make_regen_fn(runner=runner, base_spec=spec)
    rec: dict = {}
    try:
        out = regen(PREV_CODE, "FileNotFoundError: 'data/附件 1.xls'")
        rec.update({"ok": True, "out_chars": len(out)})
    except LLMCallError as exc:
        msg = str(exc)
        rec.update({"ok": False, "exc_type": "LLMCallError", "message": msg[-800:]})
    except Exception as exc:  # noqa: BLE001
        rec.update({"ok": False, "exc_type": type(exc).__name__, "message": str(exc)[-800:]})
    return rec


def main() -> None:
    out: dict = {"B2a_samples": [], "B2c_fallback_samples": [], "B2b_real_regen": []}
    for i in range(N_SAMPLE):
        print(f"--- B2a sample {i} (main={MODELS['generation']}, num_predict=8192) ---",
              flush=True)
        rec = one_call(8192, MODELS["generation"])
        out["B2a_samples"].append(rec)
        print(f"    done_reason={rec.get('done_reason')} eval={rec.get('eval_count')} "
              f"chars={rec.get('content_chars')} parse={rec.get('json_parse')}", flush=True)
    # 关键假设：RoleRunner 三档链的**最后一档是 0.8b 降级模型**，失败信息取最后一档。
    # 单独采样 0.8b，看它是否才是坏 JSON 的来源。
    for i in range(2):
        print(f"--- B2c sample {i} (fallback={MODELS['fallback']}, num_predict=8192) ---",
              flush=True)
        rec = one_call(8192, MODELS["fallback"])
        out["B2c_fallback_samples"].append(rec)
        print(f"    done_reason={rec.get('done_reason')} eval={rec.get('eval_count')} "
              f"chars={rec.get('content_chars')} parse={rec.get('json_parse')}", flush=True)
    truncated = [s for s in out["B2a_samples"] + out["B2c_fallback_samples"]
                 if s.get("done_reason") == "length"
                 or "Unterminated" in str(s.get("json_parse"))]
    out["truncated_count"] = len(truncated)
    if truncated:
        print("--- 对照：num_predict=16384 (main) ---", flush=True)
        out["control_16384"] = one_call(16384, MODELS["generation"])
        print(f"    {out['control_16384']}", flush=True)
    for i in range(2):
        print(f"--- B2b real regen via RoleRunner #{i} ---", flush=True)
        rec = real_regen_once()
        out["B2b_real_regen"].append(rec)
        print(f"    ok={rec.get('ok')} {rec.get('exc_type', '')}", flush=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
