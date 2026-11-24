"""前端模块化机检（2026-10-18 task-001 §8；2026-10-20 task-004 改为共用端点清单）。

设计意图：**"模块化"必须能被机检证明**，不是靠文件重命名。
本文件只读 `frontend/`，不启动服务、不改任何文件、不 import 被测代码。

端点清单的唯一来源是 `tests/endpoints_spec.py`（与 `tests/test_api_frontend_routes.py`
共用同一份）。本文件**不再自带一份字面量清单**，也**不写死条数**：条数真值由
`test_api_frontend_routes.py::test_路由清单与运行时集合相等` 保证，本文件只负责
「清单里的每一条，前端都得有调用点」。

`BACKEND_ONLY`（本文件内）登记**故意不接**的端点，逐条带理由，并由
`test_8c` 反证「理由仍然成立」：一旦有人在前端补了调用点，这条登记必须删掉，否则红。
这不是豁免通道——它是把「没有前端入口」这件事从注释里搬到断言里。

关于第 3 条（零外网）的覆盖面，有一处必须写明的口径：

- 规则约束的是**我方撰写的前端资源**不得引任何外部地址（CDN / 图片 / web font）。
  故扫描范围是 `frontend/index.html`、`frontend/assets/**`（含 `*.js` / `*.css`）。
- `frontend/vendor/pico.min.css` 是上一轮 vendored 的上游 Pico.css v2.0.6，
  **逐字节未改动**（红线 4：不许删、不许换成外链），其内部自带两处非请求型字符串：
  一是 SVG 的 W3C 命名空间标识符，二是上游作者站点的署名注释。
  两者都不会在运行时发起请求，也不由我方引入；把它判红等于要求改 vendored 上游，
  与红线 4 直接冲突。因此对 vendor 采用**白名单 + 哈希锚定**的双重断言：
  除这两处外不得再多出任何一处，且文件哈希必须与既有锚点一致。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from endpoints_spec import ENDPOINTS

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
ASSETS = FRONTEND / "assets"
CORE = ASSETS / "core"
VIEWS = ASSETS / "views"
VENDOR = FRONTEND / "vendor"

CORE_FILES = [
    "api.js", "dom.js", "theme.js",
    "sse.js", "run_stream.js", "profile_form.js",
    # 第 31 轮波次 1：三条只读能力的控件（FR-8 进度 / FR-9 溯源 / FR-10 一致性）
    "progress_panel.js", "profile_trace.js", "consistency_panel.js",
    # 第 31 轮波次 2：FR-7 论文装配（`POST /paper/build`）——产出 manifest 即一致性校验的 sidecar
    "paper_panel.js",
    # 第 32 轮波次 3：FR-3 方案卡片 / FR-1 题面解析 / 归档预览（原在 views/profile.js 内，已下沉）
    "plan_panel.js", "problem_panel.js", "archive_preview.js",
]
VIEW_FILES = [
    "home.js", "problem.js", "plan.js", "solve.js",
    "paper.js", "deliver.js", "ledger.js",
]

# 6 个对外契约 hash（五步作业链路 + 日志台账）；外壳首页 #/ 单独断言。
HASHES = {
    "#/problem", "#/plan", "#/solve",
    "#/paper", "#/deliver", "#/ledger",
}

# 前端**没有调用点**的端点：每条都必须写清「为什么不接」，分两类前缀：
#   【设计如此】长期不接（别名 / 无第二处用途）；
#   【缺口·待接线】后端先落地、SPA 还没有入口（本轮不许当没看见，条数有上限钉住）。
# `test_8c` 双向反证：登记项必须在清单里（清单删了要同步），且前端**此刻确实**没接
# （哪天接了，这条登记必须删掉，否则「不接」会退化成「接了也当没接」）。
FRONTEND_GAP: dict[tuple[str, str], str] = {
    ("GET", "/profile/show"):
        "【设计如此】兼容别名，等价 GET /profile/{profile_id}?version=——SPA 直接走后者，不重复接一遍",
    ("GET", "/archive/item/{archive_id}"):
        "【设计如此】档案单条明细；SPA 的「查看所有」用 /archive/list 的行数据 + /archive/page 逐页取图，没有第二处需要它",
}

# 缺口条数基线（2026-10-21 波次 3 后实测 **2** 条，全部为「设计如此」）。
# **只许缩小**：新增一条前端没入口的路由必须先把 SPA 接上，或由 lead 明确裁决后再抬这个数。
# 演进：8（波次前）→ 5（波次 1 接上 progress/status、consistency/check、profile/{id}/trace）
#      → 4（波次 2 接上 paper/build）→ **2**（波次 3 接上 problem/parse + plan/build）
# 口径提醒：`5 - 1 = 4`，不是 3——派工规格里写的「5 → 3」是笔误；覆盖实数与缺口数必须自洽
# （30 - GAP = 覆盖数）。波次 3 同理：30 - 2 = 28。
# 第 35 轮新增 `upload` + `solve/generate` 两条后端路由、且**同时**接上了线，
# 分母 30 → 32、缺口仍 2 → 覆盖数 28 → **30**（`32 - 2 = 30`，与 `test_8` 自洽）。
FRONTEND_GAP_BASELINE = 2

APP_LINE_CAP = 180
CSS_LINE_CAP = 700
JS_LINE_CAP = 400

PICO_SHA256 = "dd5fd5591afd81ee21dcc117ad85c014dc3f1f19dc2d7b7d101ea0acc29274c2"
# vendor 内允许出现的两处非请求型字符串（去掉 scheme 拼写，避免本文件自身被 netguard 误判）。
VENDOR_ALLOWED_HOSTS = {"www.w3.org", "picocss.com"}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}

# scheme + "://" + host（不含路径）。拼接写法是为了让本文件不出现裸的 scheme 字面量。
URL_RE = re.compile("(https?)" + ":" + r"//([^\s\"'<>)\\/]+)")

# SVG 标准命名空间豁免（与 test_frontend_copy.py 同口径）：xmlns 属性值是 XML 标识符，
# 零网络请求；豁免只限这一种属性值，其余位置出现 w3.org 仍算违规。
XMLNS_NS_RE = re.compile(
    r"""xmlns(:\w+)?\s*=\s*["']https?://www\.w3\.org/(2000/svg|1999/xhtml)["']"""
)

