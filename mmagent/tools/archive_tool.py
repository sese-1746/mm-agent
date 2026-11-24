"""FR-14 档案库（历年真题 + 官方论文）：manifest 入库、检索、素材定位。

**分层与职责边界**（与 `store/archive.py` 的模块 docstring 同一口径）：

- `store/archive.py`：只写 SQL（upsert / list / summary / get），**不读磁盘**；
- 本模块：读 manifest、校验语义与路径合法性、**现算** `file_present`、
  把 `extra` 在 JSON 文本与 dict 之间来回转、按 `archive_id` 解析出磁盘路径。

**零网络**：素材已由抓取阶段落在 `artifacts/exam_corpus/`（目录合计 **892,194,088 B =
850.9 MiB**（`du -sh` 854M）；其中 manifest 覆盖的 1380 个素材文件 **733,637,993 B**，
官方原始 zip 包 `_bundle/` 另有 157,634,704 B），产品侧只读本地文件。本文件**不得** import
`urllib` / `socket` / `httpx` / `requests` / `ftplib` / `http` —— 违反会被
`python -m mmagent.cli netguard-check` 判违规。

## 路径安全口径

`local_path` 只接受**相对仓库根、POSIX 分隔**的路径；`..` 段 / 盘符 / 绝对路径一律
`ValueError`。`resolve_asset` 只使用**库里的** `local_path`（调用方无法传路径进来），
因此目录穿越在「入库」与「读盘」两侧各挡一次。

## 哈希口径（与 manifest 生成侧一致，已用真实素材逐条核验）

- 真题：单个 PDF 的 `sha256(文件字节)`；
- 论文：按页序取 `sha256(每页字节)` 的十六进制串，`"\n".join(...)` 后再取 `sha256`；
- **附件**（`extra.attachments[]`）：逐项 `sha256(文件字节)` 与 manifest 的 `sha256` 比对，
  条目写作 `<archive_id>::<附件 name>`。`extra.skipped_attachments[]`（按设计没抓的媒体）
  没有 sha256，**不校验**，但会在返回值里报 `skipped_attachment_count`。

只校验主体文件会造成**假绿灯**：69 个附件全丢时 `sha_mismatch` 仍是空数组。
故 `verify_sha` 必须覆盖附件；附件缺失则无条件进 `attachment_missing`。

`verify_sha=True` 才重算哈希（33 条主体文件 + 69 个附件，约数秒）；不一致**收集进
`sha_mismatch`**，不静默通过。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from ..store import archive as archive_store
from ..store.db import DEFAULT_DB_PATH

# 仓库根：`mmagent/tools/archive_tool.py` → parents[2]
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = DEFAULT_ROOT / "artifacts" / "exam_corpus" / "manifest.json"

SCHEMA_VERSION = 1

# manifest 每条 item 的**必需**字段（`extra` / `page_count` 见下，按 kind 分别校验）
_REQUIRED_STR_FIELDS = ("archive_id", "local_path", "sha256", "fetched_at")

# 盘符前缀（`C:` / `C:/x` / `C:x` 都要挡；POSIX 段里不会出现冒号结尾的首段）
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


# ---------------------------------------------------------------- 路径与哈希

def _check_local_path(local_path, archive_id) -> str:
    """校验 `local_path` 是「相对仓库根 + POSIX 分隔 + 无 `..` + 无盘符」。

    四种典型穿越写法（`../etc/passwd`、`/etc/passwd`、`C:/x`、`a/../../b`）都必须被挡，
    否则 manifest 一旦被改，`/archive/file/{id}` 就会变成任意文件读取。
    """
    if not isinstance(local_path, str) or not local_path.strip():
        raise ValueError(f"archive_id={archive_id!r}：local_path 必须是非空字符串")
    text = local_path.strip()
    if "\\" in text:
        raise ValueError(
            f"archive_id={archive_id!r}：local_path 必须用 POSIX 分隔符 '/'，收到 {text!r}")
    if _DRIVE_RE.match(text) or (PurePosixPath(text).parts
                                 and PurePosixPath(text).parts[0].endswith(":")):
        raise ValueError(f"archive_id={archive_id!r}：local_path 不得含盘符，收到 {text!r}")
    if text.startswith("/"):
        raise ValueError(f"archive_id={archive_id!r}：local_path 不得是绝对路径，收到 {text!r}")
    parts = PurePosixPath(text).parts
    if not parts:
        raise ValueError(f"archive_id={archive_id!r}：local_path 为空路径")
    if any(part == ".." for part in parts):
        raise ValueError(f"archive_id={archive_id!r}：local_path 不得含 '..' 段，收到 {text!r}")
    return text


def _is_safe_rel(text) -> bool:
    """`_check_local_path` 的非抛版本（读写两侧共用同一套规则，不复制第二份判断）。"""
    try:
        _check_local_path(text, "?")
    except ValueError:
        return False
    return True


def _check_page_file(name, owner) -> str:
    """论文**页文件名**必须是单段名字（不许含分隔符 / 盘符 / `..`）。

    `extra.pages[].file` 与 `local_path` 是**两条独立入口**：后者被 `_check_local_path`
    挡了四种穿越写法，前者却一直被直接 `target / name` 拼路径 —— 等于开了一个
    「manifest 里一行改成 `../../../../README.md` 就能读任意文件」的面。
    实测该字段改成 `../../../../README.md` 后 `/archive/page/<id>/1` 真返回了 README 前几个字节。
    故与 `local_path` 同等待遇：入库时抛错（exit 2），读库时再校验一次（防有人直接改库）。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{owner}：页文件名必须是非空字符串，收到 {name!r}")
    text = name.strip()
    if "/" in text or "\\" in text:
        raise ValueError(f"{owner}：页文件名不得含路径分隔符，收到 {text!r}")
    if ":" in text or _DRIVE_RE.match(text):
        raise ValueError(f"{owner}：页文件名不得含冒号/盘符，收到 {text!r}")
    if text in (".", ".."):
        raise ValueError(f"{owner}：页文件名不得是 '.' 或 '..'，收到 {text!r}")
    return text


