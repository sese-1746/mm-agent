"""probe_14_normalize_pdf.py — 把 PDF 抽出的文本做 NFKC 归一化

pypdf 抽出的中文里混入了「康熙部首」兼容字符（如 U+2F74 ⽴ 代替 U+7ACB 立），
导致 grep '立' 命中不了 '⽴'。NFKC 归一化会把兼容字符折叠成正常汉字，
使关键词检索可靠（否则「课件有没有讲 X」的结论会假阴性）。
"""
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent / "pdf"

for name in ("hitl.pdf.txt", "graph07.pdf.txt"):
    raw = (HERE / name).read_text(encoding="utf-8")
    norm = unicodedata.normalize("NFKC", raw)
    dest = HERE / name.replace(".pdf.txt", ".norm.txt")
    dest.write_text(norm, encoding="utf-8")
    changed = sum(1 for a, b in zip(raw, norm) if a != b)
    print(f"[ok] {name} -> {dest.name}  (差异字符数={changed})")

# 归一化后再做一次关键词核查
KEYS_HITL = ["response_schema", "编辑", "edit", "update_state", "多轮", "幂等",
             "get_state_history", "回溯", "时间旅行", "工具内", "from_conn_string",
             "FastAPI", "HTTP", "approve", "reject", "独立管理", "不要用",
             "RedisSaver", "SqliteSaver", "thread_id", "检查点"]
KEYS_G7 = ["response_schema", "interrupt", "Command", "checkpoint", "检查点",
           "回溯", "幂等", "from_conn_string", "循环", "降级", "超时", "thread_id",
           "MemorySaver", "人工", "审核"]

for label, keys in (("hitl", KEYS_HITL), ("graph07", KEYS_G7)):
    t = (HERE / f"{label}.norm.txt").read_text(encoding="utf-8")
    print(f"\n===== {label} 关键词（NFKC 归一化后）=====")
    for k in keys:
        print(f"  {k:22s} {t.count(k)} 次")
