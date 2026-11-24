"""FR-4 真题文本抽取（甲方案）的验收用例。

钉的是六件容易「看起来做了、其实没做」的事：

1. **逐份都有明确状态**：真实语料 10 份的页数/字符数逐份比对（不是只看总和）；
2. **两种失败分开标**：`text_layer_missing`（字符太少）与 `garbled`（CJK 占比过低），
   且失败条目**不能**同时被算进 `ok`（空文本不许冒充成功）；
3. **清洗只做四件事**：去页码/重复页眉、合并中文硬换行、压缩空白、段落分隔；
   顺带反向钉住「不许多做」——数字与英文不会被粘成一串；
4. **幂等**：同输入重复跑，txt 字节相同、索引除时间戳外相同、库行数不变；
5. **`archive_item.extra.text_path` 回写正确**且不破坏 `extra` 里原有键（附件清单）；
6. **零联网零 LLM**：模块不 import 网络/模型客户端，也不含 URL 字面量。

真实 PDF 用例只在语料缺失时跳过（`skipif` 仅看文件是否存在，不吞真实失败）。
"""
from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from mmagent.store import archive as archive_store
from mmagent.tools import exam_text_tool as tool

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "artifacts" / "exam_corpus" / "manifest.json"
SRC_PDF = REPO / "artifacts" / "exam_corpus" / "exams" / "2025" / "A题.pdf"

needs_corpus = pytest.mark.skipif(
    not (MANIFEST.exists() and SRC_PDF.exists()),
    reason="真实真题语料不存在（PDF 不入库，需先从官方源抓取）")

# 逐份实测值（2026-10-20，pypdf 6.21.1）：页数 / 清洗后字符数。
# 用**逐份**字典而不是总和：总和相同但某两份互换，是抽取错位的典型样子。
EXPECTED_PAGES = {
    "exam-2024-A": 3, "exam-2024-B": 2, "exam-2024-C": 1, "exam-2024-D": 2,
    "exam-2024-E": 1, "exam-2025-A": 1, "exam-2025-B": 2, "exam-2025-C": 2,
    "exam-2025-D": 2, "exam-2025-E": 1,
}
EXPECTED_CHARS = {
    "exam-2024-A": 2062, "exam-2024-B": 1717, "exam-2024-C": 1457,
    "exam-2024-D": 1501, "exam-2024-E": 1011, "exam-2025-A": 1566,
    "exam-2025-B": 1017, "exam-2025-C": 2280, "exam-2025-D": 2231,
    "exam-2025-E": 902,
}
EXPECTED_TOTAL_CHARS = 15744


# ---------------------------------------------------------------- 夹具

@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """最小本地语料：1 份真实 PDF 放在 tmp 根下的 `pdfs/a.pdf`。"""
    root = tmp_path / "repo"
    (root / "pdfs").mkdir(parents=True)
    shutil.copy2(SRC_PDF, root / "pdfs" / "a.pdf")
    return root


def _manifest(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "items": items}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def _item(archive_id: str, local_path: str, *, kind: str = "exam_problem",
          extra: dict | None = None) -> dict:
    return {
        "archive_id": archive_id, "kind": kind, "year": 2025, "problem_code": "A",
        "tag": "", "title": "用例条目", "source_url": "", "asset_url": "",
        "local_path": local_path, "sha256": "0" * 64, "bytes": 1,
        "page_count": None, "fetched_at": "2026-10-19T00:00:00.000Z",
        "extra": extra if extra is not None else {},
    }


