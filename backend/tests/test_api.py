from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

import main as app_main
from database import SessionLocal
from models import StrategyRule


def _seed_lock_rule(user_id: str) -> None:
    db = SessionLocal()
    try:
        rule = StrategyRule(
            user_id=user_id,
            raw_text="测试规则：亏损>=200锁仓",
            parsed_json=json.dumps(
                {
                    "rule_name": "测试锁仓",
                    "intent": "测试拦截链路",
                    "conditions": [
                        {"metric": "loss_usd", "operator": ">=", "value": 200},
                        {"metric": "action_type", "operator": "==", "value": "BUY"},
                    ],
                    "action": "LOCK_ACCOUNT",
                    "timeframe": "全天",
                    "confidence": 1.0,
                },
                ensure_ascii=False,
            ),
            is_active=True,
        )
        db.add(rule)
        db.commit()
    finally:
        db.close()


def test_health() -> None:
    client = TestClient(app_main.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_mock_order_blocks_and_returns_alert(monkeypatch) -> None:
    async def fake_copy(_context):
        return "测试拦截文案"

    monkeypatch.setattr(app_main, "generate_intercept_copy_with_gemini", fake_copy)

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    _seed_lock_rule(user_id)

    client = TestClient(app_main.app)
    resp = client.post(
        "/trade/mock-order",
        json={
            "user_id": user_id,
            "symbol": "BTCUSDT",
            "action_type": "BUY",
            "price": 50000,
            "loss_usd": 250,
            "note": "test",
            "market_snapshot": {"RSI": 80, "change_percent": -3.2},
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert body["accepted"] is False
    assert body["matched_rule_action"] == "LOCK_ACCOUNT"
    assert body["alert"]["message"] == "测试拦截文案"


def test_daily_report_returns_expected_shape(monkeypatch) -> None:
    async def fake_copy(_context):
        return "测试拦截文案"

    monkeypatch.setattr(app_main, "generate_intercept_copy_with_gemini", fake_copy)

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    _seed_lock_rule(user_id)

    client = TestClient(app_main.app)
    order_resp = client.post(
        "/trade/mock-order",
        json={
            "user_id": user_id,
            "symbol": "BTCUSDT",
            "action_type": "BUY",
            "price": 50000,
            "loss_usd": 250,
            "note": "test",
            "market_snapshot": {"RSI": 81, "change_percent": -2.8},
        },
    )
    assert order_resp.status_code == 200

    report_resp = client.get(f"/reports/daily/{user_id}")
    assert report_resp.status_code == 200

    body = report_resp.json()
    expected_keys = {
        "user_id",
        "report_date",
        "total_actions",
        "total_violations",
        "blocked_count",
        "discipline_win_rate_today",
        "recovered_capital_today",
        "similar_fomo_count_7d",
        "current_fomo_index",
        "current_discipline_score",
        "saved_capital_total",
        "summary",
    }
    assert expected_keys.issubset(body.keys())
    assert body["user_id"] == user_id
    assert body["blocked_count"] >= 1
