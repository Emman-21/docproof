"""Config / Environment Variable Verification Subagent.

Scans a repository to detect mismatches between the environment variables
declared in ``.env.example`` and the variables referenced in documentation
and source code.

Three classes of finding are emitted as DocumentationContracts:

``pass``
    The key is declared in ``.env.example`` **and** is referenced in at
    least one documentation file.

``fail``
    The key is referenced in source code but is **not** declared in
    ``.env.example``.  This means the application will silently receive an
    empty or None value in a fresh checkout — a probable runtime error.

``warning``
    The key is declared in ``.env.example`` but is **not** referenced in
    any documentation file.  Developers cloning the repo have no written
    guidance on what the variable controls.

Public API
----------
run(repo_path: str | Path) -> list[DocumentationContract]
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from app.core.models import DocumentationContract

# ---------------------------------------------------------------------------
# Regex patterns for scanning source files
# ---------------------------------------------------------------------------

# Matches Python: os.environ["VAR"], os.environ.get("VAR"), os.getenv("VAR")
_PY_ENV_RE = re.compile(
    r"""os\.(?:environ(?:\.get)?\s*[\[(]\s*['""]|getenv\s*\(\s*['""])([A-Z][A-Z0-9_]*)""",
)

# Matches JavaScript/TypeScript: process.env.VAR_NAME
_JS_ENV_RE = re.compile(
    r"""process\.env\.([A-Z][A-Z0-9_]*)""",
)

# Matches shell scripts / .env references: $VAR_NAME or ${VAR_NAME}
_SHELL_ENV_RE = re.compile(
    r"""\$\{?([A-Z][A-Z0-9_]{2,})\}?""",
)

# Matches documentation back-tick references: `VAR_NAME`
_DOC_BACKTICK_RE = re.compile(
    r"""`([A-Z][A-Z0-9_]+)`""",
)

# Matches documentation list-item style: - VAR_NAME — or - `VAR_NAME` —
_DOC_LIST_RE = re.compile(
    r"""^[-*]\s+[`']?([A-Z][A-Z0-9_]+)[`']?\s*[—–-]""",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
)

_DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".txt"})

_SHELL_EXTENSIONS: frozenset[str] = frozenset({".sh", ".bash", ".zsh"})

# Directories to skip when scanning source
_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache"}
)

# Keys that are always false positives when scanning shell / env files
_FALSE_POSITIVE_KEYS: frozenset[str] = frozenset(
    {"PATH", "HOME", "USER", "SHELL", "PWD", "TERM", "LANG", "LC_ALL", "IFS"}
)


# ---------------------------------------------------------------------------
# .env.example parser
# ---------------------------------------------------------------------------

def _parse_env_example(repo: Path) -> dict[str, str]:
    """Return a mapping of key → default_value from ``.env.example``.

    Lines that are comments or blank are skipped.  The default_value is the
    raw string after the ``=``, which may be empty.
    """
    env_file = repo / ".env.example"
    if not env_file.is_file():
        return {}
    result: dict[str, str] = {}
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key and re.match(r"^[A-Z][A-Z0-9_]*$", key):
                result[key] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Documentation scanner
# ---------------------------------------------------------------------------

def _find_doc_files(repo: Path) -> list[Path]:
    """Return Markdown and RST files from repo root and docs/ directory."""
    candidates: list[Path] = []
    for pattern in ("*.md", "*.rst", "docs/*.md", "docs/*.rst"):
        candidates.extend(repo.glob(pattern))
    seen: set[Path] = set()
    result: list[Path] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _extract_doc_keys(text: str) -> set[str]:
    """Return all uppercase identifiers referenced as env vars in *text*."""
    keys: set[str] = set()
    for m in _DOC_BACKTICK_RE.finditer(text):
        keys.add(m.group(1))
    for m in _DOC_LIST_RE.finditer(text):
        keys.add(m.group(1))
    return keys


def _scan_docs(repo: Path) -> dict[str, tuple[str, int]]:
    """Return mapping key → (source_ref, line_no) for keys found in docs."""
    found: dict[str, tuple[str, int]] = {}
    for doc_file in _find_doc_files(repo):
        try:
            text = doc_file.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(doc_file.relative_to(repo))
        for line_no, line in enumerate(text.splitlines(), start=1):
            for m in _DOC_BACKTICK_RE.finditer(line):
                key = m.group(1)
                if key not in _FALSE_POSITIVE_KEYS and key not in found:
                    found[key] = (f"{rel}#L{line_no}", line_no)
            for m in _DOC_LIST_RE.finditer(line):
                key = m.group(1)
                if key not in _FALSE_POSITIVE_KEYS and key not in found:
                    found[key] = (f"{rel}#L{line_no}", line_no)
    return found


# ---------------------------------------------------------------------------
# Source code scanner
# ---------------------------------------------------------------------------

def _collect_source_files(repo: Path) -> list[Path]:
    """Walk the repo and return all scannable source files."""
    result: list[Path] = []
    for path in repo.rglob("*"):
        # Skip unwanted directories
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _SOURCE_EXTENSIONS or suffix in _SHELL_EXTENSIONS:
            result.append(path)
    return result


