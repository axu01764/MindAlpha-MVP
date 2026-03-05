from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator


class RuleParseRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="用户唯一标识")
    raw_text: str = Field(..., min_length=1, description="自然语言规则")


class RuleParseResponse(BaseModel):
    id: int
    user_id: str
    raw_text: str
    parsed_json: Dict[str, Any]


class TradeMockOrderRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="用户唯一标识")
    symbol: str = Field(default="BTCUSDT", description="交易标的")
    action_type: str = Field(..., description="交易动作：BUY 或 SELL")
    price: float | None = Field(default=None, description="下单价格")
    loss_usd: float | None = Field(default=None, description="当前累计亏损（美元）")
    note: str | None = Field(default=None, description="备注信息")
    market_snapshot: Dict[str, Any] = Field(
        default_factory=dict, description="可选盘面快照"
    )

    @field_validator("action_type")
    @classmethod
    def normalize_action_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"BUY", "SELL"}:
            raise ValueError("action_type 必须是 BUY 或 SELL")
        return normalized


class InterceptSignal(BaseModel):
    level: str
    title: str
    message: str


class PsychProfileSnapshot(BaseModel):
    fomo_index: float
    discipline_score: float
    saved_capital: float


class TradeMockOrderResponse(BaseModel):
    accepted: bool
    blocked: bool
    reason: str
    matched_rule_id: int | None = None
    matched_rule_action: str | None = None
    action_log_id: int
    alert: InterceptSignal | None = None
    profile: PsychProfileSnapshot | None = None


class DailyReportResponse(BaseModel):
    user_id: str
    report_date: str
    total_actions: int
    total_violations: int
    blocked_count: int
    discipline_win_rate_today: float
    recovered_capital_today: float
    similar_fomo_count_7d: int
    current_fomo_index: float
    current_discipline_score: float
    saved_capital_total: float
    summary: str
