"""v2.9: 概览睡眠当日口径 + 身体指标分字段最新值 + 训练睡眠同步"""
from datetime import date, timedelta

TODAY = date.today().isoformat()
YDAY = (date.today() - timedelta(days=1)).isoformat()
D3 = (date.today() - timedelta(days=3)).isoformat()
D5 = (date.today() - timedelta(days=5)).isoformat()
D6 = (date.today() - timedelta(days=6)).isoformat()


def _reset_metrics(rows):
    """rows: [(date, weight, sleep_h, resting_hr, hrv_ms)]"""
    from db import get_db
    conn = get_db()
    conn.execute("DELETE FROM body_metrics")
    for r in rows:
        conn.execute(
            "INSERT INTO body_metrics(date,weight,sleep_h,resting_hr,hrv_ms,notes) VALUES(?,?,?,?,?,'')",
            list(r))
    conn.commit()
    conn.close()


def _card(cards, label):
    return next((c for c in cards if c["label"] == label), None)


# ── 1. 当日缺睡眠: 明确「未记录」，不跨日回退 ──

def test_today_missing_sleep_shows_unrecorded_with_hint():
    from agent.tools import tool_get_today_context
    _reset_metrics([(TODAY, 72.8, None, 50, 73)])
    r = tool_get_today_context()
    sleep = _card(r["view_spec"]["cards"], "睡眠")
    assert sleep is not None, "睡眠卡必须始终出现"
    assert sleep["value"] == "未记录"
    assert "补录" in sleep["sub"]
    assert any("今日睡眠未记录" in c for c in r["view_spec"]["caveats"])
    # 同日其它字段不受影响
    assert _card(r["view_spec"]["cards"], "静息心率")["value"] == 50
    assert r["latest_metric"]["sleep_h"] is None
    assert r["latest_metric"]["resting_hr"] == 50


def test_yesterday_sleep_is_not_shown_as_today():
    from agent.tools import tool_get_today_context
    _reset_metrics([(TODAY, 72.8, None, 50, 73), (YDAY, None, 7.5, None, None)])
    r = tool_get_today_context()
    assert _card(r["view_spec"]["cards"], "睡眠")["value"] == "未记录"


def test_today_low_sleep_triggers_readiness_signal():
    from agent.tools import tool_get_today_context
    _reset_metrics([(TODAY, None, 5.8, None, None)])
    r = tool_get_today_context()
    sleep = _card(r["view_spec"]["cards"], "睡眠")
    assert sleep["value"] == 5.8
    assert any("低于6h" in s for s in r["view_spec"]["readiness_signals"])


def test_today_normal_sleep_no_signal():
    from agent.tools import tool_get_today_context
    _reset_metrics([(TODAY, None, 7.5, None, None)])
    r = tool_get_today_context()
    assert _card(r["view_spec"]["cards"], "睡眠")["value"] == 7.5
    assert not any("睡眠" in s for s in r["view_spec"]["readiness_signals"])


# ── 2. 体重/RHR/HRV: 各字段独立取最新非空值并标注记录日 ──

def test_per_field_latest_values_with_own_dates():
    from agent.tools import tool_get_today_context
    # 体重最新在D3；RHR最新在今天，基线取D5/D6两次；HRV只有D6
    _reset_metrics([
        (TODAY, None, None, 50, None),
        (D3, 71.0, None, None, None),
        (D6, 76.0, 7.5, 60, 60),
        (D5, 77.0, 7.0, 61, 65),
    ])
    r = tool_get_today_context()
    w = _card(r["view_spec"]["cards"], "体重")
    assert w["value"] == 71.0 and D3 in w["sub"]
    hr = _card(r["view_spec"]["cards"], "静息心率")
    assert hr["value"] == 50 and TODAY in hr["sub"] and "2次均值" in hr["sub"]
    hrv = _card(r["view_spec"]["cards"], "HRV")
    assert hrv["value"] == 65 and D5 in hrv["sub"]
    lm = r["latest_metric"]
    assert lm["weight_date"] == D3 and lm["resting_hr_date"] == TODAY and lm["hrv_date"] == D5


