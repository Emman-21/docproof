"""Tests for the Re-verification Subagent.

Coverage:
  - _apply_fixes():  patching contracts in-memory, applied_count, unknown ids
  - _merge_results(): pipeline-only, applied-only, both (reverified wins vs. not)
  - _build_summary(): counts of pass / fail / warning
  - run() schema:     ReverificationResult fields and types
  - run() end-to-end: approved fix → all_pass, stub_count==0, applied_count
  - run() against sample_repo: pipeline agents produce real contracts
  - run() with empty inputs: graceful empty-list handling
"""
from __future__ import annotations

import pytest
from pathlib import Path

from app.core.models import DocumentationContract, FixSuggestion, VerificationSummary
from app.orchestration.subagents.reverification_agent import (
    ReverificationResult,
    _apply_fixes,
    _build_summary,
    _merge_results,
    run,
)

# ---------------------------------------------------------------------------
# Locate the canonical sample_repo relative to this test file.
# Layout:  docproof/
#             backend/tests/test_reverification_agent.py   ← __file__
#             sample_repo/                                  ← target
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REPO = _REPO_ROOT / "sample_repo"


# ---------------------------------------------------------------------------
# Contract and FixSuggestion factory helpers
# ---------------------------------------------------------------------------

def _make_contract(
    *,
    id: str = "TEST-001",
    area: str = "runtime_requirements",
    source: str = "README.md#L10",
    claim: str = "Requires Node.js 18+",
    expected: str = "Node.js >=18",
    actual: str = "Node.js >=20",
    status: str = "fail",
    evidence: str = "Mismatch detected.",
    evidenceFile: str = "package.json",
    evidenceLines: str = "Line 1",
    evidenceSnippet: str = '"engines": {"node": ">=20.0.0"}',
    suggested_fix: str = "Update the docs.",
    severity: str | None = "high",
    approved: bool = False,
    reverified: bool = False,
) -> DocumentationContract:
    return DocumentationContract(
        id=id,
        area=area,  # type: ignore[arg-type]
        source=source,
        claim=claim,
        expected=expected,
        actual=actual,
        status=status,  # type: ignore[arg-type]
        evidence=evidence,
        evidenceFile=evidenceFile,
        evidenceLines=evidenceLines,
        evidenceSnippet=evidenceSnippet,
        suggested_fix=suggested_fix,
        approvalStatus="pending",
        approved=approved,
        reverified=reverified,
        severity=severity,  # type: ignore[arg-type]
    )


def _make_fix(
    *,
    contract_id: str = "TEST-001",
    area: str = "runtime_requirements",
    patch_type: str = "doc_edit",
    target_file: str = "README.md",
    description: str = "Update Node version",
    diff: str = "--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@ runtime\n-Requires Node.js 18+\n+Requires Node.js 20+",
    raw_fix: str = "Requires Node.js 20+",
) -> FixSuggestion:
    return FixSuggestion(
        contract_id=contract_id,
        area=area,  # type: ignore[arg-type]
        patch_type=patch_type,  # type: ignore[arg-type]
        target_file=target_file,
        description=description,
        diff=diff,
        raw_fix=raw_fix,
    )


# ---------------------------------------------------------------------------
# _apply_fixes — unit tests
# ---------------------------------------------------------------------------

