"""FastAPI backend for Council Web UI.

Runs council jobs via subprocess (same behaviour as the CLI) and serves
run artifact data.  Binds to 127.0.0.1 by default — do NOT expose to the
internet; this server can execute code-review tooling on local repositories.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("council.ui.api")

app = FastAPI(title="Council API", version="0.1.0")

# Allow the Streamlit frontend (localhost) to talk to us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "http://localhost:*"],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Job registry
# ---------------------------------------------------------------------------

_LOG_BUFFER_MAX = 200_000  # characters kept per job


@dataclass
class _Job:
    job_id: str
    mode: str
    task: str
    cmd: list[str]
    process: subprocess.Popen[str] | None = None
    run_dir: str | None = None
    log_buffer: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def status(self) -> str:
        if self.process is None:
            return "pending"
        if self.process.poll() is None:
            return "running"
        return "exited"

    @property
    def exit_code(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def append_log(self, text: str) -> None:
        with self._lock:
            self.log_buffer += text
            if len(self.log_buffer) > _LOG_BUFFER_MAX:
                self.log_buffer = self.log_buffer[-_LOG_BUFFER_MAX:]

    def get_log(self) -> str:
        with self._lock:
            return self.log_buffer


_jobs: dict[str, _Job] = {}


# ---------------------------------------------------------------------------
# Helpers – run listing / manifest parsing
# ---------------------------------------------------------------------------


def _find_runs_dir() -> Path:
    """Return the default runs directory (cwd/runs)."""
    return Path.cwd() / "runs"


def parse_manifest(run_dir: Path) -> dict[str, Any]:
    """Parse a run directory's manifest/state into a summary dict.

    Returns a dict with keys: name, path, timestamp, status, mode,
    task_preview.  Gracefully handles missing or broken files.
    """
    info: dict[str, Any] = {
        "name": run_dir.name,
        "path": str(run_dir),
    }

    # Try manifest.json first (richer), fall back to state.json.
    manifest_path = run_dir / "manifest.json"
    state_path = run_dir / "state.json"

    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            info["timestamp"] = data.get("start_time", "")
            info["status"] = data.get("no_save") and "completed" or _status_from_state(state_path)
            info["mode"] = data.get("mode", "")
            info["task_preview"] = data.get("task_preview", "")
            info["duration_sec"] = data.get("total_duration_sec")
            return info
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: state.json
    info["status"] = _status_from_state(state_path)
    info["mode"] = ""
    info["task_preview"] = ""
    info["timestamp"] = ""

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            info["mode"] = state.get("mode", "")
            info["task_preview"] = state.get("task_preview", "")
            info["status"] = state.get("status", info["status"])
            info["timestamp"] = state.get("started_at", "")
        except (json.JSONDecodeError, OSError):
            pass

    return info


def _status_from_state(state_path: Path) -> str:
    if not state_path.exists():
        return "unknown"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("status", "unknown")
    except (json.JSONDecodeError, OSError):
        return "unknown"


def list_runs(outdir: Path | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return summaries for recent runs, newest first."""
    runs_dir = outdir or _find_runs_dir()
    if not runs_dir.is_dir():
        return []

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )

    results: list[dict[str, Any]] = []
    for rd in run_dirs[:limit]:
        results.append(parse_manifest(rd))
    return results


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class JobRequest(BaseModel):
    mode: str  # fix | feature | review
    task: str
    diff: str | None = None
    context: str | None = None
    include: list[str] | None = None
    include_glob: list[str] | None = None
    include_from_diff: bool = False
    no_save: bool = False
    redact_paths: bool = False
    smart_context: bool | None = None
    structured_review: bool | None = None
    claude_n: int = 1
    codex_n: int = 1
    outdir: str = "runs"


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------


def _build_command(req: JobRequest) -> list[str]:
    """Build the ``council`` CLI command list from a job request."""
    council_bin = shutil.which("council") or sys.executable
    cmd = [sys.executable, "-m", "council"] if council_bin == sys.executable else [council_bin]

    cmd.append(req.mode)
    cmd.append(req.task)

    if req.diff:
        cmd.extend(["--diff", req.diff])
    if req.context:
        cmd.extend(["--context", req.context])
    if req.include:
        for inc in req.include:
            cmd.extend(["--include", inc])
    if req.include_glob:
        for g in req.include_glob:
            cmd.extend(["--include-glob", g])
    if req.include_from_diff:
        cmd.append("--include-from-diff")
    if req.no_save:
        cmd.append("--no-save")
    if req.redact_paths:
        cmd.append("--redact-paths")
    if req.smart_context is True:
        cmd.append("--smart-context")
    elif req.smart_context is False:
        cmd.append("--no-smart-context")
    if req.structured_review is True:
        cmd.append("--structured-review")
    elif req.structured_review is False:
        cmd.append("--no-structured-review")
    if req.claude_n != 1:
        cmd.extend(["--claude-n", str(req.claude_n)])
    if req.codex_n != 1:
        cmd.extend(["--codex-n", str(req.codex_n)])
    if req.outdir != "runs":
        cmd.extend(["--outdir", req.outdir])

    return cmd


def _run_job(job: _Job) -> None:
    """Execute a council subprocess in a background thread."""
    try:
        proc = subprocess.Popen(
            job.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        job.process = proc

        assert proc.stdout is not None
        for line in proc.stdout:
            job.append_log(line)
            # Detect the machine-readable RUN_DIR line.
            if line.startswith("RUN_DIR="):
                job.run_dir = line.strip().split("=", 1)[1]
        proc.wait()
    except Exception as exc:
        job.append_log(f"\n[council-api] Job failed: {exc}\n")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runs")
def get_runs(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return list_runs(limit=limit)


@app.get("/runs/{run_id}/final")
def get_run_final(run_id: str) -> dict[str, str | None]:
    runs_dir = _find_runs_dir()
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        raise HTTPException(404, f"Run not found: {run_id}")

    final_md = run_dir / "final" / "final.md"
    content = final_md.read_text(encoding="utf-8") if final_md.exists() else None
    return {"final": content}


@app.get("/runs/{run_id}/patch")
def get_run_patch(run_id: str) -> dict[str, str | None]:
    runs_dir = _find_runs_dir()
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        raise HTTPException(404, f"Run not found: {run_id}")

    patch_file = run_dir / "final" / "final.patch"
    content = patch_file.read_text(encoding="utf-8") if patch_file.exists() else None
    return {"patch": content}


@app.post("/jobs")
def create_job(req: JobRequest) -> dict[str, str | None]:
    if req.mode not in ("fix", "feature", "review"):
        raise HTTPException(400, f"Invalid mode: {req.mode}")

    cmd = _build_command(req)
    job_id = uuid.uuid4().hex[:12]
    job = _Job(job_id=job_id, mode=req.mode, task=req.task, cmd=cmd)
    _jobs[job_id] = job

    t = threading.Thread(target=_run_job, args=(job,), daemon=True)
    t.start()

    return {"job_id": job_id, "run_dir": None}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"Job not found: {job_id}")

    return {
        "job_id": job.job_id,
        "status": job.status,
        "exit_code": job.exit_code,
        "run_dir": job.run_dir,
        "mode": job.mode,
    }


@app.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str) -> dict[str, str]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"Job not found: {job_id}")

    return {"logs": job.get_log()}
