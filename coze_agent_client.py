from __future__ import annotations

import os
import time
from typing import Any


def agent_status() -> dict[str, Any]:
    return {
        "configured": bool(os.getenv("COZE_API_TOKEN", "").strip() and os.getenv("COZE_AGENT_BOT_ID", "").strip()),
        "bot_id": os.getenv("COZE_AGENT_BOT_ID", "").strip(),
        "api_base": os.getenv("COZE_API_BASE", "https://api.coze.cn").rstrip("/"),
    }


def run_agent_chat(*, user_id: str, question: str, parameters: dict[str, Any] | None = None,
                   conversation_id: str | None = None, timeout_seconds: int = 90) -> dict[str, Any]:
    token = os.getenv("COZE_API_TOKEN", "").strip()
    bot_id = os.getenv("COZE_AGENT_BOT_ID", "").strip()
    if not token or not bot_id:
        raise RuntimeError("未配置COZE_API_TOKEN或COZE_AGENT_BOT_ID")
    try:
        from cozepy import COZE_CN_BASE_URL, ChatEventType, Coze, Message, TokenAuth
    except ImportError as exc:
        raise RuntimeError("未安装cozepy，请重新部署并安装requirements.txt") from exc

    base_url = os.getenv("COZE_API_BASE", "").strip() or COZE_CN_BASE_URL
    client = Coze(auth=TokenAuth(token), base_url=base_url)
    started = time.perf_counter()
    answer_parts: list[str] = []
    reasoning_parts: list[str] = []
    final_conversation_id = conversation_id
    usage: dict[str, Any] = {}
    stream = client.chat.stream(
        bot_id=bot_id,
        user_id=user_id,
        additional_messages=[Message.build_user_question_text(question)],
        conversation_id=conversation_id,
        parameters=parameters or {},
    )
    for event in stream:
        if time.perf_counter() - started > timeout_seconds:
            raise RuntimeError(f"Coze Agent响应超过{timeout_seconds}秒")
        if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA and event.message:
            if getattr(event.message, "reasoning_content", None):
                reasoning_parts.append(event.message.reasoning_content)
            if getattr(event.message, "content", None):
                answer_parts.append(event.message.content)
            final_conversation_id = getattr(event.message, "conversation_id", None) or final_conversation_id
        elif event.event == ChatEventType.CONVERSATION_CHAT_COMPLETED and event.chat:
            final_conversation_id = getattr(event.chat, "conversation_id", None) or final_conversation_id
            chat_usage = getattr(event.chat, "usage", None)
            if chat_usage:
                for key in ("token_count", "input_count", "output_count"):
                    value = getattr(chat_usage, key, None)
                    if value is not None:
                        usage[key] = value
    return {
        "answer": "".join(answer_parts).strip(),
        "reasoning_summary": "".join(reasoning_parts).strip(),
        "conversation_id": final_conversation_id,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "usage": usage,
    }
