import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

import asyncio
import json

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from config import settings
from schemas import (
    AssistantMessage,
    ChatJobCreateResponse,
    ChatJobRequest,
    ChatJobStatusResponse,
    ChatRequest,
    ChatResponse,
    WarmupResponse,
)
from utils.agent_worker import AgentWorker
from utils.auth import require_bearer
from utils.cors import apply_cors
from utils.cursor_agent import run_cursor_agent, stream_cursor_agent
from utils.http_handlers import validation_error
from utils.logging_setup import setup_app_logging
from utils.prompt import msgs_to_prompt

_WARMUP_PROMPT = "Reply with exactly: ok"

_APP_ROOT = Path(__file__).resolve().parent

logger = logging.getLogger("chatbot_api")
_warned_missing_cursor_api_key = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    log_dir = setup_app_logging(_APP_ROOT)
    logger.info("기동 완료 — 타이밍·요청 로그는 %s 에 기록됩니다.", log_dir / "app.log")
    worker = AgentWorker()
    await worker.start()
    app.state.agent_worker = worker
    logger.info("AgentWorker started (concurrency=%s)", settings.agent_worker_concurrency)
    yield
    await worker.stop()
    logger.info("AgentWorker stopped")


app = FastAPI(
    title="Cursor Agent Chat API",
    version="1.0.0",
    redirect_slashes=False,
    lifespan=_lifespan,
)
app.add_exception_handler(RequestValidationError, validation_error)


@app.middleware("http")
async def _request_timing_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - t0
    path = request.url.path
    if path != "/health":
        rid = getattr(request.state, "agent_run_id", None)
        if rid:
            logger.info(
                "HTTP %s %s -> %s in %.3fs [run_id=%s]",
                request.method,
                path,
                response.status_code,
                elapsed,
                rid,
            )
        else:
            logger.info(
                "HTTP %s %s -> %s in %.3fs",
                request.method,
                path,
                response.status_code,
                elapsed,
            )
    return response

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
    out["agent_worker_concurrency"] = str(settings.agent_worker_concurrency)
    out["agent_job_store_max"] = str(settings.agent_job_store_max)
    return out