def _seed(db_path: Path, items: list[dict]) -> None:
    """按 manifest 条目把档案行写进临时库（模拟「已入库、待抽文本」的状态）。"""
    rows = [{
        "archive_id": it["archive_id"], "kind": it["kind"], "year": it["year"],
        "problem_code": it.get("problem_code") or "", "tag": it.get("tag") or "",
        "title": it.get("title") or "", "source_url": it.get("source_url") or "",
        "asset_url": it.get("asset_url") or "", "local_path": it["local_path"],
        "sha256": it["sha256"], "bytes": it["bytes"],
        "page_count": it.get("page_count"), "fetched_at": it["fetched_at"],
        "extra": json.dumps(it.get("extra") or {}, ensure_ascii=False),
    } for it in items]
    archive_store.upsert_items(rows, db_path=db_path)


def _row(db_path: Path, archive_id: str) -> dict:
    return archive_store.get_item(archive_id, db_path=db_path)


# ---------------------------------------------------------------- 真实语料：逐份状态

@needs_corpus
def test_extract_all_reports_every_exam_with_status(tmp_path: Path) -> None:
    """10 份真题必须**逐份**给出页数/字符数/两个标记，不许只给汇总数字。"""
    result = tool.extract_all(MANIFEST, tmp_path / "text",
                              db_path=tmp_path / "mm.db")
    assert result["total"] == 10
    assert result["ok"] == 10
    assert result["text_layer_missing"] == 0
    assert result["garbled"] == 0
    assert result["failed"] == 0
    assert result["skipped_papers"] == 23          # 论文 1301 页 JPEG 本轮明不做

    got_ids = [item["archive_id"] for item in result["items"]]
    assert sorted(got_ids) == sorted(EXPECTED_PAGES)
    assert {i["archive_id"]: i["pages"] for i in result["items"]} == EXPECTED_PAGES
    assert {i["archive_id"]: i["chars"] for i in result["items"]} == EXPECTED_CHARS
    assert result["result_chars"] == EXPECTED_TOTAL_CHARS

    for item in result["items"]:
        assert item["error"] is None
        assert item["text_layer_missing"] is False
        assert item["garbled"] is False
        assert item["text"].strip(), f"{item['archive_id']} 抽出了空文本却报成功"
        assert item["text_path"].endswith(f"{item['archive_id']}.txt")


@needs_corpus
def test_extract_one_real_pdf_keeps_numbers_and_chinese() -> None:
    """真实 PDF：中文与数字都要在（`223 节` / `341 cm` 这类题面关键量不能丢）。"""
    result = tool.extract_one(SRC_PDF)
    assert result["error"] is None
    assert result["pages"] == 1
    assert result["chars"] > 1000
    text = result["text"]
    assert "烟幕" in text and "无人机" in text
    assert "300 m/s" in text or "300 m/s 。" in text
    assert result["cjk_ratio"] > 0.5


# ---------------------------------------------------------------- 两种失败标记

@needs_corpus
def test_text_layer_missing_flag_is_set(tmp_path: Path) -> None:
    """字符数 < 页数×阈值 → `text_layer_missing`（用极高标准抬阈值来造这一状态）。"""
    result = tool.extract_one(SRC_PDF, min_chars_per_page=100_000)
    assert result["text_layer_missing"] is True
    assert result["garbled"] is False
    assert result["chars"] > 0                    # 抽到了字，只是不到阈值


def test_garbled_flag_for_latin_only_text(tmp_path: Path, monkeypatch) -> None:
    """CJK 占比 < 0.10 且非空 → `garbled`（字体映射失败的形状）。"""
    pdf = tmp_path / "latin.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class _Page:
        def extract_text(self) -> str:
            return "Hello world " * 40

    class _Reader:
        def __init__(self, _path):
            self.pages = [_Page(), _Page()]

    monkeypatch.setattr(tool, "PdfReader", _Reader)
    result = tool.extract_one(pdf, min_chars_per_page=1)
    assert result["garbled"] is True
    assert result["text_layer_missing"] is False
    assert result["cjk_ratio"] == 0.0


