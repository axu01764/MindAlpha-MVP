import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

RULE_PARSE_SYSTEM_PROMPT = """
你是交易纪律系统的解析器。
把用户输入的交易纪律规则，转换成结构化 JSON。
输出必须是 JSON 对象，不要输出 Markdown，不要输出解释。

JSON 字段要求：
- rule_name: string，给规则起一个简短名字
- intent: string，规则意图（例如：防止追高、止损纪律）
- conditions: array，条件列表，每个条件包含 metric/operator/value
- action: string，触发后执行动作（例如：BLOCK_BUY、LOCK_ACCOUNT、WARN）
- timeframe: string，时间范围或场景（例如：早盘、全天）
- confidence: number，0 到 1 的置信度
""".strip()

INTERCEPT_COPY_SYSTEM_PROMPT = """
你是严厉但专业的交易心理教练。
你需要输出一段强干预文案，打断用户的冲动下单行为。
要求：
1) 使用中文，80~140字。
2) 直接称呼用户 ID（如果有）。
3) 尽量引用输入中的关键数据（如 RSI、过去7天冲动次数、累计亏损、纪律胜率）。
4) 语气强硬、有压迫感，但不要侮辱用户。
5) 只返回纯文本，不要 Markdown，不要项目符号。
""".strip()


def _chat_completions_endpoint(base_url: str) -> str:
    normalized = base_url.rstrip("/")

    if normalized.endswith("/chat/completions"):
        return normalized

    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"

    return f"{normalized}/v1/chat/completions"


def _load_llm_config() -> tuple[str, str, float, str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("缺少 GEMINI_API_KEY，请先配置环境变量或 .env")

    base_url = os.getenv("GEMINI_BASE_URL", "https://grsaiapi.com/v1")
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro")
    timeout = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))

    if "generativelanguage.googleapis.com" in base_url and api_key.startswith("sk-"):
        raise ValueError(
            "当前 key 不是 Google 官方 Gemini key。"
            "若你使用第三方 OpenAI 兼容网关，请把 GEMINI_BASE_URL 改成对应网关地址。"
        )

    endpoint = _chat_completions_endpoint(base_url)
    return api_key, endpoint, timeout, model


def _chat_payload(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    return {
        "model": model,
        "stream": False,
        "temperature": temperature,
        "messages": messages,
    }


def _extract_message_content(result: Dict[str, Any]) -> Any:
    choices = result.get("choices", [])
    if not choices:
        raise ValueError("Gemini 返回格式异常：缺少 choices")

    message = choices[0].get("message", {})
    content = message.get("content")
    if content is None:
        raise ValueError("Gemini 返回格式异常：缺少 message.content")

    return content


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        joined = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
        if joined.strip():
            return joined.strip()

    raise ValueError("Gemini 返回内容格式异常")


def _sanitize_intercept_copy(text: str) -> str:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.strip()

    # 兼容少量模型会输出“最终答案：”之类前缀
    cleaned = re.sub(r"^(最终答案|Final Answer)[:：]\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _extract_json(content: Any) -> Dict[str, Any]:
    text = _extract_text(content)

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("Gemini 返回不是有效 JSON")

    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Gemini 返回的 JSON 不是对象")

    return data


def parse_rule_with_gemini(raw_text: str) -> Dict[str, Any]:
    api_key, endpoint, timeout, model = _load_llm_config()

    payload = _chat_payload(
        messages=[
            {"role": "system", "content": RULE_PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        model=model,
        temperature=0.1,
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(endpoint, headers=headers, json=payload)

    if response.status_code >= 400:
        detail = response.text[:500]
        raise ValueError(f"Gemini API 错误 {response.status_code}: {detail}")

    result = response.json()
    content = _extract_message_content(result)
    return _extract_json(content)


async def generate_intercept_copy_with_gemini(context: Dict[str, Any]) -> str:
    api_key, endpoint, timeout, model = _load_llm_config()

    prompt = (
        "请根据以下 JSON 生成一段强干预交易文案。"
        "必须包含关键数据点，帮助用户立即停止冲动下单。\n\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
    )

    payload = _chat_payload(
        messages=[
            {"role": "system", "content": INTERCEPT_COPY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.5,
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=headers, json=payload)

    if response.status_code >= 400:
        detail = response.text[:500]
        raise ValueError(f"Gemini API 错误 {response.status_code}: {detail}")

    result = response.json()
    content = _extract_message_content(result)
    text = _extract_text(content)
    cleaned = _sanitize_intercept_copy(text.replace("\n", " "))
    if cleaned:
        return cleaned
    raise ValueError("Gemini 未返回可用干预文案")
