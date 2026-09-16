"""命令行工具: 训练日志录入 / 报告生成"""
import sys, json, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def cmd_log(args):
    """录入训练日志: python cli.py log <date> <type> [--rpe 7] [--sleep 7.5] [--weight 76]"""
    from db import exercises_map, get_db
    
    if len(args) < 2:
        print("用法: cli.py log <YYYY-MM-DD> <type> [options]")
        print("  type: legs/push/pull/interval/lsd/relax/rest")
        return
    
    date, stype = args[0], args[1]
    rpe = sleep_h = weight = None
    notes = ""
    i = 2
    while i < len(args):
        if args[i] == '--rpe' and i+1 < len(args): rpe = float(args[i+1]); i += 2
        elif args[i] == '--sleep' and i+1 < len(args): sleep_h = float(args[i+1]); i += 2
        elif args[i] == '--weight' and i+1 < len(args): weight = float(args[i+1]); i += 2
        elif args[i] == '--note' and i+1 < len(args): notes = args[i+1]; i += 2
        else: i += 1
    
    from db import upsert_session
    cycle = get_cycle_for_date(date)
    cid = cycle['id'] if cycle else None
    rid = upsert_session(date, cid, week_no=None, day_no=None, type_=stype, sleep_h=sleep_h, bodyweight=weight, rpe=rpe, status="done", notes=notes)
    print(f"✅ session #{rid} logged: {date} {stype} (rpe={rpe}, sleep={sleep_h}h, bw={weight}kg)")

def cmd_sets(args):
    """录入训练组: cli.py sets <session_id> <exercise> <kg> <reps> [rpe]"""
    from db import exercises_map, insert_set
    if len(args) < 4:
        print("用法: cli.py sets <session_id> <exercise名> <kg> <reps> [rpe]")
        return
    sid, ename = int(args[0]), args[1]
    kg, rep = float(args[2]), int(args[3])
    rpe = float(args[4]) if len(args) > 4 else None
    ex = exercises_map()
    if ename not in ex:
        print(f"❌ 动作 '{ename}' 不存在。可用: {list(ex.keys())}")
        return
    insert_set(sid, ex[ename], 1, planned_kg=kg, planned_reps=rep, actual_kg=kg, actual_reps=rep, rpe=rpe, status="done")
    print(f"✅ set logged: {ename} {kg}kg×{rep} rpe={rpe}")

def cmd_cardio(args):
    """录入跑步数据: cli.py cardio <session_id> <kind> <km> <pace_sec> [hr]"""
    from db import insert_cardio
    if len(args) < 4:
        print("用法: cli.py cardio <session_id> <interval|lsd> <km> <pace_sec> [avg_hr]")
        return
    sid, kind = int(args[0]), args[1]
    km, pace = float(args[2]), int(args[3])
    hr = int(args[4]) if len(args) > 4 else None
    actual = json.dumps({"km": km, "pace_sec": pace})
    insert_cardio(sid, kind, actual_json=actual, distance_km=km, avg_pace_sec=pace, avg_hr=hr)
    print(f"✅ cardio logged: {kind} {km}km @ {pace}s/km HR={hr}")

def cmd_review(args=None):
    """生成周度分析报告"""
    from analytics import weekly_summary
    s = weekly_summary()
    print("=" * 50)
    print("  周度训练分析报告")
    print("=" * 50)
    
    if s['e1rm']:
        print("--- e1RM 趋势 ---")
        for ex_name, entries in s['e1rm'].items():
            if entries:
                latest = entries[-1]
                print(f"  {ex_name}: {latest['e1rm']}kg ({latest['kg']}×{latest['reps']})")
    
    if s['acwr']:
        a = s['acwr']
        status_label = {'normal': '✓正常', 'high': '⚠偏高', 'low': '▼偏低', 'insufficient': '…数据不足'}[a['status']]
        ratio_str = f"{a['ratio']}" if a['ratio'] else 'N/A'
        print(f"\n--- ACWR ---")
        print(f"  急性7d: {a['acute_7d']:.0f}  慢性28d周均: {a['chronic_28d_avg']:.0f}  比值: {ratio_str} {status_label}")
    
    if s['triggers']:
        print("\n--- ⚠ 减载警示 ---")
        for t in s['triggers']:
            print(f"  [{t['type']}] {t['message']}")
    
    if s['adherence']:
        print("\n--- 跳过动作 ---")
        for name, count in s['adherence'].items():
            print(f"  {name}: {count}次")

def cmd_help(args=None):
    print("""fitness-tracker CLI:
  log <date> <type> [--rpe N] [--sleep H] [--weight KG]  录入训练日
  sets <session_id> <动作名> <kg> <reps> [rpe]            录入训练组
  cardio <session_id> <interval|lsd> <km> <pace_sec> [hr] 录入跑步
  review                                                    生成周度分析报告
  help                                                      帮助""")

def get_cycle_for_date(date):
    from db import get_db
    conn = get_db()
    row = conn.execute("SELECT * FROM cycles WHERE ? BETWEEN start_date AND end_date", [date]).fetchone()
    conn.close()
    return dict(row) if row else None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        cmd_help()
    else:
        cmd = sys.argv[1]
        a = sys.argv[2:]
        {'log': cmd_log, 'sets': cmd_sets, 'cardio': cmd_cardio, 'review': cmd_review, 'help': cmd_help}.get(cmd, cmd_help)(a)
