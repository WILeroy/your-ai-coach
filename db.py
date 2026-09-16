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
    notes TEXT,
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
"""

def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

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
def upsert_session(date, cycle_id, week_no, day_no, type_, sleep_h=None, bodyweight=None, rpe=None, status='planned', notes=''):
    """session 存在则 UPDATE(保留 id 和子表数据), 不存在则 INSERT"""
    conn = get_db()
    row = conn.execute("SELECT id FROM sessions WHERE date=? AND type=?", [date, type_]).fetchone()
    if row:
        sid = row['id']
        conn.execute("""
            UPDATE sessions SET
              cycle_id=COALESCE(?,cycle_id), week_no=COALESCE(?,week_no), day_no=COALESCE(?,day_no),
              sleep_h=COALESCE(?,sleep_h), bodyweight=COALESCE(?,bodyweight), rpe=COALESCE(?,rpe),
              status=?, notes=?
            WHERE id=?""",
            [cycle_id, week_no, day_no, sleep_h, bodyweight, rpe, status, notes, sid])
    else:
        cur = conn.execute(
            "INSERT INTO sessions(date,cycle_id,week_no,day_no,type,sleep_h,bodyweight,rpe,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [date, cycle_id, week_no, day_no, type_, sleep_h, bodyweight, rpe, status, notes])
        sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid

def merge_session_sets(session_id, payload):
    """合并式录入组数据, 绝不删除计划组。
    payload: [{"exercise_id": int, "groups": [{"kg":..,"reps":..,"rpe":..,"notes":..}]}, ...]
    规则:
      - 每个动作按计划行(set_no 升序, 排除 extra 行)与实际组一一对位 → 更新 actual + status='done'
      - 实际组多于计划行 → 追加 extra 行
      - 计划行未被实际组覆盖 → status 由 planned 转 'skipped'(未做)
      - 新动作(无计划行) → 全部作为 done 行新增(首组起沿用计划位为空)
    """
    conn = get_db()
    for item in payload:
        eid = item['exercise_id']
        groups = item['groups']
        planned_rows = conn.execute(
            "SELECT id FROM sets WHERE session_id=? AND exercise_id=? AND status != 'extra' ORDER BY set_no, id",
            [session_id, eid]).fetchall()
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

def insert_session(date, cycle_id, week_no, day_no, type_, sleep_h=None, bodyweight=None, rpe=None, status='planned', notes=''):
    conn = get_db()
    cur = conn.execute(
        "INSERT OR REPLACE INTO sessions(date,cycle_id,week_no,day_no,type,sleep_h,bodyweight,rpe,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [date, cycle_id, week_no, day_no, type_, sleep_h, bodyweight, rpe, status, notes]
    )
    conn.commit()
    sid = cur.lastrowid
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

def insert_body_metric(date, weight=None, sleep_h=None, resting_hr=None, notes=''):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO body_metrics(date,weight,sleep_h,resting_hr,notes) VALUES(?,?,?,?,?)",
        [date, weight, sleep_h, resting_hr, notes]
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
