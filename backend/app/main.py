"""DocProof FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import config
from app.storage import db as store
<<<<<<< HEAD
from app.api import contracts, verify, approve, trust_score, fix
=======
from app.api import contracts, verify, approve, reverify, history, trust_score
>>>>>>> 370c285035238c71b5cfd94c853a9733d25c697f


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Seed the in-memory store on startup."""
    store.reset()
    yield


app = FastAPI(title=config.APP_TITLE, version=config.APP_VERSION, lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow the Vite frontend dev server (and any configured origins)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(contracts.router)
app.include_router(verify.router)
app.include_router(approve.router)
app.include_router(reverify.router)
app.include_router(history.router)
app.include_router(trust_score.router)
app.include_router(fix.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


