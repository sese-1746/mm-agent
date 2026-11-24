"""NFR-4 度量纯函数：哈希、目录扫描、`sha256sum` 读写、基线比对、样本量纪律。

**本模块只用标准库**（`hashlib` / `os` / `pathlib` / `typing`），无网络、无第三方依赖。
无隐式 I/O 副作用：只有显式传路径的 `sha256_file` / `scan_files` / `load_sums` / `dump_sums`
会读写磁盘，`compare_sums` / `summarize_runs` 是纯计算。

契约来源：`.agent-runs/2026-10-17/task-003/README.md` §3.2（逐字实现，实现方无权调整语义）。

口径提醒（README §3.1）：golden 真值是**本产线在冻结输入下的确定性产物哈希**，
不是规划书 §4.2.1 的 `row_count` 抽检值（该三值已在 §5 穷举证伪）。

**哈希口径 = `HASH_CALIBER = "text-eol-normalized(LF)"`（D-19，2026-10-17 主理人裁定）**：
对 `TEXT_EXTS` 内的文本文件，先把 `\r\n` 与孤立 `\r` 统一成 `\n` 再取 sha256；二进制后缀原样哈希。
**为什么必须这样**：仓库 `.gitattributes` 强制 `* text=auto eol=lf`，而 Windows 工作树里的产线产物是
CRLF —— 实测 8/8 文件 `git ls-files --eol` 为 `i/lf w/crlf`，且 `HEAD` blob 哈希与工作树哈希 8/8 不同。
若按原始字节冻结，**干净 clone（LF 检出）上必然 8 个 modified**，golden 完全不可移植。
**代价（明确接受）**：纯换行符变更（CRLF↔LF）**不可检出**；内容任何其它改动仍可检出。
"""
import hashlib
import os
from pathlib import Path
from typing import Mapping, Sequence

#: `scan_files` 跳过的目录名（与 `.gitignore` 同口径：构建/测试缓存不进基线）
_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache"})
#: `scan_files` 跳过的文件后缀（`.gitignore` 的 `*.tmp`）
_SKIP_SUFFIX = ".tmp"
#: `load_sums` 接受的十六进制字符集
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
#: 样本量下限：低于此值**只报 k/N，不报比率**（README §2 第 6 条）
MIN_RATE_N = 10
#: 参与 EOL 规范化的文本后缀；不在表内的一律**原样**哈希（二进制不做任何改写）
TEXT_EXTS = frozenset({".json", ".csv", ".md", ".txt", ".py", ".yaml", ".yml",
                       ".sql", ".j2", ".html", ".htm", ".css", ".js", ".ts", ".ipynb"})
#: 冻结哈希口径标识（同时写入 `tests/golden/cases.json` 的 `hash_caliber`）
HASH_CALIBER = "text-eol-normalized(LF)"


def canonical_bytes(data: bytes, suffix: str) -> bytes:
    """把文本类内容规范化为 LF；非 `TEXT_EXTS` 后缀原样返回。

    **顺序不可颠倒**：先替换 `\\r\\n` → `\\n`，再替换孤立 `\\r` → `\\n`；
    反过来做会把一个 CRLF 变成两个 LF，导致同一内容产生两种哈希。
    """
    if suffix.lower() not in TEXT_EXTS:
        return data
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256_blob(data: bytes, suffix: str) -> str:
    """对内存字节按 `HASH_CALIBER` 取 sha256（测试可直接喂字节，不落盘）。"""
    return hashlib.sha256(canonical_bytes(data, suffix)).hexdigest()


def sha256_file(path: str | Path) -> str:
    """返回文件的 sha256（小写 64 字符），按 `HASH_CALIBER` 先做 EOL 规范化。

    签名保持 `sha256_file(path)` 不变（调用方无需感知口径）。
    为做规范化需整文件读入内存；本仓库最大产物 221 KB，可接受。
    """
    path = Path(path)
    return sha256_blob(path.read_bytes(), path.suffix)


def scan_files(root: str | Path, *, rel_to: str | Path | None = None) -> dict[str, str]:
    """递归收集 `root` 下的普通文件 → `{相对路径(posix 分隔): sha256}`。

    - `rel_to` 缺省 = `root`；给出时相对路径以此为基准（`rel_to` 必须是 `root` 的祖先，
      否则抛 `ValueError`）。
    - 跳过 `__pycache__` / `.pytest_cache` 目录，以及以 `.tmp` 结尾的文件（与 `.gitignore` 同口径）。
    - 返回 dict 按 key 升序构造，便于直接 `json.dumps` 得到稳定输出。
    """
    root = Path(root)
    base = Path(rel_to) if rel_to is not None else root
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(_SKIP_SUFFIX):
                continue
            path = Path(dirpath) / name
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError as exc:  # rel_to 不是 root 的祖先
                raise ValueError(f"rel_to={base} 不是 root={root} 的祖先目录") from exc
            found[rel] = sha256_file(path)
    return dict(sorted(found.items()))


