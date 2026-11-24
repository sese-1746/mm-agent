"""上传路由（第 35 轮）：把题面 PDF + 附件收进 `data/uploads/<profile_id>/`。

**为什么是 base64 JSON 而不是 multipart**：全仓未安装 `python-multipart`（`UploadFile`/`File()`
零命中），且本轮禁装包 → `multipart/form-data` 解析不了。故统一走
`application/json` + 每文件一段 base64（前端 `core/api.js::fileToB64` 生成）。

落盘与判定约定（冻结契约见本轮 README §1.1，独立验收补充项见 task-002 §3-2/§3-3/§3-8/§3-9/§3-10）：

- 允许扩展名**只有** `.pdf` / `.xls` / `.xlsx`（大小写不敏感），其它 → `400 UPLOAD_UNSUPPORTED_TYPE`；
- `files` 为空 → `400 UPLOAD_EMPTY`；
- 单文件解码后 > 20 MB 或合计 > 40 MB → `413 UPLOAD_TOO_LARGE`；
- base64 非法 → `422 UPLOAD_BAD_BASE64`；
- **文件名非法**（NUL/控制字符、路径分隔符、Windows 非法字符、`..`、保留设备名、首尾空白或点、
  超 200 字符、同批重名）→ `400 UPLOAD_BAD_FILENAME`。**不静默改名**——用户看到的名字必须就是
  磁盘上的名字（否则 `saved_as != name` 又是一处「机器绿、人肉漏」）。
- **一个 PDF 都没有** → `400 UPLOAD_NO_PROBLEM_PDF`：只收到 Excel 时 `problem_text` 必然为空，
  下游（FR-1 题面解析 / FR-9 建档）拿到一份没有题面的档案，错误会延迟到很后面才爆且看不出因果。
- `problem_name`（可选）必须指向本次上传的一个 **PDF**，否则 `422 UPLOAD_PROBLEM_NOT_FOUND`；
  缺省取**第一个 PDF**（与前端「第一个 PDF 当题面」的重排一致）。
- PDF 用 `tools/pdf_text.py::extract_pdf_text` 抽文字层；抽不到 → `text_layer: false` 并进 `warnings`
  （扫描件是可诊断的降级，不是失败）。

**校验顺序 = 契约**：全部校验（文件名 → 扩展名 → base64 解码 → 大小 → 有无题面 PDF → problem_name）
都在 `_new_profile_dir()` **之前**完成；写盘失败则回收刚建的目录。即「**失败不留痕**」——
用户重试一次不该多出一个空档案目录。

`profile_id` 自动生成：`P` + `YYYYMMDD-HHMMSS`（**本地时间**）；同秒冲突追加 `-2`、`-3`…
`UPLOADS_ROOT` 是模块级常量而非硬编码路径，测试/探针可 monkeypatch 到 `tmp_path`，避免污染工作区。
"""
from __future__ import annotations

import base64
import binascii
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..tools.pdf_text import extract_pdf_text

router = APIRouter(prefix="/api/v1", tags=["upload"])

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOADS_ROOT = REPO_ROOT / "data" / "uploads"

_PDF_EXTS = {".pdf"}
_DATA_EXTS = {".xls", ".xlsx"}
ALLOWED_EXTS = _PDF_EXTS | _DATA_EXTS

# 单文件 20 MB / 合计 40 MB（解码后的字节数，不是 base64 文本长度）
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_NAME_LEN = 200

# Windows 文件名里不允许的字符（`\x00` 与全部控制字符另行判）
_ILLEGAL_PATH_CHARS = set('\x00/\\:*?"<>|')
# Windows 保留设备名（大小写不敏感；带扩展名同样被占用，如 `CON.pdf`）
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}

SCANNED_WARNING = "{name} 是扫描件，抽不到文字层；请人工转录题面后粘贴到『题面文本』框"
ATTACHMENT_PDF_WARNING = "附件 PDF 只抽文本、不做数值体检（体检仅支持 .xls/.xlsx）"


class UploadFileItem(BaseModel):
    """一个待落盘文件：`name` 保留原名，`b64` 是文件字节的 base64。"""
    name: str = Field(min_length=1)
    b64: str


class UploadRequest(BaseModel):
    """上传请求体。`files` 允许为空列表——空则按契约回 `400 UPLOAD_EMPTY`（不是 422）。"""
    files: list[UploadFileItem] = Field(default_factory=list)
    problem_name: str | None = None


def _bad(status: int, code: str, message: str) -> HTTPException:
    """统一错误体入口：`detail` 带 `error_code`，由 `app.py::_http_handler` 泛化分支取用。"""
    return HTTPException(status_code=status,
                         detail={"error_code": code, "message": message})


