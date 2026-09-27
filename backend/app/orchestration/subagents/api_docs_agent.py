"""API Documentation Verification Subagent.

Scans a repository's documentation files for HTTP API endpoint declarations
(method + path), then cross-references them against the FastAPI route
implementations found under a ``backend/`` directory tree.

Three classes of finding are emitted as DocumentationContracts:

``pass``
    The endpoint is declared in documentation **and** implemented in the
    FastAPI codebase.  Method and path both match.

``fail``
    The endpoint is declared in documentation but is **not** found in any
    FastAPI router file.  The frontend/consumer will receive a 404 or 405 at
    runtime.

``warning``
    The endpoint is implemented in the FastAPI codebase but is **not**
    mentioned in any documentation file.  Consumers have no documented
    contract for this endpoint.

Public API
----------
run(repo_path: str | Path, backend_path: str | Path | None = None)
    -> list[DocumentationContract]

Parameters
----------
repo_path:
    Root of the repository that contains the documentation.
backend_path:
    Directory that contains the FastAPI application.  Defaults to
    ``<repo_path>/../backend`` when not supplied, which matches the
    layout used by this project.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from app.core.models import DocumentationContract

# ---------------------------------------------------------------------------
# Regex patterns for extracting API endpoints from documentation
# ---------------------------------------------------------------------------

# Matches fenced block body line: "GET /path/to/resource" or "POST /api/v1/items"
# Also matches inline: `GET /path` or **GET /path**
_DOC_ENDPOINT_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s`*\"'\)>|]*)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regex patterns for extracting routes from FastAPI source code
# ---------------------------------------------------------------------------

# Matches: @router.get("/path", ...) or @app.post("/path/{id}", ...)
_FASTAPI_ROUTE_RE = re.compile(
    r"""@(?:router|app)\.(get|post|put|patch|delete|head|options)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------

_DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst"})

_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build"}
)


def _find_doc_files(repo: Path) -> list[Path]:
    """Return all Markdown and RST files in the repo tree."""
    candidates: list[Path] = []
    for path in repo.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in _DOC_EXTENSIONS:
            candidates.append(path)
    # Stable ordering: sort by relative path string
    candidates.sort(key=lambda p: str(p.relative_to(repo)))
    return candidates


def _find_python_files(backend: Path) -> list[Path]:
    """Return all .py files under *backend*, skipping noise dirs."""
    result: list[Path] = []
    for path in backend.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            result.append(path)
    result.sort(key=lambda p: str(p.relative_to(backend)))
    return result


# ---------------------------------------------------------------------------
# Data classes (use __slots__ for memory efficiency, same pattern as peers)
# ---------------------------------------------------------------------------

class _DocEndpoint:
    __slots__ = ("method", "path", "source_file", "line_no", "raw_line")

    def __init__(
        self,
        method: str,
        path: str,
        source_file: str,
        line_no: int,
        raw_line: str,
    ) -> None:
        self.method = method.upper()
        self.path = path.rstrip("/") or "/"
        self.source_file = source_file
        self.line_no = line_no
        self.raw_line = raw_line.strip()


class _ImplEndpoint:
    __slots__ = ("method", "path", "source_file", "line_no", "decorator_snippet")

    def __init__(
        self,
        method: str,
        path: str,
        source_file: str,
        line_no: int,
        decorator_snippet: str,
    ) -> None:
        self.method = method.upper()
        self.path = path.rstrip("/") or "/"
        self.source_file = source_file
        self.line_no = line_no
        self.decorator_snippet = decorator_snippet.strip()


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_doc_endpoints(text: str, source_file: str) -> list[_DocEndpoint]:
    """Return all (method, path) pairs declared in a documentation file."""
    endpoints: list[_DocEndpoint] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in _DOC_ENDPOINT_RE.finditer(line):
            method = m.group(1).upper()
            path = m.group(2).rstrip("/") or "/"
            endpoints.append(
                _DocEndpoint(
                    method=method,
                    path=path,
                    source_file=source_file,
                    line_no=line_no,
                    raw_line=line,
                )
            )
    return endpoints


