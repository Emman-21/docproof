"""Tests for the Fix Suggestion Subagent.

All tests use in-process contract construction — no filesystem access required.
Every assertion exercises live logic through fix_agent.run() or its internal
helpers.

Coverage:
  - run() filtering:  only fail contracts → suggestions; pass/warning skipped
  - run() ordering:   suggestions follow the fail-contract input order
  - run() schema:     every FixSuggestion has required fields and valid types
  - runtime_requirements fixes
  - commands fixes
  - config_env fixes  (fail: missing from .env.example; warning: undocumented)
  - api_docs fixes
  - _make_diff helper output format
  - unknown area → silently skipped
"""
from __future__ import annotations

import pytest

from app.core.models import DocumentationContract, FixSuggestion
from app.orchestration.subagents.fix_agent import (
    _fix_api_docs,
    _fix_commands,
    _fix_config_env,
    _fix_runtime_requirements,
    _generate_fix,
    _make_diff,
    _source_file,
    run,
)


# ---------------------------------------------------------------------------
# Contract factory helpers
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
        approved=False,
        reverified=False,
        severity=severity,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# _source_file helper
# ---------------------------------------------------------------------------

class TestSourceFile:
    def test_strips_line_anchor(self):
        c = _make_contract(source="README.md#L42")
        assert _source_file(c) == "README.md"

    def test_nested_path(self):
        c = _make_contract(source="docs/api.md#L7")
        assert _source_file(c) == "docs/api.md"

    def test_no_anchor(self):
        c = _make_contract(source="README.md")
        assert _source_file(c) == "README.md"


# ---------------------------------------------------------------------------
# _make_diff helper
# ---------------------------------------------------------------------------

