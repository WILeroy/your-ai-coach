"""数据库 schema 与连接助手"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'fitness.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    pattern TEXT NOT NULL,       -- squat/hinge/push/pull/core/accessory/stretch/cardio
    equipment TEXT,              -- barbell/dumbbell/machine/bodyweight/cable
    is_main INTEGER DEFAULT 0,  -- 1 = 三大项目标动作
    notes TEXT
);

-- 一个标准动作可以有多个用户/中文/英文别名。写入时别名会被解析到 canonical exercise，
-- 避免同一动作因“胸推/卧推/bench press”等命名差异被拆成多条历史。
CREATE TABLE IF NOT EXISTS exercise_aliases (
    id INTEGER PRIMARY KEY,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    alias TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'builtin', -- builtin/learned/user
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_exercise_aliases_exercise ON exercise_aliases(exercise_id);

CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    phase TEXT,                  -- adaptation/load/verification/deload
    goals_json TEXT,             -- [{"exercise":"杠铃卧推","from":35,"to":40,"sets":5,"reps":5}]
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    cycle_id INTEGER REFERENCES cycles(id),
    week_no INTEGER,
    day_no INTEGER,
    type TEXT NOT NULL,          -- legs/push/pull/interval/lsd/relax/rest
    sleep_h REAL,
    bodyweight REAL,
    rpe REAL,                    -- session RPE 1-10
    status TEXT DEFAULT 'planned', -- planned/done/partial/skipped/rest
    notes TEXT,                  -- legacy: 迁移前计划/完成备注混用
    planned_notes TEXT,          -- 课表设计/处方备注
    actual_notes TEXT,           -- 执行情况/完成备注
    UNIQUE(date, type)
);

CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    set_no INTEGER NOT NULL,
    planned_kg REAL,
    planned_reps INTEGER,
    actual_kg REAL,
    actual_reps INTEGER,
    rpe REAL,
    status TEXT DEFAULT 'planned', -- done/modified/skipped/extra
    notes TEXT
);

CREATE TABLE IF NOT EXISTS cardio (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,          -- interval/lsd/warmup/cooldown
    planned_json TEXT,           -- {"intervals":[{"m":400,"pace_sec":330}],"total_km":5}
    actual_json TEXT,            -- same structure with actual values
    distance_km REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    duration_s INTEGER,
    avg_pace_sec INTEGER
);

CREATE TABLE IF NOT EXISTS body_metrics (
    id INTEGER PRIMARY KEY,
    date TEXT UNIQUE NOT NULL,
    weight REAL,
    sleep_h REAL,
    resting_hr INTEGER,
    hrv_ms REAL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sets_session ON sets(session_id);

CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at);

CREATE TABLE IF NOT EXISTS agent_pending_actions (
    action_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    messages_json TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    tool_call_id TEXT NOT NULL DEFAULT '',
    actions_json TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apa_session ON agent_pending_actions(session_id);
CREATE INDEX IF NOT EXISTS idx_apa_expires ON agent_pending_actions(expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT PRIMARY KEY,
    fails INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    updated_at TEXT
);
"""

def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    # 增量迁移: 老库补 actions_json 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_pending_actions)").fetchall()]
    if "actions_json" not in cols:
        conn.execute("ALTER TABLE agent_pending_actions ADD COLUMN actions_json TEXT")
    body_cols = [r[1] for r in conn.execute("PRAGMA table_info(body_metrics)").fetchall()]
    if "hrv_ms" not in body_cols:
        conn.execute("ALTER TABLE body_metrics ADD COLUMN hrv_ms REAL")
    session_cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    if "planned_notes" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN planned_notes TEXT")
    if "actual_notes" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN actual_notes TEXT")
    _migrate_session_notes(conn)
    _sync_builtin_exercise_aliases(conn)
    _repair_session_execution_status(conn)
    conn.commit()
    conn.close()


def _migrate_session_notes(conn):
    """把旧 notes 按当时状态拆成计划/完成备注；幂等，不修改原始 legacy 字段。"""
    conn.execute("""
        UPDATE sessions
        SET actual_notes=COALESCE(actual_notes, notes)
        WHERE status IN ('done', 'partial') AND notes IS NOT NULL AND notes != ''
    """)
    conn.execute("""
        UPDATE sessions
        SET planned_notes=COALESCE(planned_notes, notes)
        WHERE status NOT IN ('done', 'partial') AND notes IS NOT NULL AND notes != ''
    """)