IMPORT_RE = re.compile(r"""import\s[^;]*?from\s*["']([^"']+)["']""")
EXPORT_RE = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)", re.M)
RENDER_EXPORT_RE = re.compile(r"export\s+function\s+render\s*\(")
STRING_PATH_RE = re.compile(r"""["'`](/[^"'`\s]*)["'`]""")
# 模板字面量里以 `${API}` 为前缀的端点（`${API}` 就是 `core/api.js` 的 `API` 常量）。
TEMPLATE_API_RE = re.compile(r"\$\{API\}(/[^\s\"'`<>]*)")

# 整行注释：`// …`、`/* …`、块注释的 `* …` 续行（D-104）。
# 只按**行**判定，不碰行内代码——`apiPost("/x");  // 说明` 这行的代码部分照常计入。
_COMMENT_LINE_RE = re.compile(r"^\s*(//|/\*|\*)")


def _strip_comment_lines(text: str) -> str:
    """去掉整行注释。

    为什么需要：`STRING_PATH_RE` 是**纯文本**正则，不认 JS 语法。注释里写一句
    `见 /plan/build` 就会被当成**真实前端调用点**，后果有两个：
    ① 替尚未接线的端点伪造覆盖，`test_8` 假绿；② 让 `test_8c` 的「已接却仍登记」
    反向检查失效。第 31 轮波次 1 与第 32 轮波次 3 各出过一次，故在**收集器**根治，
    而不是靠人记得别在注释里写路径。
    """
    return "\n".join(ln for ln in text.splitlines() if not _COMMENT_LINE_RE.match(ln))


def _tokens_in(text: str) -> set[str]:
    """从一段**源码文本**收集端点候选（去注释后匹配两种写法）。"""
    text = _strip_comment_lines(text)
    tokens: set[str] = set()
    for raw in STRING_PATH_RE.findall(text):
        tokens.add(re.sub(r"\$\{[^}]*\}", "{}", raw).split("?")[0])
    for raw in TEMPLATE_API_RE.findall(text):
        tokens.add(re.sub(r"\$\{[^}]*\}", "{}", raw).split("?")[0])
    return tokens


