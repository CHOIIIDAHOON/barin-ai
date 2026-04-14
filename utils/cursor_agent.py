"""Cursor CLI 하위 프로세스: 생성, 대기(주기 로그), 타임아웃 시 정리."""

import asyncio
import logging
import os
import sys
from typing import Optional, Tuple

from config import settings
from utils.agent_reap import reap_proc
from utils.cursor_cmd import build_cmd

logger = logging.getLogger("chatbot_api")


async def _wait_communicate_with_heartbeat(
    proc: asyncio.subprocess.Process,
    comm_task: asyncio.Task,
    *,
    timeout_sec: float,
    heartbeat_sec: float,
) -> bool:
    """communicate가 끝나면 True, timeout_sec 내에 못 끝나면 False."""
    elapsed = 0.0
    while elapsed < timeout_sec:
        chunk = min(heartbeat_sec, timeout_sec - elapsed)
        done, _ = await asyncio.wait(
            {comm_task},
            timeout=chunk,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if done:
            return True
        elapsed += chunk
        logger.info(
            "Cursor 에이전트 실행 중… (경과 약 %.0fs / 제한 %ss, PID=%s)",
            elapsed,
            int(timeout_sec),
            proc.pid,
        )
    return False


async def run_cursor_agent(
    prompt: str,
    *,
    timeout_sec: int,
    heartbeat_log_sec: Optional[float] = None,
) -> Tuple[str, str, int]:
    """
    Cursor/agent CLI를 실행하고 (stdout, stderr, returncode)를 돌려준다.
    타임아웃 시 하위 프로세스를 정리하고 asyncio.TimeoutError를 발생시킨다.
    """
    hb = (
        float(settings.agent_heartbeat_log_sec)
        if heartbeat_log_sec is None
        else heartbeat_log_sec
    )
    project = settings.cursor_project_dir

    cmd = build_cmd(prompt)
    env = os.environ.copy()
    api_key = (settings.cursor_api_key or "").strip()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

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
        logger.error("cursor/agent 실행 파일을 찾을 수 없음 cmd[0]=%r", cmd[0] if cmd else "")
        raise

    comm_task = asyncio.create_task(proc.communicate())
    try:
        if hb <= 0:
            done, _ = await asyncio.wait(
                {comm_task},
                timeout=float(timeout_sec),
                return_when=asyncio.FIRST_COMPLETED,
            )
            ok = bool(done)
        else:
            ok = await _wait_communicate_with_heartbeat(
                proc,
                comm_task,
                timeout_sec=float(timeout_sec),
                heartbeat_sec=max(5.0, hb),
            )
    except asyncio.CancelledError:
        logger.info("Cursor agent: 요청 취소됨 — 하위 프로세스 정리")
        await asyncio.shield(reap_proc(proc, comm_task))
        raise

    if not ok:
        logger.error("Cursor agent: 타임아웃 (%ss)", timeout_sec)
        await asyncio.shield(reap_proc(proc, comm_task))
        raise asyncio.TimeoutError()

    try:
        stdout_b, stderr_b = comm_task.result()
    except Exception:
        await asyncio.shield(reap_proc(proc, comm_task))
        raise

    out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    err = (stderr_b or b"").decode("utf-8", errors="replace").strip()
    return out, err, proc.returncode if proc.returncode is not None else -1
