"""Run external tools (yt-dlp, gallery-dl, ffmpeg) without blocking the event loop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


class ProcTimeout(Exception):
    pass


async def run(*args: str, timeout: float | None = None) -> ProcResult:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ProcTimeout(f"{args[0]} exceeded {timeout}s")
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    return ProcResult(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))
