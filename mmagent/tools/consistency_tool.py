"""FR-10 一致性校验：**正文数字 vs 结果文件**（纯读，不调 LLM、不联网、不写盘）。

需求来源：《智能体业务场景需求说明书》场景一流程第 6 步——「论文装配 → … → 跑
FR-10 一致性校验（正文数字 vs 结果文件）」。本场景最贵的故障是「错数字安静地流进
论文」，所以本工具只回答一个问题：**人写的正文里出现的数字，能不能在结果表里找到出处**。

第 2 轮口径（R25 返工）：**不再靠段内正则猜「哪个数字是数据」**。首轮实现把真论文
（`paper_tool` 产物）跑出 8 条 unmatched，没有一条是编造数字——全是版本号 `v1`、
档案转述的赛题常量（480 小时、±10%）、占位句里的小节号 `5.1`、附录路径里的数字。
根因是「从文本猜语义」；改为**读 sidecar 的逐段来源**做段级判定：

1. **有 sidecar（`paper.manifest.json`）时**：先比对每段 `text_sha256`——
   不符或缺失即视为**人工改写**，参与校验（`tampered_paragraphs`）；相符才按来源判定：
   `origin ∈ {mm-origin-placeholder, mm-origin-agent}` 或 `from_archive` 为真的段落排除，
   `mm-origin-human` 参与校验。**sidecar 里没有的段落同样参与校验（fail-open）**——
   人改稿后 sidecar 必然落后于 docx，那正是编造数字唯一能进来的时刻。
   **有 sidecar 时标题/题注/空段的文本特征一律不短路**（「表 3 显示…」是常见正文句式，
   若被题注规则吞掉就是假绿灯）；只有「无条目且文本为空」记 `empty`。
2. **无 sidecar 时**兜底：标题段、`图 N`/`表 N` 题注、空段、以及「附录 A」起到文末
   全部排除；其余段落参与校验。返回值用 `sidecar_used` 区分两种口径。
3. **四态 `status`**（优先级从高到低）：`mismatch` → `stale_sidecar` → `no_data` → `ok`。
   `consistent`：`mismatch` → `False`；`stale_sidecar` / `no_data` → `None`；`ok` → `True`。
   「没东西可校验」「校验基础已过期」**都不是**「校验通过」（不许绿灯摆拍）。
   `stale_sidecar` = 存在未登记或被改写段落；若最终没有留下可判定数字才落到该状态
   （若过期段落里的数字都有出处，仍判 `ok`，否则真论文人工补写会被一律判红）。
4. 抽 token 前先剥离非数据串：绝对路径、`*.py|csv|json|docx|md` 文件名、`vN` 版本号。
5. `ignore_tokens` 显式豁免：命中项从校验集合移除，但逐条回显在 `ignored` 里（豁免是
   自由度，必须留痕，不许静默）。
6. 中文数字不解析，但参与校验且抽不到阿拉伯数字的段落会进 `cjk_numeral_paragraphs`
   （「有数字但我读不出来」的显式信号，交人工确认）。

7. **表格口径（第 26 轮，关闭 D-63）**：`doc.tables` **参与**校验，判据是「这张表
   是否被 sidecar 登记过」，**不是**「表名是不是结果表」。`paper_tool` 已把符号表逐格
   写进 sidecar 的 `tables[*].cells`，故对每张 docx 表算**内容指纹**（行优先、单元格
   `\\x1f` 分隔、行 `\\x1e` 分隔，`sha256`），与 sidecar 同构桶比对：
   - **命中** → 该表是 agent 产出的结构表（符号说明/参数表），单元格文本**不抽 token**
     （`x1`/`t2` 这类符号下标天然不误报），计入 `registered_tables` 与
     `excluded["table_registered"]`；
   - **未命中但展平后与 sidecar 某表整表相等** → 只是合并/重排（Word 合并会把两格
     文本用换行拼成新文本，指纹必然变），不参与校验，计入 `format_changed_tables` 与
     `excluded["table_format_only"]`（第 27 轮修 D-79a）；
   - **其余未命中** → 该表被人工新增或改写（或 sidecar 没有 `tables` 键 / 无 sidecar），
     **逐格**抽 token 参与校验，表下标进 `tampered_tables`。
   这与段落级「`text_sha256` 相符且来源为占位/agent 则排除」是同一套 fail-open 逻辑：
   人动过的表格必须被查。

其余口径：正文段落读 `doc.paragraphs`，表格读 `doc.tables`（见第 7 条）；NFKC 归一再按
`\\d+(?:\\.\\d+)?%?` 抽 token；结果表递归读 `results_dir/**/*.csv`（`utf-8-sig` 优先，
失败再 `gbk`）；严格比对之外另做「去尾部 `%`」的宽松比对，只报告 `loose_matched`，
不阻断；`paper_numbers` / `result_numbers` 均按去重后 token 数计。
目录不存在或没有 CSV → `FileNotFoundError`（调用方转 exit 2）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from docx import Document

#: 允许的数字形态：整数 / 小数 / 百分数（`12`、`0.85`、`12%`）
TOKEN_RE = re.compile(r"\d+(?:\.\d+)?%?")
#: 图题/表题（`图 3 得分分布`、`表 2 结果`）——编号不是数据
CAPTION_RE = re.compile(r"^(?:图|表)\s*\d+")
#: `Heading 1` / `Heading 2` …（大纲级别）
HEADING_RE = re.compile(r"^Heading\s+(\d+)$")
#: 无 sidecar 时的附录识别前缀（与 `paper_tool.CHAPTER_APPENDIX` 同一章名）
APPENDIX_PREFIX = "附录 A"
#: sidecar 后缀：`paper.docx` → `paper.manifest.json`（与 `paper_tool` 一致）
MANIFEST_SUFFIX = ".manifest.json"
#: 结果表解码顺序：先 UTF-8（含 BOM），失败再 GBK
CSV_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gbk")
#: `preview` 截断长度（与 manifest 的 `text_preview` 同口径）
PREVIEW_CHARS = 60

#: sidecar 里的段落来源（`paper_tool.ORIGINS`）
ORIGIN_AGENT = "mm-origin-agent"
ORIGIN_HUMAN = "mm-origin-human"
ORIGIN_PLACEHOLDER = "mm-origin-placeholder"

#: 抽 token 前剥离的非数据串（这些出现在人写正文里也不该被当成数据）
PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s，。；）)]*")
FILENAME_RE = re.compile(r"\S+\.(?:py|csv|json|docx|md)(?![A-Za-z0-9_])")
VERSION_RE = re.compile(r"(?<![A-Za-z0-9])v\d+")
#: 中文数字（不做解析，只暴露「这段像有数字但读不出来」的信号）
CJK_NUMERAL_RE = re.compile(r"[零一二三四五六七八九十百千万亿两]")
#: 行首编号（正文段）：`表 3 显示…`、`第 3 章…`、`问题1：…`。
#: 只剥行首、只剥一次——编号不是数据，但正文里的真实数字必须保留。
LEADING_BODY_NUMBER_RE = re.compile(
    r"^(?:(?:图|表|式)\s*\d+(?:[.\-－]\d+)*"
    r"|第\s*\d+(?:\.\d+)*\s*[章节步]"
    r"|问题\s*\d+)")
#: 行首编号（标题段）：`5.1 结论…` 必须含小数点；`2024 年…` 不得被剥。
LEADING_HEADING_NUMBER_RE = re.compile(
    r"^(?:\d+(?:\.\d+){1,3}"
    r"|[一二三四五六七八九十]+[、.．])")

#: 表格单元格分隔符（行内）与行分隔符：指纹必须能区分 `[["a","b"],["c"]]` 与
#: `[["a"],["b","c"]]`，故行间用 `\x1e`，不只靠 `\x1f`。
CELL_SEP = "\x1f"
ROW_SEP = "\x1e"

#: `excluded` 的固定桶（每个被排除的段落/表格恰好落进一个桶）
#: 注意：**没有** `unrecorded`——sidecar 未登记的段落不是「被排除」，而是**要校验**
#: （见 `_collect`；人改稿后 sidecar 必然落后，那正是错数字能进来的时刻）。
#: 按**表格**计数的桶有两个：`table_registered`（指纹命中）与 `table_format_only`
#: （指纹不命中但展平后与 sidecar 某表相等 → 只被认为是合并/重排，D-79a）。
EXCLUDED_KEYS: tuple[str, ...] = (
    "placeholder", "agent", "from_archive", "heading", "caption", "empty",
    "appendix", "table_registered", "table_format_only",
)


# ---------------------------------------------------------------- 归一化/抽 token
def _nfkc(text: object) -> str:
    """NFKC 归一：全角数字 → 半角、`％` → `%`（只影响形态，不改数值）。"""
    return unicodedata.normalize("NFKC", text if isinstance(text, str) else "")


def strip_non_data(text: str) -> str:
    """剥离非数据串：绝对路径、结果/脚本文件名、`vN` 版本号。

    为什么在抽 token 之前做：真论文里 `C:/…/solve.py`、`summary.csv`、`档案 v1`
    都会贡献数字 token，但它们不是数据；先剥掉再抽，比事后按数值猜安全。
    """
    body = PATH_RE.sub(" ", text)
    body = FILENAME_RE.sub(" ", body)
    return VERSION_RE.sub(" ", body)


def strip_leading_numbering(text: str, *, heading: bool = False) -> str:
    """剥掉行首编号（只剥一次、只剥行首），避免 `表 3 显示…` 的 `3` 被当数据。

    标题段用更严的规则：必须含小数点（`5.1`）或中文序号（`一、`），
    这样 `2024 年赛题复盘…` 的 `2024` 不会被误剥。
    """
    pattern = LEADING_HEADING_NUMBER_RE if heading else LEADING_BODY_NUMBER_RE
    return pattern.sub("", _nfkc(text).strip(), count=1)


def extract_tokens(text: str) -> list[str]:
    """按出现顺序返回文本里的数字 token（**不去重**，去重由调用方决定）。"""
    return TOKEN_RE.findall(strip_non_data(_nfkc(text)))


def _loose(token: str) -> str:
    """宽松比对的键：去掉尾部 `%`（`12%` → `12`），其余原样。"""
    return token[:-1] if token.endswith("%") else token


# ---------------------------------------------------------------- sidecar
def _resolve_sidecar(paper_docx: str | Path,
                     sidecar: str | Path | None) -> tuple[Path | None, bool]:
    """定位 sidecar，返回（路径, 是否显式传入）。

    显式传入但路径不是文件 → `FileNotFoundError`（**不许静默降级**，否则调用方会以为
    用了 sidecar）。自动探测只看 docx 同名 `*.manifest.json` 是否存在。
    """
    if sidecar is not None:
        path = Path(sidecar)
        if not path.is_file():
            raise FileNotFoundError(f"显式指定的 sidecar 不存在：{path}")
        return path, True
    auto = Path(paper_docx).with_suffix(MANIFEST_SUFFIX)
    return (auto, False) if auto.is_file() else (None, False)


def _load_sidecar(path: Path, *,
                  strict: bool) -> tuple[dict[int, dict], list[dict] | None] | None:
    """读 sidecar，返回（`paragraph_index → 条目`, `tables` 列表或 `None`）。

    `strict=True`（显式传入）：读不出 / 结构不符 → `ValueError`；
    `strict=False`（自动探测）：返回 `None`，调用方退回启发式并把 `sidecar_used`
    如实置为 `False`。返回空 dict 是**有效结果**（manifest 里没有段落条目）。

    `tables` 为 `None` 的三种情形**同义**：sidecar 没有 `tables` 键（第 21 轮之前的
    旧 manifest）、`tables` 不是数组、数组里没有可用 `cells`。都按「一张表也没登记」
    处理 → 全部表格参与校验（fail-open，不许静默排除）。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if strict:
            raise ValueError(f"sidecar 读不出或不是合法 JSON：{path}（{exc}）") from exc
        return None
    if not isinstance(data, dict) or not isinstance(data.get("paragraphs"), list):
        if strict:
            raise ValueError(f"sidecar 结构不符（缺少 paragraphs 数组）：{path}")
        return None
    entries: dict[int, dict] = {}
    for entry in data["paragraphs"]:
        if not isinstance(entry, dict):
            continue
        index = entry.get("paragraph_index")
        if isinstance(index, int) and not isinstance(index, bool):
            entries[index] = entry
    raw_tables = data.get("tables")
    tables = [item for item in raw_tables
              if isinstance(item, dict) and isinstance(item.get("cells"), list)] \
        if isinstance(raw_tables, list) else None
    return entries, (tables or None)


