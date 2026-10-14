"""probe_02_sqlite_persistence.py — 复现课件 demo11（SqliteSaver 持久化）

差异说明（必须记录）：
  demo11 用 ChatTongyi(model="qwen-plus") 作为 agent 节点，需要外网 + DASHSCOPE_API_KEY，
  项目约束「零外网」，故本探针把 agent_node 换成**确定性 stub**（仍返回带 tool_calls 的
  AIMessage），其余结构（State / tools / 路由 / interrupt / SqliteSaver / thread_id /
  场景1~4 流程）与 demo11 **逐行对齐**。interrupt()/Command(resume=)/SqliteSaver 的用法不变。
"""
import os
import sqlite3
import sys
import tempfile
from typing import Annotated

from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import ToolMessage, AIMessage, HumanMessage
from langchain_core.tools import tool

DB = os.path.join(tempfile.gettempdir(), "vera_probe11_checkpoints.sqlite")
if os.path.exists(DB):
    os.remove(DB)
print(f"[probe] checkpoint db = {DB}")

# ===================== 选择存储方式（demo11 第 22-34 行）=====================
conn = sqlite3.connect(DB, check_same_thread=False)
memory = SqliteSaver(conn)
print(f"OK 使用存储：{type(memory).__name__}")


# ===================== 1. State（demo11 第 38-39 行）=====================
class State(TypedDict):
    messages: Annotated[list, add_messages]


# ===================== 2. 工具（demo11 第 43-55 行）=====================
@tool
def delete_database(db_name: str) -> str:
    """危险操作：永久删除指定数据库"""
    return f"数据库 `{db_name}` 已成功永久删除"


@tool
def get_weather(city: str) -> str:
    """查询城市实时天气"""
    return f"{city} 今天天气晴朗，气温为25°C。"


tools = [delete_database, get_weather]
tools_map = {t.name: t for t in tools}


# ===================== 3'. 模型替换为 stub（demo11 第 58-70 行原为 ChatTongyi）=====
def agent_node(state: State):
    """确定性替身：根据最后一条 human 消息产出带 tool_calls 的 AIMessage。"""
    last_human = None
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            last_human = m
            break
    text = last_human.content if last_human else ""
    if "删除" in text:
        # 抽 db 名：'删除数据库 users' -> users
        db = text.split()[-1]
        ai = AIMessage(
            content="",
            tool_calls=[{"name": "delete_database", "args": {"db_name": db},
                         "id": "call_del_1", "type": "tool_call"}],
        )
    else:
        city = text.replace("今天", "").replace("天气怎么样", "").strip() or "西安"
        ai = AIMessage(
            content="",
            tool_calls=[{"name": "get_weather", "args": {"city": city},
                         "id": "call_w_1", "type": "tool_call"}],
        )
    return {"messages": [ai]}


# ===================== 4. 节点（demo11 第 79-135 行）=====================
def tool_node(state: State):
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        return {"messages": []}
    results = []
    for tool_call in last_message.tool_calls:
        if tool_call["name"] == "delete_database":
            continue
        result = tools_map[tool_call["name"]].invoke(tool_call["args"])
        results.append(ToolMessage(content=result, tool_call_id=tool_call["id"]))
    return {"messages": results}


def sensitive_tool_node(state: State):
    last_message = state["messages"][-1]
    if isinstance(last_message, ToolMessage):
        return {"messages": []}
    delete_call = None
    for tc in last_message.tool_calls:
        if tc["name"] == "delete_database":
            delete_call = tc
            break
    if not delete_call:
        return {"messages": []}
    db_name = delete_call["args"]["db_name"]
    print("\n" + "=" * 50)
    print("系统已暂停")
    print(f"   操作：删除数据库 '{db_name}'")
    print("=" * 50)
    human_decision = interrupt({
        "action": "delete_database",
        "target": db_name,
        "warning": "此操作不可恢复！",
    })
    print(f"[probe] interrupt() 在 resume 时返回：{human_decision!r} (type={type(human_decision).__name__})")
    if human_decision == "approve":
        result = tools_map["delete_database"].invoke(delete_call["args"])
        content = f"OK {result}（已人工确认）"
    else:
        content = "操作已取消（人工拒绝）"
    return {"messages": [ToolMessage(content=content, tool_call_id=delete_call["id"])]}


