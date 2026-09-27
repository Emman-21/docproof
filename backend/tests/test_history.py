from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.storage import history_db


client = TestClient(app)


def test_history_database_round_trip():
    saved = history_db.save_history_run(
        repository="C:/demo-project",
        branch="main",
        run_type="verify",
        trust_score=74.0,
        summary={
            "total": 17,
            "passed": 12,
            "failed": 4,
            "warnings": 1,
        },
    )

    runs = history_db.get_history()

    assert len(runs) == 1

    assert runs[0]["id"] == saved["id"]
    assert runs[0]["branch"] == "main"
    assert runs[0]["runType"] == "verify"
    assert runs[0]["trustScore"] == 74.0

    assert runs[0]["summary"] == {
        "total": 17,
        "passed": 12,
        "failed": 4,
        "warnings": 1,
    }


def test_history_endpoint_returns_saved_runs():
    history_db.save_history_run(
        repository="C:/demo-project",
        branch="main",
        run_type="verify",
        trust_score=85.0,
        summary={
            "total": 17,
            "passed": 14,
            "failed": 2,
            "warnings": 1,
        },
    )

    response = client.get(
        "/history"
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body) == 1
    assert body[0]["trustScore"] == 85.0
    assert body[0]["summary"]["passed"] == 14


def test_verify_creates_history_run():
    sample_repo = (
        Path(__file__).resolve().parents[2]
        / "sample_repo"
    )

    response = client.post(
        "/verify",
        json={
            "repository": str(sample_repo),
            "branch": "main",
            "documentation": [
                "README.md"
            ],
        },
    )

    assert response.status_code == 200

    runs = history_db.get_history(
        repository=str(
            sample_repo.resolve()
        )
    )

    assert len(runs) == 1

    run = runs[0]

    assert run["runType"] == "verify"
    assert run["branch"] == "main"

    response_body = response.json()

    assert (
        run["trustScore"]
        == response_body["trust_score"]
    )

    assert (
        run["summary"]
        == response_body["summary"]
    )
