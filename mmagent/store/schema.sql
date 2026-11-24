-- mmagent 数据库结构（FR-9 档案版本链 + 事件/产物/指标表）
-- 所有时间戳为 TEXT ISO8601 UTC；连接级 PRAGMA 见 store/db.py。

-- 档案版本链：append-only，adopted 行不可变（触发器保证）
CREATE TABLE IF NOT EXISTS profile_doc (
    profile_id    TEXT NOT NULL,
    version_id    INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('draft','adopted','superseded')),
    patch         TEXT NOT NULL DEFAULT '{}',   -- JSON：本版本档案内容补丁
    actor         TEXT NOT NULL,                -- 写入者（角色名或 human）
    upstream_refs TEXT NOT NULL DEFAULT '[]',   -- JSON：上游引用（artifact/事件 id 列表）
    supersedes    INTEGER,                      -- 被接替的 adopted 版本号
    created_at    TEXT NOT NULL,                -- ISO8601 UTC
    PRIMARY KEY (profile_id, version_id)
);

-- adopted 行禁止 UPDATE（旧版本靠 version_id 比较自然退位，无需改状态）
CREATE TRIGGER IF NOT EXISTS trg_profile_adopted_immutable
BEFORE UPDATE ON profile_doc
WHEN OLD.status = 'adopted'
BEGIN
    SELECT RAISE(ABORT, 'adopted immutable');
END;

-- adopted 行禁止 DELETE
CREATE TRIGGER IF NOT EXISTS trg_profile_adopted_nodelete
BEFORE DELETE ON profile_doc
WHEN OLD.status = 'adopted'
BEGIN
    SELECT RAISE(ABORT, 'adopted immutable');
END;

-- adopted 行禁止被 INSERT 覆盖：SQLite 的 INSERT OR REPLACE 依靠「隐式 DELETE」，
-- 而该隐式 DELETE 只有在 recursive_triggers=ON 时才触发上面的 BEFORE DELETE 触发器。
-- 这里用 BEFORE INSERT 守卫兜底：主键 (profile_id, version_id) 已存在且为 adopted 时
-- 直接 ABORT，防止 REPLACE 绕过不可变约束。正常写入走 MAX(version_id)+1，永不撞主键。
CREATE TRIGGER IF NOT EXISTS trg_profile_adopted_guard_insert
BEFORE INSERT ON profile_doc
WHEN EXISTS (SELECT 1 FROM profile_doc
             WHERE profile_id = NEW.profile_id
               AND version_id = NEW.version_id
               AND status = 'adopted')
BEGIN
    SELECT RAISE(ABORT, 'adopted immutable');
END;

-- 事件流水
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL DEFAULT '{}',      -- JSON
    trace_id   TEXT NOT NULL DEFAULT ''
);

-- 产物登记（含 prompt 版本与模型标签，支撑 FR-13 溯源）
CREATE TABLE IF NOT EXISTS artifacts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    kind           TEXT NOT NULL,               -- figure/table/script/doc/llm_output ...
    path           TEXT NOT NULL DEFAULT '',
    profile_id     TEXT,
    version_id     INTEGER,
    producer       TEXT NOT NULL DEFAULT '',    -- 产出角色
    prompt_version TEXT,                        -- <role>@x.y.z
    model_tag      TEXT,                        -- 模型标签，如 ollama/qwen3.5:latest
    input_hash     TEXT                         -- 输入摘要哈希
);

-- 指标观测（LLM 调用时延、token 等）
CREATE TABLE IF NOT EXISTS metric_obs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    metric         TEXT NOT NULL,
    value          REAL,
    unit           TEXT NOT NULL DEFAULT '',
    run_id         TEXT NOT NULL DEFAULT '',
    prompt_version TEXT,
    model_tag      TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_artifacts_profile ON artifacts (profile_id, version_id);
CREATE INDEX IF NOT EXISTS idx_metric_run ON metric_obs (run_id);

-- NFR-7 脱敏命中台账（只存原文哈希，不存原文）
CREATE TABLE IF NOT EXISTS desensitize_hit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    rule         TEXT NOT NULL,            -- phone|student_id|id_card|name_column|advisor
    table_id     TEXT NOT NULL DEFAULT '',
    field_raw    TEXT NOT NULL DEFAULT '', -- 列名或单元格坐标
    masked_value TEXT NOT NULL DEFAULT '[已脱敏]',
    action       TEXT NOT NULL DEFAULT 'replaced',  -- replaced|mask_column
    trace_id     TEXT NOT NULL DEFAULT '',
    raw_sha256   TEXT NOT NULL DEFAULT ''  -- 仅哈希，不落原文
);

CREATE INDEX IF NOT EXISTS idx_desens_rule ON desensitize_hit (rule, ts);

-- 出网审计台账（NFR-2 / FR-13 联动）：每次**云端** LLM 调用写一行。
-- provider=dashscope 时由 agents/base.py::RoleRunner 记录；本地 Ollama 不出网，不写。
-- 列序：id / ts / provider / endpoint / model / role / prompt_hash / ok / error_code
--
-- ⚠️ 本文件是**全库 DDL 的唯一真源**（表 / 索引 / 触发器都在这里，且都是 IF NOT EXISTS）。
--    `store/db.py::ensure_schema()` 在 `connect()` 期用 `sqlite3.complete_statement()`
--    逐句切分本文件，按 (type, name) 比对 `sqlite_master`，**缺失的对象就地补建**，
--    因此既有 `.db` 文件也能拿到新对象。**不需要**在代码里维护第二份 DDL 文本，
--    也不需要「两处保持同文」——只改本文件即可自动生效。
CREATE TABLE IF NOT EXISTS net_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    provider    TEXT NOT NULL,
    endpoint    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 0,
    error_code  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_net_audit_ts ON net_audit (ts);