def test_empty_text_layer_is_not_success(tmp_path: Path, monkeypatch) -> None:
    """一个字都抽不出 → 标记 + 计数，绝不能算 `ok`。"""
    pdf = tmp_path / "blank.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class _Page:
        def extract_text(self) -> str:
            return ""

    class _Reader:
        def __init__(self, _path):
            self.pages = [_Page(), _Page()]

    monkeypatch.setattr(tool, "PdfReader", _Reader)
    one = tool.extract_one(pdf)
    assert one["text_layer_missing"] is True
    assert one["chars"] == 0
    assert one["error"] is None


def test_short_latin_pdf_fills_exactly_one_bucket(tmp_path: Path, monkeypatch) -> None:
    """「1 页 55 字符纯拉丁」曾同时进 missing 与 garbled 两个桶（`gk-a-r29` 缺陷 1）。

    两个标记必须互斥：没有文本层是更根本的诊断，此时不再重复报乱码。
    """
    pdf = tmp_path / "short.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class _Page:
        def extract_text(self) -> str:
            return "A" * 55

    class _Reader:
        def __init__(self, _path):
            self.pages = [_Page()]

    monkeypatch.setattr(tool, "PdfReader", _Reader)
    one = tool.extract_one(pdf)
    assert one["chars"] > 0
    assert one["text_layer_missing"] is True
    assert one["garbled"] is False, "同一份被两个桶各计一次 → 守恒等式会被打穿"

    manifest = _manifest(tmp_path, [_item("exam-2099-A", "short.pdf")])
    (tmp_path / "short.pdf").write_bytes(pdf.read_bytes())
    result = tool.extract_all(manifest, tmp_path / "text",
                              db_path=tmp_path / "mm.db", root=tmp_path)
    assert result["total"] == 1
    buckets = (result["ok"] + result["text_layer_missing"]
               + result["garbled"] + result["failed"])
    assert buckets == result["total"], f"四桶和 {buckets} != total {result['total']}"


# ---------------------------------------------------------------- 清洗：只做四件事

def test_clean_drops_page_number_and_repeated_header() -> None:
    pages = [
        "1\n2025 年竞赛题目\n第一页正文内容\n",
        "2\n2025 年竞赛题目\n第二页正文内容\n",
    ]
    text = tool.clean_pages(pages)
    assert "2025 年竞赛题目" not in text
    assert text.startswith("第一页正文内容")
    # 页码行被剔掉，不与正文粘连
    assert "1第一页" not in text and "2第二页" not in text
    assert "第二页正文内容" in text


def test_mirrored_pages_are_not_wiped() -> None:
    """A/B 页 + B/A 页（首末互为镜像）曾被互判为重复，整篇清空（`gk-a-r29` 缺陷 2）。"""
    pages = ["标题甲\n正文壹", "正文壹\n标题甲"]
    text = tool.clean_pages(pages)
    assert text.strip(), "镜像版式被清洗成空文本"
    assert "正文壹" in text


def test_single_page_duplicate_lines_are_not_wiped() -> None:
    """单页两行完全相同：同页内重复是版式，不是页眉页脚，不许整页删空。"""
    text = tool.clean_pages(["2024 年数学建模竞赛\n2024 年数学建模竞赛"])
    assert text.strip()
    assert text.count("2024 年数学建模竞赛") == 2


def test_repeated_header_at_same_position_is_still_dropped() -> None:
    """反向锚点：真页眉（多页**同一位置**重复）仍必须被剔，别把上面两条护栏改过头。"""
    pages = [
        "1\n2025 年竞赛题目\n正文甲\n",
        "2\n2025 年竞赛题目\n正文乙\n",
    ]
    text = tool.clean_pages(pages)
    assert "2025 年竞赛题目" not in text
    assert "正文甲" in text and "正文乙" in text


