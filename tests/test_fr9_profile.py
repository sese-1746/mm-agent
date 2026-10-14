"""FR-9 档案版本链测试（append-only / adopted 行不可变 / 草案保留最近 5 版）。

被测模块：mmagent/store/db.py、mmagent/store/schema.sql、mmagent/store/profile.py
覆盖 success_criteria：
- 连续 write_version 写 8 次 → draft 恰好 5 版且 version_id 严格递增
- adopt_new_version 只追加、不 UPDATE 旧行；supersedes 链可查
- 对 adopted 行 UPDATE/DELETE → 触发器抛错 → AdoptedVersionImmutableError
- 事件表留下 error_code='ADOPTED_IMMUTABLE' 记录
- read_profile('last') 优先最新 adopted；patch / upstream_refs JSON 往返一致
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from mmagent.store import profile as P
from mmagent.store.db import connect, init_db

PROFILE = "profile-A"
PROFILE_B = "profile-B"


# ------------------------------------------------------------------ 工具

def _rows(db_path, sql: str, args: tuple = ()) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _versions(db_path, status: str | None = None) -> list[int]:
    sql = "SELECT version_id FROM profile_doc WHERE profile_id=? "
    args: tuple = (PROFILE,)
    if status:
        sql += "AND status=? "
        args = (PROFILE, status)
    sql += "ORDER BY version_id"
    return [r["version_id"] for r in _rows(db_path, sql, args)]


def _events(db_path, event_type: str) -> list[dict]:
    return _rows(db_path, "SELECT * FROM events WHERE event_type=? ORDER BY id",
                 (event_type,))


# ------------------------------------------------------------------ 建库

def test_init_db_幂等_且建出版本链表与不可变触发器(tmp_path):
    db_path = tmp_path / "mm.db"
    init_db(db_path)
    init_db(db_path)  # 幂等：二次执行不报错
    tables = {r["name"] for r in _rows(
        db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"profile_doc", "events", "artifacts", "metric_obs"} <= tables
    triggers = {r["name"] for r in _rows(
        db_path, "SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"trg_profile_adopted_immutable", "trg_profile_adopted_nodelete"} <= triggers


# ------------------------------------------------------------------ 草案版本链

def test_连续写8次草案_只保留最近5版且版本号严格递增(tmp_db):
    returned = [P.write_version(PROFILE, {"n": i}, "planner", db_path=tmp_db)
                for i in range(1, 9)]

    assert returned == list(range(1, 9)), "返回的 version_id 应严格递增且连续"
    assert _versions(tmp_db, status="draft") == [4, 5, 6, 7, 8], "草案应只保留最近 5 版"
    assert len(_versions(tmp_db, status="draft")) == P.DRAFT_KEEP == 5


def test_每次写入草案都留下一条事件流水(tmp_db):
    for i in range(1, 9):
        P.write_version(PROFILE, {"n": i}, "planner", db_path=tmp_db)

    events = _events(tmp_db, "profile.write_version")
    assert len(events) == 8, "事件流水不得因草案裁剪而丢失"
    payloads = [json.loads(e["payload"]) for e in events]
    assert [p["version_id"] for p in payloads] == list(range(1, 9))
    assert all(p["status"] == "draft" for p in payloads)


def test_草案裁剪不影响不同档案(tmp_db):
    for i in range(1, 7):
        P.write_version(PROFILE, {"n": i}, "planner", db_path=tmp_db)
    for i in range(1, 4):
        P.write_version(PROFILE_B, {"n": i}, "planner", db_path=tmp_db)

    assert _versions(tmp_db, status="draft") == [2, 3, 4, 5, 6]
    rows = _rows(tmp_db, "SELECT version_id FROM profile_doc WHERE profile_id=?"
                 " ORDER BY version_id", (PROFILE_B,))
    assert [r["version_id"] for r in rows] == [1, 2, 3]


def test_写入时upstream_refs缺省为空列表(tmp_db):
    vid = P.write_version(PROFILE, {"a": 1}, "planner", db_path=tmp_db)
    assert P.read_profile(PROFILE, vid, db_path=tmp_db)["upstream_refs"] == []


# ------------------------------------------------------------------ adopt 语义

def test_adopt_只追加新行_旧草案行原封不动(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    before = _rows(tmp_db, "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                   (PROFILE, vid))[0]

    adopted_id = P.adopt_new_version(PROFILE, vid, "human:leader", db_path=tmp_db)

    assert adopted_id == vid + 1, "adopt 应追加在链尾"
    after = _rows(tmp_db, "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                  (PROFILE, vid))[0]
    assert after == before, "源草案行不得被 UPDATE"
    assert after["status"] == "draft"

    adopted = P.read_profile(PROFILE, adopted_id, db_path=tmp_db)
    assert adopted["status"] == "adopted"
    assert adopted["patch"] == {"plan": "v1"}
    assert adopted["supersedes"] == vid


def test_再次adopt_旧adopted行保持adopted且不可变_链可追溯(tmp_db):
    v1 = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    v2 = P.adopt_new_version(PROFILE, v1, "human:leader", db_path=tmp_db)
    snapshot_v2 = _rows(tmp_db,
                        "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                        (PROFILE, v2))[0]

    v3 = P.adopt_new_version(PROFILE, v2, "human:leader", db_path=tmp_db)

    after_v2 = _rows(tmp_db,
                     "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                     (PROFILE, v2))[0]
    assert after_v2 == snapshot_v2, "旧 adopted 行不得被 UPDATE（靠 version_id 自然退位）"
    assert after_v2["status"] == "adopted"

    # supersedes 链：v3 → v2 → v1
    assert P.read_profile(PROFILE, v3, db_path=tmp_db)["supersedes"] == v2
    assert P.read_profile(PROFILE, v2, db_path=tmp_db)["supersedes"] == v1
    assert _versions(tmp_db, status="adopted") == [v2, v3]


def test_adopt_继承源版本patch与upstream_refs(tmp_db):
    refs = ["artifact:12", "event:34"]
    vid = P.write_version(PROFILE, {"k": "中文值"}, "planner",
                          upstream_refs=refs, db_path=tmp_db)
    adopted_id = P.adopt_new_version(PROFILE, vid, "human:leader", db_path=tmp_db)

    adopted = P.read_profile(PROFILE, adopted_id, db_path=tmp_db)
    assert adopted["patch"] == {"k": "中文值"}
    assert adopted["upstream_refs"] == refs


def test_adopt_后版本号仍严格递增(tmp_db):
    v1 = P.write_version(PROFILE, {"n": 1}, "planner", db_path=tmp_db)
    v2 = P.adopt_new_version(PROFILE, v1, "human", db_path=tmp_db)
    v3 = P.write_version(PROFILE, {"n": 2}, "planner", db_path=tmp_db)
    v4 = P.adopt_new_version(PROFILE, v3, "human", db_path=tmp_db)

    assert [v1, v2, v3, v4] == [1, 2, 3, 4]
    all_ids = _rows(tmp_db, "SELECT version_id FROM profile_doc WHERE profile_id=?"
                    " ORDER BY version_id", (PROFILE,))
    assert [r["version_id"] for r in all_ids] == sorted({v1, v2, v3, v4})


def test_adopt_不存在的源版本抛KeyError(tmp_db):
    with pytest.raises(KeyError):
        P.adopt_new_version(PROFILE, 99, "human", db_path=tmp_db)


# ------------------------------------------------------------------ adopted 不可变

def test_直接SQL_UPDATE_adopted行_触发器拦截(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    adopted_id = P.adopt_new_version(PROFILE, vid, "human", db_path=tmp_db)

    conn = connect(tmp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute("UPDATE profile_doc SET patch='{}' WHERE profile_id=?"
                         " AND version_id=?", (PROFILE, adopted_id))
        assert "adopted immutable" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()

    assert P.read_profile(PROFILE, adopted_id, db_path=tmp_db)["patch"] == {"plan": "v1"}


def test_直接SQL_DELETE_adopted行_触发器拦截(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    adopted_id = P.adopt_new_version(PROFILE, vid, "human", db_path=tmp_db)

    conn = connect(tmp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute("DELETE FROM profile_doc WHERE profile_id=? AND version_id=?",
                         (PROFILE, adopted_id))
        assert "adopted immutable" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()

    assert P.read_profile(PROFILE, adopted_id, db_path=tmp_db)["status"] == "adopted"


def test_try_update_row_对adopted行抛AdoptedVersionImmutableError(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    adopted_id = P.adopt_new_version(PROFILE, vid, "human", db_path=tmp_db)

    with pytest.raises(P.AdoptedVersionImmutableError) as exc:
        P.try_update_row(PROFILE, adopted_id, db_path=tmp_db, patch={"hacked": True})

    assert f"v{adopted_id}" in str(exc.value)
    assert P.read_profile(PROFILE, adopted_id, db_path=tmp_db)["patch"] == {"plan": "v1"}


def test_try_update_row_对draft行可正常修改(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)

    P.try_update_row(PROFILE, vid, db_path=tmp_db, patch={"plan": "v1-edited"},
                     actor="human:leader")

    row = P.read_profile(PROFILE, vid, db_path=tmp_db)
    assert row["patch"] == {"plan": "v1-edited"}
    assert row["actor"] == "human:leader"


def test_try_update_row_无字段时不改动任何行(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    before = _rows(tmp_db, "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                   (PROFILE, vid))[0]

    assert P.try_update_row(PROFILE, vid, db_path=tmp_db) is None

    after = _rows(tmp_db, "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                  (PROFILE, vid))[0]
    assert after == before


def test_不可变违规写入事件表ADOPTED_IMMUTABLE(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    adopted_id = P.adopt_new_version(PROFILE, vid, "human", db_path=tmp_db)

    with pytest.raises(P.AdoptedVersionImmutableError):
        P.try_update_row(PROFILE, adopted_id, db_path=tmp_db, patch={"hacked": True})

    errors = _events(tmp_db, "error")
    assert errors, "不可变违规必须在事件表留痕"
    payload = json.loads(errors[-1]["payload"])
    assert payload["error_code"] == "ADOPTED_IMMUTABLE"
    assert payload["profile_id"] == PROFILE
    assert payload["version_id"] == adopted_id


def test_合规更新不产生ADOPTED_IMMUTABLE事件(tmp_db):
    vid = P.write_version(PROFILE, {"plan": "v1"}, "planner", db_path=tmp_db)
    P.adopt_new_version(PROFILE, vid, "human", db_path=tmp_db)

    assert _events(tmp_db, "error") == []


# ------------------------------------------------------------------ read_profile

def test_read_profile_last尚无adopted时退回最新草案(tmp_db):
    for i in range(1, 4):
        P.write_version(PROFILE, {"n": i}, "planner", db_path=tmp_db)

    row = P.read_profile(PROFILE, "last", db_path=tmp_db)
    assert (row["version_id"], row["status"], row["patch"]) == (3, "draft", {"n": 3})


def test_read_profile_last优先返回最新adopted而非更新的草案(tmp_db):
    vid = P.write_version(PROFILE, {"n": 1}, "planner", db_path=tmp_db)
    adopted_id = P.adopt_new_version(PROFILE, vid, "human", db_path=tmp_db)
    newer_draft = P.write_version(PROFILE, {"n": 2}, "planner", db_path=tmp_db)
    assert newer_draft > adopted_id

    row = P.read_profile(PROFILE, "last", db_path=tmp_db)
    assert row["version_id"] == adopted_id
    assert row["status"] == "adopted"
    assert row["patch"] == {"n": 1}


def test_read_profile_可按下标读历史草案(tmp_db):
    for i in range(1, 4):
        P.write_version(PROFILE, {"n": i}, "planner", db_path=tmp_db)

    row = P.read_profile(PROFILE, 1, db_path=tmp_db)
    assert row["status"] == "draft"
    assert row["patch"] == {"n": 1}
    assert row["created_at"].endswith("Z")


def test_read_profile_paths只返回指定顶层键(tmp_db):
    vid = P.write_version(PROFILE, {"a": 1, "b": 2, "c": {"d": 3}}, "planner",
                          db_path=tmp_db)

    row = P.read_profile(PROFILE, vid, paths=["a", "c"], db_path=tmp_db)
    assert row["patch"] == {"a": 1, "c": {"d": 3}}


def test_read_profile_不存在的版本抛KeyError(tmp_db):
    with pytest.raises(KeyError):
        P.read_profile(PROFILE, 42, db_path=tmp_db)


def test_read_profile_不存在的档案抛KeyError(tmp_db):
    with pytest.raises(KeyError):
        P.read_profile("never-written", "last", db_path=tmp_db)


# ------------------------------------------------------------------ JSON 往返

def test_patch复杂结构JSON往返一致(tmp_db):
    patch = {
        "题号": "A",
        "指标": [{"名称": "满意度", "权重": 0.35}, {"名称": "投诉量", "权重": -0.2}],
        "阈值": {"下限": 0, "上限": 100.5},
        "启用": True,
        "备注": None,
        "空对象": {},
        "空列表": [],
        "含引号\"与\\反斜杠": "换行\n制表\t",
    }
    vid = P.write_version(PROFILE, patch, "planner", db_path=tmp_db)

    assert P.read_profile(PROFILE, vid, db_path=tmp_db)["patch"] == patch


def test_upstream_refs复杂结构JSON往返一致(tmp_db):
    refs = ["artifact:1", "event:2", {"nested": ["x", 3]}]
    vid = P.write_version(PROFILE, {"n": 1}, "planner",
                          upstream_refs=refs, db_path=tmp_db)

    assert P.read_profile(PROFILE, vid, db_path=tmp_db)["upstream_refs"] == refs


def test_patch以UTF8存储不进ensure_ascii转义(tmp_db):
    P.write_version(PROFILE, {"题号": "A"}, "planner", db_path=tmp_db)

    raw = _rows(tmp_db, "SELECT patch FROM profile_doc WHERE profile_id=?", (PROFILE,))[0]
    assert "题号" in raw["patch"], "中文应原样落库（ensure_ascii=False）"
    assert "\\u" not in raw["patch"]


def test_并发写入不跳号且版本号互不相同(tmp_db):
    """BEGIN IMMEDIATE + MAX(version_id)+1 必须扛住多线程并发写。"""
    n = 8
    results: list[int] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            vid = P.write_version(PROFILE, {"n": i}, "planner", db_path=tmp_db)
            with lock:
                results.append(vid)
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发写不应失败：{errors}"
    assert sorted(results) == list(range(1, n + 1)), f"版本号应连续无跳号：{results}"
    assert len(set(results)) == n
    assert _versions(tmp_db, status="draft") == [n - 4, n - 3, n - 2, n - 1, n]


# --------------------------------------------------- INSERT OR REPLACE 绕过（待修）
# 现状：schema.sql 只有 BEFORE UPDATE / BEFORE DELETE 两个触发器；SQLite 的
# `INSERT OR REPLACE` 走隐式 DELETE，而隐式 DELETE 仅在 `PRAGMA recursive_triggers=ON`
# （默认 OFF）时触发 BEFORE DELETE → 可原地覆盖 adopted 行。
# 实测（schema.sql md5 c50e61c7…）：REPLACE 静默成功，patch 被改成 {"hacked":1}。
# 期望（队长已派给实现方：加 BEFORE INSERT 守卫触发器）：REPLACE 被 ABORT。
REPLACE_SQL = (
    "INSERT OR REPLACE INTO profile_doc"
    " (profile_id, version_id, status, patch, actor, upstream_refs, supersedes, created_at)"
    " VALUES (?,?,?,?,?,?,?,?)")


def _adopt_one(tmp_db, patch: dict = None) -> int:
    vid = P.write_version(PROFILE, patch or {"plan": "v1"}, "planner", db_path=tmp_db)
    return P.adopt_new_version(PROFILE, vid, "human:leader", db_path=tmp_db)


def _try_replace_adopted(db_path, adopted_id: int) -> None:
    """在原生连接上尝试用 REPLACE 覆盖 adopted 行（成功=绕过）。"""
    conn = connect(db_path)
    try:
        conn.execute(REPLACE_SQL, (PROFILE, adopted_id, "adopted", '{"hacked":1}',
                                   "attacker", "[]", None, "2026-01-01T00:00:00.000Z"))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    finally:
        conn.close()


def test_INSERT_OR_REPLACE_不能覆盖adopted行(tmp_db):
    """回归 ISSUE-FR9-01：REPLACE 靠隐式 DELETE 绕过 BEFORE DELETE 触发器（默认不递归），
    已由 BEFORE INSERT 守卫触发器兜底 —— 直接 ABORT。"""
    adopted_id = _adopt_one(tmp_db)

    with pytest.raises(sqlite3.IntegrityError) as exc:
        _try_replace_adopted(tmp_db, adopted_id)

    assert "adopted immutable" in str(exc.value)


def test_INSERT_OR_REPLACE_失败后adopted行数据未被改动(tmp_db):
    """不只断言抛异常：REPLACE 失败后 adopted 行必须字节级不变（SELECT 回读比对）。"""
    adopted_id = _adopt_one(tmp_db, {"plan": "v1", "关键字段": "原始值"})
    before = _rows(tmp_db, "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                   (PROFILE, adopted_id))[0]

    try:
        _try_replace_adopted(tmp_db, adopted_id)
    except sqlite3.IntegrityError:
        pass

    after = _rows(tmp_db, "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                  (PROFILE, adopted_id))[0]
    assert after == before, "adopted 行必须字节级不变（不能只断言抛异常）"


@pytest.mark.xfail(reason="BEFORE INSERT 守卫是 SQL 触发器，RAISE(ABORT) 直接回滚语句，"
                          "应用层无机会写 events 表 → 违规无审计留痕；"
                          "需实现方在写路径捕获 IntegrityError 后补记事件（见 ISSUES-FR9-03）",
                   strict=False)
def test_INSERT_OR_REPLACE_违规留下事件留痕(tmp_db):
    adopted_id = _adopt_one(tmp_db)

    try:
        _try_replace_adopted(tmp_db, adopted_id)
    except sqlite3.IntegrityError:
        pass

    codes = [json.loads(e["payload"]).get("error_code") for e in _events(tmp_db, "error")]
    assert "ADOPTED_IMMUTABLE" in codes


def test_连接仍保持WAL与外键约束(tmp_db):
    """WAL 移出 connect 后不得丢掉库级设置与连接级约束（替代易 flaky 的计时断言）。"""
    conn = connect(tmp_db)
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    finally:
        conn.close()

    assert str(mode).lower() == "wal"
    assert fk == 1
    assert busy == 5000


def test_开启recursive_triggers后_REPLACE被现有触发器拦下(tmp_db):
    """现有触发器在 recursive_triggers=ON 下即可拦 REPLACE —— 佐证绕过机制。"""
    adopted_id = _adopt_one(tmp_db)
    conn = connect(tmp_db)
    try:
        conn.execute("PRAGMA recursive_triggers=ON;")
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute(REPLACE_SQL, (PROFILE, adopted_id, "adopted", '{"hacked":1}',
                                       "attacker", "[]", None, "2026-01-01T00:00:00.000Z"))
        assert "adopted immutable" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()

    assert P.read_profile(PROFILE, adopted_id, db_path=tmp_db)["patch"] == {"plan": "v1"}
