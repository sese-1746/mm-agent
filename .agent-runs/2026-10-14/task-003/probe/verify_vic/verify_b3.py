"""验证员 Vic — Part B3：用**与真实首轮生成完全一致**的载荷打 raw /api/chat，
取 done_reason / eval_count / 完整输出，判定 "Unterminated string" 的真实成因。

与 B1/B2 的差别：这里 user 载荷 = BASE_SPEC(含 notes) + inputs + data_schema，
**不含 prev_code/prev_error**（即 solve 的首轮 generate_script 输入）。
若失败，再用 num_predict=16384 做对照，直接检验「截断」假设。
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

HERE = Path(__file__).parent
OUT = HERE / "verify_b3_result.json"
SCHEMA_REPORT = REPO / "artifacts" / "intake" / "schema_report.json"

BASE_SPEC = {
    "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
    "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
    "notes": [
        "用 xlrd 打开 .xls",
        "列名与表头行以 data_schema 为准（不要按语义猜列名）",
        "matplotlib 用 Agg 后端，中文图题，savefig(dpi=300)",
        '末尾把产出清单写入 manifest.json（内容形如 {"files": ["figures/satisfaction_line.png"]}）',
        "不要用 try/except 吞掉异常后正常退出；失败必须以非 0 退出码结束",
    ],
}


def render_system() -> str:
    src = (REPO / "mmagent/agents/prompts/coder.system.j2").read_text(encoding="utf-8")
    return Template(src).render(role="coder")


def build_payload(num_predict: int, cjk: bool = True) -> dict:
    spec = dict(BASE_SPEC)
    spec["inputs"] = (["data/附件1 各门店客户满意度综合得分-A题.xls"] if cjk
                      else ["data/att1.xls"])
    spec["data_schema"] = solve_tool.table_schema_digest(SCHEMA_REPORT, "att1_satisfaction")
    return {
        "model": "qwen3.5:latest",
        "messages": [{"role": "system", "content": render_system()},
                     {"role": "user", "content": json.dumps(spec, ensure_ascii=False)}],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict},
        "format": "json", "think": False,
    }


def call(num_predict: int, tag: str) -> dict:
    rec: dict = {"tag": tag, "num_predict": num_predict}
    try:
        with httpx.Client(timeout=400.0) as cli:
            r = cli.post("http://localhost:11434/api/chat", json=build_payload(num_predict))
        d = r.json()
        content = (d.get("message", {}) or {}).get("content") or ""
        rec.update({"done_reason": d.get("done_reason"), "eval_count": d.get("eval_count"),
                    "prompt_eval_count": d.get("prompt_eval_count"),
                    "content_chars": len(content),
                    "endswith_brace": content.rstrip().endswith("}")})
        # 保存完整原始输出，便于检查尾部是否是"半截代码"
        (HERE / f"raw_{tag}.txt").write_text(content, encoding="utf-8")
        try:
            obj = json.loads(content, strict=False)
            rec["json_parse"] = "OK"
            rec["code_chars"] = len(str(obj.get("code", "")))
        except Exception as exc:  # noqa: BLE001
            rec["json_parse"] = f"{type(exc).__name__}: {exc}"
            rec["tail_200"] = content[-200:]
            # 统计 code 字段里出现的"真实换行"数量（粗暴计数）
            rec["raw_newline_in_content"] = content.count("\n")
            rec["escaped_backslash_n"] = content.count("\\n")
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def main() -> None:
    out: dict = {"B3_initial_gen_8192": call(8192, "b3_8192")}
    b3 = out["B3_initial_gen_8192"]
    print(f"8192: done_reason={b3.get('done_reason')} eval={b3.get('eval_count')} "
          f"chars={b3.get('content_chars')} parse={b3.get('json_parse')}", flush=True)
    if b3.get("done_reason") == "length" or "Unterminated" in str(b3.get("json_parse")):
        out["B3_control_16384"] = call(16384, "b3_16384")
        c = out["B3_control_16384"]
        print(f"16384: done_reason={c.get('done_reason')} eval={c.get('eval_count')} "
              f"chars={c.get('content_chars')} parse={c.get('json_parse')}", flush=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
