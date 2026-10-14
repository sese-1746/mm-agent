"""FR-5 静态守卫：对 LLM 生成的脚本做 AST 级安全检查。

规则（import 白名单 + 危险调用黑名单）：
- **import 白名单**（只放行以下顶层模块，其余一律拦截）：
  pandas / numpy / matplotlib / math / json / csv / sqlite3 / pathlib /
  statistics / itertools / collections / datetime / re / random
- **危险调用黑名单**（裸名）：eval / exec / __import__ / compile
- **危险调用黑名单**（属性调用）：os.system、os.popen、subprocess.*、
  importlib.import_module（动态导入绕过）
- 禁止相对导入。

采用「白名单」而非「黑名单」的原因：黑名单无法穷举绕过路径，例如
`from os import system; system("dir")` 不含 `os.system` 属性调用即被漏放；
`importlib.import_module("subprocess")` 不含 `import subprocess` 语句亦被漏放。
改为白名单后，`os`/`importlib`/`httpx` 等顶层名不在白名单即被拒绝。
违规抛 ScriptGuardError（携带全部违规点）。
"""
from __future__ import annotations

import ast

# 只允许 import 这些顶层模块（脚本建模/绘图够用，且无外网/执行能力）
ALLOWED_IMPORTS = {
    "pandas", "numpy", "matplotlib", "math", "json", "csv", "sqlite3",
    "pathlib", "statistics", "itertools", "collections", "datetime",
    "re", "random",
}
# 显式点名禁止的顶层模块（即便拼写靠近白名单也拒绝；作为白名单的补网）
BANNED_IMPORTS = {
    "requests", "httpx", "urllib3", "http", "socket", "urllib", "ftplib",
    "subprocess", "os", "sys", "shutil", "importlib", "pty", "signal",
}
BANNED_CALLS = {  # 裸名调用
    "eval", "exec", "__import__", "compile",
}
BANNED_ATTR_CALLS = {  # 属性调用（模块.函数）——防绕过
    ("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "spawnv"),
    ("subprocess", "run"), ("subprocess", "call"),
    ("subprocess", "Popen"), ("subprocess", "check_output"),
    ("importlib", "import_module"),
}


class ScriptGuardError(Exception):
    """脚本静态检查违规。message 内含全部违规行号与说明。"""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("脚本守卫拦截：\n" + "\n".join(f"- {v}" for v in violations))


def _root_name(node: ast.expr) -> str:
    """取属性链根名，如 subprocess.run → 'subprocess'。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _check_import_root(root: str, display: str, lineno: int,
                       violations: list[str]) -> None:
    """顶层名既要在白名单内，又不能在显式禁止集合内。"""
    if root in BANNED_IMPORTS:
        violations.append(f"第{lineno}行：禁止 import {display}（{root} 在禁止名单）")
    elif root not in ALLOWED_IMPORTS:
        violations.append(f"第{lineno}行：import {display} 不在白名单"
                          f"（仅允许：{', '.join(sorted(ALLOWED_IMPORTS))}）")


def validate_script_ast(src: str) -> None:
    """解析源码并执行白名单/黑名单检查；全部通过则返回 None，否则抛 ScriptGuardError。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        raise ScriptGuardError([f"语法错误，无法解析：{exc}"]) from exc

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import_root(alias.name.split(".")[0], alias.name,
                                   node.lineno, violations)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                violations.append(f"第{node.lineno}行：禁止相对导入（level={node.level}）")
                continue
            _check_import_root((node.module or "").split(".")[0],
                               f"from {node.module} import ...",
                               node.lineno, violations)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                violations.append(f"第{node.lineno}行：禁止调用 {func.id}()")
            elif isinstance(func, ast.Attribute):
                key = (_root_name(func), func.attr)
                if key in BANNED_ATTR_CALLS:
                    violations.append(f"第{node.lineno}行：禁止调用 {key[0]}.{key[1]}()")
    if violations:
        raise ScriptGuardError(violations)
