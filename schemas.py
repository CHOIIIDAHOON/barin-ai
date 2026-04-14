"""POST /chat 요청·응답용 Pydantic 모델."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_length=1)


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatResponse(BaseModel):
    message: AssistantMessage
    debug: Optional[Dict[str, str]] = None


class WarmupResponse(BaseModel):
    """POST /warmup — 첫 CLI 기동·인증을 미리 끌어올릴 때 쓰는 가벼운 확인 응답."""

    status: Literal["ok"] = "ok"
    message: str = "Cursor 에이전트가 응답했습니다."
