"""分析引擎: e1RM / 容量 / ACWR / 依从性 / 平台期 / 跑步经济性 / 减载触发"""
from db import get_db, get_sessions_with_sets, get_all_exercises
import json, math
from datetime import datetime, timedelta

def epley_e1rm(kg, reps):
    if reps == 1: return kg
    if reps > 10: return None  # 超过10次的组 Epley 失真严重，不用于估算
    return round(kg * (1 + reps / 30), 1)

def compute_e1rm_history():
    """返回 {exercise_name: [{date, e1rm, kg, reps}, ...]}"""
    sessions = get_sessions_with_sets()
    history = {}
    for s in sessions:
        for st in s['sets']:
            name = st['exercise_name']
            if name not in history:
                history[name] = []
            erm = epley_e1rm(st['actual_kg'], st['actual_reps'])
            if erm is None: continue
            # 取本次该动作最佳 e1RM
            existing = [e for e in history[name] if e['date'] == s['date']]
            if existing:
                if erm > existing[0]['e1rm']:
                    existing[0]['e1rm'] = erm
                    existing[0]['kg'] = st['actual_kg']
                    existing[0]['reps'] = st['actual_reps']
            else:
                history[name].append({
                    'date': s['date'], 'e1rm': erm,
                    'kg': st['actual_kg'], 'reps': st['actual_reps']
                })
    return history

def compute_volume_trend(pattern_filter=None):
    """返回 {week_label: kgs} 按周按动作模式汇总容量"""
    sessions = get_sessions_with_sets()
    weekly = {}
    for s in sessions:
        d = datetime.strptime(s['date'], '%Y-%m-%d')
        iso = d.isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        if wk not in weekly:
            weekly[wk] = {'push': 0, 'pull': 0, 'squat': 0, 'hinge': 0, 'core': 0, 'other': 0}
        for st in s['sets']:
            kg = st['actual_kg'] or 0
            reps = st['actual_reps'] or 0
            vol = kg * reps
            p = st['pattern']
            if p in weekly[wk]:
                weekly[wk][p] += vol
            else:
                weekly[wk]['other'] += vol
    return dict(sorted(weekly.items()))

def compute_acwr():
    """急慢性负荷比, 用 sRPE 做统一负荷单位.
    返回 [{date, acute, chronic, ratio, status}, ...]
    status: normal / high / low / insufficient"""
    sessions = get_sessions_with_sets()
    if not sessions:
        return []
    loads = []
    for s in sessions:
        # sRPE 近似: session RPE × 训练时长 (分钟), 缺少时长则用组数估算
        rpe = s.get('rpe') or 5
        set_count = len(s['sets'])
        duration_est = set_count * 3 + (20 if s['cardio'] else 0)  # 每组≈3分钟, 力量训练主体
        load = rpe * duration_est
        loads.append({'date': s['date'], 'load': load})
    
    result = []
    for i, entry in enumerate(loads):
        d = datetime.strptime(entry['date'], '%Y-%m-%d')
        # 7天急性负荷
        acute_7d = sum(e['load'] for e in loads if (d - datetime.strptime(e['date'], '%Y-%m-%d')).days < 7 and (d - datetime.strptime(e['date'], '%Y-%m-%d')).days >= 0)
        # 28天慢性负荷均值
        chronic_28d = [e['load'] for e in loads if (d - datetime.strptime(e['date'], '%Y-%m-%d')).days < 28 and (d - datetime.strptime(e['date'], '%Y-%m-%d')).days >= 0]
        chronic = sum(chronic_28d) / 4 if chronic_28d else 0
        if len(chronic_28d) < 10:
            status = 'insufficient'
            ratio = None
        else:
            ratio = round(acute_7d / chronic, 2) if chronic > 0 else 1.0
            if ratio > 1.5: status = 'high'
            elif ratio < 0.8: status = 'low'
            else: status = 'normal'
        result.append({
            'date': entry['date'], 'acute_7d': acute_7d,
            'chronic_28d_avg': round(chronic, 1) if chronic_28d else 0,
            'ratio': ratio, 'status': status
        })
    return result