def _declared_page_files(item: dict) -> list[str]:
    """`extra.pages[].file` 的原始声明值（**未校验**，给校验侧用）。"""
    extra = parse_extra(item.get("extra"))
    pages = extra.get("pages") if isinstance(extra, dict) else None
    if not isinstance(pages, list):
        return []
    names: list[str] = []
    for page in pages:
        if isinstance(page, dict) and page.get("file") is not None:
            names.append(page.get("file"))
    return names


def _sha256_file(path: Path) -> str:
    """分块算文件 sha256（论文单页可达数百 KB，真题 PDF 数 MB，不必整块读）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- 附件（extra.attachments）

def _attachments_of(item: dict) -> list[dict]:
    """取 `extra.attachments`（条目既可来自 manifest 的 dict，也可来自库里的 JSON 文本）。"""
    extra = parse_extra(item.get("extra"))
    if not isinstance(extra, dict):
        return []
    atts = extra.get("attachments")
    if not isinstance(atts, list):
        return []
    return [a for a in atts if isinstance(a, dict) and str(a.get("name") or "").strip()]


def _skipped_count_of(item: dict) -> int:
    """`extra.skipped_attachments` 的条目数（**按设计没抓**的媒体，无 sha256，不参与校验）。"""
    extra = parse_extra(item.get("extra"))
    if not isinstance(extra, dict):
        return 0
    skipped = extra.get("skipped_attachments")
    return len(skipped) if isinstance(skipped, list) else 0


def _attachment_status(item: dict, root: Path) -> tuple[int, list[str], list[str]]:
    """→ `(附件总项数, 缺失附件名列表, 路径非法附件名列表)`。

    「缺失」与「路径非法」分开：前者是磁盘事实，后者是 manifest 被改过（不该静默计入缺失，
    但也**不能**去读越界路径 —— 所以既不算存在，也要能被调用方看见）。
    """
    attachments = _attachments_of(item)
    missing: list[str] = []
    unsafe: list[str] = []
    for att in attachments:
        name = str(att.get("name")).strip()
        rel = att.get("local_path")
        if not isinstance(rel, str) or not rel.strip():
            missing.append(name)          # 没有落盘路径 = 这份附件本来就没有文件
        elif not _is_safe_rel(rel):
            unsafe.append(name)           # 路径越界：不读，也不当「缺失」蒙混过去
        elif not (root / rel.strip()).exists():
            missing.append(name)
    return len(attachments), missing, unsafe


def _paper_page_files(item: dict, target: Path) -> list[Path]:
    """论文页图按**页序**返回；优先用 `extra.pages`（带 `page` 与 `file`），退回 `p*.jpg` 字典序。

    真实 manifest 两种信息都有；`extra.pages` 是权威顺序（`p001.jpg` 零填充恰好与字典序
    一致，但换一篇非零填充的素材就会错，故不拿字典序当第一口径）。
    """
    extra = item.get("extra")
    pages = extra.get("pages") if isinstance(extra, dict) else None
    if isinstance(pages, list) and pages:
        ordered: list[tuple[int, str]] = []
        for page in pages:
            if not isinstance(page, dict) or not page.get("file"):
                continue
            try:
                # 非法页名不参与哈希计算（不读它）；缺失由 `_paper_page_status` 报
                name = _check_page_file(page["file"], "页文件")
            except ValueError:
                continue
            try:
                index = int(page.get("page") or 0)
            except (TypeError, ValueError):
                index = 0
            ordered.append((index, name))
        ordered.sort(key=lambda pair: pair[0])
        if ordered:
            return [target / name for _idx, name in ordered]
    return sorted(target.glob("p*.jpg"))


def _main_present(kind: str, target: Path) -> bool:
    """主体在不在：真题必须是**文件**，论文必须是**目录**。

    不用 `exists()`：`local_path` 位置上若被一个同名目录（或同名文件）占住，`exists()`
    仍返回 True，但 `_computed_sha` 算不出哈希 → 又一处「看起来在、其实读不出」。
    按 kind 收紧到具体类型，让「类型不对」落进 `file_missing`。
    """
    return target.is_dir() if kind == "paper" else target.is_file()


def _paper_page_status(item: dict, root: Path) -> tuple[int, list[str]]:
    """论文页图口径 → `(声明页数, 缺失页文件名列表)`；非论文 / 目录整体缺失 → `(0, [])`。

    **为什么必须单独一层**（2026-10-19 实证的洞）：论文目录在、少了一页时，
    `_computed_sha` 见 `any(not p.is_file())` 就返回 `None`，调用方
    `if computed is not None and computed != sha` 于是**整段跳过**；而 `target.exists()`
    仍是 True，故 `file_missing=0` / `attachment_missing=0` / `sha_mismatch=0` / EXIT=0。
    实测挪走 `papers/2025/B060/p037.jpg` 后四个数全 0，只有 `/archive/page/…/37` 会 404 ——
    「缺失」必须自己有一层能报出来，不能指望别人替它报。

    目录整体缺失仍由 `file_missing` 报（此处返回 `(0, [])`），两层**不重叠**。

    页文件名**非法**（含正斜杠 / 反斜杠 / 冒号 / `..`）时按「缺失」计：路越界 = 取不到，
    不能静默当存在，也不能让 `_computed_sha` 去读它。
    """
    target = root / str(item.get("local_path") or "")
    if not target.is_dir():
        return 0, []
    extra = parse_extra(item.get("extra"))
    pages = extra.get("pages") if isinstance(extra, dict) else None
    declared: list[str] = []
    unsafe: list[str] = []
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            name = page.get("file")
            if name is None or (isinstance(name, str) and not name.strip()):
                continue
            try:
                declared.append(_check_page_file(name, "页文件"))
            except ValueError:
                unsafe.append(str(name))
    if unsafe:
        return len(declared) + len(unsafe), unsafe + [
            name for name in declared if not (target / name).is_file()]
    if not declared:
        # 没有 `extra.pages`：退回按 `page_count` 推 `pNNN.jpg`。真实 manifest 都带
        # `extra.pages`，此路只挡手工造的 manifest —— 宁可多报一条，也不要静默放过。
        try:
            count = int(item.get("page_count") or 0)
        except (TypeError, ValueError):
            return 0, []
        if count <= 0:
            return 0, []
        declared = [f"p{n:03d}.jpg" for n in range(1, count + 1)]
    return len(declared), [name for name in declared if not (target / name).is_file()]


def _computed_sha(item: dict, root: Path) -> str | None:
    """按 kind 重算 sha256；**算不出来就返回 `None`**（调用方必须把 None 当「没通过」）。

    注意：`None` 不是「缺失」的代言人。主体缺失由 `file_missing` 报、论文缺页由
    `_paper_page_status` 报；本函数只管「字节能不能算出来」。
    """
    target = root / item["local_path"]
    if item["kind"] == "exam_problem":
        return _sha256_file(target) if target.is_file() else None
    if not target.is_dir():
        return None
    files = _paper_page_files(item, target)
    if not files or any(not p.is_file() for p in files):
        return None
    per_page = [_sha256_file(p) for p in files]
    return hashlib.sha256("\n".join(per_page).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- manifest 解析

def _require_str(item: dict, key: str, index: int, archive_id) -> str:
    """取必需非空字符串字段；缺键 / 空值都抛 `ValueError` 并点名 archive_id 与字段。"""
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"manifest 第 {index} 条（archive_id={archive_id!r}）："
            f"缺少必需字段或为空：{key!r}")
    return value.strip()


def _normalize_item(item, index: int, root: Path, *,
                    verify_sha: bool) -> tuple[dict, bool, list[str], list[str], int, list[str], int]:
    """单条校验 + 归一化 →
    `(row, file_present, sha_mismatch, attachment_missing, skipped, page_missing, page_total)`。

    返回的 `sha_mismatch` 是**条目**列表：主体项为 `<archive_id>`，附件项为
    `<archive_id>::<附件 name>`（读者必须能直接看出是哪个附件不对）。
    `skipped` 是 `extra.skipped_attachments` 条数（按设计没抓的媒体，不参与校验）。
    `page_missing` 是论文**页级**缺失（`<archive_id>::p037.jpg`），`page_total` 是声明页数。
    """
    if not isinstance(item, dict):
        raise ValueError(f"manifest 第 {index} 条不是对象，收到 {type(item).__name__}")
    archive_id = item.get("archive_id")
    if not isinstance(archive_id, str) or not archive_id.strip():
        raise ValueError(f"manifest 第 {index} 条：缺少必需字段或为空：'archive_id'")
    archive_id = archive_id.strip()

    for key in _REQUIRED_STR_FIELDS:
        _require_str(item, key, index, archive_id)

    kind = item.get("kind")
    if kind not in archive_store.KINDS:
        raise ValueError(
            f"archive_id={archive_id!r}：kind 只能是 {archive_store.KINDS}，收到 {kind!r}")

    try:
        year = int(item.get("year"))
    except (TypeError, ValueError):
        raise ValueError(
            f"archive_id={archive_id!r}：year 必须是可转 int 的年份，收到 {item.get('year')!r}")
    if year <= 0:
        raise ValueError(f"archive_id={archive_id!r}：year 必须 > 0，收到 {year}")

    page_count = item.get("page_count")
    if kind == "paper":
        # 论文必须能界定翻页范围（`/archive/page/{id}/{n}` 的上界），缺了就无法判越界
        try:
            page_count = int(page_count)
        except (TypeError, ValueError):
            raise ValueError(
                f"archive_id={archive_id!r}：paper 的 page_count 必须是正整数，"
                f"收到 {item.get('page_count')!r}")
        if page_count <= 0:
            raise ValueError(
                f"archive_id={archive_id!r}：paper 的 page_count 必须 > 0，收到 {page_count}")
    elif page_count is not None:
        page_count = int(page_count)

    local_path = _check_local_path(item.get("local_path"), archive_id)

    extra_raw = item.get("extra", {})
    if extra_raw is None:
        extra_text = "{}"
    elif isinstance(extra_raw, dict):
        extra_text = json.dumps(extra_raw, ensure_ascii=False)
    elif isinstance(extra_raw, str):
        extra_text = extra_raw
    else:
        raise ValueError(
            f"archive_id={archive_id!r}：extra 必须是对象或 JSON 字符串，"
            f"收到 {type(extra_raw).__name__}")

    # 附件路径同样过穿越校验：附件是**会按路径去读**的文件，比主体更需要挡
    for att in _attachments_of({"extra": extra_raw}):
        rel = att.get("local_path")
        if rel is None or (isinstance(rel, str) and not rel.strip()):
            continue                      # 没有落盘路径 → 记「缺失」，不当穿越
        _check_local_path(rel, f"{archive_id}::{att.get('name')}")

    # 页文件名是**第二条**路径入口（拼在 `papers/<id>/` 之后），同样必须挡：
    # 不挡的话 manifest 里一行 `../../../../README.md` 就能读任意文件。
    for name in _declared_page_files({"extra": extra_raw}):
        _check_page_file(name, f"archive_id={archive_id!r} 的页文件")

    row = {
        "archive_id": archive_id,
        "kind": kind,
        "year": year,
        "problem_code": item.get("problem_code") or "",
        "tag": item.get("tag") or "",
        "title": item.get("title") or "",
        "source_url": item.get("source_url") or "",
        "asset_url": item.get("asset_url") or "",
        "local_path": local_path,
        "sha256": item["sha256"].strip(),
        "bytes": item.get("bytes"),
        "page_count": page_count,
        "fetched_at": item["fetched_at"].strip(),
        "extra": extra_text,
    }

    target = root / local_path
    file_present = _main_present(kind, target)
    # 论文**页级**缺失：目录在、少一页也要能报出来（见 `_paper_page_status` 的说明）
    page_total, page_missing_names = _paper_page_status(
        {"kind": kind, "local_path": local_path, "extra": extra_raw,
         "page_count": page_count}, root)
    page_missing = [f"{archive_id}::{name}" for name in page_missing_names]

    mismatches: list[str] = []
    if verify_sha and file_present and not page_missing:
        computed = _computed_sha({**item, "local_path": local_path}, root)
        # 不用 `computed is not None and ...`：那会把「算不出来」当成「没问题」。
        # 算不出（None）说明根本没法校验，必须与「不符」同等对待。
        if computed != row["sha256"]:
            mismatches.append(archive_id)

    _, att_missing_names, att_unsafe_names = _attachment_status(
        {"extra": extra_raw}, root)
    # 越界路径已在上面的 `_check_local_path` 抛错；这里若仍有（`extra` 为 JSON 文本的边界情形）
    # 一律按缺失处理——绝不读它
    attachment_missing = [f"{archive_id}::{name}"
                          for name in (att_missing_names + att_unsafe_names)]
    if verify_sha:
        for att in _attachments_of({"extra": extra_raw}):
            name = str(att.get("name")).strip()
            rel = att.get("local_path")
            if name in att_missing_names or name in att_unsafe_names:
                continue                  # 文件不在 → 由 attachment_missing 报，不重复算「哈希不符」
            expected = str(att.get("sha256") or "").strip()
            if not expected:
                # 没有期望值 = **没法校验**，不能当作「校验通过」
                mismatches.append(f"{archive_id}::{name}")
                continue
            if _sha256_file(root / str(rel).strip()) != expected:
                mismatches.append(f"{archive_id}::{name}")
    return (row, file_present, mismatches, attachment_missing,
            _skipped_count_of({"extra": extra_raw}), page_missing, page_total)


def ingest_manifest(manifest_path: str | Path = DEFAULT_MANIFEST, *,
                    db_path: str | Path = DEFAULT_DB_PATH,
                    root: str | Path | None = None,
                    verify_sha: bool = False) -> dict:
    """读 manifest → 校验 → `store.archive.upsert_items` 幂等落库。

    返回：

    ```
    {"manifest", "schema_version", "counts": {total, exam_problem, paper},
     "inserted", "updated",
     "file_missing": ["<archive_id>", …],              # 主体（PDF / 论文目录）缺失
     "page_missing": ["<archive_id>::p037.jpg", …],     # 论文**页级**缺失（目录在、页少了）
     "page_total": 1301,                               # 论文声明页数合计
     "attachment_missing": ["<archive_id>::<name>", …],# 附件缺失（**无论是否 --verify-sha 都查**）
     "sha_mismatch": ["<archive_id>", "<archive_id>::<name>", …],  # 主体项无后缀、附件项带 ::name
     "skipped_attachment_count": 35,                   # 按设计没抓的媒体（不校验，但要能看见）
     "verified_sha": bool}
    ```

    **为什么 `--verify-sha` 必须覆盖附件**：只看主体时，69 个附件（合计 498,981,810 B =
    475.9 MiB，占素材文件 68%）全丢仍会
    得到 `sha_mismatch=[]`，读者会把「没校验」读成「校验通过」——这正是假绿灯。

    **为什么还要单独一层 `page_missing`**：论文 1301 页里少一页，目录仍在 → `file_missing`
    不响、附件不响、`_computed_sha` 直接返回 None 于是 `sha_mismatch` 也不响。三处全 0
    而其实素材是残的 —— 缺页必须自己有一层能报出来。

    `file_present` **只出现在返回值里**（返回体不含逐条明细，故这里只汇总缺失 id），
    库表没有该列：磁盘状态是「现在」的事实，写进库会立刻过期。
    """
    path = Path(manifest_path)
    root_path = Path(root) if root is not None else DEFAULT_ROOT
    data = json.loads(path.read_text(encoding="utf-8"))

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"manifest schema_version 必须为 {SCHEMA_VERSION}，收到 {version!r}：{path}")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"manifest 的 items 必须是非空数组：{path}")

    rows: list[dict] = []
    file_missing: list[str] = []
    page_missing: list[str] = []
    attachment_missing: list[str] = []
    sha_mismatch: list[str] = []
    skipped_total = 0
    page_total = 0
    for index, item in enumerate(items):
        (row, file_present, mismatches, att_missing, skipped,
         page_miss, pages) = _normalize_item(
            item, index, root_path, verify_sha=verify_sha)
        rows.append(row)
        if not file_present:
            file_missing.append(row["archive_id"])
        page_missing.extend(page_miss)
        page_total += pages
        attachment_missing.extend(att_missing)
        sha_mismatch.extend(mismatches)
        skipped_total += skipped

    # archive_id 重复由 store 层判（唯一真源，避免两处口径）；此处先落库再取计数
    result = archive_store.upsert_items(rows, db_path=db_path)

    counts = {"total": len(rows), "exam_problem": 0, "paper": 0}
    for row in rows:
        counts[row["kind"]] += 1

    return {
        "manifest": str(path),
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        "inserted": result["inserted"],
        "updated": result["updated"],
        "file_missing": file_missing,
        "page_missing": page_missing,
        "page_total": page_total,
        "attachment_missing": attachment_missing,
        "sha_mismatch": sha_mismatch,
        "skipped_attachment_count": skipped_total,
        "verified_sha": bool(verify_sha),
    }


# ---------------------------------------------------------------- 检索

def parse_extra(raw):
    """库里的 `extra` 文本 → dict；解析失败 / 非对象时**原样返回**（不抛、不吞成 `{}`）。"""
    if isinstance(raw, (dict, list)) or raw is None:
        return raw if raw is not None else {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    return parsed


def _row_with_presence(row: dict, root: Path) -> dict:
    """给一行补 `file_present`（主体，**现算**）+ `attachments_missing` + `pages_missing`。

    - `attachments_missing` 恒为 `int`（无附件 / 都齐 → `0`）：前端要拿它做 `> 0` 判断，
      `None` 会让 `null > 0` 在某些模板里静默变 false；
    - `pages_missing` 同理恒为 `int`（非论文 / 目录缺失 / 页齐 → `0`）：论文「目录在、少页」
      是 `file_present` 看不见的一层，必须让调用方有数可查。

    `extra.pages`（每篇 60~70 条逐页清单）**不下发**：33 行原样下发约 371 KB/请求（gk-r27 实测
    `?limit=200` 响应 371,964 B），而逐页浏览只走 `/archive/page/{id}/{n}`，列表侧不需要它。
    去掉后保留 `pages_omitted`（条数）以免读者以为「这篇没有页」。
    """
    out = dict(row)
    local_path = out.get("local_path") or ""
    out["file_present"] = bool(local_path) and _main_present(
        out.get("kind"), root / local_path)
    full_extra = parse_extra(out.get("extra"))
    # 页级判定必须用**完整** extra（含 `pages` 的逐页文件名），否则会退回按 page_count
    # 猜 `pNNN.jpg`，对非零填充命名的素材会误报缺失。故先算，再瘦身。
    _, page_missing_names = _paper_page_status(
        {"kind": out.get("kind"), "local_path": local_path, "extra": full_extra,
         "page_count": out.get("page_count")}, root)
    out["pages_missing"] = len(page_missing_names)
    _, missing, unsafe = _attachment_status({"extra": full_extra}, root)
    out["attachments_missing"] = len(missing) + len(unsafe)
    extra = full_extra
    if isinstance(extra, dict) and isinstance(extra.get("pages"), list):
        count = len(extra["pages"])
        extra = {k: v for k, v in extra.items() if k != "pages"}
        extra["pages_omitted"] = count
    out["extra"] = extra
    return out


def list_archives(*, year: int | None = None, kind: str | None = None,
                  problem_code: str | None = None, q: str | None = None,
                  limit: int = 200, offset: int = 0,
                  db_path: str | Path = DEFAULT_DB_PATH,
                  root: str | Path | None = None) -> dict:
    """过滤 + 分页检索；返回 `store.archive.list_items` 的三键，每行多
    `file_present`（主体）与 `attachments_missing`（int，缺失附件数）。"""
    root_path = Path(root) if root is not None else DEFAULT_ROOT
    result = archive_store.list_items(
        year=year, kind=kind, problem_code=problem_code, q=q,
        limit=limit, offset=offset, db_path=db_path)
    result["rows"] = [_row_with_presence(row, root_path) for row in result["rows"]]
    result["count"] = len(result["rows"])
    return result


def _rel_to_root(path: Path, root: Path) -> str:
    """仓库根内 → POSIX 相对路径；根外 → 原样字符串（不编造相对路径）。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def archive_summary(*, db_path: str | Path = DEFAULT_DB_PATH,
                    root: str | Path | None = None,
                    manifest_path: str | Path = DEFAULT_MANIFEST) -> dict:
    """档案统计 + **现算**的主体 / 论文页 / 附件三层缺失数与 manifest 存在性。

    三个口径**分开报**（只看其中一层就是假绿灯）：

    - `file_missing`：**主体**（真题 PDF / 论文目录）缺失数 —— 语义保持不变；
    - `page_total` / `page_missing` / `page_missing_ids`：论文**页级**口径（真实语料 1301 页）。
      目录在、少一页时 `file_missing` 仍是 0，这一层是唯一的眼睛；
    - `attachment_total` / `attachment_missing` / `attachment_missing_ids`：附件口径
      （`extra.attachments[]`，真实语料 69 项）。

    三层互不包含：69 个附件全丢、或 1301 页里少一页，`file_missing` 都还是 0。
    """
    root_path = Path(root) if root is not None else DEFAULT_ROOT
    data = archive_store.summary(db_path=db_path)
    rows = archive_store.list_items(limit=None, offset=0, db_path=db_path)["rows"]
    missing = [r["archive_id"] for r in rows
               if not r.get("local_path")
               or not _main_present(r.get("kind"), root_path / r["local_path"])]
    page_total = 0
    page_missing: list[str] = []
    attachment_total = 0
    attachment_missing: list[str] = []
    for row in rows:
        _, page_missing_names = _paper_page_status(row, root_path)
        if row.get("kind") == "paper":
            # 总页数取库里的 page_count（**声明值**）：目录整个丢了也不能让分母缩水
            page_total += int(row.get("page_count") or 0)
        page_missing.extend(f"{row['archive_id']}::{name}" for name in page_missing_names)
        total, att_missing, att_unsafe = _attachment_status(row, root_path)
        attachment_total += total
        attachment_missing.extend(f"{row['archive_id']}::{name}"
                                  for name in (att_missing + att_unsafe))
    manifest = Path(manifest_path)
    data["file_missing"] = len(missing)
    data["file_missing_ids"] = missing[:20]
    data["page_total"] = page_total
    data["page_missing"] = len(page_missing)
    data["page_missing_ids"] = page_missing[:20]
    data["attachment_total"] = attachment_total
    data["attachment_missing"] = len(attachment_missing)
    data["attachment_missing_ids"] = attachment_missing[:20]
    data["manifest_present"] = manifest.is_file()
    data["manifest_path"] = _rel_to_root(manifest, root_path)
    return data