def _extract_impl_endpoints(text: str, source_file: str) -> list[_ImplEndpoint]:
    """Return all FastAPI routes declared in a Python source file."""
    endpoints: list[_ImplEndpoint] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in _FASTAPI_ROUTE_RE.finditer(line):
            method = m.group(1).upper()
            path = m.group(2).rstrip("/") or "/"
            endpoints.append(
                _ImplEndpoint(
                    method=method,
                    path=path,
                    source_file=source_file,
                    line_no=line_no,
                    decorator_snippet=line.strip(),
                )
            )
    return endpoints


# ---------------------------------------------------------------------------
# Normalisation: turn FastAPI path params {var} into a canonical token
# so that /contracts/{contract_id} == /contracts/{id}
# ---------------------------------------------------------------------------

_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


def _normalise_path(path: str) -> str:
    """Replace all ``{param}`` segments with ``{*}`` for comparison."""
    return _PATH_PARAM_RE.sub("{*}", path)


def _endpoint_key(method: str, path: str) -> tuple[str, str]:
    """Canonical (METHOD, /normalised/path) key for deduplication."""
    return (method.upper(), _normalise_path(path.rstrip("/") or "/"))


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

def _scan_docs(repo: Path) -> list[_DocEndpoint]:
    """Return deduplicated API endpoints declared in documentation."""
    seen: set[tuple[str, str]] = set()
    result: list[_DocEndpoint] = []
    for doc_file in _find_doc_files(repo):
        try:
            text = doc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(doc_file.relative_to(repo))
        for ep in _extract_doc_endpoints(text, rel):
            key = _endpoint_key(ep.method, ep.path)
            if key not in seen:
                seen.add(key)
                result.append(ep)
    return result


def _scan_impl(backend: Path) -> list[_ImplEndpoint]:
    """Return deduplicated FastAPI routes found in the backend source tree."""
    seen: set[tuple[str, str]] = set()
    result: list[_ImplEndpoint] = []
    for py_file in _find_python_files(backend):
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(py_file.relative_to(backend))
        for ep in _extract_impl_endpoints(text, rel):
            key = _endpoint_key(ep.method, ep.path)
            if key not in seen:
                seen.add(key)
                result.append(ep)
    return result


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------

