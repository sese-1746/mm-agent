"""纯 Python 文献综述生成器。

该模块只处理本地 ``.docx`` / ``.pdf`` 文献：提取标题和正文，按关键词与摘要片段
生成一份可编辑的简易综述，并把结果写成 Word 文档。它不调用模型、不联网，
因此即使没有配置任何模型密钥也能工作。
"""
from __future__ import annotations

import base64
import binascii
import io
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from docx import Document
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "artifacts" / "literature_reviews"
MAX_DOCUMENTS = 20
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_DOCUMENT_BYTES = 80 * 1024 * 1024
MAX_TITLE_LENGTH = 200
ALLOWED_SUFFIXES = {".docx", ".pdf"}

_CJK = re.compile(r"[\u4e00-\u9fff]{2,8}")
_EN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_SENTENCE = re.compile(r"[^。！？!?；;\n]{12,180}[。！？!?；;]?")
_STOPWORDS = {
    "本文", "研究", "方法", "结果", "分析", "问题", "模型", "数据", "论文",
    "可以", "通过", "进行", "提出", "基于", "以及", "对于", "其中", "一种",
    "相关", "不同", "主要", "本文中", "研究中", "the", "and", "with", "from",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _validate_name(name: str) -> str:
    """校验上传文件名，避免把路径或控制字符带入产物与台账。"""
    value = str(name or "")
    if not value.strip():
        raise ValueError("上传文档缺少文件名")
    if value != value.strip():
        raise ValueError(f"{value!r} 文件名不能含首尾空白")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{value!r} 含有不可用的控制字符")
    if any(separator in value for separator in ("/", "\\")) or ".." in value:
        raise ValueError(f"{value} 文件名不能包含路径片段")
    if value.endswith((".", " ")):
        raise ValueError(f"{value} 文件名不能以句点或空格结尾")
    if Path(value).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"{value} 不是支持的文献文件；当前仅支持 .docx 或 .pdf")
    return value


def _decode_doc(name: str, encoded: str) -> bytes:
    name = _validate_name(name)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} 的 base64 解码失败：{exc}") from exc
    if not data:
        raise ValueError(f"{name} 为空文件")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"{name} 超过 20 MB 单文件限制")
    return data


def _text_paragraphs(text: str) -> list[str]:
    """把 PDF 文本层按换行拆成可归纳的段落。"""
    return [_clean(part) for part in re.split(r"\n+", text or "") if _clean(part)]


def extract_docx(name: str, data: bytes) -> dict:
    """提取一个 docx 的标题、段落和表格文字。"""
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx 对损坏 zip 的异常类型不稳定
        raise ValueError(f"{name} 无法读取：请确认是有效的 .docx 文件") from exc
    paragraphs = [_clean(p.text) for p in doc.paragraphs if _clean(p.text)]
    table_text: list[str] = []
    for table in doc.tables:
        for row in table.rows:
            cells = [_clean(cell.text) for cell in row.cells]
            line = "；".join(x for x in cells if x)
            if line:
                table_text.append(line)
    text = "\n".join(paragraphs + table_text)
    title = paragraphs[0] if paragraphs else Path(name).stem
    return {"name": name, "title": title[:120], "paragraphs": paragraphs,
            "table_lines": table_text, "text": text, "format": "docx"}


def extract_pdf(name: str, data: bytes) -> dict:
    """提取 PDF 文字层；扫描 PDF 允许生成空文本综述，损坏 PDF 明确报错。"""
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ValueError("PDF 受密码保护")
            except Exception as exc:
                raise ValueError(f"{name} 无法读取：PDF 受密码保护或密码不正确") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{name} 无法读取：请确认是有效的 PDF 文件") from exc

    try:
        pages = list(reader.pages)
    except Exception as exc:
        raise ValueError(f"{name} 无法读取：PDF 页面结构损坏") from exc

    paragraphs: list[str] = []
    for page in pages:
        try:
            paragraphs.extend(_text_paragraphs(page.extract_text() or ""))
        except Exception:
            continue
    text = "\n".join(paragraphs)
    title = paragraphs[0] if paragraphs else Path(name).stem
    return {"name": name, "title": title[:120], "paragraphs": paragraphs,
            "table_lines": [], "text": text, "format": "pdf",
            "pages": len(pages)}