# ---------------------------------------------------------------- 结果表
def _read_csv_rows(path: Path) -> list[list[str]]:
    """按 `CSV_ENCODINGS` 顺序试解码；全部失败则抛出最后一次 `UnicodeDecodeError`。"""
    last_error: UnicodeDecodeError | None = None
    for encoding in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.reader(handle))
        except UnicodeDecodeError as exc:       # 换下一种编码
            last_error = exc
    assert last_error is not None
    raise last_error


def _read_results(root: Path) -> tuple[set[str], list[str]]:
    """扫 `root/**/*.csv`，返回（去重 token 集合，排序后的相对文件名列表）。"""
    files = sorted((p for p in root.rglob("*.csv") if p.is_file()),
                   key=lambda p: p.as_posix())
    if not files:
        raise FileNotFoundError(f"结果目录下没有 CSV 文件：{root}")
    tokens: set[str] = set()
    names: list[str] = []
    for path in files:
        names.append(path.relative_to(root).as_posix())
        for row in _read_csv_rows(path):
            for cell in row:
                tokens.update(extract_tokens(cell))
    return tokens, sorted(names)


# ---------------------------------------------------------------- 正文
def _appendix_start(paragraphs) -> int | None:
    """无 sidecar 时的附录起点：第一个以「附录 A」开头的段落下标（含该段）。"""
    for index, para in enumerate(paragraphs):
        if _nfkc(para.text).strip().startswith(APPENDIX_PREFIX):
            return index
    return None


