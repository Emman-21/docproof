from __future__ import annotations

from app.core.models import DocumentationContract, Severity
from app.verification.config_verifier import ConfigVerificationResult


def build_documentation_contract(
    result: ConfigVerificationResult,
    *,
    contract_id: str,
    evidence_file: str,
    suggested_fix: str = "",
    severity: Severity | None = None,
) -> DocumentationContract:
    return DocumentationContract(
        id=contract_id,
        area=result.claim.area,
        source=result.claim.source,
        claim=result.claim.claim,
        expected=result.claim.expected,
        actual=result.actual,
        status=result.status,
        evidence=result.evidence,
        evidenceFile=evidence_file,
        suggested_fix=suggested_fix,
        approvalStatus="pending",
        approved=False,
        reverified=False,
        severity=severity,
    )