def _repair_session_execution_status(conn):
    """让状态与实际记录一致，防止“备注已完成但状态仍 planned”。"""
    rows = conn.execute("""
        SELECT s.id, s.status,
               SUM(CASE WHEN st.actual_reps IS NOT NULL OR st.actual_kg IS NOT NULL
                        THEN 1 ELSE 0 END) AS actual_sets,
               COUNT(st.id) AS total_sets
        FROM sessions s
        LEFT JOIN sets st ON st.session_id=s.id
        GROUP BY s.id
    """).fetchall()
    for r in rows:
        actual = r["actual_sets"] or 0
        total = r["total_sets"] or 0
        if r["status"] == "planned" and actual:
            new_status = "done" if actual >= total else "partial"
            conn.execute("UPDATE sessions SET status=? WHERE id=?", [new_status, r["id"]])

    # 兼容早期用备注代替状态写入的 Relax 课：只有明确括号标记才修复，避免误判普通计划备注。
    conn.execute("""
        UPDATE sessions
        SET status='done',
            actual_notes=COALESCE(actual_notes, '用户已标注完成'),
            planned_notes=TRIM(REPLACE(REPLACE(COALESCE(planned_notes, notes),
                                               '（已完成）', ''), '(已完成)', ''))
        WHERE (status='planned' OR actual_notes='用户已标注完成')
          AND (notes LIKE '%（已完成）%' OR notes LIKE '%(已完成)%')
    """)


# 只为当前库确实存在的标准动作建立别名；不会把用户自定义动作误并入内置动作。
BUILTIN_EXERCISE_ALIASES = {
    "卧推": "杠铃卧推",
    "平卧推": "杠铃卧推",
    "胸推": "杠铃卧推",
    "bench press": "杠铃卧推",
    "深蹲": "杠铃深蹲",
    "颈后深蹲": "杠铃深蹲",
    "squat": "杠铃深蹲",
    "硬拉": "传统硬拉",
    "deadlift": "传统硬拉",
    "划船": "杠铃划船",
    "杠铃俯身划船": "杠铃划船",
    "推肩": "坐姿哑铃推肩",
    "肩推": "坐姿哑铃推肩",
    "哑铃推举": "坐姿哑铃推肩",
    "shoulder press": "坐姿哑铃推肩",
    "高位下拉": "引体/高位下拉",
    "lat pulldown": "引体/高位下拉",
    "腿举": "倒蹬",
    "leg press": "倒蹬",
    "上斜卧推": "上斜杠铃卧推",
    "二头弯举": "哑铃弯举",
    "哑铃二头弯举": "哑铃弯举",
    "dumbbell curl": "哑铃弯举",
}


