from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        value_dt = datetime.fromisoformat(text)
        if value_dt.tzinfo is None:
            value_dt = value_dt.replace(tzinfo=CN_TZ)
        return value_dt.astimezone(CN_TZ)
    except ValueError:
        return None


def next_workday_9(now: datetime) -> datetime:
    candidate = now + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.replace(hour=9, minute=0, second=0, microsecond=0)


def decide_task(task: dict[str, Any], current: datetime, current_user_id: str) -> dict[str, Any]:
    """Shared FT04 action-state and priority rule.

    Both the normal task workspace and the Agent order diagnosis should consume
    this function so waiting windows, deadlines and responsibility use one source
    of truth.
    """
    due = parse_dt(task.get("business_deadline"))
    promise = parse_dt(task.get("promised_reply_at"))
    planned = parse_dt(task.get("next_action_at"))
    risk = str(task.get("risk_level") or "none").lower()
    urgent = bool(task.get("urgent")) or risk == "critical"
    weekend = current.weekday() >= 5
    hard = False
    suppressed = False
    next_action = task.get("next_action_at")
    reasons: list[str] = []

    if str(task.get("status") or "").upper() == "DONE":
        state, action = "DONE", "无需处理"
        reasons.append("任务已完成")
    elif task.get("responsibility_status") == "not_mine" or (
        task.get("owner_user_id")
        and current_user_id
        and task.get("owner_user_id") != current_user_id
        and task.get("responsibility_status") == "assigned"
    ):
        state, action = "NOT_MY_RESPONSIBILITY", "转交给正确负责人并记录"
        reasons.append("不属于当前用户责任")
    elif bool(task.get("pending_confirmation")):
        state, action = "NEEDS_CONFIRMATION", "审核候选变化并确认是否生效"
        reasons.append("存在待确认候选或高责任字段")
    elif (risk == "critical" and not task.get("owner_user_id")) or (
        risk == "critical" and due and (current - due).total_seconds() >= 8 * 3600
    ):
        state, action, hard = "ESCALATE", "请求主管介入并明确负责人", True
        reasons.append("重大事项无负责人" if not task.get("owner_user_id") else "重大事项已严重逾期")
    elif task.get("waiting_on") and promise and promise > current:
        state = "WAITING_EXTERNAL"
        action = f"等待{task.get('waiting_on')}回复"
        next_action = task.get("promised_reply_at")
        suppressed = True
        reasons.append("已完成当前动作，仍在对方承诺回复窗口内")
    elif task.get("waiting_on") and promise and promise <= current:
        state, action, hard = "DO_NOW", f"再次跟进{task.get('waiting_on')}", True
        reasons.append("已超过对方承诺回复时间")
    elif planned and planned <= current:
        state, action, hard = "DO_NOW", task.get("recommended_action") or "立即处理计划事项", True
        reasons.append("已到计划处理时间")
    elif due and due <= current:
        state, action, hard = "DO_NOW", task.get("recommended_action") or "立即处理逾期事项", True
        reasons.append("业务截止时间已过")
    elif urgent:
        state, action, hard = "DO_NOW", task.get("recommended_action") or "立即人工处理紧急事项", True
        reasons.append("存在关键风险")
    elif planned and (planned - current).total_seconds() <= 12 * 3600:
        state, action = "DO_TODAY", task.get("recommended_action") or "今天完成"
        reasons.append("计划处理时间在今天")
    elif due and (due - current).total_seconds() <= 12 * 3600:
        state, action = "DO_TODAY", task.get("recommended_action") or "今天完成"
        reasons.append("任务将在今天到期")
    else:
        state, action = "SCHEDULED", task.get("recommended_action") or "按计划处理"
        next_action = next_action or task.get("business_deadline")
        reasons.append("尚未进入立即处理窗口")

    if weekend and not urgent and not hard and state in {"DO_NOW", "DO_TODAY"}:
        state, action = "SCHEDULED", "安排在下一个工作日处理"
        next_action = iso(next_workday_9(current))
        reasons.append("周末且非紧急，不制造全天候回复压力")

    state_weight = {
        "ESCALATE": 1000,
        "DO_NOW": 900,
        "NEEDS_CONFIRMATION": 800,
        "DO_TODAY": 700,
        "SCHEDULED": 300,
        "WAITING_EXTERNAL": 100,
        "NOT_MY_RESPONSIBILITY": 0,
        "DONE": -1000,
    }
    risk_weight = {"critical": 300, "high": 180, "medium": 80, "low": 20, "none": 0}
    score = state_weight[state] + risk_weight.get(risk, 0)
    if due:
        remaining = (due - current).total_seconds() / 3600
        if remaining <= 0:
            score += 200
        elif remaining <= 24:
            score += 100
    if suppressed:
        score = -1000

    result = dict(task)
    evidence_raw = task.get("evidence_json")
    if isinstance(evidence_raw, str):
        try:
            evidence = json.loads(evidence_raw or "[]")
        except json.JSONDecodeError:
            evidence = []
    else:
        evidence = task.get("evidence") or []
    result.update(
        {
            "action_state": state,
            "recommended_action": action,
            "next_action_at": next_action,
            "ranking_suppressed": suppressed,
            "priority_score": score,
            "priority_reasons": reasons,
            "evidence": evidence,
        }
    )
    return result
