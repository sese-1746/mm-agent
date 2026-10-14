"""tests/ 公共夹具（只放测试基础设施，不含业务逻辑）。

职责：
1. 把仓库根目录加入 sys.path，保证 `python -m pytest tests` 能 import mmagent；
2. `tmp_db`：临时 SQLite 库（按 store/schema.sql 建表）；
3. `real_attachment_dataset`：A 题真实附件（只读源目录 → 复制到 tmp_path），
   源目录缺失时自动 skip（集成测试不得硬依赖本机数据）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A 题真实附件目录（只读；2024 校赛 A 题）
REAL_DATASET_DIR = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
REAL_XLS_GLOB = "附件[1-6]*.xls"


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """临时库路径，已按 schema.sql 建表（含触发器）。"""
    from mmagent.store.db import init_db

    db_path = tmp_path / "mm.db"
    init_db(db_path)
    return db_path


@pytest.fixture()
def real_attachment_dataset(tmp_path: Path) -> Path:
    """把 A 题附件 1~6（.xls）复制到 tmp_path/dataset，返回该目录。

    复制到临时目录可让「文件数 == 6」的断言稳定，不夹带目录里其它 xlsx。
    源目录不存在 → skip。
    """
    if not REAL_DATASET_DIR.exists():
        pytest.skip(f"真实数据目录不存在，跳过集成测试：{REAL_DATASET_DIR}")
    sources = sorted(p for p in REAL_DATASET_DIR.glob(REAL_XLS_GLOB)
                     if p.suffix.lower() == ".xls")
    if len(sources) < 6:
        pytest.skip(f"真实附件不足 6 个（实际 {len(sources)} 个）：{REAL_DATASET_DIR}")
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    for src in sources:
        shutil.copy2(src, dataset_dir / src.name)
    return dataset_dir
