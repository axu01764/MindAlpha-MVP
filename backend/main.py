import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from llm_service import generate_intercept_copy_with_gemini, parse_rule_with_gemini
from models import ActionLog, Base, StrategyRule, UserPsychProfile
from schemas import (
    DailyReportResponse,
    InterceptSignal,
    PsychProfileSnapshot,
    RuleParseRequest,
    RuleParseResponse,
    TradeMockOrderRequest,
    TradeMockOrderResponse,
)

# 启动时自动建表
Base.metadata.create_all(bind=engine)

app = FastAPI(title="MindAlpha MVP API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INTERCEPT_ACTIONS = {
    "LOCK_ACCOUNT",
    "BLOCK_ORDER",
    "BLOCK_TRADE",
    "BLOCK_BUY",
    "BLOCK_SELL",
}

ACTION_PRIORITY = {
    "LOCK_ACCOUNT": 100,
    "BLOCK_ORDER": 90,
    "BLOCK_TRADE": 90,
    "BLOCK_BUY": 70,
    "BLOCK_SELL": 70,
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def _safe_json_loads(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return {}

    return {}


def _extract_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group(0))

    return None


def _normalize_operator(value: Any) -> str:
    raw = str(value or "==").strip().lower()

    mapping = {
        ">": ">",
        "gt": ">",
        "大于": ">",
        "超过": ">",
        ">=": ">=",
        "gte": ">=",
        "大于等于": ">=",
        "至少": ">=",
        "不低于": ">=",
        "<": "<",
        "lt": "<",
        "小于": "<",
        "<=": "<=",
        "lte": "<=",
        "小于等于": "<=",
        "不超过": "<=",
        "==": "==",
        "=": "==",
        "eq": "==",
        "等于": "==",
        "!=": "!=",
        "ne": "!=",
        "不等于": "!=",
        "contains": "contains",
        "includes": "contains",
        "包含": "contains",
    }

    return mapping.get(raw, "==")


def _lookup_snapshot(snapshot: Dict[str, Any], key: str) -> Any:
    if key in snapshot:
        return snapshot[key]

    normalized = _normalize_key(key)
    for snap_key, snap_value in snapshot.items():
        if isinstance(snap_key, str) and _normalize_key(snap_key) == normalized:
            return snap_value

    return None


def _flatten_market_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}

    nested = snapshot.get("market_snapshot")
    if isinstance(nested, dict):
        merged.update(nested)

    merged.update(snapshot)
    return merged


def _first_non_none(values: List[Any]) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _resolve_metric_value(
    metric: Any, payload: TradeMockOrderRequest, snapshot: Dict[str, Any]
) -> Any:
    if metric is None:
        return None

    metric_str = str(metric)
    metric_key = _normalize_key(metric_str)

    if metric_key in {
        "lossusd",
        "loss",
        "lossamount",
        "dailyloss",
        "todayloss",
        "currentloss",
        "亏损",
        "亏损金额",
        "浮亏",
        "单日亏损",
        "drawdownusd",
        "pnl",
        "pnlusd",
        "累计亏损",
    }:
        return _first_non_none(
            [
                payload.loss_usd,
                _lookup_snapshot(snapshot, "loss_usd"),
                _lookup_snapshot(snapshot, "loss"),
                _lookup_snapshot(snapshot, "pnl"),
            ]
        )

    if metric_key in {"actiontype", "action", "交易动作", "动作", "方向"}:
        return payload.action_type

    if metric_key in {"price", "currentprice", "现价", "价格"}:
        return _first_non_none([payload.price, _lookup_snapshot(snapshot, "price")])

    if metric_key in {"symbol", "标的", "交易对"}:
        return _first_non_none([payload.symbol, _lookup_snapshot(snapshot, "symbol")])

    if metric_key in {"changepercent", "涨幅", "涨跌幅", "pctchange", "涨跌"}:
        return _lookup_snapshot(snapshot, "change_percent")

    return _lookup_snapshot(snapshot, metric_str)


def _compare_values(left: Any, right: Any, operator: str) -> bool:
    if operator == "contains":
        if left is None or right is None:
            return False
        return str(right) in str(left)

    left_num = _extract_number(left)
    right_num = _extract_number(right)

    if operator in {">", ">=", "<", "<="}:
        if left_num is None or right_num is None:
            return False

        if operator == ">":
            return left_num > right_num
        if operator == ">=":
            return left_num >= right_num
        if operator == "<":
            return left_num < right_num
        return left_num <= right_num

    left_str = "" if left is None else str(left).strip().lower()
    right_str = "" if right is None else str(right).strip().lower()

    if operator == "!=":
        return left_str != right_str

    return left_str == right_str


