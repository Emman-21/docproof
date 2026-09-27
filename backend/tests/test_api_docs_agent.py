"""Tests for the API Documentation Verification Subagent.

All tests run against two fixtures:
  - sample_repo/   — the real repo with deliberate API doc mismatches (lives at
                     project root; discovered via __file__ path arithmetic)
  - tmp_repo       — a per-test scratch directory built with pytest's
                     tmp_path fixture for precise, isolated scenarios

No mock data or stubs are used.  Every assertion exercises live file I/O
through api_docs_agent.run().

Deliberate mismatches seeded in sample_repo/:
  - docs/api.md documents DELETE /contracts/{id}, which is NOT implemented
    in the backend                                      → fail
  - docs/api.md documents GET /status, which is NOT implemented
    in the backend (only GET /health exists)            → fail
  - GET /health is implemented in main.py but NOT in docs/api.md → warning
  - GET /contracts, GET /contracts/{id}, POST /verify,
    POST /approve/{id}, POST /reject/{id}, GET /trust-score are both
    documented and implemented                          → pass
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.orchestration.subagents.api_docs_agent import (
    _endpoint_key,
    _extract_doc_endpoints,
    _extract_impl_endpoints,
    _normalise_path,
    _scan_docs,
    _scan_impl,
    run,
)

# ---------------------------------------------------------------------------
# Locate the canonical sample_repo relative to this test file.
# Layout:  docproof/
#             backend/tests/test_api_docs_agent.py   ← __file__
#             sample_repo/                            ← target
#             backend/                                ← backend source
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REPO = _REPO_ROOT / "sample_repo"
BACKEND_DIR = _REPO_ROOT / "backend"


# ---------------------------------------------------------------------------
# Unit tests — _normalise_path
# ---------------------------------------------------------------------------

class TestNormalisePath:
    def test_no_params_unchanged(self):
        assert _normalise_path("/contracts") == "/contracts"

    def test_single_param_replaced(self):
        assert _normalise_path("/contracts/{contract_id}") == "/contracts/{*}"

    def test_multiple_params_replaced(self):
        assert _normalise_path("/a/{x}/b/{y}") == "/a/{*}/b/{*}"

    def test_root_unchanged(self):
        assert _normalise_path("/") == "/"


class TestEndpointKey:
    def test_method_uppercased(self):
        key = _endpoint_key("get", "/contracts")
        assert key[0] == "GET"

    def test_path_params_normalised(self):
        k1 = _endpoint_key("GET", "/contracts/{contract_id}")
        k2 = _endpoint_key("GET", "/contracts/{id}")
        assert k1 == k2

    def test_trailing_slash_stripped(self):
        k1 = _endpoint_key("GET", "/contracts/")
        k2 = _endpoint_key("GET", "/contracts")
        assert k1 == k2

    def test_different_methods_are_different_keys(self):
        assert _endpoint_key("GET", "/x") != _endpoint_key("POST", "/x")


# ---------------------------------------------------------------------------
# Unit tests — _extract_doc_endpoints
# ---------------------------------------------------------------------------

class TestExtractDocEndpoints:
    def test_bare_line_in_fenced_block(self):
        text = "```\nGET /contracts\n```\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert any(e.method == "GET" and e.path == "/contracts" for e in eps)

    def test_backtick_inline(self):
        text = "Call `GET /contracts` to list all contracts.\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert any(e.method == "GET" and e.path == "/contracts" for e in eps)

    def test_bold_inline(self):
        text = "Use **POST /verify** to start verification.\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert any(e.method == "POST" and e.path == "/verify" for e in eps)

    def test_path_with_param(self):
        text = "GET /contracts/{id}\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert any("{id}" in e.path for e in eps)

    def test_line_number_recorded(self):
        text = "line1\nGET /contracts\nline3\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert eps[0].line_no == 2

    def test_source_file_recorded(self):
        text = "GET /health\n"
        eps = _extract_doc_endpoints(text, "docs/api.md")
        assert eps[0].source_file == "docs/api.md"

    def test_case_insensitive_method(self):
        text = "get /contracts\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert eps[0].method == "GET"

    def test_no_endpoints_returns_empty(self):
        text = "Nothing to see here.\n"
        eps = _extract_doc_endpoints(text, "api.md")
        assert eps == []


# ---------------------------------------------------------------------------
# Unit tests — _extract_impl_endpoints
# ---------------------------------------------------------------------------

class TestExtractImplEndpoints:
    def test_router_get(self):
        text = '@router.get("/contracts")\ndef get_contracts(): ...\n'
        eps = _extract_impl_endpoints(text, "api/contracts.py")
        assert any(e.method == "GET" and e.path == "/contracts" for e in eps)

    def test_router_post_with_status_code(self):
        text = '@router.post("/verify", status_code=202)\ndef verify(): ...\n'
        eps = _extract_impl_endpoints(text, "api/verify.py")
        assert any(e.method == "POST" and e.path == "/verify" for e in eps)

    def test_app_get(self):
        text = '@app.get("/health")\ndef health(): ...\n'
        eps = _extract_impl_endpoints(text, "main.py")
        assert any(e.method == "GET" and e.path == "/health" for e in eps)

    def test_path_with_param(self):
        text = '@router.get("/contracts/{contract_id}")\ndef get_contract(contract_id: str): ...\n'
        eps = _extract_impl_endpoints(text, "api/contracts.py")
        assert any("{contract_id}" in e.path for e in eps)

    def test_decorator_snippet_captured(self):
        text = '@router.get("/contracts", response_model=List[Contract])\n'
        eps = _extract_impl_endpoints(text, "api/contracts.py")
        assert eps[0].decorator_snippet == text.strip()

    def test_no_routes_returns_empty(self):
        text = "def helper(): pass\n"
        eps = _extract_impl_endpoints(text, "utils.py")
        assert eps == []


# ---------------------------------------------------------------------------
# Unit tests — _scan_docs / _scan_impl deduplication
# ---------------------------------------------------------------------------

class TestScanDeduplication:
    def test_scan_docs_deduplicates_same_endpoint(self, tmp_path):
        (tmp_path / "a.md").write_text(
            "GET /contracts\nGET /contracts\n", encoding="utf-8"
        )
        eps = _scan_docs(tmp_path)
        keys = [_endpoint_key(e.method, e.path) for e in eps]
        assert keys.count(("GET", "/contracts")) == 1

    def test_scan_docs_deduplicates_across_files(self, tmp_path):
        (tmp_path / "a.md").write_text("GET /contracts\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("GET /contracts\n", encoding="utf-8")
        eps = _scan_docs(tmp_path)
        keys = [_endpoint_key(e.method, e.path) for e in eps]
        assert keys.count(("GET", "/contracts")) == 1

    def test_scan_impl_deduplicates_same_route(self, tmp_path):
        (tmp_path / "a.py").write_text(
            '@router.get("/x")\n@router.get("/x")\n', encoding="utf-8"
        )
        eps = _scan_impl(tmp_path)
        keys = [_endpoint_key(e.method, e.path) for e in eps]
        assert keys.count(("GET", "/x")) == 1


# ---------------------------------------------------------------------------
# Integration tests — real sample_repo with deliberate mismatches
# ---------------------------------------------------------------------------

class TestSampleRepoMismatches:
    """The sample_repo/docs/api.md is seeded with two deliberate mismatches:

    docs/api.md declares     | Backend implements       | Expected status
    -------------------------|--------------------------|----------------
    DELETE /contracts/{id}   | (not implemented)        | fail
    GET /status              | (not implemented)        | fail
    GET /health              | main.py @app.get         | warning (impl only)
    GET /contracts           | contracts.py             | pass
    GET /contracts/{id}      | contracts.py             | pass
    POST /verify             | verify.py                | pass
    POST /approve/{id}       | approve.py               | pass
    POST /reject/{id}        | approve.py               | pass
    GET /trust-score         | trust_score.py           | pass
    """

    @pytest.fixture(scope="class")
    @classmethod
    def contracts(cls):
        return run(SAMPLE_REPO, BACKEND_DIR)

    def test_sample_repo_exists(self):
        assert SAMPLE_REPO.is_dir(), f"sample_repo not found at {SAMPLE_REPO}"

    def test_backend_dir_exists(self):
        assert BACKEND_DIR.is_dir(), f"backend not found at {BACKEND_DIR}"

    def test_returns_list(self, contracts):
        assert isinstance(contracts, list)

    def test_returns_contracts(self, contracts):
        assert len(contracts) > 0

    def test_all_contracts_are_api_docs_area(self, contracts):
        for c in contracts:
            assert c.area == "api_docs"

    def test_all_contracts_have_non_empty_id(self, contracts):
        for c in contracts:
            assert c.id and c.id.startswith("API-")

    def test_all_contracts_have_source_with_file(self, contracts):
        for c in contracts:
            assert c.source

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

    # --- Fail cases ---

    def test_delete_contracts_is_fail(self, contracts):
        c = next(
            (x for x in contracts
             if x.status == "fail"
             and "DELETE" in x.claim
             and "/contracts" in x.claim),
            None,
        )
        assert c is not None, "No fail contract for DELETE /contracts/{id}"

    def test_get_status_is_fail(self, contracts):
        c = next(
            (x for x in contracts
             if x.status == "fail"
             and "GET" in x.claim
             and "/status" in x.claim),
            None,
        )
        assert c is not None, "No fail contract for GET /status"

    def test_fail_contracts_have_severity_high(self, contracts):
        for c in contracts:
            if c.status == "fail":
                assert c.severity == "high", (
                    f"Contract {c.id} has status=fail but severity={c.severity}"
                )

    def test_fail_contracts_have_suggested_fix(self, contracts):
        for c in contracts:
            if c.status == "fail":
                assert c.suggested_fix and len(c.suggested_fix) > 0

    # --- Pass cases ---

    def test_get_contracts_is_pass(self, contracts):
        c = next(
            (x for x in contracts
             if x.status == "pass"
             and "GET" in x.claim
             and x.claim.strip().endswith("/contracts is documented")),
            None,
        )
        assert c is not None, "No pass contract for GET /contracts"

    def test_post_verify_is_pass(self, contracts):
        c = next(
            (x for x in contracts
             if x.status == "pass" and "POST" in x.claim and "/verify" in x.claim),
            None,
        )
        assert c is not None, "No pass contract for POST /verify"

    def test_get_trust_score_is_pass(self, contracts):
        c = next(
            (x for x in contracts
             if x.status == "pass"
             and "GET" in x.claim
             and "/trust-score" in x.claim),
            None,
        )
        assert c is not None, "No pass contract for GET /trust-score"

    def test_pass_contracts_have_no_severity(self, contracts):
        for c in contracts:
            if c.status == "pass":
                assert c.severity is None, (
                    f"Contract {c.id} has status=pass but severity={c.severity}"
                )

    def test_pass_contracts_have_no_suggested_fix(self, contracts):
        for c in contracts:
            if c.status == "pass":
                assert c.suggested_fix == ""

    # --- Warning cases ---

    def test_health_endpoint_is_warning(self, contracts):
        c = next(
            (x for x in contracts
             if x.status == "warning" and "/health" in x.claim),
            None,
        )
        assert c is not None, "No warning contract for GET /health"

    def test_warning_contracts_have_severity_low(self, contracts):
        for c in contracts:
            if c.status == "warning":
                assert c.severity == "low", (
                    f"Contract {c.id} has status=warning but severity={c.severity}"
                )

    def test_warning_contracts_have_suggested_fix(self, contracts):
        for c in contracts:
            if c.status == "warning":
                assert c.suggested_fix and len(c.suggested_fix) > 0


# ---------------------------------------------------------------------------
# Isolated scenario tests — tmp_path for deterministic control
# ---------------------------------------------------------------------------

class TestEmptyRepo:
    def test_no_docs_no_backend_returns_empty(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        assert result == []


class TestPassScenario:
    """Documented endpoint with matching implementation → pass."""

    def test_matching_endpoint_produces_pass(self, tmp_path):
        (tmp_path / "api.md").write_text(
            "GET /items\n", encoding="utf-8"
        )
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "routes.py").write_text(
            '@router.get("/items")\ndef list_items(): ...\n', encoding="utf-8"
        )
        contracts = run(tmp_path, backend)
        assert len(contracts) == 1
        assert contracts[0].status == "pass"

    def test_pass_contract_evidence_mentions_both_files(self, tmp_path):
        (tmp_path / "api.md").write_text("POST /orders\n", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "orders.py").write_text(
            '@router.post("/orders")\ndef create_order(): ...\n', encoding="utf-8"
        )
        contracts = run(tmp_path, backend)
        assert "api.md" in contracts[0].evidence
        assert "orders.py" in contracts[0].evidence

    def test_path_param_variant_matches(self, tmp_path):
        """Doc uses {id} but impl uses {item_id} — should still match."""
        (tmp_path / "api.md").write_text(
            "GET /items/{id}\n", encoding="utf-8"
        )
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "routes.py").write_text(
            '@router.get("/items/{item_id}")\ndef get_item(item_id: str): ...\n',
            encoding="utf-8",
        )
        contracts = run(tmp_path, backend)
        assert contracts[0].status == "pass"


class TestFailScenario:
    """Documented endpoint missing from backend → fail."""

    def test_missing_impl_produces_fail(self, tmp_path):
        (tmp_path / "api.md").write_text(
            "DELETE /items/{id}\n", encoding="utf-8"
        )
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        assert len(result) == 1
        assert result[0].status == "fail"

    def test_fail_contract_severity_is_high(self, tmp_path):
        (tmp_path / "api.md").write_text("GET /missing\n", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        assert result[0].severity == "high"

    def test_fail_contract_suggested_fix_is_non_empty(self, tmp_path):
        (tmp_path / "api.md").write_text("POST /ghost\n", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        assert result[0].suggested_fix and len(result[0].suggested_fix) > 0

    def test_fail_contract_evidence_mentions_404(self, tmp_path):
        (tmp_path / "api.md").write_text("GET /missing\n", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        assert "404" in result[0].evidence or "not found" in result[0].evidence.lower()


class TestWarningScenario:
    """Implemented endpoint not in docs → warning."""

    def test_undocumented_impl_produces_warning(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "routes.py").write_text(
            '@router.get("/internal")\ndef internal(): ...\n', encoding="utf-8"
        )
        result = run(tmp_path, backend)
        assert len(result) == 1
        assert result[0].status == "warning"

    def test_warning_contract_severity_is_low(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "routes.py").write_text(
            '@router.get("/internal")\ndef internal(): ...\n', encoding="utf-8"
        )
        result = run(tmp_path, backend)
        assert result[0].severity == "low"

    def test_warning_contract_suggested_fix_non_empty(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "routes.py").write_text(
            '@app.get("/health")\ndef health(): ...\n', encoding="utf-8"
        )
        result = run(tmp_path, backend)
        assert result[0].suggested_fix and len(result[0].suggested_fix) > 0


class TestDeduplication:
    """Same endpoint declared multiple times in docs → single contract."""

    def test_duplicate_doc_endpoint_deduplicated(self, tmp_path):
        (tmp_path / "a.md").write_text(
            "GET /items\nGET /items\n", encoding="utf-8"
        )
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        paths = [c.claim for c in result if "/items" in c.claim]
        assert len(paths) == 1

    def test_same_endpoint_across_two_doc_files_deduplicated(self, tmp_path):
        (tmp_path / "a.md").write_text("GET /items\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("GET /items\n", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        result = run(tmp_path, backend)
        paths = [c.claim for c in result if "/items" in c.claim]
        assert len(paths) == 1


class TestContractSchema:
    """Every returned contract must satisfy the DocumentationContract schema."""

    def test_contract_has_required_fields(self, tmp_path):
        (tmp_path / "api.md").write_text("GET /x\n", encoding="utf-8")
        backend = tmp_path / "backend"
        backend.mkdir()
        contracts = run(tmp_path, backend)
        c = contracts[0]
        assert c.id
        assert c.area == "api_docs"
        assert c.source
        assert c.claim
        assert c.expected
        assert c.actual
        assert c.status in ("pass", "fail", "warning")
        assert c.evidence
        assert c.approvalStatus == "pending"
        assert c.approved is False
        assert c.reverified is False
