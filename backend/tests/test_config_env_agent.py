"""Tests for the Config / Environment Variable Verification Subagent.

All tests run against two fixtures:
  - sample_repo/   — the real repo with deliberate config errors (lives at
                     project root; discovered via __file__ path arithmetic)
  - tmp_repo       — a per-test scratch directory built with pytest's
                     tmp_path fixture for precise, isolated scenarios

No mock data or stubs are used.  Every assertion exercises live file I/O
through config_env_agent.run().

Deliberate errors seeded in sample_repo/:
  - .env.example declares DATABASE_URL, API_KEY, and PORT.
  - README.md documents DATABASE_URL and API_KEY but NOT PORT.
    → PORT should produce a "warning" contract.
  - sample_repo/src/config.py uses SECRET_KEY via os.environ.get() but
    SECRET_KEY is NOT declared in .env.example.
    → SECRET_KEY should produce a "fail" contract.
  - DATABASE_URL and API_KEY are both declared and documented.
    → Each should produce a "pass" contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestration.subagents.config_env_agent import (
    _parse_env_example,
    _extract_doc_keys,
    _extract_source_keys,
    _scan_docs,
    _scan_source,
    run,
)

# ---------------------------------------------------------------------------
# Locate the canonical sample_repo relative to this test file.
# Layout:  docproof/
#             backend/tests/test_config_env_agent.py   ← __file__
#             sample_repo/                              ← target
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REPO = _REPO_ROOT / "sample_repo"


# ---------------------------------------------------------------------------
# Unit tests — _parse_env_example
# ---------------------------------------------------------------------------

class TestParseEnvExample:
    def test_returns_empty_when_no_env_example(self, tmp_path):
        result = _parse_env_example(tmp_path)
        assert result == {}

    def test_parses_key_value(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "DATABASE_URL=postgresql://localhost/db\n", encoding="utf-8"
        )
        result = _parse_env_example(tmp_path)
        assert "DATABASE_URL" in result
        assert result["DATABASE_URL"] == "postgresql://localhost/db"

    def test_parses_empty_value(self, tmp_path):
        (tmp_path / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
        result = _parse_env_example(tmp_path)
        assert "API_KEY" in result
        assert result["API_KEY"] == ""

    def test_skips_comment_lines(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "# This is a comment\nAPI_KEY=secret\n", encoding="utf-8"
        )
        result = _parse_env_example(tmp_path)
        assert len(result) == 1
        assert "API_KEY" in result

    def test_skips_blank_lines(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "\nAPI_KEY=secret\n\n", encoding="utf-8"
        )
        result = _parse_env_example(tmp_path)
        assert len(result) == 1

    def test_skips_lowercase_keys(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "debug=true\nAPI_KEY=secret\n", encoding="utf-8"
        )
        result = _parse_env_example(tmp_path)
        assert "debug" not in result
        assert "API_KEY" in result

    def test_parses_multiple_keys(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "DATABASE_URL=db\nAPI_KEY=key\nPORT=3000\n", encoding="utf-8"
        )
        result = _parse_env_example(tmp_path)
        assert set(result.keys()) == {"DATABASE_URL", "API_KEY", "PORT"}

    def test_sample_repo_env_example(self):
        result = _parse_env_example(SAMPLE_REPO)
        assert "DATABASE_URL" in result
        assert "API_KEY" in result
        assert "PORT" in result


# ---------------------------------------------------------------------------
# Unit tests — _extract_doc_keys
# ---------------------------------------------------------------------------

class TestExtractDocKeys:
    def test_backtick_key_extracted(self):
        keys = _extract_doc_keys("Set `DATABASE_URL` to point at your DB.\n")
        assert "DATABASE_URL" in keys

    def test_list_item_key_extracted(self):
        keys = _extract_doc_keys("- `API_KEY` — required. API key.\n")
        assert "API_KEY" in keys

    def test_list_item_without_backticks(self):
        keys = _extract_doc_keys("- API_KEY — required.\n")
        assert "API_KEY" in keys

    def test_ignores_lowercase_words(self):
        keys = _extract_doc_keys("Copy `.env.example` to `.env`.\n")
        # .env.example and .env are not env var names
        assert "ENV" not in keys

    def test_multiple_keys_in_one_line(self):
        keys = _extract_doc_keys("Set `DATABASE_URL` and `API_KEY`.\n")
        assert "DATABASE_URL" in keys
        assert "API_KEY" in keys


# ---------------------------------------------------------------------------
# Unit tests — _extract_source_keys
# ---------------------------------------------------------------------------

class TestExtractSourceKeys:
    def test_os_environ_get(self):
        text = 'DB = os.environ.get("DATABASE_URL", "")\n'
        result = _extract_source_keys(text, ".py")
        keys = [k for k, _ in result]
        assert "DATABASE_URL" in keys

    def test_os_environ_bracket(self):
        text = 'key = os.environ["API_KEY"]\n'
        result = _extract_source_keys(text, ".py")
        keys = [k for k, _ in result]
        assert "API_KEY" in keys

    def test_os_getenv(self):
        text = 'secret = os.getenv("SECRET_KEY", "")\n'
        result = _extract_source_keys(text, ".py")
        keys = [k for k, _ in result]
        assert "SECRET_KEY" in keys

    def test_process_env_js(self):
        text = "const db = process.env.DATABASE_URL;\n"
        result = _extract_source_keys(text, ".js")
        keys = [k for k, _ in result]
        assert "DATABASE_URL" in keys

    def test_process_env_ts(self):
        text = "const key: string = process.env.API_KEY ?? '';\n"
        result = _extract_source_keys(text, ".ts")
        keys = [k for k, _ in result]
        assert "API_KEY" in keys

    def test_line_numbers_correct(self):
        text = "# first line\nDB = os.environ.get('DATABASE_URL', '')\n"
        result = _extract_source_keys(text, ".py")
        assert result[0][1] == 2

    def test_false_positive_keys_excluded(self):
        text = 'path = os.environ.get("PATH", "")\n'
        result = _extract_source_keys(text, ".py")
        keys = [k for k, _ in result]
        assert "PATH" not in keys

    def test_no_matches_returns_empty_list(self):
        text = "x = 1 + 2\n"
        result = _extract_source_keys(text, ".py")
        assert result == []


# ---------------------------------------------------------------------------
# Integration tests — real sample_repo with deliberate config errors
# ---------------------------------------------------------------------------

class TestSampleRepoConfig:
    """The sample_repo is seeded with three deliberate config findings:

    Key          | .env.example | Documented | Source code | Expected status
    -------------|-------------|------------|-------------|----------------
    DATABASE_URL | yes          | yes        | yes         | pass
    API_KEY      | yes          | yes        | yes         | pass
    PORT         | yes          | no         | yes         | warning
    SECRET_KEY   | no           | no         | yes         | fail
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
            f"Expected at least 4 contracts, got {len(contracts)}: "
            + str([c.claim for c in contracts])
        )

    def test_all_contracts_are_config_env_area(self, contracts):
        for c in contracts:
            assert c.area == "config_env", f"Contract {c.id} has area={c.area!r}"

    def test_all_contracts_have_non_empty_id(self, contracts):
        for c in contracts:
            assert c.id and c.id.startswith("CFG-")

    def test_ids_are_unique(self, contracts):
        ids = [c.id for c in contracts]
        assert len(ids) == len(set(ids)), "Duplicate contract IDs detected"

    def test_all_contracts_start_as_pending(self, contracts):
        for c in contracts:
            assert c.approvalStatus == "pending"

    def test_all_contracts_not_approved(self, contracts):
        for c in contracts:
            assert c.approved is False

    def test_all_contracts_not_reverified(self, contracts):
        for c in contracts:
            assert c.reverified is False

    def test_all_contracts_have_required_fields(self, contracts):
        for c in contracts:
            assert c.id
            assert c.area == "config_env"
            assert c.source
            assert c.claim
            assert c.expected
            assert c.actual
            assert c.status in ("pass", "fail", "warning")
            assert c.evidence

    # --- DATABASE_URL → pass ---

    def test_database_url_is_pass(self, contracts):
        c = next((x for x in contracts if "DATABASE_URL" in x.claim), None)
        assert c is not None, "DATABASE_URL contract not found"
        assert c.status == "pass"

    def test_database_url_has_no_severity(self, contracts):
        c = next(x for x in contracts if "DATABASE_URL" in x.claim)
        assert c.severity is None

    def test_database_url_has_no_suggested_fix(self, contracts):
        c = next(x for x in contracts if "DATABASE_URL" in x.claim)
        assert c.suggested_fix == ""

    def test_database_url_evidence_file_is_env_example(self, contracts):
        c = next(x for x in contracts if "DATABASE_URL" in x.claim)
        assert c.evidenceFile == ".env.example"

    # --- API_KEY → pass ---

    def test_api_key_is_pass(self, contracts):
        c = next((x for x in contracts if "API_KEY" in x.claim), None)
        assert c is not None, "API_KEY contract not found"
        assert c.status == "pass"

    def test_api_key_source_contains_line_number(self, contracts):
        c = next(x for x in contracts if "API_KEY" in x.claim)
        assert "#L" in c.source, f"source missing #L: {c.source!r}"

    # --- PORT → warning (seeded: in .env.example but not documented) ---

    def test_port_is_warning(self, contracts):
        c = next((x for x in contracts if "PORT" in x.claim), None)
        assert c is not None, "PORT contract not found"
        assert c.status == "warning", (
            f"Expected PORT to produce warning; got status={c.status!r}"
        )

    def test_port_severity_is_low(self, contracts):
        c = next(x for x in contracts if "PORT" in x.claim)
        assert c.severity == "low"

    def test_port_has_suggested_fix(self, contracts):
        c = next(x for x in contracts if "PORT" in x.claim)
        assert c.suggested_fix and len(c.suggested_fix) > 0

    def test_port_evidence_file_is_env_example(self, contracts):
        c = next(x for x in contracts if "PORT" in x.claim)
        assert c.evidenceFile == ".env.example"

    # --- SECRET_KEY → fail (seeded: used in code, not in .env.example) ---

    def test_secret_key_is_fail(self, contracts):
        c = next((x for x in contracts if "SECRET_KEY" in x.claim), None)
        assert c is not None, "SECRET_KEY contract not found"
        assert c.status == "fail", (
            f"Expected SECRET_KEY to fail; got status={c.status!r}, "
            f"evidence={c.evidence!r}"
        )

    def test_secret_key_severity_is_high(self, contracts):
        c = next(x for x in contracts if "SECRET_KEY" in x.claim)
        assert c.severity == "high"

    def test_secret_key_has_suggested_fix(self, contracts):
        c = next(x for x in contracts if "SECRET_KEY" in x.claim)
        assert c.suggested_fix and len(c.suggested_fix) > 0

    def test_secret_key_evidence_mentions_env_example(self, contracts):
        c = next(x for x in contracts if "SECRET_KEY" in x.claim)
        assert ".env.example" in c.evidence

    def test_secret_key_evidence_file_is_source_file(self, contracts):
        c = next(x for x in contracts if "SECRET_KEY" in x.claim)
        # evidence_file should point at the source file, not .env.example
        assert c.evidenceFile and ".env.example" not in c.evidenceFile

    def test_secret_key_source_contains_line_number(self, contracts):
        c = next(x for x in contracts if "SECRET_KEY" in x.claim)
        assert "#L" in c.source

    # --- fail contracts have high severity ---

    def test_all_fail_contracts_have_severity_high(self, contracts):
        for c in contracts:
            if c.status == "fail":
                assert c.severity == "high", (
                    f"Contract {c.id} (claim={c.claim!r}) has status=fail "
                    f"but severity={c.severity!r}"
                )

    # --- pass contracts have no severity ---

    def test_all_pass_contracts_have_no_severity(self, contracts):
        for c in contracts:
            if c.status == "pass":
                assert c.severity is None, (
                    f"Contract {c.id} (claim={c.claim!r}) has status=pass "
                    f"but severity={c.severity!r}"
                )


