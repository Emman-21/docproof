"""Repository fa?ade over the in-memory storage layer.

API handlers and services should use this module rather than accessing
db.py directly. This keeps the storage implementation replaceable.
"""
from __future__ import annotations

from typing import List, Optional

from app.core.models import (
    DocumentationContract,
    FixSuggestion,
    VerificationContext,
)
from app.storage import db


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

def get_all_contracts() -> List[DocumentationContract]:
    return db.all_contracts()


def get_contract(
    contract_id: str,
) -> Optional[DocumentationContract]:
    return db.get_contract(contract_id)


def replace_contracts(
    contracts: List[DocumentationContract],
) -> None:
    """Replace current contracts with freshly verified results."""
    db.replace_contracts(contracts)


def approve_contract(
    contract_id: str,
) -> Optional[DocumentationContract]:
    return db.update_contract(
        contract_id,
        approvalStatus="approved",
        approved=True,
    )


def reject_contract(
    contract_id: str,
) -> Optional[DocumentationContract]:
    return db.update_contract(
        contract_id,
        approvalStatus="rejected",
        approved=False,
    )


# ---------------------------------------------------------------------------
# Fix suggestions
# ---------------------------------------------------------------------------

def replace_fixes(
    fixes: List[FixSuggestion],
) -> None:
    """Replace fix suggestions with results from the latest verification."""
    db.replace_fixes(fixes)


def get_all_fixes() -> List[FixSuggestion]:
    return db.all_fixes()


def get_fix(
    contract_id: str,
) -> Optional[FixSuggestion]:
    return db.get_fix(contract_id)


# ---------------------------------------------------------------------------
# Verification context
# ---------------------------------------------------------------------------

def set_verification_context(
    repository_path: str,
    backend_path: Optional[str] = None,
) -> VerificationContext:
    """Remember repository information needed for later reverification."""
    context = VerificationContext(
        repository_path=repository_path,
        backend_path=backend_path,
    )

    db.set_verification_context(context)

    return context


def get_verification_context() -> Optional[VerificationContext]:
    return db.get_verification_context()