@app.post("/warmup", response_model=WarmupResponse, dependencies=[Depends(require_bearer)])
async def warmup(request: Request) -> WarmupResponse:
    """첫 Cursor CLI 기동·인증·캐시를 미리 끌어올릴 때 호출(클라이언트 기동 시 1회 권장)."""
    t_req = time.perf_counter()
    if settings.mock_agent:
        logger.info(
            "POST /warmup 타이밍: mock total=%.3fs",
            time.perf_counter() - t_req,
        )
        return WarmupResponse(message="MOCK_AGENT=true — CLI를 호출하지 않았습니다.")

    project = settings.cursor_project_dir
    if not os.path.isdir(project):
        raise HTTPException(
            status_code=500,
            detail=f"cursor_project_dir 경로가 디렉터리가 아닙니다: {project}",
        )

    run_id = secrets.token_hex(4)
    request.state.agent_run_id = run_id
    logger.info(
        "[%s] POST /warmup: Cursor agent 호출 시작 (최대 %ss)",
        run_id,
        settings.warmup_timeout_sec,
    )

    t_agent_start = time.perf_counter()
    try:
        _out, err, returncode = await run_cursor_agent(
            _WARMUP_PROMPT,
            timeout_sec=settings.warmup_timeout_sec,
            run_id=run_id,
        )
    except FileNotFoundError:
        logger.info(
            "[%s] POST /warmup 타이밍: cursor_agent=%.3fs (FileNotFoundError) total=%.3fs",
            run_id,
            time.perf_counter() - t_agent_start,
            time.perf_counter() - t_req,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Cursor 에이전트 실행 파일을 찾을 수 없습니다. .env의 CURSOR_CLI_PATH에 전체 경로를 설정하세요 "
                f"(예: `which cursor` 또는 `which agent`). 현재 설정: {settings.cursor_cli_path!r}."
            ),
        )
    except TimeoutError:
        logger.info(
            "[%s] POST /warmup 타이밍: cursor_agent=%.3fs (TimeoutError) total=%.3fs",
            run_id,
            time.perf_counter() - t_agent_start,
            time.perf_counter() - t_req,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"워밍업 시간이 초과되었습니다(WARMUP_TIMEOUT_SEC={settings.warmup_timeout_sec}). "
                "첫 실행은 더 오래 걸릴 수 있으니 값을 늘리거나, 서버에서 한 번 수동으로 `cursor agent`를 실행해 보세요."
            ),
        )

    if returncode != 0:
        logger.info(
            "[%s] POST /warmup 타이밍: cursor_agent=%.3fs (비정상 종료 returncode=%s) total=%.3fs",
            run_id,
            time.perf_counter() - t_agent_start,
            returncode,
            time.perf_counter() - t_req,
        )
        logger.error("[%s] POST /warmup: agent 실패 returncode=%s", run_id, returncode)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "cursor_agent_warmup_failed",
                "message": "워밍업용 에이전트 실행이 비정상 종료했습니다.",
                "returncode": returncode,
                "stderr": (err[-8000:] if err else ""),
            },
        )

    logger.info(
        "[%s] POST /warmup 타이밍: cursor_agent=%.3fs total=%.3fs",
        run_id,
        time.perf_counter() - t_agent_start,
        time.perf_counter() - t_req,
    )
    return WarmupResponse()


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_bearer)])
async def chat(
    request: Request,
    body: ChatRequest,
    debug: bool = Query(False, description="응답의 debug 필드에 stdout/stderr 포함 여부"),
) -> ChatResponse:
    """메시지 목록으로 Cursor agent를 실행하고 stdout을 assistant 답으로 돌려준다."""
    global _warned_missing_cursor_api_key

    t_req = time.perf_counter()
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
        logger.info(
            "POST /chat 타이밍: mock total=%.3fs",
            time.perf_counter() - t_req,
        )
        return ChatResponse(message=AssistantMessage(content=content), debug=dbg)

    run_id = secrets.token_hex(4)
    request.state.agent_run_id = run_id
    logger.info(
        "[%s] POST /chat: Cursor agent 시작 — 끝날 때까지 응답을 보내지 않음 (최대 %ss)",
        run_id,
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

    t_prompt_start = time.perf_counter()
    prompt = msgs_to_prompt(body.messages)
    t_prompt = time.perf_counter() - t_prompt_start
    project = settings.cursor_project_dir
    if not os.path.isdir(project):
        raise HTTPException(
            status_code=500,
            detail=f"cursor_project_dir 경로가 디렉터리가 아닙니다: {project}",
        )

    t_agent_start = time.perf_counter()
    try:
        out, err, returncode = await run_cursor_agent(
            prompt,
            timeout_sec=settings.agent_timeout_sec,
            run_id=run_id,
        )
    except FileNotFoundError:
        logger.info(
            "[%s] POST /chat 타이밍: prompt_build=%.3fs cursor_agent=%.3fs (FileNotFoundError) total=%.3fs",
            run_id,
            t_prompt,
            time.perf_counter() - t_agent_start,
            time.perf_counter() - t_req,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Cursor 에이전트 실행 파일을 찾을 수 없습니다. .env의 CURSOR_CLI_PATH에 전체 경로를 설정하세요 "
                f"(예: `which cursor` 또는 `which agent`). 현재 설정: {settings.cursor_cli_path!r}."
            ),
        )
    except TimeoutError:
        logger.info(
            "[%s] POST /chat 타이밍: prompt_build=%.3fs cursor_agent=%.3fs (TimeoutError) total=%.3fs",
            run_id,
            t_prompt,
            time.perf_counter() - t_agent_start,
            time.perf_counter() - t_req,
        )
        raise HTTPException(
            status_code=504,
            detail="Cursor 에이전트 응답 시간이 초과되었습니다.",
        )

    t_agent = time.perf_counter() - t_agent_start
    logger.info(
        "[%s] POST /chat 타이밍: prompt_build=%.3fs cursor_agent=%.3fs total=%.3fs",
        run_id,
        t_prompt,
        t_agent,
        time.perf_counter() - t_req,
    )

    if returncode != 0:
        logger.error("[%s] POST /chat: agent 실패 returncode=%s", run_id, returncode)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "cursor_agent_failed",
                "message": "Cursor 에이전트가 비정상 종료했습니다.",
                "returncode": returncode,
                "stderr": err[-8000:] if err else "",
                "stdout": out[-8000:] if out else "",
            },
        )

    logger.info("[%s] POST /chat: agent 완료, stdout 길이=%s", run_id, len(out))
    content = out if out else "(에이전트 출력 없음)"
    dbg = None
    if debug:
        dbg = {"stdout": out, "stderr": err}
    return ChatResponse(message=AssistantMessage(content=content), debug=dbg)