def test_over_cleaned_falls_back_to_raw_text(tmp_path: Path, monkeypatch) -> None:
    """清洗保留量 < 原文一半 → 退回未清洗原文并置 `over_cleaned`（宁可脏，不许空）。

    构造「超长重复页眉 + 短正文」：页眉被正确剔除后只剩一成内容，属清洗误伤的极端形状。
    """
    pdf = tmp_path / "over.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    header = "页眉" * 50                      # 100 字符，跨页同一位置重复

    class _Page:
        def __init__(self, body: str) -> None:
            self._body = body

        def extract_text(self) -> str:
            return self._body

    class _Reader:
        def __init__(self, _path):
            self.pages = [_Page(f"{header}\n第 {n} 页正文\n{n}") for n in range(1, 6)]

    monkeypatch.setattr(tool, "PdfReader", _Reader)
    result = tool.extract_one(pdf)
    assert result["raw_chars"] >= tool.CLEAN_GUARD_MIN_CHARS
    assert result["over_cleaned"] is True
    assert result["text"].strip(), "洗过头时退回了空文本"
    assert "第 3 页正文" in result["text"]
    assert result["chars"] == len(result["text"])


def test_clean_merges_chinese_hard_breaks_but_keeps_latin_spaced() -> None:
    pages = ["这是被硬换行\n切断的中文句子\n\n以及英文\n123 数字混排\n"]
    text = tool.clean_pages(pages)
    assert "这是被硬换行切断的中文句子" in text
    assert "以及英文 123 数字混排" in text
    assert "以及英文123" not in text


def test_clean_compresses_whitespace_and_keeps_paragraph_break() -> None:
    pages = ["第一段   有   多空格\n\n\n\n第二段\n"]
    text = tool.clean_pages(pages)
    assert "第一段 有 多空格" in text
    assert "多空格\n第二段" in text          # 空行 → 单个换行（段分隔）
    assert "\n\n" not in text


def test_clean_does_not_touch_formulas_or_numbers() -> None:
    """反向锚点：清洗不得改写内容（不还原公式、不纠错、不重排数字）。"""
    pages = ["速度为 300 m/s ， 半径为 7 m 的圆柱\n目标位于 (0, 200, 0) 处\n"]
    text = tool.clean_pages(pages)
    assert "300 m/s" in text
    assert "(0, 200, 0)" in text
    assert "7 m" in text
    assert text.count("300") == 1


# ---------------------------------------------------------------- 落地 + 回写

@needs_corpus
def test_extract_all_writes_lf_text_and_relative_text_path(tmp_path: Path, corpus: Path) -> None:
    """txt 必须是 UTF-8 **LF**（无 CR），`extra.text_path` 是相对仓库根的 POSIX 路径。"""
    items = [_item("exam-2025-A", "pdfs/a.pdf",
                   extra={"attachments": [{"name": "附件/result1.xlsx"}]})]
    manifest = _manifest(tmp_path, items)
    db_path = tmp_path / "mm.db"
    _seed(db_path, items)

    result = tool.extract_all(manifest, corpus / "text", db_path=db_path,
                              root=corpus, min_chars_per_page=200)
    assert result["ok"] == 1 and result["failed"] == 0
    assert result["db_updated"] == 1
    assert result["db_missing"] == 0

    txt = corpus / "text" / "exam-2025-A.txt"
    raw = txt.read_bytes()
    assert raw and b"\r" not in raw
    assert raw.decode("utf-8").endswith("\n")

    extra = json.loads(_row(db_path, "exam-2025-A")["extra"])
    assert extra["text_path"] == "text/exam-2025-A.txt"
    # 原有键必须保留（回写只加一个字段，不许覆盖附件清单）
    assert extra["attachments"] == [{"name": "附件/result1.xlsx"}]

    listed = json.loads((corpus / "text" / "manifest.json").read_text(encoding="utf-8"))
    assert listed["exam-2025-A"]["pages"] == 1
    assert listed["exam-2025-A"]["chars"] == result["items"][0]["chars"]
    assert listed["exam-2025-A"]["text_layer_missing"] is False
    assert listed["exam-2025-A"]["garbled"] is False


