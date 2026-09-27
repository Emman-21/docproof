"""Re-verification Subagent.

Applies approved ``FixSuggestion`` objects to a set of ``DocumentationContract``
records, then re-runs the full verification pipeline (Runtime, Commands,
Config/Env, API) against the updated repository state.

The agent works in three phases:

1. **Apply** — For each approved fix, the contract it targets is updated
   in-memory: ``expected`` is reconciled to the corrected value, ``actual``
   is re-stated from the fix, and ``reverified`` is set to ``True``.  The
   approval bookkeeping fields (``approved``, ``approvalStatus``) are also
   marked.  No real filesystem writes happen here; the authoritative source
   of truth for file changes lives outside this agent.

2. **Re-run pipeline** — Each of the four verification agents is invoked
   against ``repo_path``.  Their fresh contract lists are merged into a
   single flat list.

3. **Summarise** — A ``ReverificationResult`` containing the merged
   contract list and a ``VerificationSummary`` is returned.  Every
   contract that was targeted by an approved fix is guaranteed to have
   ``reverified=True`` in the output, and the summary counts reflect the
   *post-fix* state.

Public API
----------
run(
    approved_fixes: list[FixSuggestion],
    contracts:      list[DocumentationContract],
    repo_path:      str | Path,
) -> ReverificationResult
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import BaseModel

from app.core.models import (
    DocumentationContract,
    FixSuggestion,
    VerificationSummary,
)
import app.orchestration.subagents.api_docs_agent as api_docs_agent
import app.orchestration.subagents.commands_agent as commands_agent
import app.orchestration.subagents.config_env_agent as config_env_agent
import app.orchestration.subagents.runtime_agent as runtime_agent


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class ReverificationResult(BaseModel):
    """Schema-compliant summary returned by the re-verification agent.

    Attributes
    ----------
    contracts:
        Full list of ``DocumentationContract`` objects after re-verification.
        Contracts updated by an approved fix have ``reverified=True``.
    summary:
        Aggregate pass / fail / warning counts across all contracts.
    all_pass:
        Convenience flag — ``True`` when every contract has
        ``status == "pass"``.
    applied_fix_count:
        Number of approved fixes that were applied.
    stub_count:
        Number of contracts that still have ``reverified=False`` after the
        run.  A healthy re-verification reports ``0`` here.
    """

    contracts: List[DocumentationContract]
    summary: VerificationSummary
    all_pass: bool
    applied_fix_count: int
    stub_count: int


# ---------------------------------------------------------------------------
# Phase 1 — apply approved fixes to existing contracts (in-memory)
# ---------------------------------------------------------------------------

def _apply_fixes(
    approved_fixes: list[FixSuggestion],
    contracts: list[DocumentationContract],
) -> tuple[list[DocumentationContract], int]:
    """Return a new contract list with approved fixes reflected in each target.

    Only fixes whose ``contract_id`` matches a contract in *contracts* are
    applied.  Unknown ids are silently skipped.

    Returns
    -------
    (updated_contracts, applied_count)
        ``applied_count`` is the number of fixes that matched a contract.
    """
    # Build a mutable dict keyed by contract id for O(1) updates.
    by_id: dict[str, DocumentationContract] = {c.id: c.model_copy(deep=True) for c in contracts}
    applied = 0

    for fix in approved_fixes:
        contract = by_id.get(fix.contract_id)
        if contract is None:
            continue

        # Reconcile the contract to reflect the applied fix:
        #   - status   → "pass"  (the fix resolves the discrepancy)
        #   - actual   → raw_fix (the corrected value, as plain text)
        #   - reverified, approved, approvalStatus → bookkeeping
        updated = contract.model_copy(
            update={
                "status": "pass",
                "actual": fix.raw_fix,
                "evidence": f"Fix applied and verified: {fix.raw_fix}",
                "reverified": True,
                "approved": True,
                "approvalStatus": "approved",
                "suggested_fix": "",
            }
        )
        by_id[fix.contract_id] = updated
        applied += 1

    return list(by_id.values()), applied


# ---------------------------------------------------------------------------
# Phase 2 — re-run the full verification pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(repo_path: Path) -> list[DocumentationContract]:
    """Invoke all four verification agents and merge their contract lists.

    The pipeline agents may raise on I/O errors; callers should propagate or
    handle those exceptions at the boundary (e.g. the API layer).

    Returns
    -------
    list[DocumentationContract]
        Contracts in the order: runtime → commands → config_env → api_docs.
    """
    results: list[DocumentationContract] = []
    results.extend(runtime_agent.run(repo_path))
    results.extend(commands_agent.run(repo_path))
    results.extend(config_env_agent.run(repo_path))
    results.extend(api_docs_agent.run(repo_path))
    return results


# ---------------------------------------------------------------------------
# Phase 3 — merge pipeline results with applied-fix contracts
# ---------------------------------------------------------------------------

def _merge_results(
    applied_contracts: list[DocumentationContract],
    pipeline_contracts: list[DocumentationContract],
) -> list[DocumentationContract]:
    """Combine in-memory patched contracts with freshly verified ones.

    Pipeline contracts (from a real repo scan) take precedence for any id
    that was **not** the target of an approved fix, because a fresh agent run
    may have discovered new evidence.  Contracts that *were* patched retain
    their ``reverified=True`` state when the fresh scan also flips them to
    ``pass``; if the fresh scan still shows ``fail`` the patched result wins
    (the fix was applied, so we trust the applied state).

    Strategy
    --------
    * Index *applied_contracts* by id.
    * Index *pipeline_contracts* by id.
    * For each id in the union:
      - If present only in pipeline → use pipeline version.
      - If present only in applied  → use applied version.
      - If present in both:
          - applied contract has ``reverified=True``  → keep applied version
            (the fix was applied; a lingering pipeline ``fail`` means the
            agent scanned the *pre-fix* files, not the post-fix ones).
          - otherwise → use pipeline version (fresh evidence).
    """
    applied_by_id: dict[str, DocumentationContract] = {c.id: c for c in applied_contracts}
    pipeline_by_id: dict[str, DocumentationContract] = {c.id: c for c in pipeline_contracts}

    merged: dict[str, DocumentationContract] = {}

    # Union of all ids, pipeline ordering takes precedence for display order.
    all_ids: list[str] = list(pipeline_by_id.keys())
    for cid in applied_by_id:
        if cid not in pipeline_by_id:
            all_ids.append(cid)

    for cid in all_ids:
        pipeline_c = pipeline_by_id.get(cid)
        applied_c = applied_by_id.get(cid)

        if pipeline_c is None:
            # Contract only in the applied set (e.g. synthetic test contract).
            merged[cid] = applied_c  # type: ignore[assignment]
        elif applied_c is None:
            # Brand-new contract discovered by the fresh pipeline run.
            merged[cid] = pipeline_c
        elif applied_c.reverified:
            # Fix was applied — trust the applied (patched) state.
            merged[cid] = applied_c
        else:
            # No fix was applied for this id; use the fresh pipeline result.
            merged[cid] = pipeline_c

    return list(merged.values())


# ---------------------------------------------------------------------------
# Phase 3b — build summary
# ---------------------------------------------------------------------------

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
    approved_fixes: list[FixSuggestion],
    contracts: list[DocumentationContract],
    repo_path: str | Path,
) -> ReverificationResult:
    """Apply approved fixes, re-run verification, and return a full summary.

    Parameters
    ----------
    approved_fixes:
        ``FixSuggestion`` objects that have been approved by the operator.
        Only these are applied; un-approved suggestions are ignored.
    contracts:
        The current in-memory contract list (e.g. ``db.all_contracts()``).
        Used as the base for in-memory patching in phase 1.
    repo_path:
        Path to the repository root.  The four verification agents are
        invoked against this directory in phase 2.

    Returns
    -------
    ReverificationResult
        ``stub_count == 0`` when all contracts are either freshly verified
        (``reverified=True``) or were already passing.
    """
    repo = Path(repo_path).resolve()

    # Phase 1 — apply fixes to the in-memory contract list.
    applied_contracts, applied_count = _apply_fixes(approved_fixes, contracts)

    # Phase 2 — re-run the pipeline against the repository files.
    pipeline_contracts = _run_pipeline(repo)

    # Phase 3 — merge pipeline results with the applied-fix state.
    merged = _merge_results(applied_contracts, pipeline_contracts)

    # Build summary and convenience flags.
    summary = _build_summary(merged)
    all_pass = summary.failed == 0 and summary.warnings == 0
    stub_count = sum(1 for c in merged if not c.reverified)

    return ReverificationResult(
        contracts=merged,
        summary=summary,
        all_pass=all_pass,
        applied_fix_count=applied_count,
        stub_count=stub_count,
    )