def _rule_matches_conditions(
    rule_json: Dict[str, Any], payload: TradeMockOrderRequest, snapshot: Dict[str, Any]
) -> bool:
    conditions = rule_json.get("conditions", [])

    if isinstance(conditions, dict):
        conditions = [conditions]

    if not isinstance(conditions, list) or len(conditions) == 0:
        return True

    for condition in conditions:
        if not isinstance(condition, dict):
            return False

        metric = condition.get("metric")
        operator = _normalize_operator(condition.get("operator"))
        expected = condition.get("value")

        actual = _resolve_metric_value(metric, payload, snapshot)
        if not _compare_values(actual, expected, operator):
            return False

    return True


def _rule_blocks_action(rule_action: str, action_type: str) -> bool:
    if rule_action in {"LOCK_ACCOUNT", "BLOCK_ORDER", "BLOCK_TRADE"}:
        return True
    if rule_action == "BLOCK_BUY":
        return action_type == "BUY"
    if rule_action == "BLOCK_SELL":
        return action_type == "SELL"
    return False


def _find_matching_rule(
    payload: TradeMockOrderRequest, db: Session, snapshot: Dict[str, Any]
) -> tuple[StrategyRule | None, Dict[str, Any], str | None]:
    rows = (
        db.query(StrategyRule)
        .filter(StrategyRule.user_id == payload.user_id, StrategyRule.is_active.is_(True))
        .order_by(StrategyRule.created_at.desc())
        .all()
    )

    matches: List[tuple[int, StrategyRule, Dict[str, Any], str]] = []

    for row in rows:
        rule_json = _safe_json_loads(row.parsed_json)
        rule_action = str(rule_json.get("action", "")).strip().upper()

        if not rule_action and row.raw_text:
            raw_text_upper = row.raw_text.upper()
            if "LOCK_ACCOUNT" in raw_text_upper:
                rule_action = "LOCK_ACCOUNT"

        if rule_action not in INTERCEPT_ACTIONS:
            continue

        if not _rule_blocks_action(rule_action, payload.action_type):
            continue

        if _rule_matches_conditions(rule_json, payload, snapshot):
            priority = ACTION_PRIORITY.get(rule_action, 0)
            matches.append((priority, row, rule_json, rule_action))

    if matches:
        matches.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        _, row, rule_json, rule_action = matches[0]
        return row, rule_json, rule_action

    return None, {}, None


def _extract_loss(snapshot: Dict[str, Any]) -> float:
    merged = _flatten_market_snapshot(snapshot)

    value = _first_non_none(
        [
            _lookup_snapshot(merged, "loss_usd"),
            _lookup_snapshot(merged, "loss"),
            _lookup_snapshot(merged, "loss_amount"),
            _lookup_snapshot(merged, "daily_loss"),
            _lookup_snapshot(merged, "pnl"),
        ]
    )

    number = _extract_number(value)
    if number is None:
        return 0.0

    return abs(number)


def _extract_rsi(snapshot: Dict[str, Any]) -> float | None:
    merged = _flatten_market_snapshot(snapshot)
    value = _first_non_none(
        [
            _lookup_snapshot(merged, "RSI"),
            _lookup_snapshot(merged, "rsi"),
            _lookup_snapshot(merged, "rsi_value"),
        ]
    )
    return _extract_number(value)


def _collect_behavior_stats(
    db: Session, user_id: str, lookback_days: int = 7
) -> Dict[str, float | int]:
    since = datetime.utcnow() - timedelta(days=lookback_days)
    rows = (
        db.query(ActionLog)
        .filter(ActionLog.user_id == user_id, ActionLog.timestamp >= since)
        .order_by(ActionLog.timestamp.desc())
        .all()
    )

    total_actions = len(rows)
    violations = 0
    blocked = 0
    similar_fomo_count = 0
    blocked_loss_saved = 0.0

    for row in rows:
        snapshot = _safe_json_loads(row.market_snapshot)
        action_type = (row.action_type or "").upper()

        if row.is_violation:
            violations += 1

        if row.was_blocked:
            blocked += 1
            blocked_loss_saved += _extract_loss(snapshot)

        if row.is_violation and action_type == "BUY":
            similar_fomo_count += 1

    discipline_win_rate = 100.0
    if total_actions > 0:
        discipline_win_rate = ((total_actions - violations) / total_actions) * 100.0

    return {
        "total_actions": total_actions,
        "violations": violations,
        "blocked": blocked,
        "similar_fomo_count": similar_fomo_count,
        "blocked_loss_saved": round(blocked_loss_saved, 2),
        "discipline_win_rate": round(discipline_win_rate, 2),
    }


