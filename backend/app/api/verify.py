from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.models import ProjectSelection
from app.orchestration import pipeline
from app.storage import db as store

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory verification state
# ---------------------------------------------------------------------------

class _VerificationState(BaseModel):
    status: Literal["idle", "running", "complete", "error"] = "idle"
    error: str = ""

_state = _VerificationState()


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_pipeline(repository: str, branch: str) -> None:
    global _state
    tmp_dir: str | None = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="docproof_")
        repo_path = Path(tmp_dir) / "repo"

        # Clone the repository (shallow, single branch for speed)
        result = subprocess.run(
            [
                "git", "clone",
                "--depth", "1",
                "--branch", branch,
                "--single-branch",
                repository,
                str(repo_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            # Try without specifying branch (falls back to default branch)
            result = subprocess.run(
                [
                    "git", "clone",
                    "--depth", "1",
                    "--single-branch",
                    repository,
                    str(repo_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                _state = _VerificationState(
                    status="error",
                    error=f"git clone failed: {result.stderr.strip()}",
                )
                return

        # Detect backend path inside cloned repo
        backend_path: Path | None = None
        for candidate in ("backend", "app", "src/backend"):
            candidate_path = repo_path / candidate
            if candidate_path.is_dir():
                backend_path = candidate_path
                break

        # Run the full verification pipeline
        pipeline_result = pipeline.run(
            repo_path=repo_path,
            backend_path=backend_path,
        )

        # Replace the in-memory store with the real pipeline results
        store.reset_with(
            pipeline_result.contracts,
            fixes=pipeline_result.fixes,
            trust_score_after=pipeline_result.trust_score_after,
        )

        _state = _VerificationState(status="complete")

    except subprocess.TimeoutExpired:
        _state = _VerificationState(
            status="error",
            error="git clone timed out after 120 seconds.",
        )
    except Exception as exc:  # noqa: BLE001
        _state = _VerificationState(status="error", error=str(exc))
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/verify", status_code=202)
def trigger_verification(project: ProjectSelection) -> JSONResponse:
    """Clone *project.repository* and run the full verification pipeline.

    Returns 202 immediately.  Poll ``GET /verify/status`` for completion.
    """
    global _state
    if _state.status == "running":
        return JSONResponse(
            status_code=409,
            content={"status": "already_running"},
        )

    _state = _VerificationState(status="running")

    thread = threading.Thread(
        target=_run_pipeline,
        args=(project.repository, project.branch or "main"),
        daemon=True,
    )
    thread.start()

    return JSONResponse(
        status_code=202,
        content={"status": "verification_started", "repository": project.repository},
    )


@router.get("/verify/status")
def verification_status() -> dict:
    """Return the current verification run state."""
    return {
        "status": _state.status,
        "error": _state.error,
        "trust_score_after": store.get_trust_score_after(),
    }