def _display(path: Path) -> str:
    """仓库内路径 → POSIX 相对路径（如 `data/uploads/P2026...`）；不在 CWD 下则给绝对路径。"""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _decode(item: UploadFileItem) -> bytes:
    """base64 → 字节。非法 base64 抛 422 `UPLOAD_BAD_BASE64`。"""
    try:
        return base64.b64decode(item.b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad(422, "UPLOAD_BAD_BASE64",
                   f"{item.name} 的 base64 解码失败：{exc}") from exc


def _check_name(name: str) -> str:
    """校验并**原样**返回文件名；非法 → 400 `UPLOAD_BAD_FILENAME`（不做任何静默改写）。

    「静默改名」比拒绝更坏：`PureWindowsPath("a:b.pdf").name` 会变成 `b.pdf`，响应里
    `name` 与 `saved_as` 从此对不上，而用户毫无线索（task-002 §3-10）。
    """
    reason: str | None = None
    if not name or not name.strip():
        reason = "文件名不能为空"
    elif any(ch in _ILLEGAL_PATH_CHARS or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
        reason = "含路径分隔符/控制字符/Windows 非法字符"
    elif name != name.strip() or name.endswith("."):
        reason = "首尾不能是空白，结尾不能是点"
    elif ".." in name:
        reason = "不能含 `..` 路径段"
    elif name.split(".")[0].upper() in _RESERVED_NAMES:
        reason = "是 Windows 保留设备名"
    elif len(name) > MAX_NAME_LEN:
        reason = f"文件名过长（>{MAX_NAME_LEN} 字符）"
    if reason:
        raise _bad(400, "UPLOAD_BAD_FILENAME",
                   f"文件名 {name!r} 不合法：{reason}（请改名后重传）")
    return name


def _new_profile_dir() -> tuple[str, Path]:
    """生成 `P<YYYYMMDD-HHMMSS>` 并**原子**占用目录；同秒冲突追加 `-2`、`-3`…"""
    base = "P" + datetime.now().strftime("%Y%m%d-%H%M%S")
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        profile_id = base if n == 1 else f"{base}-{n}"
        target = UPLOADS_ROOT / profile_id
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            n += 1
            continue
        return profile_id, target


@router.post("/upload")
def upload(req: UploadRequest) -> dict:
    """接收 base64 JSON，落盘到 `data/uploads/<profile_id>/` 并抽题面 PDF 文本。

    校验全部前置（见模块 docstring）：任一文件不合法都**零落盘、零建目录**。
    """
    if not req.files:
        raise _bad(400, "UPLOAD_EMPTY",
                   "files 为空：至少要上传一个 .pdf/.xls/.xlsx 文件")

    names = [_check_name(f.name) for f in req.files]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise _bad(400, "UPLOAD_BAD_FILENAME",
                   f"同一批里有重名文件：{sorted(dup)}（会互相覆盖，请改名后重传）")

    for name in names:
        if Path(name).suffix.lower() not in ALLOWED_EXTS:
            raise _bad(400, "UPLOAD_UNSUPPORTED_TYPE",
                       f"不支持的文件类型：{name}（只允许 .pdf / .xls / .xlsx）")

    # 先全量解码 + 量尺寸，再落盘：任一文件不合法就一个字节都不写（不留半截目录）
    payloads: list[bytes] = []
    total = 0
    for item, name in zip(req.files, names):
        data = _decode(item)
        if len(data) > MAX_FILE_BYTES:
            raise _bad(413, "UPLOAD_TOO_LARGE",
                       f"{name} 解码后 {len(data)} 字节，超过单文件上限 "
                       f"{MAX_FILE_BYTES} 字节（20 MB）")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise _bad(413, "UPLOAD_TOO_LARGE",
                       f"合计 {total} 字节，超过总量上限 {MAX_TOTAL_BYTES} 字节（40 MB）")
        payloads.append(data)

    # 题面必须是 PDF：没有 PDF 等于交了一份「没有题面」的档案（`problem_text` 必为空）
    pdf_names = [n for n in names if Path(n).suffix.lower() in _PDF_EXTS]
    if not pdf_names:
        raise _bad(400, "UPLOAD_NO_PROBLEM_PDF",
                   f"题面必须是 PDF：本次只收到 Excel 附件（{len(names)} 个），"
                   "请把题面 PDF 一起传上来")

    problem_file = req.problem_name or pdf_names[0]
    if problem_file not in pdf_names:
        raise _bad(422, "UPLOAD_PROBLEM_NOT_FOUND",
                   f"problem_name={req.problem_name!r} 不是本次上传的 PDF 之一：{pdf_names}")

    profile_id, target_dir = _new_profile_dir()
    try:
        for name, data in zip(names, payloads):
            (target_dir / name).write_bytes(data)
    except (OSError, ValueError) as exc:   # 写盘失败不能落成 500 类名，也不能留空目录
        shutil.rmtree(target_dir, ignore_errors=True)
        raise _bad(400, "WRITE_FAILED", f"上传落盘失败：{exc}") from exc

    warnings: list[str] = []
    files_out: list[dict] = []
    problem_text = ""
    for item, name, data in zip(req.files, names, payloads):
        if Path(name).suffix.lower() in _DATA_EXTS:
            # 体检（FR-2）只认 .xls/.xlsx；附件一律 text_chars=0 / text_layer=null
            kind, chars, layer = "data", 0, None
        else:
            kind = "problem" if name == problem_file else "attachment_pdf"
            text = extract_pdf_text(target_dir / name).strip()
            chars, layer = len(text), bool(text)
            if kind == "problem":
                problem_text = text
            if not layer:
                warnings.append(SCANNED_WARNING.format(name=name))
            if kind == "attachment_pdf":
                warnings.append(ATTACHMENT_PDF_WARNING)
        files_out.append({"name": item.name, "saved_as": name, "kind": kind,
                          "bytes": len(data),
                          "text_chars": chars, "text_layer": layer})

    return {"profile_id": profile_id,
            "dataset_dir": _display(target_dir),
            "problem_text": problem_text,
            "problem_file": problem_file,
            "files": files_out,
            "warnings": warnings}
