from __future__ import annotations

from typing import List

from fastapi import APIRouter, Query

from app.core.models import HistoryRun
from app.storage import history_db

router = APIRouter()


@router.get("/history", response_model=List[HistoryRun])
def get_history(
    repository: str | None = None,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
) -> list[dict]:
    """Return persisted DocProof verification runs, most recent first."""

    return history_db.get_history(
        repository=repository,
        limit=limit,
    )