def _classify(entry: dict) -> str | None:
    """sidecar 条目的段级判定：`from_archive` 优先，其次 origin。

    返回 `None` 表示「已登记但来源不认识」——按 fail-open 处理：**参与校验**，
    不排除（宁可多查，不可漏查）。
    """
    if entry.get("from_archive"):
        return "from_archive"
    origin = str(entry.get("origin") or "")
    if origin == ORIGIN_AGENT:
        return "agent"
    if origin == ORIGIN_PLACEHOLDER:
        return "placeholder"
    if origin == ORIGIN_HUMAN:
        return "human"
    return None


def _entry_sha(entry: dict) -> str | None:
    """取条目里可用的 `text_sha256`；缺失/空串返回 `None`。"""
    value = entry.get("text_sha256")
    return value if isinstance(value, str) and value else None


def _is_legacy_sidecar(entries: dict[int, dict] | None) -> bool:
    """整份 sidecar 的条目**全都**缺 `text_sha256` → 旧格式（只能按下标匹配）。

    部分缺失不算 legacy：那几段仍按 fail-closed 当作被改写校验。
    """
    return bool(entries) and all(_entry_sha(entry) is None for entry in entries.values())


def _sha_buckets(entries: dict[int, dict]) -> dict[str, list[dict]]:
    """`sha256 → 条目列表`（按 sidecar 顺序），供插入段落后的重对齐使用。"""
    buckets: dict[str, list[dict]] = {}
    for _, entry in sorted(entries.items()):
        sha = _entry_sha(entry)
        if sha:
            buckets.setdefault(sha, []).append(entry)
    return buckets


def _take_by_sha(buckets: dict[str, list[dict]], cursors: dict[str, int],
                 sha: str) -> dict | None:
    """取该 sha 下一个未用条目（重复段落按顺序一一配对）。"""
    bucket = buckets.get(sha)
    if not bucket:
        return None
    position = cursors.get(sha, 0)
    if position >= len(bucket):
        return None
    cursors[sha] = position + 1
    return bucket[position]


# ---------------------------------------------------------------- 表格（D-63）
def _cell_text(cell: object) -> str:
    """单元格文本原样取出：非字符串（`None` 等）保守地变成空串。

    **不做 NFKC 归一**：指纹要回答「这一格有没有被人动过」，全角改半角同样是动过；
    归一留给后面的抽 token 环节（`strip_leading_numbering` / `extract_tokens`）。
    """
    return cell if isinstance(cell, str) else ""


