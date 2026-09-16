"""Agent 工具层 v2: 结构化读写工具 + view_spec 可视化协议

设计原则:
- 读工具: 窄参数、强类型，返回数据 + view_spec(前端自动渲染图表)
- 写工具: 一律确认门控 (preview -> 用户确认 -> confirmed=true 执行)
- 确认状态持久化到 SQLite (agent_pending_actions)，多 worker 安全
"""
import sys, os, json, uuid
import re
import html as html_mod
import socket
import ipaddress
import urllib.request
import urllib.parse
from html.parser import HTMLParser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, date
from db import (get_db, get_session_detail, exercises_map, upsert_session,
                merge_session_sets, upsert_cardio, get_latest_cycle,
                backup_db_if_new_day)
from analytics import (compute_e1rm_history, compute_volume_trend, compute_acwr,
                        detect_plateaus, compute_adherence, get_deload_triggers,
                        running_economy_trend, epley_e1rm)

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PENDING_EXPIRE_MINUTES = 10


# ============================================================
# 确认状态管理 (SQLite 持久化, 多 worker 安全)
# ============================================================

def store_pending(action_id, session_id, actions, messages):
    """批量存储待确认操作。
    actions: [{tool_name, args, tool_call_id, preview}...] — 一轮内全部 pending 操作
    必须在所有 tool 消息 append 完成后调用，保证快照完整。"""
    conn = get_db()
    now = datetime.now()
    first = actions[0]
    conn.execute(
        """INSERT OR REPLACE INTO agent_pending_actions
           (action_id, session_id, messages_json, tool_name, args_json, tool_call_id,
            actions_json, created_at, expires_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        [action_id, session_id, json.dumps(messages, ensure_ascii=False, default=str),
         first["tool_name"], json.dumps(first.get("args", {}), ensure_ascii=False, default=str),
         first.get("tool_call_id", ""),
         json.dumps(actions, ensure_ascii=False, default=str),
         now.isoformat(), (now + timedelta(minutes=PENDING_EXPIRE_MINUTES)).isoformat()])
    conn.commit()
    conn.close()


def get_pending(action_id=None, session_id=None):
    """取出(并删除)一条 pending action; 按 action_id 或 session 最新一条"""
    conn = get_db()
    row = None
    if action_id:
        row = conn.execute("SELECT * FROM agent_pending_actions WHERE action_id=?",
                           [action_id]).fetchone()
    elif session_id:
        row = conn.execute(
            "SELECT * FROM agent_pending_actions WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
            [session_id]).fetchone()
    if row:
        conn.execute("DELETE FROM agent_pending_actions WHERE action_id=?", [row['action_id']])
        conn.commit()
    conn.close()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row['expires_at']) < datetime.now():
            return None
    except Exception:
        return None
    d = dict(row)
    d['messages'] = json.loads(d.pop('messages_json'))
    # 批量格式: actions_json 数组; 旧格式: 单 action 包装为数组
    if d.get('actions_json'):
        d.pop('tool_name', None)
        d.pop('args_json', None)
        d.pop('tool_call_id', None)
        d['actions'] = json.loads(d.pop('actions_json'))
    else:
        d['actions'] = [{"tool_name": d.pop('tool_name'),
                         "args": json.loads(d.pop('args_json', '{}')),
                         "tool_call_id": d.pop('tool_call_id', '')}]
    return d


def peek_pending_session(session_id):
    """查看 session 是否有 pending(不删除)"""
    conn = get_db()
    row = conn.execute(
        "SELECT expires_at FROM agent_pending_actions WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        [session_id]).fetchone()
    conn.close()
    if not row:
        return False
    try:
        return datetime.fromisoformat(row['expires_at']) >= datetime.now()
    except Exception:
        return False


def cleanup_expired():
    conn = get_db()
    conn.execute("DELETE FROM agent_pending_actions WHERE expires_at < ?",
                 [datetime.now().isoformat()])
    conn.commit()
    conn.close()


# ============================================================
# 通用辅助
# ============================================================

def _match_exercise(name):
    """精确 -> 包含 匹配动作名。返回 (matched_name, candidates)"""
    if not name:
        return None, []
    ex_map = exercises_map()
    if name in ex_map:
        return name, []
    candidates = [k for k in ex_map if name in k or k in name]
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates[:5]


def _preview_or_execute(confirmed, build_preview, execute):
    """通用确认流程: 未确认返回预览，已确认执行(先备份)"""
    if not confirmed:
        return {"status": "pending", "action_id": uuid.uuid4().hex[:8],
                "preview": build_preview()}
    try:
        backup_db_if_new_day()
        result = execute()
        return {"status": "done", "ui_refresh": True, **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _weeks_ago(weeks):
    return (date.today() - timedelta(days=weeks * 7)).isoformat()


# 动作名 -> pattern/equipment 推断(用于库外动作新增建议)
_PATTERN_HINTS = [
    (("蹲",), "squat"), (("硬拉", "罗马尼亚", "臀桥"), "hinge"),
    (("推", "卧推", "飞鸟", "屈伸"), "push"), (("划船", "引体", "下拉", "拉"), "pull"),
    (("弯举",), "accessory"), (("卷腹", "平板", "核心"), "core"),
    (("拉伸", "泡沫轴"), "stretch"), (("跑", "间歇", "lsd"), "cardio"),
]
_EQUIPMENT_HINTS = [
    (("杠铃", "直杠"), "barbell"), (("哑铃",), "dumbbell"),
    (("绳索", "拉力器"), "cable"), (("悍马", "机", "器械"), "machine"),
]


def _infer_exercise_meta(name):
    pattern = "accessory"
    for kws, p in _PATTERN_HINTS:
        if any(k in name for k in kws):
            pattern = p
            break
    equipment = None
    for kws, eq in _EQUIPMENT_HINTS:
        if any(k in name for k in kws):
            equipment = eq
            break
    return pattern, equipment


# ============================================================
# 读工具 (免确认, 带 view_spec)
# ============================================================

def tool_get_today_context(**kwargs):
    """今天日期/星期、当日计划配重、近7天完成状态、最新身体指标"""
    today = date.today().isoformat()
    conn = get_db()

    today_sessions = conn.execute(
        "SELECT * FROM sessions WHERE date=? ORDER BY type", [today]).fetchall()
    today_list = []
    for s in today_sessions:
        s = dict(s)
        sets_rows = conn.execute("""
            SELECT st.*, e.name as exercise_name FROM sets st
            JOIN exercises e ON st.exercise_id=e.id WHERE st.session_id=?
            ORDER BY st.set_no""", [s['id']]).fetchall()
        by_ex = {}
        for st in sets_rows:
            n = st['exercise_name']
            by_ex.setdefault(n, [])
            by_ex[n].append({"set_no": st["set_no"],
                             "planned": '%skg×%s' % (st["planned_kg"] or "?", st["planned_reps"] or "?"),
                             "actual": ('%skg×%s' % (st["actual_kg"], st["actual_reps"])
                                        if st["actual_kg"] is not None else None),
                             "status": st["status"]})
        s['exercises'] = by_ex
        today_list.append(s)

    week_rows = conn.execute(
        """SELECT date, type, status, rpe FROM sessions
           WHERE date >= date(?, '-6 days') AND date <= ? ORDER BY date""",
        [today, today]).fetchall()
    week_summary = [{"date": r['date'], "type": r['type'], "status": r['status'],
                     "rpe": r['rpe']} for r in week_rows]

    metric = conn.execute(
        "SELECT date, weight, sleep_h, resting_hr FROM body_metrics ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()

    done_count = sum(1 for w in week_summary if w['status'] in ('done', 'partial'))
    cards = [{"label": "近7天完成", "value": "%d/%d" % (done_count, len(week_summary)), "unit": "次"}]
    if metric:
        cards.append({"label": "体重(%s)" % metric['date'], "value": metric['weight'], "unit": "kg"})
        cards.append({"label": "睡眠(%s)" % metric['date'], "value": metric['sleep_h'], "unit": "h"})

    return {
        "today": today,
        "weekday": WEEKDAY_CN[date.today().weekday()],
        "today_sessions": today_list,
        "week": week_summary,
        "latest_metric": dict(metric) if metric else None,
        "view_spec": {"view": "metric_cards", "title": "今天 · %s" % today, "cards": cards},
    }


def tool_get_exercise_history(exercise, days=90, limit=50, **kwargs):
    """某动作的训练历史 + e1RM 趋势"""
    matched, candidates = _match_exercise(exercise)
    if not matched:
        return {"error": "未找到动作 '%s'" % exercise, "candidates": candidates,
                "hint": "可用 search_exercises 查找正确名称"}
    since = _weeks_ago(max(1, days // 7))
    conn = get_db()
    rows = conn.execute("""
        SELECT s.date, st.actual_kg, st.actual_reps, st.planned_kg, st.planned_reps, st.rpe, st.status
        FROM sets st JOIN sessions s ON st.session_id=s.id
        JOIN exercises e ON st.exercise_id=e.id
        WHERE e.name=? AND s.date>=? AND st.status != 'skipped'
        ORDER BY s.date DESC, st.set_no LIMIT ?""", [matched, since, int(limit)]).fetchall()
    conn.close()

    history = [dict(r) for r in rows]
    best = {}
    for h in history:
        if h['actual_kg'] and h['actual_reps']:
            erm = epley_e1rm(h['actual_kg'], h['actual_reps'])
            if erm and (h['date'] not in best or erm > best[h['date']]['e1rm']):
                best[h['date']] = {"e1rm": erm, "kg": h['actual_kg'], "reps": h['actual_reps']}
    dates = sorted(best.keys())
    series = [{"name": matched, "data": [best[d]['e1rm'] for d in dates]}]

    return {
        "exercise": matched,
        "sets": history[::-1],
        "e1rm_series": {d: best[d] for d in dates},
        "view_spec": {"view": "line", "title": "%s e1RM 趋势" % matched,
                      "x": dates, "series": series, "y_name": "kg"},
    }


def tool_get_sessions(date_from=None, date_to=None, type=None, status=None, limit=50, **kwargs):
    """查询训练课列表"""
    q = "SELECT * FROM sessions WHERE 1=1"
    args = []
    if date_from:
        q += " AND date>=?"; args.append(date_from)
    if date_to:
        q += " AND date<=?"; args.append(date_to)
    if type:
        q += " AND type=?"; args.append(type)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY date DESC LIMIT ?"; args.append(min(max(int(limit or 50), 1), 200))
    conn = get_db()
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()

    columns = [{"key": "date", "label": "日期"}, {"key": "type", "label": "类型"},
               {"key": "status", "label": "状态"}, {"key": "rpe", "label": "RPE"},
               {"key": "sleep_h", "label": "睡眠"}, {"key": "notes", "label": "备注"}]
    return {"sessions": rows,
            "view_spec": {"view": "table", "title": "训练课(%d条)" % len(rows),
                          "columns": columns, "rows": rows[:50]}}


ANALYTICS_KINDS = ("e1rm", "volume", "acwr", "plateau", "adherence", "deload", "running")


def tool_get_analytics(kind, weeks=12, **kwargs):
    """分析引擎: e1rm/volume/acwr/plateau/adherence/deload/running"""
    if kind not in ANALYTICS_KINDS:
        return {"error": "未知 kind: %s" % kind, "available": list(ANALYTICS_KINDS)}
    weeks = min(max(int(weeks or 12), 1), 52)
    since = _weeks_ago(weeks)

    if kind == "e1rm":
        hist = compute_e1rm_history()
        items = sorted(hist.items(), key=lambda kv: -len(kv[1]))[:6]
        all_dates = sorted({e['date'] for _, es in items for e in es if e['date'] >= since})
        series = []
        for name, entries in items:
            m = {e['date']: e['e1rm'] for e in entries if e['date'] >= since}
            series.append({"name": name, "data": [m.get(d) for d in all_dates]})
        return {"data": {n: [e for e in entries if e["date"] >= since] for n, entries in items},
                "view_spec": {"view": "line", "title": "e1RM 趋势", "x": all_dates,
                              "series": series, "y_name": "kg", "connect_nulls": True}}

    if kind == "volume":
        vol = compute_volume_trend()
        weeks_list = list(vol.keys())[-weeks:]
        patterns = ["squat", "hinge", "push", "pull", "core", "other"]
        series = [{"name": p, "data": [vol[w].get(p, 0) for w in weeks_list]}
                  for p in patterns if any(vol[w].get(p, 0) > 0 for w in weeks_list)]
        return {"data": {w: vol[w] for w in weeks_list},
                "view_spec": {"view": "bar", "title": "周容量吨位", "x": weeks_list,
                              "series": series, "y_name": "kg", "stack": True}}

    if kind == "acwr":
        hist = [h for h in compute_acwr() if h['date'] >= since]
        return {"data": hist,
                "view_spec": {"view": "line", "title": "ACWR 急慢性负荷比",
                              "x": [h['date'] for h in hist],
                              "series": [{"name": "ACWR", "data": [h['ratio'] for h in hist]}],
                              "y_name": "ratio", "mark_line": 1.5}}

    if kind == "plateau":
        plateaus = detect_plateaus(compute_e1rm_history())
        rows = [{"exercise": k, **v} for k, v in plateaus.items()]
        columns = [{"key": "exercise", "label": "动作"}, {"key": "since_date", "label": "停滞自"},
                   {"key": "current_e1rm", "label": "当前e1RM"}, {"key": "stale_count", "label": "连续无提升"}]
        return {"data": plateaus,
                "view_spec": {"view": "table", "title": "平台期检测", "columns": columns, "rows": rows}}

    if kind == "adherence":
        adh = compute_adherence()
        rows = [{"exercise": k, "skipped": v} for k, v in adh.items()]
        columns = [{"key": "exercise", "label": "动作"}, {"key": "skipped", "label": "跳过次数"}]
        return {"data": adh,
                "view_spec": {"view": "table", "title": "依从性(跳过统计)", "columns": columns, "rows": rows}}

    if kind == "deload":
        triggers = get_deload_triggers()
        rows = [{"type": t['type'], "message": t['message']} for t in triggers]
        columns = [{"key": "type", "label": "类型"}, {"key": "message", "label": "信号"}]
        return {"data": triggers,
                "view_spec": {"view": "table", "title": "减载触发信号", "columns": columns, "rows": rows}}

    if kind == "running":
        trend = [t for t in running_economy_trend() if t['date'] >= since]
        return {"data": trend,
                "view_spec": {"view": "line", "title": "LSD 配速趋势",
                              "x": [t['date'] for t in trend],
                              "series": [{"name": "配速", "data": [t['pace_sec'] for t in trend]}],
                              "y_name": "sec/km"}}
    return {"error": "unreachable"}


def tool_get_plan(cycle="current", **kwargs):
    """当前周期计划: 日历 + 目标 + 进度"""
    conn = get_db()
    if cycle == "current":
        row = conn.execute("SELECT * FROM cycles ORDER BY start_date DESC LIMIT 1").fetchone()
    else:
        row = conn.execute("SELECT * FROM cycles WHERE id=?", [int(cycle)]).fetchone()
    if not row:
        conn.close()
        return {"error": "没有周期数据，可用 create_plan 生成"}
    c = dict(row)
    sessions = [dict(s) for s in conn.execute(
        "SELECT * FROM sessions WHERE cycle_id=? ORDER BY date", [c['id']]).fetchall()]
    conn.close()
    c['goals'] = json.loads(c.pop('goals_json') or '[]')
    c.pop('notes', None)
    erm = compute_e1rm_history()
    goals = []
    for g in c['goals']:
        name = g.get('exercise')
        latest = erm.get(name, [{}])[-1] if erm.get(name) else {}
        goals.append(dict(g, current_e1rm=latest.get('e1rm')))
    today = date.today().isoformat()
    return {"cycle": c, "goals_progress": goals,
            "sessions": [{"date": s['date'], "type": s['type'], "status": s['status']}
                         for s in sessions],
            "today_index": next((i for i, s in enumerate(sessions) if s['date'] >= today), None)}


def tool_get_body_metrics(days=90, **kwargs):
    """身体指标历史(体重/睡眠/晨脉)"""
    since = _weeks_ago(max(1, days // 7))
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT date, weight, sleep_h, resting_hr FROM body_metrics WHERE date>=? ORDER BY date",
        [since]).fetchall()]
    conn.close()
    x = [r['date'] for r in rows]
    series = [{"name": "体重kg", "data": [r['weight'] for r in rows]},
              {"name": "睡眠h", "data": [r['sleep_h'] for r in rows]},
              {"name": "晨脉bpm", "data": [r['resting_hr'] for r in rows]}]
    return {"metrics": rows,
            "view_spec": {"view": "line", "title": "身体指标", "x": x, "series": series,
                          "connect_nulls": True}}


def tool_search_exercises(keyword, **kwargs):
    """模糊搜索动作库"""
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT name, pattern, equipment, is_main FROM exercises WHERE name LIKE ? ORDER BY is_main DESC, name LIMIT 20",
        ["%" + keyword + "%"]).fetchall()]
    conn.close()
    return {"results": rows}


# ═══════════════════════════════════════════════
# 联网工具 (免注册: 必应中国版抓取 + 网页正文读取)
# ═══════════════════════════════════════════════

_WEB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _strip_tags(s):
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def tool_web_search(query, max_results=5, **kwargs):
    """联网搜索(搜狗→360→必应 多引擎免注册)。返回标题/链接/摘要，自动在画布渲染来源卡片"""
    query = (query or "").strip()
    if not query:
        return {"error": "query 不能为空"}
    max_results = min(max(int(max_results or 5), 1), 8)

    results = (_search_sogou(query, max_results)
               or _search_so(query, max_results)
               or _search_bing(query, max_results))

    if not results:
        return {"error": "搜索暂不可用或无结果", "hint": "请稍后重试或换个关键词"}
    return {
        "query": query, "results": results,
        "view_spec": {"view": "search_results",
                      "title": "🔍 %s" % query, "results": results},
    }


def _http_get(url, timeout=8, headers=None):
    h = {"User-Agent": _WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _resolve_redirect(link, base="https://www.sogou.com"):
    """解析 /link?url=... 跳转链接的真实URL；失败返回原链接"""
    if not link.startswith("/link") and not link.startswith("https://www.so.com/link"):
        return link
    url = base + link if link.startswith("/") else link
    try:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(url, headers={
                "User-Agent": _WEB_UA}), timeout=6)
            page = resp.read(4096).decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
            return loc if loc.startswith("http") else link
        m = (re.search(r'url=([^"\'>&\s]+)', page)
             or re.search(r'(https?://[^\s"\'<>]+)', page))
        return html_mod.unescape(m.group(1)) if m else link
    except Exception as e:
        return link


def _block_snippet(blk, min_len=25):
    """从结果块兜底提取摘要"""
    for sel in (".fz-mid", ".str-text-info", ".str_info", ".space-txt",
                ".text-layout", ".res-desc", ".g-link", "p"):
        el = blk.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) >= min_len:
                return t[:160]
    texts = [t.strip() for t in blk.stripped_strings if len(t.strip()) > min_len]
    return texts[0][:160] if texts else ""


def _search_sogou(query, max_results):
    try:
        body = _http_get("https://www.sogou.com/web?query=" + urllib.parse.quote(query))
    except Exception:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    items = []
    for blk in soup.select("div.vrwrap"):
        a = blk.select_one("h3 a[href]")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title:
            continue
        items.append({"title": title[:80], "url": link, "snippet": _block_snippet(blk)})
        if len(items) >= max_results:
            break
    if not items:
        return []
    # 并行解析跳转链接
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(items), 5)) as ex:
        real_urls = list(ex.map(lambda it: _resolve_redirect(it["url"]), items))
    for it, u in zip(items, real_urls):
        it["url"] = u
    return [it for it in items if it["url"].startswith("http")]


def _search_so(query, max_results):
    try:
        body = _http_get("https://www.so.com/s?q=" + urllib.parse.quote(query))
    except Exception:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    items = []
    for blk in soup.select("li.res-list, div.res-list, li[class*=res-list]"):
        a = blk.select_one("h3 a[href]")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or not link:
            continue
        items.append({"title": title[:80], "url": link, "snippet": _block_snippet(blk)})
        if len(items) >= max_results:
            break
    if not items:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(items), 5)) as ex:
        real_urls = list(ex.map(lambda it: _resolve_redirect(it["url"], "https://www.so.com"), items))
    for it, u in zip(items, real_urls):
        it["url"] = u
    return [it for it in items if it["url"].startswith("http")]


def _search_bing(query, max_results):
    try:
        body = _http_get("https://cn.bing.com/search?q=" + urllib.parse.quote(query))
    except Exception:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    results = []
    for blk in soup.select("li.b_algo"):
        a = blk.select_one("h2 a[href]") or blk.select_one("a[href^='http']")
        if not a or not a.get("href", "").startswith("http"):
            continue
        title = _strip_tags(a.decode_contents())
        # 清理可能残留的显示URL前缀 (如 "baidu.com › item")
        title = re.sub(r"^[a-z0-9.\-]+\.[a-z]{2,}(\s*›[^ht]*)?(https?://\S+)?\s*", "", title) or title
        p = blk.select_one(".b_caption p") or blk.select_one("p")
        snippet = _strip_tags(p.decode_contents())[:160] if p else ""
        if title:
            results.append({"title": title[:80], "url": a["href"], "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _assert_public_http_url(url):
    """SSRF 防护: 仅允许 http/https + 公网IP + 80/443端口"""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        raise ValueError("URL 无法解析")
    if p.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https")
    if p.port not in (None, 80, 443):
        raise ValueError("仅支持 80/443 端口")
    host = p.hostname
    if not host:
        raise ValueError("缺少主机名")
    # 解析全部IP并检查
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise ValueError("主机名无法解析")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("禁止访问内网/回环地址")


class _TextExtractor(HTMLParser):
    """提取 <title> 与正文可见文本"""
    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        t = data.strip()
        if t:
            self.parts.append(t)


def tool_web_fetch(url, max_chars=4000, **kwargs):
    """读取网页正文文本(trafilatura 抽取主内容)。部分反爬站点(如知乎)会失败"""
    url = (url or "").strip()
    try:
        _assert_public_http_url(url)
    except ValueError as e:
        return {"error": "URL 不允许: %s" % e}

    req = urllib.request.Request(url, headers={
        "User-Agent": _WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(2_000_000)  # 上限2MB
            charset = r.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, "ignore")
    except Exception as e:
        return {"error": "网页读取失败: %s" % str(e)[:80],
                "hint": "该站点可能拦截了程序访问，可改用搜索摘要"}

    import trafilatura
    text = trafilatura.extract(body, include_comments=False,
                               include_tables=True, favor_recall=True) or ""
    title = ""
    tm = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    if tm:
        title = _strip_tags(tm.group(1))
    if not text:
        # trafilatura 失败时退回粗提取
        ex = _TextExtractor()
        try:
            ex.feed(body)
        except Exception:
            pass
        text = re.sub(r"\s{2,}", " ", " ".join(ex.parts)).strip()
        title = title or ex.title.strip()
    max_chars = min(max(int(max_chars or 4000), 500), 8000)
    if not text:
        return {"error": "未能提取正文(可能是JS渲染页面)"}
    return {"title": (title or "")[:100], "url": url,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars}


def tool_show_view(page, **kwargs):
    """让前端切换页面: coach/dashboard/trends/review/plan"""
    valid = {"coach", "dashboard", "trends", "review", "plan"}
    if page not in valid:
        return {"error": "未知页面 %s" % page, "available": sorted(valid)}
    return {"status": "done", "view_spec": {"view": "navigate", "page": page}}


# ============================================================
# 写工具 (确认门控)
# ============================================================

def tool_log_training(date=None, stype=None, sets=None, cardio=None, rpe=None, sleep_h=None,
                      bodyweight=None, notes=None, confirmed=False, **kwargs):
    """记录一次训练"""
    d = date or kwargs.get("date_") or datetime.now().strftime("%Y-%m-%d")
    sets = sets or []
    if not stype:
        return {"error": "stype 必填: legs/push/pull/interval/lsd/relax"}

    def preview():
        p = {"date": d, "type": stype, "rpe": rpe, "sleep_h": sleep_h,
             "bodyweight": bodyweight, "notes": notes or ""}
        p["sets_summary"] = []
        missing = []
        for s in sets:
            ename = s.get("exercise", s.get("name", ""))
            matched, candidates = _match_exercise(ename)
            kgs = []
            groups = s.get("groups") or ([s] if "kg" in s else [])
            for g in groups:
                kgs.append("%skg×%s" % (g.get('kg', '?'), g.get('reps', '?')))
            item = {"exercise": matched or ename, "groups": kgs, "exists_in_db": bool(matched)}
            if candidates:
                item["candidates"] = candidates
            p["sets_summary"].append(item)
            if not matched:
                sug_pattern, sug_eq = _infer_exercise_meta(ename)
                missing.append({"name": ename, "suggested_pattern": sug_pattern,
                                "suggested_equipment": sug_eq})
        if missing:
            p["missing_exercises"] = missing
        if cardio and cardio.get("kind"):
            p["cardio_summary"] = {"kind": cardio["kind"], "distance_km": cardio.get("distance_km")}
        return p

    def execute():
        cid = None
        cycle = get_latest_cycle()
        if cycle and cycle['start_date'] <= d <= cycle['end_date']:
            cid = cycle['id']
        sid = upsert_session(d, cid, None, None, stype,
                             sleep_h=sleep_h, bodyweight=bodyweight, rpe=rpe,
                             status="done", notes=notes or "")
        ex_map = exercises_map()
        logged, skipped = [], []
        payload = []
        for s in sets:
            ename = s.get("exercise", s.get("name", ""))
            matched, _ = _match_exercise(ename)
            if not matched:
                skipped.append(ename)
                continue
            groups = s.get("groups") or ([s] if "kg" in s else [])
            if not groups:
                continue
            payload.append({"exercise_id": ex_map[matched], "groups": groups})
            for g in groups:
                logged.append({"exercise": matched, "kg": g.get("kg"), "reps": g.get("reps")})
        if payload:
            merge_session_sets(sid, payload)
        if cardio and cardio.get("kind") and (cardio.get("distance_km") or cardio.get("actual")):
            upsert_cardio(sid, cardio["kind"],
                          planned_json=json.dumps(cardio.get("planned", {}), ensure_ascii=False) if cardio.get("planned") else None,
                          actual_json=json.dumps(cardio.get("actual", {}), ensure_ascii=False) if cardio.get("actual") else None,
                          distance_km=cardio.get("distance_km"),
                          avg_hr=cardio.get("avg_hr"), avg_pace_sec=cardio.get("avg_pace_sec"))
        result = {"session_id": sid, "date": d, "type": stype, "logged_sets": logged}
        if skipped:
            result["skipped_unknown_exercises"] = skipped
        return result

    return _preview_or_execute(confirmed, preview, execute)


def tool_update_session(session_id, changes=None, confirmed=False, **kwargs):
    """修改已记录的训练课字段(rpe/notes/sleep_h/date/type)"""
    changes = changes or {}

    def preview():
        old = get_session_detail(int(session_id))
        if not old:
            return {"error": "session %s 不存在" % session_id}
        old.pop('sets', None); old.pop('cardio', None)
        return {"session_id": session_id, "old": old, "changes": changes}

    def execute():
        conn = get_db()
        sid = int(session_id)
        allowed = {"date": "date", "type": "type", "notes": "notes"}
        numeric = {"rpe": float, "sleep_h": float, "bodyweight": float}
        for k, v in changes.items():
            if k in allowed:
                conn.execute("UPDATE sessions SET %s=? WHERE id=?" % allowed[k], [v, sid])
            elif k in numeric:
                conn.execute("UPDATE sessions SET %s=? WHERE id=?" % k, [numeric[k](v), sid])
        conn.commit()
        conn.close()
        return {"session_id": session_id, "updated": list(changes.keys())}

    return _preview_or_execute(confirmed, preview, execute)


def tool_delete_data(target, target_id, confirmed=False, **kwargs):
    """删除训练数据(session/set/cardio/exercise)"""
    valid = {"session", "set", "cardio", "exercise"}
    if target not in valid:
        return {"error": "不可删除: %s，可选: %s" % (target, valid)}

    def preview():
        conn = get_db()
        table = {"session": "sessions", "set": "sets", "cardio": "cardio", "exercise": "exercises"}[target]
        row = conn.execute("SELECT * FROM %s WHERE id=?" % table, [target_id]).fetchone()
        conn.close()
        if not row:
            return {"error": "%s %s 不存在" % (target, target_id)}
        return {"target": target, "target_id": target_id, "data": dict(row)}

    def execute():
        conn = get_db()
        table = {"session": "sessions", "set": "sets", "cardio": "cardio", "exercise": "exercises"}[target]
        conn.execute("DELETE FROM %s WHERE id=?" % table, [target_id])
        conn.commit()
        conn.close()
        return {"deleted": target, "id": target_id}

    return _preview_or_execute(confirmed, preview, execute)


def tool_create_plan(start_date, weeks=4, template="standard", goals=None,
                     confirmed=False, **kwargs):
    """生成训练周期计划"""
    from planner import build_cycle_template
    goals = goals or []

    def preview():
        days = build_cycle_template(start_date, int(weeks), template)
        by_week = {}
        for dd in days:
            by_week.setdefault(dd.get('week_no', 1), []).append(
                {"date": dd['date'], "type": dd['type']})
        return {"start_date": start_date, "weeks": weeks, "template": template,
                "goals": goals, "schedule": by_week}

    def execute():
        from db import insert_session, insert_cycle
        days = build_cycle_template(start_date, int(weeks), template)
        if not days:
            return {"error": "生成计划为空"}
        end_date = days[-1]['date']
        cid = insert_cycle(start_date, end_date, template,
                           json.dumps(goals, ensure_ascii=False), '')
        ex_map = exercises_map()
        for dd in days:
            sid = insert_session(dd['date'], cid, dd.get('week_no'), dd.get('day_no'),
                                 dd['type'], status='planned')
            if dd['type'] in ('push', 'pull', 'legs'):
                _create_stock_sets(sid, dd['type'], ex_map)
        return {"cycle_id": cid, "start_date": start_date, "end_date": end_date,
                "days_count": len(days)}

    return _preview_or_execute(confirmed, preview, execute)


def _create_stock_sets(session_id, session_type, ex_map):
    stock = {
        "push": ["杠铃卧推", "哑铃推举", "哑铃飞鸟", "三头臂屈伸"],
        "pull": ["传统硬拉", "杠铃划船", "引体向上", "哑铃弯举"],
        "legs": ["杠铃深蹲", "罗马尼亚硬拉", "保加利亚分腿蹲", "腿弯举"],
    }
    conn = get_db()
    for i, ename in enumerate(stock.get(session_type, []), 1):
        eid = ex_map.get(ename)
        if not eid:
            continue
        conn.execute(
            "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
            [session_id, eid, i, None, None])
    conn.commit()
    conn.close()


def tool_adjust_plan(date=None, changes=None, confirmed=False, **kwargs):
    """调整某天计划的动作组"""
    d = date or kwargs.get("date_")
    changes = changes or []

    def preview():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if not row:
            conn.close()
            return {"error": "%s 没有可调整的计划" % d}
        current = [dict(r) for r in conn.execute("""
            SELECT e.name, st.planned_kg, st.planned_reps FROM sets st
            JOIN exercises e ON st.exercise_id=e.id WHERE st.session_id=?""", [row['id']]).fetchall()]
        conn.close()
        return {"date": d, "current": current, "new": changes}

    def execute():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if not row:
            conn.close()
            return {"error": "%s 没有可调整的计划" % d}
        sid = row['id']
        ex_map = exercises_map()
        adjusted = []
        for ch in changes:
            matched, _ = _match_exercise(ch.get("exercise", ""))
            if not matched:
                continue
            eid = ex_map[matched]
            conn.execute("DELETE FROM sets WHERE session_id=? AND exercise_id=?", [sid, eid])
            for i, g in enumerate(ch.get("groups", [ch]), 1):
                conn.execute(
                    "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
                    [sid, eid, i, g.get("kg"), g.get("reps")])
                adjusted.append({"exercise": matched, "kg": g.get("kg"), "reps": g.get("reps")})
        conn.commit()
        conn.close()
        return {"date": d, "adjusted": adjusted}

    return _preview_or_execute(confirmed, preview, execute)


def tool_log_body_metric(date=None, weight=None, sleep_h=None, resting_hr=None,
                         notes=None, confirmed=False, **kwargs):
    """记录身体指标"""
    d = date or kwargs.get("date_") or datetime.now().strftime("%Y-%m-%d")

    def preview():
        return {"date": d, "weight": weight, "sleep_h": sleep_h,
                "resting_hr": resting_hr, "notes": notes}

    def execute():
        from db import insert_body_metric
        insert_body_metric(d, weight, sleep_h, resting_hr, notes or "")
        return {"date": d, "saved": True}

    return _preview_or_execute(confirmed, preview, execute)


def tool_manage_exercises(action, name, pattern=None, equipment=None, is_main=0,
                          notes=None, confirmed=False, **kwargs):
    """动作库管理: add/update"""
    if action not in ("add", "update"):
        return {"error": "action 必须是 add 或 update"}

    def preview():
        ex_map = exercises_map()
        existing = name in ex_map
        if action == "add" and existing:
            return {"error": "动作 '%s' 已存在" % name}
        if action == "update" and not existing:
            return {"error": "动作 '%s' 不存在" % name}
        return {"action": action, "name": name, "pattern": pattern, "equipment": equipment,
                "is_main": bool(is_main)}

    def execute():
        conn = get_db()
        if action == "add":
            conn.execute("INSERT INTO exercises(name,pattern,equipment,is_main,notes) VALUES(?,?,?,?,?)",
                         [name, pattern or "accessory", equipment, int(bool(is_main)), notes or ""])
        else:
            conn.execute("""UPDATE exercises SET pattern=COALESCE(?,pattern),
                            equipment=COALESCE(?,equipment), is_main=COALESCE(?,is_main),
                            notes=COALESCE(?,notes) WHERE name=?""",
                         [pattern, equipment, int(bool(is_main)) if is_main else None, notes, name])
        conn.commit()
        conn.close()
        return {"action": action, "name": name}

    return _preview_or_execute(confirmed, preview, execute)


# ============================================================
# OpenAI 工具 schema
# ============================================================

def _fn(name, desc, params=None, required=None):
    p = {"type": "object", "properties": params or {}}
    if required:
        p["required"] = required
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": p}}


def _str(desc, **kw):
    d = {"type": "string", "description": desc}; d.update(kw); return d


def _num(desc, **kw):
    d = {"type": "number", "description": desc}; d.update(kw); return d


_GROUPS = {"type": "array", "items": {"type": "object", "properties": {
    "kg": _num("重量kg"), "reps": _num("次数")}}}

TOOL_SCHEMAS = [
    _fn("get_today_context", "获取今天日期星期、当日训练计划与配重、近7天完成状态、最新身体指标。回答'今天练什么/今天状态'用它"),
    _fn("get_exercise_history", "查某动作训练历史与e1RM趋势(自动渲染曲线)",
        {"exercise": _str("动作名，如 杠铃卧推"), "days": _num("回看天数，默认90"), "limit": _num("最多组数，默认50")},
        ["exercise"]),
    _fn("get_sessions", "查询训练课列表",
        {"date_from": _str("起始日期YYYY-MM-DD"), "date_to": _str("结束日期YYYY-MM-DD"),
         "type": _str("legs/push/pull/interval/lsd/relax/rest"),
         "status": _str("planned/done/partial/skipped"), "limit": _num("默认50")}),
    _fn("get_analytics", "分析引擎。kind: e1rm(e1RM趋势) volume(周容量) acwr(负荷比) plateau(平台期) adherence(依从性) deload(减载信号) running(跑步配速)",
        {"kind": _str("分析类型", enum=list(ANALYTICS_KINDS)), "weeks": _num("回看周数，默认12")},
        ["kind"]),
    _fn("get_plan", "查周期计划日历+目标进度", {"cycle": _str("current或周期ID，默认current")}),
    _fn("get_body_metrics", "身体指标历史(体重/睡眠/晨脉)", {"days": _num("回看天数，默认90")}),
    _fn("search_exercises", "模糊搜索动作库。同一关键词无结果时不要重复搜索，直接告知用户并建议用 manage_exercises 新增",
        {"keyword": _str("关键词")}, ["keyword"]),
    _fn("web_search", "联网搜索(训练技术/营养/伤病康复/器材/时效性问题用)。返回标题/链接/摘要并自动在画布展示来源",
        {"query": _str("搜索关键词"), "max_results": _num("条数，默认5，最多8")}, ["query"]),
    _fn("web_fetch", "读取指定网页正文纯文本。web_search摘要不够时用；部分反爬站点会失败",
        {"url": _str("完整URL http/https"), "max_chars": _num("正文截取字数，默认4000")}, ["url"]),
    _fn("show_view", "让用户界面切换页面", {"page": _str("页面", enum=["coach", "dashboard", "trends", "review", "plan"])}, ["page"]),
    _fn("log_training", "记录一次训练(需确认)。sets每项:{exercise,groups:[{kg,reps}]}。预览会返回 missing_exercises(库外动作及建议pattern)，用户同意新增时同一轮并行调用 manage_exercises(add)+log_training，系统会一次确认全部执行",
        {"date": _str("日期YYYY-MM-DD，默认今天"), "stype": _str("类型: legs/push/pull/interval/lsd/relax"),
         "sets": {"type": "array", "description": "动作组列表",
                  "items": {"type": "object", "properties": {
                      "exercise": _str("动作名"), "groups": _GROUPS}}},
         "cardio": {"type": "object", "properties": {"kind": _str("interval/lsd"), "distance_km": _num("距离km"), "avg_hr": _num("平均心率"), "avg_pace_sec": _num("平均配速秒/km")}},
         "rpe": _num("整体RPE 1-10"), "sleep_h": _num("睡眠小时"), "notes": _str("备注"),
         "confirmed": {"type": "boolean", "description": "用户确认后为true"}},
        ["stype"]),
    _fn("update_session", "修改训练课字段(需确认)",
        {"session_id": _num("session ID"), "changes": {"type": "object", "description": "{rpe,sleep_h,notes,date,type}子集"},
         "confirmed": {"type": "boolean"}},
        ["session_id", "changes"]),
    _fn("delete_data", "删除数据(需确认)",
        {"target": _str("session/set/cardio/exercise"), "target_id": _num("记录ID"),
         "confirmed": {"type": "boolean"}}, ["target", "target_id"]),
    _fn("create_plan", "生成训练周期计划(需确认)",
        {"start_date": _str("开始日期YYYY-MM-DD"), "weeks": _num("周数，默认4"),
         "template": _str("模板，默认standard"),
         "goals": {"type": "array", "description": "目标列表", "items": {"type": "object", "properties": {
             "exercise": _str("动作"), "from": _num("当前kg"), "to": _num("目标kg"),
             "sets": _num("组数"), "reps": _num("次数")}}},
         "confirmed": {"type": "boolean"}}, ["start_date"]),
    _fn("adjust_plan", "调整某天计划的动作组(需确认)",
        {"date": _str("日期YYYY-MM-DD"), "changes": {"type": "array", "items": {"type": "object", "properties": {
            "exercise": _str("动作名"), "groups": _GROUPS}}},
         "confirmed": {"type": "boolean"}}, ["date", "changes"]),
    _fn("log_body_metric", "记录身体指标(需确认)",
        {"date": _str("日期YYYY-MM-DD"), "weight": _num("体重kg"), "sleep_h": _num("睡眠h"),
         "resting_hr": _num("晨脉bpm"), "confirmed": {"type": "boolean"}}, ["date"]),
    _fn("manage_exercises", "动作库管理(需确认)",
        {"action": _str("add或update"), "name": _str("动作名"), "pattern": _str("squat/hinge/push/pull/core/accessory/stretch/cardio"),
         "equipment": _str("barbell/dumbbell/machine/bodyweight/cable"),
         "is_main": _num("是否核心动作0/1"), "confirmed": {"type": "boolean"}},
        ["action", "name"]),
]


TOOL_MAP = {
    "get_today_context": tool_get_today_context,
    "get_exercise_history": tool_get_exercise_history,
    "get_sessions": tool_get_sessions,
    "get_analytics": tool_get_analytics,
    "get_plan": tool_get_plan,
    "get_body_metrics": tool_get_body_metrics,
    "search_exercises": tool_search_exercises,
    "web_search": tool_web_search,
    "web_fetch": tool_web_fetch,
    "show_view": tool_show_view,
    "log_training": tool_log_training,
    "update_session": tool_update_session,
    "delete_data": tool_delete_data,
    "create_plan": tool_create_plan,
    "adjust_plan": tool_adjust_plan,
    "log_body_metric": tool_log_body_metric,
    "manage_exercises": tool_manage_exercises,
}


def store_pending_batch_legacy(action_id, session_id, tool_name, args, tool_call_id, messages):
    """以旧格式(单 action, 无 actions_json)写入，仅用于兼容性测试"""
    conn = get_db()
    from datetime import datetime, timedelta
    now = datetime.now()
    conn.execute(
        """INSERT OR REPLACE INTO agent_pending_actions
           (action_id, session_id, messages_json, tool_name, args_json, tool_call_id, created_at, expires_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        [action_id, session_id, json.dumps(messages, ensure_ascii=False, default=str),
         tool_name, json.dumps(args, ensure_ascii=False, default=str), tool_call_id,
         now.isoformat(), (now + timedelta(minutes=PENDING_EXPIRE_MINUTES)).isoformat()])
    conn.commit()
    conn.close()
