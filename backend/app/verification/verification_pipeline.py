from __future__ import annotations

from dataclasses import dataclass

from app.core.models import DocumentationContract, VerificationSummary
from app.verification.config_verifier import verify_config_claims
from app.verification.doc_parser import ParseRequest, parse_documentation
from app.verification.evidence_builder import build_documentation_contract
from app.verification.trust_score import calculate_trust_score


@dataclass
class ConfigVerificationPipelineResult:
    contracts: list[DocumentationContract]
    summary: VerificationSummary
    trust_score: float


def run_config_verification(
    *,
    documentation_path: str,
    documentation_content: str,
    env_path: str,
    env_content: str,
) -> ConfigVerificationPipelineResult:
    documentation_claims = [
        claim
        for claim in parse_documentation(
            ParseRequest(
                source_path=documentation_path,
                content=documentation_content,
            )
        )
        if claim.area == "config_env"
    ]

    env_claims = [
        claim
        for claim in parse_documentation(
            ParseRequest(
                source_path=env_path,
                content=env_content,
            )
        )
        if claim.area == "config_env"
    ]

    actual_env: dict[str, str] = {}

    for claim in env_claims:
        if "=" not in claim.expected:
            continue

        key, value = claim.expected.split("=", 1)
        actual_env[key] = value

    verification_results = verify_config_claims(
        documentation_claims,
        actual_env,
        evidence_file=env_path,
    )

    contracts = [
        build_documentation_contract(
            result,
            contract_id=f"config-{index:03d}",
            evidence_file=env_path,
        )
        for index, result in enumerate(verification_results, start=1)
    ]

    summary = VerificationSummary(
        total=len(contracts),
        passed=sum(contract.status == "pass" for contract in contracts),
        failed=sum(contract.status == "fail" for contract in contracts),
        warnings=sum(contract.status == "warning" for contract in contracts),
    )

    return ConfigVerificationPipelineResult(
        contracts=contracts,
        summary=summary,
        trust_score=calculate_trust_score(contracts),
    )