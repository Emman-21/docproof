"""Persistent SQLite storage for DocProof verification history."""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _database_path() -> Path:
    override = os.getenv("DOCPROOF_HISTORY_DB")

    if override:
        return Path(override).expanduser().resolve()

    backend_root = Path(__file__).resolve().parents[2]

    return backend_root / ".docproof" / "history.db"


def _connect() -> sqlite3.Connection:
    path = _database_path()

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    return connection


def init_db() -> None:
    """Create the verification history table if needed."""

    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                repository TEXT NOT NULL,
                branch TEXT NOT NULL,
                run_type TEXT NOT NULL,
                trust_score REAL NOT NULL,
                total INTEGER NOT NULL,
                passed INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                warnings INTEGER NOT NULL,
                commit_ref TEXT NOT NULL
            )
            """
        )

        connection.commit()


def _summary_dict(summary: Any) -> dict:
    if hasattr(summary, "model_dump"):
        return summary.model_dump()

    return dict(summary)


def _row_to_public(
    row: sqlite3.Row,
    *,
    current: bool = False,
) -> dict:
    return {
        "id": row["id"],
        "date": row["created_at"],
        "repository": row["repository"],
        "branch": row["branch"],
        "runType": row["run_type"],
        "commit": row["commit_ref"],
        "trustScore": round(
            float(row["trust_score"]),
            2,
        ),
        "summary": {
            "total": row["total"],
            "passed": row["passed"],
            "failed": row["failed"],
            "warnings": row["warnings"],
        },
        "current": current,
    }


def save_history_run(
    *,
    repository: str,
    branch: str,
    run_type: str,
    trust_score: float,
    summary: Any,
    commit_ref: str = "local",
) -> dict:
    """Persist one successful verification or reverification run."""

    if run_type not in {
        "verify",
        "reverify",
    }:
        raise ValueError(
            "run_type must be 'verify' or 'reverify'"
        )

    init_db()

    data = _summary_dict(summary)

    run_id = (
        "run-"
        + uuid.uuid4().hex[:12]
    )

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO history_runs (
                id,
                created_at,
                repository,
                branch,
                run_type,
                trust_score,
                total,
                passed,
                failed,
                warnings,
                commit_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                created_at,
                repository,
                branch or "main",
                run_type,
                float(trust_score),
                int(data["total"]),
                int(data["passed"]),
                int(data["failed"]),
                int(data["warnings"]),
                commit_ref,
            ),
        )

        connection.commit()

    return {
        "id": run_id,
        "date": created_at,
        "repository": repository,
        "branch": branch or "main",
        "runType": run_type,
        "commit": commit_ref,
        "trustScore": round(
            float(trust_score),
            2,
        ),
        "summary": {
            "total": int(data["total"]),
            "passed": int(data["passed"]),
            "failed": int(data["failed"]),
            "warnings": int(data["warnings"]),
        },
        "current": True,
    }


def get_history(
    *,
    repository: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return newest verification runs first."""

    init_db()

    with _connect() as connection:
        if repository:
            rows = connection.execute(
                """
                SELECT *
                FROM history_runs
                WHERE repository = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    repository,
                    limit,
                ),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM history_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        _row_to_public(
            row,
            current=(index == 0),
        )
        for index, row in enumerate(rows)
    ]


def get_latest_branch(
    repository: str,
) -> str | None:
    """Return the branch from the latest run for a repository."""

    init_db()

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT branch
            FROM history_runs
            WHERE repository = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (repository,),
        ).fetchone()

    if row is None:
        return None

    return str(row["branch"])


def clear_history() -> None:
    """Delete history rows. Primarily used by tests."""

    init_db()

    with _connect() as connection:
        connection.execute(
            "DELETE FROM history_runs"
        )

        connection.commit()
