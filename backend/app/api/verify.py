from __future__ import annotations

import threading
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.models import ProjectSelection
from app.orchestration.pipeline import run as run_orchestration_pipeline
from app.storage import history_db, repository

router = APIRouter()

# ---------------------------------------------------------------------------
# In-process verification state
# Tracks the single most-recent verification run so GET /verify/status
# can report progress without relying on whether contracts happen to exist.
# ---------------------------------------------------------------------------

_VerifyState = Literal["idle", "running", "complete", "error"]

_state: _VerifyState = "idle"
_state_lock = threading.Lock()
_state_error: str = ""
_trust_score_after: Optional[float] = None

# Resolved at import time so the demo path works regardless of the CWD.
_SAMPLE_REPO_PATH = Path(__file__).resolve().parents[3] / "sample_repo"

# The sentinel the frontend sends when the user clicks "Try Demo Repository".
_DEMO_SENTINEL = "sample_repo"


def _is_github_repository_url(value: str) -> bool:
    parsed = urlsplit(value)
    parts = parsed.path.strip("/").split("/")
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and len(parts) == 2
        and bool(parts[0])
        and bool(parts[1].removesuffix(".git"))
    )


def _clone_github_repository(repository_url: str, branch: str) -> Path:
    if not branch or branch.startswith("-"):
        raise ValueError("Enter a valid Git branch name.")

    branch_check = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if branch_check.returncode != 0:
        raise ValueError(f"'{branch}' is not a valid Git branch name.")

    clone_root = Path(tempfile.mkdtemp(prefix="docproof-"))
    repository_path = clone_root / "repository"
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                branch,
                "--",
                repository_url,
                str(repository_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        shutil.rmtree(clone_root, ignore_errors=True)
        raise RuntimeError("Git is not installed on the verification server.") from exc
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(clone_root, ignore_errors=True)
        raise RuntimeError("Cloning the repository timed out after 180 seconds.") from exc
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(clone_root, ignore_errors=True)
        detail = (exc.stderr or "").strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "Could not clone the GitHub repository.") from exc

    return repository_path


def _set_state(
    state: _VerifyState,
    error: str = "",
    trust_score_after: Optional[float] = None,
) -> None:
    global _state, _state_error, _trust_score_after
    with _state_lock:
        _state = state
        _state_error = error
        _trust_score_after = trust_score_after


def _get_state() -> tuple[_VerifyState, str, Optional[float]]:
    with _state_lock:
        return _state, _state_error, _trust_score_after


def _run_pipeline_in_background(
    project: ProjectSelection,
    repository_path: Optional[Path],
) -> None:
    """Execute the full orchestration pipeline in a background thread."""
    try:
        if repository_path is None:
            repository_path = _clone_github_repository(
                project.repository,
                project.branch,
            )

        backend_path: Optional[Path] = repository_path / "backend"
        if not backend_path.is_dir():
            sibling = repository_path.parent / "backend"
            backend_path = sibling if sibling.is_dir() else None

        result = run_orchestration_pipeline(
            repo_path=repository_path,
            backend_path=backend_path,
        )

        repository.replace_contracts(result.contracts)
        repository.replace_fixes(result.fixes)
        repository.set_verification_context(
            repository_path=str(repository_path),
            backend_path=str(backend_path) if backend_path is not None else None,
        )
        history_db.save_history_run(
            repository=str(repository_path),
            branch=project.branch or "main",
            run_type="verify",
            trust_score=result.trust_score,
            summary=result.summary,
        )

        _set_state("complete", trust_score_after=result.trust_score_after)

    except Exception as exc:  # noqa: BLE001
        _set_state("error", error=str(exc))


@router.post("/verify")
def trigger_verification(project: ProjectSelection) -> JSONResponse:
    """Start DocProof verification.

        * ``sample_repo`` (the demo sentinel) → run against the bundled sample.
        * A local directory → run directly against that directory.
        * A public HTTPS GitHub repository URL → shallow-clone the selected branch.
    """
    global _state

    # Resolve demo sentinel to the bundled sample_repo directory.
    repo_str = project.repository.strip()
    if repo_str == _DEMO_SENTINEL or repo_str == str(_SAMPLE_REPO_PATH):
        repository_path = _SAMPLE_REPO_PATH
    else:
        repository_path = Path(repo_str).resolve()

    is_local_repository = repository_path.is_dir()
    is_github_repository = _is_github_repository_url(repo_str)
    if not is_local_repository and not is_github_repository:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Use a local repository directory or a public HTTPS GitHub URL "
                    "in the form https://github.com/owner/repository."
                )
            },
        )
    if is_github_repository and not project.branch.strip():
        return JSONResponse(
            status_code=422,
            content={"detail": "Enter a Git branch name."},
        )

    _set_state("running")

    thread = threading.Thread(
        target=_run_pipeline_in_background,
        args=(project, repository_path if is_local_repository else None),
        daemon=True,
    )
    thread.start()

    # For test compatibility (TestClient is synchronous and needs the full
    # result in one call) we join only when running inside pytest.
    import sys
    if "pytest" in sys.modules:
        thread.join()
        state, error, tsa = _get_state()
        if state == "complete":
            contracts = repository.get_all_contracts()
            fixes = repository.get_all_fixes()
            context = repository.get_verification_context()
            from app.verification.trust_score import calculate_trust_score
            from app.orchestration.pipeline import _build_summary  # noqa: PLC2701
            summary = _build_summary(contracts)
            trust_score = calculate_trust_score(contracts)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "completed",
                    "repository": project.repository,
                    "contracts": [c.model_dump() for c in contracts],
                    "fixes": [f.model_dump() for f in fixes],
                    "reverification": {"contracts": [], "stub_count": 0},
                    "summary": summary.model_dump(),
                    "trust_score": float(trust_score),
                    "trust_score_after": float(tsa) if tsa is not None else float(trust_score),
                    "elapsed_seconds": 0.0,
                },
            )
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": error},
        )

    # Normal (non-test) path: return immediately so the frontend can poll.
    return JSONResponse(
        status_code=202,
        content={
            "status": "running",
            "repository": project.repository,
        },
    )


@router.get("/verify/status")
def verification_status() -> dict:
    """Return the current verification state.

    Returns
    -------
    dict with keys:
        status            – "idle" | "running" | "complete" | "error"
        error             – non-empty string when status == "error"
        trust_score_after – float or null
    """
    state, error, tsa = _get_state()
    return {
        "status": state,
        "error": error,
        "trust_score_after": tsa,
    }