@needs_corpus
def test_extract_all_is_idempotent(tmp_path: Path, corpus: Path) -> None:
    """重复运行：txt 字节相同、索引除时间戳外相同、库不新增行、extra 不变。"""
    items = [_item("exam-2025-A", "pdfs/a.pdf")]
    manifest = _manifest(tmp_path, items)
    db_path = tmp_path / "mm.db"
    _seed(db_path, items)
    out = corpus / "text"

    first = tool.extract_all(manifest, out, db_path=db_path, root=corpus)
    first_bytes = (out / "exam-2025-A.txt").read_bytes()
    first_extra = json.loads(_row(db_path, "exam-2025-A")["extra"])
    first_manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    first_count = archive_store.summary(db_path=db_path)["total"]

    second = tool.extract_all(manifest, out, db_path=db_path, root=corpus)
    second_bytes = (out / "exam-2025-A.txt").read_bytes()
    second_manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    assert first_bytes == second_bytes
    assert first["items"][0]["sha256_of_text"] == second["items"][0]["sha256_of_text"]
    assert json.loads(_row(db_path, "exam-2025-A")["extra"]) == first_extra
    assert archive_store.summary(db_path=db_path)["total"] == first_count == 1
    assert second["db_unchanged"] == 1 and second["db_updated"] == 0

    for entry in (first_manifest, second_manifest):
        entry["exam-2025-A"].pop("extracted_at")
    assert first_manifest == second_manifest


@needs_corpus
def test_dry_run_writes_nothing(tmp_path: Path, corpus: Path) -> None:
    items = [_item("exam-2025-A", "pdfs/a.pdf")]
    manifest = _manifest(tmp_path, items)
    db_path = tmp_path / "mm.db"
    _seed(db_path, items)

    result = tool.extract_all(manifest, corpus / "text", db_path=db_path,
                              root=corpus, dry_run=True)
    assert result["dry_run"] is True
    assert result["ok"] == 1
    assert result["text_manifest_path"] is None
    assert not (corpus / "text").exists()
    assert json.loads(_row(db_path, "exam-2025-A")["extra"]) == {}


# ---------------------------------------------------------------- 跳过 / 失败 / 缺库

@needs_corpus
def test_papers_are_skipped_and_counted(tmp_path: Path, corpus: Path) -> None:
    items = [
        _item("exam-2025-A", "pdfs/a.pdf"),
        _item("paper-2025-A196", "papers/2025/A196", kind="paper"),
    ]
    manifest = _manifest(tmp_path, items)
    result = tool.extract_all(manifest, corpus / "text", db_path=tmp_path / "mm.db",
                              root=corpus)
    assert result["total"] == 1
    assert result["skipped_papers"] == 1
    assert [i["archive_id"] for i in result["items"]] == ["exam-2025-A"]


def test_missing_pdf_is_failed_not_empty_success(tmp_path: Path) -> None:
    items = [_item("exam-2099-A", "pdfs/nope.pdf")]
    manifest = _manifest(tmp_path, items)
    result = tool.extract_all(manifest, tmp_path / "text",
                              db_path=tmp_path / "mm.db", root=tmp_path)
    assert result["failed"] == 1
    assert result["ok"] == 0
    assert result["total"] == 1
    item = result["items"][0]
    assert item["error"].startswith("file_not_found")
    assert item["text_path"] is None
    assert result["db_updated"] == 0


@needs_corpus
def test_db_missing_row_is_reported_not_inserted(tmp_path: Path, corpus: Path) -> None:
    """库里没有该 archive_id → 报 `db_missing`，**不插入**伪造行。"""
    items = [_item("exam-2025-A", "pdfs/a.pdf")]
    manifest = _manifest(tmp_path, items)
    db_path = tmp_path / "mm.db"
    result = tool.extract_all(manifest, corpus / "text", db_path=db_path, root=corpus)
    assert result["db_missing"] == 1
    assert archive_store.summary(db_path=db_path)["total"] == 0