def _cells_fingerprint(cells: list[list[str]]) -> str:
    """表格内容指纹：行优先、单元格 `\\x1f` 分隔、行间 `\\x1e` 分隔，`sha256`。

    空单元格保留空串参与拼接——位置必须稳定，否则「删掉一格」会让后面所有格错位，
    指纹从「同一张表」变成「另一张表」。行间另加 `\\x1e` 是为了让
    `[["a","b"],["c"]]` 与 `[["a"],["b","c"]]` 不撞车（只靠 `\\x1f` 会撞）。

    与 `paper_tool` 同源：sidecar 的 `tables[*].cells` 是 `[[表头...], [数据行...]]`，
    本函数对**同样的二维数组**做同样的拼接，故 agent 产出的表与 docx 里那张表指纹相等。
    """
    rows = [CELL_SEP.join(_cell_text(cell) for cell in row)
            for row in cells if isinstance(row, list)]
    return hashlib.sha256(ROW_SEP.join(rows).encode("utf-8")).hexdigest()


def _cell_bag(cells: list[list[str]]) -> tuple:
    """整表的「内容包」：**字符计数 + 数字 token 多重集**，都与位置无关。

    为什么不能只用「去空白后按行优先拼成一个串」这种位置敏感判据
    （第 27 轮独立验收实测的缺陷）：
    拼接是**位置敏感**的。横向合并在同一行内完成，行优先顺序不变，所以拼接碰巧相等；
    **纵向合并跨行**——把第 1、2 行的 `件` 并成一格 `件\\n件` 后，第二个 `件` 从第 2 行
    跑到了第 1 行末尾，拼接串随之错位（sidecar `…件结果x2乙产量件结果` vs 论文
    `…件件结果x2乙产量结果`），纯纵向合并**仍然假红**。合并本来就可能跨行列，
    判据因此不能要求「同一个字符还在原来那一行」。

    位置无关到什么程度：只要求**每个字符出现的次数**与**每个数字 token 出现的次数**
    完全相同。这仍然是一条强约束——表中每多一个字符、每换一个数字都会不等：
    - `符号`+`含义` 合并 → 字符计数不变 ✓ 放过
    - `件`+`件` 纵向合并 → `件` 仍是 2 次 ✓ 放过
    - 合并后把某格改成 `9999` → 字符计数多 4 个 `9`、数字多一个 `9999` ✗ 抓住
    - `42` 改成 `24` → 字符计数相同，但数字多重集 `{42}` → `{24}` ✗ 抓住
    - 把 A 格里原有的数字搬到 B 格 → 两种计数都不变 ✓ 放过（**有意放宽**：位置搬动
      不引入任何新数字，不可能藏编造值；FR-10 查的是「正文数字能否在结果文件找到」）

    为什么数字 token 要**逐格**抽再合并，而不是对拼接串抽：拼接会把相邻两格的数字
    粘成一个假 token（`5` 与 `42` 拼成 `542`），sidecar 侧却分别抽到 `5`/`42`，
    于是纯格式改动会被判成内容改动。
    """
    chars: list[str] = []
    tokens: list[str] = []
    for row in cells:
        if not isinstance(row, list):
            continue
        for cell in row:
            # `str.split()` 无参按**所有** Unicode 空白切分（含 `\n`/`\t`/`\xa0`）。
            text = "".join(_cell_text(cell).split())
            chars.extend(text)
            tokens.extend(extract_tokens(text))
    return (tuple(sorted(Counter(chars).items())), tuple(sorted(tokens)))


def _table_cells_logical(table) -> list[list[str]]:
    """取「逻辑网格」的单元格文本：同一个 `w:tc` 只保留**首次**出现。

    `row.cells` 对合并区域会把同一个单元格重复返回（横向合并 2 格读两次同样的
    `符号\\n含义`，纵向合并 3 行读三次），直接展平会把合并后的文本重复计入 →
    `符号含义符号含义` ≠ sidecar 的 `符号含义`，于是「纯合并」也会被判内容改动。
    按 `w:tc` 的树内路径去重后，合并区域恰好贡献**一次**它拼接后的文本，与 sidecar
    里合并前的若干格首尾相接**完全相同**（`符号`+`含义` == `符号\\n含义` 去空白）。

    去重键用 `getpath` 而不是 `id(cell._tc)`：lxml 的 element 代理会在回收后重建，
    `id` 可能被复用（实测纵向合并时第二列三行拿到同一个 `id`）。
    """
    seen: set[str] = set()
    rows: list[list[str]] = []
    for row in table.rows:
        texts: list[str] = []
        for cell in row.cells:
            tc = cell._tc
            try:
                key = tc.getroottree().getpath(tc)
            except (AttributeError, ValueError):      # 非 lxml 实现：退回不去重
                key = ""
            if key:
                if key in seen:
                    continue
                seen.add(key)
            texts.append(cell.text or "")
        rows.append(texts)
    return rows


def _table_sha_buckets(tables: list[dict] | None) -> dict[str, list[dict]]:
    """`指纹 → sidecar 表条目列表`（`tables` 为 `None` 时返回空桶 = 一张也没登记）。"""
    buckets: dict[str, list[dict]] = {}
    for entry in tables or []:
        if not isinstance(entry, dict):
            continue
        cells = entry.get("cells")
        if not isinstance(cells, list):
            continue
        buckets.setdefault(_cells_fingerprint(cells), []).append(entry)
    return buckets


def _table_cells(table) -> list[list[str]]:
    """按 `row.cells` 逐格取文本（**与 `paper_tool` 写 sidecar 时的取法同源**）。

    `paper_tool.build_paper` 用 `table.add_row().cells` 逐格 `cell.text = value`，
    并把源数据行写进 `tables[*].cells`；两边都是「一行 N 格」的二维数组，故指纹可比。

    合并单元格注意：python-docx 的 `row.cells` 对合并区域会**重复返回同一个 cell**
    （横向合并的 2 格里读两次同样的文本），而 `paper_tool` 从不合并单元格、sidecar 里
    没有重复格 → 带合并的表**必然指纹不命中**。指纹因此只回答「有没有被动过」，
    「是不是只是合并/重排」由 `_cell_bag` + `_table_cells_logical` 回答（D-79a）。
    """
    return [[cell.text or "" for cell in row.cells] for row in table.rows]