# ---------------------------------------------------------------------------
# Isolated scenario tests — use tmp_path for deterministic control
# ---------------------------------------------------------------------------

class TestNoEnvExample:
    def test_empty_repo_returns_empty_list(self, tmp_path):
        assert run(tmp_path) == []

    def test_repo_with_only_readme_returns_empty(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
        assert run(tmp_path) == []


class TestDeclaredAndDocumented:
    """Key in .env.example and documented → pass."""

    def test_documented_key_is_pass(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "DATABASE_URL=postgresql://localhost/db\n", encoding="utf-8"
        )
        (tmp_path / "README.md").write_text(
            "- `DATABASE_URL` — connection string\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "pass"

    def test_pass_contract_fields(self, tmp_path):
        (tmp_path / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "Set `API_KEY` before running.\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        c = contracts[0]
        assert c.id.startswith("CFG-")
        assert c.area == "config_env"
        assert c.status == "pass"
        assert c.severity is None
        assert c.suggested_fix == ""
        assert "#L" in c.source
        assert c.evidenceFile == ".env.example"


class TestDeclaredNotDocumented:
    """Key in .env.example but NOT documented → warning."""

    def test_undocumented_key_is_warning(self, tmp_path):
        (tmp_path / ".env.example").write_text("PORT=3000\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
        contracts = run(tmp_path)
        assert len(contracts) == 1
        assert contracts[0].status == "warning"

    def test_warning_severity_is_low(self, tmp_path):
        (tmp_path / ".env.example").write_text("PORT=3000\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
        contracts = run(tmp_path)
        assert contracts[0].severity == "low"

    def test_warning_has_suggested_fix(self, tmp_path):
        (tmp_path / ".env.example").write_text("PORT=3000\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
        contracts = run(tmp_path)
        assert contracts[0].suggested_fix and len(contracts[0].suggested_fix) > 0

    def test_warning_evidence_file_is_env_example(self, tmp_path):
        (tmp_path / ".env.example").write_text("PORT=3000\n", encoding="utf-8")
        contracts = run(tmp_path)
        assert contracts[0].evidenceFile == ".env.example"


class TestUsedInCodeNotDeclared:
    """Key used in source but NOT in .env.example → fail."""

    def test_undeclared_key_is_fail(self, tmp_path):
        (tmp_path / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
        src = tmp_path / "app.py"
        src.write_text(
            'import os\nsecret = os.getenv("SECRET_KEY", "")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        fail_contracts = [c for c in contracts if c.status == "fail"]
        assert len(fail_contracts) == 1
        assert "SECRET_KEY" in fail_contracts[0].claim

    def test_fail_severity_is_high(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text(
            'import os\nsecret = os.getenv("SECRET_KEY", "")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        fail_contracts = [c for c in contracts if c.status == "fail"]
        assert fail_contracts[0].severity == "high"

    def test_fail_has_suggested_fix(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text(
            'import os\nsecret = os.getenv("SECRET_KEY", "")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        fail_contracts = [c for c in contracts if c.status == "fail"]
        assert fail_contracts[0].suggested_fix

    def test_fail_evidence_file_is_source(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text(
            'import os\nsecret = os.getenv("SECRET_KEY", "")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        fail_contracts = [c for c in contracts if c.status == "fail"]
        assert fail_contracts[0].evidenceFile and "app.py" in fail_contracts[0].evidenceFile

    def test_fail_source_has_line_number(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text(
            'import os\nsecret = os.getenv("SECRET_KEY", "")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        fail_contracts = [c for c in contracts if c.status == "fail"]
        assert "#L" in fail_contracts[0].source


class TestDeduplication:
    """Each key should appear at most once in the contract list."""

    def test_same_key_in_multiple_source_files_deduplicated(self, tmp_path):
        (tmp_path / ".env.example").write_text("", encoding="utf-8")
        (tmp_path / "a.py").write_text(
            'import os\ndb = os.getenv("SECRET_KEY")\n', encoding="utf-8"
        )
        (tmp_path / "b.py").write_text(
            'import os\ndb = os.getenv("SECRET_KEY")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        secret_contracts = [c for c in contracts if "SECRET_KEY" in c.claim]
        assert len(secret_contracts) == 1

    def test_same_key_documented_multiple_places_deduplicated(self, tmp_path):
        (tmp_path / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "Set `API_KEY` first.\nAlso set `API_KEY` again.\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        api_key_contracts = [c for c in contracts if "API_KEY" in c.claim]
        assert len(api_key_contracts) == 1


class TestContractSchema:
    """Every contract must satisfy the DocumentationContract schema."""

    def test_contract_has_all_required_fields(self, tmp_path):
        (tmp_path / ".env.example").write_text("DATABASE_URL=db\n", encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "- `DATABASE_URL` — connection string\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        assert len(contracts) == 1
        c = contracts[0]
        assert c.id
        assert c.area == "config_env"
        assert c.source
        assert c.claim
        assert c.expected
        assert c.actual
        assert c.status in ("pass", "fail", "warning")
        assert c.evidence
        assert c.approvalStatus == "pending"
        assert c.approved is False
        assert c.reverified is False

    def test_all_fail_contracts_have_evidence_snippet(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text(
            'import os\nkey = os.getenv("MISSING_KEY")\n', encoding="utf-8"
        )
        contracts = run(tmp_path)
        for c in contracts:
            if c.status == "fail":
                assert c.evidenceSnippet, f"Contract {c.id} missing evidenceSnippet"


class TestJavaScriptSource:
    """Env vars referenced in JS/TS source files should be detected."""

    def test_process_env_in_js(self, tmp_path):
        (tmp_path / "index.js").write_text(
            "const db = process.env.DATABASE_URL;\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        fail_contracts = [c for c in contracts if "DATABASE_URL" in c.claim]
        assert len(fail_contracts) == 1
        assert fail_contracts[0].status == "fail"

    def test_declared_js_key_is_pass(self, tmp_path):
        (tmp_path / ".env.example").write_text("DATABASE_URL=\n", encoding="utf-8")
        (tmp_path / "README.md").write_text(
            "Set `DATABASE_URL` first.\n", encoding="utf-8"
        )
        (tmp_path / "index.js").write_text(
            "const db = process.env.DATABASE_URL;\n", encoding="utf-8"
        )
        contracts = run(tmp_path)
        db_contracts = [c for c in contracts if "DATABASE_URL" in c.claim]
        assert len(db_contracts) == 1
        assert db_contracts[0].status == "pass"