class TestMakeDiff:
    def test_diff_has_minus_header(self):
        d = _make_diff("README.md", ["old line"], ["new line"])
        assert "--- a/README.md" in d

    def test_diff_has_plus_header(self):
        d = _make_diff("README.md", ["old line"], ["new line"])
        assert "+++ b/README.md" in d

    def test_diff_has_hunk_header(self):
        d = _make_diff("README.md", ["old line"], ["new line"])
        assert "@@" in d

    def test_old_lines_prefixed_minus(self):
        d = _make_diff("README.md", ["old line"], ["new line"])
        assert "-old line" in d

    def test_new_lines_prefixed_plus(self):
        d = _make_diff("README.md", ["old line"], ["new line"])
        assert "+new line" in d

    def test_empty_old_lines(self):
        d = _make_diff(".env.example", [], ["NEW_VAR=value"])
        # No removal lines (lines starting with a single "-", not "---")
        removal_lines = [l for l in d.splitlines() if l.startswith("-") and not l.startswith("---")]
        assert removal_lines == []

    def test_empty_new_lines(self):
        d = _make_diff("api.md", ["GET /gone"], [])
        # No addition lines (lines starting with a single "+", not "+++")
        addition_lines = [l for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
        assert addition_lines == []

    def test_context_header_included(self):
        d = _make_diff("f.md", ["a"], ["b"], context_header="Prerequisites")
        assert "Prerequisites" in d

    def test_multiple_old_lines(self):
        d = _make_diff("f.md", ["line1", "line2"], ["line3"])
        assert "-line1" in d
        assert "-line2" in d

    def test_hunk_counts_match_lines(self):
        d = _make_diff("f.md", ["a", "b", "c"], ["x", "y"])
        # @@ -1,3 +1,2 @@
        assert "-1,3" in d
        assert "+1,2" in d


# ---------------------------------------------------------------------------
# _fix_runtime_requirements
# ---------------------------------------------------------------------------

class TestFixRuntimeRequirements:
    def _contract(self, **kw) -> DocumentationContract:
        defaults = dict(
            area="runtime_requirements",
            source="README.md#L9",
            claim="- **Node.js 18+** — the minimum version required to run the frontend.",
            expected="Node.js >=18",
            actual="Node.js >=20",
            status="fail",
            suggested_fix="Update docs to Node.js 20+.",
        )
        defaults.update(kw)
        return _make_contract(**defaults)

    def test_returns_fix_suggestion(self):
        fix = _fix_runtime_requirements(self._contract())
        assert isinstance(fix, FixSuggestion)

    def test_patch_type_is_doc_edit(self):
        fix = _fix_runtime_requirements(self._contract())
        assert fix.patch_type == "doc_edit"

    def test_target_file_is_doc_file(self):
        fix = _fix_runtime_requirements(self._contract())
        assert fix.target_file == "README.md"

    def test_diff_contains_old_version(self):
        fix = _fix_runtime_requirements(self._contract())
        assert "18" in fix.diff

    def test_diff_contains_new_version(self):
        fix = _fix_runtime_requirements(self._contract())
        assert "20" in fix.diff

    def test_diff_minus_has_old_claim(self):
        fix = _fix_runtime_requirements(self._contract())
        assert any(line.startswith("-") and "18" in line for line in fix.diff.splitlines())

    def test_diff_plus_has_new_version(self):
        fix = _fix_runtime_requirements(self._contract())
        assert any(line.startswith("+") and "20" in line for line in fix.diff.splitlines())

    def test_raw_fix_contains_corrected_version(self):
        fix = _fix_runtime_requirements(self._contract())
        assert "20" in fix.raw_fix

    def test_contract_id_preserved(self):
        c = self._contract(id="RT-NODE-ABCDEF")
        fix = _fix_runtime_requirements(c)
        assert fix.contract_id == "RT-NODE-ABCDEF"

    def test_area_preserved(self):
        fix = _fix_runtime_requirements(self._contract())
        assert fix.area == "runtime_requirements"

    def test_python_version_fix(self):
        c = _make_contract(
            area="runtime_requirements",
            source="README.md#L11",
            claim="- **Python 3.9+** — required for the backend.",
            expected="Python >=3.9",
            actual="Python >=3.11",
            status="fail",
        )
        fix = _fix_runtime_requirements(c)
        assert "3.9" in fix.diff
        assert "3.11" in fix.diff


# ---------------------------------------------------------------------------
# _fix_commands
# ---------------------------------------------------------------------------

class TestFixCommands:
    def _contract(self, **kw) -> DocumentationContract:
        defaults = dict(
            area="commands",
            source="README.md#L24",
            claim="npm start",
            expected='script "start" defined in package.json',
            actual='script "start" not found in package.json scripts',
            status="fail",
            evidenceSnippet='{"scripts": {"dev": "vite", "build": "vite build"}}',
            suggested_fix=(
                'Add a "start" script to the "scripts" block in package.json, '
                "or update the documentation to use one of the existing scripts: "
                "['dev', 'build']. Try `npm run dev` instead."
            ),
        )
        defaults.update(kw)
        return _make_contract(**defaults)

    def test_returns_fix_suggestion(self):
        fix = _fix_commands(self._contract())
        assert isinstance(fix, FixSuggestion)

    def test_patch_type_is_doc_edit(self):
        fix = _fix_commands(self._contract())
        assert fix.patch_type == "doc_edit"

    def test_target_file_is_doc_file(self):
        fix = _fix_commands(self._contract())
        assert fix.target_file == "README.md"

    def test_diff_removes_old_command(self):
        fix = _fix_commands(self._contract())
        assert any(line.startswith("-") and "npm start" in line for line in fix.diff.splitlines())

    def test_diff_adds_replacement(self):
        fix = _fix_commands(self._contract())
        plus_lines = [l for l in fix.diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        assert len(plus_lines) > 0

    def test_raw_fix_is_replacement_command(self):
        fix = _fix_commands(self._contract())
        # raw_fix should be the new command extracted from suggested_fix
        assert fix.raw_fix  # non-empty

    def test_contract_id_preserved(self):
        c = self._contract(id="CMD-XXXXXXXX")
        fix = _fix_commands(c)
        assert fix.contract_id == "CMD-XXXXXXXX"

    def test_fallback_when_no_suggested_fix(self):
        """When suggested_fix is empty, raw_fix falls back to actual."""
        c = self._contract(suggested_fix="", actual="npm run dev")
        fix = _fix_commands(c)
        assert fix.raw_fix == "npm run dev"

    def test_backtick_command_extracted(self):
        """Backtick-wrapped command in suggested_fix is unwrapped."""
        c = self._contract(suggested_fix="Use `npm run dev` instead.")
        fix = _fix_commands(c)
        assert fix.raw_fix == "npm run dev"


# ---------------------------------------------------------------------------
# _fix_config_env
# ---------------------------------------------------------------------------

class TestFixConfigEnvFail:
    """Fail case: key used in source but missing from .env.example."""

    def _contract(self, **kw) -> DocumentationContract:
        defaults = dict(
            area="config_env",
            source="src/config.py#L16",
            claim="`SECRET_KEY` is used in source code",
            expected="SECRET_KEY declared in .env.example",
            actual="SECRET_KEY missing from .env.example",
            status="fail",
            evidenceFile="src/config.py",
            evidenceSnippet='SECRET_KEY: str = os.environ.get("SECRET_KEY", "")',
            suggested_fix="Add `SECRET_KEY=<your-value>` to .env.example.",
        )
        defaults.update(kw)
        return _make_contract(**defaults)

    def test_returns_fix_suggestion(self):
        assert isinstance(_fix_config_env(self._contract()), FixSuggestion)

    def test_patch_type_is_config_edit(self):
        fix = _fix_config_env(self._contract())
        assert fix.patch_type == "config_edit"

    def test_target_file_is_env_example(self):
        fix = _fix_config_env(self._contract())
        assert fix.target_file == ".env.example"

    def test_diff_adds_new_entry(self):
        fix = _fix_config_env(self._contract())
        plus_lines = [l for l in fix.diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        assert any("SECRET_KEY" in l for l in plus_lines)

    def test_raw_fix_mentions_env_example(self):
        fix = _fix_config_env(self._contract())
        assert ".env.example" in fix.raw_fix

    def test_raw_fix_contains_var_name(self):
        fix = _fix_config_env(self._contract())
        assert "SECRET_KEY" in fix.raw_fix

    def test_contract_id_preserved(self):
        c = self._contract(id="CFG-SECRET_K-ABCDEF")
        assert _fix_config_env(c).contract_id == "CFG-SECRET_K-ABCDEF"


class TestFixConfigEnvWarning:
    """Warning case: key in .env.example but not documented."""

    def _contract(self, **kw) -> DocumentationContract:
        defaults = dict(
            area="config_env",
            source=".env.example",
            claim="`PORT` is declared in .env.example but not documented",
            expected="PORT documented in README or docs",
            actual="PORT present in .env.example only",
            status="warning",
            evidenceFile=".env.example",
            evidenceSnippet="PORT=3000",
            suggested_fix=(
                "Add a description of `PORT` to the README so that developers "
                "know what value to supply when setting up the project."
            ),
        )
        defaults.update(kw)
        return _make_contract(**defaults, severity="low")

    def test_returns_fix_suggestion(self):
        assert isinstance(_fix_config_env(self._contract()), FixSuggestion)

    def test_patch_type_is_doc_edit(self):
        fix = _fix_config_env(self._contract())
        assert fix.patch_type == "doc_edit"

    def test_target_file_is_readme(self):
        fix = _fix_config_env(self._contract())
        assert "README" in fix.target_file or fix.target_file.endswith(".md")

    def test_diff_adds_doc_entry(self):
        fix = _fix_config_env(self._contract())
        plus_lines = [l for l in fix.diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        assert any("PORT" in l for l in plus_lines)

    def test_raw_fix_mentions_readme(self):
        fix = _fix_config_env(self._contract())
        assert "README" in fix.raw_fix or ".md" in fix.raw_fix


# ---------------------------------------------------------------------------
# _fix_api_docs
# ---------------------------------------------------------------------------

class TestFixApiDocs:
    def _contract(self, **kw) -> DocumentationContract:
        defaults = dict(
            area="api_docs",
            source="docs/api.md#L22",
            claim="DELETE /contracts/{id} is documented",
            expected="DELETE /contracts/{id} implemented in backend",
            actual="DELETE /contracts/{id} not found in backend routes",
            status="fail",
            evidenceFile="docs/api.md",
            evidenceSnippet="DELETE /contracts/{id}",
            suggested_fix=(
                "Either implement DELETE /contracts/{id} in the FastAPI backend "
                "or remove it from the documentation."
            ),
        )
        defaults.update(kw)
        return _make_contract(**defaults)

    def test_returns_fix_suggestion(self):
        assert isinstance(_fix_api_docs(self._contract()), FixSuggestion)

    def test_patch_type_is_doc_edit(self):
        fix = _fix_api_docs(self._contract())
        assert fix.patch_type == "doc_edit"

    def test_target_file_is_doc_file(self):
        fix = _fix_api_docs(self._contract())
        assert fix.target_file == "docs/api.md"

    def test_diff_removes_phantom_endpoint(self):
        fix = _fix_api_docs(self._contract())
        minus_lines = [l for l in fix.diff.splitlines() if l.startswith("-") and not l.startswith("---")]
        assert any("DELETE" in l and "/contracts" in l for l in minus_lines)

    def test_raw_fix_offers_two_options(self):
        fix = _fix_api_docs(self._contract())
        assert "remove" in fix.raw_fix.lower() or "either" in fix.raw_fix.lower()

    def test_raw_fix_contains_fastapi_stub(self):
        fix = _fix_api_docs(self._contract())
        assert "@router.delete" in fix.raw_fix

    def test_contract_id_preserved(self):
        c = self._contract(id="API-ABCDEF12")
        assert _fix_api_docs(c).contract_id == "API-ABCDEF12"

    def test_get_status_endpoint_fix(self):
        c = _make_contract(
            area="api_docs",
            source="docs/api.md#L56",
            claim="GET /status is documented",
            expected="GET /status implemented in backend",
            actual="GET /status not found in backend routes",
            status="fail",
            evidenceFile="docs/api.md",
            evidenceSnippet="GET /status",
            suggested_fix="Remove GET /status from the documentation or implement it.",
        )
        fix = _fix_api_docs(c)
        assert "GET" in fix.diff
        assert "/status" in fix.diff


# ---------------------------------------------------------------------------
# _generate_fix dispatcher
# ---------------------------------------------------------------------------

class TestGenerateFix:
    def test_runtime_area_dispatched(self):
        c = _make_contract(area="runtime_requirements", status="fail")
        fix = _generate_fix(c)
        assert fix is not None
        assert fix.area == "runtime_requirements"

    def test_commands_area_dispatched(self):
        c = _make_contract(area="commands", status="fail")
        fix = _generate_fix(c)
        assert fix is not None
        assert fix.area == "commands"

    def test_config_env_area_dispatched(self):
        c = _make_contract(area="config_env", status="fail")
        fix = _generate_fix(c)
        assert fix is not None
        assert fix.area == "config_env"

    def test_api_docs_area_dispatched(self):
        c = _make_contract(area="api_docs", status="fail")
        fix = _generate_fix(c)
        assert fix is not None
        assert fix.area == "api_docs"


# ---------------------------------------------------------------------------
# run() — filtering and ordering
# ---------------------------------------------------------------------------

class TestRunFiltering:
    def test_empty_input_returns_empty(self):
        assert run([]) == []

    def test_pass_contracts_skipped(self):
        c = _make_contract(status="pass")
        assert run([c]) == []

    def test_warning_contracts_skipped(self):
        c = _make_contract(status="warning", severity="low")
        assert run([c]) == []

    def test_fail_contracts_processed(self):
        c = _make_contract(status="fail")
        fixes = run([c])
        assert len(fixes) == 1

    def test_mixed_list_only_fails_processed(self):
        contracts = [
            _make_contract(id="A", status="pass"),
            _make_contract(id="B", status="fail"),
            _make_contract(id="C", status="warning", severity="low"),
            _make_contract(id="D", status="fail"),
        ]
        fixes = run(contracts)
        assert len(fixes) == 2

    def test_ordering_preserved(self):
        contracts = [
            _make_contract(id="FIRST", status="fail"),
            _make_contract(id="SECOND", status="fail"),
        ]
        fixes = run(contracts)
        assert fixes[0].contract_id == "FIRST"
        assert fixes[1].contract_id == "SECOND"

    def test_all_areas_handled(self):
        """run() must produce one fix per failing contract across all four areas."""
        contracts = [
            _make_contract(id="R1", area="runtime_requirements", status="fail"),
            _make_contract(id="C1", area="commands", status="fail",
                           claim="npm start", expected="script present", actual="script absent"),
            _make_contract(id="E1", area="config_env", status="fail",
                           claim="`SECRET_KEY` is used in source code",
                           expected="SECRET_KEY declared in .env.example",
                           actual="SECRET_KEY missing from .env.example"),
            _make_contract(id="A1", area="api_docs", status="fail",
                           claim="DELETE /x is documented",
                           expected="DELETE /x implemented",
                           actual="DELETE /x not found in backend routes"),
        ]
        fixes = run(contracts)
        assert len(fixes) == 4
        areas = {f.area for f in fixes}
        assert areas == {"runtime_requirements", "commands", "config_env", "api_docs"}


# ---------------------------------------------------------------------------
# run() — schema compliance
# ---------------------------------------------------------------------------

class TestRunSchema:
    def test_fix_suggestion_has_contract_id(self):
        fixes = run([_make_contract(id="SCHEMA-001", status="fail")])
        assert fixes[0].contract_id == "SCHEMA-001"

    def test_fix_suggestion_has_area(self):
        fixes = run([_make_contract(status="fail")])
        assert fixes[0].area in ("runtime_requirements", "commands", "config_env", "api_docs")

    def test_fix_suggestion_has_patch_type(self):
        fixes = run([_make_contract(status="fail")])
        assert fixes[0].patch_type in ("doc_edit", "config_edit", "code_edit")

    def test_fix_suggestion_has_non_empty_target_file(self):
        fixes = run([_make_contract(status="fail")])
        assert fixes[0].target_file

    def test_fix_suggestion_has_non_empty_description(self):
        fixes = run([_make_contract(status="fail")])
        assert fixes[0].description

    def test_fix_suggestion_has_non_empty_diff(self):
        fixes = run([_make_contract(status="fail")])
        assert fixes[0].diff

    def test_fix_suggestion_diff_is_valid_format(self):
        fixes = run([_make_contract(status="fail")])
        d = fixes[0].diff
        assert "---" in d and "+++" in d and "@@" in d

    def test_fix_suggestion_has_non_empty_raw_fix(self):
        fixes = run([_make_contract(status="fail")])
        assert fixes[0].raw_fix

    def test_fix_suggestion_is_pydantic_model(self):
        fixes = run([_make_contract(status="fail")])
        assert isinstance(fixes[0], FixSuggestion)

    def test_fix_suggestion_serialises_to_json(self):
        fixes = run([_make_contract(status="fail")])
        data = fixes[0].model_dump()
        assert "contract_id" in data
        assert "diff" in data
        assert "patch_type" in data


# ---------------------------------------------------------------------------
# Integration: run() against the seed contracts in db.py
# ---------------------------------------------------------------------------

class TestRunAgainstSeedContracts:
    """Verifies the agent works end-to-end with the seeded db contracts."""

    @pytest.fixture(scope="class")
    @classmethod
    def seed_fixes(cls):
        from app.storage import db
        contracts = db.all_contracts()
        return run(contracts)

    def test_returns_list(self, seed_fixes):
        assert isinstance(seed_fixes, list)

    def test_count_matches_seed_fail_count(self, seed_fixes):
        from app.storage import db
        failing = [c for c in db.all_contracts() if c.status == "fail"]
        assert len(seed_fixes) == len(failing)

    def test_all_seed_fixes_have_diff(self, seed_fixes):
        for fix in seed_fixes:
            assert fix.diff, f"Empty diff for contract {fix.contract_id}"

    def test_all_seed_fixes_have_raw_fix(self, seed_fixes):
        for fix in seed_fixes:
            assert fix.raw_fix, f"Empty raw_fix for contract {fix.contract_id}"

    def test_all_seed_fixes_valid_patch_type(self, seed_fixes):
        for fix in seed_fixes:
            assert fix.patch_type in ("doc_edit", "config_edit", "code_edit")

    def test_all_seed_fixes_valid_area(self, seed_fixes):
        for fix in seed_fixes:
            assert fix.area in ("runtime_requirements", "commands", "config_env", "api_docs")

    def test_dp001_fix_contract_id(self, seed_fixes):
        fix = next((f for f in seed_fixes if f.contract_id == "DP-001"), None)
        assert fix is not None, "No fix for DP-001 (Node.js version mismatch)"

    def test_dp001_fix_updates_node_version(self, seed_fixes):
        fix = next(f for f in seed_fixes if f.contract_id == "DP-001")
        assert "20" in fix.diff

    def test_dp005_fix_contract_id(self, seed_fixes):
        fix = next((f for f in seed_fixes if f.contract_id == "DP-005"), None)
        assert fix is not None, "No fix for DP-005 (npm start → npm run dev)"

    def test_dp010_fix_contract_id(self, seed_fixes):
        fix = next((f for f in seed_fixes if f.contract_id == "DP-010"), None)
        assert fix is not None, "No fix for DP-010 (missing JWT_SECRET env var)"

    def test_dp015_fix_contract_id(self, seed_fixes):
        fix = next((f for f in seed_fixes if f.contract_id == "DP-015"), None)
        assert fix is not None, "No fix for DP-015 (wrong POST /api/users/create route)"
