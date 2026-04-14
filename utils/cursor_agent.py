"""Cursor / agent CLI subprocess: spawn, heartbeat logs, timeout, streaming."""

import asyncio
import logging
import os
import secrets
import sys
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from config import settings
from utils.agent_reap import reap_proc
from utils.cursor_cmd import build_cmd

logger = logging.getLogger("chatbot_api")


async def _create_agent_subprocess(
    prompt: str,
    *,
    run_id: str,
) -> asyncio.subprocess.Process:
    """Spawn cursor/agent CLI; raises FileNotFoundError if binary missing."""
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
        logger.error(
            "[%s] cursor/agent binary not found cmd[0]=%r",
            run_id,
            cmd[0] if cmd else "",
        )
        raise

    logger.info("[%s] Cursor agent subprocess started PID=%s", run_id, proc.pid)
    return proc


async def _wait_communicate_with_heartbeat(
    proc: asyncio.subprocess.Process,
    comm_task: asyncio.Task,
    *,
    run_id: str,
    timeout_sec: float,
    heartbeat_sec: float,
) -> bool:
    """Return True if communicate finished within timeout_sec."""
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
            "[%s] Cursor agent still running (elapsed ~%.0fs / limit %ss, PID=%s)",
            run_id,
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
    run_id: Optional[str] = None,
) -> Tuple[str, str, int]:
    """
    Run cursor/agent CLI once; return (stdout, stderr, returncode).
    On timeout, kill subprocess and raise asyncio.TimeoutError.
    """
    rid = (run_id or "").strip() or secrets.token_hex(4)
    hb = (
        float(settings.agent_heartbeat_log_sec)
        if heartbeat_log_sec is None
        else heartbeat_log_sec
    )

    t_run = time.perf_counter()
    proc = await _create_agent_subprocess(prompt, run_id=rid)
    t_spawn = time.perf_counter() - t_run

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
                run_id=rid,
                timeout_sec=float(timeout_sec),
                heartbeat_sec=max(5.0, hb),
            )
    except asyncio.CancelledError:
        logger.info("[%s] Cursor agent: request cancelled, reaping subprocess", rid)
        await asyncio.shield(reap_proc(proc, comm_task))
        raise

    if not ok:
        logger.error("[%s] Cursor agent: timeout (%ss)", rid, timeout_sec)
        await asyncio.shield(reap_proc(proc, comm_task))
        raise asyncio.TimeoutError()

    try:
        stdout_b, stderr_b = comm_task.result()
    except Exception:
        await asyncio.shield(reap_proc(proc, comm_task))
        raise

    out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    err = (stderr_b or b"").decode("utf-8", errors="replace").strip()
    t_total = time.perf_counter() - t_run
    t_communicate = max(0.0, t_total - t_spawn)
    logger.info(
        "[%s] cursor_agent timing: subprocess_create=%.3fs communicate_wait=%.3fs total=%.3fs prompt_chars=%s",
        rid,
        t_spawn,
        t_communicate,
        t_total,
        len(prompt),
    )
    return out, err, proc.returncode if proc.returncode is not None else -1


async def _drain_stderr(proc: asyncio.subprocess.Process, chunks: List[bytes]) -> None:
    if proc.stderr is None:
        return
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        chunks.append(line)


async def stream_cursor_agent(
    prompt: str,
    *,
    timeout_sec: int,
    heartbeat_log_sec: Optional[float] = None,
    run_id: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Stream CLI stdout in chunks. Yields dict events:
      {"type": "chunk", "text": str}
      {"type": "done", "returncode": int, "stderr": str}
      {"type": "error", "message": str, "stderr": str}

    The subprocess is always reaped in ``finally`` (client disconnect, cancel, errors).
    """
    rid = (run_id or "").strip() or secrets.token_hex(4)
    hb = (
        float(settings.agent_heartbeat_log_sec)
        if heartbeat_log_sec is None
        else heartbeat_log_sec
    )
    chunk_sz = max(256, int(settings.agent_stream_read_chunk_bytes))
    stderr_chunks: List[bytes] = []

    proc: Optional[asyncio.subprocess.Process] = None
    stderr_task: Optional[asyncio.Task] = None
    t_run = time.perf_counter()
    t_spawn = 0.0

    try:
        proc = await _create_agent_subprocess(prompt, run_id=rid)
        t_spawn = time.perf_counter() - t_run
        stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_chunks))
        deadline = time.monotonic() + float(timeout_sec)

        assert proc.stdout is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                err_txt = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
                yield {"type": "error", "message": "timeout", "stderr": err_txt}
                return

            wait = remaining if hb <= 0 else min(max(5.0, hb), remaining)
            try:
                piece = await asyncio.wait_for(proc.stdout.read(chunk_sz), timeout=wait)
            except asyncio.TimeoutError:
                if hb > 0:
                    logger.info(
                        "[%s] Cursor agent stream heartbeat (PID=%s)",
                        rid,
                        proc.pid,
                    )
                if proc.returncode is not None:
                    break
                continue

            if piece:
                yield {
                    "type": "chunk",
                    "text": piece.decode("utf-8", errors="replace"),
                }
            else:
                break

        await asyncio.wait_for(stderr_task, timeout=60.0)
        await asyncio.wait_for(proc.wait(), timeout=30.0)
        rc = proc.returncode if proc.returncode is not None else -1
        err_txt = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        t_total = time.perf_counter() - t_run
        logger.info(
            "[%s] cursor_agent stream timing: subprocess_create=%.3fs total=%.3fs prompt_chars=%s returncode=%s",
            rid,
            t_spawn,
            t_total,
            len(prompt),
            rc,
        )
        yield {"type": "done", "returncode": rc, "stderr": err_txt}
    except asyncio.CancelledError:
        logger.info(
            "[%s] stream_cursor_agent cancelled; reaping agent PID=%s",
            rid,
            getattr(proc, "pid", None),
        )
        raise
    except Exception as e:
        if proc is None:
            raise
        err_txt = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        yield {"type": "error", "message": str(e), "stderr": err_txt}
    finally:
        if proc is not None and stderr_task is not None:
            try:
                await asyncio.shield(_reap_streaming_proc(proc, stderr_task))
            except Exception as exc:
                logger.warning("[%s] stream reap failed: %s", rid, exc)


async def _reap_streaming_proc(
    proc: asyncio.subprocess.Process,
    stderr_task: asyncio.Task,
) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    if not stderr_task.done():
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass
    if proc.stdout:
        try:
            await proc.stdout.read()
        except Exception:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=30.0)
    except Exception:
        pass