# ------------------------------------------------------------------ 工具

def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _authored_files() -> list[Path]:
    """我方撰写的前端资源（不含 vendor/）。"""
    files = [FRONTEND / "index.html"]
    files += sorted(ASSETS.rglob("*.js"))
    files += sorted(ASSETS.rglob("*.css"))
    return [p for p in files if p.is_file() and VENDOR not in p.parents]


def _js_files() -> list[Path]:
    return sorted(ASSETS.rglob("*.js"))


def _url_hosts(text: str) -> set[str]:
    return {m.group(2) for m in URL_RE.finditer(text)}


def _view_sources() -> dict[str, str]:
    return {n: (VIEWS / n).read_text(encoding="utf-8") for n in VIEW_FILES}


# ------------------------------------------------------------ 1 结构存在

def test_1_模块结构齐全():
    """core 十三个文件（api/dom/theme/sse/run_stream/profile_form + 7 个能力面板）与 views/*.js 七个文件齐全。"""
    expected = [CORE / n for n in CORE_FILES] + [VIEWS / n for n in VIEW_FILES]
    missing = [str(p.relative_to(REPO)) for p in expected if not p.is_file()]
    assert not missing, f"缺少模块文件：{missing}"

    extra = sorted(p.name for p in VIEWS.glob("*.js") if p.name not in VIEW_FILES)
    assert not extra, f"views/ 下出现未申报模块：{extra}"


# ---------------------------------------------------------- 2 行数上限

def test_2_单文件行数上限():
    """每个 js ≤400 行，app.js ≤180 行，style.css ≤700 行。"""
    rules: list[tuple[Path, int]] = [
        (ASSETS / "app.js", APP_LINE_CAP),
        (ASSETS / "style.css", CSS_LINE_CAP),
    ]
    rules += [(p, JS_LINE_CAP) for p in _js_files() if p != ASSETS / "app.js"]

    over = {
        str(p.relative_to(REPO)): f"{_lines(p)} 行 > 上限 {cap}"
        for p, cap in rules if p.is_file() and _lines(p) > cap
    }
    assert not over, f"单文件超行数上限（＝又长成单体）：{over}"


# ------------------------------------------------------------ 3 零外网

def test_3a_自研前端零外网():
    """frontend 下我方撰写资源中的 scheme+host 只允许 127.0.0.1 / localhost。"""
    hits = []
    for path in _authored_files():
        for m in URL_RE.finditer(XMLNS_NS_RE.sub("", path.read_text(encoding="utf-8"))):
            if m.group(2) not in LOOPBACK_HOSTS:
                hits.append(f"{path.relative_to(REPO)}: {m.group(1)}" + "://" + m.group(2))
    assert not hits, f"自研前端出现非本机地址：{hits}"


def test_3b_vendor_仅含两处非请求型字符串且哈希未变():
    """vendored Pico.css 保留、未被改动，且未混入第三处地址。"""
    pico = VENDOR / "pico.min.css"
    assert pico.is_file(), "红线 4：不许删掉 vendor/pico.min.css"

    raw = pico.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PICO_SHA256, "vendored 上游文件被改动过"

    hosts = _url_hosts(raw.decode("utf-8", errors="ignore"))
    assert hosts == VENDOR_ALLOWED_HOSTS, f"vendor 内地址集合变化：{sorted(hosts)}"


# -------------------------------------------------------- 4 路由完整性

def test_4_路由表恰好六个hash且含外壳首页():
    """从 app.js 解析出的 hash 集合等于 6 项常量集合，且包含 #/。

    **去注释再解析**（与 `test_8` 的 D-104 同口径）：`#/xxx` 出现在注释里不得算作路由，
    否则在注释里写一句 `#/profile` 就会让本用例**假红**，且失败信息会指向错的方向。
    """
    src = _strip_comment_lines((ASSETS / "app.js").read_text(encoding="utf-8"))
    found = set(re.findall(r"#/[A-Za-z0-9_]*", src))

    extra = sorted(found - HASHES - {"#/"})
    lacking = sorted(HASHES - found)
    assert not extra and not lacking, f"hash 集合不符：多出 {extra}，缺少 {lacking}"
    assert "#/" in found, "外壳首页 hash #/ 缺失"

    # 路由表条目数必须与集合大小一致（防止同一 hash 写两遍）
    assert len(re.findall(r'hash:\s*"#/', src)) == len(HASHES) + 1, "ROUTES 条目数不为 6（含 HOME 则为 7）"


