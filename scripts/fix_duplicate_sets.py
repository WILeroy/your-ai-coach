#!/usr/bin/env python3
"""训练组数据去重/校验工具

场景: log_training 重复调用可能造成新动作(无计划行)组数据翻倍(v2.1 已通过
merge_session_sets 替换语义修复，本脚本用于校验与清理历史数据)。

用法:
    .venv/bin/python scripts/fix_duplicate_sets.py            # dry-run 只报告
    .venv/bin/python scripts/fix_duplicate_sets.py --execute  # 实际清理
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

# 目标组数(可选): {exercise_name: 期望最大 done 组数}
TARGETS = {}


def find_issues(conn):
    issues = []
    # 1. 结构性重复: 同 (session, exercise, set_no) 多行
    rows = conn.execute("""
        SELECT st.session_id, st.exercise_id, st.set_no, COUNT(*) c, GROUP_CONCAT(st.id) ids
        FROM sets st GROUP BY st.session_id, st.exercise_id, st.set_no HAVING c > 1
    """).fetchall()
    for r in rows:
        ids = sorted(int(x) for x in r['ids'].split(','))
        issues.append(("dup_set_no", r['session_id'], r['exercise_id'],
                       ids, f"set_no={r['set_no']} 有 {r['c']} 行, 保留 id={ids[0]}"))
    # 2. 目标组数超限(如指定)
    for name, target in TARGETS.items():
        erow = conn.execute("SELECT id FROM exercises WHERE name=?", [name]).fetchone()
        if not erow:
            continue
        rows = conn.execute("""
            SELECT st.session_id, COUNT(*) c, GROUP_CONCAT(st.id) ids FROM sets st
            WHERE st.exercise_id=? AND st.status IN ('done','extra')
            GROUP BY st.session_id HAVING c > ?
        """, [erow['id'], target]).fetchall()
        for r in rows:
            ids = sorted(int(x) for x in r['ids'].split(','))
            issues.append(("over_target", r['session_id'], erow['id'],
                           ids[target:], f"{name} {r['c']} 组 > 目标 {target}, 删除 {ids[target:]}"))
    return issues


def main():
    execute = "--execute" in sys.argv
    conn = get_db()
    issues = find_issues(conn)
    if not issues:
        # 附带: 会话61完整性校验
        s61 = conn.execute("""SELECT COUNT(*) FROM sets WHERE session_id=61 AND status IN ('done','extra')""").fetchone()[0]
        p61 = conn.execute("SELECT COUNT(*) FROM sets WHERE session_id=61 AND status='planned'").fetchone()[0]
        # 清理过期 pending
        from agent.tools import cleanup_expired
        cleanup_expired()
        print("✅ 未发现重复组数据")
        print(f"   session 61: {s61} done + {p61} planned" + (" (期望 27+9 ✓)" if (s61, p61) == (27, 9) else " ⚠️ 与期望 27+9 不符"))
        conn.close()
        return
    print(f"发现 {len(issues)} 处问题:")
    for kind, sid, eid, ids, desc in issues:
        print(f"  [{kind}] session={sid} exercise={eid}: {desc}")
    if not execute:
        print("\n(dry-run 未修改。加 --execute 执行清理)")
        conn.close()
        return
    for kind, sid, eid, ids, desc in issues:
        q = ",".join(str(i) for i in ids)
        conn.execute(f"DELETE FROM sets WHERE id IN ({q})")
    conn.commit()
    print("✅ 清理完成")
    conn.close()


if __name__ == "__main__":
    main()