def _build_contract(
    *,
    contract_id: str,
    status: str,
    source: str,
    claim: str,
    expected: str,
    actual: str,
    evidence: str,
    evidence_file: str,
    evidence_lines: str,
    evidence_snippet: str,
    suggested_fix: str,
    severity: Optional[str],
) -> DocumentationContract:
    return DocumentationContract(
        id=contract_id,
        area="api_docs",
        source=source,
        claim=claim,
        expected=expected,
        actual=actual,
        status=status,  # type: ignore[arg-type]
        evidence=evidence,
        evidenceFile=evidence_file,
        evidenceLines=evidence_lines,
        evidenceSnippet=evidence_snippet,
        suggested_fix=suggested_fix,
        approvalStatus="pending",
        approved=False,
        reverified=False,
        severity=severity,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    repo_path: str | Path,
    backend_path: str | Path | None = None,
) -> list[DocumentationContract]:
    """Scan *repo_path* docs and cross-reference against *backend_path* routes.

    Parameters
    ----------
    repo_path:
        Root of the repository containing documentation files.
    backend_path:
        Directory that holds the FastAPI application source.  When ``None``,
        defaults to ``<repo_path>/../backend``.

    Returns
    -------
    list[DocumentationContract]
        One entry per unique (METHOD, /path) pair that appears in either
        documentation or implementation.  Ordered: docs-only endpoints first
        (sorted by source file + line), then impl-only endpoints.
    """
    repo = Path(repo_path).resolve()
    if backend_path is None:
        backend = (repo.parent / "backend").resolve()
    else:
        backend = Path(backend_path).resolve()

    doc_endpoints = _scan_docs(repo)
    impl_endpoints = _scan_impl(backend)

    # Build a lookup: normalised key → ImplEndpoint
    impl_by_key: dict[tuple[str, str], _ImplEndpoint] = {
        _endpoint_key(ep.method, ep.path): ep
        for ep in impl_endpoints
    }

    # Track which impl keys were matched by a doc entry
    matched_impl_keys: set[tuple[str, str]] = set()

    contracts: list[DocumentationContract] = []

    # --- Pass 1: every endpoint found in docs --------------------------------
    for doc_ep in doc_endpoints:
        key = _endpoint_key(doc_ep.method, doc_ep.path)
        impl_ep = impl_by_key.get(key)
        _hash = hashlib.sha256(
            f"api_docs|{doc_ep.source_file}#L{doc_ep.line_no}|{doc_ep.method} {doc_ep.path} is documented".encode()
        ).hexdigest()[:8].upper()
        contract_id = f"API-{_hash}"

        if impl_ep is not None:
            # pass — documented AND implemented
            matched_impl_keys.add(key)
            contracts.append(
                _build_contract(
                    contract_id=contract_id,
                    status="pass",
                    source=f"{doc_ep.source_file}#L{doc_ep.line_no}",
                    claim=f"{doc_ep.method} {doc_ep.path} is documented",
                    expected=f"{doc_ep.method} {doc_ep.path} implemented in backend",
                    actual=f"{doc_ep.method} {doc_ep.path} found in {impl_ep.source_file}",
                    evidence=(
                        f"{doc_ep.method} {doc_ep.path} is declared in "
                        f"{doc_ep.source_file} (line {doc_ep.line_no}) and "
                        f"implemented in {impl_ep.source_file} (line {impl_ep.line_no})."
                    ),
                    evidence_file=impl_ep.source_file,
                    evidence_lines=f"Line {impl_ep.line_no}",
                    evidence_snippet=impl_ep.decorator_snippet,
                    suggested_fix="",
                    severity=None,
                )
            )
        else:
            # fail — documented but NOT implemented
            contracts.append(
                _build_contract(
                    contract_id=contract_id,
                    status="fail",
                    source=f"{doc_ep.source_file}#L{doc_ep.line_no}",
                    claim=f"{doc_ep.method} {doc_ep.path} is documented",
                    expected=f"{doc_ep.method} {doc_ep.path} implemented in backend",
                    actual=f"{doc_ep.method} {doc_ep.path} not found in backend routes",
                    evidence=(
                        f"{doc_ep.method} {doc_ep.path} is declared in "
                        f"{doc_ep.source_file} (line {doc_ep.line_no}) but no "
                        f"matching FastAPI route was found in the backend source tree. "
                        f"Consumers calling this endpoint will receive a 404 or 405."
                    ),
                    evidence_file=doc_ep.source_file,
                    evidence_lines=f"Line {doc_ep.line_no}",
                    evidence_snippet=doc_ep.raw_line.strip(),
                    suggested_fix=(
                        f"Either implement {doc_ep.method} {doc_ep.path} in the "
                        f"FastAPI backend or remove it from the documentation."
                    ),
                    severity="high",
                )
            )

    # --- Pass 2: impl endpoints not mentioned in docs ------------------------
    for impl_ep in impl_endpoints:
        key = _endpoint_key(impl_ep.method, impl_ep.path)
        if key in matched_impl_keys:
            continue
        # Check whether any doc endpoint (before normalisation) already covers it
        if key in {_endpoint_key(d.method, d.path) for d in doc_endpoints}:
            continue

        _hash = hashlib.sha256(
            f"api_docs|{impl_ep.source_file}#L{impl_ep.line_no}|{impl_ep.method} {impl_ep.path} is implemented but undocumented".encode()
        ).hexdigest()[:8].upper()
        contract_id = f"API-{_hash}"
        contracts.append(
            _build_contract(
                contract_id=contract_id,
                status="warning",
                source=f"{impl_ep.source_file}#L{impl_ep.line_no}",
                claim=f"{impl_ep.method} {impl_ep.path} is implemented but undocumented",
                expected=f"{impl_ep.method} {impl_ep.path} described in API documentation",
                actual=f"{impl_ep.method} {impl_ep.path} present in backend only",
                evidence=(
                    f"{impl_ep.method} {impl_ep.path} is implemented in "
                    f"{impl_ep.source_file} (line {impl_ep.line_no}) but has no "
                    f"corresponding entry in any documentation file. Consumers of "
                    f"this API have no documented contract for this endpoint."
                ),
                evidence_file=impl_ep.source_file,
                evidence_lines=f"Line {impl_ep.line_no}",
                evidence_snippet=impl_ep.decorator_snippet,
                suggested_fix=(
                    f"Add {impl_ep.method} {impl_ep.path} to the API documentation "
                    f"with its request/response contract."
                ),
                severity="low",
            )
        )

    return contracts
