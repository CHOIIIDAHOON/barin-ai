import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError

from config import settings
from schemas import AssistantMessage, ChatRequest, ChatResponse
from utils.agent_reap import reap_proc
from utils.auth import require_bearer
from utils.cors import apply_cors
from utils.cursor_cmd import build_cmd
from utils.http_handlers import validation_error
from utils.prompt import msgs_to_prompt

_APP_ROOT = Path(__file__).resolve().parent

logger = logging.getLogger("chatbot_api")
_warned_missing_cursor_api_key = False

app = FastAPI(title="Cursor Agent Chat API", version="1.0.0", redirect_slashes=False)
app.add_exception_handler(RequestValidationError, validation_error)

if not settings.use_nginx_cors:
    apply_cors(app)


@app.get("/health")
def health() -> Dict[str, str]:
    """프로세스·설정 대조용 liveness 및 Cursor 관련 설정 요약."""
    out: Dict[str, str] = {
        "status": "ok",
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


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_bearer)])
async def chat(
    body: ChatRequest,
    debug: bool = Query(False, description="응답의 debug 필드에 stdout/stderr 포함 여부"),
) -> ChatResponse:
    """메시지 목록으로 Cursor agent를 실행하고 stdout을 assistant 답으로 돌려준다."""
    global _warned_missing_cursor_api_key

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
        dbg: Optional[Dict[str, str]] = {"mode": "mock_agent"} if debug else None
        return ChatResponse(message=AssistantMessage(content=content), debug=dbg)

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

    prompt = msgs_to_prompt(body.messages)
    project = settings.cursor_project_dir
    if not os.path.isdir(project):
        raise HTTPException(
            status_code=500,
            detail=f"cursor_project_dir 경로가 디렉터리가 아닙니다: {project}",
        )

    cmd = build_cmd(prompt)
    env = os.environ.copy()
    api_key = (settings.cursor_api_key or "").strip()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

    sub_kw = {}
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

    comm_task = asyncio.create_task(proc.communicate())
    try:
        done, _pending = await asyncio.wait(
            {comm_task},
            timeout=settings.agent_timeout_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        logger.info("POST /chat: 요청 취소됨 — 에이전트 하위 프로세스만 정리합니다")
        await asyncio.shield(reap_proc(proc, comm_task))
        raise

    if not done:
        logger.error("POST /chat: agent 타임아웃 (%ss)", settings.agent_timeout_sec)
        await asyncio.shield(reap_proc(proc, comm_task))
        raise HTTPException(
            status_code=504,
            detail="Cursor 에이전트 응답 시간이 초과되었습니다.",
        )

    try:
        stdout_b, stderr_b = comm_task.result()
    except Exception:
        await asyncio.shield(reap_proc(proc, comm_task))
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