# ---------------------------------------------------------------- 素材定位

def resolve_asset(archive_id: str, *, page_no: int | None = None,
                  db_path: str | Path = DEFAULT_DB_PATH,
                  root: str | Path | None = None) -> tuple[Path, str]:
    """按 `archive_id` 定位磁盘文件 → `(path, media_type)`。

    - 真题：返回 PDF；给了 `page_no` → `ValueError`（真题没有页概念）；
    - 论文：`page_no` 必填且在 `1..page_count`；返回该页 JPEG。

    `local_path` **只从库里取**（调用方无法传路径），故不存在「调用方穿越」面；
    库存值仍再校验一次（相对路径 / 无 `..`），防的是「有人直接改库」。
    """
    root_path = Path(root) if root is not None else DEFAULT_ROOT
    row = archive_store.get_item(archive_id, db_path=db_path)
    if row is None:
        raise FileNotFoundError(f"档案不存在：{archive_id}")

    local_path = _check_local_path(row.get("local_path"), archive_id)
    target = root_path / local_path

    if row["kind"] == "exam_problem":
        if page_no is not None:
            raise ValueError(
                f"真题档案没有分页概念：{archive_id}（page_no 仅用于论文）")
        if not target.is_file():
            raise FileNotFoundError(f"素材文件不存在：{local_path}")
        return target, "application/pdf"

    page_count = row.get("page_count")
    if page_count is None or int(page_count) <= 0:
        raise ValueError(f"论文档案缺少可用 page_count：{archive_id}")
    page_count = int(page_count)
    if page_no is None:
        raise ValueError(
            f"论文档案必须给 page_no：{archive_id}（合法范围 1..{page_count}）")
    if not isinstance(page_no, int) or isinstance(page_no, bool):
        raise ValueError(f"page_no 必须是整数，收到 {page_no!r}")
    if page_no < 1 or page_no > page_count:
        raise ValueError(
            f"page_no 越界：{archive_id} 合法范围 1..{page_count}，收到 {page_no}")

    extra = parse_extra(row.get("extra"))
    page_file = None
    if isinstance(extra, dict) and isinstance(extra.get("pages"), list):
        for page in extra["pages"]:
            if isinstance(page, dict) and int(page.get("page") or 0) == page_no:
                page_file = page.get("file")
                break
    if page_file is not None:
        # 读侧再挡一次：库里可能被人直接改过（与 `local_path` 同一威胁模型）
        page_file = _check_page_file(page_file, f"{archive_id} 第 {page_no} 页")
    candidates = ([str(page_file)] if page_file else []) + [f"p{page_no:03d}.jpg"]
    for name in candidates:
        path = target / name
        if path.is_file():
            return path, "image/jpeg"
    raise FileNotFoundError(
        f"论文页图不存在：{local_path}/ 第 {page_no} 页（试过：{'、'.join(candidates)}）")
