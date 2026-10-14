"""probe_05_interrupt_semantics.py — 实测 interrupt() 的精确语义（Q2 关键）

要回答：
  (a) 挂起后，若再用 graph.invoke(None, config)（不用 Command(resume=)）会怎样？
  (b) interrupt() 是否在 resume 时**返回** Command 里传的值？返回值即上次调用处。
  (c) 节点内在 interrupt() **之前**的代码，在 resume 时会不会重复执行（副作用重复）？
  (d) interrupt() 传入的 payload 从哪里读得到（__interrupt__ / get_state().tasks）。
  (e) 同一节点内多次 interrupt 的行为。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from typing import Annotated  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402
from langgraph.graph import StateGraph, START, END  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import interrupt, Command  # noqa: E402

EFFECTS = []  # 记录节点副作用发生次数


class S(TypedDict):
    log: Annotated[list, add_messages]


def gate_node(state: S):
    EFFECTS.append("副作用：进入 gate_node")          # (c) 观察点
    print(f"    [node] 已进入 gate_node，副作用累计={len(EFFECTS)}")
    payload = {"ask": "确认？", "n": 1}
    val = interrupt(payload)                           # (b)(d)
    print(f"    [node] interrupt() 返回 = {val!r}")
    return {"log": [f"resume={val!r}"]}


def build():
    b = StateGraph(S)
    b.add_node("gate", gate_node)
    b.add_edge(START, "gate")
    b.add_edge("gate", END)
    return b.compile(checkpointer=MemorySaver())


print("=" * 70)
print("(a) 不用 Command(resume=)，直接 invoke(None, config) 会怎样？")
print("=" * 70)
g = build()
cfg = {"configurable": {"thread_id": "t_a"}}
r1 = g.invoke({"log": ["start"]}, config=cfg)
print(f"  首次 invoke -> __interrupt__ 存在={ '__interrupt__' in r1 }")
try:
    r2 = g.invoke(None, config=cfg)
    print(f"  invoke(None) 返回 keys={list(r2.keys())}")
    print(f"  invoke(None) 后 __interrupt__={r2.get('__interrupt__')}")
    snap = g.get_state(cfg)
    print(f"  invoke(None) 后 next={snap.next}  log={snap.values.get('log')}")
    print("  => 观察：不带 resume 值时，未提供人工决策（见下方判定）")
except Exception as exc:  # noqa: BLE001
    print(f"  invoke(None) 抛异常: {type(exc).__name__}: {exc}")

print()
print("=" * 70)
print("(b) Command(resume=X) 是否让 interrupt() 返回 X？")
print("=" * 70)
g = build()
cfg = {"configurable": {"thread_id": "t_b"}, "__effects": None}
g.invoke({"log": ["start"]}, config=cfg)
print("  恢复前副作用次数 =", len(EFFECTS))
r = g.invoke(Command(resume="APPROVED_BY_HUMAN"), config=cfg)
print(f"  恢复后 log = {r.get('log')}")
print("  恢复后副作用次数 =", len(EFFECTS), "（>1 说明 interrupt 之前的代码被重跑）")
snap = g.get_state(cfg)
print(f"  终态 next={snap.next} (空=END)")

print()
print("=" * 70)
print("(d) interrupt payload 的读取位置")
print("=" * 70)
g = build()
cfg = {"configurable": {"thread_id": "t_d"}}
r = g.invoke({"log": ["start"]}, config=cfg)
print(f"  invoke 返回值 __interrupt__ = {r.get('__interrupt__')}")
snap = g.get_state(cfg)
print(f"  get_state().next            = {snap.next}")
for t in snap.tasks:
    print(f"  get_state().tasks[0].interrupts = {t.interrupts}")
    for iv in t.interrupts:
        print(f"    interrupt.value = {iv.value}")

print()
print("=" * 70)
print("(e) 同一节点内两次 interrupt 的恢复顺序")
print("=" * 70)


class S2(TypedDict):
    log: Annotated[list, add_messages]


def two_gates(state: S2):
    a = interrupt({"q": "first"})
    print(f"    [node2] first 返回 {a!r}")
    b = interrupt({"q": "second"})
    print(f"    [node2] second 返回 {b!r}")
    return {"log": [f"a={a!r} b={b!r}"]}


b2 = StateGraph(S2)
b2.add_node("two", two_gates)
b2.add_edge(START, "two")
b2.add_edge("two", END)
g2 = b2.compile(checkpointer=MemorySaver())
cfg = {"configurable": {"thread_id": "t_e"}}
r = g2.invoke({"log": []}, config=cfg)
print(f"  第1次 invoke __interrupt__ = {r.get('__interrupt__')}")
r = g2.invoke(Command(resume="val_1"), config=cfg)
print(f"  resume=val_1 后 __interrupt__ = {r.get('__interrupt__')}")
r = g2.invoke(Command(resume="val_2"), config=cfg)
print(f"  resume=val_2 后 log = {r.get('log')}")
print("\n[probe_05] 完成")
