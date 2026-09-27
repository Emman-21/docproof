from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.storage import repository
from app.verification.trust_score import calculate_trust_score
import app.orchestration.subagents.reverification_agent as reverification_agent


router = APIRouter()


@router.post("/reverify/{contract_id}")
def reverify_contract(contract_id: str) -> dict:
    """Reverify one previously approved documentation fix.

    The endpoint only allows contracts explicitly approved by the user.
    It retrieves the stored FixSuggestion and verification context from
    the most recent verification run, then invokes the reverification
    agent for that approved fix.
    """

    # ------------------------------------------------------------
    # Contract must exist
    # ------------------------------------------------------------

    contract = repository.get_contract(contract_id)

    if contract is None:
        raise HTTPException(
            status_code=404,
            detail=f"Contract '{contract_id}' not found.",
        )

    # ------------------------------------------------------------
    # Human approval is required
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
    # Retrieve the exact generated fix
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
    # Retrieve repository context
    # ------------------------------------------------------------

    context = repository.get_verification_context()

    if context is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No verification context exists. "
                "Run verification before reverifying."
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

    # ------------------------------------------------------------
    # Run reverification using ONLY the approved fix
    # ------------------------------------------------------------

    current_contracts = repository.get_all_contracts()

    result = reverification_agent.run(
        approved_fixes=[fix],
        contracts=current_contracts,
        repo_path=repository_path,
    )

    # ------------------------------------------------------------
    # Persist post-reverification contracts
    # ------------------------------------------------------------

    repository.replace_contracts(
        result.contracts
    )

    updated_contract = repository.get_contract(
        contract_id
    )

    if updated_contract is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Reverification completed but the target "
                "contract could not be recovered."
            ),
        )

    updated_trust_score = calculate_trust_score(
        result.contracts
    )

    # ------------------------------------------------------------
    # Return useful frontend result
    # ------------------------------------------------------------

    return {
        "contract_id": updated_contract.id,
        "status": updated_contract.status,
        "approvalStatus": (
            updated_contract.approvalStatus
        ),
        "approved": updated_contract.approved,
        "reverified": updated_contract.reverified,
        "evidence": updated_contract.evidence,
        "summary": result.summary.model_dump(),
        "trust_score": updated_trust_score,
        "applied_fix_count": (
            result.applied_fix_count
        ),
        "all_pass": result.all_pass,
    }
