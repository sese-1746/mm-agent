"""PDF 文本层抽取（第 35 轮）：供 `POST /upload` 把题面 PDF 变成 `problem_text`。

依赖说明：`python-multipart` 未安装且装不了，上传走 base64 JSON；PDF 解析用已装的
`pypdf 6.21.1`（`deploy/wheelhouse/pypdf-6.21.1-py3-none-any.whl`）。

契约（与 `POST /upload` 的 `text_layer` 判据配套）：

- **有文字层** → 返回该文本；调用方据此判 `text_layer: true`；
- **没有文字层**（扫描件/图片页）→ 返回**空串**；
- **任何异常都不外抛**（加密 PDF / 结构损坏 / 单页抽取失败）→ 同样返回空串或已抽到的部分。

之所以「不抛异常」：`text_layer` 是**可诊断的降级信号**（→ 进 `warnings`，提示人工转录），
不是失败。把它抛成异常会让上传整体失败，而用户真正需要的是一条明确的提醒。
"""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

# 单页抽取失败的兜底不在循环里抛：逐页尽力，拿不到的页按空串计。
_PAGE_SEP = "\n"


def extract_pdf_text(path: str | Path) -> str:
    """抽取 PDF 全文；抽不出文字（扫描件/加密/损坏）时返回空串，**不抛异常**。"""
    try:
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001  单页失败不拖垮整份文件
                pages.append("")
        return _PAGE_SEP.join(pages)
    except Exception:  # noqa: BLE001  打不开/加密/结构损坏 → 视为无文字层
        return ""
