"""种子数据迁移: 将现有 fitness-plan.html 中的全部数据导入数据库"""
from db import (init_db, insert_exercise, insert_cycle, insert_session,
                 insert_set, insert_cardio, exercises_map, get_db)
import json

def seed_all():
    init_db()
    
    # ── 0. 档案 ──
    from db import insert_body_metric
    conn = get_db()
    profile_data = {
        "height": "181", "weight": "76", "bmi": "23.2",
        "training_years": "1", "half_marathon": "2:21",
        "dumbbell_weights": "[3,5,7.5,10,12.5]",
        "barbell_plate_weights": "[2.5,5,10,20]"
    }
    for k, v in profile_data.items():
        conn.execute("INSERT OR REPLACE INTO profile(key,value) VALUES(?,?)", [k, v])
    conn.commit()
    conn.close()
    
    # ── 1. 动作库 ──
    exercises = [
        # 主力量 (三大项)
        ("杠铃卧推", "push", "barbell", 1),
        ("杠铃深蹲", "squat", "barbell", 1),
        ("传统硬拉", "hinge", "barbell", 1),
        # 辅助: 推
        ("哑铃上斜卧推", "push", "dumbbell", 0),
        ("坐姿哑铃推举", "push", "dumbbell", 0),
        ("绳索下压/臂屈伸", "push", "cable", 0),
        ("侧平举", "push", "dumbbell", 0),
        # 辅助: 拉
        ("引体/高位下拉", "pull", "bodyweight", 0),
        ("杠铃划船", "pull", "barbell", 0),
        ("坐姿绳索划船", "pull", "cable", 0),
        ("哑铃弯举", "pull", "dumbbell", 0),
        # 辅助: 核心
        ("悬垂举腿/卷腹", "core", "bodyweight", 0),
        # 下肢
        ("保加利亚分腿蹲", "squat", "dumbbell", 0),
        ("腿弯举", "hinge", "machine", 0),
        ("提踵", "accessory", "bodyweight", 0),
        # 额外 (W1 实际出现的)
        ("倒蹬", "squat", "machine", 0),
        ("山羊挺身", "hinge", "machine", 0),
        ("卷腹", "core", "dumbbell", 0),
    ]
    for name, pattern, equip, is_main in exercises:
        insert_exercise(name, pattern, equip, is_main)
    
    ex = exercises_map()
    
    # ── 2. 周期 ──
    goals = json.dumps([
        {"exercise": "杠铃卧推", "from": 35, "to": 40, "sets": 5, "reps": 5},
        {"exercise": "杠铃深蹲", "from": 60, "to": 65, "sets": 5, "reps": 5},
        {"exercise": "传统硬拉", "from": 35, "to": 40, "sets": 3, "reps": 5},
    ])
    cycle_id = None
    conn = get_db()
    existing = conn.execute("SELECT id FROM cycles WHERE start_date='2026-07-13'").fetchone()
    conn.close()
    if existing:
        cycle_id = existing['id']
        print(f"ℹ️  复用已有周期 cycle_id={cycle_id}")
    else:
        cycle_id = insert_cycle("2026-07-13", "2026-07-31", "adaptation", goals, "七月训练周期: W1适应/W2增荷/W3验收")
    
    # ── 3. 训练日志 ──
    
    # W1 ─── Day1: 7/13 Mon relax (已完成，放松无运动组)
    insert_session("2026-07-13", cycle_id, 1, 1, "relax", sleep_h=None, bodyweight=None, rpe=3, status="done",
                   notes="瑜伽流15min·泡沫轴15min·关节灵活10min·呼吸收尾10min")
    
    # Day2: 7/14 Tue interval 400m×4 @5:30
    sid = insert_session("2026-07-14", cycle_id, 1, 2, "interval", sleep_h=None, bodyweight=None, rpe=7, status="done",
                         notes="W1间歇跑: 仅完成4/6组, 配速准确")
    insert_cardio(sid, "interval",
                  planned_json='{"intervals":[{"m":400,"count":6,"pace_sec":330}],"warmup_km":1,"cooldown_km":1}',
                  actual_json='{"intervals":[{"m":400,"count":4,"pace_sec":330}],"warmup_km":1,"cooldown_km":1}',
                  distance_km=4, avg_hr=None, max_hr=None, duration_s=None, avg_pace_sec=330)

    # Day3: 7/15 Wed legs (strength)
    sid = insert_session("2026-07-15", cycle_id, 1, 3, "legs", sleep_h=None, bodyweight=None, rpe=8, status="done",
                         notes="深蹲超量(65×8), 硬拉未做⚠️, 提踵未做")
    leg_ex = {"杠铃深蹲": ex["杠铃深蹲"], "传统硬拉": ex["传统硬拉"],
              "保加利亚分腿蹲": ex["保加利亚分腿蹲"], "腿弯举": ex["腿弯举"], "提踵": ex["提踵"],
              "倒蹬": ex["倒蹬"], "山羊挺身": ex["山羊挺身"], "卷腹": ex["卷腹"]}
    # 计划组 (W1 原计划)
    for en, eid in [("杠铃深蹲", leg_ex["杠铃深蹲"])]:
        insert_set(sid, eid, 1, planned_kg=60, planned_reps=8, actual_kg=40, actual_reps=15, status="done")
        insert_set(sid, eid, 2, planned_kg=60, planned_reps=8, actual_kg=50, actual_reps=15, status="done")
        insert_set(sid, eid, 3, planned_kg=60, planned_reps=8, actual_kg=50, actual_reps=15, status="done")
        insert_set(sid, eid, 4, planned_kg=60, planned_reps=8, actual_kg=60, actual_reps=12, status="done")
        insert_set(sid, eid, 5, actual_kg=65, actual_reps=8, status="extra")
    insert_set(sid, leg_ex["传统硬拉"], 1, planned_kg=35, planned_reps=8, status="skipped")
    insert_set(sid, leg_ex["保加利亚分腿蹲"], 1, planned_kg=0, planned_reps=10, actual_kg=3, actual_reps=15, status="done")
    insert_set(sid, leg_ex["保加利亚分腿蹲"], 2, planned_kg=0, planned_reps=10, actual_kg=3, actual_reps=15, status="done")
    insert_set(sid, leg_ex["保加利亚分腿蹲"], 3, planned_kg=0, planned_reps=10, actual_kg=3, actual_reps=15, status="done")
    for i in range(1,5):
        insert_set(sid, leg_ex["腿弯举"], i, planned_kg=None, planned_reps=12, actual_kg=45, actual_reps=15, status="done")
    insert_set(sid, leg_ex["提踵"], 1, planned_kg=None, planned_reps=15, status="skipped")
    for i in range(1,5):
        insert_set(sid, leg_ex["倒蹬"], i, actual_kg=40, actual_reps=12, status="extra")
    for i in range(1,5):
        insert_set(sid, leg_ex["山羊挺身"], i, actual_kg=10, actual_reps=15, status="extra")
    for i in range(1,5):
        insert_set(sid, leg_ex["卷腹"], i, actual_kg=5, actual_reps=12, status="extra")

    # Day4: 7/16 Thu push (strength)
    sid = insert_session("2026-07-16", cycle_id, 1, 4, "push", sleep_h=None, bodyweight=None, rpe=7, status="done",
                         notes="卧推超量(40×6), 哑铃辅助项偏轻")
    push_ex = {"杠铃卧推": ex["杠铃卧推"], "哑铃上斜卧推": ex["哑铃上斜卧推"],
               "坐姿哑铃推举": ex["坐姿哑铃推举"], "绳索下压/臂屈伸": ex["绳索下压/臂屈伸"],
               "侧平举": ex["侧平举"]}
    for i, (kg, reps) in enumerate([(35,12),(35,12),(35,12),(40,6)], 1):
        insert_set(sid, push_ex["杠铃卧推"], i, planned_kg=35, planned_reps=8, actual_kg=kg, actual_reps=reps, status="done")
    for i, (kg, reps) in enumerate([(7.5,10),(7.5,10),(10,10),(10,10)], 1):
        insert_set(sid, push_ex["哑铃上斜卧推"], i, planned_kg=12, planned_reps=10, actual_kg=kg, actual_reps=reps, status="done")
    for i in range(1,5):
        insert_set(sid, push_ex["坐姿哑铃推举"], i, planned_kg=10, planned_reps=10, actual_kg=5, actual_reps=10, status="done")
    for i in range(1,5):
        insert_set(sid, push_ex["绳索下压/臂屈伸"], i, planned_kg=None, planned_reps=12, actual_kg=None, actual_reps=12, status="done")
    for i in range(1,4):
        insert_set(sid, push_ex["侧平举"], i, planned_kg=6, planned_reps=15, actual_kg=5, actual_reps=12, status="done")

    # Day5: 7/17 Fri rest
    insert_session("2026-07-17", cycle_id, 1, 5, "rest", sleep_h=None, bodyweight=None, rpe=1, status="rest",
                   notes="被动恢复·为LSD蓄力")

    # Day6: 7/18 Sat lsd (推迟至周日)
    sid = insert_session("2026-07-18", cycle_id, 1, 6, "lsd", sleep_h=None, bodyweight=None, rpe=None, status="partial",
                         notes="周六改为被动休息, LSD推迟至周日")
    insert_cardio(sid, "lsd", planned_json='{"km":10,"pace_sec_min":390,"pace_sec_max":420,"max_hr":150}',
                  actual_json='{"km":10,"note":"推迟至周日完成"}')

    # Day7: 7/19 Sun pull + lsd (同一天力量+耐力)
    sid = insert_session("2026-07-19", cycle_id, 1, 7, "pull", sleep_h=None, bodyweight=None, rpe=8, status="done",
                         notes="同日完成拉类训练+10km LSD双倍任务")
    pull_ex = {"引体/高位下拉": ex["引体/高位下拉"], "杠铃划船": ex["杠铃划船"],
               "坐姿绳索划船": ex["坐姿绳索划船"], "哑铃弯举": ex["哑铃弯举"],
               "悬垂举腿/卷腹": ex["悬垂举腿/卷腹"]}
    for i in range(1,5):
        insert_set(sid, pull_ex["引体/高位下拉"], i, planned_kg=None, planned_reps=8, actual_kg=38.6, actual_reps=8, status="done",
                   notes="助力38.6kg")
    for i in range(1,5):
        insert_set(sid, pull_ex["杠铃划船"], i, planned_kg=35, planned_reps=10, actual_kg=40, actual_reps=10, status="done")
    for i in range(1,5):
        insert_set(sid, pull_ex["坐姿绳索划船"], i, planned_kg=None, planned_reps=10, actual_kg=27.2, actual_reps=12, status="done")
    for i in range(1,5):
        insert_set(sid, pull_ex["哑铃弯举"], i, planned_kg=8, planned_reps=12, actual_kg=11, actual_reps=8, status="done",
                   notes="杠铃弯举替代")
    for i in range(1,4):
        insert_set(sid, pull_ex["悬垂举腿/卷腹"], i, planned_kg=None, planned_reps=12, actual_kg=5, actual_reps=10, status="done",
                   notes="仅卷腹, 悬垂未做")
    insert_cardio(sid, "lsd", planned_json='{"km":10,"pace_sec_min":390,"pace_sec_max":420,"max_hr":150}',
                  actual_json='{"km":10,"note":"周日下午完成"}', distance_km=10)

    # ── W2 (已校准计划) ──
    w2_plan = [
        ("2026-07-20", 2, 1, "relax", "瑜伽流·泡沫轴·关节灵活·呼吸收尾"),
        ("2026-07-21", 2, 2, "interval", "400m×6 @5:20 组间90s"),
        ("2026-07-22", 2, 3, "legs", "硬拉首项35kg 4×6; 深蹲60kg 5×6 + 可选65×5"),
        ("2026-07-23", 2, 4, "push", "卧推35kg 5×6; 上斜10kg×3; 推举7.5kg; 侧平举5kg×15"),
        ("2026-07-24", 2, 5, "rest", "完全休息"),
        ("2026-07-25", 2, 6, "lsd", "10km 6:30-7:00 心率<150"),
        ("2026-07-26", 2, 7, "pull", "划船40kg 4×8; 引体减助力; 弯举10kg"),
    ]
    for date, wk, day, stype, notes in w2_plan:
        sid = insert_session(date, cycle_id, wk, day, stype, status="planned", notes=notes)
        if stype == "interval":
            insert_cardio(sid, "interval",
                          planned_json='{"intervals":[{"m":400,"count":6,"pace_sec":320}],"warmup_km":1,"cooldown_km":1}')
        elif stype == "lsd":
            insert_cardio(sid, "lsd",
                          planned_json='{"km":10,"pace_sec_min":390,"pace_sec_max":420,"max_hr":150}')

    # W2 力量课 planned sets
    # Legs 7/22: 硬拉 first
    sid_legs2 = None
    for date, wk, day, stype, _ in w2_plan:
        if stype == "legs": sid_legs2 = get_session_id(date)
    if sid_legs2:
        for i in range(1,5):
            insert_set(sid_legs2, ex["传统硬拉"], i, planned_kg=35, planned_reps=6)
        for i in range(1,6):
            insert_set(sid_legs2, ex["杠铃深蹲"], i, planned_kg=60, planned_reps=6)
        insert_set(sid_legs2, ex["杠铃深蹲"], 6, planned_kg=65, planned_reps=5, notes="可选顶组")
        for i in range(1,4):
            insert_set(sid_legs2, ex["保加利亚分腿蹲"], i, planned_kg=5, planned_reps=10)
        for i in range(1,4):
            insert_set(sid_legs2, ex["腿弯举"], i, planned_kg=45, planned_reps=12)
        for i in range(1,5):
            insert_set(sid_legs2, ex["提踵"], i, planned_reps=15)
    # Push 7/23
    sid_push2 = None
    for date, wk, day, stype, _ in w2_plan:
        if stype == "push": sid_push2 = get_session_id(date)
    if sid_push2:
        for i in range(1,6):
            insert_set(sid_push2, ex["杠铃卧推"], i, planned_kg=35, planned_reps=6)
        insert_set(sid_push2, ex["杠铃卧推"], 6, planned_kg=40, planned_reps=5, notes="可选顶组")
        for i in range(1,4):
            insert_set(sid_push2, ex["哑铃上斜卧推"], i, planned_kg=10, planned_reps=10)
        for i in range(1,4):
            insert_set(sid_push2, ex["坐姿哑铃推举"], i, planned_kg=7.5, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_push2, ex["绳索下压/臂屈伸"], i, planned_reps=12)
        for i in range(1,4):
            insert_set(sid_push2, ex["侧平举"], i, planned_kg=5, planned_reps=15)
    # Pull 7/26
    sid_pull2 = None
    for date, wk, day, stype, _ in w2_plan:
        if stype == "pull": sid_pull2 = get_session_id(date)
    if sid_pull2:
        for i in range(1,5):
            insert_set(sid_pull2, ex["引体/高位下拉"], i, planned_reps=6, notes="减助力")
        for i in range(1,5):
            insert_set(sid_pull2, ex["杠铃划船"], i, planned_kg=40, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_pull2, ex["坐姿绳索划船"], i, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_pull2, ex["哑铃弯举"], i, planned_kg=10, planned_reps=10)
        for i in range(1,4):
            insert_set(sid_pull2, ex["悬垂举腿/卷腹"], i, planned_reps=12)

    # ── W3 (验收期，全计划) ──
    w3_plan = [
        ("2026-07-27", 3, 1, "relax", "验收周放松: 瑜伽·泡沫轴·关节灵活"),
        ("2026-07-28", 3, 2, "legs", "深蹲65kg 5×5; 硬拉40kg 3×5 ★验收"),
        ("2026-07-29", 3, 3, "push", "卧推40kg 5×5; 上斜16kg; 推举14kg ★验收"),
        ("2026-07-30", 3, 4, "rest", "上下肢验收间缓冲"),
        ("2026-07-31", 3, 5, "pull", "划船40kg 4×6; 弯举10kg ★收官"),
    ]
    for date, wk, day, stype, notes in w3_plan:
        insert_session(date, cycle_id, wk, day, stype, status="planned", notes=notes)
    
    # W3 力量 planned sets
    sid_legs3 = get_session_id("2026-07-28")
    if sid_legs3:
        for i in range(1,6):
            insert_set(sid_legs3, ex["杠铃深蹲"], i, planned_kg=65, planned_reps=5, notes="验收")
        for i in range(1,4):
            insert_set(sid_legs3, ex["传统硬拉"], i, planned_kg=40, planned_reps=5, notes="验收")
        for i in range(1,4):
            insert_set(sid_legs3, ex["保加利亚分腿蹲"], i, planned_kg=12, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_legs3, ex["腿弯举"], i, planned_reps=10)
        for i in range(1,5):
            insert_set(sid_legs3, ex["提踵"], i, planned_reps=15)
    sid_push3 = get_session_id("2026-07-29")
    if sid_push3:
        for i in range(1,6):
            insert_set(sid_push3, ex["杠铃卧推"], i, planned_kg=40, planned_reps=5, notes="验收")
        for i in range(1,4):
            insert_set(sid_push3, ex["哑铃上斜卧推"], i, planned_kg=16, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_push3, ex["坐姿哑铃推举"], i, planned_kg=14, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_push3, ex["绳索下压/臂屈伸"], i, planned_reps=12)
        for i in range(1,4):
            insert_set(sid_push3, ex["侧平举"], i, planned_kg=8, planned_reps=12)
    sid_pull3 = get_session_id("2026-07-31")
    if sid_pull3:
        for i in range(1,5):
            insert_set(sid_pull3, ex["引体/高位下拉"], i, planned_reps=6)
        for i in range(1,5):
            insert_set(sid_pull3, ex["杠铃划船"], i, planned_kg=40, planned_reps=6, notes="验收")
        for i in range(1,4):
            insert_set(sid_pull3, ex["坐姿绳索划船"], i, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_pull3, ex["哑铃弯举"], i, planned_kg=10, planned_reps=8)
        for i in range(1,4):
            insert_set(sid_pull3, ex["悬垂举腿/卷腹"], i, planned_reps=12)
    
    # ── 体重数据 ──
    from db import insert_body_metric
    insert_body_metric("2026-07-13", weight=76.0, sleep_h=7.5, notes="周期开始")
    
    print(f"✅ 种子数据迁移完成: cycle_id={cycle_id}")


def get_session_id(date):
    conn = get_db()
    row = conn.execute("SELECT id FROM sessions WHERE date=? LIMIT 1", [date]).fetchone()
    conn.close()
    return row['id'] if row else None


if __name__ == '__main__':
    seed_all()
