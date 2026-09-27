from app.verification.doc_parser import ExtractedClaim
from app.verification.config_verifier import (
    ConfigVerificationResult,
    verify_config_claim,
    verify_config_claims,
)


def make_claim(expected: str, claim: str | None = None) -> ExtractedClaim:
    return ExtractedClaim(
        area="config_env",
        claim=claim or expected,
        expected=expected,
        source="README.md#L1",
        extraction_method="regex",
    )


def test_matching_value_passes():
    claim = make_claim("PORT=3000")

    result = verify_config_claim(
        claim,
        {
            "PORT": "3000",
            "DEBUG": "false",
        },
        evidence_file=".env.example",
    )

    assert isinstance(result, ConfigVerificationResult)
    assert result.status == "pass"
    assert result.actual == "PORT=3000"
    assert ".env.example" in result.evidence


def test_wrong_value_fails():
    claim = make_claim("PORT=3000")

    result = verify_config_claim(
        claim,
        {"PORT": "8080"},
        evidence_file=".env.example",
    )

    assert result.status == "fail"
    assert result.actual == "PORT=8080"


def test_required_variable_present_passes():
    claim = make_claim("DATABASE_URL required")

    result = verify_config_claim(
        claim,
        {"DATABASE_URL": "postgres://localhost/widgets"},
        evidence_file=".env.example",
    )

    assert result.status == "pass"
    assert result.actual == "DATABASE_URL=postgres://localhost/widgets"


def test_required_variable_missing_fails():
    claim = make_claim("JWT_SECRET required")

    result = verify_config_claim(
        claim,
        {
            "PORT": "3000",
            "DEBUG": "false",
            "DATABASE_URL": "postgres://localhost/widgets",
        },
        evidence_file=".env.example",
    )

    assert result.status == "fail"
    assert result.actual == "JWT_SECRET missing"
    assert "JWT_SECRET" in result.evidence


def test_missing_variable_with_expected_value_fails():
    claim = make_claim("DEBUG=false")

    result = verify_config_claim(
        claim,
        {},
        evidence_file=".env.example",
    )

    assert result.status == "fail"
    assert result.actual == "DEBUG missing"


def test_verifier_rejects_non_config_claim():
    claim = ExtractedClaim(
        area="commands",
        claim="npm test",
        expected="npm test",
        source="README.md#L1",
        extraction_method="regex",
    )

    try:
        verify_config_claim(
            claim,
            {"PORT": "3000"},
            evidence_file=".env.example",
        )
    except ValueError as exc:
        assert "config_env" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-config claim")


def test_verify_multiple_config_claims():
    claims = [
        make_claim("PORT=3000"),
        make_claim("DEBUG=false"),
        make_claim("DATABASE_URL required"),
        make_claim("JWT_SECRET required"),
    ]

    actual_env = {
        "PORT": "3000",
        "DEBUG": "false",
        "DATABASE_URL": "postgres://localhost/widgets",
    }

    results = verify_config_claims(
        claims,
        actual_env,
        evidence_file=".env.example",
    )

    assert len(results) == 4

    assert [result.status for result in results] == [
        "pass",
        "pass",
        "pass",
        "fail",
    ]

    assert results[0].actual == "PORT=3000"
    assert results[1].actual == "DEBUG=false"
    assert results[2].actual == "DATABASE_URL=postgres://localhost/widgets"
    assert results[3].actual == "JWT_SECRET missing"