def _sync_builtin_exercise_aliases(conn):
    existing = {r[0] for r in conn.execute("SELECT name FROM exercises").fetchall()}
    for alias, canonical in BUILTIN_EXERCISE_ALIASES.items():
        # alias 本身已经是标准动作时不覆盖 canonical 身份。
        if alias in existing or canonical not in existing:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO exercise_aliases(exercise_id, alias, source)
               SELECT id, ?, 'builtin' FROM exercises WHERE name=?""",
            [alias, canonical])

def backup_db_if_new_day():
    """每日首次调用时备份数据库（幂等）。"""
    import shutil, glob
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    marker = os.path.join(os.path.dirname(DB_PATH), f'.backup-{today}')
    if os.path.exists(marker):
        return
    existing = glob.glob(os.path.join(os.path.dirname(DB_PATH), f'fitness.db.bak-{datetime.now().strftime("%Y%m%d")}*'))
    if existing:
        open(marker, 'w').close()
        return
    try:
        shutil.copy2(DB_PATH, os.path.join(os.path.dirname(DB_PATH), f'fitness.db.bak-{today}'))
        open(marker, 'w').close()
    except Exception:
        pass

def exercises_map():
    """返回 {name: id} 映射"""
    conn = get_db()
    rows = conn.execute("SELECT id, name FROM exercises").fetchall()
    conn.close()
    return {r['name']: r['id'] for r in rows}

def sessions_by_cycle(cycle_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sessions WHERE cycle_id=? ORDER BY date", [cycle_id]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_session_detail(session_id):
    conn = get_db()
    session = conn.execute("SELECT * FROM sessions WHERE id=?", [session_id]).fetchone()
    if not session:
        return None
    s = dict(session)
    sets_rows = conn.execute("""
        SELECT s.*, e.name as exercise_name, e.pattern, e.is_main
        FROM sets s JOIN exercises e ON s.exercise_id = e.id
        WHERE s.session_id=? ORDER BY s.set_no
    """, [session_id]).fetchall()
    s['sets'] = [dict(r) for r in sets_rows]
    cardio_row = conn.execute("SELECT * FROM cardio WHERE session_id=?", [session_id]).fetchone()
    s['cardio'] = dict(cardio_row) if cardio_row else None
    conn.close()
    return s

def get_all_exercises():
    conn = get_db()
    rows = conn.execute("SELECT * FROM exercises ORDER BY pattern, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_sessions():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.*, c.phase FROM sessions s
        LEFT JOIN cycles c ON s.cycle_id = c.id
        ORDER BY s.date
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_latest_cycle():
    conn = get_db()
    row = conn.execute("SELECT * FROM cycles ORDER BY start_date DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None

# ── write helpers ──
def insert_exercise(name, pattern, equipment=None, is_main=0, notes=None):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO exercises(name,pattern,equipment,is_main,notes) VALUES(?,?,?,?,?)",
        [name, pattern, equipment, is_main, notes]
    )
    conn.commit()
    conn.close()

# ── 合并式录入(不破坏计划数据) ──
def upsert_session(date, cycle_id, week_no, day_no, type_, sleep_h=None,
                   bodyweight=None, rpe=None, status='planned', notes=None,
                   planned_notes=None, actual_notes=None):
    """合并式更新 session；notes 是兼容参数，按状态路由且空值不覆盖。"""
    conn = get_db()
    legacy_note = notes if notes not in (None, "") else None
    if planned_notes is None:
        planned_notes = legacy_note if status not in ("done", "partial") else None
    if actual_notes is None:
        actual_notes = legacy_note if status in ("done", "partial") else None
    row = conn.execute("SELECT id FROM sessions WHERE date=? AND type=?", [date, type_]).fetchone()
    if row:
        sid = row['id']
        conn.execute("""
            UPDATE sessions SET
              cycle_id=COALESCE(?,cycle_id), week_no=COALESCE(?,week_no), day_no=COALESCE(?,day_no),
              sleep_h=COALESCE(?,sleep_h), bodyweight=COALESCE(?,bodyweight), rpe=COALESCE(?,rpe),
              status=?,
              notes=COALESCE(?, notes),
              planned_notes=COALESCE(?, planned_notes),
              actual_notes=COALESCE(?, actual_notes)
            WHERE id=?""",
            [cycle_id, week_no, day_no, sleep_h, bodyweight, rpe, status,
             legacy_note, planned_notes if planned_notes not in (None, "") else None,
             actual_notes if actual_notes not in (None, "") else None, sid])
    else:
        cur = conn.execute(
            """INSERT INTO sessions(date,cycle_id,week_no,day_no,type,sleep_h,bodyweight,rpe,
                                   status,notes,planned_notes,actual_notes)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [date, cycle_id, week_no, day_no, type_, sleep_h, bodyweight, rpe,
             status, legacy_note, planned_notes, actual_notes])
        sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid

def get_body_metric(date):
    """读取某日身体指标；不存在返回空骨架。"""
    conn = get_db()
    row = conn.execute("SELECT * FROM body_metrics WHERE date=?", [date]).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"date": date, "weight": None, "sleep_h": None,
            "resting_hr": None, "hrv_ms": None, "notes": ""}

