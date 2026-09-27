from app.verification.verification_pipeline import run_config_verification


def test_run_config_verification_builds_contracts_summary_and_score():
    readme = """# Widget Service

## Environment Variables

- `PORT` -- default is 3000
- `DEBUG` defaults to false
- `DATABASE_URL` is required. PostgreSQL connection string.
- `JWT_SECRET` -- required. Token signing key.
"""

    env_example = """PORT=3000
DEBUG=false
DATABASE_URL=postgres://localhost/widgets
"""

    result = run_config_verification(
        documentation_path="README.md",
        documentation_content=readme,
        env_path=".env.example",
        env_content=env_example,
    )

    assert len(result.contracts) == 4

    contracts = {
        contract.expected: contract
        for contract in result.contracts
    }

    assert contracts["PORT=3000"].status == "pass"
    assert contracts["PORT=3000"].actual == "PORT=3000"

    assert contracts["DEBUG=false"].status == "pass"
    assert contracts["DEBUG=false"].actual == "DEBUG=false"

    assert contracts["DATABASE_URL required"].status == "pass"
    assert (
        contracts["DATABASE_URL required"].actual
        == "DATABASE_URL=postgres://localhost/widgets"
    )

    assert contracts["JWT_SECRET required"].status == "fail"
    assert contracts["JWT_SECRET required"].actual == "JWT_SECRET missing"

    assert all(
        contract.evidenceFile == ".env.example"
        for contract in result.contracts
    )

    assert result.summary.total == 4
    assert result.summary.passed == 3
    assert result.summary.failed == 1
    assert result.summary.warnings == 0

    assert result.trust_score == 75.0