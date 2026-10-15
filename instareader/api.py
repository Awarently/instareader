"""HTTP interface.

- GET  /                -> the web page (paste a link, read the result). The page itself needs no auth;
                           every API call it makes sends the access code as a bearer token.
- POST /api/jobs        {url, force} -> {id, status, ...}   start a job without waiting
- GET  /api/jobs/{id}   -> {status: queued|running|done|error, result?, message?}
- GET  /api/recent      -> recently read posts
- POST /read            {url, force} -> result JSON (blocks until done; for scripts/curl)
- GET  /health
"""
from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .errors import ReelError
from .worker import JobQueue

PAGE = Path(__file__).with_name("static") / "index.html"


class ReadRequest(BaseModel):
    url: str
    force: bool = False  # bypass cache and reprocess


def create_app(jobs: JobQueue, token: str) -> FastAPI:
    app = FastAPI(title="instareader", docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(503, "HTTP_TOKEN is not set; the API is disabled.")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Missing or wrong access code.")

    @app.get("/", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "queued": jobs.pending, "cached_results": jobs.cache.count()}

    @app.post("/read", dependencies=[Depends(auth)])
    async def read(req: ReadRequest):
        try:
            return await jobs.submit(req.url, force=req.force)
        except ReelError as e:
            return JSONResponse(e.to_dict(), status_code=e.http_status)

    @app.post("/api/jobs", dependencies=[Depends(auth)])
    async def start_job(req: ReadRequest):
        try:
            key, status = jobs.start_job(req.url, force=req.force)
        except ReelError as e:
            return JSONResponse({"status": "error", **e.to_dict()}, status_code=e.http_status)
        return {"id": key, **status}

    @app.get("/api/jobs/{key}", dependencies=[Depends(auth)])
    async def job(key: str):
        status = jobs.job_status(key)
        if status is None:
            raise HTTPException(404, "Unknown job.")
        return {"id": key, **status}

    @app.get("/api/recent", dependencies=[Depends(auth)])
    async def recent():
        return jobs.cache.recent(30)

    return app
