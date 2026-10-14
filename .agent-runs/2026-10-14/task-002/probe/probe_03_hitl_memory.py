"""probe_03_hitl_memory.py — 复现课件 demo09（人机回环，MemorySaver）

与 demo09 差异：agent 节点用确定性 stub 替代 ChatTongyi（零外网）。
sensitive_tool_node 中的 interrupt() + Command(resume=) 用法与 demo09 完全一致。
覆盖 demo09 的场景1/2/3/4：普通查询 / 删库批准 / 删库拒绝 / 查看快照。
"""
from typing import Annotated

from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command
from langchain_core.messages import ToolMessage, AIMessage, HumanMessage
from langchain_core.tools import tool


class State(TypedDict):
    messages: Annotated[list, add_messages]


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


def agent_node(state: State):
    last_human = None
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            last_human = m
            break
    text = last_human.content if last_human else ""
    if "删除" in text:
        db = text.split()[-1]
        ai = AIMessage(content="", tool_calls=[{"name": "delete_database", "args": {"db_name": db},
                                                "id": "call_del_1", "type": "tool_call"}])
    else:
        city = text.replace("今天", "").replace("天气怎么样", "").strip() or "西安"
        ai = AIMessage(content="", tool_calls=[{"name": "get_weather", "args": {"city": city},
                                                "id": "call_w_1", "type": "tool_call"}])
    return {"messages": [ai]}


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
    human_decision = interrupt({
        "action": "delete_database",
        "target": db_name,
        "warning": "此操作不可恢复！",
    })
    print(f"    [interrupt 返回] {human_decision!r}")
    if human_decision == "approve":
        result = tools_map["delete_database"].invoke(delete_call["args"])
        content = f"OK {result}（已人工确认）"
    else:
        content = "操作已取消（人工拒绝）"
    return {"messages": [ToolMessage(content=content, tool_call_id=delete_call["id"])]}


def should_continue(state: State):
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        return END
    for tc in last_message.tool_calls:
        if tc["name"] == "delete_database":
            return "sensitive_tool"
    return "tools"


builder = StateGraph(State)
builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)
builder.add_node("sensitive_tool", sensitive_tool_node)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue,
                              {"tools": "tools", "sensitive_tool": "sensitive_tool", END: END})
builder.add_edge("tools", END)
builder.add_edge("sensitive_tool", END)

memory = MemorySaver()
graph = builder.compile(checkpointer=memory)
print(graph.get_graph().draw_mermaid())

print("=" * 60)
print("LangGraph 人机回环演示（probe 复现 demo09，MemorySaver）")
print("=" * 60)

# 场景1：普通查询
config1 = {"configurable": {"thread_id": "user_001"}}
r1 = graph.invoke({"messages": [("human", "西安 天气怎么样")]}, config=config1)
print(f"\n场景1 最终答案：{r1['messages'][-1].content}")

# 场景2：删库 + 批准
config2 = {"configurable": {"thread_id": "user_002"}}
print("\n场景2 第1步：用户请求删除数据库（触发 interrupt）")
r2 = graph.invoke({"messages": [("human", "删除数据库 users")]}, config=config2)
print(f"    图已暂停；返回 keys={list(r2.keys())}；__interrupt__={r2.get('__interrupt__')}")
print("场景2 第2步：人工批准，恢复执行")
r2r = graph.invoke(Command(resume="approve"), config=config2)
print(f"场景2 最终答案：{r2r['messages'][-1].content}")

# 场景3：删库 + 拒绝
config3 = {"configurable": {"thread_id": "user_003"}}
print("\n场景3 第1步：用户请求删除数据库（触发 interrupt）")
graph.invoke({"messages": [("human", "删除数据库 orders")]}, config=config3)
print("    图已暂停；")
print("场景3 第2步：人工拒绝，恢复执行")
r3r = graph.invoke(Command(resume="reject"), config=config3)
print(f"场景3 最终答案：{r3r['messages'][-1].content}")

# 场景4：快照
print("\n场景4：查看保存的状态快照")
for thread_id in ["user_001", "user_002", "user_003"]:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = memory.get(config)
    print(f"  会话 {thread_id}：snapshot type={type(snapshot).__name__}")
    if isinstance(snapshot, dict) and "channel_values" in snapshot:
        messages = snapshot["channel_values"].get("messages", [])
        print(f"   消息数：{len(messages)}")
        for msg in messages:
            content = msg.content[:50] + "..." if len(msg.content) > 50 else msg.content
            print(f"      {msg.type}: {content}")
    else:
        print("   无法读取状态")

print("\n演示完成！")