def _collect_tables(doc, *, sidecar_tables: list[dict] | None,
                    ignore_tokens: tuple[str, ...],
                    first_seen: dict[str, dict],
                    ignored: dict[str, dict]) -> dict:
    """逐表判定「是否被 sidecar 登记」，未登记的表**逐格**抽 token 参与校验。

    判据（第 26 轮裁定 R-1）：**不是**「表名是不是结果表」，而是「这张表的内容指纹
    能不能在 sidecar `tables[*].cells` 里找到」。`paper_tool` 已把符号表逐格登记，
    所以命中 = agent 产出的结构表 → 单元格不参与数字校验（`x1`/`t2` 不误报）；
    未命中 = 人工新增/改写，或 sidecar 没有 `tables`（旧 manifest）→ 逐格校验。

    `first_seen` / `ignored` 与段落共用同一份字典（表格 token 与正文 token 一起判重、
    一起比对结果表），表内位置记 `{"kind": "table", "table": i, "row": r, "col": c}`。

    合并单元格注意：python-docx 的 `row.cells` 对合并区域会**重复返回同一个 cell**
    （横向合并的 2 格里读两次同样的 `符号\\n含义`），而 `paper_tool` 从不合并单元格、
    sidecar 里没有重复格 → 带合并的表**必然指纹不命中**。这是 D-79a 的假红来源：
    人只是合并了表头，却被整表逐格校验，`x1` 的下标 `1` 被当成编造数字。
    第 27 轮的处置不是放宽指纹，而是补一道**展平比对**（见下）。

    返回 `checked_tables` / `registered_tables` / `tampered_tables` /
    `format_changed_tables` / `table_cells_checked`（见 `check_consistency` 文档）。

    第 27 轮（D-79a）：指纹不命中时再比一次**内容包**（`_cell_bag`：字符计数 +
    数字 token 多重集，与位置无关）——与 sidecar 里**某一张**表的内容包**整表完全
    相等** → 判「格式改动（疑似合并/重排）」，不参与数字校验，表下标进
    `format_changed_tables`；不等 → 维持逐格校验。
    判据是**整表相等**，不是「有一格能对上」，故「合并后又改数字」仍会被抓（见测试）。
    与位置无关是必需的：横向合并在同一行内完成，纵向合并（`件`+`件` → 一格 `件\n件`）
    跨行，位置敏感判据会漏掉后者（第 27 轮独立验收实测）。
    """
    tables = list(getattr(doc, "tables", None) or [])
    buckets = _table_sha_buckets(sidecar_tables)
    #: `内容包 → 剩余可配对张数`：与指纹桶一样逐张消费，重复表不会都靠同一条登记放过。
    #: 键是 `_cell_bag`（字符计数 + 数字多重集），**与位置无关**，故纵向合并也能命中。
    bag_left: dict[tuple, int] = {}
    for entry in sidecar_tables or []:
        if not isinstance(entry, dict):
            continue
        cells = entry.get("cells")
        if not isinstance(cells, list):
            continue
        bag = _cell_bag(cells)
        if bag[0]:                     # 空表（一个字符都没有）不作为「格式改动」凭证
            bag_left[bag] = bag_left.get(bag, 0) + 1
    cursors: dict[str, int] = {}
    ignored_set = set(ignore_tokens)
    registered_tables = 0
    checked_tables = 0
    table_cells_checked = 0
    tampered_tables: list[int] = []
    format_changed_tables: list[int] = []

    for index, table in enumerate(tables):
        cells = _table_cells(table)
        # 重复出现的同名/同内容表逐张消费（与段落 `_take_by_sha` 同构）。
        entry = _take_by_sha(buckets, cursors, _cells_fingerprint(cells))
        if entry is not None:
            registered_tables += 1
            continue
        # 指纹不命中：再问一次「是不是只是被合并/重排」（内容包整表相等）。
        bag = _cell_bag(_table_cells_logical(table))
        if bag[0] and bag_left.get(bag, 0) > 0:
            bag_left[bag] -= 1
            format_changed_tables.append(index)
            continue
        tampered_tables.append(index)
        checked_tables += 1
        for row_index, row in enumerate(cells):
            for col_index, text in enumerate(row):
                table_cells_checked += 1
                # 与正文同一套词法：剥行首编号（`表 2` 可能落在首格）后抽 token。
                tokens = extract_tokens(strip_leading_numbering(text))
                for token in tokens:
                    if token in ignored_set:
                        ignored.setdefault(token, {"kind": "table", "table": index,
                                                   "row": row_index,
                                                   "col": col_index})
                        continue
                    if token in first_seen:
                        continue
                    first_seen[token] = {
                        "kind": "table",
                        # `chapter` / `paragraph_index` 是为**兼容 `cli.py` 的渲染**而保留
                        # 的键（冻结文件，本轮不可改）：CLI 对每条 unmatched 无条件取
                        # `item["chapter"]` 与 `item["paragraph_index"]`，缺键会 KeyError。
                        # 表格没有段落下标，故为 `None`；真实定位看 `table`/`row`/`col`。
                        "chapter": "",
                        "paragraph_index": None,
                        "table": index,
                        "row": row_index,
                        "col": col_index,
                        "preview": text.strip()[:PREVIEW_CHARS],
                    }

    return {
        "checked_tables": checked_tables,
        "registered_tables": registered_tables,
        "tampered_tables": tampered_tables,
        "format_changed_tables": format_changed_tables,
        "table_cells_checked": table_cells_checked,
    }


