# FR-9 档案版本链 —— 对抗性独立验证报告

- 验证人：调查员 Vera（只读，未改动 `mmagent/` 与 `tests/`）
- 时间：2026-10-13（task #6 后续）
- 脚本：`fr9_attack.py`（可复跑，隔离临时库 `fr9_tmp.db`，**不触碰 `data/mm.db`**）；原始输出 `fr9_attack_dump.txt`（88 行）
- 方法：黑盒 + 白盒混合 —— 不看实现方自测代码，自己用**原生 `sqlite3`** 绕过应用层，直接对库施压

---

## 0. 结论速览

| 攻击点 | 判定 | 一句话 |
|---|---|---|
| 1a–1e 原生 `UPDATE`/`DELETE`/全表 `DELETE`/改 `status` | **证成** | 触发器全部 `RAISE(ABORT,'adopted immutable')`，整行快照前后相等 |
| 1f 应用层 `AdoptedVersionImmutableError` | **证成** | `try_update_row` 正确抛出领域异常 |
| 1g 事件表留痕 | **证成** | `events` 确有 `error_code='ADOPTED_IMMUTABLE'` 记录（id=3） |
| 2a–2c 8 次写入 / draft=5 / 无跳号 | **证成** | 返回 id 1..8，库内 draft 恰 5 个（4..8），连续 |
| 3a–3c adopt 后旧行零字段变更、新行 INSERT | **证成** | 旧 adopted 整行快照不变；新行 `supersedes` 正确 |
| 4a–4c 并发（6 进程 ×5 写） | **证成** | 30 个 id 无重复、1..30 连续；`BEGIN IMMEDIATE`+`MAX+1` 成立 |
| 5a–5d patch/refs JSON 往返（中文+嵌套） | **证成** | 逐键相等，`paths` 过滤正确 |
| **6a `INSERT OR REPLACE` 绕过不可变** | **打穿** | **可用原生 sqlite3 覆盖已 adopted 行**（见 §3） |
| 6b 开启 `recursive_triggers` | 证成 | 开启后被拦（说明根因就是默认 OFF） |
| 6d 候选修复（BEFORE INSERT 守卫） | 证成 | 实测有效，且不依赖 pragma |

**一句话**：实现方声称的领域语义（领域异常、事件留痕、草案裁剪、`MAX+1` 并发、INSERT-only adopt、JSON 往返）**全部成立**；但**「adopted 行不可变」这一条对原生 sqlite3 客户端并不成立** —— 存在 `INSERT OR REPLACE` 隐式 DELETE 绕过口。

---

## 1. 攻击面与隔离说明

- 攻击对象：`mmagent/store/schema.sql`（2 条触发器）+ `mmagent/store/profile.py` + `mmagent/store/db.py`。
- 隔离：每轮攻击在 `fr9_tmp.db`（脚本同目录）上 `init_db` 新建，**未读写仓库的 `data/mm.db`**，未改动任何 `.py`/`.sql`/`tests/`。
- 攻击 6d 中新增的守卫触发器只 `CREATE` 在隔离库内，用于**验证候选修复可行**，不是对仓库的改动。

---

## 2. 逐点证据

### 2.1 原生 DML 攻击（1a–1e、1h）

实现只有两条触发器：

```sql
CREATE TRIGGER trg_profile_adopted_immutable BEFORE UPDATE ON profile_doc
  WHEN OLD.status = 'adopted' BEGIN SELECT RAISE(ABORT,'adopted immutable'); END;
CREATE TRIGGER trg_profile_adopted_nodelete  BEFORE DELETE ON profile_doc
  WHEN OLD.status = 'adopted' BEGIN SELECT RAISE(ABORT,'adopted immutable'); END;
```

| 子项 | 攻击 | 结果 |
|---|---|---|
| 1a | 原生 `UPDATE profile_doc SET patch=...` | ABORT：`adopted immutable` ✓ |
| 1b | 原生 `DELETE ... WHERE version_id=?`（单行） | ABORT ✓ |
| 1c | 原生 `DELETE FROM profile_doc`（无 WHERE，逐行） | ABORT ✓ |
| 1d | 原生 `UPDATE ... SET status='superseded'`（换姿势） | ABORT ✓ |
| 1e | 整行快照 before == after | **True**，行仍在 ✓ |
| 1h | `DROP TRIGGER` 后再 UPDATE | **成功**（见 §5 已知限制） |

> 过程说明：首次跑时 1f/1g 出现 `database is locked` / 无事件记录，经排查是**我自己的测试连接在失败事务后未 rollback 持有写锁**污染了后续应用层调用（测试脚手架问题，非实现缺陷）。修正（每步 `rollback` 并释放连接）后 1f/1g 恢复为「证成」。

### 2.2 应用层转换 + 事件留痕（1f、1g）

