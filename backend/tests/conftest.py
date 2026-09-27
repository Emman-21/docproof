from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_history_database(
    tmp_path,
    monkeypatch,
):
    """Keep test history databases outside the real DocProof database."""

    database_path = (
        tmp_path
        / "docproof-test-history.db"
    )

    monkeypatch.setenv(
        "DOCPROOF_HISTORY_DB",
        str(database_path),
    )

    yield
