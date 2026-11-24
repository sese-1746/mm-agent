"""FR-12 导出闭环：把 `paper.docx` + 附件打成**可交付目录**。

契约来源：`.agent-runs/2026-10-18/task-009/README.md` §R3 / §R4 / §T1。

四件事，按顺序：
1. **reviewer 必填**（去空白后 ≥2 字符）——规划书 §15 要求页脚写「已由 XXX 人工审核」，
   留空就等于没署名发出，故这是 `ValueError` 而不是警告。
2. **阻断闸**（全部有真实数据来源，不引用任何"合理性"死代码）：
   a. `paper.docx` 不存在；b. sidecar `paper.manifest.json` 的正文段落数与 docx 实测不符；
   c. 档案版本 `status == "draft"`（未 adopt）；d. 档案 `open_questions` 非空；
   e. 附件源位于交付目录内（复制前的清理会先删掉源）；f. 交付目录就是源文件所在目录。
3. **页脚写在副本上**：`Document(源)` → 每个 section 的 `footer` 写固定句式 → 存到
   `out_dir/paper.docx`。**源文件一个字节都不动**（回归用例 8 卡住这条）。
4. `manifest.json` + `SHA256SUMS`（`sha256sum -c` 可直接校验）。

R22b 补三道**业务硬闸**（《业务场景需求说明书》场景一原文要求，都在写盘之后判定，
阻断时回滚本次写入的产物，保持「阻断即交付目录为空」：
- `insufficient_files`：交付文件数 < `MIN_FILES`（文档：固定目录树与文件数 ≥10 且与台账一致）；
- `missing_disclosure`：`compliance/` 下没有合规声明（文件名含 `disclosure` 或 `声明`）；
- `sanity_blocked`：结果表存在越界（得分不在 0~100 等）——复用 `checks/sanity.py` 的内置规则，
  规则本身一行都不重写；无 CSV 时如实记 `sanity_note`（「没查」绝不写成「通过」）。

不调用 LLM、不联网、不改正文内容（正文由第 21 轮确定性装配产出）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

from docx import Document

from ..checks import sanity
from ..store import profile as profile_store
from ..store.artifacts import list_artifacts
from ..store.db import DEFAULT_DB_PATH, utcnow

TOOL_VERSION = "export_tool@0.1.0"

#: 规划书 §15 的固定句式（`reviewer` 由人工传入，不做任何默认值）
FOOTER_TEMPLATE = "本文由 AI 辅助生成，已由 {reviewer} 人工审核"
MIN_REVIEWER_CHARS = 2

MANIFEST_NAME = "manifest.json"
SUMS_NAME = "SHA256SUMS"
FOUR_FIELDS_NAME = "four_fields.json"
PAPER_NAME = "paper.docx"
SIDECAR_SUFFIX = ".manifest.json"

#: 一并打进交付目录的附件目录（不存在则跳过，manifest 里记 null）
ATTACHMENT_DIRS: tuple[tuple[str, str], ...] = (
    ("figures", "figures"),
    ("results", "results"),
    ("compliance", "compliance"),
)

#: T4 `four_fields.json` 只导这些列（其余列是内部 id/时间戳）
FOUR_FIELDS_KEYS = ("kind", "path", "producer", "prompt_version", "model_tag",
                    "input_hash")

#: R22b：文档「固定目录树与文件数（≥10 且与台账一致）」的硬下限
MIN_FILES = 10
#: 合规声明只认这两种后缀（md 给评审、docx 给提交）
DISCLOSURE_SUFFIXES = (".md", ".docx")
#: 文件名（不区分大小写）命中任一关键词即算合规声明
DISCLOSURE_KEYWORDS = ("disclosure", "声明")
#: 没有 CSV 时写进 manifest 的说明——**不许**把「没查」记成「通过」
SANITY_NO_CSV_NOTE = "results 下无 CSV，未做越界检查"


class ExportBlocked(Exception):
    """导出前置闸未通过。**不许静默继续**（CLI 转 exit 6）。"""

    def __init__(self, gate: str, reason: str):
        self.gate = gate
        self.reason = reason
        super().__init__(f"导出被阻断[{gate}]：{reason}")


# ---------------------------------------------------------------- 基础工具
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _nonempty_body_paragraphs(doc) -> int:
    """正文（不含表格单元格）非空段落数——与第 21 轮 manifest 的 `body` 口径一致。"""
    return sum(1 for para in doc.paragraphs if para.text.strip())


def _validate_reviewer(reviewer: str) -> str:
    """reviewer 必填且去空白后 ≥ `MIN_REVIEWER_CHARS`；返回去空白后的值。"""
    name = reviewer.strip() if isinstance(reviewer, str) else ""
    if len(name) < MIN_REVIEWER_CHARS:
        raise ValueError(
            f"reviewer 必填（去空白后至少 {MIN_REVIEWER_CHARS} 个字符，实际 {name!r}）")
    return name


# ---------------------------------------------------------------- 四道闸
def _gate_paper_exists(paper: Path) -> None:
    if not paper.is_file():
        raise ExportBlocked("paper_exists", f"paper.docx 不存在：{paper}")


def _body_sha_sequence(doc) -> list[str]:
    """正文非空段落的 `text_sha256` 序列（与第 21 轮 manifest 的 `paragraphs[]` 同序同口径）。"""
    return [hashlib.sha256(para.text.encode("utf-8")).hexdigest()
            for para in doc.paragraphs if para.text.strip()]


def _table_cell_matrix(doc) -> list[list[list[str]]]:
    """docx 全部表格的逐格文本：`[表][行][列]`。"""
    return [[[cell.text for cell in row.cells] for row in table.rows]
            for table in doc.tables]


def _gate_out_dir_conflict(paper: Path, out_dir: Path) -> None:
    """闸 e：交付目录里的 `paper.docx` **不许**就是源文件（否则就地覆盖，不可逆）。

    §1-4 / §T1-4 明写「不要覆盖源文件」。判据用 `resolve()`（不要求目标已存在）
    与 `samefile()`（硬链接/大小写/短路径都算同一文件）双重覆盖。
    """
    target = (Path(out_dir) / PAPER_NAME)
    try:
        same = target.resolve() == paper.resolve()
    except OSError:
        same = False
    if not same and target.exists():
        try:
            same = target.samefile(paper)
        except OSError:
            same = False
    if same:
        raise ExportBlocked(
            "out_dir_conflict",
            f"交付目录与源文件冲突：{target} 就是源 paper.docx；"
            f"请换一个 --out-dir（源文件不允许被覆盖）")


def _gate_attachment_sources(*, target: Path,
                             sources: dict[str, str | Path | None]) -> None:
    """闸 f：附件源**不得**位于交付目录之内。

    否则 `_clean_owned_dirs` 会先删掉 `target/figures`——而那正是源目录——再复制一个
    空目录，附件静默消失且退出码仍是 0（二轮复核 N1 实测：
    `--out-dir build --figures-dir build/figures` → `figures=0`、
    `attachments.figures=null`、`rc=0`）。这类"什么都不报、东西没了"正是本场景最贵的故障。
    """
    tgt = Path(target).resolve()
    for key, src in sources.items():
        if src is None:
            continue
        try:
            s = Path(src).resolve()
        except OSError:                      # 路径不可解析：交给后续 is_dir 判定
            continue
        if s == tgt or tgt in s.parents:
            raise ExportBlocked(
                "attachment_source_conflict",
                f"{key} 源目录 {s} 位于交付目录 {tgt} 之内：复制前的清理会先删掉它，"
                f"导致附件静默丢失；请把附件源移到交付目录之外")


def _clean_owned_dirs(target: Path) -> list[str]:
    """清掉**我们自己拥有的**三个附件子目录，避免二次导出残留旧文件。

    只动 `figures` / `results` / `compliance` 三个目录名，**绝不动** `out_dir` 本身，
    也不碰用户在 out_dir 里放的其它东西。
    """
    cleaned: list[str] = []
    for _, dirname in ATTACHMENT_DIRS:
        path = target / dirname
        if path.is_dir():
            shutil.rmtree(path)
            cleaned.append(dirname)
    return cleaned


def _gate_manifest_matches(paper: Path) -> dict:
    """闸 b：sidecar 必须与 docx **逐段、逐格**一致（防 docx 被替换/删表/改字）。

    三步比对，任一不等即阻断（只比「段落数」挡不住同段数整体替换，也挡不住删表）：
    1. 正文：按顺序取非空段落的 sha256 序列，与 `paragraphs[].text_sha256` **逐条**相等；
    2. 表格张数：`len(doc.tables)` == `len(sidecar.tables)`；
    3. 表格单元格：每张表的 `[行][列]` 文本与 `tables[].cells` 逐格相等。
    """
    sidecar = paper.with_suffix(SIDECAR_SUFFIX)
    if not sidecar.is_file():
        raise ExportBlocked("manifest_mismatch",
                            f"sidecar 缺失，无法核对内容：{sidecar}")
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportBlocked("manifest_mismatch", f"sidecar 不可解析：{exc}") from exc
    entries = data.get("paragraphs")
    if not isinstance(entries, list):
        raise ExportBlocked("manifest_mismatch", "sidecar 缺 paragraphs 数组")

    doc = Document(str(paper))

    # 1. 正文逐段 sha256
    declared_seq = [e.get("text_sha256") for e in entries if isinstance(e, dict)]
    actual_seq = _body_sha_sequence(doc)
    if len(declared_seq) != len(actual_seq):
        raise ExportBlocked(
            "manifest_mismatch",
            f"段落数不匹配：manifest 声明 {len(declared_seq)}，docx 实测 {len(actual_seq)}"
            f"（docx 可能被替换或损坏）")
    for i, (want, got) in enumerate(zip(declared_seq, actual_seq)):
        if want != got:
            raise ExportBlocked(
                "manifest_mismatch",
                f"第 {i + 1} 段文本指纹不符：manifest={str(want)[:12]}… "
                f"docx={got[:12]}…（docx 被改字/被替换）")

    # 2/3. 表格逐格
    declared_tables = data.get("tables")
    if declared_tables is None:
        declared_tables = []
    if not isinstance(declared_tables, list):
        raise ExportBlocked("manifest_mismatch", "sidecar 的 tables 不是数组")
    actual_tables = _table_cell_matrix(doc)
    if len(declared_tables) != len(actual_tables):
        raise ExportBlocked(
            "manifest_mismatch",
            f"表格张数不匹配：manifest {len(declared_tables)}，docx {len(actual_tables)}"
            f"（表格被删/被加）")
    for t_index, (declared, actual) in enumerate(zip(declared_tables, actual_tables), 1):
        cells = declared.get("cells") if isinstance(declared, dict) else None
        if cells is None:
            raise ExportBlocked(
                "manifest_mismatch",
                f"第 {t_index} 张表在 sidecar 里没有 cells，无法核对（fail-closed）")
        normalized = [[str(value) for value in row] for row in cells]
        if normalized != actual:
            raise ExportBlocked(
                "manifest_mismatch",
                f"第 {t_index} 张表单元格不符：manifest {normalized} / docx {actual}")
    return data


def _gate_profile(profile_id: str | None, version, *, allow_draft: bool,
                  allow_open_questions: bool,
                  db_path: str | Path) -> dict:
    """闸 c/d：档案 draft 未 adopt、`open_questions` 非空。无 `profile_id` 则跳过。"""
    info = {"profile_id": None, "version_id": None, "status": None,
            "draft_override": False, "open_questions": 0,
            "open_questions_override": False, "patch": {}}
    if not profile_id:
        return info
    try:
        data = profile_store.read_profile(profile_id, version, db_path=db_path)
    except KeyError as exc:
        raise ExportBlocked("profile_missing",
                            f"档案不存在，无法核对 draft/open_questions：{exc}") from exc
    patch = data.get("patch") or {}
    info.update({"profile_id": data["profile_id"],
                 "version_id": data["version_id"],
                 "status": data.get("status")})
    if data.get("status") == "draft":
        if not allow_draft:
            raise ExportBlocked(
                "profile_draft",
                f"档案 {profile_id} v{data['version_id']} 是 draft（未 adopt）；"
                f"请先 adopt，或显式 --allow-draft")
        info["draft_override"] = True
    questions = patch.get("open_questions")
    count = len(questions) if isinstance(questions, list) else 0
    info["open_questions"] = count
    if count and not allow_open_questions:
        raise ExportBlocked(
            "open_questions",
            f"档案 {profile_id} v{data['version_id']} 有 {count} 条 open_questions；"
            f"请先清空，或显式 --allow-open-questions")
    info["open_questions_override"] = bool(count)
    return info


# ---------------------------------------------------------------- 页脚
def _rewrite_zip_deterministic(src: Path, dst: Path) -> None:
    """把 docx（本质是 zip）按**固定时间戳**重写一份到 `dst`。

    `python-docx` 保存时 zip 条目带**当前时间**，于是「同样内容两次导出」字节不同、
    sha256 也不同，§3 第 10 条（幂等）就永远过不了。固定时间戳后导出物可复现。
    条目顺序与压缩方式保持不变（`[Content_Types].xml` 仍在首位）。
    """
    with zipfile.ZipFile(src) as zin:
        items = [(info, zin.read(info.filename)) for info in zin.infolist()]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in items:
            fixed = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = info.external_attr
            zout.writestr(fixed, data)


def _write_footer(src: Path, dst: Path, reviewer: str) -> str:
    """把固定页脚写进**副本**：`src` 只读，`dst` 落盘。

    覆盖**每个 section**（多 section 的 docx 也必须都有署名）；某 section 的页脚若
    `is_linked_to_previous=True`，先断开链接再写，避免继承到上一个 section 的空白页脚。
    """
    text = FOOTER_TEMPLATE.format(reviewer=reviewer)
    doc = Document(str(src))
    for section in doc.sections:
        footer = section.footer
        if footer.is_linked_to_previous:
            footer.is_linked_to_previous = False
        para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        para.text = text
    # 先落到临时文件，再以固定时间戳重写成正式产物（避免 os.replace 覆盖已存在文件）
    raw = dst.with_name(dst.name + ".raw")
    doc.save(str(raw))
    _rewrite_zip_deterministic(raw, dst)   # 幂等：同一内容两次导出字节相同
    raw.unlink()
    return text


# ---------------------------------------------------------------- 附件复制
def _copy_attachment(src: str | Path | None, dst: Path) -> list[str]:
    """把附件目录整棵复制到 `dst`，返回相对 `dst.parent` 的 posix 路径列表。"""
    if src is None:
        return []
    root = Path(src)
    if not root.is_dir():
        return []
    shutil.copytree(root, dst, dirs_exist_ok=True)
    return sorted(p.relative_to(dst.parent).as_posix()
                  for p in dst.rglob("*") if p.is_file())


def _write_four_fields(out_dir: Path, *, profile_id: str | None, version,
                       db_path: str | Path) -> tuple[int, str]:
    """T4：产物四字段导出。**没有产物就写空数组**，不编造。"""
    version_id = None if version == "last" else int(version)
    try:
        rows = list_artifacts(profile_id=profile_id, version_id=version_id,
                              limit=1000, db_path=db_path)["rows"]
    except sqlite3.Error as exc:      # 库/表缺失：写空数组并留原因，不伪造条目
        rows = []
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = ""
    payload = [{k: row.get(k) for k in FOUR_FIELDS_KEYS} for row in rows]
    (out_dir / FOUR_FIELDS_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return len(payload), error


# ---------------------------------------------------------------- R22b 三道业务硬闸
def _collect_disclosure(target: Path) -> dict:
    """扫**已落盘**的 `out_dir/compliance`，返回 `{"present": bool, "files": [文件名]}`。

    判据（文档：「缺少合规声明时导出按钮直接变红阻断」）：至少一个 `*.md` / `*.docx`，
    且文件名（不区分大小写）包含 `disclosure` 或 `声明`。看落盘后的副本而不是源目录——
    交付包里没有的东西，等于没有。
    """
    root = target / "compliance"
    names: list[str] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in DISCLOSURE_SUFFIXES:
                continue
            lowered = path.name.lower()
            if any(key.lower() in lowered for key in DISCLOSURE_KEYWORDS):
                names.append(path.relative_to(root).as_posix())
    return {"present": bool(names), "files": names}


def _read_result_tables(results_dir: str | Path | None) -> tuple[dict | None, str]:
    """扫 `results_dir/**/*.csv`，返回 `({相对路径: DataFrame}, error)`。

    - `tables is None`：**没有可查的结果表**（没传目录 / 目录不存在 / 没有 CSV），
      这**不是**「检查通过」，调用方必须落 `sanity_note`；
    - `error` 非空：某个 CSV 编码或解析失败——检查做不完整，调用方按 **fail-closed** 阻断。
    """
    if results_dir is None:
        return None, ""
    root = Path(results_dir)
    if not root.is_dir():
        return None, ""
    csvs = sorted((p for p in root.rglob("*.csv") if p.is_file()),
                  key=lambda p: p.as_posix())
    if not csvs:
        return None, ""
    import pandas as pd          # 局部 import：不导出结果的命令不必付 pandas 的启动成本

    tables: dict[str, object] = {}
    for path in csvs:
        rel = path.relative_to(root).as_posix()
        frame = None
        problems: list[str] = []
        for encoding in ("utf-8-sig", "gbk"):
            try:
                frame = pd.read_csv(path, encoding=encoding)
                break
            except (OSError, ValueError) as exc:   # UnicodeDecodeError / ParserError 都是 ValueError
                problems.append(f"{encoding}: {exc}")
        if frame is None:
            return tables, f"{rel} 读取失败（{'；'.join(problems)}）"
        tables[rel] = frame
    return tables, ""


def _discard(path: Path, attempts: int = 3) -> None:
    """删掉本次写出的单个文件；宿主回收站守卫偶发 `0x2`（文件正被扫描/瞬时消失）时重试。

    实测：连续删除时约 1/40 次 `SHFileOperationW` 抛 `OSError`（本机 safe-delete 守卫），
    不重试会把「闸阻断 exit 6」错报成「写盘失败 exit 3」。
    """
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _rollback_export(target: Path) -> None:
    """回滚本次写入：删掉我们自己的产物，保持「阻断即交付目录为空」。

    只删**本工具写的东西**（`paper.docx` / `four_fields.json` / `manifest.json` /
    `SHA256SUMS` + 三个附件子目录），不碰用户在 out_dir 里放的其它文件。
    """
    for name in (PAPER_NAME, PAPER_NAME + ".raw", FOUR_FIELDS_NAME,
                 MANIFEST_NAME, SUMS_NAME):
        path = target / name
        if path.is_file():
            _discard(path)
    _clean_owned_dirs(target)


def _gate_file_count(count: int, attachments: dict[str, int | None], *,
                     allow: bool) -> bool:
    """闸 g：交付文件数 < `MIN_FILES` → 阻断。返回「是否被放行」。"""
    if count >= MIN_FILES:
        return False
    if allow:
        return True
    missing = [key for key, _ in ATTACHMENT_DIRS if not attachments.get(key)]
    lacking = MIN_FILES - count
    raise ExportBlocked(
        "insufficient_files",
        f"当前 {count} 个文件，缺 {lacking} 个：请补齐 "
        f"{'/'.join(missing) or 'figures/results/compliance'} 产物后再导出"
        f"（文档要求固定目录树文件数 ≥{MIN_FILES} 且与台账一致）；"
        f"确需少文件交付请显式 --allow-few-files")


def _gate_disclosure(disclosure: dict, *, allow: bool) -> bool:
    """闸 h：没有合规声明 → 阻断。返回「是否被放行」。"""
    if disclosure["present"]:
        return False
    if allow:
        return True
    raise ExportBlocked(
        "missing_disclosure",
        f"compliance 下没有合规声明（需 *.md/*.docx 且文件名含 disclosure 或 声明）："
        f"当前 files={disclosure['files']}；文档要求缺合规声明即阻断导出，"
        f"确需导出请显式 --allow-missing-disclosure")


def _gate_sanity(report, *, error: str, allow: bool) -> bool:
    """闸 i：结果表越界报警未处理（或结果表读不出来）→ 阻断。返回「是否被放行」。"""
    blocking = bool(error) or bool(report is not None and report.blocking)
    if not blocking:
        return False
    if allow:
        return True
    if error:
        raise ExportBlocked(
            "sanity_blocked",
            f"结果表读取失败，越界检查无法完成（fail-closed）：{error}")
    first = report.violations[0]["message"]
    raise ExportBlocked(
        "sanity_blocked",
        f"{len(report.violations)} 条越界报警未处理，例如 {first}；"
        f"请先修正结果表，或显式 --allow-sanity")


# ---------------------------------------------------------------- 主入口
def build_deliverable(*, paper_docx: str | Path,
                      figures_dir: str | Path | None = None,
                      results_dir: str | Path | None = None,
                      compliance_dir: str | Path | None = None,
                      out_dir: str | Path, reviewer: str,
                      profile_id: str | None = None, version="last",
                      allow_draft: bool = False,
                      allow_open_questions: bool = False,
                      allow_few_files: bool = False,
                      allow_missing_disclosure: bool = False,
                      allow_sanity: bool = False,
                      db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """导出可交付目录。返回 `{out_dir, files, total_bytes, reviewer,
    paper_paragraphs, figures, file_count, disclosure_present, sanity_blocking}`。

    三个 `allow_*`（R22b）默认全 False：文件数 <10、没有合规声明、结果表越界，
    都会被阻断；放行时 manifest 分别记 `file_count_override` / `disclosure_override` /
    `sanity_override`，不把「放行」伪装成「合规」。

    异常：`ValueError`（reviewer 非法）、`ExportBlocked`（各道闸）、`OSError`（写盘失败）。
    """
    reviewer_name = _validate_reviewer(reviewer)
    paper = Path(paper_docx)
    _gate_paper_exists(paper)
    _gate_out_dir_conflict(paper, Path(out_dir))
    _gate_attachment_sources(
        target=Path(out_dir),
        sources={"figures": figures_dir, "results": results_dir,
                 "compliance": compliance_dir})
    sidecar = _gate_manifest_matches(paper)
    info = _gate_profile(profile_id, version, allow_draft=allow_draft,
                         allow_open_questions=allow_open_questions, db_path=db_path)

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    cleaned = _clean_owned_dirs(target)      # 只清 figures/results/compliance

    # 页脚写在**副本**上：源 paper.docx 的 sha256 必须保持不变
    footer_text = _write_footer(paper, target / PAPER_NAME, reviewer_name)
    copied: list[str] = [PAPER_NAME]
    attachments: dict[str, int | None] = {}
    for key, dirname in ATTACHMENT_DIRS:
        src = {"figures": figures_dir, "results": results_dir,
               "compliance": compliance_dir}[key]
        exists = src is not None and Path(src).is_dir()
        copied_files = _copy_attachment(src, target / dirname)
        copied.extend(copied_files)
        # §T1-3：目录不存在 → null（与「存在但为空」的 0 区分开）
        attachments[key] = len(copied_files) if exists else None

    four_fields_n, four_fields_error = _write_four_fields(
        target, profile_id=profile_id, version=version, db_path=db_path)
    copied.append(FOUR_FIELDS_NAME)      # 无产物时也是一个 `[]` 文件，照样进清单

    files = []
    for rel in sorted(set(copied)):
        path = target / rel
        files.append({"path": rel, "sha256": _sha256(path), "bytes": path.stat().st_size})
    total_bytes = sum(item["bytes"] for item in files)

    # ---- R22b：三道业务硬闸。必须在写盘之后判（要知道真实的落盘文件数/声明/结果表），
    # 阻断时把本次产物回滚干净，保持「阻断即交付目录为空」。
    file_count = len(files) + 2          # + manifest.json + SHA256SUMS（下面必然产出）
    disclosure = _collect_disclosure(target)
    tables, sanity_error = _read_result_tables(results_dir)
    sanity_report = sanity.run_sanity_checks(tables) if tables is not None else None
    sanity_info = {
        "checked_tables": len(tables or {}),
        "violations": len(sanity_report.violations) if sanity_report else 0,
        "blocking": bool(sanity_error) or bool(sanity_report and sanity_report.blocking),
    }
    try:
        file_count_override = _gate_file_count(file_count, attachments,
                                               allow=allow_few_files)
        disclosure_override = _gate_disclosure(disclosure,
                                              allow=allow_missing_disclosure)
        sanity_override = _gate_sanity(sanity_report, error=sanity_error,
                                       allow=allow_sanity)
    except ExportBlocked:
        _rollback_export(target)
        raise

    doc = Document(str(target / PAPER_NAME))
    manifest = {
        "tool_version": TOOL_VERSION,
        "generated_at": utcnow(),
        "reviewer": reviewer_name,
        "footer_text": footer_text,
        "source": {"profile_id": info["profile_id"],
                   "version_id": info["version_id"],
                   "status": info["status"]},
        "files": files,
        "file_count": file_count,
        "figures": attachments["figures"] or 0,
        "attachments": attachments,
        "cleaned": cleaned,
        "appendix_code": bool(sidecar.get("appendix_code", False)),
        "draft_override": info["draft_override"],
        "open_questions": info["open_questions"],
        "paper_paragraphs": _nonempty_body_paragraphs(doc),
        "four_fields_n": four_fields_n,
        "disclosure": disclosure,
        "sanity": sanity_info,
    }
    if tables is None:
        # 「没查」与「查过没问题」必须能区分：没查 → 记 note，不许写成通过
        manifest["sanity_note"] = SANITY_NO_CSV_NOTE
    if sanity_error:
        manifest["sanity_error"] = sanity_error
    if info["open_questions_override"]:
        manifest["open_questions_override"] = True
    if file_count_override:
        manifest["file_count_override"] = True
    if disclosure_override:
        manifest["disclosure_override"] = True
    if sanity_override:
        manifest["sanity_override"] = True
    if four_fields_error:
        manifest["four_fields_error"] = four_fields_error
    manifest_path = target / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    # SHA256SUMS：连 manifest.json 一起列（自身无法自校验）
    lines = [f"{item['sha256']}  {item['path']}" for item in files]
    lines.append(f"{_sha256(manifest_path)}  {MANIFEST_NAME}")
    # `newline="\n"`：Windows 上默认会把 \n 翻成 \r\n，`sha256sum -c` 会把 \r 读进文件名
    (target / SUMS_NAME).write_text("\n".join(lines) + "\n",
                                    encoding="utf-8", newline="\n")

    return {
        "out_dir": str(target),
        "files": [item["path"] for item in files],
        "total_bytes": total_bytes,
        "reviewer": reviewer_name,
        "paper_paragraphs": manifest["paper_paragraphs"],
        "figures": manifest["figures"],
        "file_count": file_count,
        "disclosure_present": disclosure["present"],
        "sanity_blocking": sanity_info["blocking"],
    }
