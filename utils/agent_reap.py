"""에이전트 자식 프로세스와 communicate 태스크를 안전하게 정리한다."""

import asyncio
import logging

logger = logging.getLogger("chatbot_api")


async def reap_proc(
    proc: asyncio.subprocess.Process,
    comm_task: asyncio.Task,
    *,
    comm_wait_sec: float = 30.0,
    proc_wait_sec: float = 30.0,
) -> None:
    """kill·wait로 프로세스를 끝내고, communicate 태스크가 남아 있으면 기다리거나 취소한다."""
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