def _extract_source_keys(text: str, suffix: str) -> list[tuple[str, int]]:
    """Return list of (key, line_no) for every env var reference in *text*."""
    results: list[tuple[str, int]] = []
    patterns: list[re.Pattern[str]] = []
    if suffix in _SOURCE_EXTENSIONS:
        if suffix == ".py":
            patterns.append(_PY_ENV_RE)
        else:
            patterns.append(_JS_ENV_RE)
    if suffix in _SHELL_EXTENSIONS:
        patterns.append(_SHELL_ENV_RE)
    # For any extension, also look for JS-style process.env in .ts/.tsx/.mjs etc.
    if suffix in {".ts", ".tsx", ".mjs", ".cjs"}:
        patterns.append(_JS_ENV_RE)

    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        for pattern in patterns:
            for m in pattern.finditer(line):
                key = m.group(1)
                if key not in _FALSE_POSITIVE_KEYS:
                    results.append((key, line_no))
    return results


def _scan_source(repo: Path) -> dict[str, tuple[str, int, str]]:
    """Return mapping key → (source_ref, line_no, snippet) from source files."""
    found: dict[str, tuple[str, int, str]] = {}
    for src_file in _collect_source_files(repo):
        suffix = src_file.suffix.lower()
        try:
            text = src_file.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(src_file.relative_to(repo))
        for key, line_no in _extract_source_keys(text, suffix):
            if key not in found:
                # Capture the snippet line
                lines = text.splitlines()
                snippet = lines[line_no - 1].strip() if line_no <= len(lines) else ""
                found[key] = (f"{rel}#L{line_no}", line_no, snippet)
    return found


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------

def _build_contract(
    *,
    contract_id: str,
    key: str,
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
        area="config_env",
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

def run(repo_path: str | Path) -> list[DocumentationContract]:
    """Scan *repo_path* and return a list of DocumentationContracts.

    Parameters
    ----------
    repo_path:
        Absolute or relative path to the repository root to analyse.

    Returns
    -------
    list[DocumentationContract]
        One entry per environment variable key that is either declared in
        ``.env.example``, referenced in documentation, or used in source code.
        All contracts are deterministically ordered by key name.
    """
    repo = Path(repo_path).resolve()

    declared_keys = _parse_env_example(repo)   # key → default value
    doc_refs = _scan_docs(repo)                # key → (source_ref, line_no)
    src_refs = _scan_source(repo)              # key → (source_ref, line_no, snippet)

    env_example_rel = ".env.example"

    contracts: list[DocumentationContract] = []

    # --- Case 1: key declared in .env.example ---
    # pass  → also documented
    # warning → NOT documented
    for key in sorted(declared_keys):
        default_val = declared_keys[key]
        snippet = f"{key}={default_val}" if default_val else f"{key}="
        if key in doc_refs:
            # pass
            doc_source, _ = doc_refs[key]
            _hash = hashlib.sha256(f"config_env|{doc_source}|`{key}` is documented".encode()).hexdigest()[:8].upper()
            contracts.append(
                _build_contract(
                    contract_id=f"CFG-{_hash}",
                    key=key,
                    status="pass",
                    source=doc_source,
                    claim=f"`{key}` is documented",
                    expected=f"{key} declared in .env.example and documented",
                    actual=f"{key} found in .env.example and documentation",
                    evidence=(
                        f"{key} is declared in .env.example (value: {snippet!r}) "
                        f"and referenced in documentation at {doc_source}."
                    ),
                    evidence_file=env_example_rel,
                    evidence_lines=f"See {doc_source}",
                    evidence_snippet=snippet,
                    suggested_fix="",
                    severity=None,
                )
            )
        else:
            # warning — in .env.example but not in docs
            source_ref = env_example_rel
            _hash = hashlib.sha256(f"config_env|{env_example_rel}|`{key}` is declared in .env.example but not documented".encode()).hexdigest()[:8].upper()
            contracts.append(
                _build_contract(
                    contract_id=f"CFG-{_hash}",
                    key=key,
                    status="warning",
                    source=source_ref,
                    claim=f"`{key}` is declared in .env.example but not documented",
                    expected=f"{key} documented in README or docs",
                    actual=f"{key} present in .env.example only",
                    evidence=(
                        f"{key} is declared in .env.example but has no corresponding "
                        f"entry in any documentation file.  Developers have no guidance "
                        f"on what this variable controls or what value it should take."
                    ),
                    evidence_file=env_example_rel,
                    evidence_lines="See .env.example",
                    evidence_snippet=snippet,
                    suggested_fix=(
                        f"Add a description of `{key}` to the README (or docs/) so that "
                        f"developers know what value to supply when setting up the project."
                    ),
                    severity="low",
                )
            )

    # --- Case 2: key used in source code but NOT in .env.example ---
    # fail  → undeclared dependency
    for key in sorted(src_refs):
        if key in declared_keys:
            continue  # already handled above
        src_source, src_line_no, snippet = src_refs[key]
        _hash = hashlib.sha256(f"config_env|{src_source}|`{key}` is used in source code".encode()).hexdigest()[:8].upper()
        contracts.append(
            _build_contract(
                contract_id=f"CFG-{_hash}",
                key=key,
                status="fail",
                source=src_source,
                claim=f"`{key}` is used in source code",
                expected=f"{key} declared in .env.example",
                actual=f"{key} missing from .env.example",
                evidence=(
                    f"{key} is referenced in source code ({src_source}) but is not "
                    f"declared in .env.example.  A fresh checkout will receive an empty "
                    f"or None value for this variable, which may cause a runtime error."
                ),
                evidence_file=src_source.split("#")[0],
                evidence_lines=f"Line {src_line_no}",
                evidence_snippet=snippet,
                suggested_fix=(
                    f"Add `{key}=<your-value>` to .env.example with a placeholder or "
                    f"example value so that developers know this variable is required."
                ),
                severity="high",
            )
        )

    return contracts
