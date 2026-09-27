from __future__ import annotations

<<<<<<< HEAD
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
=======
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.models import ProjectSelection
from app.orchestration.pipeline import run as run_orchestration_pipeline
from app.storage import history_db, repository

>>>>>>> 370c285035238c71b5cfd94c853a9733d25c697f

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

<<<<<<< HEAD
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
=======
@router.post("/verify")
def trigger_verification(
    project: ProjectSelection,
) -> JSONResponse:
    """Run DocProof verification for a local repository.

    Local repositories are processed immediately through the full
    DocProof orchestration pipeline.

    Remote or unavailable repositories retain the queued behavior until
    repository fetching or cloning is implemented.
    """
    repository_path = Path(project.repository).resolve()

    if not repository_path.is_dir():
        return JSONResponse(
            status_code=202,
            content={
                "status": "verification_queued",
                "repository": project.repository,
            },
        )

    if not project.documentation:
        raise HTTPException(
            status_code=400,
            detail="At least one documentation file is required",
        )

    for documentation_file in project.documentation:
        documentation_path = repository_path / documentation_file

        if not documentation_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    "Documentation file not found: "
                    f"{documentation_file}"
                ),
            )

    backend_path: Path | None = repository_path / "backend"

    if not backend_path.is_dir():
        sibling_backend = repository_path.parent / "backend"

        if sibling_backend.is_dir():
            backend_path = sibling_backend
        else:
            backend_path = None

    result = run_orchestration_pipeline(
        repo_path=repository_path,
        backend_path=backend_path,
    )

    repository.replace_contracts(result.contracts)
    repository.replace_fixes(result.fixes)

    repository.set_verification_context(
        repository_path=str(repository_path),
        backend_path=(
            str(backend_path)
            if backend_path is not None
            else None
        ),
    )

    history_db.save_history_run(
        repository=str(repository_path),
        branch=project.branch or "main",
        run_type="verify",
        trust_score=result.trust_score,
        summary=result.summary,
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "completed",
            "repository": project.repository,
            "contracts": [
                contract.model_dump()
                for contract in result.contracts
            ],
            "fixes": [
                fix.model_dump()
                for fix in result.fixes
            ],
            "reverification": result.reverification.model_dump(),
            "summary": result.summary.model_dump(),
            "trust_score": result.trust_score,
            "trust_score_after": result.trust_score_after,
            "elapsed_seconds": result.elapsed_seconds,
        },
>>>>>>> 370c285035238c71b5cfd94c853a9733d25c697f
    )


@router.get("/verify/status")
def verification_status() -> dict:
    """Return the current verification run state."""
    return {
        "status": _state.status,
        "error": _state.error,
        "trust_score_after": store.get_trust_score_after(),
    }
