"""Background queue worker for Cursor CLI jobs (async /chat/jobs)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from config import settings
from utils.cursor_agent import run_cursor_agent

logger = logging.getLogger("chatbot_api")


@dataclass
class JobRecord:
    job_id: str
    run_id: str
    status: str
    prompt: str
    webhook_url: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    webhook_delivered: Optional[bool] = None
    webhook_error: Optional[str] = None


@dataclass
class _QueuedJob:
    job_id: str
    run_id: str
    prompt: str
    webhook_url: Optional[str]


class AgentWorker:
    """Long-lived asyncio tasks draining a queue; each job still runs one cursor subprocess."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Optional[_QueuedJob]] = asyncio.Queue()
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()
        self._tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        n = max(1, int(settings.agent_worker_concurrency))
        for i in range(n):
            self._tasks.append(asyncio.create_task(self._worker_loop(i)))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def submit_job(
        self,
        *,
        job_id: str,
        run_id: str,
        prompt: str,
        webhook_url: Optional[str],
    ) -> None:
        rec = JobRecord(
            job_id=job_id,
            run_id=run_id,
            status="queued",
            prompt=prompt,
            webhook_url=(webhook_url or "").strip() or None,
        )
        async with self._lock:
            self._jobs[job_id] = rec
            self._prune_locked()
        await self._queue.put(
            _QueuedJob(
                job_id=job_id,
                run_id=run_id,
                prompt=prompt,
                webhook_url=rec.webhook_url,
            )
        )

    async def submit_mock_job(
        self,
        *,
        job_id: str,
        run_id: str,
        content: str,
        webhook_url: Optional[str],
    ) -> None:
        rec = JobRecord(
            job_id=job_id,
            run_id=run_id,
            status="queued",
            prompt="",
            webhook_url=(webhook_url or "").strip() or None,
        )
        async with self._lock:
            self._jobs[job_id] = rec
            self._prune_locked()
        await self._queue.put(
            _QueuedJob(
                job_id=job_id,
                run_id=run_id,
                prompt="__mock__:" + content,
                webhook_url=rec.webhook_url,
            )
        )

    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        async with self._lock:
            r = self._jobs.get(job_id)
            return r

    def _prune_locked(self) -> None:
        cap = max(10, int(settings.agent_job_store_max))
        if len(self._jobs) <= cap:
            return
        terminal = [
            k
            for k, v in self._jobs.items()
            if v.status in ("completed", "failed", "cancelled")
        ]
        terminal.sort(key=lambda k: self._jobs[k].completed_at or 0.0)
        while len(self._jobs) > cap and terminal:
            oid = terminal.pop(0)
            self._jobs.pop(oid, None)

    async def _worker_loop(self, worker_id: int) -> None:
        logger.info("AgentWorker consumer %s started", worker_id)
        try:
            while True:
                item = await self._queue.get()
                try:
                    if item is None:
                        return
                    await self._run_one(item, worker_id)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("AgentWorker consumer %s cancelled", worker_id)
            raise

    async def _run_one(self, item: _QueuedJob, worker_id: int) -> None:
        jid, rid = item.job_id, item.run_id
        if item.prompt.startswith("__mock__:"):
            body = item.prompt.split(":", 1)[1]
            async with self._lock:
                rec = self._jobs.get(jid)
                if rec:
                    rec.status = "running"
                    rec.started_at = time.time()
            async with self._lock:
                rec = self._jobs.get(jid)
                if rec:
                    rec.status = "completed"
                    rec.stdout = body
                    rec.returncode = 0
                    rec.completed_at = time.time()
                    self._prune_locked()
            await self._maybe_webhook(jid)
            return

        async with self._lock:
            rec = self._jobs.get(jid)
            if rec:
                rec.status = "running"
                rec.started_at = time.time()

        try:
            out, err, code = await run_cursor_agent(
                item.prompt,
                timeout_sec=settings.agent_timeout_sec,
                run_id=rid,
            )
        except FileNotFoundError:
            async with self._lock:
                rec = self._jobs.get(jid)
                if rec:
                    rec.status = "failed"
                    rec.error = "cursor_cli_not_found"
                    rec.completed_at = time.time()
            await self._maybe_webhook(jid)
            return
        except asyncio.TimeoutError:
            async with self._lock:
                rec = self._jobs.get(jid)
                if rec:
                    rec.status = "failed"
                    rec.error = "timeout"
                    rec.completed_at = time.time()
            await self._maybe_webhook(jid)
            return
        except Exception as e:
            logger.exception("[%s] AgentWorker job failed", rid)
            async with self._lock:
                rec = self._jobs.get(jid)
                if rec:
                    rec.status = "failed"
                    rec.error = str(e)
                    rec.completed_at = time.time()
            await self._maybe_webhook(jid)
            return

        async with self._lock:
            rec = self._jobs.get(jid)
            if rec:
                rec.stdout = out
                rec.stderr = err
                rec.returncode = code
                if code == 0:
                    rec.status = "completed"
                else:
                    rec.status = "failed"
                    rec.error = "agent_nonzero_exit"
                rec.completed_at = time.time()
                self._prune_locked()

        await self._maybe_webhook(jid)

    async def _maybe_webhook(self, job_id: str) -> None:
        async with self._lock:
            rec = self._jobs.get(job_id)
            if not rec or not rec.webhook_url:
                return
            if rec.webhook_delivered is not None:
                return
            url = rec.webhook_url
            payload = {
                "job_id": rec.job_id,
                "status": rec.status,
                "returncode": rec.returncode,
                "error": rec.error,
                "stdout": (rec.stdout or "")[:8000],
                "stderr_tail": (rec.stderr or "")[-8000:],
            }

        try:
            async with httpx.AsyncClient(timeout=settings.job_webhook_timeout_sec) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
            ok, werr = True, None
        except Exception as e:
            ok, werr = False, str(e)

        async with self._lock:
            rec = self._jobs.get(job_id)
            if rec:
                rec.webhook_delivered = ok
                rec.webhook_error = werr
