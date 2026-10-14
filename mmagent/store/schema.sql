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
