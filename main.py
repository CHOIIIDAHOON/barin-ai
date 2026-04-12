import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Literal

_APP_ROOT = Path(__file__).resolve().parent

logger = logging.getLogger("chatbot_api")

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from config import settings

app = FastAPI(title="Cursor Agent Chat API", version="1.0.0", redirect_slashes=False)


def _install_cors(application: FastAPI) -> None:
    """브라우저에서 Flutter 웹 → 원격 API 호출 시 CORS 없으면 ClientException: Failed to fetch."""
    if settings.cors_allow_all:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return

    extra = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    opts: dict = {
        "allow_origins": extra,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if settings.cors_enable_localhost_regex:
        opts["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    application.add_middleware(CORSMiddleware, **opts)


if not settings.use_nginx_cors:
    _install_cors(app)
security = HTTPBearer(auto_error=False)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatResponse(BaseModel):
    message: AssistantMessage
    debug: dict[str, str] | None = None


def messages_to_agent_prompt(messages: list[ChatMessage]) -> str:
    """Turn OpenAI-style messages into one instruction string for `cursor agent`."""
    blocks: list[str] = []
    for m in messages:
        label = {"system": "System", "user": "User", "assistant": "Assistant"}[m.role]
        blocks.append(f"## {label}\n{m.content.strip()}")
    return (
        "You are helping via the Cursor CLI agent. Follow the conversation below.\n\n"
        + "\n\n".join(blocks)
    )


def verify_bearer(
    creds: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
) -> None:
    secret = (settings.chat_api_secret or "").strip()
    if not secret:
        return
    if creds is None or creds.credentials != secret:
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization Bearer token")


def _cli_is_standalone_agent_binary(cli: str) -> bool:
    """~/.local/bin/agent 처럼 `agent` 단독 실행 파일이면 True (cursor agent 서브커맨드 아님)."""
    name = Path((cli or "").strip() or "cursor").name
    return name in ("agent", "agent.exe")


def build_agent_command(prompt: str) -> list[str]:
    cli = settings.cursor_cli_path.strip() or "cursor"
    # Non-interactive API: skip "Workspace Trust Required" prompt for cursor_project_dir.
    if _cli_is_standalone_agent_binary(cli):
        cmd: list[str] = [cli, "--trust"]
    else:
        cmd = [cli, "agent", "--trust"]
    model = settings.cursor_model.strip()
    if model:
        cmd.extend(["--model", model])
    mode = (settings.cursor_agent_mode or "").strip().lower()
    if mode in ("ask", "plan"):
        cmd.extend(["--mode", mode])
    cmd.extend(["-p", "--output-format", "text", prompt])
    return cmd


@app.get("/health")
def health() -> dict[str, str]:
    out: dict[str, str] = {
        "status": "ok",
        # 어떤 프로세스가 8000을 잡았는지 대조용 (비밀 아님)
        "app_dir": str(_APP_ROOT),
        "cursor_project_dir": settings.cursor_project_dir,
        "cursor_model": settings.cursor_model,
        "cursor_agent_mode": settings.cursor_agent_mode or "(default full agent)",
    }
    if settings.mock_agent:
        out["cursor_agent"] = "mock"
    return out


@app.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    _: Annotated[None, Depends(verify_bearer)],
    debug: bool = Query(False, description="Include stdout/stderr in debug field"),
) -> ChatResponse:
    if settings.mock_agent:
        logger.warning("POST /chat: MOCK_AGENT — Cursor CLI 호출 없음")
        last_user = next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            "",
        )
        content = (
            "[MOCK] Cursor CLI 없이 동작하는 테스트 응답입니다.\n\n"
            f"마지막 사용자 메시지:\n{last_user or '(없음)'}\n\n"
            "실제 에이전트를 쓰려면 .env에서 MOCK_AGENT=false 로 두세요."
        )
        dbg: dict[str, str] | None = {"mode": "mock_agent"} if debug else None
        return ChatResponse(message=AssistantMessage(content=content), debug=dbg)

    logger.info(
        "POST /chat: Cursor agent 시작 — 끝날 때까지 응답을 보내지 않음 (최대 %ss)",
        settings.agent_timeout_sec,
    )
    prompt = messages_to_agent_prompt(body.messages)
    project = settings.cursor_project_dir
    if not os.path.isdir(project):
        raise HTTPException(
            status_code=500,
            detail=f"cursor_project_dir is not a directory: {project}",
        )

    cmd = build_agent_command(prompt)
    env = os.environ.copy()

    # SSH 터미널에서 띄운 Uvicorn + Ctrl+C 등이 자식 agent 에 SIGINT 를 보내 130/-2 로 끊기는 것을 줄임.
    sub_kw: dict = {}
    if sys.platform != "win32":
        sub_kw["start_new_session"] = True

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **sub_kw,
        )
    except FileNotFoundError:
        logger.error("POST /chat: cursor CLI 없음 cmd[0]=%s", cmd[0] if cmd else "")
        raise HTTPException(
            status_code=500,
            detail=(
                "Cursor agent executable not found. Set CURSOR_CLI_PATH in .env to a full path "
                f"(e.g. `which cursor` or `which agent`). Config has: {settings.cursor_cli_path!r}."
            ),
        )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.agent_timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.error("POST /chat: agent 타임아웃 (%ss)", settings.agent_timeout_sec)
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="Cursor agent timed out")

    out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    err = (stderr_b or b"").decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        logger.error("POST /chat: agent 실패 returncode=%s", proc.returncode)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "cursor_agent_failed",
                "returncode": proc.returncode,
                "stderr": err[-8000:] if err else "",
                "stdout": out[-8000:] if out else "",
            },
        )

    logger.info("POST /chat: agent 완료, stdout 길이=%s", len(out))
    content = out if out else "(no output from agent)"
    dbg = None
    if debug:
        dbg = {"stdout": out, "stderr": err}
    return ChatResponse(message=AssistantMessage(content=content), debug=dbg)