# ---------------------------------------------------------------- 零联网零 LLM

def test_module_is_offline_and_llm_free() -> None:
    """模块不得 import 网络/模型客户端，也不得含 URL 字面量（`netguard-check` 口径）。"""
    source = Path(tool.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"socket", "ftplib", "http", "urllib", "requests", "httpx",
                 "openai", "dashscope", "agents"}
    assert not (imported & forbidden), imported & forbidden
    assert "pypdf" in imported

    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [s for s in literals if s.startswith(("http://", "https://"))]

    # 零 LLM：模块里不存在任何 runner / 模型调用的入口
    assert not hasattr(tool, "runner")

def test_清空空白不算洗过头(tmp_path: Path, monkeypatch) -> None:
    """压缩连续空白**不是删内容**：宽间距版式不许被判 `over_cleaned`（D-88）。

    修复前的实测（`artifacts/tmp/gk_r29_oc_probe`）：一张「5 列 × 40 空格间隔」的
    对齐表 —— `raw=5160`、清洗后约 360 字符，比例 0.07 → 判「洗过头」并退回未清洗
    原文；但**非空白字符一个都没少**（`solid 320 → 320`），页眉页脚因此反而没剔掉。
    同类：目录页点线引导、双栏间距。

    判据改成「非空白字符保留率」后，同一样本 `over_cleaned=False`，且清洗真的生效
    （空白被压缩）。下方同时断言真实删除仍会被抓，防止改判据变成放过。
    """
    pdf = tmp_path / "wide.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    def _page_body(n: int) -> str:
        # 每页 12 行、每行两段文字被 40 个空格隔开；**页与页不同**，否则「跨页同位置
        # 重复」规则会把所有行当页眉剔掉（那样测的就不是空白压缩了）。
        return "\n".join(
            f"第{n}页第{row}行" + " " * 40 + f"数据{row * 7 + n}"
            for row in range(12))

    class _Page:
        def __init__(self, body: str) -> None:
            self._body = body

        def extract_text(self) -> str:
            return self._body

    class _Reader:
        def __init__(self, _path):
            self.pages = [_Page(_page_body(n)) for n in range(1, 6)]

    monkeypatch.setattr(tool, "PdfReader", _Reader)
    result = tool.extract_one(pdf)

    solid = lambda text: len("".join(text.split()))
    assert result["raw_chars"] >= tool.CLEAN_GUARD_MIN_CHARS
    assert result["over_cleaned"] is False, "空白压缩被误判成洗过头"
    # 清洗真的生效了：长度显著变短（空白被压缩）
    assert result["chars"] < result["raw_chars"] * 0.6
    # 但内容一个字没少：非空白字符总数与原文完全相等
    # （不能硬编码行长——`数据{row*7+n}` 的位数逐行不同）
    expected_solid = sum(len("".join(_page_body(n).split())) for n in range(1, 6))
    assert solid(result["text"]) == expected_solid


def test_真删内容仍然判洗过头(tmp_path: Path, monkeypatch) -> None:
    """反方向锚点：改判据不许变成「永不触发」（与上面成对，防单向放宽）。"""
    pdf = tmp_path / "over2.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    header = "页眉" * 50                       # 100 个非空白字符，跨页同位置重复

    class _Page:
        def __init__(self, body: str) -> None:
            self._body = body

        def extract_text(self) -> str:
            return self._body

    class _Reader:
        def __init__(self, _path):
            self.pages = [_Page(f"{header}\n第{n}页正文\n{n}") for n in range(1, 6)]

    monkeypatch.setattr(tool, "PdfReader", _Reader)
    result = tool.extract_one(pdf)
    # 页眉占据原文绝大部分非空白字符，剔掉后保留率远低于一半 → 必须触发
    assert result["over_cleaned"] is True
    assert result["text"].strip()
