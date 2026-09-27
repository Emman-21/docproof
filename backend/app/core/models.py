"""Pydantic models that exactly mirror frontend/src/types.ts.

Field names are intentionally mixed case to match the TypeScript interface:
  - approvalStatus, evidenceFile, evidenceLines, evidenceSnippet  → camelCase
  - suggested_fix, approved, reverified                           → snake_case (as in TS)

model_config uses populate_by_name=True so both alias and field name work
when constructing instances in Python code.
"""
from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Literal types (mirror TS union types)
# ---------------------------------------------------------------------------

VerificationArea = Literal[
    "runtime_requirements",
    "commands",
    "config_env",
    "api_docs",
]

PatchType = Literal["doc_edit", "config_edit", "code_edit"]

ContractResultStatus = Literal["pass", "fail", "warning"]

ApprovalStatus = Literal["pending", "approved", "rejected"]

Severity = Literal["high", "medium", "low"]


# ---------------------------------------------------------------------------
# Core domain model
# ---------------------------------------------------------------------------

class DocumentationContract(BaseModel):
    """Mirrors the DocumentationContract interface in frontend/src/types.ts."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    area: VerificationArea
    source: str
    claim: str
    expected: str
    actual: str
    status: ContractResultStatus
    evidence: str
    # Optional evidence detail fields — camelCase to match the frontend
    evidenceFile: Optional[str] = None
    evidenceLines: Optional[str] = None
    evidenceSnippet: Optional[str] = None
    suggested_fix: str
    approvalStatus: ApprovalStatus
    approved: bool
    reverified: bool
    severity: Optional[Severity] = None


# ---------------------------------------------------------------------------
# Fix suggestion model
# ---------------------------------------------------------------------------

class FixSuggestion(BaseModel):
    """A structured, actionable fix for a single failing DocumentationContract.

    ``diff`` contains a unified-diff fragment that a developer can apply
    directly (or review) to resolve the discrepancy detected by the
    verification agent.  ``raw_fix`` holds the same information as plain text
    for consumers that do not want to parse diffs.
    """

    model_config = ConfigDict(populate_by_name=True)

    contract_id: str
    area: VerificationArea
    patch_type: PatchType
    target_file: str
    description: str
    diff: str
    raw_fix: str


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------

class VerificationSummary(BaseModel):
    total: int
    passed: int
    failed: int
    warnings: int


class ProjectSelection(BaseModel):
    """Request body for POST /verify."""
    repository: str
    branch: str
    documentation: List[str]


class TrustScoreResponse(BaseModel):
    score: float
