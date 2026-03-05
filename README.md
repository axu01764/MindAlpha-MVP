# MindAlpha MVP - AGI 交易心理管家

## 项目现状
当前版本已打通完整 MVP 闭环：
- 自然语言策略录入 -> LLM 解析 JSON -> `StrategyRule` 入库
- 实时行情接入（Binance WebSocket）-> 前端缓存 K 线/订单簿
- Mock 下单 -> 规则拦截 -> 动态劝阻话术 -> `ActionLog` 入库
- 拦截后更新 `UserPsychProfile` -> 生成每日《知行合一报告》

## 目录结构
- `backend/`：FastAPI、SQLAlchemy、Alembic、拦截引擎
- `frontend/`：单页控制台（实时行情 + 策略录入 + 报告）
- `.github/workflows/ci.yml`：CI 自动检查与测试
- `docker-compose.yml`：本地一键容器化启动

## 环境变量
在 `backend/.env` 中配置：

```env
GEMINI_API_KEY=your_key
GEMINI_BASE_URL=https://grsaiapi.com/v1
GEMINI_MODEL=gemini-3.1-pro
GEMINI_TIMEOUT_SECONDS=30
```

`GEMINI_BASE_URL` 支持：
- `https://grsaiapi.com`
- `https://grsaiapi.com/v1`
- `https://grsaiapi.com/v1/chat/completions`

## 本地运行（开发模式）
后端：
```bash
cd backend
venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn main:app --reload
```

前端：
```bash
cd frontend
python -m http.server 5500
```

访问：
- 后端文档：`http://127.0.0.1:8000/docs`
- 前端页面：`http://127.0.0.1:5500/`

## Alembic 迁移
初始化后已生成首个 revision：`e6c527321e8d`。

常用命令：
```bash
cd backend
venv\Scripts\activate
alembic current
alembic heads
alembic revision --autogenerate -m "your message"
alembic upgrade head
```

## 容器化运行（Stage B）
```bash
docker compose up --build
```

访问：
- 前端：`http://127.0.0.1:5500/`
- 后端：`http://127.0.0.1:8000/docs`

## 测试与 CI
本地测试：
```bash
cd backend
venv\Scripts\activate
pytest -q
```

CI 已配置：
- 安装 `backend/requirements-dev.txt`
- 编译检查（`py_compile`）
- 执行 `pytest`

## 核心接口
- `POST /rules/parse`：解析并保存策略规则
- `GET /rules/{user_id}`：查询用户规则
- `POST /trade/mock-order`：模拟下单并触发拦截（携带实时行情快照）
- `GET /action-logs/{user_id}`：查询行为日志
- `GET /reports/daily/{user_id}`：生成今日知行合一报告

## 实时行情快照说明
前端会通过 Binance 公共流拉取：
- `trade`
- `depth20@100ms`
- `kline_1m`

触发 mock 下单时，前端把当前快照并入 `market_snapshot` 发送给后端。后端写入 `ActionLog.market_snapshot`，用于复盘“违规行为发生时的现场行情”。
