from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.fixes.approval_flow import (
    FixApplicationError,
    apply_fix,
    rollback_fix,
)
from app.orchestration.pipeline import (
    run as run_orchestration_pipeline,
)
from app.storage import history_db, repository
from app.verification.trust_score import calculate_trust_score


router = APIRouter()


@router.post("/reverify/{contract_id}")
def reverify_contract(contract_id: str) -> dict:
    """Apply one approved fix and verify the real changed repository."""

    # ------------------------------------------------------------
    # 1. Contract must exist
    # ------------------------------------------------------------

    contract = repository.get_contract(contract_id)

    if contract is None:
        raise HTTPException(
            status_code=404,
            detail=f"Contract '{contract_id}' not found.",
        )

    # ------------------------------------------------------------
    # 2. Human approval is required
    # ------------------------------------------------------------

    if (
        contract.approvalStatus != "approved"
        or contract.approved is not True
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Contract '{contract_id}' must be approved "
                "before reverification."
            ),
        )

    # ------------------------------------------------------------
    # 3. Retrieve exact approved fix
    # ------------------------------------------------------------

    fix = repository.get_fix(contract_id)

    if fix is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No stored fix exists for contract "
                f"'{contract_id}'. Run verification first."
            ),
        )

    # ------------------------------------------------------------
    # 4. Retrieve repository context
    # ------------------------------------------------------------

    context = repository.get_verification_context()

    if context is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No verification context exists. "
                "Run verification first."
            ),
        )

    repository_path = Path(
        context.repository_path
    ).resolve()

    if not repository_path.is_dir():
        raise HTTPException(
            status_code=409,
            detail=(
                "The repository used for verification "
                "is no longer available."
            ),
        )

    backend_path = (
        Path(context.backend_path).resolve()
        if context.backend_path
        else None
    )

    # ------------------------------------------------------------
    # 5. Record trust score BEFORE modifying files
    # ------------------------------------------------------------

    current_contracts = repository.get_all_contracts()

    trust_score_before = calculate_trust_score(
        current_contracts
    )

    # ------------------------------------------------------------
    # 6. Physically apply the approved fix
    # ------------------------------------------------------------

    try:
        applied_fix = apply_fix(
            repository_path,
            fix,
        )

    except FixApplicationError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    # ------------------------------------------------------------
    # 7. Fresh verification from the changed filesystem
    # ------------------------------------------------------------

    try:
        fresh_result = run_orchestration_pipeline(
            repo_path=repository_path,
            backend_path=backend_path,
        )

    except Exception as exc:
        rollback_fix(applied_fix)

        raise HTTPException(
            status_code=500,
            detail=(
                "Fresh verification failed. "
                "The file change was rolled back."
            ),
        ) from exc

    fresh_contracts = fresh_result.contracts
    trust_score_after = fresh_result.trust_score

    # ------------------------------------------------------------
    # 8. Check whether the original issue still fails
    #
    # Contract IDs may change after editing the claim text,
    # so compare the verification area + source location.
    # ------------------------------------------------------------

    same_location = [
        candidate
        for candidate in fresh_contracts
        if (
            candidate.area == contract.area
            and candidate.source == contract.source
        )
    ]

    still_failing = any(
        candidate.status in {"fail", "warning"}
        for candidate in same_location
    )

    score_regressed = (
        trust_score_after < trust_score_before
    )

    # ------------------------------------------------------------
    # 9. Roll back if real verification rejects the edit
    # ------------------------------------------------------------

    if still_failing or score_regressed:
        rollback_fix(applied_fix)

        raise HTTPException(
            status_code=409,
            detail=(
                "The approved fix did not pass fresh verification. "
                "The file change was rolled back."
            ),
        )

    # ------------------------------------------------------------
    # 10. Mark the freshly verified equivalent contract
    #
    # The ID may be different because claim text changed.
    # ------------------------------------------------------------

    persisted_contracts = []

    for candidate in fresh_contracts:
        if (
            candidate.area == contract.area
            and candidate.source == contract.source
            and candidate.status == "pass"
        ):
            candidate = candidate.model_copy(
                update={
                    "approvalStatus": "approved",
                    "approved": True,
                    "reverified": True,
                }
            )

        persisted_contracts.append(candidate)

    # ------------------------------------------------------------
    # 11. Persist REAL post-edit results
    # ------------------------------------------------------------

    repository.replace_contracts(
        persisted_contracts
    )

    repository.replace_fixes(
        fresh_result.fixes
    )

    history_branch = (
        history_db.get_latest_branch(
            str(repository_path)
        )
        or "main"
    )

    history_db.save_history_run(
        repository=str(repository_path),
        branch=history_branch,
        run_type="reverify",
        trust_score=trust_score_after,
        summary=fresh_result.summary,
    )

    # ------------------------------------------------------------
    # 12. Build evidence
    # ------------------------------------------------------------

    verified_match = next(
        (
            candidate
            for candidate in persisted_contracts
            if (
                candidate.area == contract.area
                and candidate.source == contract.source
                and candidate.status == "pass"
            )
        ),
        None,
    )

    if verified_match is not None:
        evidence = verified_match.evidence
        resulting_status = verified_match.status
    else:
        evidence = (
            f"Approved fix was written to {fix.target_file}. "
            "Fresh verification no longer reports the "
            "original discrepancy."
        )
        resulting_status = "pass"

    # ------------------------------------------------------------
    # 13. Return real verification result
    # ------------------------------------------------------------

    return {
        "contract_id": contract_id,
        "status": resulting_status,
        "approvalStatus": "approved",
        "approved": True,
        "reverified": True,
        "verified_from_disk": True,
        "file_changed": applied_fix.changed,
        "target_file": fix.target_file,
        "evidence": evidence,
        "summary": fresh_result.summary.model_dump(),
        "trust_score_before": trust_score_before,
        "trust_score": trust_score_after,
        "applied_fix_count": 1,
    }
