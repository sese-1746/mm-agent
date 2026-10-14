"""验证员 Vic — Part C2：安静窗口下的复核（ASCII ×3）。

动机：C 轮运行期间有多个并行探针同时打 Ollama，5/6 失败是 LLMCallError(JSON 异常)，
不一定是脚本本身不可运行。本脚本在无并行负载时重跑 ASCII 模式，比较两次结果，
判断失败率是否与并发负载相关。复用 verify_c 的 stage/classify/run_once。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import verify_c  # noqa: E402


def main() -> None:
    records = []
    for i in (10, 11, 12):  # 独立 idx，避免覆盖 C 轮工作区
        rec = verify_c.run_once("ascii", i)
        rec["quiet_window"] = True
        records.append(rec)
        print(f"quiet ascii #{i}: ok={rec.get('ok')} rc={rec.get('rc')} "
              f"kind={rec.get('kind')} elapsed={rec.get('elapsed_s')}s", flush=True)
    out = HERE / "verify_c2_quiet_result.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in records if r.get("ok"))
    print(f"\n安静窗口 ascii: {ok}/{len(records)} ok  kinds={[r.get('kind') for r in records]}")
    print(f"明细: {out}")


if __name__ == "__main__":
    main()