def detect_plateaus(e1rm_history, stale_sessions=3, threshold_pct=0):
    """检测平台期: 连续 n 次训练 e1RM 无提升"""
    plateaus = {}
    for ex_name, entries in e1rm_history.items():
        if len(entries) < stale_sessions + 1:
            continue
        erms = [e['e1rm'] for e in entries]
        for i in range(stale_sessions, len(erms)):
            window = erms[i-stale_sessions:i+1]
            if max(window) <= window[0] * (1 + threshold_pct/100):
                # 不增长, 但只报告最新一个平台期
                plateaus[ex_name] = {
                    'since_date': entries[i-stale_sessions]['date'],
                    'current_e1rm': erms[i],
                    'stale_count': stale_sessions
                }
                break
    return plateaus

def compute_adherence():
    """依从性分析: 通过直接查 DB 统计被跳过的动作及次数"""
    from db import get_db
    conn = get_db()
    rows = conn.execute("""
        SELECT e.name, COUNT(*) as cnt FROM sets st
        JOIN exercises e ON st.exercise_id = e.id
        JOIN sessions s ON st.session_id = s.id
        WHERE st.status = 'skipped' AND s.status = 'done'
        GROUP BY e.name ORDER BY cnt DESC
    """).fetchall()
    conn.close()
    return {r['name']: r['cnt'] for r in rows}

def get_deload_triggers():
    """减载触发条件检查, 返回警告列表"""
    triggers = []
    erm_hist = compute_e1rm_history()
    # 检查三大项是否有连续2周 e1RM 下降
    main_lifts = []
    exs = get_all_exercises()
    for ex in exs:
        if ex['is_main']:
            main_lifts.append(ex['name'])
    for lift in main_lifts:
        if lift not in erm_hist or len(erm_hist[lift]) < 2:
            continue
        recent = erm_hist[lift][-3:] if len(erm_hist[lift]) >= 3 else erm_hist[lift]
        if len(recent) >= 2 and all(recent[i]['e1rm'] <= recent[i-1]['e1rm'] for i in range(1, len(recent))):
            delta = recent[-1]['e1rm'] - recent[0]['e1rm']
            triggers.append({
                'type': 'e1rm_decline', 'exercise': lift,
                'delta': round(delta, 1), 'message': f'{lift} e1RM 连续下降 {abs(delta)}kg，建议考虑减载'
            })
    # ACWR
    acwr_data = compute_acwr()
    if acwr_data and acwr_data[-1]['status'] == 'high':
        triggers.append({'type': 'acwr_high', 'message': '急慢性负荷比偏高 (>1.5), 伤病风险上升'})
    return triggers

def running_economy_trend():
    """LSD 同心率下配速趋势"""
    sessions = get_sessions_with_sets()
    trend = []
    for s in sessions:
        c = s.get('cardio')
        if c and c.get('kind') == 'lsd' and c.get('avg_pace_sec'):
            trend.append({
                'date': s['date'],
                'pace_sec': c['avg_pace_sec'],
                'avg_hr': c['avg_hr'],
                'distance_km': c['distance_km']
            })
    return trend

def weekly_summary():
    """综合周度分析包"""
    erm = compute_e1rm_history()
    volume = compute_volume_trend()
    acwr = compute_acwr()
    plateaus = detect_plateaus(erm)
    adherence = compute_adherence()
    triggers = get_deload_triggers()
    running = running_economy_trend()
    return {
        'e1rm': erm,
        'volume': volume,
        'acwr': acwr[-1] if acwr else None,
        'acwr_history': acwr,
        'plateaus': plateaus,
        'adherence': adherence,
        'triggers': triggers,
        'running_trend': running
    }


# ── 周度报告(按周切片) ──

def _fmt_kg(kg):
    if kg is None: return ''
    return '自重' if kg == 0 else f'{kg:g}kg'

def _summarize_day_sets(sets):
    """按动作分组, 返回 (计划摘要列表, 实际摘要列表)"""
    groups, order = {}, []
    for st in sets:
        n = st['exercise_name']
        if n not in groups: groups[n] = []; order.append(n)
        groups[n].append(st)
    planned_parts, actual_parts = [], []
    for name in order:
        g = groups[name]
        planned = [s for s in g if s['status'] != 'extra' and (s['planned_kg'] is not None or s['planned_reps'] is not None)]
        if planned:
            p = planned[0]
            txt = f"{name} {_fmt_kg(p['planned_kg'])}×{p['planned_reps']}"
            if len(planned) > 1: txt += f"×{len(planned)}组"
            planned_parts.append(txt)
        done = [s for s in g if s['status'] != 'skipped' and s['actual_reps'] is not None]
        if done:
            parts, cur, cnt = [], None, 0
            for s in done:
                lb = f"{_fmt_kg(s['actual_kg'])}×{s['actual_reps']}"
                if lb == cur: cnt += 1
                else:
                    if cur: parts.append(cur + (f"×{cnt}" if cnt > 1 else ""))
                    cur, cnt = lb, 1
            if cur: parts.append(cur + (f"×{cnt}" if cnt > 1 else ""))
            actual_parts.append(f"{name} {'/'.join(parts)}")
        elif any(s['status'] == 'skipped' for s in g):
            actual_parts.append(f"{name} 未做")
    return planned_parts, actual_parts