```
1f: 应用层抛 AdoptedVersionImmutableError：profile P1 v2 已 adopted，禁止修改/删除
1g: events 命中 = {'id': 3, 'event_type': 'error',
     'payload': '{"error_code": "ADOPTED_IMMUTABLE", "profile_id": "P1", "version_id": 2}'}
```

- `_convert_integrity()` 正确把触发器 `IntegrityError` 转成领域异常，并**确实往 `events` 表写了一条 `error_code='ADOPTED_IMMUTABLE'`**（不是只看异常）。✓

### 2.3 草案裁剪与连续性（2a–2d）

```
2a: 8 次写入返回 id=[1,2,3,4,5,6,7,8]；库内 draft 数=5，draft ids=[4,5,6,7,8]
2b: 写入返回序列严格递增无跳号：True
2c: 存活 draft 集内部连续：True
2d: 裁剪物理删除低号 draft（3 个）→ profile 全历史在表内不连续
```

- 「draft 恰好 5 个」「写入序列 1..N 严格递增无跳号」**证成**。✓
- 2d 为设计使然（保留最近 5 版），**不是缺陷**；但下游若断言「表内存在连续 1..N」会失败——建议文档措辞明确为「写入序列连续」而非「表内历史连续」。

### 2.4 adopt 的整行不可变（3a–3d）

```
3a: 旧 adopted v2 整行未被改：True
3b: 旧 adopted v2 status 仍为 'adopted'
3c: 新 adopted v4 为 INSERT 行，supersedes=3
3d: P3 = [v1 draft, v2 adopted(sup=1), v3 draft, v4 adopted(sup=3)]
```

- `adopt_new_version` 只 INSERT、旧行零字段变更：**证成**。✓
- 观察：采用后**同时存在多条 `status='adopted'`**（v2、v4）。这与设计注释一致（旧版靠 `version_id` 自然退位），`read_profile('last')` 取 `ORDER BY version_id DESC LIMIT 1` → 返回最新 adopted，行为正确。但**注意**：`status` 里的 `'superseded'` 取值从未被写入，且无「唯一 adopted」约束——见 §5。

### 2.5 并发（4a–4d）

6 进程 × 各 5 次写同一 profile：

```
4a: 30 个 id 无重复
4b: 合并后 1..30 连续无跳号：True
4c: 库内 version_id 无重复：[26..30]
4d: 库内 draft 数=5
```

- `BEGIN IMMEDIATE` 串行化写者 + 事务内 `MAX(version_id)+1`：**证成**，无重号/跳号。✓

### 2.6 JSON 往返（5a–5d）

- `patch`（含中文键值、嵌套 dict/list、`None`/`True`/`False`、含引号与反斜杠、emoji）与 `upstream_refs`（list 内混字符串与 dict）写读**逐键相等**；`paths` 顶层过滤正确。✓

---

## 3. ★ 打穿项：`INSERT OR REPLACE` 绕过 adopted 不可变（6a）

### 3.1 攻击与结果

```python
# 隔离库内：先 write_version -> adopt_new_version 得到 adopted 行 v2
c.execute("INSERT OR REPLACE INTO profile_doc"
          " (profile_id,version_id,status,patch,actor,upstream_refs,supersedes,created_at)"
          " VALUES ('P6',?,'adopted','{\"hacked\":3}','attacker','[]',NULL,'2026-01-01T00:00:00.000Z')",
          (adopted_version_id,))
```

```
6a: INSERT OR REPLACE 覆盖成功！patch 由 {"k": "orig"} → {"hacked":3}
    （recursive_triggers=0，隐式 DELETE 未触发 adopted 删除触发器）
```

**已 adopted 行被原地覆盖，不可变性被打穿。** 全程原生 `sqlite3`，未触碰应用层。

### 3.2 根因

SQLite 的 `REPLACE` / `INSERT OR REPLACE` 在遇到主键冲突时，通过**隐式 DELETE 冲突行**再插入新行来实现。关键点：

> **REPLACE 的隐式 DELETE 只有在 `PRAGMA recursive_triggers=ON` 时才触发 `BEFORE DELETE` 触发器**；而 SQLite 默认 `recursive_triggers=OFF`。

因此：

1. `INSERT OR REPLACE` 命中 `(profile_id, version_id)` 主键冲突；
2. 隐式 DELETE 绕过 `trg_profile_adopted_nodelete`（因 `recursive_triggers` 默认 OFF，`BEFORE DELETE` 不触发）；
3. 老 adopted 行被删，新行（`status='adopted'`）被插入；
4. `BEFORE UPDATE` 触发器与本次攻击无关（没有 UPDATE 语句）；
5. 结果：`patch` 被替换为攻击者内容，`events` 无任何留痕。