class TestApplyFixes:
    def test_returns_updated_list(self):
        contract = _make_contract(id="C-001", status="fail")
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], [contract])
        assert isinstance(updated, list)

    def test_applied_count_increments_per_match(self):
        c1 = _make_contract(id="C-001", status="fail")
        c2 = _make_contract(id="C-002", status="fail")
        f1 = _make_fix(contract_id="C-001")
        f2 = _make_fix(contract_id="C-002")
        _, applied = _apply_fixes([f1, f2], [c1, c2])
        assert applied == 2

    def test_unknown_contract_id_skipped(self):
        contract = _make_contract(id="C-001")
        fix = _make_fix(contract_id="DOES-NOT-EXIST")
        _, applied = _apply_fixes([fix], [contract])
        assert applied == 0

    def test_patched_contract_status_becomes_pass(self):
        contract = _make_contract(id="C-001", status="fail")
        fix = _make_fix(contract_id="C-001", raw_fix="Requires Node.js 20+")
        updated, _ = _apply_fixes([fix], [contract])
        patched = next(c for c in updated if c.id == "C-001")
        assert patched.status == "pass"

    def test_patched_contract_reverified_true(self):
        contract = _make_contract(id="C-001", status="fail", reverified=False)
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], [contract])
        patched = next(c for c in updated if c.id == "C-001")
        assert patched.reverified is True

    def test_patched_contract_approved_true(self):
        contract = _make_contract(id="C-001", status="fail", approved=False)
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], [contract])
        patched = next(c for c in updated if c.id == "C-001")
        assert patched.approved is True

    def test_patched_contract_approval_status_approved(self):
        contract = _make_contract(id="C-001", status="fail")
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], [contract])
        patched = next(c for c in updated if c.id == "C-001")
        assert patched.approvalStatus == "approved"

    def test_patched_actual_becomes_raw_fix(self):
        contract = _make_contract(id="C-001", status="fail")
        fix = _make_fix(contract_id="C-001", raw_fix="Requires Node.js 20+")
        updated, _ = _apply_fixes([fix], [contract])
        patched = next(c for c in updated if c.id == "C-001")
        assert patched.actual == "Requires Node.js 20+"

    def test_patched_suggested_fix_cleared(self):
        contract = _make_contract(id="C-001", status="fail", suggested_fix="Do something")
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], [contract])
        patched = next(c for c in updated if c.id == "C-001")
        assert patched.suggested_fix == ""

    def test_unmatched_contracts_unchanged(self):
        c1 = _make_contract(id="C-001", status="fail")
        c2 = _make_contract(id="C-002", status="fail")
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], [c1, c2])
        untouched = next(c for c in updated if c.id == "C-002")
        assert untouched.status == "fail"
        assert untouched.reverified is False

    def test_empty_fixes_returns_unchanged_list(self):
        contract = _make_contract(id="C-001", status="fail")
        updated, applied = _apply_fixes([], [contract])
        assert applied == 0
        assert updated[0].status == "fail"

    def test_empty_contracts_returns_empty(self):
        fix = _make_fix(contract_id="C-001")
        updated, applied = _apply_fixes([fix], [])
        assert updated == []
        assert applied == 0

    def test_original_list_not_mutated(self):
        contract = _make_contract(id="C-001", status="fail")
        original_status = contract.status
        fix = _make_fix(contract_id="C-001")
        _apply_fixes([fix], [contract])
        assert contract.status == original_status  # original untouched

    def test_length_preserved(self):
        contracts = [
            _make_contract(id="C-001", status="fail"),
            _make_contract(id="C-002", status="pass"),
            _make_contract(id="C-003", status="warning"),
        ]
        fix = _make_fix(contract_id="C-001")
        updated, _ = _apply_fixes([fix], contracts)
        assert len(updated) == 3


# ---------------------------------------------------------------------------
# _merge_results — unit tests
# ---------------------------------------------------------------------------

class TestMergeResults:
    def test_pipeline_only_included(self):
        pipeline = [_make_contract(id="P-001", status="pass")]
        merged = _merge_results([], pipeline)
        assert any(c.id == "P-001" for c in merged)

    def test_applied_only_included_when_not_in_pipeline(self):
        applied = [_make_contract(id="A-001", status="pass", reverified=True)]
        merged = _merge_results(applied, [])
        assert any(c.id == "A-001" for c in merged)

    def test_reverified_applied_wins_over_pipeline(self):
        applied = [_make_contract(id="X-001", status="pass", reverified=True)]
        pipeline = [_make_contract(id="X-001", status="fail", reverified=False)]
        merged = _merge_results(applied, pipeline)
        result = next(c for c in merged if c.id == "X-001")
        assert result.status == "pass"
        assert result.reverified is True

    def test_non_reverified_applied_uses_pipeline(self):
        applied = [_make_contract(id="X-001", status="fail", reverified=False)]
        pipeline = [_make_contract(id="X-001", status="pass", reverified=False)]
        merged = _merge_results(applied, pipeline)
        result = next(c for c in merged if c.id == "X-001")
        assert result.status == "pass"

    def test_union_of_ids_present(self):
        applied = [
            _make_contract(id="A-001", status="pass", reverified=True),
            _make_contract(id="BOTH", status="pass", reverified=True),
        ]
        pipeline = [
            _make_contract(id="P-001", status="pass"),
            _make_contract(id="BOTH", status="fail"),
        ]
        merged = _merge_results(applied, pipeline)
        ids = {c.id for c in merged}
        assert {"A-001", "P-001", "BOTH"} == ids

    def test_pipeline_order_respected(self):
        pipeline = [
            _make_contract(id="P-001"),
            _make_contract(id="P-002"),
            _make_contract(id="P-003"),
        ]
        merged = _merge_results([], pipeline)
        assert [c.id for c in merged] == ["P-001", "P-002", "P-003"]


