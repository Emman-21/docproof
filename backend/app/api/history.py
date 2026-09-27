from __future__ import annotations

from fastapi import APIRouter, Query

from app.storage import history_db


router = APIRouter()


@router.get("/history")
def get_history(
    repository: str | None = None,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
) -> list[dict]:
    """Return persisted DocProof verification runs."""

    return history_db.get_history(
        repository=repository,
        limit=limit,
    )
