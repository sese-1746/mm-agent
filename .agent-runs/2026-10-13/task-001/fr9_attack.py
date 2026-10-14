# -*- coding: utf-8 -*-
"""FR-9 档案版本链对抗性独立验证（只读实现、不改 mmagent/ 与 tests/）。

在隔离临时库上黑盒攻击实现方声称的不可变性：
  1) 原生 sqlite3 绕过应用层对 adopted 行 UPDATE/DELETE → 触发器是否 ABORT，
     错误消息是否含 'adopted immutable'，应用层是否转成 AdoptedVersionImmutableError，
     且 events 表确有 error_code='ADOPTED_IMMUTABLE' 记录。
  2) 连续 write_version ×8 → 库里直接断言 draft 恰好 5 个、version_id 严格递增无跳号。
  3) adopt_new_version 前后整行快照对比 → 旧 adopted 行零字段被 UPDATE，新行是 INSERT。
  4) 两进程并发各写 5 次 → version_id 无重复无跳号。
  5) patch / upstream_refs 的 JSON 往返（含中文与嵌套）。

所有库文件写在脚本同目录（fr9_tmp*.db），不触碰 data/mm.db。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(r"H:\mm-agent")
sys.path.insert(0, str(REPO))

from mmagent.store.db import init_db, connect                      # noqa: E402
from mmagent.store.profile import (                                # noqa: E402
    write_version, adopt_new_version, read_profile, try_update_row,
    AdoptedVersionImmutableError,
)

HERE = Path(__file__).parent
DB = str(HERE / "fr9_tmp.db")

RESULTS: list[tuple[str, str, str]] = []   # (编号, 判定, 证据)


def rec(no: str, verdict: str, ev: str) -> None:
    RESULTS.append((no, verdict, ev))
    print(f"[{verdict:<6}] {no}: {ev}")


def fresh_db(path: str) -> None:
    for suf in ("", "-wal", "-shm", "-journal"):
        p = path + suf
        if os.path.exists(p):
            os.remove(p)
    init_db(path)


def raw_conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def row_snapshot(c: sqlite3.Connection, pid: str, vid: int) -> dict | None:
    r = c.execute("SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                  (pid, vid)).fetchone()
    return dict(r) if r else None


# ====================================================================== 1) 原生 DML 攻击
def attack1() -> None:
    print("\n" + "=" * 92)
    print("攻击 1：原生 sqlite3 绕过应用层，对 adopted 行 UPDATE / DELETE")
    print("=" * 92)
    fresh_db(DB)
    vid = write_version("P1", {"k": "v1"}, "tester", db_path=DB)
    avid = adopt_new_version("P1", vid, "tester", db_path=DB)
    c = raw_conn(DB)
    before = row_snapshot(c, "P1", avid)

    # (a) 原生 UPDATE
    try:
        c.execute("UPDATE profile_doc SET patch='{\"hacked\":1}' WHERE profile_id='P1' AND version_id=?",
                  (avid,))
        c.commit()
        rec("1a", "打穿", "原生 UPDATE 成功执行，被 adopted 行被改（触发器失效）")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("1a", "证成", f"原生 UPDATE 被 ABORT：{e}（含 'adopted immutable'={'adopted immutable' in str(e)}）")

    # (b) 原生 DELETE（单行）
    try:
        c.execute("DELETE FROM profile_doc WHERE profile_id='P1' AND version_id=?", (avid,))
        c.commit()
        rec("1b", "打穿", "原生 DELETE 单行成功，adopted 行被删")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("1b", "证成", f"原生 DELETE 被 ABORT：{e}（含 'adopted immutable'={'adopted immutable' in str(e)}）")

    # (c) 原生 DELETE 全表（无 WHERE，逐行触发）
    try:
        c.execute("DELETE FROM profile_doc")
        c.commit()
        rec("1c", "打穿", "DELETE FROM profile_doc 全表删除成功")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("1c", "证成", f"全表 DELETE 被 ABORT：{e}")

    # (d) 尝试把 adopted 行 status 改成 'superseded'（换个 UPDATE 姿势）
    try:
        c.execute("UPDATE profile_doc SET status='superseded' WHERE profile_id='P1' AND version_id=?",
                  (avid,))
        c.commit()
        rec("1d", "打穿", "改 status 成功，adopted 行被降级")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("1d", "证成", f"改 status 被 ABORT：{e}")

    after = row_snapshot(c, "P1", avid)
    rec("1e", "证成" if before == after else "打穿",
        f"整行快照 before == after：{before == after}（行仍在={after is not None}）")
    c.rollback()
    c.close()   # 关键：先释放锁，避免污染后续应用层调用

    # (e) 应用层转换 + 事件落库（用干净连接）
    try:
        try_update_row("P1", avid, db_path=DB, patch={"hack": 1})
        rec("1f", "打穿", "try_update_row 未抛异常（应用层未拦截）")
    except AdoptedVersionImmutableError as e:
        rec("1f", "证成", f"应用层抛 AdoptedVersionImmutableError：{e}")
    except Exception as e:  # noqa: BLE001
        rec("1f", "存疑", f"抛了其它异常类型 {type(e).__name__}: {e}")

    c = raw_conn(DB)
    evs = c.execute("SELECT id, event_type, payload FROM events WHERE event_type='error'"
                    " ORDER BY id DESC LIMIT 10").fetchall()
    hit = [dict(r) for r in evs if "ADOPTED_IMMUTABLE" in (r["payload"] or "")]
    rec("1g", "证成" if hit else "打穿",
        f"events 表含 error_code=ADOPTED_IMMUTABLE 记录：{bool(hit)}；命中={hit[:2]}")
    c.close()

    # (f) 残余风险：DROP TRIGGER 后不可变性被绕开（记录为已知限制）
    c2 = raw_conn(DB)
    try:
        c2.execute("DROP TRIGGER trg_profile_adopted_immutable")
        c2.execute("UPDATE profile_doc SET patch='{\"hacked\":2}' WHERE profile_id='P1' AND version_id=?",
                   (avid,))
        c2.commit()
        rec("1h", "限制", "DROP TRIGGER 后 UPDATE 成功 → 不可变性是 DML 级触发器，非防 DDL 篡改（已知限制）")
    except sqlite3.Error as e:
        rec("1h", "证成", f"即便 DROP TRIGGER 仍失败：{e}")
    c2.close()


# ====================================================================== 2) 草案裁剪 / 连续性
def attack2() -> None:
    print("\n" + "=" * 92)
    print("攻击 2：连续 write_version ×8 → draft 数 = 5？version_id 严格递增无跳号？")
    print("=" * 92)
    ids = [write_version("P2", {"i": i}, "tester", db_path=DB) for i in range(8)]
    c = raw_conn(DB)
    drafts = [r["version_id"] for r in c.execute(
        "SELECT version_id FROM profile_doc WHERE profile_id='P2' AND status='draft'"
        " ORDER BY version_id").fetchall()]
    c.close()
    rec("2a", "证成" if len(drafts) == 5 else "失败",
        f"8 次写入返回 id={ids}；库内 draft 数={len(drafts)}，draft ids={drafts}")
    contiguous = ids == list(range(1, 9))
    rec("2b", "证成" if contiguous else "失败",
        f"写入返回序列严格递增无跳号：{contiguous}（{ids}）")
    surv_contig = drafts == list(range(drafts[0], drafts[0] + 5)) if drafts else False
    rec("2c", "证成" if surv_contig else "失败",
        f"存活 draft 集内部连续：{surv_contig}（{drafts}）")
    if drafts and drafts[0] != 1:
        rec("2d", "备注", f"裁剪导致低号 draft 被物理删除（{drafts[0]-1} 个），"
                          f"故 profile 全历史在表内不连续——这是设计（保留最近 5 版），非可断言缺陷")


# ====================================================================== 3) adopt 的整行不可变
def attack3() -> None:
    print("\n" + "=" * 92)
    print("攻击 3：adopt_new_version 后，旧 adopted 行是否零字段被 UPDATE")
    print("=" * 92)
    v1 = write_version("P3", {"x": 1}, "tester", db_path=DB)          # v1 draft
    a1 = adopt_new_version("P3", v1, "tester", db_path=DB)            # v2 adopted, supersedes=1
    c = raw_conn(DB)
    snap_a1 = row_snapshot(c, "P3", a1)
    v3 = write_version("P3", {"x": 2}, "tester", db_path=DB)          # v3 draft
    a2 = adopt_new_version("P3", v3, "tester", db_path=DB)            # v4 adopted, supersedes=3
    c.close()
    c = raw_conn(DB)
    snap_a1_after = row_snapshot(c, "P3", a1)
    rec("3a", "证成" if snap_a1 == snap_a1_after else "打穿",
        f"旧 adopted v{a1} 整行未被改：{snap_a1 == snap_a1_after}")
    rec("3b", "证成" if snap_a1_after and snap_a1_after["status"] == "adopted" else "备注",
        f"旧 adopted v{a1} status 仍为 '{snap_a1_after and snap_a1_after['status']}'"
        f"（设计：靠 version_id 自然退位，未置 'superseded'）")
    new_row = row_snapshot(c, "P3", a2)
    rec("3c", "证成" if new_row and new_row["supersedes"] == v3 else "失败",
        f"新 adopted v{a2} 为 INSERT 行，supersedes={new_row and new_row['supersedes']}（期望 {v3}）")
    allrows = [dict(r) for r in c.execute(
        "SELECT version_id,status,supersedes FROM profile_doc WHERE profile_id='P3'"
        " ORDER BY version_id").fetchall()]
    rec("3d", "备注", f"P3 全版本链：{allrows} → 出现多条 status='adopted'（v{a1} 与 v{a2}）")
    c.close()


# ====================================================================== 4) 并发
def _worker(args) -> list[int]:
    db_path, pid, n = args
    sys.path.insert(0, str(REPO))
    from mmagent.store.profile import write_version as wv
    out = []
    for i in range(n):
        out.append(wv(pid, {"i": i}, "concurrent", db_path=db_path))
    return out


def attack6() -> None:
    print("\n" + "=" * 92)
    print("攻击 6：用 INSERT OR REPLACE / REPLACE 触发『隐式 DELETE』绕过 adopted 不可变")
    print("=" * 92)
    fresh_db(DB)
    v = write_version("P6", {"k": "orig"}, "tester", db_path=DB)
    a = adopt_new_version("P6", v, "tester", db_path=DB)
    c = raw_conn(DB)
    before = row_snapshot(c, "P6", a)
    # 默认 recursive_triggers（SQLite 默认 OFF）下，REPLACE 的隐式 DELETE 是否触发 DELETE 触发器？
    rt = c.execute("PRAGMA recursive_triggers").fetchone()[0]
    try:
        c.execute(
            "INSERT OR REPLACE INTO profile_doc"
            " (profile_id,version_id,status,patch,actor,upstream_refs,supersedes,created_at)"
            " VALUES ('P6',?,'adopted','{\"hacked\":3}','attacker','[]',NULL,'2026-01-01T00:00:00.000Z')",
            (a,))
        c.commit()
        c.rollback()
        after = row_snapshot(c, "P6", a)
        if after != before:
            rec("6a", "打穿",
                f"INSERT OR REPLACE 覆盖成功！patch 由 {before['patch']} → {after['patch']}"
                f"（recursive_triggers={rt}，隐式 DELETE 未触发 adopted 删除触发器）")
        else:
            rec("6a", "证成", f"INSERT OR REPLACE 未改行（recursive_triggers={rt}）")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("6a", "证成", f"INSERT OR REPLACE 被 ABORT：{e}")
    # 打开 recursive_triggers 再试一次，对比
    try:
        c.execute("PRAGMA recursive_triggers=ON")
        c.execute(
            "INSERT OR REPLACE INTO profile_doc"
            " (profile_id,version_id,status,patch,actor,upstream_refs,supersedes,created_at)"
            " VALUES ('P6',?,'adopted','{\"hacked\":4}','attacker','[]',NULL,'2026-01-01T00:00:00.000Z')",
            (a,))
        c.commit()
        c.rollback()
        after2 = row_snapshot(c, "P6", a)
        rec("6b", "打穿" if after2 != before else "证成",
            f"开启 recursive_triggers 后 INSERT OR REPLACE：{'仍被覆盖' if after2 != before else '被 ABORT/未改'}")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("6b", "证成", f"开启 recursive_triggers 后 INSERT OR REPLACE 被 ABORT：{e}")
    # (c) 直接 INSERT 一条新的 adopted 行（极高 version_id）是否被拦？
    try:
        c.execute(
            "INSERT INTO profile_doc"
            " (profile_id,version_id,status,patch,actor,upstream_refs,supersedes,created_at)"
            " VALUES ('P6',999,'adopted','{\"evil\":1}','attacker','[]',NULL,'2026-01-01T00:00:00.000Z')")
        c.commit()
        c.rollback()
        rec("6c", "限制", "原生 INSERT 一条 status='adopted' 的新行未被拦（append-only 允许 INSERT；"
                          "仅既有 adopted 行不可改）→ read_profile('last') 会返回 v999")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("6c", "证成", f"原生 INSERT adopted 行被 ABORT：{e}")

    # (d) 验证候选最小修复：加一条 BEFORE INSERT 守卫（不依赖 recursive_triggers）
    now = row_snapshot(c, "P6", a)
    c.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_profile_adopted_guard_insert\n"
        "BEFORE INSERT ON profile_doc\n"
        "WHEN EXISTS (SELECT 1 FROM profile_doc\n"
        "             WHERE profile_id=NEW.profile_id AND version_id=NEW.version_id\n"
        "               AND status='adopted')\n"
        "BEGIN SELECT RAISE(ABORT, 'adopted immutable'); END;")
    c.execute("PRAGMA recursive_triggers=OFF")   # 保持默认 OFF，证明修复不依赖该 pragma
    try:
        c.execute(
            "INSERT OR REPLACE INTO profile_doc"
            " (profile_id,version_id,status,patch,actor,upstream_refs,supersedes,created_at)"
            " VALUES ('P6',?,'adopted','{\"hacked\":5}','attacker','[]',NULL,'2026-01-01T00:00:00.000Z')",
            (a,))
        c.commit()
        c.rollback()
        after3 = row_snapshot(c, "P6", a)
        rec("6d", "证成" if after3 == now else "打穿",
            f"候选修复（BEFORE INSERT 守卫 + recursive_triggers=OFF）后 INSERT OR REPLACE："
            f"{'仍被覆盖' if after3 != now else '被拦/未改 → 修复有效'}")
    except sqlite3.IntegrityError as e:
        c.rollback()
        rec("6d", "证成", f"候选修复后 INSERT OR REPLACE 被 ABORT：{e} → 修复有效，且不依赖 recursive_triggers")
    c.close()


def attack4() -> None:
    print("\n" + "=" * 92)
    print("攻击 4：N 进程并发各写 5 次 → version_id 无重复无跳号？")
    print("=" * 92)
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    NPROC = 6
    with ctx.Pool(NPROC) as pool:
        res = pool.map(_worker, [(DB, "P4", 5)] * NPROC)
    ids = sorted(x for sub in res for x in sub)
    c = raw_conn(DB)
    rows = [r["version_id"] for r in c.execute(
        "SELECT version_id FROM profile_doc WHERE profile_id='P4'").fetchall()]
    c.close()
    uniq = len(set(ids)) == len(ids)
    rec("4a", "证成" if uniq else "打穿",
        f"{NPROC} 进程共返回 {len(ids)} 个 id；有重复={not uniq}")
    contiguous = ids == list(range(1, NPROC * 5 + 1))
    rec("4b", "证成" if contiguous else "失败",
        f"合并后 1..{NPROC*5} 连续无跳号：{contiguous}（首/末={ids[:3]}…{ids[-3:]}）")
    rec("4c", "证成" if len(set(rows)) == len(rows) else "打穿",
        f"库内实际 version_id 无重复：{len(set(rows)) == len(rows)}；库内={sorted(rows)}")
    rec("4d", "备注", f"库内 draft 数={len(rows)}（裁剪后应≤5）")


# ====================================================================== 5) JSON 往返
def attack5() -> None:
    print("\n" + "=" * 92)
    print("攻击 5：patch / upstream_refs 的 JSON 往返一致性（中文 + 嵌套）")
    print("=" * 92)
    patch = {
        "赛题": "2024 数学建模 A题（校赛）",
        "嵌套": {"层级": [1, 2, {"深度": "中文值"}], "空": None, "真假": [True, False]},
        "列表": ["甲", "乙", {"内": {"x": 1.5}}],
        "引号": '含"双引号"和\\反斜杠',
        "emoji": "测试🚀",
    }
    refs = ["artifact:附件1", {"id": "事件123", "note": "上游引用"}]
    vid = write_version("P5", patch, "测试员", upstream_refs=refs, db_path=DB)
    r = read_profile("P5", vid, db_path=DB)
    rec("5a", "证成" if r["patch"] == patch else "打穿",
        f"patch 往返逐键相等：{r['patch'] == patch}")
    rec("5b", "证成" if r["upstream_refs"] == refs else "打穿",
        f"upstream_refs 往返相等：{r['upstream_refs'] == refs}")
    rec("5c", "备注", f"读回 patch={json.dumps(r['patch'], ensure_ascii=False)[:120]}")
    # paths 过滤
    r2 = read_profile("P5", vid, paths=["嵌套"], db_path=DB)
    rec("5d", "证成" if set(r2["patch"].keys()) == {"嵌套"} else "失败",
        f"paths 过滤只返回指定顶层键：{list(r2['patch'].keys())}")


def main() -> None:
    print(f"隔离库：{DB}（不触碰 data/mm.db）")
    attack1()
    attack2()
    attack3()
    attack4()
    attack5()
    attack6()
    print("\n" + "=" * 92)
    print("汇总")
    print("=" * 92)
    for no, v, ev in RESULTS:
        print(f"  {no:<4} {v:<6} {ev[:150]}")
    print(f"\n打穿/失败项：{[no for no, v, _ in RESULTS if v in ('打穿', '失败')]}")
    print(f"证成项：{[no for no, v, _ in RESULTS if v == '证成']}")


if __name__ == "__main__":
    main()