def _collect(doc, *, include_appendix: bool, sidecar_entries: dict[int, dict] | None,
             ignore_tokens: tuple[str, ...]) -> dict:
    """逐段分类并抽数字。

    返回：`first_seen`（token → 首次出现位置）、`ignored`（被显式豁免的 token →
    位置字典；由 `check_consistency` 连同表格豁免项一起渲染成列表）、
    `excluded`（分桶计数，7 个段落桶 + `table_registered` 由 `_collect_tables` 填）、
    `checked_paragraphs`（参与校验的段落数）、
    `appendix_skipped`、`unrecorded_paragraphs`、`tampered_paragraphs`
    （sha256 与 sidecar 不符、按人工改写处理的段落下标）、
    `cjk_numeral_paragraphs`（有中文数字但抽不到阿拉伯数字的段落下标）。

    两条 fail-open（第 4 轮）：
    1. **有 sidecar 时格式规则不生效**：`Heading N` / `图 N|表 N` 开头 / 空段这些文本
       特征不再抢先排除，一律以 `origin` + `text_sha256` 为准。否则「表 3 显示，最优
       利润为 9999 元。」这种最常见句式会被 `caption` 桶吞掉，报出假绿灯。
    2. **sha256 不符即视为人工改写并参与校验**：人会在原占位段上直接打字，
       origin 仍是 placeholder；不校验 sha 就等于永远放行。缺 `text_sha256` 字段同样
       按改写处理（fail-closed）。

    第 6 轮再修两处误伤：
    3. **按 `text_sha256` 重对齐**：先建 `sha → 条目` 桶逐个消费，插入段落/空段导致的
       下标整体错位不会再被当成「整篇改写」；只有 sha 对不上任何条目时才判被改写。
    4. **旧格式 sidecar**（全部条目都缺 `text_sha256`）退回下标匹配并置
       `legacy_sidecar=True`——缺字段与内容变了是两件事，但同样如实报告。
    5. **行首编号先剥**：`表 3 显示…` / `5.1 结论…` 的编号不是数据；剥后仍能抓到
       `9999`（见 `strip_leading_numbering`）。

    无 sidecar 时才退回格式启发式：标题 / 空段 / 题注 / 「附录 A」起全部排除。
    """
    paragraphs = doc.paragraphs
    #: 无 sidecar 时按章名切到文末（启发式兜底）。
    appendix_from = None if (include_appendix or sidecar_entries is not None) \
        else _appendix_start(paragraphs)
    ignored_set = set(ignore_tokens)
    legacy_sidecar = _is_legacy_sidecar(sidecar_entries)
    sha_buckets = _sha_buckets(sidecar_entries) \
        if sidecar_entries is not None and not legacy_sidecar else {}
    sha_cursors: dict[str, int] = {}
    excluded = {key: 0 for key in EXCLUDED_KEYS}
    first_seen: dict[str, dict] = {}
    ignored: dict[str, dict] = {}
    unrecorded_paragraphs: list[int] = []
    tampered_paragraphs: list[int] = []
    cjk_numeral_paragraphs: list[int] = []
    checked_paragraphs = 0
    chapter = ""

    for index, para in enumerate(paragraphs):
        text = para.text or ""
        style = para.style.name or ""
        normalized = _nfkc(text).strip()
        heading = HEADING_RE.match(style)
        if heading and int(heading.group(1)) == 1:
            # 一级标题决定「这一段的 chapter 归属」；是否排除由下方口径决定。
            chapter = _nfkc(text).strip()

        if sidecar_entries is None:
            # ---- 无 sidecar：格式规则兜底 ----
            if heading:
                excluded["heading"] += 1
                continue
            if not normalized:
                excluded["empty"] += 1
                continue
            if CAPTION_RE.match(normalized):        # `图 N` / `表 N` 题注
                excluded["caption"] += 1
                continue
            if not include_appendix and appendix_from is not None and index >= appendix_from:
                excluded["appendix"] += 1
                continue
        else:
            # ---- 有 sidecar：sha256 重对齐优先，格式特征一律不短路 ----
            if not normalized:
                excluded["empty"] += 1
                continue
            actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            entry = None if legacy_sidecar \
                else _take_by_sha(sha_buckets, sha_cursors, actual_sha)
            if entry is not None:
                bucket = _classify(entry)
                # 只有**可证明**的非数据来源才排除；来源不认识同样参与校验。
                if bucket in ("from_archive", "agent", "placeholder"):
                    excluded[bucket] += 1
                    continue
            else:
                indexed = sidecar_entries.get(index)
                if legacy_sidecar:
                    # 旧格式没有 sha，只能按下标；缺条目才算未登记。
                    if indexed is None:
                        unrecorded_paragraphs.append(index)
                    else:
                        bucket = _classify(indexed)
                        if bucket in ("from_archive", "agent", "placeholder"):
                            excluded[bucket] += 1
                            continue
                elif indexed is not None:
                    # 下标处有条目但 sha 对不上 → 该段被就地改写（或条目缺 sha）。
                    tampered_paragraphs.append(index)
                else:
                    unrecorded_paragraphs.append(index)

        checked_paragraphs += 1
        # 行首编号不是数据（`表 3 显示…`）；只剥一次、只剥行首。
        tokens = extract_tokens(strip_leading_numbering(text, heading=heading is not None))
        if not tokens and len(CJK_NUMERAL_RE.findall(normalized)) >= 2:
            cjk_numeral_paragraphs.append(index)
        for token in tokens:
            if token in ignored_set:
                ignored.setdefault(token, {"paragraph_index": index})
                continue
            if token in first_seen:
                continue
            first_seen[token] = {
                "kind": "paragraph",
                "chapter": chapter,
                "paragraph_index": index,
                "preview": text.strip()[:PREVIEW_CHARS],
            }

    return {
        "first_seen": first_seen,
        "ignored": ignored,
        "excluded": excluded,
        "checked_paragraphs": checked_paragraphs,
        "appendix_skipped": not include_appendix,
        "unrecorded_paragraphs": unrecorded_paragraphs,
        "tampered_paragraphs": tampered_paragraphs,
        "cjk_numeral_paragraphs": cjk_numeral_paragraphs,
        "legacy_sidecar": legacy_sidecar,
    }


