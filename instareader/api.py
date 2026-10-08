"""HTTP interface: POST /read {url} -> result JSON. Bearer token required."""
from __future__ import annotations

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .errors import ReelError
from .worker import JobQueue


class ReadRequest(BaseModel):
    url: str
    force: bool = False  # bypass cache and reprocess


def create_app(jobs: JobQueue, token: str) -> FastAPI:
    app = FastAPI(title="instareader", docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(503, "HTTP_TOKEN is not set; the HTTP endpoint is disabled.")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Missing or wrong bearer token.")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "queued": jobs.pending, "cached_results": jobs.cache.count()}

    @app.post("/read", dependencies=[Depends(auth)])
    async def read(req: ReadRequest):
        try:
            return await jobs.submit(req.url, force=req.force)
        except ReelError as e:
            return JSONResponse(e.to_dict(), status_code=e.http_status)

    return app