# ===================== 5. 条件路由（demo11 第 139-148 行）=====================
def should_continue(state: State):
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        return END
    for tc in last_message.tool_calls:
        if tc["name"] == "delete_database":
            return "sensitive_tool"
    return "tools"


# ===================== 6. 构图（demo11 第 152-166 行）=====================
builder = StateGraph(State)
builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)
builder.add_node("sensitive_tool", sensitive_tool_node)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue,
                              {"tools": "tools", "sensitive_tool": "sensitive_tool", END: END})
builder.add_edge("tools", "agent")
builder.add_edge("sensitive_tool", "agent")
graph = builder.compile(checkpointer=memory)
# 工具执行完回到 agent 后，stub 会再次产出 tool_call，形成循环。
# 与 demo11 不同（那里 ChatTongyi 会在拿到工具结果后给文本答案）。
# 为保持探针可终止，把回边改为回到 END。
builder2 = StateGraph(State)
builder2.add_node("agent", agent_node)
builder2.add_node("tools", tool_node)
builder2.add_node("sensitive_tool", sensitive_tool_node)
builder2.add_edge(START, "agent")
builder2.add_conditional_edges("agent", should_continue,
                               {"tools": "tools", "sensitive_tool": "sensitive_tool", END: END})
builder2.add_edge("tools", END)
builder2.add_edge("sensitive_tool", END)
graph = builder2.compile(checkpointer=memory)
print("[probe] graph compiled with SqliteSaver")

print("=" * 60)
print("人机回环 + 持久化存储演示（probe 复现 demo11）")
print("=" * 60)

# ---------- 场景1 ----------
print("\n【场景1】普通查询")
config1 = {"configurable": {"thread_id": "user_001"}}
result1 = graph.invoke({"messages": [("human", "西安 天气怎么样")]}, config=config1)
print(f"最终答案：{result1['messages'][-1].content}")

# ---------- 场景2：暂停 ----------
print("\n【场景2】敏感操作 - 暂停")
config2 = {"configurable": {"thread_id": "user_002"}}
r2 = graph.invoke({"messages": [("human", "删除数据库 users")]}, config=config2)
print("图已暂停，等待人工确认...")
print("  返回 dict keys :", list(r2.keys()))
print("  __interrupt__  :", r2.get("__interrupt__"))

# ---------- 场景3：恢复（demo11 用 input()，这里程序化给 approve）----------
print("\n【场景3】从持久化存储恢复")
graph3 = builder2.compile(checkpointer=memory)  # 模拟重启后重新 compile
config2 = {"configurable": {"thread_id": "user_002"}}
r2_resume = graph3.invoke(Command(resume="approve"), config=config2)
print(f"恢复后答案：{r2_resume['messages'][-1].content}")

# ---------- 场景4：查看持久化状态（demo11 第 208-222 行）----------
print("\n【场景4】查看持久化状态")
for thread_id in ["user_001", "user_002"]:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = memory.get(config)
    print(f"\n会话 {thread_id}：  snapshot type = {type(snapshot).__name__}")
    if snapshot and isinstance(snapshot, dict) and "channel_values" in snapshot:
        messages = snapshot["channel_values"].get("messages", [])
        print(f"   消息数：{len(messages)}")
        for msg in messages:
            prefix = "人" if msg.type == "human" else "AI" if msg.type == "ai" else "工具"
            content = msg.content[:40] + "..." if len(msg.content) > 40 else msg.content
            print(f"      {prefix} {msg.type}: {content}")
    else:
        print("   无法读取")

print("\n" + "=" * 60)
print("演示完成！数据保存在：" + DB)
print("=" * 60)
conn.close()