# ---------------------------------------------------------- 5 模块契约

def test_5_每个视图恰导出一个render():
    """每个 views/*.js 恰有 1 个 `export function render`，且不导出别的东西。"""
    problems = []
    for name, src in _view_sources().items():
        n = len(RENDER_EXPORT_RE.findall(src))
        if n != 1:
            problems.append(f"{name}: 发现 {n} 个 render 导出")
        exported = EXPORT_RE.findall(src)
        if exported != ["render"]:
            problems.append(f"{name}: 导出了 {exported}（只允许 render）")
    assert not problems, problems


# ------------------------------------------------------ 6 禁止跨 view

def test_6_视图之间禁止互相import():
    """views/*.js 的相对 import 只允许指向 ../core/。"""
    problems = []
    for name, src in _view_sources().items():
        specs = IMPORT_RE.findall(src)
        bad = [s for s in specs if not s.startswith("../core/")]
        if bad:
            problems.append(f"{name}: {bad}")
    assert not problems, f"出现跨 view / 越界 import：{problems}"


# ---------------------------------------------------------- 7 语法机检

def test_7_语法机检_node_module_check():
    """对每个 js 文件执行 `node --input-type=module --check`。"""
    node = shutil.which("node")
    if not node:
        pytest.skip(
            "跳过原因：本机 PATH 上找不到 node 可执行文件，"
            "无法执行 `node --input-type=module --check` 语法机检"
            "（规格 §8 第 7 条允许 skip，但必须在跳过原因里写明）"
        )

    failures = []
    for path in _js_files():
        src = path.read_text(encoding="utf-8")
        cp = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=src, capture_output=True, text=True, encoding="utf-8",
        )
        if cp.returncode != 0:
            failures.append(f"{path.relative_to(REPO)} -> {(cp.stderr or '').strip()[:400]}")
    assert not failures, "语法机检失败：\n" + "\n".join(failures)


# ---------------------------------------------------------- 8 接口齐备

def _api_tokens() -> set[str]:
    """把 js 里出现的「以 / 开头的字符串字面量」收集成端点候选集。

    - 模板插值 `${...}` 归一为 `{}`，故 /profile/${x} 与清单里的 /profile/{profile_id} 可对齐；
    - 查询串 `?...` 一律截掉（查询参数不是端点身份）；
    - **两种写法都要认**：`` "/profile/write" ``（引号紧跟 `/`）与
      `` `${API}/archive/file/${aid}` ``（模板里以 `${API}` 为前缀）。
      后者是 D-95 把硬拼的 `/api/v1/...` 收进 `API` 常量之后新增的写法；
      旧规则只认前一种，会把**真实存在的调用点**判成「未覆盖」——那是收集器盲区，不是覆盖缺口。
    - **注释不算调用点**：见 `_strip_comment_lines`（D-104）。
    """
    files = [ASSETS / "app.js"] + [CORE / n for n in CORE_FILES] + [VIEWS / n for n in VIEW_FILES]
    tokens: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        tokens |= _tokens_in(path.read_text(encoding="utf-8"))
    return tokens


def test_8d_注释中的路径不计入调用点():
    """反向自证（D-104）：注释里的 `/xxx` **绝不能**算作前端调用点。

    这条用例存在的价值：`STRING_PATH_RE` 是纯文本正则，不认语法。历史事故两起——
    第 31 轮波次 1、第 32 轮波次 3（lead 修 D-103 时，在解释「不要写前导斜杠」的注释里
    又写了前导斜杠，导致 `/plan/build` 的 `test_8` 仍是假绿：把真实调用整行删掉测试照样绿）。
    """
    src = (
        '// apiPost("/ghost/line", {})\n'
        '/* apiGet("/ghost/block") */\n'
        ' * apiGet("/ghost/star")\n'
        'apiPost("/real/keep", {});   // 行内注释不影响本行代码\n'
        "const x = `${API}/real/tpl`;\n"
    )
    toks = _tokens_in(src)
    assert "/real/keep" in toks and "/real/tpl" in toks, f"真实调用点被误删：{toks}"
    ghosts = {t for t in toks if t.startswith("/ghost")}
    assert not ghosts, f"注释里的路径被当成调用点：{ghosts}"