def _get_or_create_profile(db: Session, user_id: str) -> UserPsychProfile:
    profile = db.query(UserPsychProfile).filter(UserPsychProfile.user_id == user_id).first()
    if profile:
        return profile

    profile = UserPsychProfile(user_id=user_id)
    db.add(profile)
    db.flush()
    return profile


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def _update_psych_profile(
    profile: UserPsychProfile,
    blocked: bool,
    loss_usd: float,
    rsi: float | None,
) -> None:
    severity = 8.0 + min(loss_usd / 25.0, 20.0)
    if rsi and rsi > 70:
        severity += min((rsi - 70.0) * 1.2, 15.0)

    if blocked:
        profile.fomo_index = _clamp((profile.fomo_index * 0.75) + severity, 0.0, 100.0)
        profile.discipline_score = _clamp(
            profile.discipline_score - (3.5 + severity * 0.18), 0.0, 100.0
        )
        profile.saved_capital = round(profile.saved_capital + loss_usd, 2)
    else:
        profile.fomo_index = _clamp(profile.fomo_index - 1.5, 0.0, 100.0)
        profile.discipline_score = _clamp(profile.discipline_score + 0.8, 0.0, 100.0)

    profile.last_updated = datetime.utcnow()


def _fallback_coaching_message(
    payload: TradeMockOrderRequest,
    stats_7d: Dict[str, float | int],
    rsi: float | None,
) -> str:
    rsi_text = f"RSI 达到 {rsi:.0f}" if rsi is not None else "当前技术位已偏热"
    similar_count = int(stats_7d.get("similar_fomo_count", 0))
    blocked_loss_saved = float(stats_7d.get("blocked_loss_saved", 0.0))
    discipline_win_rate = float(stats_7d.get("discipline_win_rate", 100.0))

    return (
        f"{payload.user_id}，你正在 {rsi_text} 的阶段尝试 {payload.action_type}。"
        f"过去7天你已有 {similar_count} 次类似冲动，系统累计拦截潜在亏损 ${blocked_loss_saved:.0f}。"
        f"当前纪律胜率 {discipline_win_rate:.1f}% ，你确定还要给市场送钱吗？"
    )


def _build_alert(message: str) -> InterceptSignal:
    return InterceptSignal(level="critical", title="检测到FOMO情绪", message=message)


def _profile_snapshot(profile: UserPsychProfile) -> PsychProfileSnapshot:
    return PsychProfileSnapshot(
        fomo_index=round(profile.fomo_index, 2),
        discipline_score=round(profile.discipline_score, 2),
        saved_capital=round(profile.saved_capital, 2),
    )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/rules/parse", response_model=RuleParseResponse)