def merge_session_sets(session_id, payload):
    """合并式录入组数据, 绝不删除计划组。
    payload: [{"exercise_id": int, "groups": [{"kg":..,"reps":..,"rpe":..,"notes":..}]}, ...]
    规则:
      - 每个动作按计划行(set_no 升序, 排除 extra 行)与实际组一一对位 → 更新 actual + status='done'
      - 实际组多于计划行 → 追加 extra 行
      - 无计划行的新动作 → 替换语义(先删该动作已有的 done/extra 行再插入)，保证重复调用幂等
      - 计划行未被实际组覆盖 → status 由 planned 转 'skipped'(未做)
    """
    conn = get_db()
    for item in payload:
        eid = item['exercise_id']
        groups = item['groups']
        planned_rows = conn.execute(
            "SELECT id FROM sets WHERE session_id=? AND exercise_id=? AND status != 'extra' ORDER BY set_no, id",
            [session_id, eid]).fetchall()
        if not planned_rows:
            # 新动作: 替换语义 — 清掉该动作此前的实际记录(避免重复调用导致组数翻倍)
            conn.execute(
                "DELETE FROM sets WHERE session_id=? AND exercise_id=? AND status IN ('done','extra')",
                [session_id, eid])
        else:
            # 有计划行: 清掉此前的 extra 行(将由本次 groups 重新生成)，保证幂等
            conn.execute(
                "DELETE FROM sets WHERE session_id=? AND exercise_id=? AND status='extra'",
                [session_id, eid])
        max_set_no = conn.execute(
            "SELECT COALESCE(MAX(set_no),0) as m FROM sets WHERE session_id=? AND exercise_id=?",
            [session_id, eid]).fetchone()['m']
        for i, g in enumerate(groups):
            if i < len(planned_rows):
                conn.execute(
                    "UPDATE sets SET actual_kg=?, actual_reps=?, rpe=COALESCE(?,rpe), status='done', notes=COALESCE(?,notes) WHERE id=?",
                    [g.get('kg'), g.get('reps'), g.get('rpe'), g.get('notes'), planned_rows[i]['id']])
            else:
                new_status = 'extra' if planned_rows else 'done'
                max_set_no += 1
                conn.execute(
                    "INSERT INTO sets(session_id,exercise_id,set_no,actual_kg,actual_reps,rpe,status,notes) VALUES(?,?,?,?,?,?,?,?)",
                    [session_id, eid, max_set_no, g.get('kg'), g.get('reps'), g.get('rpe'), new_status, g.get('notes')])
        if len(groups) < len(planned_rows):
            for r in planned_rows[len(groups):]:
                conn.execute("UPDATE sets SET status='skipped' WHERE id=? AND status='planned'", [r['id']])
        # 实际记录写入后同步课状态，避免 sets 已有 actual 但 session 仍是 planned。
        counts = conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN actual_reps IS NOT NULL OR actual_kg IS NOT NULL
                            THEN 1 ELSE 0 END) AS actual
            FROM sets WHERE session_id=?
        """, [session_id]).fetchone()
        if (counts["actual"] or 0) > 0:
            execution_status = "done" if (counts["actual"] or 0) >= (counts["total"] or 0) else "partial"
            conn.execute("UPDATE sessions SET status=? WHERE id=?", [execution_status, session_id])
    conn.commit()
    conn.close()

def upsert_cardio(session_id, kind, planned_json=None, actual_json=None, distance_km=None, avg_hr=None, max_hr=None, duration_s=None, avg_pace_sec=None):
    """cardio 存在则 UPDATE, 不存在则 INSERT"""
    conn = get_db()
    row = conn.execute("SELECT id FROM cardio WHERE session_id=? AND kind=?", [session_id, kind]).fetchone()
    if row:
        conn.execute("""
            UPDATE cardio SET
              planned_json=COALESCE(?,planned_json), actual_json=COALESCE(?,actual_json),
              distance_km=COALESCE(?,distance_km), avg_hr=COALESCE(?,avg_hr), max_hr=COALESCE(?,max_hr),
              duration_s=COALESCE(?,duration_s), avg_pace_sec=COALESCE(?,avg_pace_sec)
            WHERE id=?""",
            [planned_json, actual_json, distance_km, avg_hr, max_hr, duration_s, avg_pace_sec, row['id']])
    else:
        conn.execute(
            "INSERT INTO cardio(session_id,kind,planned_json,actual_json,distance_km,avg_hr,max_hr,duration_s,avg_pace_sec) VALUES(?,?,?,?,?,?,?,?,?)",
            [session_id, kind, planned_json or '{}', actual_json or '{}', distance_km, avg_hr, max_hr, duration_s, avg_pace_sec])
    conn.commit()
    conn.close()

def insert_cycle(start, end, phase, goals_json='[]', notes=''):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO cycles(start_date,end_date,phase,goals_json,notes) VALUES(?,?,?,?,?)",
        [start, end, phase, goals_json, notes]
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid

def insert_session(date, cycle_id, week_no, day_no, type_, sleep_h=None,
                   bodyweight=None, rpe=None, status='planned', notes='',
                   planned_notes=None, actual_notes=None):
    """插入训练课；若 (date,type) 已存在则做字段合并更新。

    安全约束: 绝不能使用 INSERT OR REPLACE。SQLite 的 REPLACE 会先删除冲突旧行，
    而 sets/cardio 对 sessions 是 ON DELETE CASCADE，旧实现会把该课全部组数据
    级联清空(create_plan 覆盖已有日期时触发真实数据丢失)。
    """
    conn = get_db()
    legacy_note = notes if notes not in (None, "") else None
    planned_note_value = planned_notes
    if planned_note_value is None and status not in ("done", "partial"):
        planned_note_value = legacy_note
    actual_note_value = actual_notes
    if actual_note_value is None and status in ("done", "partial"):
        actual_note_value = legacy_note
    row = conn.execute(
        "SELECT id FROM sessions WHERE date=? AND type=?", [date, type_]).fetchone()
    if row:
        sid = row['id']
        conn.execute("""
            UPDATE sessions SET
              cycle_id=COALESCE(?,cycle_id), week_no=COALESCE(?,week_no), day_no=COALESCE(?,day_no),
              sleep_h=COALESCE(?,sleep_h), bodyweight=COALESCE(?,bodyweight), rpe=COALESCE(?,rpe),
              status=?, notes=COALESCE(?,notes),
              planned_notes=COALESCE(?,planned_notes), actual_notes=COALESCE(?,actual_notes)
            WHERE id=?""",
            [cycle_id, week_no, day_no, sleep_h, bodyweight, rpe, status,
             legacy_note,
             planned_note_value if planned_note_value not in (None, "") else None,
             actual_note_value if actual_note_value not in (None, "") else None, sid])
    else:
        cur = conn.execute(
            """INSERT INTO sessions(date,cycle_id,week_no,day_no,type,sleep_h,bodyweight,rpe,
                                   status,notes,planned_notes,actual_notes)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [date, cycle_id, week_no, day_no, type_, sleep_h, bodyweight, rpe,
             status, legacy_note, planned_note_value, actual_note_value])
        sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid

