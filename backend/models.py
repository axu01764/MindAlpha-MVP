from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from datetime import datetime
from database import Base


class StrategyRule(Base):
    __tablename__ = "strategy_rules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)  # 区分不同用户
    raw_text = Column(String)  # 用户输入的自然语言，比如"跌破6万清仓"
    parsed_json = Column(String)  # 大模型解析后的执行逻辑 (Skills)
    is_active = Column(Boolean, default=True)  # 规则是否在生效中
    created_at = Column(DateTime, default=datetime.utcnow)


class ActionLog(Base):
    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    action_type = Column(String)  # "BUY" 或 "SELL"
    market_snapshot = Column(String)  # 当时的盘面快照 (比如: {"BTC": 65000, "RSI": 80})
    is_violation = Column(Boolean)  # 是否违背了上面的 StrategyRule
    was_blocked = Column(Boolean)  # 是否被成功物理拦截
    timestamp = Column(DateTime, default=datetime.utcnow)


class UserPsychProfile(Base):
    __tablename__ = "user_psych_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)
    fomo_index = Column(Float, default=0.0)  # 追高/恐慌指数 (0-100)
    discipline_score = Column(Float, default=100.0)  # 知行合一得分
    saved_capital = Column(Float, default=0.0)  # 核心爽点：帮用户挽回了多少钱
    last_updated = Column(DateTime, default=datetime.utcnow)
