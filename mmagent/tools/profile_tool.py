"""档案工具：转发 store.profile 的版本链操作。"""
from __future__ import annotations

from pathlib import Path

from ..store import profile as profile_store


def write_version(profile_id: str, patch: dict, actor: str,
                  upstream_refs: list | None = None,
                  db_path: str | Path = profile_store.DEFAULT_DB_PATH) -> int:
    """追加一个 draft 档案版本，返回新 version_id（转发 store.profile）。"""
    return profile_store.write_version(profile_id, patch, actor,
                                       upstream_refs, db_path)


def adopt(profile_id: str, from_version_id: int, actor: str,
          db_path: str | Path = profile_store.DEFAULT_DB_PATH) -> int:
    """把指定版本采纳为新的 adopted 版本，返回新 version_id。"""
    return profile_store.adopt_new_version(profile_id, from_version_id, actor, db_path)


def read(profile_id: str, version: str | int = "last",
         paths: list[str] | None = None,
         db_path: str | Path = profile_store.DEFAULT_DB_PATH) -> dict:
    """读取档案版本内容（version='last' 取最新 adopted）。"""
    return profile_store.read_profile(profile_id, version, paths, db_path)