def parse_and_save_rule(payload: RuleParseRequest, db: Session = Depends(get_db)):
    try:
        parsed = parse_rule_with_gemini(payload.raw_text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini 解析失败: {exc}") from exc

    record = StrategyRule(
        user_id=payload.user_id,
        raw_text=payload.raw_text,
        parsed_json=json.dumps(parsed, ensure_ascii=False),
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    return RuleParseResponse(
        id=record.id,
        user_id=record.user_id,
        raw_text=record.raw_text,
        parsed_json=parsed,
    )


@app.get("/rules/{user_id}")
def list_rules(user_id: str, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = (
        db.query(StrategyRule)
        .filter(StrategyRule.user_id == user_id)
        .order_by(StrategyRule.created_at.desc())
        .all()
    )

    result: List[Dict[str, Any]] = []
    for row in rows:
        parsed_json = _safe_json_loads(row.parsed_json)

        result.append(
            {
                "id": row.id,
                "user_id": row.user_id,
                "raw_text": row.raw_text,
                "parsed_json": parsed_json,
                "is_active": row.is_active,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return result


@app.post("/trade/mock-order", response_model=TradeMockOrderResponse)
async def mock_order(payload: TradeMockOrderRequest, db: Session = Depends(get_db)):
    snapshot: Dict[str, Any] = dict(payload.market_snapshot or {})
    snapshot.setdefault("symbol", payload.symbol)

    if payload.price is not None:
        snapshot.setdefault("price", payload.price)

    if payload.loss_usd is not None:
        snapshot.setdefault("loss_usd", payload.loss_usd)

    matched_rule, matched_rule_json, matched_rule_action = _find_matching_rule(
        payload, db, snapshot
    )

    blocked = matched_rule is not None
    accepted = not blocked

    stats_7d = _collect_behavior_stats(db, payload.user_id, lookback_days=7)
    rsi = _extract_rsi(snapshot)
    loss_usd = payload.loss_usd if payload.loss_usd is not None else _extract_loss(snapshot)

    reason = "模拟下单通过，未触发风控规则。"
    alert: InterceptSignal | None = None

    if blocked:
        context = {
            "user_id": payload.user_id,
            "symbol": payload.symbol,
            "action_type": payload.action_type,
            "current_price": payload.price,
            "current_loss_usd": round(loss_usd, 2),
            "current_rsi": rsi,
            "matched_rule_action": matched_rule_action,
            "matched_rule_text": matched_rule.raw_text if matched_rule else None,
            "last_7d_similar_fomo_count": int(stats_7d.get("similar_fomo_count", 0)),
            "last_7d_blocked_loss_saved_usd": float(stats_7d.get("blocked_loss_saved", 0.0)),
            "last_7d_discipline_win_rate": float(stats_7d.get("discipline_win_rate", 100.0)),
            "note": payload.note,
        }

        try:
            reason = await generate_intercept_copy_with_gemini(context)
        except Exception:
            reason = _fallback_coaching_message(payload, stats_7d, rsi)

        alert = _build_alert(reason)

    profile = _get_or_create_profile(db, payload.user_id)
    _update_psych_profile(profile, blocked=blocked, loss_usd=loss_usd, rsi=rsi)

    log_snapshot = {
        "symbol": payload.symbol,
        "action_type": payload.action_type,
        "price": payload.price,
        "loss_usd": round(loss_usd, 2),
        "note": payload.note,
        "market_snapshot": snapshot,
        "decision": {
            "accepted": accepted,
            "blocked": blocked,
            "reason": reason,
        },
        "matched_rule": (
            {
                "id": matched_rule.id,
                "action": matched_rule_action,
                "raw_text": matched_rule.raw_text,
                "parsed_json": matched_rule_json,
            }
            if matched_rule
            else None
        ),
        "behavior_stats_7d": stats_7d,
    }

    action_log = ActionLog(
        user_id=payload.user_id,
        action_type=payload.action_type,
        market_snapshot=json.dumps(log_snapshot, ensure_ascii=False),
        is_violation=blocked,
        was_blocked=blocked,
    )

    db.add(action_log)
    db.commit()
    db.refresh(action_log)
    db.refresh(profile)

    return TradeMockOrderResponse(
        accepted=accepted,
        blocked=blocked,
        reason=reason,
        matched_rule_id=matched_rule.id if matched_rule else None,
        matched_rule_action=matched_rule_action,
        action_log_id=action_log.id,
        alert=alert,
        profile=_profile_snapshot(profile),
    )


@app.get("/action-logs/{user_id}")
def list_action_logs(user_id: str, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = (
        db.query(ActionLog)
        .filter(ActionLog.user_id == user_id)
        .order_by(ActionLog.timestamp.desc())
        .all()
    )

    result: List[Dict[str, Any]] = []
    for row in rows:
        snapshot = _safe_json_loads(row.market_snapshot)
        result.append(
            {
                "id": row.id,
                "user_id": row.user_id,
                "action_type": row.action_type,
                "market_snapshot": snapshot,
                "is_violation": row.is_violation,
                "was_blocked": row.was_blocked,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
        )

    return result


@app.get("/reports/daily/{user_id}", response_model=DailyReportResponse)
def daily_report(user_id: str, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    rows = (
        db.query(ActionLog)
        .filter(
            ActionLog.user_id == user_id,
            ActionLog.timestamp >= day_start,
            ActionLog.timestamp < day_end,
        )
        .order_by(ActionLog.timestamp.desc())
        .all()
    )

    total_actions = len(rows)
    total_violations = 0
    blocked_count = 0
    recovered_capital_today = 0.0

    for row in rows:
        if row.is_violation:
            total_violations += 1

        if row.was_blocked:
            blocked_count += 1
            snapshot = _safe_json_loads(row.market_snapshot)
            recovered_capital_today += _extract_loss(snapshot)

    discipline_win_rate_today = 100.0
    if total_actions > 0:
        discipline_win_rate_today = ((total_actions - total_violations) / total_actions) * 100.0

    stats_7d = _collect_behavior_stats(db, user_id, lookback_days=7)

    profile = _get_or_create_profile(db, user_id)

    summary = (
        f"今日成功拦截 {blocked_count} 次致命 FOMO，"
        f"变相为您挽回本金 ${recovered_capital_today:.0f}。"
        f"当前知行合一得分：{profile.discipline_score:.0f} 分。"
    )

    return DailyReportResponse(
        user_id=user_id,
        report_date=day_start.date().isoformat(),
        total_actions=total_actions,
        total_violations=total_violations,
        blocked_count=blocked_count,
        discipline_win_rate_today=round(discipline_win_rate_today, 2),
        recovered_capital_today=round(recovered_capital_today, 2),
        similar_fomo_count_7d=int(stats_7d.get("similar_fomo_count", 0)),
        current_fomo_index=round(profile.fomo_index, 2),
        current_discipline_score=round(profile.discipline_score, 2),
        saved_capital_total=round(profile.saved_capital, 2),
        summary=summary,
    )
