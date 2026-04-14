"""Pydantic models for chat API."""

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
    """Response for POST /warmup."""

    status: Literal["ok"] = "ok"
    message: str = "Cursor agent is ready."


class ChatJobRequest(ChatRequest):
    """POST /chat/jobs — optional webhook on completion or failure."""

    webhook_url: Optional[str] = Field(
        default=None,
        description="If set, POST JSON payload when job finishes.",
    )


class ChatJobCreateResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"


JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class ChatJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: Optional[AssistantMessage] = None
    error: Optional[str] = None
    returncode: Optional[int] = None
    stderr_tail: Optional[str] = None
    webhook_delivered: Optional[bool] = None
    webhook_error: Optional[str] = None