def test_8_后端接口全部有前端调用点():
    """清单里每一条（除 `FRONTEND_GAP` 登记项）都必须有前端调用点。"""
    tokens = _api_tokens()
    norm = lambda s: re.sub(r"\{[^}]*\}", "{}", s)  # noqa: E731

    missing = [f"{m} {p}" for m, p in ENDPOINTS
               if norm(p) not in tokens and (m, p) not in FRONTEND_GAP]
    assert not missing, (
        f"前端未覆盖的端点：{missing}（已收集候选：{sorted(tokens)}）。"
        "若这是新落地的后端路由，请先接线；确属 SPA 不需要的，再登记进 FRONTEND_GAP 并写明理由")


def test_8b_端点清单自身无重复():
    """清单里不许出现「同一路径写两遍」（防靠重复项凑数）。

    条数真值不在这里断言：它由 `tests/test_api_frontend_routes.py` 的
    「清单 == 运行时集合」等式保证，本文件只查清单自身的形状。
    """
    paths = [p for _, p in ENDPOINTS]
    dup = sorted({p for p in paths if paths.count(p) > 1})
    assert not dup, f"端点清单有重复路径：{dup}"


def test_8c_前端缺口登记仍然成立且未扩大():
    """`FRONTEND_GAP` 不是豁免通道：键要在清单里、前端此刻**确实**没接、条数不允许增大。

    三个方向都查，缺一条就会退化成「登记一次、永远免责」：
    1. 清单删了某路由 → 登记成了僵尸项；
    2. 前端哪一天接了 → 登记必须删，否则「不接」等于「接了也当没接」；
    3. 又冒出一条没入口的新路由 → 条数超基线，必须接线或由 lead 裁决后抬基线。
    """
    stale = [f"{m} {p}" for m, p in FRONTEND_GAP if (m, p) not in ENDPOINTS]
    assert not stale, f"FRONTEND_GAP 里有清单中不存在的端点（清单已删？）：{stale}"

    tokens = _api_tokens()
    norm = lambda s: re.sub(r"\{[^}]*\}", "{}", s)  # noqa: E731
    covered = [f"{m} {p}" for m, p in FRONTEND_GAP if norm(p) in tokens]
    assert not covered, f"前端已经接了这些端点，请从 FRONTEND_GAP 删除登记：{covered}"

    assert len(FRONTEND_GAP) <= FRONTEND_GAP_BASELINE, (
        f"前端缺口 {len(FRONTEND_GAP)} 条 > 基线 {FRONTEND_GAP_BASELINE}："
        f"新增的多半是刚落地的后端路由，请先接线（清单共 {len(ENDPOINTS)} 条）")


# -------------------------------------------------------- 9 侧栏残留

def test_9_侧栏已彻底移除():
    """index.html 不再含 class="sidebar"；style.css 不再定义 .sidebar。"""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (ASSETS / "style.css").read_text(encoding="utf-8")

    assert 'class="sidebar"' not in html, "index.html 仍有侧栏元素"
    assert "sidebar" not in html
    assert not re.search(r"\.sidebar\b", css), "style.css 仍定义 .sidebar"


# ------------------------------------------------------------- 加固项

def test_10_出网点唯一在core_api():
    """全站只有 core/api.js 允许出现 fetch( —— 唯一出网点，便于审计。"""
    offenders = [str(p.relative_to(REPO)) for p in _js_files()
                 if "fetch(" in p.read_text(encoding="utf-8") and p != CORE / "api.js"]
    assert not offenders, f"fetch( 出现在 core/api.js 之外：{offenders}"


