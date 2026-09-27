"""Tests for the Runtime Requirements Verification Subagent.

All tests run against two fixtures:
  - sample_repo/   — the real repo with deliberate mismatches (lives at
                     project root; discovered via __file__ path arithmetic)
  - tmp_repo       — a per-test scratch directory built with pytest's
                     tmp_path fixture for precise, isolated scenarios

No mock data or stubs are used.  Every assertion exercises live file I/O
through runtime_agent.run().
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestration.subagents.runtime_agent import (
    _normalise_semver,
    _version_tuple,
    _versions_match,
    run,
)

# ---------------------------------------------------------------------------
# Locate the canonical sample_repo relative to this test file.
# Layout:  docproof/
#             backend/tests/test_runtime_agent.py   ← __file__
#             sample_repo/                           ← target
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REPO = _REPO_ROOT / "sample_repo"


# ---------------------------------------------------------------------------
# Unit tests — pure helpers (no filesystem)
# ---------------------------------------------------------------------------

class TestNormaliseSemver:
    def test_strips_gte(self):
        assert _normalise_semver(">=20.0.0") == "20.0.0"

    def test_strips_tilde(self):
        assert _normalise_semver("~3.11") == "3.11"

    def test_strips_caret(self):
        assert _normalise_semver("^18") == "18"

    def test_plain_number_unchanged(self):
        assert _normalise_semver("18") == "18"

    def test_strips_whitespace(self):
        assert _normalise_semver("  >=20.0.0  ") == "20.0.0"


class TestVersionTuple:
    def test_major_minor(self):
        assert _version_tuple("3.11") == (3, 11)

    def test_major_only(self):
        assert _version_tuple("20") == (20,)

    def test_major_minor_patch(self):
        assert _version_tuple("20.0.0") == (20, 0, 0)

    def test_invalid_returns_zero(self):
        assert _version_tuple("not-a-version") == (0,)


class TestVersionsMatch:
    def test_equal_versions_match(self):
        assert _versions_match("18", "18") is True

    def test_equal_dotted_versions_match(self):
        assert _versions_match("3.11", "3.11") is True

    def test_different_major_does_not_match(self):
        assert _versions_match("18", "20") is False

    def test_different_minor_does_not_match(self):
        assert _versions_match("3.9", "3.11") is False


# ---------------------------------------------------------------------------
# Integration tests — real sample_repo with deliberate mismatches
# ---------------------------------------------------------------------------

class TestSampleRepoMismatches:
    """The sample_repo is seeded with three deliberate mismatches.

    README claims       | Config declares      | Expected status
    --------------------|----------------------|----------------
    Node.js 18+         | engines.node >=20.0.0| fail
    npm 8+              | engines.npm >=10.0.0 | fail
    Python 3.9+         | setup.cfg >=3.11     | fail
    """

    @pytest.fixture(scope="class")
    @classmethod
    def contracts(cls):
        return run(SAMPLE_REPO)

    def test_sample_repo_exists(self):
        assert SAMPLE_REPO.is_dir(), f"sample_repo not found at {SAMPLE_REPO}"

    def test_returns_list(self, contracts):
        assert isinstance(contracts, list)

    def test_returns_at_least_three_contracts(self, contracts):
        assert len(contracts) >= 3

    def test_all_contracts_are_runtime_area(self, contracts):
        for c in contracts:
            assert c.area == "runtime_requirements"

    def test_all_contracts_have_non_empty_id(self, contracts):
        for c in contracts:
            assert c.id and len(c.id) > 0

    def test_all_contracts_have_source_with_line(self, contracts):
        for c in contracts:
            assert "#L" in c.source, f"source missing #L: {c.source!r}"

    def test_node_mismatch_is_detected(self, contracts):
        node_contract = next(
            (c for c in contracts if "Node.js" in c.expected), None
        )
        assert node_contract is not None, "No Node.js contract found"
        assert node_contract.status == "fail"

    def test_node_expected_is_18(self, contracts):
        node = next(c for c in contracts if "Node.js" in c.expected)
        assert "18" in node.expected

    def test_node_actual_is_20(self, contracts):
        node = next(c for c in contracts if "Node.js" in c.expected)
        assert "20" in node.actual

    def test_node_evidence_file_is_package_json(self, contracts):
        node = next(c for c in contracts if "Node.js" in c.expected)
        assert node.evidenceFile == "package.json"

    def test_node_evidence_snippet_contains_engines(self, contracts):
        node = next(c for c in contracts if "Node.js" in c.expected)
        assert "engines" in node.evidenceSnippet

    def test_node_suggested_fix_is_non_empty(self, contracts):
        node = next(c for c in contracts if "Node.js" in c.expected)
        assert node.suggested_fix and len(node.suggested_fix) > 0

    def test_npm_mismatch_is_detected(self, contracts):
        npm_contract = next(
            (c for c in contracts if "npm >=" in c.expected), None
        )
        assert npm_contract is not None, "No npm contract found"
        assert npm_contract.status == "fail"

    def test_npm_expected_is_8(self, contracts):
        npm = next(c for c in contracts if "npm >=" in c.expected)
        assert "8" in npm.expected

    def test_npm_actual_is_10(self, contracts):
        npm = next(c for c in contracts if "npm >=" in c.expected)
        assert "10" in npm.actual

    def test_python_mismatch_is_detected(self, contracts):
        py_contract = next(
            (c for c in contracts if "Python >=" in c.expected), None
        )
        assert py_contract is not None, "No Python contract found"
        assert py_contract.status == "fail"

    def test_python_expected_is_3_9(self, contracts):
        py = next(c for c in contracts if "Python >=" in c.expected)
        assert "3.9" in py.expected

    def test_python_actual_is_3_11(self, contracts):
        py = next(c for c in contracts if "Python >=" in c.expected)
        assert "3.11" in py.actual

    def test_python_evidence_file_is_setup_cfg(self, contracts):
        py = next(c for c in contracts if "Python >=" in c.expected)
        assert py.evidenceFile == "setup.cfg"

    def test_all_failing_contracts_have_severity_high(self, contracts):
        for c in contracts:
            if c.status == "fail":
                assert c.severity == "high", (
                    f"Contract {c.id} has status=fail but severity={c.severity}"
                )

    def test_all_contracts_start_as_pending(self, contracts):
        for c in contracts:
            assert c.approvalStatus == "pending"

    def test_all_contracts_not_approved(self, contracts):
        for c in contracts:
            assert c.approved is False

    def test_all_contracts_not_reverified(self, contracts):
        for c in contracts:
            assert c.reverified is False

    def test_ids_are_unique(self, contracts):
        ids = [c.id for c in contracts]
        assert len(ids) == len(set(ids)), "Duplicate contract IDs detected"


# ---------------------------------------------------------------------------
# Isolated scenario tests — use tmp_path for deterministic control
# ---------------------------------------------------------------------------

class TestNoDocFiles:
    def test_empty_repo_returns_empty_list(self, tmp_path):
        result = run(tmp_path)
        assert result == []


class TestNoConfigFiles:
    """README declares runtimes but no config files exist → warning contracts."""

    def test_missing_config_produces_warning(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "warning"

    def test_warning_contract_has_suggested_fix(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert contracts[0].suggested_fix and len(contracts[0].suggested_fix) > 0


class TestPassScenario:
    """README and package.json agree on the same Node version → pass."""

    def test_matching_versions_produce_pass(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=18"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "pass"

    def test_pass_contract_has_no_suggested_fix(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=18"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert contracts[0].suggested_fix == ""

    def test_pass_contract_has_no_severity(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=18"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert contracts[0].severity is None


class TestFailScenario:
    """README claims 18+, package.json requires 20+ → fail."""

    def test_mismatch_produces_fail(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=20.0.0"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "fail"

    def test_fail_contract_evidence_mentions_both_versions(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=20.0.0"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        evidence = contracts[0].evidence
        assert "18" in evidence
        assert "20" in evidence

    def test_fail_contract_severity_is_high(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=20.0.0"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert contracts[0].severity == "high"


class TestNvmrcFallback:
    """When package.json has no engines.node, .nvmrc is used instead."""

    def test_nvmrc_used_when_no_engines_node(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "test"}), encoding="utf-8"
        )
        (tmp_path / ".nvmrc").write_text("20\n", encoding="utf-8")
        contracts = run(tmp_path)
        assert contracts[0].status == "fail"
        assert contracts[0].evidenceFile == ".nvmrc"


class TestPythonSetupCfg:
    """Python constraint read from setup.cfg."""

    def test_setup_cfg_python_requires_detected(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Python 3.9+ for the backend.\n", encoding="utf-8"
        )
        (tmp_path / "setup.cfg").write_text(
            "[options]\npython_requires = >=3.11\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "fail"
        assert "3.9" in contracts[0].expected
        assert "3.11" in contracts[0].actual

    def test_setup_cfg_evidence_file_named(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Python 3.9+ for the backend.\n", encoding="utf-8"
        )
        (tmp_path / "setup.cfg").write_text(
            "[options]\npython_requires = >=3.11\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert contracts[0].evidenceFile == "setup.cfg"


class TestDeduplication:
    """Duplicate claims in the same file should produce only one contract."""

    def test_duplicate_runtime_claim_deduplicated(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+.\n"
            "Node.js 18+ is strongly recommended.\n",
            encoding="utf-8",
        )
        (tmp_path / "package.json").write_text(
            json.dumps({"engines": {"node": ">=20.0.0"}}), encoding="utf-8"
        )
        contracts = run(tmp_path)
        node_contracts = [c for c in contracts if "Node.js" in c.expected]
        assert len(node_contracts) == 1


class TestContractSchema:
    """Every returned contract must satisfy the DocumentationContract schema."""

    def test_contract_has_required_fields(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "Requires Node.js 18+ to run.\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        c = contracts[0]
        assert c.id
        assert c.area == "runtime_requirements"
        assert c.source
        assert c.claim
        assert c.expected
        assert c.actual
        assert c.status in ("pass", "fail", "warning")
        assert c.evidence
        assert c.approvalStatus == "pending"
        assert c.approved is False
        assert c.reverified is False