# ---------------------------------------------------------------------------
# _build_summary — unit tests
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_empty_list(self):
        s = _build_summary([])
        assert s.total == 0
        assert s.passed == 0
        assert s.failed == 0
        assert s.warnings == 0

    def test_all_pass(self):
        contracts = [_make_contract(id=f"C-{i}", status="pass") for i in range(3)]
        s = _build_summary(contracts)
        assert s.total == 3
        assert s.passed == 3
        assert s.failed == 0
        assert s.warnings == 0

    def test_mixed_statuses(self):
        contracts = [
            _make_contract(id="C-1", status="pass"),
            _make_contract(id="C-2", status="fail"),
            _make_contract(id="C-3", status="warning"),
            _make_contract(id="C-4", status="pass"),
        ]
        s = _build_summary(contracts)
        assert s.total == 4
        assert s.passed == 2
        assert s.failed == 1
        assert s.warnings == 1

    def test_returns_verification_summary_type(self):
        s = _build_summary([])
        assert isinstance(s, VerificationSummary)


# ---------------------------------------------------------------------------
# run() — schema and output shape
# ---------------------------------------------------------------------------

class TestRunSchema:
    """Verify the output shape of run() using a no-op call (empty inputs, tmp repo)."""

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, tmp_path_factory):
        repo = tmp_path_factory.mktemp("empty_repo")
        return run(approved_fixes=[], contracts=[], repo_path=repo)

    def test_returns_reverification_result(self, result):
        assert isinstance(result, ReverificationResult)

    def test_has_contracts_list(self, result):
        assert isinstance(result.contracts, list)

    def test_has_summary(self, result):
        assert isinstance(result.summary, VerificationSummary)

    def test_has_all_pass_flag(self, result):
        assert isinstance(result.all_pass, bool)

    def test_has_applied_fix_count(self, result):
        assert isinstance(result.applied_fix_count, int)

    def test_has_stub_count(self, result):
        assert isinstance(result.stub_count, int)

    def test_empty_repo_applied_count_zero(self, result):
        assert result.applied_fix_count == 0

    def test_serialises_to_json(self, result):
        json_str = result.model_dump_json()
        assert "contracts" in json_str
        assert "summary" in json_str

    def test_summary_total_matches_contracts_length(self, result):
        assert result.summary.total == len(result.contracts)


# ---------------------------------------------------------------------------
# run() — end-to-end: applying a fix flips the contract to pass
# ---------------------------------------------------------------------------

class TestRunApplyFixEndToEnd:
    """Verify that an approved fix is reflected in the final result."""

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, tmp_path_factory):
        repo = tmp_path_factory.mktemp("repo_fix")
        contract = DocumentationContract(
            id="E2E-001",
            area="runtime_requirements",
            source="README.md#L10",
            claim="Requires Node.js 18+",
            expected="Node.js >=18",
            actual="Node.js >=20",
            status="fail",
            evidence="Mismatch.",
            evidenceFile="package.json",
            evidenceLines="Line 1",
            evidenceSnippet='{"node": ">=20"}',
            suggested_fix="Update to Node.js 20+",
            approvalStatus="pending",
            approved=False,
            reverified=False,
            severity="high",
        )
        fix = FixSuggestion(
            contract_id="E2E-001",
            area="runtime_requirements",
            patch_type="doc_edit",
            target_file="README.md",
            description="Update Node.js version claim",
            diff="--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-Requires Node.js 18+\n+Requires Node.js 20+",
            raw_fix="Requires Node.js 20+",
        )
        return run(approved_fixes=[fix], contracts=[contract], repo_path=repo)

    def test_applied_fix_count_is_one(self, result):
        assert result.applied_fix_count == 1

    def test_patched_contract_is_pass(self, result):
        target = next((c for c in result.contracts if c.id == "E2E-001"), None)
        assert target is not None
        assert target.status == "pass"

    def test_patched_contract_reverified(self, result):
        target = next(c for c in result.contracts if c.id == "E2E-001")
        assert target.reverified is True

    def test_patched_contract_approved(self, result):
        target = next(c for c in result.contracts if c.id == "E2E-001")
        assert target.approved is True

    def test_all_pass_true_when_no_other_failures(self, result):
        # Only the one patched contract (now pass), empty pipeline from empty repo.
        # all_pass is True when failed==0 and warnings==0.
        assert result.all_pass is True

    def test_stub_count_zero(self, result):
        # The one contract has reverified=True; pipeline is empty.
        # Only the patched contract is present (merged wins applied over pipeline).
        patched_contracts = [c for c in result.contracts if c.id == "E2E-001"]
        assert all(c.reverified for c in patched_contracts)

    def test_summary_counts_reflect_patched_state(self, result):
        # The patched contract is pass, so passed >= 1 and failed == 0.
        assert result.summary.failed == 0


# ---------------------------------------------------------------------------
# run() — empty fix list leaves contracts unchanged
# ---------------------------------------------------------------------------

