"""FR-4 向量索引工具：分块 / 余弦 / 读写索引 / 算 manifest。

**零联网、零 LLM**：本模块不 import 任何 HTTP 客户端、也不 import `mmagent.agents`。
向量由外部**注入**（`embed_fn`），因此同一个工具层既能被真实构建脚本用真模型，
也能被离线用例用确定性假 embedder —— 「谁去取向量」不是本模块的事。

## 为什么索引落文件而不是建表（作者裁决 §1）

`store/schema.sql` 是冻结基线，零漂移优先。索引写 `artifacts/rag/index/`，且**必须可重建**：

- `manifest.json`：模型 / 维度 / 分块参数 / 逐份语料的 `sha256` 与块数（**可追溯**）；
- `vectors.jsonl`：每行一个块（`chunk_id/source/start/end/text/vec`，UTF-8、`ensure_ascii=False`）。

给定同一份语料 + 同一模型重跑，`chunk_id` / `source` / `start` / `end` / `text` **逐字节相同**
（向量浮点值不要求逐位相同）。`built_at` 是唯一的必然变化项。

## 可追溯不是「相信旧清单」

构建时对每份语料**实算** `sha256(text)`，与 `artifacts/exam_corpus/text/manifest.json`
里同名的 `sha256_of_text` 比对；不等就 `CorpusMismatchError` 直接退出。
只信旧清单 = 放弃追溯（清单可能描述的是上一版文本）。

## 分块策略（冻结）

按 `chunk_chars=600` 滑窗、`chunk_overlap=80` 重叠；每一步**优先在最近的换行处切**
（在下界 `chunk_chars - overlap` 与上界之间找最后一个 `\\n`），找不到才硬切。
`chunk_id = f"{source}#{序号}"`，序号**从 0 起**。
下一步起点 = 本块终点 - overlap，因此相邻块的**前 80 个字符就是上一块的尾部 80 字符**。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

# 冻结参数（README §4）：改这两个值等于改索引语义，必须同步改用例与报告
DEFAULT_CHUNK_CHARS = 600
DEFAULT_CHUNK_OVERLAP = 80
SCHEMA_VERSION = 1

MANIFEST_NAME = "manifest.json"
VECTORS_NAME = "vectors.jsonl"


class RagIndexError(Exception):
    """索引层的通用错误基类。"""


class IndexMissingError(RagIndexError):
    """索引目录/文件不存在：**显式报错**，不静默返回空结果。"""


class CorpusMismatchError(RagIndexError):
    """语料实算 sha256 与语料清单不符（或字符数不符）：拒绝构建。"""


class IndexCorruptError(RagIndexError):
    """索引文件内部不自洽（行数/向量维度/必需键与 manifest 对不上）。

    **必须显式报错而不是静默降级**：某条 `vec` 被截短时 `cosine` 会抛 `ValueError`，
    不拦就变成「500 + 类名」；`vectors.jsonl` 被截断时更阴——静默少检索若干块，
    用户完全不知道结果不全。两种都归到这里（路由 → `503 RAG_INDEX_CORRUPT`）。
    """


# ---------------------------------------------------------------- 分块


def chunk_text(text: str, chunk_chars: int = DEFAULT_CHUNK_CHARS,
               chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[dict]:
    """把一段文本切成 `[{start, end, text}]`（`text == 原文[start:end]`）。

    优先在最近的换行处切：在 `[start + chunk_chars - overlap - 1, end)` 里找最后一个
    `\\n`，找到就把终点提前到该换行处（换行本身**不进**本块，留给下一块的块首）。
    找不到换行 → 硬切 `chunk_chars`。

    重叠恒为 `chunk_overlap`：`next_start = end - chunk_overlap`，
    因此 `chunks[i+1]["text"][:chunk_overlap] == chunks[i]["text"][-chunk_overlap:]`。
    """
    if chunk_chars <= 0:
        raise ValueError(f"chunk_chars 必须为正整数，收到 {chunk_chars!r}")
    if not 0 <= chunk_overlap < chunk_chars:
        raise ValueError(
            f"chunk_overlap 必须满足 0 <= overlap < chunk_chars，收到 {chunk_overlap!r}")

    body = text or ""
    total = len(body)
    chunks: list[dict] = []
    start = 0
    while start < total:
        end = min(start + chunk_chars, total)
        if end < total:
            # 允许提前切的最早位置：保证块体至少 chunk_chars - overlap 字符（否则块会碎成噪声）
            floor = start + max(1, chunk_chars - chunk_overlap - 1)
            if floor < end:
                nl = body.rfind("\n", floor, end)
                if nl != -1:
                    end = nl
        chunks.append({"start": start, "end": end, "text": body[start:end]})
        if end >= total:
            break
        nxt = end - chunk_overlap
        # 防御：任何参数组合下都必须前进（否则会死循环）
        start = nxt if nxt > start else start + 1
    return chunks


# ---------------------------------------------------------------- 余弦 / 排序


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一向量为零向量 → 0.0（不抛 ZeroDivisionError，也不返回 NaN）。"""
    if len(a) != len(b):
        raise ValueError(f"向量维度不一致：{len(a)} vs {len(b)}")
    dot = math.fsum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(math.fsum(float(x) * float(x) for x in a))
    nb = math.sqrt(math.fsum(float(y) * float(y) for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank(query_vec: Sequence[float], records: Iterable[dict], k: int) -> list[dict]:
    """按余弦**降序**取前 k 条；同分按 `chunk_id` 升序（排序必须确定性）。"""
    if k <= 0:
        raise ValueError(f"k 必须为正整数，收到 {k!r}")
    scored = []
    for rec in records:
        scored.append({
            "chunk_id": rec["chunk_id"],
            "source": rec["source"],
            "start": rec["start"],
            "end": rec["end"],
            "text": rec["text"],
            "score": cosine(query_vec, rec["vec"]),
        })
    scored.sort(key=lambda r: (-r["score"], r["chunk_id"]))
    hits = scored[:k]
    for i, hit in enumerate(hits, start=1):
        hit["rank"] = i
        hit["score"] = round(hit["score"], 4)
    return hits


# ---------------------------------------------------------------- 索引读写


def sha256_text(text: str) -> str:
    """文本的 sha256（UTF-8 字节口径，与语料清单的 `sha256_of_text` 同口径）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus_digest(entries: Sequence[dict]) -> str:
    """把 `manifest.corpus[*].{name,sha256}` 压成一个总指纹（响应体 `index.corpus_sha256`）。

    不写进 `manifest.json`（README §4 的键集是冻结的），在读取时按同样规则算出来，
    因此「同一份语料 → 同一个指纹」且不需要多存一个可能说谎的字段。
    """
    payload = "".join(f"{e['name']}:{e['sha256']}\n" for e in entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_corpus_manifest(corpus_dir: Path) -> dict:
    path = corpus_dir / "manifest.json"
    if not path.is_file():
        raise IndexMissingError(f"语料清单不存在：{path}（请先跑 cli exam-text 抽取真题文本）")
    return json.loads(path.read_text(encoding="utf-8"))


def display_path(path: Path) -> str:
    """仓库内路径 → POSIX 相对路径；不在仓库下则给绝对 POSIX 路径。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_index(corpus_dir: str | Path, out_dir: str | Path,
                embed_fn: Callable[[list[str]], list[list[float]]], *,
                model: str, built_at: str,
                chunk_chars: int = DEFAULT_CHUNK_CHARS,
                chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
                batch_size: int = 8) -> dict:
    """从语料目录构建索引并落盘，返回 manifest。

    - `embed_fn(texts) -> list[list[float]]`：向量由外部注入（本模块不碰网络）；
    - `model` / `built_at` 由调用方给（本模块不读配置、不读时钟 → 可复现）；
    - 语料清单里的每一条都必须有对应 `.txt`，且**实算 sha256** 与之相等，否则
      `CorpusMismatchError`（不写任何文件）。
    """
    corpus_path = Path(corpus_dir)
    out_path = Path(out_dir)
    corpus_manifest = _read_corpus_manifest(corpus_path)

    docs: list[dict] = []
    records: list[dict] = []
    for name in sorted(corpus_manifest):
        meta = corpus_manifest[name] or {}
        text_path = corpus_path / f"{name}.txt"
        if not text_path.is_file():
            raise CorpusMismatchError(f"语料清单声明了 {name}，但文件不存在：{text_path}")
        text = text_path.read_text(encoding="utf-8")
        digest = sha256_text(text)
        expected = str(meta.get("sha256_of_text") or "")
        if digest != expected:
            raise CorpusMismatchError(
                f"语料 {name} 的实算 sha256 与清单不符：实算 {digest}，清单 {expected}"
                "（索引拒绝按旧清单构建）")
        if meta.get("chars") is not None and int(meta["chars"]) != len(text):
            raise CorpusMismatchError(
                f"语料 {name} 的字符数不符：实算 {len(text)}，清单 {meta['chars']}")
        pieces = chunk_text(text, chunk_chars, chunk_overlap)
        docs.append({
            "name": name,
            "path": display_path(text_path),
            "sha256": digest,
            "chars": len(text),
            "chunks": len(pieces),
        })
        for idx, piece in enumerate(pieces):
            records.append({
                "chunk_id": f"{name}#{idx}",
                "source": name,
                "start": piece["start"],
                "end": piece["end"],
                "text": piece["text"],
            })

    if not records:
        raise RagIndexError("语料为空：没有任何可索引的文本块")

    vectors: list[list[float]] = []
    step = max(1, int(batch_size))
    texts = [rec["text"] for rec in records]
    for i in range(0, len(texts), step):
        batch = texts[i:i + step]
        got = embed_fn(batch)
        if len(got) != len(batch):
            raise RagIndexError(
                f"embedding 返回条数不符：请求 {len(batch)} 条，返回 {len(got)} 条")
        vectors.extend(got)

    dims = len(vectors[0])
    for rec, vec in zip(records, vectors):
        if len(vec) != dims:
            raise RagIndexError(
                f"embedding 维度不一致：{rec['chunk_id']} 为 {len(vec)}，首条为 {dims}")
        rec["vec"] = [float(x) for x in vec]

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "dims": dims,
        "built_at": built_at,
        "chunk_chars": int(chunk_chars),
        "chunk_overlap": int(chunk_overlap),
        "chunks": len(records),
        "corpus": docs,
    }
    write_index(out_path, manifest, records)
    return manifest


def write_index(out_dir: str | Path, manifest: dict, records: Sequence[dict]) -> None:
    """落 `manifest.json` + `vectors.jsonl`（UTF-8 无 BOM、LF、`ensure_ascii=False`）。"""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rec, ensure_ascii=False) for rec in records]
    (out_path / VECTORS_NAME).write_text("\n".join(lines) + "\n",
                                         encoding="utf-8", newline="\n")
    (out_path / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")


REQUIRED_RECORD_KEYS = ("chunk_id", "source", "start", "end", "text", "vec")


def load_index(index_root: str | Path) -> tuple[dict, list[dict]]:
    """读索引 → `(manifest, records)`，**并校验自洽性**。

    三类失败分开报（都不返回空列表、都不静默少检索）：

    - 文件不在 → `IndexMissingError`；
    - 文件在但内部不自洽（行数 ≠ `manifest.chunks` / 某条 `vec` 长度 ≠ `manifest.dims` /
      缺必需键）→ `IndexCorruptError`（路由 → `503 RAG_INDEX_CORRUPT`）；
    - 解析不了（JSON 坏）→ 由 `json.JSONDecodeError` 冒泡，路由兜到 500（那是**代码/文件
      被外部改坏**的极端情形，不属于契约内的四种业务错）。

    「静默少检索」是最坏的一种：`vectors.jsonl` 被截断后仍能正常返回结果，只是少了几块，
    用户永远不知道 —— 所以行数与 manifest 必须对账。
    """
    root = Path(index_root)
    manifest_path = root / MANIFEST_NAME
    vectors_path = root / VECTORS_NAME
    if not manifest_path.is_file() or not vectors_path.is_file():
        raise IndexMissingError(
            f"RAG 索引不存在：{display_path(root)}（缺 {MANIFEST_NAME} 或 {VECTORS_NAME}）；"
            "先跑 cli rag rebuild 重建索引")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[dict] = []
    with vectors_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if not records:
        raise IndexMissingError(
            f"RAG 索引为空：{display_path(vectors_path)}；先跑 cli rag rebuild 重建索引")

    expected = manifest.get("chunks")
    if expected is not None and int(expected) != len(records):
        raise IndexCorruptError(
            f"RAG 索引损坏（先跑 cli rag rebuild 重建索引）："
            f"{display_path(vectors_path)} 有 {len(records)} 行，manifest.chunks={expected}"
            "（可能被截断或追加）")
    dims = manifest.get("dims")
    for rec in records:
        missing = [k for k in REQUIRED_RECORD_KEYS if k not in rec]
        if missing:
            raise IndexCorruptError(
                f"RAG 索引损坏（先跑 cli rag rebuild 重建索引）："
                f"块 {rec.get('chunk_id', '<无 chunk_id>')} 缺字段 {missing}")
        if dims is not None and len(rec["vec"]) != int(dims):
            raise IndexCorruptError(
                f"RAG 索引损坏（先跑 cli rag rebuild 重建索引）："
                f"块 {rec['chunk_id']} 的向量维度 {len(rec['vec'])} ≠ manifest.dims={dims}")
    return manifest, records
