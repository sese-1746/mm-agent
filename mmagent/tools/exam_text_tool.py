"""FR-4 真题文本抽取（甲方案）：PDF 文本层 → 纯文本，落地并回写档案元数据。

**范围**：只处理 `artifacts/exam_corpus/manifest.json` 里 `kind='exam_problem'` 的 10 份真题。
论文（23 条 / 1301 页 JPEG）**没有文本层**，需 OCR，本轮不做 —— 被显式跳过并计数
（`skipped_papers`），不是静默略过。

**纯确定性、零 LLM、零联网**：本模块只调用 `pypdf` 读本地 PDF，不 import 任何网络/模型客户端
（`cli netguard-check` 会判违规）。

## 两种失败必须分开标（否则就是「空文本冒充成功」）

1. `text_layer_missing`：`chars < pages × min_chars_per_page`（默认 200）→ 该 PDF 大概率是
   扫描件 / 纯图，一个字都没抽出来；
2. `garbled`：`chars > 0` 但 **CJK 占比 < 0.10** → 字体映射失败，抽出来的是一串拉丁乱码。

两者都逐份回显在 `items[]` 里，也写进 `text/manifest.json`；`ok` 只数「既没 error、
也没这两个标记」的条目。**两个标记互斥**（`text_layer_missing` 优先：没有文本层是更根本的
诊断，此时不再重复报乱码），故四个桶构成划分：

    ok + text_layer_missing + garbled + failed == total

（`over_cleaned` 是正交信息项，可与 `ok` 重叠，不参与该等式。）

## 清洗只做四件事（多一件都是编造）

- 去页眉页脚：逐页**首个/末个非空行**若为纯页码（`^\\s*\\d+\\s*$`），或在**别的页面的同一位置**
  重复出现 → 剔；同一页的首/末行**永不互比**（否则镜像版式会把整篇删空）；
- 合并硬换行：中文行尾（`[\\u4e00-\\u9fff]`）直接接中文行首 → 空串连接，其余 → 一个空格；
- 压缩连续空白：水平空白 ≥2 → 1；空行 → 段分隔（保留单个换行）；
- 段落分隔：页面之间用单个换行隔开。

**不做**：公式还原、表格重建、术语纠错、任何润色。抽不出来就是抽不出来，交给标记说。

**洗过头就退回原文**：把原文洗成空，或在够长的文档（≥200 字符）上保留量不足一半 →
用未清洗原文，并置 `over_cleaned=True`。宁可脏，不许空。

## 落地口径

- 文本：`artifacts/exam_corpus/text/<archive_id>.txt`，UTF-8 **无 BOM**、**LF**（按字节写，
  避免 Windows 下隐式 CRLF 破坏幂等）；
- 索引：`artifacts/exam_corpus/text/manifest.json`，`{archive_id: {pages, chars,
  sha256_of_text, text_layer_missing, garbled, over_cleaned, extracted_at}}`
  （键排序，只有 `extracted_at` 会变）；
- 元数据：写 `archive_item.extra` 的 `text_path`（**不新增表、不改 `schema.sql`**，
  `archive_item` 也确实没有文本列）。`extra` 里其它键（附件清单等）原样保留。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

# 仓库根：`mmagent/tools/exam_text_tool.py` → parents[2]
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = DEFAULT_ROOT / "artifacts" / "exam_corpus" / "manifest.json"
DEFAULT_OUT_DIR = DEFAULT_ROOT / "artifacts" / "exam_corpus" / "text"

# 只抽真题；论文（扫描页图）本轮明不做，走 `skipped_papers` 计数
TARGET_KIND = "exam_problem"
DEFAULT_MIN_CHARS_PER_PAGE = 200
# CJK 占比低于此值判「乱码」（字体映射失败）：真实中文 PDF 实测 0.49~0.82
MIN_CJK_RATIO = 0.10
# 清洗后保留量不得低于原文的这个比例，否则判「洗过头」并退回原文（宁可脏，不许空）。
# 比例只对**够长**的文本有意义：两页短文档删掉两行页眉就过半，按比例会误判，
# 故低于 `CLEAN_GUARD_MIN_CHARS` 只做「洗成空」兜底。
CLEAN_KEEP_RATIO = 0.5
CLEAN_GUARD_MIN_CHARS = 200

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PAGE_NO_RE = re.compile(r"^\s*\d+\s*$")
_HSPACE_RE = re.compile(r"[ \t\u3000\x0b\x0c]+")
_BLANK_LINES_RE = re.compile(r"\n{2,}")


# ---------------------------------------------------------------- 清洗（只四件事）

def _join_lines(parts: list[str]) -> str:
    """合并一段内的硬换行：中文接中文 → 直接相连，其余 → 一个空格。"""
    text = parts[0]
    for nxt in parts[1:]:
        glue = "" if (_CJK_RE.match(text[-1]) and _CJK_RE.match(nxt[0])) else " "
        text += glue + nxt
    return text


def _edge_indexes(lines: list[str], skip: set[int]) -> tuple[int | None, int | None]:
    """当前页**未剔除的**首个/末个非空行号（`skip` 里的行视为已删）。"""
    idxs = [i for i, line in enumerate(lines) if line.strip() and i not in skip]
    if not idxs:
        return None, None
    return idxs[0], idxs[-1]


def _dropped_lines(lines_per_page: list[list[str]]) -> list[set[int]]:
    """逐页算出「页眉页脚」行号：纯页码，或在多页的**同一位置**重复出现。

    分两阶段：先剔纯页码，再在**剩下的**首/末行里找跨页重复行 —— 真实真题的版式是
    「页码在最上，标题行在它下面」，一步算边缘会把标题行漏掉。

    三条护栏（验收 `gk-a-r29` 抓到过缺前两条时的整篇清空）：
    - 只看**首个/末个非空行**（页眉页脚的定义位置），页面中部的重复句不动；
    - **跨页且同位置**才算重复：首行只跟别页首行比、末行只跟别页末行比；
      同一页自己的首末行**永不互比** —— 否则「标题/正文」与「正文/标题」两页会互相判重、
      双双删除，整篇被洗成空文本；
    - 一页只剩一行时**什么都不删**：那行就是整页内容。
    """
    dropped: list[set[int]] = [set() for _ in lines_per_page]
    for page_no, lines in enumerate(lines_per_page):
        non_empty = [i for i, line in enumerate(lines) if line.strip()]
        if len(non_empty) <= 1:
            continue
        for i in (non_empty[0], non_empty[-1]):
            if _PAGE_NO_RE.match(lines[i].strip()):
                dropped[page_no].add(i)

    edges = [_edge_indexes(lines, dropped[p])
             for p, lines in enumerate(lines_per_page)]
    first_seen: Counter[str] = Counter()
    last_seen: Counter[str] = Counter()
    for lines, (first, last) in zip(lines_per_page, edges):
        if first is None:
            continue
        first_seen[lines[first].strip()] += 1
        if last is not None and last != first:      # 同页首末同一行 → 不参与互比
            last_seen[lines[last].strip()] += 1

    for page_no, lines in enumerate(lines_per_page):
        first, last = edges[page_no]
        if first is None:
            continue
        remaining = [line for i, line in enumerate(lines)
                     if line.strip() and i not in dropped[page_no]]
        if len(remaining) <= 1:
            continue                       # 只剩一行 = 整页内容，不能当页眉删
        if first_seen[lines[first].strip()] >= 2:
            dropped[page_no].add(first)
        if last is not None and last != first and last_seen[lines[last].strip()] >= 2:
            dropped[page_no].add(last)
    return dropped


def _raw_text(pages: list[str]) -> str:
    """未清洗原文（逐行 rstrip，页面之间一个换行）—— over-cleaned 时的兜底文本。"""
    lines = [line.rstrip() for page in pages for line in (page or "").splitlines()]
    text = "\n".join(lines).strip()
    return (text + "\n") if text else ""


def _clean_strict(pages: list[str]) -> str:
    """四件事的严格版本（可能被过度删除，由 `_clean` 兜底）。"""
    lines_per_page = [[line.rstrip() for line in (page or "").splitlines()]
                      for page in pages]
    dropped = _dropped_lines(lines_per_page)

    page_texts: list[str] = []
    for page_no, lines in enumerate(lines_per_page):
        paragraphs: list[list[str]] = []
        current: list[str] = []
        for i, line in enumerate(lines):
            if i in dropped[page_no]:
                continue
            text = line.strip()
            if not text:                       # 空行 = 段落分隔
                if current:
                    paragraphs.append(current)
                    current = []
                continue
            current.append(text)
        if current:
            paragraphs.append(current)
        page_texts.append("\n".join(_join_lines(p) for p in paragraphs))

    text = "\n".join(t for t in page_texts if t)
    text = _HSPACE_RE.sub(" ", text)                       # 水平连续空白 → 1
    text = _BLANK_LINES_RE.sub("\n", text)                 # 空行只保留一个（段分隔）
    text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
    return (text + "\n") if text else ""


def _solid_len(text: str) -> int:
    """**非空白字符**数（第 27 轮修 over_cleaned 误判，D-88）。

    为什么不能用 `len(text)` 比：`_clean_strict` 四件事里的「压缩连续空白」会把
    `符号␣␣␣含义` 从 8 字符压成 3 字符 —— **一个字都没删，长度却掉一半以上**。
    用长度比会把这个当成「洗过头」，于是宽间距版式（对齐的参数表、目录页、
    点线引导）**必然误判**：第 27 轮实测「5 列 × 40 空格间隔」的表，
    `raw=5160 → 清洗后约 360`、而 `solid 320 → 320`（非空白字符一个没少），
    却被标成 `over_cleaned=True` 并退回未清洗原文（页眉页脚因此没剔掉）。

    改用非空白字符数后，判据回答的是它本来该回答的问题：
    **「有没有真的删掉内容」**，而不是「空白变少了」。
    """
    return len("".join(text.split()))


def _clean(pages: list[str]) -> tuple[str, bool]:
    """→ `(文本, over_cleaned)`；清洗把内容删过头时**退回原文**并标 `over_cleaned`。

    「宁可脏，不许空」：清洗只是保守的版式修正，一旦把原文洗成空、或在**够长**的文档上
    保留量不足原文的一半（`CLEAN_GUARD_MIN_CHARS` 以上才按比例判），说明页眉页脚规则
    误伤了正文 —— 此时用未清洗原文，并让调用方把 `over_cleaned` 显式带出去，
    而不是交一份空文本还报成功。

    比例按**非空白字符**算（`_solid_len`）：压缩空白不是删内容，按字符长度比会把
    宽间距版式全判成洗过头（见 `_solid_len` 的实测数据）。
    """
    text = _clean_strict(pages)
    raw = _raw_text(pages)
    if not raw.strip():
        return "", False                       # 原文本来就没有字：不是「洗没了」
    if not text.strip():
        return raw, True
    raw_solid = _solid_len(raw)
    if (raw_solid >= CLEAN_GUARD_MIN_CHARS
            and _solid_len(text) < raw_solid * CLEAN_KEEP_RATIO):
        return raw, True
    return text, False


def clean_pages(pages: list[str]) -> str:
    """逐页文本 → 清洗后的全文（规则见模块 docstring，**只做四件事**）。

    内容被删过头时返回**未清洗原文**（见 `_clean`）；需要知道是否发生了这种回退，
    用 `extract_one()` 返回的 `over_cleaned`。
    """
    return _clean(pages)[0]


# ---------------------------------------------------------------- 单份抽取

def _failed(error: str, *, pages: int = 0) -> dict:
    """失败条目：`error` 非空、两个标记为 False。

    **刻意不把失败折进 `text_layer_missing`**：读不出文件和「文件能读但没文本层」是两种事实，
    混在一起会让「PDF 损坏」看起来像「扫描件」。失败由调用方的 `failed` 计数单独统计。
    """
    return {"pages": pages, "chars": 0, "text": "", "text_layer_missing": False,
            "garbled": False, "cjk_ratio": 0.0, "raw_chars": 0, "over_cleaned": False,
            "error": error}


def extract_one(pdf_path, *, min_chars_per_page: int = DEFAULT_MIN_CHARS_PER_PAGE) -> dict:
    """抽一份 PDF → `{pages, chars, text, text_layer_missing, garbled, error, ...}`。

    - `pages` / `chars` / `text` 是清洗后的口径（`chars == len(text)`）；`raw_chars` 是清洗前；
    - `text_layer_missing`：`chars < pages × min_chars_per_page`（无文本层/扫描件）；
    - `garbled`：**未**判 `text_layer_missing` 且 `chars > 0` 且 CJK 占比 `< MIN_CJK_RATIO`
      （字体映射失败）。两个标记**互斥**：没有文本层是更根本的诊断，此时不再重复报乱码，
      否则下游照「四桶守恒」校验会算出 `sum > total`（`gk-a-r29` 实测过）；
    - `over_cleaned`：清洗误伤过头、已退回原文（见 `_clean`）；
    - 单份失败**不抛**（整批不能被一份坏文件带崩），`error` 里带原因、`pages=0`。
    """
    path = Path(pdf_path)
    if not path.is_file():
        return _failed(f"file_not_found: {path}")
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:                    # noqa: BLE001 —— 单份失败不得炸整批
        return _failed(f"pdf_read_error: {type(exc).__name__}: {exc}")
    if not pages:
        return _failed("no_pages", pages=0)

    raw_chars = sum(len(page) for page in pages)
    text, over_cleaned = _clean(pages)
    chars = len(text)
    cjk = len(_CJK_RE.findall(text))
    ratio = (cjk / chars) if chars else 0.0
    missing = chars < len(pages) * min_chars_per_page
    return {
        "pages": len(pages),
        "chars": chars,
        "text": text,
        "text_layer_missing": missing,
        "garbled": (not missing) and bool(chars) and ratio < MIN_CJK_RATIO,
        "cjk_ratio": round(ratio, 4),
        "raw_chars": raw_chars,
        "over_cleaned": over_cleaned,
        "error": None,
    }


# ---------------------------------------------------------------- 落地与回写

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _posix_path(path: Path, root: Path) -> str:
    """仓库根内 → POSIX 相对路径；根外 → 绝对路径（不编造相对路径）。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _write_text_file(path: Path, text: str) -> None:
    """按字节写 UTF-8 无 BOM、LF —— 避免 Windows 的 `write_text` 把 `\\n` 变成 `\\r\\n`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _write_text_manifest(out_dir: Path, entries: dict[str, dict]) -> Path:
    """写 `text/manifest.json`（键排序 → 除 `extracted_at` 外重复运行字节相同）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "manifest.json"
    payload = json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text_file(target, payload)
    return target


