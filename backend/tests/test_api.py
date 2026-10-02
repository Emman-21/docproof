"""API integration tests for all Milestone 1 endpoints.

Uses FastAPI's synchronous TestClient throughout.
The in-memory store is reseeded before every test via the autouse fixture,
so tests are fully independent of execution order.

Implementation requirements this file encodes:
  - store.reset() must restore the full original seed dataset, not just clear it.
  - POST /approve/{id} and POST /reject/{id} return the updated DocumentationContract.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import db as store
from app.storage import repository


@pytest.fixture(autouse=True)
def reseed_store():
    """Reseed the in-memory store before every test."""
    store.reset()
    yield


client = TestClient(app)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_200():
    r = client.get("/health")
    assert r.status_code == 200


def test_health_returns_status_ok():
    r = client.get("/health")
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /contracts
# ---------------------------------------------------------------------------

def test_get_contracts_returns_200():
    r = client.get("/contracts")
    assert r.status_code == 200


def test_get_contracts_returns_list():
    r = client.get("/contracts")
    assert isinstance(r.json(), list)


def test_get_contracts_list_is_not_empty():
    r = client.get("/contracts")
    assert len(r.json()) > 0


# ---------------------------------------------------------------------------
# GET /contracts/{id}
# ---------------------------------------------------------------------------

def test_get_contract_dp001_returns_200():
    r = client.get("/contracts/DP-001")
    assert r.status_code == 200


def test_get_contract_dp001_id_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["id"] == "DP-001"


def test_get_contract_dp001_area_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["area"] == "runtime_requirements"


def test_get_contract_dp001_source_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["source"] == "README.md#L42"


def test_get_contract_dp001_claim_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["claim"] == "Requires Node.js 18+"


def test_get_contract_dp001_expected_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["expected"] == "Node.js >=18"


def test_get_contract_dp001_actual_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["actual"] == "Node.js >=20"


def test_get_contract_dp001_status_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["status"] == "fail"


def test_get_contract_dp001_evidence_field():
    r = client.get("/contracts/DP-001")
    assert isinstance(r.json()["evidence"], str)
    assert len(r.json()["evidence"]) > 0


def test_get_contract_dp001_suggested_fix_snake_case():
    """Frontend expects suggested_fix (snake_case), never suggestedFix."""
    body = client.get("/contracts/DP-001").json()
    assert "suggested_fix" in body
    assert "suggestedFix" not in body


def test_get_contract_dp001_approvalStatus_camelcase():
    """Frontend expects approvalStatus (camelCase), never approval_status."""
    body = client.get("/contracts/DP-001").json()
    assert "approvalStatus" in body
    assert "approval_status" not in body
    assert body["approvalStatus"] == "pending"


def test_get_contract_dp001_approved_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["approved"] is False


def test_get_contract_dp001_reverified_field():
    r = client.get("/contracts/DP-001")
    assert r.json()["reverified"] is False


def test_get_contract_dp001_optional_evidence_field_names():
    """Frontend expects camelCase evidence field names, never snake_case."""
    body = client.get("/contracts/DP-001").json()

    assert "evidenceFile" in body
    assert "evidence_file" not in body

    assert "evidenceLines" in body
    assert "evidence_lines" not in body

    assert "evidenceSnippet" in body
    assert "evidence_snippet" not in body


def test_get_contract_not_found_returns_404():
    r = client.get("/contracts/NONEXISTENT")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /verify
# ---------------------------------------------------------------------------

def test_verify_github_repository_returns_completed_results(monkeypatch):
    sample_repo = Path(__file__).resolve().parents[2] / "sample_repo"
    cloned: list[tuple[str, str]] = []

    def clone_repository(repository_url: str, branch: str) -> Path:
        cloned.append((repository_url, branch))
        return sample_repo

    monkeypatch.setattr(
        "app.api.verify._clone_github_repository",
        clone_repository,
    )
    payload = {
        "repository": "https://github.com/example/repo",
        "branch": "main",
        "documentation": ["README.md"],
    }

    r = client.post("/verify", json=payload)

    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert cloned == [(payload["repository"], "main")]


def test_verify_rejects_unsupported_repository_url():
    payload = {
        "repository": "https://example.com/team/repo",
        "branch": "main",
        "documentation": ["README.md"],
    }

    r = client.post("/verify", json=payload)

    assert r.status_code == 422
    assert "public HTTPS GitHub URL" in r.json()["detail"]


def test_verify_missing_body_returns_422():
    r = client.post("/verify", json={})

    assert r.status_code == 422


def test_verify_local_sample_repo_returns_completed_results():
    sample_repo = Path(__file__).resolve().parents[2] / "sample_repo"

    payload = {
        "repository": str(sample_repo),
        "branch": "main",
        "documentation": ["README.md"],
    }

    response = client.post("/verify", json=payload)

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "completed"

    contracts = body["contracts"]

    assert len(contracts) > 0

    assert {
        contract["area"]
        for contract in contracts
    } == {
        "runtime_requirements",
        "commands",
        "config_env",
        "api_docs",
    }

    assert body["summary"]["total"] == len(contracts)

    assert (
        body["summary"]["passed"]
        + body["summary"]["failed"]
        + body["summary"]["warnings"]
        == body["summary"]["total"]
    )

    assert isinstance(body["fixes"], list)

    assert isinstance(body["trust_score"], float)
    assert isinstance(body["trust_score_after"], float)

    assert 0.0 <= body["trust_score"] <= 100.0
    assert 0.0 <= body["trust_score_after"] <= 100.0

    assert body["trust_score_after"] >= body["trust_score"]

    assert "reverification" in body
    assert "elapsed_seconds" in body


def test_verify_local_sample_repo_persists_verified_contracts():
    sample_repo = Path(__file__).resolve().parents[2] / "sample_repo"

    payload = {
        "repository": str(sample_repo),
        "branch": "main",
        "documentation": ["README.md"],
    }

    verify_response = client.post("/verify", json=payload)

    assert verify_response.status_code == 200

    verified_contracts = verify_response.json()["contracts"]

    contracts_response = client.get("/contracts")

    assert contracts_response.status_code == 200

    persisted_contracts = contracts_response.json()

    assert len(persisted_contracts) == len(verified_contracts)

    assert {
        contract["id"]
        for contract in persisted_contracts
    } == {
        contract["id"]
        for contract in verified_contracts
    }

    assert len(persisted_contracts) > 0

    assert {
        contract["area"]
        for contract in persisted_contracts
    } == {
        "runtime_requirements",
        "commands",
        "config_env",
        "api_docs",
    }


def test_verify_persists_generated_fixes():
    sample_repo = Path(__file__).resolve().parents[2] / "sample_repo"

    payload = {
        "repository": str(sample_repo),
        "branch": "main",
        "documentation": ["README.md"],
    }

    response = client.post("/verify", json=payload)

    assert response.status_code == 200

    fixes = response.json()["fixes"]

    assert len(fixes) > 0

    for fix in fixes:
        stored_fix = repository.get_fix(
            fix["contract_id"]
        )

        assert stored_fix is not None
        assert stored_fix.contract_id == fix["contract_id"]
        assert stored_fix.target_file == fix["target_file"]


def test_verify_persists_verification_context():
    sample_repo = Path(__file__).resolve().parents[2] / "sample_repo"

    payload = {
        "repository": str(sample_repo),
        "branch": "main",
        "documentation": ["README.md"],
    }

    response = client.post("/verify", json=payload)

    assert response.status_code == 200

    context = repository.get_verification_context()

    assert context is not None

    assert (
        Path(context.repository_path)
        == sample_repo.resolve()
    )


# ---------------------------------------------------------------------------
# POST /approve/{id}
# ---------------------------------------------------------------------------

def test_approve_dp001_returns_200():
    r = client.post("/approve/DP-001")

    assert r.status_code == 200


def test_approve_dp001_sets_approvalStatus():
    body = client.post("/approve/DP-001").json()

    assert body["approvalStatus"] == "approved"


def test_approve_dp001_sets_approved_true():
    body = client.post("/approve/DP-001").json()

    assert body["approved"] is True


def test_approve_returns_full_contract():
    """Endpoint must return the full updated DocumentationContract, not just a status."""
    body = client.post("/approve/DP-001").json()

    assert body["id"] == "DP-001"
    assert body["status"] == "fail"


def test_approve_not_found_returns_404():
    r = client.post("/approve/NONEXISTENT")

    assert r.status_code == 404


def test_approve_persists_across_requests():
    client.post("/approve/DP-001")

    body = client.get("/contracts/DP-001").json()

    assert body["approvalStatus"] == "approved"
    assert body["approved"] is True


# ---------------------------------------------------------------------------
# POST /reverify/{id}
# ---------------------------------------------------------------------------

def test_reverify_nonexistent_contract_returns_404():
    response = client.post("/reverify/NONEXISTENT")

    assert response.status_code == 404


def test_reverify_pending_contract_returns_409():
    response = client.post("/reverify/DP-001")

    assert response.status_code == 409


def test_reverify_rejected_contract_returns_409():
    reject_response = client.post("/reject/DP-001")

    assert reject_response.status_code == 200

    response = client.post("/reverify/DP-001")

    assert response.status_code == 409


def test_reverify_approved_verified_contract_accepts_request(
    tmp_path: Path,
):
    import shutil

    source_root = Path(__file__).resolve().parents[2]

    source_sample = source_root / "sample_repo"
    source_backend = source_root / "backend"

    workspace = tmp_path / "workspace"

    sample_repo = workspace / "sample_repo"
    backend_copy = workspace / "backend"

    shutil.copytree(
        source_sample,
        sample_repo,
    )

    shutil.copytree(
        source_backend / "app",
        backend_copy / "app",
    )

    verify_response = client.post(
        "/verify",
        json={
            "repository": str(sample_repo),
            "branch": "main",
            "documentation": ["README.md"],
        },
    )

    assert verify_response.status_code == 200

    fixes = verify_response.json()["fixes"]

    assert len(fixes) > 0

    selected_fix = fixes[0]

    contract_id = selected_fix["contract_id"]

    target_file = (
        sample_repo
        / selected_fix["target_file"]
    )

    before_content = target_file.read_text(
        encoding="utf-8"
    )

    approve_response = client.post(
        f"/approve/{contract_id}"
    )

    assert approve_response.status_code == 200

    response = client.post(
        f"/reverify/{contract_id}"
    )

    assert response.status_code == 200

    body = response.json()
    print("REVERIFY RESPONSE BODY:", body)

    assert body["id"] == contract_id
    assert body["approvalStatus"] == "approved"
    assert body["approved"] is True
    assert body["reverified"] is True
    assert body["status"] == "pass"
# File mutation is out of scope — DocProof applies fixes in-memory only.

# ---------------------------------------------------------------------------
# POST /reject/{id}
# ---------------------------------------------------------------------------

def test_reject_dp001_returns_200():
    r = client.post("/reject/DP-001")

    assert r.status_code == 200


def test_reject_dp001_sets_approvalStatus():
    body = client.post("/reject/DP-001").json()

    assert body["approvalStatus"] == "rejected"


def test_reject_dp001_sets_approved_false():
    body = client.post("/reject/DP-001").json()

    assert body["approved"] is False


def test_reject_returns_full_contract():
    """Endpoint must return the full updated DocumentationContract, not just a status."""
    body = client.post("/reject/DP-001").json()

    assert body["id"] == "DP-001"
    assert body["status"] == "fail"


def test_reject_not_found_returns_404():
    r = client.post("/reject/NONEXISTENT")

    assert r.status_code == 404


def test_reject_persists_across_requests():
    client.post("/reject/DP-001")

    body = client.get("/contracts/DP-001").json()

    assert body["approvalStatus"] == "rejected"


def test_approve_then_reject_is_independent():
    """store.reset() in the fixture means this test always starts fresh."""
    body = client.post("/reject/DP-001").json()

    assert body["approvalStatus"] == "rejected"


# ---------------------------------------------------------------------------
# GET /trust-score
# ---------------------------------------------------------------------------

def test_trust_score_returns_200():
    r = client.get("/trust-score")

    assert r.status_code == 200


def test_trust_score_has_score_key():
    body = client.get("/trust-score").json()

    assert "score" in body


def test_trust_score_is_numeric():
    score = client.get("/trust-score").json()["score"]

    assert isinstance(score, (int, float))


def test_trust_score_within_range():
    score = client.get("/trust-score").json()["score"]

    assert 0.0 <= score <= 100.0


def test_trust_score_reflects_seed_data():
    """Seed mix of pass/fail/warning -> score must be strictly between 0 and 100."""
    score = client.get("/trust-score").json()["score"]

    assert 0.0 < score < 100.0