def _sse_pack(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


@app.post(
    "/chat/jobs",
    response_model=ChatJobCreateResponse,
    dependencies=[Depends(require_bearer)],
)
async def chat_enqueue(request: Request, body: ChatJobRequest) -> ChatJobCreateResponse:
    """Queue a chat run; poll GET /chat/jobs/{job_id} or use webhook_url."""
    job_id = secrets.token_hex(8)
    run_id = secrets.token_hex(4)
    request.state.agent_run_id = run_id
    worker: AgentWorker = request.app.state.agent_worker

    if settings.mock_agent:
        last_user = next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            "",
        )
        content = (
            "[MOCK] Cursor CLI is bypassed.\n\nLast user message:\n"
            + (last_user or "(empty)")
            + "\n\nSet MOCK_AGENT=false in .env for real runs."
        )
        await worker.submit_mock_job(
            job_id=job_id,
            run_id=run_id,
            content=content,
            webhook_url=body.webhook_url,
        )
        return ChatJobCreateResponse(job_id=job_id)

    if not os.path.isdir(settings.cursor_project_dir):
        raise HTTPException(
            status_code=500,
            detail=f"cursor_project_dir is not a directory: {settings.cursor_project_dir}",
        )

    prompt = msgs_to_prompt(body.messages)
    await worker.submit_job(
        job_id=job_id,
        run_id=run_id,
        prompt=prompt,
        webhook_url=body.webhook_url,
    )
    return ChatJobCreateResponse(job_id=job_id)


@app.get(
    "/chat/jobs/{job_id}",
    response_model=ChatJobStatusResponse,
    dependencies=[Depends(require_bearer)],
)
async def chat_job_status(request: Request, job_id: str) -> ChatJobStatusResponse:
    worker: AgentWorker = request.app.state.agent_worker
    rec = await worker.get_job(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    msg = None
    err = rec.error
    if rec.status == "completed":
        msg = AssistantMessage(content=rec.stdout or "(empty agent output)")
        err = None
    return ChatJobStatusResponse(
        job_id=rec.job_id,
        status=rec.status,  # type: ignore[arg-type]
        message=msg,
        error=err,
        returncode=rec.returncode,
        stderr_tail=(rec.stderr[-8000:] if rec.stderr else None),
        webhook_delivered=rec.webhook_delivered,
        webhook_error=rec.webhook_error,
    )


@app.post("/chat/stream", dependencies=[Depends(require_bearer)])
async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
    """Server-Sent Events: stream agent stdout; final event has returncode / stderr."""
    run_id = secrets.token_hex(4)
    request.state.agent_run_id = run_id

    async def gen():
        if settings.mock_agent:
            last_user = next(
                (m.content for m in reversed(body.messages) if m.role == "user"),
                "",
            )
            text = "[MOCK] stream\n" + (last_user or "(empty)")
            yield _sse_pack({"type": "chunk", "text": text})
            yield _sse_pack({"type": "done", "returncode": 0, "stderr": ""})
            return
        if not os.path.isdir(settings.cursor_project_dir):
            yield _sse_pack(
                {
                    "type": "error",
                    "message": "cursor_project_dir missing",
                    "stderr": "",
                }
            )
            return
        prompt = msgs_to_prompt(body.messages)
        agen = stream_cursor_agent(
            prompt,
            timeout_sec=settings.agent_timeout_sec,
            run_id=run_id,
        )
        try:
            async for ev in agen:
                yield _sse_pack(ev)
                try:
                    if await request.is_disconnected():
                        logger.warning(
                            "[%s] SSE client disconnected; closing agent stream",
                            run_id,
                        )
                        break
                except Exception:
                    pass
        except FileNotFoundError:
            yield _sse_pack(
                {
                    "type": "error",
                    "message": "cursor_cli_not_found",
                    "stderr": "",
                }
            )
        except asyncio.CancelledError:
            logger.warning("[%s] SSE stream cancelled (client/server)", run_id)
            raise
        finally:
            try:
                await agen.aclose()
            except Exception:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