对照组 **6b**：显式 `PRAGMA recursive_triggers=ON` 后，同样的 `INSERT OR REPLACE` **被 ABORT**（`adopted immutable`）→ 反证根因确为「隐式 DELETE 不触发触发器」。

### 3.3 严重度评估

- **可达性**：仅需对数据库文件的写权限（原生 sqlite3 客户端 / 任何能打开 `.db` 的进程）。应用层自身**未**使用 `INSERT OR REPLACE`（`profile.py` 全是普通 `INSERT`），所以这不是应用 bug，而是**「触发器保证不可变」这条安全声明对原生写者不成立**。
- **影响**：文档合规叙事（NFR 可追溯、adopted 冻结、四字段溯源）依赖「adopted 不可篡改」。若威胁模型包含「同机进程可直连 SQLite」，则该保证被证伪；若威胁模型仅限应用层，则影响可控。
- **建议定性**：从「已证成的不可变保证」降级为「**对应用层成立、对原生 DML 不完全成立**」，并按下节修复。

---

## 4. 最小修复建议（供 Max，我不改代码）

**候选修复（已在隔离库实测有效，见 6d）：新增一条不依赖 pragma 的 `BEFORE INSERT` 守卫触发器**

```sql
-- 阻止用 INSERT OR REPLACE / REPLACE 覆盖已 adopted 行
CREATE TRIGGER IF NOT EXISTS trg_profile_adopted_guard_insert
BEFORE INSERT ON profile_doc
WHEN EXISTS (SELECT 1 FROM profile_doc
             WHERE profile_id = NEW.profile_id
               AND version_id = NEW.version_id
               AND status = 'adopted')
BEGIN
    SELECT RAISE(ABORT, 'adopted immutable');
END;
```

实测结果（6d）：加此触发器后，**在 `recursive_triggers=OFF`（默认）下**，`INSERT OR REPLACE` 被 `ABORT: adopted immutable` 拦下 → 修复有效，且**不依赖连接级 pragma**（原生客户端也拦得住）。

- 无误伤：正常 `write_version`/`adopt_new_version` 的 `version_id = MAX+1`，永不与既有 adopted 行主键相撞，故该守卫只对「覆盖已 adopted 行」的写入触发。
- 备选项（**不充分，仅供叠加**）：在 `db.py::connect()` 加 `PRAGMA recursive_triggers=ON;`（6b 证明有效）——但只保护应用层连接，**原生客户端仍可用默认 OFF 绕过**，故不能单独作为修复，最多与上面的 INSERT 守卫叠加做纵深防御。

> 其余「已知限制」（§5）非必须修，但建议在文档中显式声明威胁模型边界。

---

## 5. 残余风险 / 已知限制（非缺陷，但需声明）

| 编号 | 限制 | 证据 | 说明 |
|---|---|---|---|
| 1h | `DROP TRIGGER` 后 UPDATE 成功 | 攻击 1h | 触发器是 DML 级约束；有 `DROP TRIGGER`/`PRAGMA writable_schema` 权限者仍可改。不可变性**不是密码学保护**。 |
| 6c | 原生 INSERT 一条**新**的 `status='adopted'` 行（如 v999）未被拦 | 攻击 6c | append-only 允许 INSERT；`read_profile('last')` 会返回 v999。若需防「伪造高版本 adopted」，应加「对同一 profile 至多一条 adopted / 只允许 version_id=MAX+1」的约束或守卫。 |
| 3d | 采用后存在多条 `status='adopted'`，`'superseded'` 从未被写入 | 攻击 3d | `read_profile('last')` 行为正确（取最新），但报表/审计若 `WHERE status='adopted'` 取多行需自行取 max。 |
| — | `write_version` 对 `IntegrityError` 不重试（直接 re-raise） | 代码 `profile.py:91-93` | 并发安全靠 `BEGIN IMMEDIATE` 串行化，而非重试；`database is locked` 只由 `OperationalError` 分支重试 3 次（退避 0.1/0.2/0.3s + busy_timeout 5s）。本次 6 进程压测未触发失败，但极端锁竞争下 `write_version` 会直接抛 `OperationalError`（无优雅降级）。 |

---

## 6. 复跑说明

```bash
PY="C:/Users/Lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe"
cd "H:/mm-agent/.agent-runs/2026-10-13/task-001"
"$PY" fr9_attack.py            # 输出见 fr9_attack_dump.txt；自动建/删隔离库 fr9_tmp.db
```

- 复跑会在同目录生成 `fr9_tmp.db`（+`-wal`/`-shm`），为一次性隔离库，可随时删除；**与仓库 `data/mm.db` 无任何关系**。
- 若要复现 6a：直接看 `fr9_attack_dump.txt` 中 `攻击 6` 段的 `6a`（打穿）与 `6d`（候选修复有效）。