def _keywords(texts: list[str], limit: int = 8) -> list[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        for token in _CJK.findall(text):
            if token not in _STOPWORDS:
                counts[token] += 1
        for token in _EN.findall(text.lower()):
            if token not in _STOPWORDS:
                counts[token] += 1
    return [token for token, _ in counts.most_common(limit)]


def _snippets(text: str, limit: int = 2) -> list[str]:
    found = [_clean(x) for x in _SENTENCE.findall(text)]
    if found:
        return found[:limit]
    compact = _clean(text)
    return [compact[:180]] if compact else []


def build_review(documents: list[dict], title: str = "数模论文文献综述") -> dict:
    """根据已提取文档生成 Markdown 形式的简易综述。"""
    all_text = [d["text"] for d in documents]
    keywords = _keywords(all_text)
    names = [d["title"] for d in documents]
    lines = [f"# {title}", "", "## 1. 研究背景与资料范围", ""]
    lines.append(
        f"本综述整理了 {len(documents)} 篇 Word/PDF 文献，围绕"
        f"{('、'.join(keywords[:5]) if keywords else '数模问题、建模方法与结果分析')}"
        "进行归纳。综述内容由文档正文的确定性抽取生成，适合作为论文初稿，"
        "正式提交前应结合原文逐条核对引用和结论。"
    )
    lines.extend(["", "## 2. 研究主题与关键词", ""])
    lines.append("、".join(keywords) if keywords else "文档正文未提取到足够的关键词。")
    lines.extend(["", "## 3. 主要研究内容与方法", ""])
    for idx, doc in enumerate(documents, 1):
        lines.append(f"### {idx}. {doc['title']}")
        snippets = _snippets(doc["text"])
        lines.append(" ".join(snippets) if snippets else "该文档未提取到可用正文段落。")
    lines.extend(["", "## 4. 综合评述", ""])
    if keywords:
        lines.append(
            "从资料中可以看到，相关研究主要围绕"
            + "、".join(keywords[:5])
            + "展开，常见路径是先完成数据清洗与指标定义，再选择统计分析、"
            "优化或预测模型进行验证。不同文献的研究对象和数据口径存在差异，"
            "因此模型结论需要结合题目约束、样本规模和评价指标解释。"
        )
    else:
        lines.append("现有材料的主题词较分散，建议补充摘要、关键词或更多正文后再归纳。")
    lines.extend(["", "## 5. 不足与展望", ""])
    lines.append(
        "现阶段综述主要依据文档文本和高频词完成，尚未自动判断研究设计质量、"
        "数据可比性或引用关系。后续可补充人工筛选、统一评价指标、开展稳健性"
        "检验，并将综述中的方法与本项目的建模方案逐一对应。"
    )
    lines.extend(["", "## 参考文献", ""])
    lines.extend(f"[{i}] {name}" for i, name in enumerate(names, 1))
    markdown = "\n".join(lines).strip() + "\n"
    return {"title": title, "document_count": len(documents),
            "documents": [{"name": d["name"], "title": d["title"],
                           "paragraphs": len(d["paragraphs"]),
                           "format": d.get("format", Path(d["name"]).suffix.lstrip(".")),
                           "pages": d.get("pages")} for d in documents],
            "keywords": keywords, "markdown": markdown}


def write_review_docx(review: dict, out_dir: str | Path = DEFAULT_OUT_DIR) -> Path:
    """把综述写成可继续编辑的 Word 文档并返回仓库相对路径。"""
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = target_dir / f"review-{stamp}.docx"
    doc = Document()
    for line in review["markdown"].splitlines():
        if line.startswith("# "):
            doc.add_heading(line[2:], level=0)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=1)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=2)
        elif line.startswith("["):
            doc.add_paragraph(line, style="List Number")
        elif line:
            doc.add_paragraph(line)
    doc.save(path)
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path.resolve()


def generate_review(items: list[dict], title: str = "数模论文文献综述",
                    out_dir: str | Path = DEFAULT_OUT_DIR) -> dict:
    """校验、解析并生成综述。"""
    if not items:
        raise ValueError("至少上传一篇 .docx 或 .pdf 文献")
    if len(items) > MAX_DOCUMENTS:
        raise ValueError(f"一次最多上传 {MAX_DOCUMENTS} 篇文献")
    names = [_validate_name(str(item.get("name") or "")) for item in items]
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise ValueError("上传文档不能重名（文件名不区分大小写）")
    decoded: list[tuple[str, bytes]] = []
    total_bytes = 0
    for name, item in zip(names, items):
        data = _decode_doc(name, str(item.get("b64") or ""))
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_DOCUMENT_BYTES:
            raise ValueError("本次上传文档总大小不能超过 80 MB")
        decoded.append((name, data))
    extracted = [
        extract_pdf(name, data) if Path(name).suffix.lower() == ".pdf"
        else extract_docx(name, data)
        for name, data in decoded
    ]
    clean_title = _clean(title) or "数模论文文献综述"
    if len(clean_title) > MAX_TITLE_LENGTH:
        raise ValueError(f"综述标题不能超过 {MAX_TITLE_LENGTH} 个字符")
    review = build_review(extracted, clean_title)
    output = write_review_docx(review, out_dir)
    review["output_docx"] = output.as_posix()
    return review