# ---------------------------------------------------------------- 主入口
def check_consistency(*, paper_docx: str | Path, results_dir: str | Path,
                      sidecar: str | Path | None = None,
                      include_appendix: bool = False,
                      ignore_tokens: tuple[str, ...] = ()) -> dict:
    """校验「论文正文数字」是否都能在结果表里找到出处。

    参数
    ----
    paper_docx      : 待校验的 docx（读 `doc.paragraphs` **与** `doc.tables`；
                      表格口径见下）
    results_dir     : 结果目录，递归读 `**/*.csv`
    sidecar         : manifest 路径；缺省自动找 `paper_docx` 同名
                      `*.manifest.json`（存在且可读就用）。
                      **显式传入**但不存在 → `FileNotFoundError`；
                      显式传入但读不出 / 结构不符 → `ValueError`。
    include_appendix: `True` 时无 sidecar 的启发式不再从「附录 A」起排除；
                      有 sidecar 时来源判定优先（代码段仍按 `origin` 排除）。
    ignore_tokens   : 显式豁免的数字 token（原样匹配），命中项进 `ignored` 回显；
                      对段落与表格单元格同样生效。

    返回
    ----
    dict：`status`（`ok`/`mismatch`/`stale_sidecar`/`no_data`）、`consistent`
    （`ok`→True、`mismatch`→False、其余→**None**）、`paper_numbers`、
    `result_numbers`、`matched`、`unmatched`、`loose_matched`、
    `checked_paragraphs`、`excluded`（分桶计数，**8 键**，无 `unrecorded`；
    第 8 键 `table_registered` 按**表格**计数，其余按段落）、
    `ignored`、`stale_sidecar`、`unrecorded_paragraphs`、`tampered_paragraphs`、
    `cjk_numeral_paragraphs`、`legacy_sidecar`、`appendix_skipped`、`sidecar_used`、
    `results_files`、`checked_tables`（未登记、参与校验的表格数）、
    `registered_tables`（已登记、被排除的表格数）、`tampered_tables`（未命中 sidecar
    的表下标）、`table_cells_checked`（实际逐格抽过 token 的单元格数）、
    `format_changed_tables`（指纹不命中但**展平后与 sidecar 某表整表相等**的表下标）。

    表格口径（第 26 轮，D-63 已关闭）：
    - 判据是「这张表的内容指纹能否在 sidecar `tables[*].cells` 里找到」，**不是**
      「表名是不是结果表」。命中 = agent 产出的结构表（符号说明等）→ 不参与校验；
      未命中 = 人工新增/改写，或 sidecar 没有 `tables` 键（旧 manifest）、无 sidecar
      → **逐格**抽 token 参与校验（fail-open，与段落同构）。
    - 指纹 = 行优先、单元格 `\\x1f` 分隔、行 `\\x1e` 分隔的 `sha256`；空单元格保留
      空串以稳定位置。
    - 表格 token 与正文 token 进**同一份** `first_seen`：表里编的 `9999` 与正文里编的
      `9999` 一样会被抓；`unmatched` 条目的 `kind` 区分 `paragraph` / `table`。
    - `tampered_tables` **不参与** `stale_sidecar`（保持既有 18 键语义不变）；它是
      表格层面的独立信号。无 sidecar 时所有表格都会列进去（无法区分「人工新增」与
      「sidecar 缺失」，但两种情况下参与校验都是正确行为）。

    第 27 轮（D-79a，合并单元格假红）：
    - 指纹不命中**不等于**内容被改。Word 合并单元格时把原两格的文本用换行拼成新文本
      （`符号` + `含义` → `符号\\n含义`），故合并**真的改了文本**、指纹必然不命中；
      但「展平后整表相等」说明每个字符都还在表里，只是排版变了。
    - 处置：指纹不命中时再把该表的**展平文本**（去所有空白后行优先拼接，合并区按
      `w:tc` 去重只取一次）与 sidecar 里**每一张**表比。**整表相等** → 记入
      `format_changed_tables` 与 `excluded["table_format_only"]`，**不参与**数字校验；
      **不等** → 维持逐格校验并进 `tampered_tables`。
    - 只是**不报噪声**，不是放过：合并后又把某格改成 `9999` → 展平不等 → 仍被抓。
    - 两个新字段/桶**不改变** `status` 四态语义，也不参与 `stale_sidecar`——与
      `tampered_tables` 一样是独立信号（格式改动不是不一致，也不是校验基础过期）。

    第 6 轮补充：
    - 段落匹配**先按 `text_sha256` 重对齐**（插入空段/新段不会让后续段落整片变
      `tampered`）；sha 对不上任何条目才判改写。
    - `legacy_sidecar=True` 表示整份 sidecar 的条目都缺 `text_sha256`（旧格式）：
      只能按下标匹配，无法判断是否被改写；部分缺字段仍按改写校验（fail-closed）。
    - 抽 token 前剥**行首编号**（`表 3 显示…`、`5.1 结论…`），剥后仍能抓到正文里的
      编造数字（`9999`）；标题里的 `2024 年…` 不会被剥。
    - `cjk_numeral_paragraphs` 只收**命中 ≥2 个**中文字数字符的段落（`两种`/`一、`
      这类单字噪声不报）。

    口径（`stale_sidecar` 与 `status` **正交**，第 5 轮由 lead 裁定）：
    - `stale_sidecar` = 存在**未登记段落**（`unrecorded_paragraphs` 非空）**或**
      **被改写段落**（`tampered_paragraphs` 非空）。
      `text_sha256` 与 sidecar 不符即计入 `tampered_paragraphs`——**即使差异只是
      一个标点**，也判 `stale_sidecar=True`（sha 视角下内容已变，不区分实质/格式）。
    - `status` 优先级 = `mismatch` > `stale_sidecar`（**仅在没有任何剩余可判定
      token 时**，即 `first_seen` 为空）> `no_data` > `ok`。
    - **`status == "ok"` 且 `stale_sidecar is True` 是合法组合**：数字全部命中，但
      sidecar 已不覆盖当前 docx，应重跑 `paper build`；这不是矛盾，两者回答不同问题
      （`status` 回答「数字对不对」，`stale_sidecar` 回答「校验基础是否过期」）。

    `cjk_numeral_paragraphs` 是「有中文数字但抽不到阿拉伯数字」的段落，
    只作信号（不解析中文数字）。

    异常
    ----
    FileNotFoundError : `results_dir` 不存在 / 无 CSV；显式 sidecar 不存在
    ValueError        : 显式 sidecar 读不出或结构不符
    """
    root = Path(results_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"结果目录不存在：{root}")
    result_tokens, result_files = _read_results(root)

    doc = Document(str(paper_docx))
    sidecar_path, explicit = _resolve_sidecar(paper_docx, sidecar)
    loaded = _load_sidecar(sidecar_path, strict=explicit) if sidecar_path else None
    sidecar_entries, sidecar_tables = loaded if loaded is not None else (None, None)
    sidecar_used = sidecar_entries is not None

    collected = _collect(doc, include_appendix=include_appendix,
                         sidecar_entries=sidecar_entries,
                         ignore_tokens=tuple(ignore_tokens))
    first_seen: dict[str, dict] = collected["first_seen"]
    ignored: dict[str, dict] = collected["ignored"]
    # 表格与段落共用 `first_seen` / `ignored`：一张表里编的数字和正文里编的一样要红。
    tables_report = _collect_tables(doc, sidecar_tables=sidecar_tables,
                                    ignore_tokens=tuple(ignore_tokens),
                                    first_seen=first_seen, ignored=ignored)
    collected["excluded"]["table_registered"] = tables_report["registered_tables"]
    collected["excluded"]["table_format_only"] = len(tables_report["format_changed_tables"])

    loose_result = {_loose(token) for token in result_tokens}
    matched = 0
    loose_matched = 0
    unmatched: list[dict] = []
    for token, where in first_seen.items():
        if token in result_tokens:
            matched += 1
        elif _loose(token) in loose_result:   # 只差一个百分号：报告但不阻断
            loose_matched += 1
        else:
            unmatched.append({"token": token, **where})

    unrecorded_paragraphs = collected["unrecorded_paragraphs"]
    tampered_paragraphs = collected["tampered_paragraphs"]
    stale_sidecar = bool(unrecorded_paragraphs or tampered_paragraphs)
    if unmatched:                                   # 1) 有编造/对不上的数字
        status, consistent = "mismatch", False
    elif stale_sidecar and not first_seen:
        # 2) 校验基础已过期（有未登记/被改写段落），且没有留下任何可判定的数字。
        #    不能报 no_data（会误导为「没东西可查」），更不能报 ok。
        #    注意：若过期段落里的数字有出处（first_seen 非空），仍判 ok——见第 4 轮
        #    用例「就地改写占位段且数字有出处则ok」，证明修法不是永远红灯。
        status, consistent = "stale_sidecar", None
    elif (collected["checked_paragraphs"] == 0
          and tables_report["checked_tables"] == 0) or not first_seen:
        # 3) 「没东西可校验」不是「校验通过」：consistent 必须是 None，不许 True。
        #    段落与表格**都**没参与校验才算没东西可查（只看段落会让「表格里有数且
        #    全命中」的论文被误判 no_data）。
        status, consistent = "no_data", None
    else:
        status, consistent = "ok", True

    return {
        "status": status,
        "consistent": consistent,
        "paper_numbers": len(first_seen),
        "result_numbers": len(result_tokens),
        "matched": matched,
        "unmatched": unmatched,
        "loose_matched": loose_matched,
        "checked_paragraphs": collected["checked_paragraphs"],
        "excluded": collected["excluded"],
        "ignored": [{"token": token, **where} for token, where in ignored.items()],
        "stale_sidecar": stale_sidecar,
        "unrecorded_paragraphs": list(unrecorded_paragraphs),
        "tampered_paragraphs": list(tampered_paragraphs),
        "cjk_numeral_paragraphs": list(collected["cjk_numeral_paragraphs"]),
        "legacy_sidecar": collected["legacy_sidecar"],
        "appendix_skipped": collected["appendix_skipped"],
        "sidecar_used": sidecar_used,
        "results_files": result_files,
        "checked_tables": tables_report["checked_tables"],
        "registered_tables": tables_report["registered_tables"],
        "tampered_tables": list(tables_report["tampered_tables"]),
        "table_cells_checked": tables_report["table_cells_checked"],
        "format_changed_tables": list(tables_report["format_changed_tables"]),
    }