def _e1rm_at_boundary(history, boundary_date):
    """各动作在 boundary_date 之前(含)的最新 e1RM"""
    snap = {}
    for name, entries in history.items():
        valid = [e for e in entries if e['date'] <= boundary_date]
        if valid:
            last = valid[-1]
            snap[name] = {'e1rm': last['e1rm'], 'set': f"{last['kg']:g}×{last['reps']}", 'date': last['date']}
    return snap

def list_available_weeks():
    """根据训练记录范围生成可选周列表"""
    conn = get_db()
    row = conn.execute("SELECT MIN(date) as mn, MAX(date) as mx FROM sessions").fetchone()
    conn.close()
    if not row or not row['mn']:
        return []
    first = datetime.strptime(row['mn'], '%Y-%m-%d')
    last = datetime.strptime(row['mx'], '%Y-%m-%d')
    monday = first - timedelta(days=first.weekday())
    today = datetime.now()
    weeks = []
    while monday <= last or monday <= today:
        ws = monday.strftime('%Y-%m-%d')
        we = (monday + timedelta(days=6)).strftime('%Y-%m-%d')
        weeks.append({'start': ws, 'end': we,
                      'is_current': ws <= today.strftime('%Y-%m-%d') <= we,
                      'is_complete': we < today.strftime('%Y-%m-%d')})
        monday += timedelta(days=7)
    return weeks

