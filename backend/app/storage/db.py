"""In-memory contract store.

This module holds all mutable runtime state for Milestone 1.
It is the single source of truth for the current contract list.

SEED DATA NOTE
--------------
_SEED_CONTRACTS contains the same 17 contracts as frontend/src/data/mockData.ts.
This data exists only so the backend is immediately usable during development
and demo — it is NOT part of the verification logic.

When POST /verify is implemented with real agents, it will call reset() and
then populate _contracts with freshly verified data, replacing the seed entirely.

The reset() function restores the full original seed, not just clears the store.
"""
from __future__ import annotations

import copy
from typing import Dict, List, Optional

from app.core.models import (
    DocumentationContract,
    FixSuggestion,
    VerificationContext,
)

# ---------------------------------------------------------------------------
# Seed data (mirrors frontend/src/data/mockData.ts)
# ---------------------------------------------------------------------------

_SEED: List[DocumentationContract] = [
    # --- runtime_requirements ---
    DocumentationContract(
        id="DP-001", area="runtime_requirements", source="README.md#L42",
        claim="Requires Node.js 18+", expected="Node.js >=18", actual="Node.js >=20",
        status="fail",
        evidence="package.json engines.node is '>=20.0.0', which contradicts the README.",
        evidenceFile="package.json", evidenceLines="Lines 8–10",
        evidenceSnippet='{\n  "engines": {\n    "node": ">=20.0.0"\n  }\n}',
        suggested_fix="Requires Node.js 20+",
        approvalStatus="pending", approved=False, reverified=False, severity="high",
    ),
    DocumentationContract(
        id="DP-002", area="runtime_requirements", source="README.md#L47",
        claim="Python 3.11+ is required for the verification service.",
        expected="Python >=3.11", actual="Python >=3.11",
        status="pass",
        evidence="The backend runtime image uses Python 3.11.",
        evidenceFile="backend/Dockerfile", evidenceLines="Line 1",
        evidenceSnippet="FROM python:3.11-slim",
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-003", area="runtime_requirements", source="docs/setup.md#L12",
        claim="npm 10 or newer is recommended.",
        expected="npm >=10", actual="npm 10.9.2",
        status="pass",
        evidence="The lockfile was generated with a compatible npm release.",
        evidenceFile="package-lock.json", evidenceLines="Metadata",
        evidenceSnippet='"lockfileVersion": 3',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-004", area="runtime_requirements", source="docs/setup.md#L16",
        claim="The frontend supports Node.js 20 LTS.",
        expected="Node.js 20 LTS", actual="Node.js >=20",
        status="pass",
        evidence="The project engines field is compatible with Node.js 20 LTS.",
        evidenceFile="package.json", evidenceLines="Lines 8–10",
        evidenceSnippet='"node": ">=20.0.0"',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    # --- commands ---
    DocumentationContract(
        id="DP-005", area="commands", source="README.md#L61",
        claim="Start the development server using npm start.",
        expected="npm start", actual="npm run dev",
        status="fail",
        evidence="package.json defines 'dev' as the development command and does not define a start script.",
        evidenceFile="package.json", evidenceLines="Lines 5–9",
        evidenceSnippet='{\n  "scripts": {\n    "dev": "vite",\n    "build": "vite build",\n    "preview": "vite preview"\n  }\n}',
        suggested_fix="Start the development server using npm run dev.",
        approvalStatus="pending", approved=False, reverified=False, severity="high",
    ),
    DocumentationContract(
        id="DP-006", area="commands", source="README.md#L68",
        claim="Install frontend dependencies with npm install.",
        expected="npm install", actual="npm install",
        status="pass",
        evidence="The frontend uses package.json and package-lock.json with npm.",
        evidenceFile="frontend/package-lock.json", evidenceLines="Root package metadata",
        evidenceSnippet='"name": "docproof-frontend"',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-007", area="commands", source="README.md#L74",
        claim="Build the frontend using npm run build.",
        expected="npm run build", actual="npm run build",
        status="pass",
        evidence="The package scripts include the documented build command.",
        evidenceFile="frontend/package.json", evidenceLines="Scripts",
        evidenceSnippet='"build": "tsc -b && vite build"',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-008", area="commands", source="README.md#L79",
        claim="Run TypeScript checks with npm run typecheck.",
        expected="npm run typecheck", actual="npm run typecheck",
        status="pass",
        evidence="The typecheck script is defined in the frontend package.",
        evidenceFile="frontend/package.json", evidenceLines="Scripts",
        evidenceSnippet='"typecheck": "tsc --noEmit -p tsconfig.app.json"',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-009", area="commands", source="README.md#L84",
        claim="Preview the production frontend using npm run preview.",
        expected="npm run preview", actual="npm run preview",
        status="pass",
        evidence="The preview script is present and invokes Vite preview.",
        evidenceFile="frontend/package.json", evidenceLines="Scripts",
        evidenceSnippet='"preview": "vite preview"',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    # --- config_env ---
    DocumentationContract(
        id="DP-010", area="config_env", source="docs/setup.md#L24",
        claim="DATABASE_URL is the only required environment variable.",
        expected="DATABASE_URL only", actual="DATABASE_URL and JWT_SECRET are required",
        status="fail",
        evidence="The backend settings read both DATABASE_URL and JWT_SECRET as required values.",
        evidenceFile="backend/app/core/config.py", evidenceLines="Lines 9–16",
        evidenceSnippet='DATABASE_URL = require_env("DATABASE_URL")\nJWT_SECRET = require_env("JWT_SECRET")',
        suggested_fix="Document both DATABASE_URL and JWT_SECRET as required environment variables.",
        approvalStatus="pending", approved=False, reverified=False, severity="medium",
    ),
    DocumentationContract(
        id="DP-011", area="config_env", source="docs/setup.md#L31",
        claim="The frontend API URL is configured through VITE_API_BASE_URL.",
        expected="VITE_API_BASE_URL configured", actual="VITE_API_BASE_URL configured",
        status="pass",
        evidence="The API client reads import.meta.env.VITE_API_BASE_URL.",
        evidenceFile="frontend/src/api/client.ts", evidenceLines="Top-level config",
        evidenceSnippet="const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;",
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-012", area="config_env", source="docs/setup.md#L35",
        claim="The local frontend runs on port 5173.",
        expected="Port 5173", actual="Port 5173",
        status="pass",
        evidence="Vite dev server configuration uses port 5173.",
        evidenceFile="vite.config.ts", evidenceLines="Server config",
        evidenceSnippet="server: { port: 5173 }",
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-013", area="config_env", source=".env.example#L4",
        claim="PORT=3000 is fixed for all environments.",
        expected="PORT=3000 always", actual="PORT defaults to 3000 but can be overridden",
        status="warning",
        evidence="The application reads PORT from the environment and falls back to 3000.",
        evidenceFile="backend/app/core/config.py", evidenceLines="Port setting",
        evidenceSnippet='PORT = int(os.getenv("PORT", "3000"))',
        suggested_fix="Clarify that PORT defaults to 3000 and can be overridden.",
        approvalStatus="pending", approved=False, reverified=False, severity="low",
    ),
    # --- api_docs ---
    DocumentationContract(
        id="DP-014", area="api_docs", source="docs/api.md#L12",
        claim="GET /contracts returns documentation contracts.",
        expected="GET /contracts", actual="GET /contracts",
        status="pass",
        evidence="The contracts endpoint uses the documented route and method.",
        evidenceFile="backend/app/api/contracts.py", evidenceLines="Route declaration",
        evidenceSnippet='@router.get("/contracts")',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-015", area="api_docs", source="docs/api.md#L28",
        claim="POST /api/users/create creates a user.",
        expected="POST /api/users/create", actual="POST /api/users",
        status="fail",
        evidence="The implemented route is POST /api/users, not POST /api/users/create.",
        evidenceFile="backend/app/api/users.py", evidenceLines="Route declaration",
        evidenceSnippet='@router.post("/api/users")',
        suggested_fix="Change the documented endpoint to POST /api/users.",
        approvalStatus="pending", approved=False, reverified=False, severity="medium",
    ),
    DocumentationContract(
        id="DP-016", area="api_docs", source="docs/api.md#L40",
        claim="POST /approve/{contract_id} approves a suggested fix.",
        expected="POST /approve/{contract_id}", actual="POST /approve/{contract_id}",
        status="pass",
        evidence="The approval endpoint matches the documented method and path.",
        evidenceFile="backend/app/api/approve.py", evidenceLines="Route declaration",
        evidenceSnippet='@router.post("/approve/{contract_id}")',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
    DocumentationContract(
        id="DP-017", area="api_docs", source="docs/api.md#L46",
        claim="GET /trust-score returns the Documentation Trust Score.",
        expected="GET /trust-score", actual="GET /trust-score",
        status="pass",
        evidence="The trust score route exists with the documented HTTP method.",
        evidenceFile="backend/app/api/trust_score.py", evidenceLines="Route declaration",
        evidenceSnippet='@router.get("/trust-score")',
        suggested_fix="",
        approvalStatus="pending", approved=False, reverified=True,
    ),
]

# ---------------------------------------------------------------------------
# Runtime store — mutated by repository operations
# ---------------------------------------------------------------------------

_contracts: Dict[str, DocumentationContract] = {}
_fixes: Dict[str, FixSuggestion] = {}
_verification_context: VerificationContext | None = None


def reset() -> None:
    """Restore the store to the original seed dataset.

    Called at application startup and by the test fixture before each test.
    Uses deep copies so mutations during a test never corrupt the seed.
    """
    global _contracts, _fixes, _verification_context

    _contracts = {
        c.id: c.model_copy(deep=True)
        for c in _SEED
    }

    _fixes = {}
    _verification_context = None


def replace_contracts(contracts: List[DocumentationContract]) -> None:
    """Replace the runtime store with freshly verified contracts."""
    global _contracts

    _contracts = {
        contract.id: contract.model_copy(deep=True)
        for contract in contracts
    }


def replace_fixes(
    fixes: List[FixSuggestion],
) -> None:
    """Replace stored fix suggestions with results from the latest run."""
    global _fixes

    _fixes = {
        fix.contract_id: fix.model_copy(deep=True)
        for fix in fixes
    }


def all_contracts() -> List[DocumentationContract]:
    return list(_contracts.values())


def get_contract(contract_id: str) -> DocumentationContract | None:
    return _contracts.get(contract_id)


def update_contract(contract_id: str, **fields) -> DocumentationContract | None:
    contract = _contracts.get(contract_id)
    if contract is None:
        return None
    updated = contract.model_copy(update=fields)
    _contracts[contract_id] = updated
    return updated


def all_fixes() -> List[FixSuggestion]:
    """Return all fix suggestions from the latest verification run."""
    return list(_fixes.values())


def get_fix(
    contract_id: str,
) -> FixSuggestion | None:
    """Return the fix suggestion associated with a contract."""
    return _fixes.get(contract_id)


def set_verification_context(
    context: VerificationContext,
) -> None:
    """Store repository information needed for later reverification."""
    global _verification_context

    _verification_context = context.model_copy(
        deep=True
    )


def get_verification_context() -> VerificationContext | None:
    """Return context from the most recent local verification run."""
    if _verification_context is None:
        return None

    return _verification_context.model_copy(
        deep=True
    )


# Seed on module import so the app is ready without an explicit startup call.
reset()
