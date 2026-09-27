"""Tests for the Commands Verification Subagent.

All tests run against two fixtures:
  - sample_repo/   — the real repo with deliberate command errors (lives at
                     project root; discovered via __file__ path arithmetic)
  - tmp_repo       — a per-test scratch directory built with pytest's
                     tmp_path fixture for precise, isolated scenarios

No mock data or stubs are used.  Every assertion exercises live file I/O
through commands_agent.run().

Deliberate errors seeded in sample_repo/:
  - README.md documents `npm start`, but package.json has no "start" script
    (it defines "dev", "build", and "preview" only).
  - README.md documents `python -m uvicorn main:app --reload`, but main.py
    does not exist in sample_repo/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestration.subagents.commands_agent import (
    _dedup_key,
    _extract_commands,
    _infer_context,
    _validate_command,
    _validate_npm_command,
    _validate_pip_command,
    _validate_python_module_command,
    run,
)

# ---------------------------------------------------------------------------
# Locate the canonical sample_repo relative to this test file.
# Layout:  docproof/
#             backend/tests/test_commands_agent.py   ← __file__
#             sample_repo/                            ← target
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REPO = _REPO_ROOT / "sample_repo"


# ---------------------------------------------------------------------------
# Unit tests — pure helpers (no filesystem)
# ---------------------------------------------------------------------------

class TestDeduplicationKey:
    def test_normalises_case(self):
        assert _dedup_key("NPM INSTALL") == "npm install"

    def test_collapses_whitespace(self):
        assert _dedup_key("npm  install") == "npm install"

    def test_identical_commands_produce_same_key(self):
        assert _dedup_key("npm start") == _dedup_key("npm start")


class TestExtractCommands:
    def test_extracts_bash_fenced_block(self):
        text = "# Setup\n\n```bash\nnpm install\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert len(cmds) == 1
        assert cmds[0].raw == "npm install"

    def test_extracts_sh_fenced_block(self):
        text = "```sh\npip install -r requirements.txt\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert any(c.raw == "pip install -r requirements.txt" for c in cmds)

    def test_skips_comment_lines(self):
        text = "```bash\n# This is a comment\nnpm install\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert len(cmds) == 1
        assert cmds[0].raw == "npm install"

    def test_skips_empty_lines(self):
        text = "```bash\n\nnpm install\n\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert len(cmds) == 1

    def test_strips_dollar_prompt(self):
        text = "```bash\n$ npm install\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert cmds[0].raw == "npm install"

    def test_records_source_file(self):
        text = "```bash\nnpm install\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert cmds[0].source_file == "README.md"

    def test_multiple_commands_in_one_block(self):
        text = "```bash\nnpm install\npip install -r requirements.txt\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert len(cmds) == 2

    def test_unlabelled_fence_is_parsed(self):
        text = "```\nnpm install\n```\n"
        cmds = _extract_commands(text, "README.md")
        assert len(cmds) == 1


class TestInferContext:
    def test_install_heading_gives_setup(self):
        text = "## Install dependencies\n\n```bash\nnpm install\n```\n"
        # block starts after the heading and newlines
        idx = text.index("```bash")
        assert _infer_context(text, idx) == "setup"

    def test_run_heading_gives_run(self):
        text = "## Run the server\n\n```bash\nnpm start\n```\n"
        idx = text.index("```bash")
        assert _infer_context(text, idx) == "run"

    def test_build_heading_gives_build(self):
        text = "## Build\n\n```bash\nnpm run build\n```\n"
        idx = text.index("```bash")
        assert _infer_context(text, idx) == "build"

    def test_unknown_heading_gives_default(self):
        text = "## Miscellaneous\n\n```bash\necho hi\n```\n"
        idx = text.index("```bash")
        assert _infer_context(text, idx) == "run"

    def test_no_heading_gives_default(self):
        text = "```bash\nnpm install\n```\n"
        assert _infer_context(text, 0) == "run"


# ---------------------------------------------------------------------------
# Unit tests — validators (isolated with tmp_path)
# ---------------------------------------------------------------------------

class TestValidateNpmCommand:
    def test_install_passes_when_package_json_present(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        r = _validate_npm_command("npm install", tmp_path)
        assert r.status == "pass"

    def test_install_warns_without_package_json(self, tmp_path):
        r = _validate_npm_command("npm install", tmp_path)
        assert r.status == "warning"

    def test_start_fails_when_script_missing(self, tmp_path):
        pkg = {"scripts": {"dev": "vite", "build": "vite build"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        r = _validate_npm_command("npm start", tmp_path)
        assert r.status == "fail"
        assert r.severity == "high"

    def test_start_passes_when_script_present(self, tmp_path):
        pkg = {"scripts": {"start": "node server.js"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        r = _validate_npm_command("npm start", tmp_path)
        assert r.status == "pass"

    def test_run_dev_passes_when_script_present(self, tmp_path):
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        r = _validate_npm_command("npm run dev", tmp_path)
        assert r.status == "pass"

    def test_run_missing_script_fails(self, tmp_path):
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        r = _validate_npm_command("npm run start", tmp_path)
        assert r.status == "fail"

    def test_fail_result_has_suggested_fix(self, tmp_path):
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        r = _validate_npm_command("npm start", tmp_path)
        assert r.suggested_fix and len(r.suggested_fix) > 0

    def test_evidence_snippet_is_scripts_json(self, tmp_path):
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        r = _validate_npm_command("npm start", tmp_path)
        assert r.evidence_snippet and "scripts" in r.evidence_snippet


class TestValidatePipCommand:
    def test_passes_when_requirements_file_exists(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100\n", encoding="utf-8")
        r = _validate_pip_command("pip install -r requirements.txt", tmp_path)
        assert r.status == "pass"

    def test_fails_when_requirements_file_missing(self, tmp_path):
        r = _validate_pip_command("pip install -r requirements.txt", tmp_path)
        assert r.status == "fail"
        assert r.severity == "high"

    def test_direct_install_warns(self, tmp_path):
        r = _validate_pip_command("pip install fastapi", tmp_path)
        assert r.status == "warning"

    def test_evidence_file_named_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        r = _validate_pip_command("pip install -r requirements.txt", tmp_path)
        assert r.evidence_file == "requirements.txt"

    def test_evidence_snippet_shows_deps(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi>=0.100\nuvicorn\n", encoding="utf-8")
        r = _validate_pip_command("pip install -r requirements.txt", tmp_path)
        assert r.evidence_snippet and "fastapi" in r.evidence_snippet


class TestValidatePythonModuleCommand:
    def test_uvicorn_passes_when_main_py_exists(self, tmp_path):
        (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
        r = _validate_python_module_command("python -m uvicorn main:app --reload", tmp_path)
        assert r.status == "pass"

    def test_uvicorn_fails_when_main_py_missing(self, tmp_path):
        r = _validate_python_module_command("python -m uvicorn main:app --reload", tmp_path)
        assert r.status == "fail"
        assert r.severity == "high"

    def test_uvicorn_fail_evidence_file_named(self, tmp_path):
        r = _validate_python_module_command("python -m uvicorn main:app --reload", tmp_path)
        assert r.evidence_file == "main.py"

    def test_uvicorn_fail_has_suggested_fix(self, tmp_path):
        r = _validate_python_module_command("python -m uvicorn main:app --reload", tmp_path)
        assert r.suggested_fix and len(r.suggested_fix) > 0

    def test_dotted_entrypoint_resolved(self, tmp_path):
        # app.main:app → app/main.py
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
        r = _validate_python_module_command("python -m uvicorn app.main:app", tmp_path)
        assert r.status == "pass"


# ---------------------------------------------------------------------------
# Integration tests — real sample_repo with deliberate command errors
# ---------------------------------------------------------------------------

class TestSampleRepoCommands:
    """The sample_repo README.md documents four commands:
        1. npm install           → should pass (package.json exists)
        2. pip install -r requirements.txt → should pass (requirements.txt exists)
        3. npm start             → should FAIL (no "start" script in package.json)
        4. python -m uvicorn main:app --reload → should FAIL (main.py missing)
    """

    @pytest.fixture(scope="class")
    @classmethod
    def contracts(cls):
        return run(SAMPLE_REPO)

    def test_sample_repo_exists(self):
        assert SAMPLE_REPO.is_dir(), f"sample_repo not found at {SAMPLE_REPO}"

    def test_returns_list(self, contracts):
        assert isinstance(contracts, list)

    def test_returns_at_least_four_contracts(self, contracts):
        assert len(contracts) >= 4, (
            f"Expected at least 4 command contracts, got {len(contracts)}: "
            + str([c.claim for c in contracts])
        )

    def test_all_contracts_are_commands_area(self, contracts):
        for c in contracts:
            assert c.area == "commands", f"Contract {c.id} has area={c.area!r}"

    def test_all_contracts_have_non_empty_id(self, contracts):
        for c in contracts:
            assert c.id and c.id.startswith("CMD-")

    def test_all_contracts_have_source_with_line(self, contracts):
        for c in contracts:
            assert "#L" in c.source, f"source missing #L: {c.source!r}"

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

    # --- npm install → pass ---

    def test_npm_install_is_pass(self, contracts):
        c = next((x for x in contracts if x.claim == "npm install"), None)
        assert c is not None, "npm install contract not found"
        assert c.status == "pass"

    def test_npm_install_evidence_file_is_package_json(self, contracts):
        c = next(x for x in contracts if x.claim == "npm install")
        assert c.evidenceFile == "package.json"

    # --- pip install -r requirements.txt → pass ---

    def test_pip_install_is_pass(self, contracts):
        c = next(
            (x for x in contracts if x.claim == "pip install -r requirements.txt"),
            None,
        )
        assert c is not None, "pip install contract not found"
        assert c.status == "pass"

    def test_pip_install_evidence_file_is_requirements_txt(self, contracts):
        c = next(x for x in contracts if x.claim == "pip install -r requirements.txt")
        assert c.evidenceFile == "requirements.txt"

    # --- npm start → FAIL (seeded error) ---

    def test_npm_start_is_fail(self, contracts):
        c = next((x for x in contracts if x.claim == "npm start"), None)
        assert c is not None, "npm start contract not found"
        assert c.status == "fail", (
            f"Expected npm start to fail; got status={c.status!r}, "
            f"evidence={c.evidence!r}"
        )

    def test_npm_start_severity_is_high(self, contracts):
        c = next(x for x in contracts if x.claim == "npm start")
        assert c.severity == "high"

    def test_npm_start_has_suggested_fix(self, contracts):
        c = next(x for x in contracts if x.claim == "npm start")
        assert c.suggested_fix and len(c.suggested_fix) > 0

    def test_npm_start_evidence_mentions_start(self, contracts):
        c = next(x for x in contracts if x.claim == "npm start")
        assert "start" in c.evidence.lower()

    def test_npm_start_evidence_file_is_package_json(self, contracts):
        c = next(x for x in contracts if x.claim == "npm start")
        assert c.evidenceFile == "package.json"

    # --- python -m uvicorn main:app --reload → FAIL (seeded error) ---

    def test_uvicorn_command_is_fail(self, contracts):
        c = next(
            (x for x in contracts if "uvicorn" in x.claim and "main:app" in x.claim),
            None,
        )
        assert c is not None, "uvicorn command contract not found"
        assert c.status == "fail", (
            f"Expected uvicorn command to fail; got status={c.status!r}, "
            f"evidence={c.evidence!r}"
        )

    def test_uvicorn_command_severity_is_high(self, contracts):
        c = next(x for x in contracts if "uvicorn" in x.claim and "main:app" in x.claim)
        assert c.severity == "high"

    def test_uvicorn_command_evidence_file_is_main_py(self, contracts):
        c = next(x for x in contracts if "uvicorn" in x.claim and "main:app" in x.claim)
        assert c.evidenceFile == "main.py"

    def test_uvicorn_command_has_suggested_fix(self, contracts):
        c = next(x for x in contracts if "uvicorn" in x.claim and "main:app" in x.claim)
        assert c.suggested_fix and len(c.suggested_fix) > 0

    # --- all fail contracts have high severity ---

    def test_all_fail_contracts_have_severity_high(self, contracts):
        for c in contracts:
            if c.status == "fail":
                assert c.severity == "high", (
                    f"Contract {c.id} (claim={c.claim!r}) has status=fail "
                    f"but severity={c.severity!r}"
                )

    # --- schema completeness ---

    def test_all_contracts_have_required_fields(self, contracts):
        for c in contracts:
            assert c.id
            assert c.area == "commands"
            assert c.source
            assert c.claim
            assert c.expected
            assert c.actual
            assert c.status in ("pass", "fail", "warning")
            assert c.evidence


# ---------------------------------------------------------------------------
# Isolated scenario tests — use tmp_path for deterministic control
# ---------------------------------------------------------------------------

class TestNoDocFiles:
    def test_empty_repo_returns_empty_list(self, tmp_path):
        assert run(tmp_path) == []


class TestNoCodeBlocks:
    def test_readme_without_code_blocks_returns_empty(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "# My Project\n\nRun npm install first.\n", encoding="utf-8"
        )
        assert run(tmp_path) == []


class TestDeduplication:
    def test_same_command_in_multiple_blocks_deduplicated(self, tmp_path):
        content = (
            "## Setup\n\n```bash\nnpm install\n```\n\n"
            "## Also setup\n\n```bash\nnpm install\n```\n"
        )
        (tmp_path / "README.md").write_text(content, encoding="utf-8")
        (tmp_path / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        contracts = run(tmp_path)
        install_contracts = [c for c in contracts if c.claim == "npm install"]
        assert len(install_contracts) == 1


class TestContractSchema:
    def test_contract_has_all_required_fields(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "## Setup\n\n```bash\nnpm install\n```\n", encoding="utf-8"
        )
        (tmp_path / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        contracts = run(tmp_path)
        assert len(contracts) == 1
        c = contracts[0]
        assert c.id
        assert c.area == "commands"
        assert c.source
        assert c.claim == "npm install"
        assert c.expected
        assert c.actual
        assert c.status in ("pass", "fail", "warning")
        assert c.evidence
        assert c.approvalStatus == "pending"
        assert c.approved is False
        assert c.reverified is False


class TestNpmStartFailScenario:
    """Isolated reproduction of the seeded npm start error."""

    def test_npm_start_fails_without_start_script(self, tmp_path):
        pkg = {"scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "## Run\n\n```bash\nnpm start\n```\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "fail"
        assert contracts[0].severity == "high"

    def test_fail_evidence_names_missing_script(self, tmp_path):
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "## Run\n\n```bash\nnpm start\n```\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert "start" in contracts[0].evidence


class TestUvicornFailScenario:
    """Isolated reproduction of the seeded uvicorn main:app error."""

    def test_uvicorn_fails_without_main_py(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "## Backend\n\n```bash\npython -m uvicorn main:app --reload\n```\n",
            encoding="utf-8",
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "fail"
        assert contracts[0].severity == "high"
        assert contracts[0].evidenceFile == "main.py"

    def test_uvicorn_passes_when_main_py_present(self, tmp_path):
        (tmp_path / "main.py").write_text("app = None\n", encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "## Backend\n\n```bash\npython -m uvicorn main:app --reload\n```\n",
            encoding="utf-8",
        )
        contracts = run(tmp_path)
        assert contracts[0].status == "pass"