def compute_week_review(week_start=None):
    """真正的周度报告: 计划vs实际对照 + 汇总 + e1RM对比 + 警报"""
    if week_start:
        ws = datetime.strptime(week_start, '%Y-%m-%d')
        ws = ws - timedelta(days=ws.weekday())
    else:
        today = datetime.now()
        ws = today - timedelta(days=today.weekday())
    we = ws + timedelta(days=6)
    ws_s, we_s = ws.strftime('%Y-%m-%d'), we.strftime('%Y-%m-%d')
    today_s = datetime.now().strftime('%Y-%m-%d')

    conn = get_db()
    sess_rows = conn.execute(
        "SELECT * FROM sessions WHERE date BETWEEN ? AND ? ORDER BY date", [ws_s, we_s]
    ).fetchall()
    days = []
    for sr in sess_rows:
        s = dict(sr)
        sets_rows = conn.execute("""
            SELECT st.*, e.name as exercise_name FROM sets st
            JOIN exercises e ON st.exercise_id = e.id
            WHERE st.session_id=? ORDER BY st.set_no, st.id
        """, [s['id']]).fetchall()
        sets = [dict(r) for r in sets_rows]
        planned_parts, actual_parts = _summarize_day_sets(sets)

        cardio_row = conn.execute("SELECT * FROM cardio WHERE session_id=?", [s['id']]).fetchone()
        cardio = dict(cardio_row) if cardio_row else None
        cardio_txt = None
        if cardio:
            try:
                act = json.loads(cardio.get('actual_json') or '{}')
                if act.get('total_interval_m'):
                    cardio_txt = f"间歇 {act['total_interval_m']}m"
                elif act.get('km'):
                    cardio_txt = f"{act['km']}km"
                if cardio.get('avg_pace_sec'):
                    ps = cardio['avg_pace_sec']
                    cardio_txt += f" @{ps//60}:{ps%60:02d}"
            except: pass

        days.append({
            'date': s['date'], 'type': s['type'], 'status': s['status'],
            'rpe': s['rpe'], 'sleep_h': s['sleep_h'], 'notes': s.get('notes') or '',
            'planned': planned_parts, 'actual': actual_parts, 'cardio': cardio_txt,
            'week_no': s.get('week_no'),
            '_cardio_raw': cardio
        })

    # 力量亮点: 本周各主项最重组 + 最佳 e1RM 组
    main_lifts = ['杠铃卧推', '杠铃深蹲', '传统硬拉']
    highlights = {}
    for lift in main_lifts:
        best_w, best_e = None, None
        for d in days:
            for st_row in conn.execute("""
                SELECT st.*, e.name as exercise_name FROM sets st
                JOIN exercises e ON st.exercise_id = e.id
                JOIN sessions se ON st.session_id = se.id
                WHERE se.date=? AND e.name=? AND st.actual_kg IS NOT NULL AND st.actual_reps IS NOT NULL
                AND st.status != 'skipped'
            """, [d['date'], lift]).fetchall():
                st = dict(st_row)
                if best_w is None or st['actual_kg'] > best_w['kg']:
                    best_w = {'kg': st['actual_kg'], 'reps': st['actual_reps'], 'date': d['date']}
                erm = epley_e1rm(st['actual_kg'], st['actual_reps'])
                if erm and (best_e is None or erm > best_e['e1rm']):
                    best_e = {'e1rm': erm, 'kg': st['actual_kg'], 'reps': st['actual_reps'], 'date': d['date']}
        if best_w or best_e:
            highlights[lift] = {'heaviest': best_w, 'best_e1rm': best_e}

    # 跑步汇总
    run = {'interval_sessions': 0, 'interval_m': 0, 'lsd_km': 0, 'lsd_sessions': 0}
    for d in days:
        cr = d.get('_cardio_raw')
        if not cr or d['status'] not in ('done', 'partial'): continue
        try: act = json.loads(cr.get('actual_json') or '{}')
        except: act = {}
        if cr['kind'] == 'interval':
            vol = act.get('total_interval_m')
            if not vol and act.get('intervals'):
                vol = sum(i.get('m', 0) * i.get('count', 0) for i in act['intervals'])
            if vol:
                run['interval_sessions'] += 1
                run['interval_m'] += int(vol)
        elif cr['kind'] == 'lsd':
            km = cr.get('distance_km') or (act.get('km') if d['status'] == 'done' else 0)
            if km:
                run['lsd_sessions'] += 1
                run['lsd_km'] += km

    # 身体数据
    rpes = [d['rpe'] for d in days if d.get('rpe')]
    sleeps = [d['sleep_h'] for d in days if d.get('sleep_h')]
    wellness = {
        'avg_rpe': round(sum(rpes)/len(rpes), 1) if rpes else None,
        'avg_sleep': round(sum(sleeps)/len(sleeps), 1) if sleeps else None,
        'done': sum(1 for d in days if d['status'] in ('done', 'rest')),
        'total': len(days)
    }

    # e1RM 本周末 vs 上周末
    history = compute_e1rm_history()
    prev_boundary = (ws - timedelta(days=1)).strftime('%Y-%m-%d')
    e1rm_now = _e1rm_at_boundary(history, we_s)
    e1rm_prev = _e1rm_at_boundary(history, prev_boundary)
    e1rm_compare = {}
    for lift in main_lifts:
        now_v = e1rm_now.get(lift)
        prev_v = e1rm_prev.get(lift)
        e1rm_compare[lift] = {
            'now': now_v, 'prev': prev_v,
            'delta': round(now_v['e1rm'] - prev_v['e1rm'], 1) if (now_v and prev_v) else None
        }

    # 本周跳过
    skipped = {}
    for d in days:
        for st_row in conn.execute("""
            SELECT e.name, COUNT(*) as cnt FROM sets st
            JOIN exercises e ON st.exercise_id = e.id
            JOIN sessions se ON st.session_id = se.id
            WHERE se.date=? AND st.status='skipped' GROUP BY e.name
        """, [d['date']]).fetchall():
            skipped[st_row['name']] = skipped.get(st_row['name'], 0) + st_row['cnt']

    conn.close()

    acwr_data = compute_acwr()
    out_days = [{k: v for k, v in d.items() if k != '_cardio_raw'} for d in days]

    return {
        'week_start': ws_s, 'week_end': we_s,
        'is_current': ws_s <= today_s <= we_s,
        'is_complete': we_s < today_s,
        'week_no': next((d['week_no'] for d in days if d.get('week_no')), None),
        'days': out_days,
        'highlights': highlights,
        'running': run,
        'wellness': wellness,
        'e1rm_compare': e1rm_compare,
        'skipped': skipped,
        'plateaus': detect_plateaus(history),
        'triggers': get_deload_triggers(),
        'acwr': acwr_data[-1] if acwr_data else None,
        'weeks': list_available_weeks()
    }
