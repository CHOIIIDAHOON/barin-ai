import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Literal

_APP_ROOT = Path(__file__).resolve().parent

logger = logging.getLogger("chatbot_api")
_warned_missing_cursor_api_key = False

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
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


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "message": "요청 데이터 검증에 실패했습니다.",
            "detail": exc.errors(),
        },
    )


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
        raise HTTPException(
            status_code=401,
            detail="인증에 실패했습니다. Authorization Bearer 토큰이 없거나 올바르지 않습니다.",
        )


def _cli_is_standalone_agent_binary(cli: str) -> bool:
    """~/.local/bin/agent 처럼 `agent` 단독 실행 파일이면 True (cursor agent 서브커맨드 아님)."""
    name = Path((cli or "").strip() or "cursor").name
    return name in ("agent", "agent.exe")


async def _reap_agent_subprocess(
    proc: asyncio.subprocess.Process,
    comm_task: asyncio.Task,
    *,
    comm_wait_sec: float = 30.0,
    proc_wait_sec: float = 30.0,
) -> None:
    """에이전트 자식 프로세스와 communicate 태스크만 정리. wait_for로 communicate를 취소하지 않은 뒤 호출하는 것을 권장."""
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except PermissionError as e:
            logger.warning("에이전트 프로세스 종료(kill) 실패: %s", e)

    if not comm_task.done():
        try:
            await asyncio.wait_for(comm_task, timeout=comm_wait_sec)
        except asyncio.TimeoutError:
            logger.error(
                "에이전트 communicate 작업이 %ss 내에 끝나지 않아 태스크를 취소합니다",
                comm_wait_sec,
            )
            comm_task.cancel()
            try:
                await comm_task
            except asyncio.CancelledError:
                pass

    if proc.returncode is None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=proc_wait_sec)
        except asyncio.TimeoutError:
            logger.error(
                "kill 후에도 에이전트 하위 프로세스가 %ss 안에 종료되지 않았습니다 (PID가 멈춘 상태일 수 있음)",
                proc_wait_sec,
            )


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
    if settings.cursor_agent_force:
        cmd.append("--force")
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
        "cursor_agent_mode": settings.cursor_agent_mode or "(기본: 전체 에이전트)",
        "cursor_api_key_configured": "yes" if (settings.cursor_api_key or "").strip() else "no",
        "cursor_agent_force": "true" if settings.cursor_agent_force else "false",
    }
    if settings.mock_agent:
        out["cursor_agent"] = "mock"
    return out


@app.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    _: Annotated[None, Depends(verify_bearer)],
    debug: bool = Query(False, description="응답의 debug 필드에 stdout/stderr 포함 여부"),
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

    global _warned_missing_cursor_api_key
    logger.info(
        "POST /chat: Cursor agent 시작 — 끝날 때까지 응답을 보내지 않음 (최대 %ss)",
        settings.agent_timeout_sec,
    )
    if not (settings.cursor_api_key or "").strip() and not _warned_missing_cursor_api_key:
        _warned_missing_cursor_api_key = True
        logger.warning(
            "CURSOR_API_KEY가 설정되어 있지 않습니다. "
            "서버 무인 실행에서는 Cursor CLI가 인증 대기로 멈출 수 있습니다 — .env에 CURSOR_API_KEY를 넣거나 "
            "`cursor agent login`으로 해당 사용자 홈에 자격 증명을 저장하세요. "
            "승인 대기로 멈추면 CURSOR_AGENT_FORCE=true 검토(파일/쉘 실행 허용)."
        )
    prompt = messages_to_agent_prompt(body.messages)
    project = settings.cursor_project_dir
    if not os.path.isdir(project):
        raise HTTPException(
            status_code=500,
            detail=f"cursor_project_dir 경로가 디렉터리가 아닙니다: {project}",
        )

    cmd = build_agent_command(prompt)
    env = os.environ.copy()
    api_key = (settings.cursor_api_key or "").strip()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

    # SSH 터미널에서 띄운 Uvicorn + Ctrl+C 등이 자식 agent 에 SIGINT 를 보내 130/-2 로 끊기는 것을 줄임.
    sub_kw: dict = {}
    if sys.platform != "win32":
        sub_kw["start_new_session"] = True

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project,
            stdin=asyncio.subprocess.DEVNULL,
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
                "Cursor 에이전트 실행 파일을 찾을 수 없습니다. .env의 CURSOR_CLI_PATH에 전체 경로를 설정하세요 "
                f"(예: `which cursor` 또는 `which agent`). 현재 설정: {settings.cursor_cli_path!r}."
            ),
        )
    # wait_for(communicate())는 타임아웃 시 communicate 태스크를 취소해 파이프/이벤트 루프가 꼬일 수 있음 → wait + 타임아웃만 사용.
    comm_task: asyncio.Task = asyncio.create_task(proc.communicate())
    try:
        done, _pending = await asyncio.wait(
            {comm_task},
            timeout=settings.agent_timeout_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        logger.info("POST /chat: 요청 취소됨 — 에이전트 하위 프로세스만 정리합니다")
        await asyncio.shield(_reap_agent_subprocess(proc, comm_task))
        raise

    if not done:
        logger.error("POST /chat: agent 타임아웃 (%ss)", settings.agent_timeout_sec)
        await asyncio.shield(_reap_agent_subprocess(proc, comm_task))
        raise HTTPException(
            status_code=504,
            detail="Cursor 에이전트 응답 시간이 초과되었습니다.",
        )

    try:
        stdout_b, stderr_b = comm_task.result()
    except Exception:
        await asyncio.shield(_reap_agent_subprocess(proc, comm_task))
        raise

    out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    err = (stderr_b or b"").decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        logger.error("POST /chat: agent 실패 returncode=%s", proc.returncode)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "cursor_agent_failed",
                "message": "Cursor 에이전트가 비정상 종료했습니다.",
                "returncode": proc.returncode,
                "stderr": err[-8000:] if err else "",
                "stdout": out[-8000:] if out else "",
            },
        )

    logger.info("POST /chat: agent 완료, stdout 길이=%s", len(out))
    content = out if out else "(에이전트 출력 없음)"
    dbg = None
    if debug:
        dbg = {"stdout": out, "stderr": err}
    return ChatResponse(message=AssistantMessage(content=content), debug=dbg)
