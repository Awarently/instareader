"""Single-worker job queue with cache lookup and de-duplication of in-flight URLs."""
from __future__ import annotations

import asyncio
import logging

from .cache import Cache
from .config import Settings
from .errors import ReelError
from .pipeline import Pipeline
from .urls import ParsedUrl, parse

log = logging.getLogger(__name__)


class JobQueue:
    def __init__(self, s: Settings, pipeline: Pipeline, cache: Cache):
        self.s = s
        self.pipeline = pipeline
        self.cache = cache
        self._queue: asyncio.Queue[tuple[ParsedUrl, asyncio.Future]] = asyncio.Queue()
        self._inflight: dict[str, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        # Background jobs started from the web page, keyed by cache key. Bounded; oldest dropped.
        self.jobs: dict[str, dict] = {}
        self._bg: set[asyncio.Task] = set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="reel-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def pending(self) -> int:
        return len(self._inflight)

    def lookup(self, url: str) -> tuple[ParsedUrl, dict | None]:
        """Parse + cache check without queuing. Raises ReelError for bad URLs."""
        target = parse(url)
        return target, self.cache.get(target.cache_key)

    async def submit(self, url: str, force: bool = False) -> dict:
        target = parse(url)
        if not force:
            hit = self.cache.get(target.cache_key)
            if hit is not None:
                return {**hit, "cached": True}
        fut = self._inflight.get(target.cache_key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._inflight[target.cache_key] = fut
            await self._queue.put((target, fut))
        # shield: a client disconnecting must not cancel the job for others waiting on it
        result = await asyncio.shield(fut)
        return {**result, "cached": False}

    def start_job(self, url: str, force: bool = False) -> tuple[str, dict]:
        """Start processing without waiting. Returns (job id, status). Raises ReelError for bad URLs."""
        target = parse(url)
        key = target.cache_key
        if not force:
            hit = self.cache.get(key)
            if hit is not None:
                return key, self._set_job(key, {"status": "done", "url": target.url, "result": {**hit, "cached": True}})
        current = self.jobs.get(key)
        if current and current["status"] in {"queued", "running"}:
            return key, current
        status = self._set_job(key, {"status": "queued", "url": target.url, "ahead": self.pending})
        task = asyncio.create_task(self._track(key, url, force))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return key, status

    def job_status(self, key: str) -> dict | None:
        if key in self.jobs:
            return self.jobs[key]
        hit = self.cache.get(key)
        return {"status": "done", "url": hit["source"]["url"], "result": {**hit, "cached": True}} if hit else None

    def _set_job(self, key: str, status: dict) -> dict:
        self.jobs.pop(key, None)
        self.jobs[key] = status
        while len(self.jobs) > 200:
            self.jobs.pop(next(iter(self.jobs)))
        return status

    async def _track(self, key: str, url: str, force: bool) -> None:
        try:
            result = await self.submit(url, force=force)
            self._set_job(key, {"status": "done", "url": result["source"]["url"], "result": result})
        except ReelError as e:
            self._set_job(key, {"status": "error", "url": url, **e.to_dict()})
        except Exception as e:  # keep the page informed rather than stuck on "running"
            log.exception("background job failed")
            self._set_job(key, {"status": "error", "url": url, "error": "processing_failed", "message": str(e)})

    async def _loop(self) -> None:
        while True:
            target, fut = await self._queue.get()
            job = self.jobs.get(target.cache_key)
            if job and job["status"] == "queued":
                job["status"] = "running"
                job.pop("ahead", None)
            try:
                async with asyncio.timeout(self.s.job_timeout):
                    result = await self.pipeline.run(target)
                self.cache.put(target.cache_key, target.url, result)
                if not fut.done():
                    fut.set_result(result)
            except TimeoutError:
                if not fut.done():
                    fut.set_exception(ReelError("timeout", f"Processing took longer than {self.s.job_timeout}s."))
            except ReelError as e:
                log.warning("job %s failed: %s %s", target.url, e.code, e.message)
                if not fut.done():
                    fut.set_exception(e)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.set_exception(ReelError("processing_failed", "Service is shutting down."))
                raise
            except Exception as e:  # keep the worker alive
                log.exception("job %s crashed", target.url)
                if not fut.done():
                    fut.set_exception(ReelError("processing_failed", f"Unexpected error: {e}"))
            finally:
                self._inflight.pop(target.cache_key, None)
                self._queue.task_done()
                # avoid "exception was never retrieved" when nobody awaits a failed job
                if fut.done() and not fut.cancelled():
                    fut.exception()
