from app.core.models import DocumentationContract
from app.verification.config_verifier import ConfigVerificationResult
from app.verification.doc_parser import ExtractedClaim
from app.verification.evidence_builder import build_documentation_contract


def test_build_failed_config_contract():
    claim = ExtractedClaim(
        area="config_env",
        claim="- `JWT_SECRET` -- required. Token signing key.",
        expected="JWT_SECRET required",
        source="README.md#L35",
        extraction_method="regex",
    )

    result = ConfigVerificationResult(
        claim=claim,
        actual="JWT_SECRET missing",
        status="fail",
        evidence="JWT_SECRET was not found in .env.example",
    )

    contract = build_documentation_contract(
        result,
        contract_id="config-001",
        evidence_file=".env.example",
        suggested_fix="Add JWT_SECRET to .env.example",
        severity="high",
    )

    assert isinstance(contract, DocumentationContract)

    assert contract.id == "config-001"
    assert contract.area == "config_env"
    assert contract.source == "README.md#L35"
    assert contract.claim == claim.claim
    assert contract.expected == "JWT_SECRET required"
    assert contract.actual == "JWT_SECRET missing"
    assert contract.status == "fail"

    assert contract.evidence == "JWT_SECRET was not found in .env.example"
    assert contract.evidenceFile == ".env.example"

    assert contract.suggested_fix == "Add JWT_SECRET to .env.example"

    assert contract.approvalStatus == "pending"
    assert contract.approved is False
    assert contract.reverified is False
    assert contract.severity == "high"


def test_build_passing_config_contract():
    claim = ExtractedClaim(
        area="config_env",
        claim="- `PORT` -- default is 3000",
        expected="PORT=3000",
        source="README.md#L32",
        extraction_method="regex",
    )

    result = ConfigVerificationResult(
        claim=claim,
        actual="PORT=3000",
        status="pass",
        evidence="PORT in .env.example is 3000; documentation expects 3000",
    )

    contract = build_documentation_contract(
        result,
        contract_id="config-002",
        evidence_file=".env.example",
    )

    assert contract.status == "pass"
    assert contract.actual == "PORT=3000"
    assert contract.evidenceFile == ".env.example"

    assert contract.suggested_fix == ""
    assert contract.approvalStatus == "pending"
    assert contract.approved is False
    assert contract.reverified is False
    assert contract.severity is None