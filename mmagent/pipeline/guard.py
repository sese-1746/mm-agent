"""FR-5 静态守卫：对 LLM 生成的脚本做 AST 级安全检查。

规则（import 白名单 + 危险调用黑名单 + 写路径/死循环约束）：
- **import 白名单**（只放行以下顶层模块，其余一律拦截）：
  pandas / numpy / matplotlib / math / json / csv / sqlite3 / pathlib /
  statistics / itertools / collections / datetime / re / random /
  **xlrd / openpyxl**（`.xls`/`.xlsx` 解析）
- **危险调用黑名单**（裸名）：eval / exec / __import__ / compile
- **危险调用黑名单**（属性调用）：os.system、os.popen、subprocess.*、
  importlib.import_module（动态导入绕过）
- **写路径约束**：`open(..., <写模式>)` 与 `Path(<字面量>).write_text/write_bytes/open(<写模式>)`
  （含 `pathlib.Path`/`pathlib.PurePath` 形式）的路径若为**绝对路径**或**含 `..` 段**，
  一律拦截（防写穿工作目录）
- **死循环约束**：`while <常量真值>` 且循环体内**任意深度**无 break/return/raise 出口，拦截
- 禁止相对导入。

采用「白名单」而非「黑名单」的原因：黑名单无法穷举绕过路径，例如
`from os import system; system("dir")` 不含 `os.system` 属性调用即被漏放；
`importlib.import_module("subprocess")` 不含 `import subprocess` 语句亦被漏放。
改为白名单后，`os`/`importlib`/`httpx` 等顶层名不在白名单即被拒绝。

`os` 属**刻意整块禁止**（不是遗漏）：白名单的核心收益就是无法靠"换成员名"绕过，
连 `os.makedirs` 这类无害成员也一律拒绝——建目录请用
`pathlib.Path(...).mkdir(parents=True, exist_ok=True)`。

修订记录：
- 2026-10-14（task-003）：白名单补入 `xlrd`/`openpyxl`。原白名单漏收 `.xls` 解析库，
  造成「提示词要求用 xlrd、守卫却禁止 import xlrd」，FR-5 读附件任务在结构上无法通过守卫。
  二者均为纯本地文件解析库，无网络/无执行能力，与守卫目的（零外网 + 无危险调用）不冲突。
- 2026-10-17（task-002/第10轮，B6）：新增两条规则，补两处**真实空白**（此前连 TODO 都没有）——
  ① **写路径约束**：此前 `open()` 的写路径完全无约束，脚本可 `open("C:/Windows/System32/x","w")`
  或 `open("../../data/mm.db","w")` 静默写穿工作目录；现拦截**字面量**绝对路径与含 `..` 段的写路径。
  ② **死循环约束**：此前 `while True: pass` 无任何静态检测，唯一防线是 runner 的 `timeout_s`
  （默认 300s），每次都要烧满超时才失败；现拦截「常量真值 + 循环体内无任何出口」的 while。
  两规则均**只判字面量、保守优先**（读操作一律不查；动态 mode 按写处理但动态路径放过），
  已用 `.agent-runs/**` 下 **125 个 `solve.py` 语料**（含各轮验收 fixture 与人工探针；其中
  第 8 轮 10 个为真实 LLM 生成脚本）做回归标定，PASS→FAIL delta = 0。
  既有白名单/黑名单/异常形状/函数签名**一条未动**。
- 2026-10-17（task-002 二次修正，验收 V1~V9 后）：① 封堵 `pathlib.Path(...)` 残留洞——
  此前接收者判定只认裸名 `Path`/`PurePath`，`pathlib.Path("C:/x").write_text("a")` 属性链根名是
  `pathlib` 即被绕过（`pathlib` 在白名单内，LLM 脚本完全可能这么写）；现同时认
  `pathlib.Path`/`pathlib.PurePath`。② 订正标定措辞：语料 125 个 **并非全部真实生成脚本**
  （29 个落在 fixture 目录、至少 15 个是人工探针），且该语料 `while` 节点 0 个、绝对路径
  字面量 0 个 → **`delta=0` 对规则 A 有含义、对规则 B 几乎无信息量**；规则 B 的有效性由
  离线正例（V4）与注入对照保证，不由该标定保证。

已知局限（**刻意不防**，勿视作已覆盖；逐类点名，勿按「已覆盖」推断）：
- **只判字面量，判不了动态路径**：`validate_script_ast(src: str)` 拿不到 workdir（签名不可变，
  有 `tools/solve_tool.py` 等调用方），因此无法把运行时 cwd 纳入比较。凡路径来自变量、f-string、
  字符串拼接（含 `"C:" + "/x"`）、`*args` 展开、`Path.cwd()/...`、函数返回值者，**一律放过**
  ——`p = "C:/x"; open(p, "w")` 不会被拦。
- **mode 的动态情形只能 fail-closed 到「当写处理」，但仍需路径是字面量才有效**：
  `open("x", mode)` 会检查 `"x"`；`open(p, mode)` 两者皆变量 → 无从判起，放过。
- **只认 `open(...)` 直名调用**：`f = open; f("C:/x", "w")`、`getattr(builtins, "open")`、
  `io.open`（`io` 也不在白名单）等**均未覆盖**。
- **只认 `Path`/`PurePath` 与 `pathlib.Path`/`pathlib.PurePath` 的「直接」构造**：
  `Path(...).resolve().write_text(...)`、`Path(...).absolute()...`、`Path("C:/x") / "y"` 后再写、
  自定义包装函数、`from pathlib import Path as P`（别名）等**均未覆盖**；子类构造亦未覆盖。
- **不做 `~` 展开、不做 `C:x`（盘符相对）判定、不做大小写/符号链接/`..` 归一**：软链穿越不在判定内。
- **保守取值（宁可多拦）**：`open("a/../b", "w")` 会因存在恰为 `..` 的一段而被拦，尽管其归一后
  仍落在 cwd 内——这是 §3 契约「任一段恰为 `..` 即拦」的刻意保守口径，语料中 0 例，不改规则。
- **规则 B 只认 `ast.Constant` 且真值**：`while not False:`、`while 1 == 1:`、`while [1]:`
  （非常量表达式）、`while flag:`（变量）、`while f():`（调用）**都判不了，放过**；
  反之循环体内**任意深度**出现 break/return/raise（含嵌在 if/for/嵌套函数里）即视为有出口并放过，
  这是刻意的保守口径——宁漏不误伤真实生成脚本。
违规抛 ScriptGuardError（携带全部违规点）。
"""
from __future__ import annotations

