"""Full Pipeline Orchestration.

Chains all six subagents into a single end-to-end execution flow:

  1. Runtime   — version claims in docs vs. package.json / setup.cfg / .nvmrc
  2. Commands  — shell commands in docs vs. scripts defined in package.json
  3. Config    — env-var declarations in .env.example vs. docs and source code
  4. API Docs  — HTTP endpoints in docs vs. FastAPI route implementations
  5. Fix       — generates a FixSuggestion for every failing contract
  6. Re-verify — applies all fixes (as approved) and re-runs the pipeline

The public entry point is ``run()``, which returns a ``PipelineResult``
containing the final aggregated JSON-serialisable report.

Public API
----------
run(
    repo_path:    str | Path,
    backend_path: str | Path | None = None,
) -> PipelineResult
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

from app.core.models import (
    DocumentationContract,
    FixSuggestion,
    VerificationSummary,
)
from app.orchestration.subagents import (
    api_docs_agent,
    commands_agent,
    config_env_agent,
    fix_agent,
    runtime_agent,
)
from app.orchestration.subagents.reverification_agent import (
    ReverificationResult,
    run as _reverification_run,
)
from app.verification.trust_score import calculate_trust_score


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class PipelineResult(BaseModel):
    """Aggregated, JSON-serialisable report produced by the full pipeline.

    Attributes
    ----------
    contracts:
        All ``DocumentationContract`` objects produced by the four verification
        agents (runtime, commands, config_env, api_docs).
    fixes:
        One ``FixSuggestion`` per failing contract (empty when all pass).
    reverification:
        Result of re-running the pipeline after all fixes are applied as
        approved.  ``reverification.stub_count == 0`` means every contract
        was fully re-verified.
    summary:
        Aggregate pass / fail / warning counts across *all* contracts from
        the initial verification run.
    trust_score:
        Documentation trust score (0.0–100.0) computed from the *initial*
        verification results.
    trust_score_after:
        Documentation trust score (0.0–100.0) computed from the *post-fix*
        re-verification contracts.
    elapsed_seconds:
        Wall-clock time taken by the pipeline run (float, rounded to 3 dp).
    """

    contracts: List[DocumentationContract]
    fixes: List[FixSuggestion]
    reverification: ReverificationResult
    summary: VerificationSummary
    trust_score: float
    trust_score_after: float
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_verification_agents(
    repo_path: Path,
    backend_path: Optional[Path],
) -> list[DocumentationContract]:
    """Invoke all four verification agents and merge their contract lists.

    Order: runtime → commands → config_env → api_docs.
    """
    contracts: list[DocumentationContract] = []
    contracts.extend(runtime_agent.run(repo_path))
    contracts.extend(commands_agent.run(repo_path))
    contracts.extend(config_env_agent.run(repo_path))
    if backend_path is not None:
        contracts.extend(api_docs_agent.run(repo_path, backend_path=backend_path))
    else:
        contracts.extend(api_docs_agent.run(repo_path))
    return contracts


def _build_summary(contracts: list[DocumentationContract]) -> VerificationSummary:
    passed = sum(1 for c in contracts if c.status == "pass")
    failed = sum(1 for c in contracts if c.status == "fail")
    warnings = sum(1 for c in contracts if c.status == "warning")
    return VerificationSummary(
        total=len(contracts),
        passed=passed,
        failed=failed,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    repo_path: str | Path,
    backend_path: str | Path | None = None,
) -> PipelineResult:
    """Execute the full verification pipeline against *repo_path*.

    Pipeline stages
    ---------------
    1. Runtime agent  — version claims vs. configuration files
    2. Commands agent — shell commands vs. package.json scripts
    3. Config agent   — env var declarations vs. docs and source
    4. API docs agent — documented endpoints vs. FastAPI routes
    5. Fix agent      — generates a fix for each failing contract
    6. Re-verification — applies all fixes (auto-approved) and re-runs

    Parameters
    ----------
    repo_path:
        Absolute or relative path to the repository root to analyse.
    backend_path:
        Directory containing the FastAPI application source.  When ``None``
        the api_docs_agent defaults to ``<repo_path>/../backend``, which
        matches the project layout.

    Returns
    -------
    PipelineResult
        A fully populated, JSON-serialisable report.
    """
    t0 = time.perf_counter()

    repo = Path(repo_path).resolve()
    backend: Optional[Path] = Path(backend_path).resolve() if backend_path is not None else None

    # ------------------------------------------------------------------
    # Stage 1–4: verification agents
    # ------------------------------------------------------------------
    contracts = _run_verification_agents(repo, backend)

    # ------------------------------------------------------------------
    # Stage 5: fix agent (processes only failing contracts)
    # ------------------------------------------------------------------
    fixes = fix_agent.run(contracts)

    # ------------------------------------------------------------------
    # Stage 6: re-verification — auto-approve all generated fixes
    # so the re-verification agent can apply and re-scan them.
    # ------------------------------------------------------------------
    approved_fixes = [
        fix.model_copy(update={})
        for fix in fixes
    ]
    reverification = _reverification_run(
        approved_fixes=approved_fixes,
        contracts=contracts,
        repo_path=repo,
    )

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    summary = _build_summary(contracts)
    trust_score = calculate_trust_score(contracts)
    trust_score_after = calculate_trust_score(reverification.contracts)
    elapsed = round(time.perf_counter() - t0, 3)

    return PipelineResult(
        contracts=contracts,
        fixes=fixes,
        reverification=reverification,
        summary=summary,
        trust_score=trust_score,
        trust_score_after=trust_score_after,
        elapsed_seconds=elapsed,
    )


def run_and_dump(
    repo_path: str | Path,
    backend_path: str | Path | None = None,
    *,
    indent: int = 2,
) -> str:
    """Run the pipeline and return the result as a formatted JSON string.

    Convenience wrapper around ``run()`` for CLI and integration use.

    Parameters
    ----------
    repo_path, backend_path:
        Forwarded to ``run()``.
    indent:
        JSON indentation level (default 2).

    Returns
    -------
    str
        Pretty-printed JSON representation of a ``PipelineResult``.
    """
    result = run(repo_path, backend_path=backend_path)
    return json.dumps(result.model_dump(), indent=indent)
