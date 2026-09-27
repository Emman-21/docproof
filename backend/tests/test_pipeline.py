"""Integration tests for the full pipeline orchestration.

All tests run against the real sample_repo/ fixture — no mocks, no stubs.
The pipeline exercises all six subagents in sequence and the assertions
validate the aggregated PipelineResult.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models import DocumentationContract, FixSuggestion, VerificationSummary
from app.orchestration.pipeline import PipelineResult, run, run_and_dump
from app.orchestration.subagents.reverification_agent import ReverificationResult

# ---------------------------------------------------------------------------
# Locate the canonical sample_repo relative to this test file.
# Layout:  docproof/
#             backend/tests/test_pipeline.py   ← __file__
#             sample_repo/                      ← target
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REPO = _REPO_ROOT / "sample_repo"
BACKEND_DIR = _REPO_ROOT / "backend"


# ---------------------------------------------------------------------------
# Shared fixture: run the full pipeline once per class
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pipeline_result() -> PipelineResult:
    """Execute the full pipeline against sample_repo/ and cache the result."""
    return run(SAMPLE_REPO, backend_path=BACKEND_DIR)


# ---------------------------------------------------------------------------
# Schema tests — PipelineResult structure
# ---------------------------------------------------------------------------

class TestPipelineResultSchema:
    def test_returns_pipeline_result_type(self, pipeline_result):
        assert isinstance(pipeline_result, PipelineResult)

    def test_has_contracts_list(self, pipeline_result):
        assert isinstance(pipeline_result.contracts, list)

    def test_has_fixes_list(self, pipeline_result):
        assert isinstance(pipeline_result.fixes, list)

    def test_has_reverification(self, pipeline_result):
        assert isinstance(pipeline_result.reverification, ReverificationResult)

    def test_has_summary(self, pipeline_result):
        assert isinstance(pipeline_result.summary, VerificationSummary)

    def test_has_trust_score(self, pipeline_result):
        assert isinstance(pipeline_result.trust_score, float)

    def test_has_trust_score_after(self, pipeline_result):
        assert isinstance(pipeline_result.trust_score_after, float)

    def test_has_elapsed_seconds(self, pipeline_result):
        assert isinstance(pipeline_result.elapsed_seconds, float)

    def test_serialises_to_json(self, pipeline_result):
        dumped = pipeline_result.model_dump()
        # Must round-trip through json without error
        raw = json.dumps(dumped)
        loaded = json.loads(raw)
        assert isinstance(loaded, dict)

    def test_all_required_keys_present(self, pipeline_result):
        keys = set(pipeline_result.model_dump().keys())
        assert {"contracts", "fixes", "reverification", "summary",
                "trust_score", "trust_score_after", "elapsed_seconds"}.issubset(keys)


# ---------------------------------------------------------------------------
# Contracts — initial verification pass (stages 1–4)
# ---------------------------------------------------------------------------

class TestContractsFromSampleRepo:
    def test_contracts_is_nonempty(self, pipeline_result):
        assert len(pipeline_result.contracts) > 0

    def test_all_contracts_are_documentation_contract(self, pipeline_result):
        for c in pipeline_result.contracts:
            assert isinstance(c, DocumentationContract)

    def test_contracts_cover_all_four_areas(self, pipeline_result):
        areas = {c.area for c in pipeline_result.contracts}
        assert areas == {"runtime_requirements", "commands", "config_env", "api_docs"}

    def test_every_contract_has_id(self, pipeline_result):
        for c in pipeline_result.contracts:
            assert c.id

    def test_every_contract_has_valid_status(self, pipeline_result):
        valid = {"pass", "fail", "warning"}
        for c in pipeline_result.contracts:
            assert c.status in valid

    def test_every_contract_has_area(self, pipeline_result):
        valid = {"runtime_requirements", "commands", "config_env", "api_docs"}
        for c in pipeline_result.contracts:
            assert c.area in valid

    def test_every_contract_has_source(self, pipeline_result):
        for c in pipeline_result.contracts:
            assert c.source

    def test_every_contract_has_evidence(self, pipeline_result):
        for c in pipeline_result.contracts:
            assert c.evidence

    def test_sample_repo_has_failing_contracts(self, pipeline_result):
        """sample_repo/ is seeded with deliberate mismatches — at least one must fail."""
        failed = [c for c in pipeline_result.contracts if c.status == "fail"]
        assert len(failed) > 0

    def test_runtime_area_has_node_contract(self, pipeline_result):
        rt_contracts = [c for c in pipeline_result.contracts
                        if c.area == "runtime_requirements"]
        claims_lower = " ".join(c.claim.lower() for c in rt_contracts)
        assert "node" in claims_lower

    def test_runtime_area_has_python_contract(self, pipeline_result):
        rt_contracts = [c for c in pipeline_result.contracts
                        if c.area == "runtime_requirements"]
        claims_lower = " ".join(c.claim.lower() for c in rt_contracts)
        assert "python" in claims_lower


# ---------------------------------------------------------------------------
# Summary — aggregate counts
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_total_matches_contracts_length(self, pipeline_result):
        assert pipeline_result.summary.total == len(pipeline_result.contracts)

    def test_summary_counts_sum_to_total(self, pipeline_result):
        s = pipeline_result.summary
        assert s.passed + s.failed + s.warnings == s.total

    def test_summary_passed_non_negative(self, pipeline_result):
        assert pipeline_result.summary.passed >= 0

    def test_summary_failed_non_negative(self, pipeline_result):
        assert pipeline_result.summary.failed >= 0

    def test_summary_warnings_non_negative(self, pipeline_result):
        assert pipeline_result.summary.warnings >= 0

    def test_summary_failed_matches_fail_contracts(self, pipeline_result):
        fail_count = sum(1 for c in pipeline_result.contracts if c.status == "fail")
        assert pipeline_result.summary.failed == fail_count

    def test_summary_passed_matches_pass_contracts(self, pipeline_result):
        pass_count = sum(1 for c in pipeline_result.contracts if c.status == "pass")
        assert pipeline_result.summary.passed == pass_count


# ---------------------------------------------------------------------------
# Trust score
# ---------------------------------------------------------------------------

class TestTrustScore:
    def test_trust_score_in_range(self, pipeline_result):
        assert 0.0 <= pipeline_result.trust_score <= 100.0

    def test_trust_score_positive_when_some_pass(self, pipeline_result):
        has_pass = any(c.status == "pass" for c in pipeline_result.contracts)
        if has_pass:
            assert pipeline_result.trust_score > 0.0

    def test_trust_score_below_100_when_some_fail(self, pipeline_result):
        has_fail = any(c.status == "fail" for c in pipeline_result.contracts)
        if has_fail:
            assert pipeline_result.trust_score < 100.0

    def test_trust_score_after_in_range(self, pipeline_result):
        assert 0.0 <= pipeline_result.trust_score_after <= 100.0

    def test_trust_score_after_gte_trust_score(self, pipeline_result):
        """Applying fixes should not decrease the trust score."""
        assert pipeline_result.trust_score_after >= pipeline_result.trust_score


# ---------------------------------------------------------------------------
# Fixes — stage 5
# ---------------------------------------------------------------------------

class TestFixes:
    def test_fixes_is_list(self, pipeline_result):
        assert isinstance(pipeline_result.fixes, list)

    def test_all_fixes_are_fix_suggestion(self, pipeline_result):
        for f in pipeline_result.fixes:
            assert isinstance(f, FixSuggestion)

    def test_fix_count_matches_fail_count(self, pipeline_result):
        fail_count = sum(1 for c in pipeline_result.contracts if c.status == "fail")
        assert len(pipeline_result.fixes) == fail_count

    def test_every_fix_has_contract_id(self, pipeline_result):
        for f in pipeline_result.fixes:
            assert f.contract_id

    def test_every_fix_contract_id_references_a_contract(self, pipeline_result):
        contract_ids = {c.id for c in pipeline_result.contracts}
        for f in pipeline_result.fixes:
            assert f.contract_id in contract_ids

    def test_every_fix_has_diff(self, pipeline_result):
        for f in pipeline_result.fixes:
            assert f.diff

    def test_every_fix_has_raw_fix(self, pipeline_result):
        for f in pipeline_result.fixes:
            assert f.raw_fix

    def test_every_fix_has_valid_patch_type(self, pipeline_result):
        valid = {"doc_edit", "config_edit", "code_edit"}
        for f in pipeline_result.fixes:
            assert f.patch_type in valid

    def test_every_fix_has_valid_area(self, pipeline_result):
        valid = {"runtime_requirements", "commands", "config_env", "api_docs"}
        for f in pipeline_result.fixes:
            assert f.area in valid

    def test_fixes_only_for_failing_contracts(self, pipeline_result):
        """Each fix must target a contract whose initial status was fail."""
        fail_ids = {c.id for c in pipeline_result.contracts if c.status == "fail"}
        for f in pipeline_result.fixes:
            assert f.contract_id in fail_ids


# ---------------------------------------------------------------------------
# Re-verification — stage 6
# ---------------------------------------------------------------------------

class TestReverification:
    def test_reverification_is_reverification_result(self, pipeline_result):
        assert isinstance(pipeline_result.reverification, ReverificationResult)

    def test_reverification_has_contracts(self, pipeline_result):
        assert isinstance(pipeline_result.reverification.contracts, list)

    def test_reverification_applied_count_matches_fixes(self, pipeline_result):
        assert pipeline_result.reverification.applied_fix_count == len(pipeline_result.fixes)

    def test_reverification_summary_total_positive(self, pipeline_result):
        assert pipeline_result.reverification.summary.total > 0

    def test_reverification_summary_counts_sum_to_total(self, pipeline_result):
        s = pipeline_result.reverification.summary
        assert s.passed + s.failed + s.warnings == s.total

    def test_reverification_all_applied_contracts_are_reverified(self, pipeline_result):
        """Every contract targeted by a fix must have reverified=True."""
        fix_ids = {f.contract_id for f in pipeline_result.fixes}
        for c in pipeline_result.reverification.contracts:
            if c.id in fix_ids:
                assert c.reverified is True

    def test_reverification_serialises_to_json(self, pipeline_result):
        raw = json.dumps(pipeline_result.reverification.model_dump())
        assert json.loads(raw)

    def test_all_approved_contracts_are_reverified(self, pipeline_result):
        """Every contract that was approved (fix applied) must have reverified=True."""
        approved_ids = {
            c.id for c in pipeline_result.reverification.contracts if c.approved
        }
        for c in pipeline_result.reverification.contracts:
            if c.id in approved_ids:
                assert c.reverified is True, (
                    f"Contract {c.id} was approved but reverified is False"
                )


# ---------------------------------------------------------------------------
# Elapsed time sanity
# ---------------------------------------------------------------------------

class TestElapsed:
    def test_elapsed_is_positive(self, pipeline_result):
        assert pipeline_result.elapsed_seconds > 0.0

    def test_elapsed_is_reasonable(self, pipeline_result):
        # The pipeline should finish in well under 30 seconds on any machine.
        assert pipeline_result.elapsed_seconds < 30.0


# ---------------------------------------------------------------------------
# run_and_dump convenience wrapper
# ---------------------------------------------------------------------------

class TestRunAndDump:
    def test_returns_string(self):
        result = run_and_dump(SAMPLE_REPO, backend_path=BACKEND_DIR)
        assert isinstance(result, str)

    def test_is_valid_json(self):
        result = run_and_dump(SAMPLE_REPO, backend_path=BACKEND_DIR)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_contains_top_level_keys(self):
        result = run_and_dump(SAMPLE_REPO, backend_path=BACKEND_DIR)
        parsed = json.loads(result)
        assert "contracts" in parsed
        assert "fixes" in parsed
        assert "reverification" in parsed
        assert "summary" in parsed
        assert "trust_score" in parsed
        assert "trust_score_after" in parsed
        assert "elapsed_seconds" in parsed

    def test_indent_respected(self):
        result = run_and_dump(SAMPLE_REPO, backend_path=BACKEND_DIR, indent=4)
        # 4-space indent means lines start with exactly 4 spaces for first level
        lines = result.splitlines()
        first_indented = next((l for l in lines if l.startswith(" ")), "")
        assert first_indented.startswith("    ")


# ---------------------------------------------------------------------------
# Empty / minimal repo corner case
# ---------------------------------------------------------------------------

class TestEmptyRepo:
    def test_empty_repo_returns_pipeline_result(self, tmp_path):
        result = run(tmp_path)
        assert isinstance(result, PipelineResult)

    def test_empty_repo_contracts_empty(self, tmp_path):
        result = run(tmp_path)
        assert result.contracts == []

    def test_empty_repo_fixes_empty(self, tmp_path):
        result = run(tmp_path)
        assert result.fixes == []

    def test_empty_repo_trust_score_zero(self, tmp_path):
        result = run(tmp_path)
        assert result.trust_score == 0.0

    def test_empty_repo_summary_all_zero(self, tmp_path):
        result = run(tmp_path)
        s = result.summary
        assert s.total == 0
        assert s.passed == 0
        assert s.failed == 0
        assert s.warnings == 0