def _update_extra_text_path(db_path, archive_id: str, text_path: str) -> str:
    """把 `text_path` 写进 `archive_item.extra`（保留其它键）→ `updated|unchanged|missing`。

    `missing`：库里没有这个 `archive_id` —— **不插入**伪造行（行是入库阶段的事实），
    由调用方计入 `db_missing` 让读者看见。
    """
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT extra FROM archive_item WHERE archive_id = ?", (archive_id,)).fetchone()
        if row is None:
            return "missing"
        try:
            extra = json.loads(row["extra"] or "{}")
        except (json.JSONDecodeError, TypeError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        if extra.get("text_path") == text_path:
            return "unchanged"
        extra["text_path"] = text_path
        conn.execute(
            "UPDATE archive_item SET extra = ? WHERE archive_id = ?",
            (json.dumps(extra, ensure_ascii=False), archive_id))
        conn.commit()
        return "updated"
    finally:
        conn.close()


# ---------------------------------------------------------------- 批量

def _load_targets(manifest_path: Path) -> tuple[list[dict], int]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"manifest 的 items 必须是数组：{manifest_path}")
    targets = [it for it in items if isinstance(it, dict) and it.get("kind") == TARGET_KIND]
    skipped = len(items) - len(targets)
    for item in targets:
        archive_id = item.get("archive_id")
        if not isinstance(archive_id, str) or not archive_id.strip():
            raise ValueError(f"manifest 里有真题条目缺 archive_id：{item!r}")
        if not isinstance(item.get("local_path"), str) or not item["local_path"].strip():
            raise ValueError(f"archive_id={archive_id!r}：缺少 local_path")
    return targets, skipped


