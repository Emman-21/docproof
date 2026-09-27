from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.models import ProjectSelection
from app.orchestration.pipeline import run as run_orchestration_pipeline
from app.storage import history_db, repository

router = APIRouter()


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
    )


@router.get("/verify/status")
def verification_status() -> dict:
    """Return whether a verification result is available."""
    contracts = repository.get_all_contracts()
    return {
        "status": "complete" if contracts else "idle",
    }