def test_11_错误体三要素与折叠区仍在():
    """error_code / message / trace_id 三要素 + rawDetails 折叠区逐字保留。

    第 34 轮验收实测到的**假绿**（本项目第 5 次「测试在守护已知缺陷」）：
    原断言是 `for token in ("error_code","message","trace_id","detail"): assert token in api`，
    而 `"message"` 命中的是 `api.js:5` **文件头注释**「失败体固定三要素 error_code / message /
    trace_id」——把上屏的三个 `<li>` 抽掉两个，本用例与 `test_frontend_copy::test_7` 仍全绿。
    另半句 `"function rawDetails" in dom or "rawDetails" in dom` 的后半恒真，等于没断言。

    故改为锚**渲染模板片段**：`<li>message：` 这种「标签 + 全角冒号」的写法只可能出现在模板里，
    注释与文档不会这样写。`rawDetails` 只认定义，不再用 `A or B` 兜底。
    """
    api = (CORE / "api.js").read_text(encoding="utf-8")
    dom = (CORE / "dom.js").read_text(encoding="utf-8")
    for frag in ("<li>error_code：", "<li>message：", "<li>trace_id："):
        assert frag in api, f"core/api.js 丢失错误体上屏要素（渲染模板被改）：{frag}"
    assert "b.detail" in api, "core/api.js 丢失 detail 分支"
    assert "function rawDetails" in dom, "core/dom.js 丢失 rawDetails 定义"
    assert "原始响应 JSON" in dom, "rawDetails 折叠区文案被改动"


def test_12_三态助手仍在():
    """setBusy / setOut / setErr 三态助手保留在 core/dom.js。"""
    dom = (CORE / "dom.js").read_text(encoding="utf-8")
    for fn in ("export function setOut", "export function setBusy", "export function setErr"):
        assert fn in dom, f"core/dom.js 缺少 {fn}"


def test_13_外壳只引本地资源且带module入口():
    """index.html 只引本仓库相对路径；app.js 以 module 方式加载。"""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="assets/app.js"></script>' in html
    assert 'href="assets/style.css"' in html
    assert 'href="vendor/pico.min.css"' in html, "红线 4：不许删掉 pico 基线"
    assert '<main class="stage" id="app">' in html or 'id="app"' in html
    assert "<nav" in html and 'id="tabs"' in html, "顶栏标签容器缺失"


ID_GET_RE = re.compile(r"""getElementById\(\s*["']([\w-]+)["']""")
ID_HELPER_RE = re.compile(r"""(?<![\w.])(?:iv|ic)\(\s*["']([\w-]+)["']""")
ID_DEF_RE = re.compile(r'id="([\w-]+)"')


def test_14_视图引用的元素id都在自身模板内定义():
    """拆分后最容易犯的错：把 getElementById 搬走了但模板没搬。

    这条静态检查把「引用了本视图模板里不存在的 id」挡在合并前。
    """
    problems = []
    for name, src in _view_sources().items():
        used = set(ID_GET_RE.findall(src)) | set(ID_HELPER_RE.findall(src))
        defined = set(ID_DEF_RE.findall(src))
        missing = sorted(used - defined)
        if missing:
            problems.append(f"{name}: 引用了模板中不存在的 id {missing}")
    assert not problems, problems


# ------------------------------------------- 15 废弃 hash 零残留（第 34 轮）

# 第 34 轮把「按规划书需求编号 P1–P7 的 7 页」重排成「按作业链路的 6 个 hash」。
# 旧 hash 一律不得残留：`paper_panel.js:104` 曾渲染 `<a href="#/profile">` 死链——
# 新路由表里没有 #/profile，`renderRoute` 会静默回落首页，用户点完不知道去了哪。
# 这类残留靠人工 grep 守不住，故落成断言（扫描面 = 我方撰写的全部前端文本 + frontend/*.md）。
OLD_HASHES = ("#/intake", "#/figures", "#/profile", "#/run", "#/compliance", "#/selftest")


def test_15_废弃hash零残留():
    """旧 6 个 hash 在 frontend/ 下不得再出现（含注释与文档）。"""
    files = _authored_files() + sorted(FRONTEND.rglob("*.md"))
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for h in OLD_HASHES:
            # 右边界：`#/run` 不得匹配 `#/runner` 之类；`#/profile` 与 `#/problem` 本身不同串。
            if re.search(re.escape(h) + r"(?![A-Za-z0-9_-])", text):
                hits.append(f"{path.relative_to(REPO)}: {h}")
    assert not hits, (
        f"frontend/ 仍残留已废弃的旧 hash（第 34 轮已重排为 {sorted(HASHES)}）：{hits}。"
        "改法见 .agent-runs/2026-10-21/task-010/README.md §3.8")