import ast
import re

# 只允许 import 这些顶层模块（脚本建模/绘图/读附件够用，且无外网/执行能力）
ALLOWED_IMPORTS = {
    "pandas", "numpy", "matplotlib", "math", "json", "csv", "sqlite3",
    "pathlib", "statistics", "itertools", "collections", "datetime",
    "re", "random",
    "xlrd", "openpyxl",  # 读 附件1~6(.xls) 与 总表(.xlsx) —— FR-5 任务本体需要
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
    # 注：`os` 目前已在 BANNED_IMPORTS 中整块禁止，故下列 os.* 规则当前**不可达**；
    # 保留为纵深防御：一旦将来有人把 os 放进白名单，这些危险成员仍会被拦住。
    ("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "spawnv"),
    ("subprocess", "run"), ("subprocess", "call"),
    ("subprocess", "Popen"), ("subprocess", "check_output"),
    ("importlib", "import_module"),
}

# ---- 规则 A（写路径）/ 规则 B（死循环）相关常量 ------------------------------
# 视为「写」的 mode 字符：w/a/x/+ 任一出现即写（'r'/'rb' 等纯读不算）
_WRITE_MODE_CHARS = frozenset("wax+")
# Path 对象上按 mode 判定的写方法；write_text/write_bytes 恒为写
_PATH_WRITE_METHODS = frozenset({"write_text", "write_bytes", "open"})
# 可作路径构造根的类名（只认直接构造出的 Path 对象）
_PATH_CTORS = frozenset({"Path", "PurePath"})
# 经模块限定名构造：pathlib.Path / pathlib.PurePath（两种写法真实脚本都会出现）
_PATH_QUALIFIED_CTORS = frozenset({("pathlib", "Path"), ("pathlib", "PurePath")})
# Windows 盘符绝对路径：C:/ C:\
_ABS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


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


def _literal_str(node: ast.expr | None) -> str | None:
    """是字符串字面量则返回其值，否则 None（动态表达式一律 None）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    """取第 index 个位置参数，缺省时退化为同名关键字参数。"""
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _is_write_mode(mode: ast.expr | None) -> bool:
    """mode 是否按「写」处理。

    - 缺省（None）→ 读，不检查；
    - 字符串字面量 → 含 w/a/x/+ 任一即为写；
    - 非常量（变量/表达式/非 str 常量）→ 判不了，按写处理（fail-closed）。
    """
    if mode is None:
        return False
    literal = _literal_str(mode)
    if literal is None:
        return True
    return any(ch in _WRITE_MODE_CHARS for ch in literal)


def _path_violation(path: str) -> str | None:
    """按规则 A 判定字面量路径：返回违规说明的关键字，合规返回 None。"""
    if (path.startswith("/") or path.startswith("\\")
            or _ABS_DRIVE_RE.match(path)):
        return "absolute"
    if any(seg == ".." for seg in re.split(r"[\\/]", path)):
        return "dotdot"
    return None


def _check_write_path(call: ast.Call, raw_path: ast.expr | None,
                      when_write: bool, violations: list[str]) -> None:
    """在 `when_write` 成立时，对 `raw_path` 的字面量值做规则 A 判定。"""
    if not when_write:
        return
    path = _literal_str(raw_path)
    if path is None:  # 动态路径：静态判不了，保守放过（见模块 docstring「已知局限」）
        return
    kind = _path_violation(path)
    if kind == "absolute":
        violations.append(f"第{call.lineno}行：禁止以绝对路径写文件：{path!r}"
                          "（只能写工作目录内的相对路径）")
    elif kind == "dotdot":
        violations.append(f"第{call.lineno}行：禁止写入含 '..' 的路径：{path!r}"
                          "（只能写工作目录内的相对路径）")


def _is_path_ctor(func: ast.expr) -> bool:
    """是否为 `Path`/`PurePath` 直接构造，或 `pathlib.Path`/`pathlib.PurePath`。

    只认这两类形态（不做别名、不做 `.resolve()` 等链式调用，见「已知局限」）。
    """
    if isinstance(func, ast.Name):
        return func.id in _PATH_CTORS
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in _PATH_QUALIFIED_CTORS
    return False


def _check_write_call(call: ast.Call, violations: list[str]) -> None:
    """规则 A：检查 `open()` 与 `Path(...).write_text/write_bytes/.open()` 的写路径。"""
    func = call.func
    # ① 内建 open(<path>, <mode>, ...)
    if isinstance(func, ast.Name) and func.id == "open":
        _check_write_path(call, _call_arg(call, 0, "file"),
                          _is_write_mode(_call_arg(call, 1, "mode")), violations)
        return
    # ② Path(<字面量>).write_text(...) / .write_bytes(...) / .open(<mode>)
    if not isinstance(func, ast.Attribute) or func.attr not in _PATH_WRITE_METHODS:
        return
    receiver = func.value
    if not isinstance(receiver, ast.Call) or not _is_path_ctor(receiver.func):
        return
    raw_path = receiver.args[0] if receiver.args else None
    # write_text / write_bytes 恒为写；.open 按自身 mode 判定
    when_write = func.attr != "open" or _is_write_mode(_call_arg(call, 0, "mode"))
    _check_write_path(call, raw_path, when_write, violations)


def _has_loop_exit(stmts: list[ast.stmt]) -> bool:
    """语句列表内**任意深度**是否存在 break/return/raise（保守口径：不区分层级）。"""
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, (ast.Break, ast.Return, ast.Raise)):
                return True
    return False


def _check_dead_loop(node: ast.While, violations: list[str]) -> None:
    """规则 B：`while <常量真值>` 且 body/orelse 内无任何出口 → 违规。"""
    test = node.test
    if not (isinstance(test, ast.Constant) and bool(test.value) is True):
        return  # 变量/调用/假值条件：判不了，放过
    if _has_loop_exit(node.body) or _has_loop_exit(node.orelse):
        return
    violations.append(f"第{node.lineno}行：while 常量真值 循环体内无 break/return/raise 出口，"
                      "可能死循环")


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
            _check_write_call(node, violations)  # 规则 A：字面量写路径
        elif isinstance(node, ast.While):
            _check_dead_loop(node, violations)  # 规则 B：常量真值死循环
    if violations:
        raise ScriptGuardError(violations)