def test_rhr_baseline_ignores_newer_row_null_field():
    """最新一整行缺RHR时，基线仍应基于历史RHR，而非被空值行截断"""
    from agent.tools import tool_get_today_context
    _reset_metrics([
        (TODAY, 72.8, None, None, None),   # 最新行只有体重
        (YDAY, None, None, 54, None),
        (D5, None, None, 60, None),
    ])
    r = tool_get_today_context()
    hr = _card(r["view_spec"]["cards"], "静息心率")
    assert hr["value"] == 54 and "1次均值" in hr["sub"]


# ── 3. 训练课携带睡眠/体重 → 同步到 body_metrics ──

def test_log_training_sleep_syncs_to_body_metrics():
    from db import get_body_metric
    from agent.tools import tool_log_training, tool_get_today_context
    _reset_metrics([])
    result = tool_log_training(date=TODAY, stype="legs", sets=[],
                               sleep_h=7.2, confirmed=True)
    assert result["status"] == "done"
    assert get_body_metric(TODAY)["sleep_h"] == 7.2
    assert _card(tool_get_today_context()["view_spec"]["cards"], "睡眠")["value"] == 7.2


def test_session_sync_does_not_overwrite_direct_body_metric():
    from db import get_body_metric, upsert_session, insert_body_metric
    _reset_metrics([])
    insert_body_metric(TODAY, sleep_h=8.0)
    upsert_session(TODAY, None, None, None, "legs", sleep_h=7.0, status="done")
    assert get_body_metric(TODAY)["sleep_h"] == 8.0, "直接录入的身体指标优先"


def test_session_sync_fills_missing_field_only():
    from db import get_body_metric, upsert_session, insert_body_metric
    _reset_metrics([])
    insert_body_metric(TODAY, resting_hr=50)
    upsert_session(TODAY, None, None, None, "legs", sleep_h=7.0, bodyweight=72.5, status="done")
    m = get_body_metric(TODAY)
    assert m["sleep_h"] == 7.0 and m["weight"] == 72.5 and m["resting_hr"] == 50


# ── 4. 历史回填幂等 ──

def test_backfill_body_metrics_from_sessions_idempotent():
    from db import get_db, init_db
    conn = get_db()
    conn.execute("DELETE FROM body_metrics")
    conn.execute("DELETE FROM sessions WHERE date IN ('2034-01-01','2034-01-02')")
    conn.execute("INSERT INTO sessions(date,type,status,sleep_h,bodyweight) VALUES('2034-01-01','legs','done',7.5,NULL)")
    conn.execute("INSERT INTO sessions(date,type,status,sleep_h,bodyweight) VALUES('2034-01-02','push','done',6.4,76.0)")
    conn.commit(); conn.close()

    init_db(); init_db()  # 连跑两次验证幂等
    conn = get_db()
    rows = {r["date"]: dict(r) for r in conn.execute(
        "SELECT * FROM body_metrics WHERE date IN ('2034-01-01','2034-01-02')")}
    conn.close()
    assert rows["2034-01-01"]["sleep_h"] == 7.5
    assert rows["2034-01-02"]["sleep_h"] == 6.4 and rows["2034-01-02"]["weight"] == 76.0
    assert len(rows) == 2

    # 已有直接值不被回填覆盖
    conn = get_db()
    conn.execute("""UPDATE body_metrics SET sleep_h=9.0 WHERE date='2034-01-01'""")
    conn.commit(); conn.close()
    init_db()
    conn = get_db()
    v = conn.execute("SELECT sleep_h FROM body_metrics WHERE date='2034-01-01'").fetchone()[0]
    conn.close()
    assert v == 9.0
