"""probe_01_versions.py — 记录解释器与 langgraph 相关包精确版本（Q2 证据）"""
import importlib.metadata as md
import sqlite3
import sys
import sysconfig

print("=" * 60)
print("解释器")
print("=" * 60)
print("sys.version      :", sys.version.replace("\n", " "))
print("executable       :", sys.executable)
print("platform         :", sysconfig.get_platform())
print("SOABI            :", sysconfig.get_config_var("SOABI"))
print("sqlite3 lib ver  :", sqlite3.sqlite_version)
print("sqlite3 module   :", sqlite3.version)

pkgs = [
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-sqlite",
    "langgraph-sdk",
    "langgraph-prebuilt",
    "langchain-core",
    "langchain-protocol",
    "aiosqlite",
    "sqlite-vec",
    "orjson",
    "ormsgpack",
    "jsonpatch",
    "langsmith",
    "websockets",
    "pydantic",
    "pydantic-settings",
    "fastapi",
    "uvicorn",
]
print()
print("=" * 60)
print("包版本")
print("=" * 60)
for p in pkgs:
    try:
        print(f"{p:32s} {md.version(p)}")
    except md.PackageNotFoundError:
        print(f"{p:32s} <NOT INSTALLED>")

print()
print("=" * 60)
print("关键 API 是否可导入")
print("=" * 60)
try:
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.memory import MemorySaver

    print("import langgraph.graph.StateGraph/START/END  : OK")
    print("import langgraph.types.interrupt/Command      : OK")
    print("import langgraph.checkpoint.sqlite.SqliteSaver: OK")
    print("import langgraph.checkpoint.memory.MemorySaver: OK")
    print("interrupt repr      :", interrupt)
    print("Command repr        :", Command)
    import inspect

    print("interrupt signature :", inspect.signature(interrupt))
    print("SqliteSaver signature:", inspect.signature(SqliteSaver.__init__))
    print("Command is a         :", type(Command))
except Exception as exc:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    print("IMPORT FAIL:", exc)