def load_sums(path: str | Path) -> dict[str, str]:
    """解析 `sha256sum` 兼容格式 → `{路径: sha256}`（保持文件内顺序）。

    规则（README §3.2）：
    - 空行与首字符为 `#` 的行忽略；
    - 每行 `<64位hex><两个空格><路径>`，亦接受单空格 + `*` 的二进制标记；
    - 路径含空格时，前 64 字符为哈希，其后 `strip()` 后整体为路径；
    - 重复路径 → `ValueError`（不允许后者覆盖前者）；
    - 格式非法 → `ValueError`，报文含**行号**。
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    sums: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) < 66:
            raise ValueError(f"{path}:{lineno}: 行长不足 66（哈希 64 + 分隔符 + 路径）：{line!r}")
        digest, rest = line[:64], line[64:]
        if any(ch not in _HEX_DIGITS for ch in digest):
            raise ValueError(f"{path}:{lineno}: 前 64 字符不是合法 sha256 十六进制：{digest!r}")
        if rest.startswith("  "):
            rel = rest[2:]
        elif rest.startswith(" *"):        # sha256sum -b 的二进制标记
            rel = rest[2:]
        elif rest.startswith(" "):
            rel = rest[1:]
        else:
            raise ValueError(f"{path}:{lineno}: 哈希与路径之间缺少空格分隔：{line!r}")
        rel = rel.strip()
        if not rel:
            raise ValueError(f"{path}:{lineno}: 路径为空")
        if rel in sums:
            raise ValueError(f"{path}:{lineno}: 重复路径 {rel!r}（不允许后者覆盖前者）")
        sums[rel] = digest.lower()
    return sums


def dump_sums(sums: Mapping[str, str], path: str | Path) -> int:
    """按路径升序写出 `sha256sum` 兼容文本，返回写出行数。

    强制 LF、UTF-8 无 BOM、末尾保留**一个**换行；父目录不存在则创建。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(sums)
    body = "".join(f"{sums[key]}  {key}\n" for key in ordered)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return len(ordered)


def compare_sums(expected: Mapping[str, str],
                 actual: Mapping[str, str]) -> dict:
    """比对两份 `{路径: sha256}`，返回恒定 8 键的结构（README §3.2）。

    `missing` / `extra` / `modified` **任一非空即 `ok=False`**：冻结目录里多出文件
    同样是基线漂移（D-16），不许忽略。
    """
    exp = dict(expected)
    act = dict(actual)
    missing = sorted(set(exp) - set(act))
    extra = sorted(set(act) - set(exp))
    modified = sorted(k for k in set(exp) & set(act) if exp[k] != act[k])
    return {
        "ok": not missing and not extra and not modified,
        "matched": len(set(exp) & set(act)) - len(modified),
        "missing": missing,
        "extra": extra,
        "modified": modified,
        "n_expected": len(exp),
        "n_actual": len(act),
        "detail": {k: {"expected": exp[k], "actual": act[k]} for k in modified},
    }


def summarize_runs(records: Sequence[Mapping]) -> dict:
    """汇总回归台账 → 恒定 7 键结构，**样本量纪律写死在代码里**。

    `n < 10` 时 `rate=None` 且 `rate_suppressed=True`，只报 `k_of_n`（如 `"7/9"`）；
    禁止把它换算成百分数或小数（README §2 第 6 条、§5 第 3 条）。

    记录元素至少含 `{"case": str, "ok": bool}`；缺键按失败计，`failed` 中记
    `"<index>:missing-field"`。
    """
    items = list(records)
    n = len(items)
    ok = 0
    failed: list[str] = []
    for index, record in enumerate(items):
        if not isinstance(record, Mapping) or "case" not in record or "ok" not in record:
            failed.append(f"{index}:missing-field")
            continue
        if bool(record["ok"]):
            ok += 1
        else:
            failed.append(str(record["case"]))
    suppressed = n < MIN_RATE_N
    return {
        "n": n,
        "ok": ok,
        "k_of_n": f"{ok}/{n}",
        "rate": None if suppressed else round(ok / n, 4),
        "rate_suppressed": suppressed,
        "suppress_reason": "N<10：只报 k/N，不报比率" if suppressed else "",
        "failed": sorted(failed),
    }
