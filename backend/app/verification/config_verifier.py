from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.core.models import ContractResultStatus
from app.verification.doc_parser import ExtractedClaim


@dataclass
class ConfigVerificationResult:
    claim: ExtractedClaim
    actual: str
    status: ContractResultStatus
    evidence: str


def _parse_expected(expected: str) -> tuple[str, str | None, bool]:
    expected = expected.strip()

    if expected.endswith(" required"):
        key = expected[: -len(" required")].strip()
        return key, None, True

    if "=" in expected:
        key, value = expected.split("=", 1)
        return key.strip(), value.strip(), False

    return expected, None, False


def verify_config_claim(
    claim: ExtractedClaim,
    actual_env: Mapping[str, str],
    *,
    evidence_file: str,
) -> ConfigVerificationResult:
    if claim.area != "config_env":
        raise ValueError("verify_config_claim only accepts config_env claims")

    key, expected_value, required = _parse_expected(claim.expected)

    if key not in actual_env:
        return ConfigVerificationResult(
            claim=claim,
            actual=f"{key} missing",
            status="fail",
            evidence=f"{key} was not found in {evidence_file}",
        )

    actual_value = actual_env[key]

    if required:
        return ConfigVerificationResult(
            claim=claim,
            actual=f"{key}={actual_value}",
            status="pass",
            evidence=f"{key} was found in {evidence_file}",
        )

    if expected_value is not None:
        status: ContractResultStatus = (
            "pass" if actual_value == expected_value else "fail"
        )

        return ConfigVerificationResult(
            claim=claim,
            actual=f"{key}={actual_value}",
            status=status,
            evidence=(
                f"{key} in {evidence_file} is {actual_value}; "
                f"documentation expects {expected_value}"
            ),
        )

    return ConfigVerificationResult(
        claim=claim,
        actual=f"{key}={actual_value}",
        status="pass",
        evidence=f"{key} was found in {evidence_file}",
    )


def verify_config_claims(
    claims: list[ExtractedClaim],
    actual_env: Mapping[str, str],
    *,
    evidence_file: str,
) -> list[ConfigVerificationResult]:
    return [
        verify_config_claim(
            claim,
            actual_env,
            evidence_file=evidence_file,
        )
        for claim in claims
    ]