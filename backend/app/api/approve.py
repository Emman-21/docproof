from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.models import DocumentationContract
from app.storage import repository, db

router = APIRouter()


@router.post("/approve/{contract_id}", response_model=DocumentationContract)
def approve_contract(contract_id: str) -> DocumentationContract:
    contract = repository.approve_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_id}' not found.")
    return contract


@router.post("/reject/{contract_id}", response_model=DocumentationContract)
def reject_contract(contract_id: str) -> DocumentationContract:
    contract = repository.reject_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_id}' not found.")
    return contract


@router.post("/reverify/{contract_id}", response_model=DocumentationContract)
def reverify_contract(contract_id: str) -> DocumentationContract:
    """Mark an approved contract as reverified and flip its status to pass.

    This is called after the human approves a fix and the frontend re-verification
    animation completes. It commits the fix result into the backend store so the
    trust score and contract state are permanently updated.
    """
    contract = db.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_id}' not found.")
    if not contract.approved:
        raise HTTPException(status_code=409, detail=f"Contract '{contract_id}' has not been approved yet.")

    updated = db.update_contract(
        contract_id,
        status="pass",
        reverified=True,
        approvalStatus="approved",
        claim=contract.suggested_fix or contract.claim,
        expected=contract.actual,
        evidence=f"Fix applied and verified: {contract.suggested_fix or contract.actual}",
    )
    return updated