def extract_all(manifest_path=DEFAULT_MANIFEST, out_dir=DEFAULT_OUT_DIR, *,
                db_path=DEFAULT_DB_PATH, dry_run: bool = False,
                root=None, min_chars_per_page: int = DEFAULT_MIN_CHARS_PER_PAGE) -> dict:
    """10 份真题批量抽取 → 写 txt + `text/manifest.json` + `archive_item.extra.text_path`。

    返回：
    ```
    {"total", "ok", "text_layer_missing", "garbled", "failed", "result_chars",
     "items": [extract_one 的结果 + archive_id + text_path + sha256_of_text],
     "skipped_papers",            # manifest 里被跳过的论文条数（本轮明不做）
     "over_cleaned",              # 清洗误伤→退回原文的条数（信息项，可与其它桶重叠）
     "db_updated", "db_unchanged", "db_missing",
     "text_manifest_path", "out_dir", "dry_run"}
    ```
    四个计数**互斥**（`garbled` 在 `text_layer_missing` 为真时不再计），
    故 `ok + text_layer_missing + garbled + failed == total` 是恒等式；
    `over_cleaned` 是**正交的信息项**（一份可以既 over_cleaned 又 ok），不参与该等式。
    `result_chars` 是**成功抽出**（`error is None`）条目的字符数合计 —— 含被标记的条目，
    因为「抽出来了但乱码」也是字符，不能靠这个数暗示质量。

    `dry_run=True`：照常抽取与判定，但**不写任何文件、不碰库**（用于先看结果再落地）。
    """
    manifest = Path(manifest_path)
    root_path = Path(root) if root is not None else DEFAULT_ROOT
    output = Path(out_dir)
    targets, skipped_papers = _load_targets(manifest)

    items: list[dict[str, Any]] = []
    entries: dict[str, dict] = {}
    db_counter = Counter()
    result_chars = 0
    for item in targets:
        archive_id = str(item["archive_id"]).strip()
        pdf_path = root_path / str(item["local_path"]).strip()
        result = extract_one(pdf_path, min_chars_per_page=min_chars_per_page)
        result["archive_id"] = archive_id
        if result["error"] is None:
            result_chars += result["chars"]
            text_file = output / f"{archive_id}.txt"
            text_path = _posix_path(text_file, root_path)
            result["text_path"] = text_path
            result["sha256_of_text"] = _sha256_text(result["text"])
            entries[archive_id] = {
                "pages": result["pages"],
                "chars": result["chars"],
                "sha256_of_text": result["sha256_of_text"],
                "text_layer_missing": result["text_layer_missing"],
                "garbled": result["garbled"],
                "over_cleaned": result["over_cleaned"],
                "extracted_at": utcnow(),
            }
            if not dry_run:
                _write_text_file(text_file, result["text"])
                db_counter[_update_extra_text_path(db_path, archive_id, text_path)] += 1
        else:
            result["text_path"] = None
            result["sha256_of_text"] = None
        items.append(result)

    text_manifest_path = None
    if not dry_run:
        text_manifest_path = _posix_path(
            _write_text_manifest(output, entries), root_path)

    ok = sum(1 for r in items
             if r["error"] is None and not r["text_layer_missing"] and not r["garbled"])
    return {
        "total": len(items),
        "ok": ok,
        "text_layer_missing": sum(1 for r in items
                                  if r["error"] is None and r["text_layer_missing"]),
        "garbled": sum(1 for r in items
                       if r["error"] is None and r["garbled"]),
        "failed": sum(1 for r in items if r["error"] is not None),
        "result_chars": result_chars,
        "items": items,
        "skipped_papers": skipped_papers,
        "over_cleaned": sum(1 for r in items
                            if r["error"] is None and r["over_cleaned"]),
        "db_updated": db_counter["updated"],
        "db_unchanged": db_counter["unchanged"],
        "db_missing": db_counter["missing"],
        "text_manifest_path": text_manifest_path,
        "out_dir": _posix_path(output, root_path),
        "dry_run": bool(dry_run),
    }