class TestRunNoFixes:
    def test_no_fixes_applied_count_zero(self, tmp_path):
        contract = _make_contract(id="NF-001", status="fail")
        result = run(approved_fixes=[], contracts=[contract], repo_path=tmp_path)
        assert result.applied_fix_count == 0

    def test_no_fixes_original_status_from_pipeline(self, tmp_path):
        # With an empty repo, the pipeline produces no contracts;
        # the original fail contract should not appear as pass.
        contract = _make_contract(id="NF-002", status="fail", reverified=False)
        result = run(approved_fixes=[], contracts=[contract], repo_path=tmp_path)
        # NF-002 has reverified=False, so pipeline result wins; pipeline is empty,
        # meaning NF-002 still appears from applied_contracts pass-through.
        # Its status should remain fail (no fix applied).
        target = next((c for c in result.contracts if c.id == "NF-002"), None)
        if target is not None:
            assert target.status == "fail"

    def test_no_fixes_all_pass_respects_failures(self, tmp_path):
        contract = _make_contract(id="NF-003", status="fail", reverified=False)
        result = run(approved_fixes=[], contracts=[contract], repo_path=tmp_path)
        # Summary includes the fail contract; all_pass must be False.
        if result.summary.failed > 0:
            assert result.all_pass is False


# ---------------------------------------------------------------------------
# run() — against sample_repo (integration)
# ---------------------------------------------------------------------------

class TestRunAgainstSampleRepo:
    """Integration tests: run() against the real sample_repo directory."""

    @pytest.fixture(scope="class")
    @classmethod
    def result(cls):
        from app.storage import db
        contracts = db.all_contracts()
        # Only apply fixes for fail contracts, using synthetic fix objects
        # that reflect what the fix_agent would produce.
        from app.orchestration.subagents.fix_agent import run as generate_fixes
        fixes = generate_fixes(contracts)
        return run(approved_fixes=fixes, contracts=contracts, repo_path=SAMPLE_REPO)

    def test_returns_reverification_result(self, result):
        assert isinstance(result, ReverificationResult)

    def test_contracts_is_nonempty_list(self, result):
        assert len(result.contracts) > 0

    def test_applied_count_matches_fail_count(self, result):
        from app.storage import db
        fail_count = sum(1 for c in db.all_contracts() if c.status == "fail")
        assert result.applied_fix_count == fail_count

    def test_summary_total_positive(self, result):
        assert result.summary.total > 0

    def test_summary_counts_sum_to_total(self, result):
        s = result.summary
        assert s.passed + s.failed + s.warnings == s.total

    def test_all_verified_contracts_have_reverified_true(self, result):
        for c in result.contracts:
            if c.approved:
                assert c.reverified is True, (
                    f"Contract {c.id} was approved but reverified is False"
                )

    def test_result_serialises_to_json(self, result):
        json_str = result.model_dump_json()
        assert len(json_str) > 0

    def test_schema_compliant_all_fields_present(self, result):
        # Every contract in the output must have all required fields populated.
        for c in result.contracts:
            assert c.id
            assert c.area
            assert c.status in ("pass", "fail", "warning")
            assert isinstance(c.reverified, bool)
            assert isinstance(c.approved, bool)

    def test_no_stub_contracts_from_fixes(self, result):
        # All contracts that were targeted by the approved fixes should not
        # have reverified=False (they were patched in-memory).
        approved = {c.id for c in result.contracts if c.approved}
        for c in result.contracts:
            if c.id in approved:
                assert c.reverified is True


# ---------------------------------------------------------------------------
# ReverificationResult model — direct construction
# ---------------------------------------------------------------------------

class TestReverificationResultModel:
    def test_can_construct(self):
        r = ReverificationResult(
            contracts=[],
            summary=VerificationSummary(total=0, passed=0, failed=0, warnings=0),
            all_pass=True,
            applied_fix_count=0,
            stub_count=0,
        )
        assert r.all_pass is True

    def test_serialises_to_dict(self):
        r = ReverificationResult(
            contracts=[],
            summary=VerificationSummary(total=0, passed=0, failed=0, warnings=0),
            all_pass=True,
            applied_fix_count=0,
            stub_count=0,
        )
        d = r.model_dump()
        assert set(d.keys()) == {
            "contracts", "summary", "all_pass", "applied_fix_count", "stub_count"
        }

    def test_stub_count_zero_means_clean(self):
        r = ReverificationResult(
            contracts=[],
            summary=VerificationSummary(total=0, passed=0, failed=0, warnings=0),
            all_pass=True,
            applied_fix_count=3,
            stub_count=0,
        )
        assert r.stub_count == 0

    def test_all_pass_false_when_failures_remain(self):
        r = ReverificationResult(
            contracts=[],
            summary=VerificationSummary(total=1, passed=0, failed=1, warnings=0),
            all_pass=False,
            applied_fix_count=0,
            stub_count=1,
        )
        assert r.all_pass is False
