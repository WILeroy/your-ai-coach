"""自动计划器: 渐进规则 / 周度校准 / 周期生成"""
from datetime import datetime, timedelta
import json

def double_progression_rule(sets_data, exercise_name, available_weights):
    """
    双倍渐进规则。
    sets_data: [{actual_kg, actual_reps, rpe, planned_kg, planned_reps}, ...]
    available_weights: [5, 7.5, 10, 12.5, ...] 哑铃可用重量列表
    返回: (next_kg, next_reps, suggestion)
    """
    if not sets_data:
        return None, None, '无历史数据，保持计划重量'
    
    completed = [s for s in sets_data if s.get('actual_kg') is not None]
    if not completed:
        return None, None, '全部跳过，建议重复上次计划'
    
    last_planned_kg = max((s.get('planned_kg', 0) for s in sets_data if s.get('planned_kg')), default=0)
    last_planned_reps = max((s.get('planned_reps', 0) for s in sets_data if s.get('planned_reps')), default=0)
    
    # 检查是否全部组完成预定次数
    all_hit_target = all(
        s.get('actual_reps', 0) >= s.get('planned_reps', 0) and s.get('actual_kg', 0) >= s.get('planned_kg', 0)
        for s in completed
    )
    
    # 末组 RPE 检查
    last_rpe = max((s.get('rpe', 5) for s in completed if s.get('rpe')), default=5)
    
    if all_hit_target and last_rpe <= 8:
        # 可以加重
        current_kg = max((s['actual_kg'] for s in completed))
        next_kg = get_next_weight(current_kg, available_weights)
        next_reps = last_planned_reps  # 维持次数
        return next_kg, next_reps, f'全部达标且 RPE≤8, {current_kg}→{next_kg}kg'
    elif not all_hit_target:
        incomplete_count = sum(1 for s in completed if s.get('actual_reps', 0) < s.get('planned_reps', 0))
        if incomplete_count / len(completed) > 0.5:
            next_kg = max((s['actual_kg'] for s in completed))
            next_kg_drop = max(next_kg * 0.9, 5)
            return round(next_kg_drop, 1), last_planned_reps, '过半组未达标，建议降重10%重建'
        else:
            return last_planned_kg, last_planned_reps, '部分未达标，建议保持重量再试一次'
    else:
        return last_planned_kg, last_planned_reps, '完成但RPE偏高，保持重量'

def get_next_weight(current_kg, available_weights):
    """找到可用配重中的下一档"""
    sorted_weights = sorted(available_weights)
    for w in sorted_weights:
        if w > current_kg:
            return w
    return current_kg + 2.5  # 兜底

def generate_weekly_calibration(cycle_plan, actual_sessions, e1rm_history, skipped_monitor):
    """
    生成周度校准报告。
    返回 markdown 格式字符串。
    """
    lines = []
    lines.append("## 本周训练校准报告")
    lines.append("")
    
    # 依从性
    completion_count = sum(1 for s in actual_sessions if s.get('status') in ('done', 'partial'))
    total = len(actual_sessions)
    lines.append(f"**出勤**: {completion_count}/{total} ({round(completion_count/total*100) if total else 0}%)")
    lines.append("")
    
    if skipped_monitor:
        lines.append("### 跳过动作监控")
        for name, count in skipped_monitor.items():
            lines.append(f"- **{name}**: 跳过 {count} 次 ⚠️")
        lines.append("")
    
    # e1RM 变化
    if e1rm_history:
        lines.append("### 三大项 e1RM 变化")
        for ex in ['杠铃卧推', '杠铃深蹲', '传统硬拉']:
            if ex in e1rm_history and e1rm_history[ex]:
                latest = e1rm_history[ex][-1]
                erm = latest['e1rm']
                lines.append(f"- {ex}: **{erm}kg** ({latest['kg']}×{latest['reps']})")
        lines.append("")
    
    # 下周建议
    lines.append("### 下周调整建议")
    lines.append("详见周期计划页面的自动校准数据。")
    lines.append("")
    
    return "\n".join(lines)

def build_cycle_template(start_date, weeks, template='standard'):
    """
    生成周期模板。
    template: 'standard' = 3周积累+1周验收 (符合现有风格)
    返回 cycle 结构及 days 列表。
    """
    days = []
    current = datetime.strptime(start_date, '%Y-%m-%d')
    week_pattern = [
        ('Mon', 'relax'), ('Tue', 'interval'), ('Wed', 'legs'),
        ('Thu', 'push'), ('Fri', 'rest'), ('Sat', 'lsd'), ('Sun', 'pull')
    ]
    verify_pattern = [
        ('Mon', 'relax'), ('Tue', 'legs'), ('Wed', 'push'),
        ('Thu', 'rest'), ('Fri', 'pull')
    ]
    
    for wk in range(weeks):
        weekday = current.weekday()
        if wk < weeks - 1:
            pattern = week_pattern
            phase = 'adaptation' if wk == 0 else 'load'
        else:
            pattern = verify_pattern
            phase = 'verification'
        
        for i in range(min(7, len(pattern))):
            day_name = pattern[i][0]
            target_weekday = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}[day_name]
            # 简单映射到实际日期
            day_offset = target_weekday - current.weekday()
            if day_offset < 0:
                day_offset += 7
            day_date = current + timedelta(days=day_offset)
            if wk == weeks - 1 and i >= len(verify_pattern):
                break
            days.append({
                'date': day_date.strftime('%Y-%m-%d'),
                'week_no': wk + 1,
                'day_no': (wk - 1) * 7 + i,
                'type': pattern[i][1],
                'phase': phase
            })
        current += timedelta(days=7)
    
    return days