def insert_set(session_id, exercise_id, set_no, planned_kg=None, planned_reps=None, actual_kg=None, actual_reps=None, rpe=None, status='planned', notes=''):
    conn = get_db()
    conn.execute(
        "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,actual_kg,actual_reps,rpe,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [session_id, exercise_id, set_no, planned_kg, planned_reps, actual_kg, actual_reps, rpe, status, notes]
    )
    conn.commit()
    conn.close()

def insert_cardio(session_id, kind, planned_json='{}', actual_json='{}', distance_km=None, avg_hr=None, max_hr=None, duration_s=None, avg_pace_sec=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO cardio(session_id,kind,planned_json,actual_json,distance_km,avg_hr,max_hr,duration_s,avg_pace_sec) VALUES(?,?,?,?,?,?,?,?,?)",
        [session_id, kind, planned_json, actual_json, distance_km, avg_hr, max_hr, duration_s, avg_pace_sec]
    )
    conn.commit()
    conn.close()

def insert_body_metric(date, weight=None, sleep_h=None, resting_hr=None,
                       hrv_ms=None, notes=''):
    conn = get_db()
    conn.execute(
        """INSERT INTO body_metrics(date,weight,sleep_h,resting_hr,hrv_ms,notes)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             weight=COALESCE(excluded.weight, body_metrics.weight),
             sleep_h=COALESCE(excluded.sleep_h, body_metrics.sleep_h),
             resting_hr=COALESCE(excluded.resting_hr, body_metrics.resting_hr),
             hrv_ms=COALESCE(excluded.hrv_ms, body_metrics.hrv_ms),
             notes=CASE WHEN excluded.notes != '' THEN excluded.notes ELSE body_metrics.notes END""",
        [date, weight, sleep_h, resting_hr, hrv_ms, notes]
    )
    conn.commit()
    conn.close()

def get_all_sets_for_exercise(exercise_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT st.*, s.date FROM sets st
        JOIN sessions s ON st.session_id = s.id
        WHERE st.exercise_id=? AND st.actual_kg IS NOT NULL AND st.actual_reps IS NOT NULL
        AND st.status != 'skipped'
        ORDER BY s.date
    """, [exercise_id]).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_sessions_with_sets():
    """返回所有含组数据的 session（用于分析）"""
    conn = get_db()
    session_rows = conn.execute("""
        SELECT s.*, c.phase FROM sessions s
        LEFT JOIN cycles c ON s.cycle_id = c.id
        WHERE s.status = 'done'
        ORDER BY s.date
    """).fetchall()
    result = []
    for sr in session_rows:
        sid = sr['id']
        s = dict(sr)
        sets_rows = conn.execute("""
            SELECT st.*, e.name as exercise_name, e.pattern, e.is_main
            FROM sets st JOIN exercises e ON st.exercise_id = e.id
            WHERE st.session_id=? AND st.actual_kg IS NOT NULL AND st.actual_reps IS NOT NULL
            AND st.status != 'skipped'
        """, [sid]).fetchall()
        s['sets'] = [dict(r) for r in sets_rows]
        cardio_row = conn.execute("SELECT * FROM cardio WHERE session_id=?", [sid]).fetchone()
        s['cardio'] = dict(cardio_row) if cardio_row else None
        result.append(s)
    conn.close()
    return result