-- FR-9 字段级溯源（A4）：每个档案字段的来源角色 / 生成时刻 / 版本 / 上游引用
CREATE TABLE IF NOT EXISTS profile_field_trace (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    profile_id    TEXT NOT NULL,
    version_id    INTEGER NOT NULL,
    field_path    TEXT NOT NULL,                  -- 形如 "paper.title" / "task.deadline"
    producer      TEXT NOT NULL DEFAULT '',       -- 产出角色（archivist/planner/...）
    produced_at   TEXT NOT NULL DEFAULT '',       -- 该字段的生成时刻（ISO8601 UTC）
    upstream_refs TEXT NOT NULL DEFAULT '[]',     -- JSON 数组：上游字段/来源 id
    trace_id      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_field_trace_doc   ON profile_field_trace (profile_id, version_id);
CREATE INDEX IF NOT EXISTS idx_field_trace_field ON profile_field_trace (field_path);

-- NFR-5 备份台账（A5）
CREATE TABLE IF NOT EXISTS backup_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    db_path     TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    bytes       INTEGER,
    ok          INTEGER NOT NULL DEFAULT 1,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_backup_log_ts ON backup_log (ts);

-- NFR-2 / 规划书第十四章「审核留痕：event_log 不可 UPDATE/DELETE」（A5）
CREATE TRIGGER IF NOT EXISTS trg_events_no_update
BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_delete
BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events immutable'); END;

-- 裸连接（recursive_triggers 默认 OFF）下 INSERT OR REPLACE 的隐式 DELETE 不触发
-- trg_events_no_delete，故补 BEFORE INSERT 兜底 —— 与 profile_doc 的
-- trg_profile_adopted_guard_insert 同构（见 schema.sql 该触发器上方注释）。
CREATE TRIGGER IF NOT EXISTS trg_events_guard_insert
BEFORE INSERT ON events
WHEN NEW.id IS NOT NULL AND EXISTS (SELECT 1 FROM events WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'events immutable'); END;

-- 历年真题与官方论文档案（FR-14：题面解析/论文装配的素材底盘）。
--
-- 为什么单独建表而不复用 `artifacts`：`artifacts` 是**单次运行的产物登记**
-- （figure/table/script/doc，配 profile_id + version_id + producer + prompt_version），
-- 本表是**跨年度的外部素材目录**（无 profile/version 概念，但有 year/problem_code/tag/
-- sha256/page_count 这些检索键）。塞进 `artifacts` 只能靠 JSON 兜住这些列，
-- 而 `list_artifacts` 的 where 全部走真实列 → 会退化成全表扫描 + 内存过滤。
--
-- 与审计表的区别：本表**可变**（同一 archive_id 重抓后 upsert 覆盖）。它记录的是
-- 「当前磁盘上这份素材是什么」，不是「历史上发生过什么」——后者归 `events`。
-- 因此这里**不加** immutable 触发器（加了反而使重抓无法落库，违背档案目录语义）。
--
-- `local_path` 一律存**相对仓库根**的 POSIX 路径；文件是否仍在由读取方现算
-- （`file_present`），不落库 —— 否则删了文件而库里仍记 True，就是假绿灯。
CREATE TABLE IF NOT EXISTS archive_item (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id   TEXT NOT NULL,                    -- 稳定业务键：exam-2025-A / paper-2025-A196
    kind         TEXT NOT NULL CHECK (kind IN ('exam_problem','paper')),
    year         INTEGER NOT NULL,
    problem_code TEXT NOT NULL DEFAULT '',         -- A/B/C/D/E；未知为空串
    tag          TEXT NOT NULL DEFAULT '',         -- 论文编号 A196；真题为空串
    title        TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',         -- 官方展示页
    asset_url    TEXT NOT NULL DEFAULT '',         -- 直接下载地址（真题 zip / 论文首图）
    local_path   TEXT NOT NULL DEFAULT '',         -- 相对仓库根，POSIX 分隔
    sha256       TEXT NOT NULL DEFAULT '',         -- 文件哈希；论文为「逐页 sha256 按序拼接」的哈希
    bytes        INTEGER,                          -- 单文件为文件大小；论文为该篇合计
    page_count   INTEGER,                          -- 论文扫描页数；真题为 NULL
    fetched_at   TEXT NOT NULL,                    -- 官方页面/素材的抓取时刻（ISO8601 UTC）
    ingested_at  TEXT NOT NULL,                    -- 本次入库时刻（ISO8601 UTC）
    extra        TEXT NOT NULL DEFAULT '{}',       -- JSON：附件清单 / 页图清单等
    UNIQUE (archive_id)
);

CREATE INDEX IF NOT EXISTS idx_archive_year_kind ON archive_item (year, kind);
CREATE INDEX IF NOT EXISTS idx_archive_kind      ON archive_item (kind);
