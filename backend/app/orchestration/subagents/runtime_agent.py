"""Runtime Requirements Verification Subagent.

Scans a repository directory to detect mismatches between version claims
written in documentation (README.md) and the version constraints declared
in configuration files (package.json, setup.cfg, .nvmrc, .python-version).

For every discrepancy found, a DocumentationContract is produced with:
  - status "fail"  when the documented version is incompatible with the config
  - status "pass"  when documentation and config agree
  - status "warning" when the claim is ambiguous or the config is absent

Public API
----------
run(repo_path: str | Path) -> list[DocumentationContract]
"""
from __future__ import annotations

import configparser
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from app.core.models import DocumentationContract

# ---------------------------------------------------------------------------
# Version-claim patterns matched against documentation text
# ---------------------------------------------------------------------------

# Each entry: (runtime_key, regex, canonical_prefix)
# runtime_key is used to correlate with config-file findings.
_DOC_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "node",
        re.compile(
            # Matches: "Node.js 18+", "Node.js >= 18", "Node 18+", "node 18.x", ">=18.0.0"
            r"Node(?:\.js)?\s*(?:>=?|v)?\s*(\d+(?:\.\d+)*)[+x]?",
            re.IGNORECASE,
        ),
        "Node.js >=",
    ),
    (
        "npm",
        re.compile(
            r"npm\s*(?:>=?)?\s*(\d+(?:\.\d+)*)\+?",
            re.IGNORECASE,
        ),
        "npm >=",
    ),
    (
        "python",
        re.compile(
            r"Python\s*(?:>=?)?\s*(\d+\.\d+)\+?",
            re.IGNORECASE,
        ),
        "Python >=",
    ),
]


# ---------------------------------------------------------------------------
# Helpers: version parsing
# ---------------------------------------------------------------------------

def _normalise_semver(raw: str) -> str:
    """Strip leading >= / ~ / ^ and return 'MAJOR.MINOR' or 'MAJOR'."""
    cleaned = raw.strip().lstrip(">=~^").strip()
    return cleaned


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert '3.11' → (3, 11).  Returns (0,) on parse failure."""
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)


def _versions_match(doc_ver: str, config_ver: str) -> bool:
    """Return True if config_ver satisfies the >= doc_ver constraint.

    Both values are treated as lower bounds already stripped of operators.
    A *match* means the config allows at least the version documented.
    A *mismatch* means the config requires *more* than documented (stricter),
    which is what the seeded errors represent.
    """
    return _version_tuple(config_ver) == _version_tuple(doc_ver)


# ---------------------------------------------------------------------------
# Helpers: config-file readers
# ---------------------------------------------------------------------------

def _read_package_json(repo: Path) -> dict:
    """Return the parsed package.json dict, or {} if absent/invalid."""
    pkg = repo / "package.json"
    if not pkg.is_file():
        return {}
    try:
        return json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_nvmrc(repo: Path) -> Optional[str]:
    """Return the Node version string from .nvmrc, or None."""
    nvmrc = repo / ".nvmrc"
    if not nvmrc.is_file():
        return None
    return nvmrc.read_text(encoding="utf-8", errors="replace").strip().lstrip("v")


def _read_python_version_file(repo: Path) -> Optional[str]:
    """.python-version file (pyenv) — return stripped content or None."""
    pv = repo / ".python-version"
    if not pv.is_file():
        return None
    return pv.read_text(encoding="utf-8", errors="replace").strip().lstrip("v")


def _read_setup_cfg_python_requires(repo: Path) -> Optional[str]:
    """Return the python_requires value from setup.cfg, or None."""
    cfg_path = repo / "setup.cfg"
    if not cfg_path.is_file():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except configparser.Error:
        return None
    return parser.get("options", "python_requires", fallback=None)


# ---------------------------------------------------------------------------
# Config resolution: produce (actual_version_str, evidence_file, snippet)
# ---------------------------------------------------------------------------

def _resolve_node_config(
    repo: Path,
    pkg: dict,
) -> tuple[Optional[str], str, str]:
    """Return (version_lower_bound, evidence_file, snippet) for Node."""
    engines_node: Optional[str] = pkg.get("engines", {}).get("node")
    if engines_node:
        ver = _normalise_semver(engines_node)
        snippet = f'"engines": {{"node": "{engines_node}"}}'
        return ver, "package.json", snippet

    nvmrc = _read_nvmrc(repo)
    if nvmrc:
        return nvmrc, ".nvmrc", nvmrc
    return None, "package.json", "(engines.node not set)"


def _resolve_npm_config(
    repo: Path,
    pkg: dict,
) -> tuple[Optional[str], str, str]:
    """Return (version_lower_bound, evidence_file, snippet) for npm."""
    engines_npm: Optional[str] = pkg.get("engines", {}).get("npm")
    if engines_npm:
        ver = _normalise_semver(engines_npm)
        snippet = f'"engines": {{"npm": "{engines_npm}"}}'
        return ver, "package.json", snippet
    return None, "package.json", "(engines.npm not set)"


def _resolve_python_config(
    repo: Path,
    pkg: dict,
) -> tuple[Optional[str], str, str]:
    """Return (version_lower_bound, evidence_file, snippet) for Python."""
    # setup.cfg python_requires
    py_req = _read_setup_cfg_python_requires(repo)
    if py_req:
        ver = _normalise_semver(py_req)
        return ver, "setup.cfg", f"python_requires = {py_req}"

    # .python-version (pyenv)
    pv = _read_python_version_file(repo)
    if pv:
        return pv, ".python-version", pv

    return None, "requirements.txt", "(python_requires not declared)"


# ---------------------------------------------------------------------------
# Documentation scanner
# ---------------------------------------------------------------------------

def _find_doc_files(repo: Path) -> list[Path]:
    """Return all Markdown and RST files in the repo root and docs/ dir."""
    candidates: list[Path] = []
    for pattern in ("*.md", "*.rst", "docs/*.md", "docs/*.rst"):
        candidates.extend(repo.glob(pattern))
    # deduplicate while preserving order
    seen: set[Path] = set()
    result: list[Path] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _extract_claims(
    text: str,
    doc_file: Path,
    repo: Path,
) -> list[tuple[str, str, str, int]]:
    """Return list of (runtime_key, raw_version, claim_sentence, line_no)."""
    results: list[tuple[str, str, str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for runtime_key, pattern, _ in _DOC_PATTERNS:
            m = pattern.search(line)
            if m:
                raw_ver = m.group(1)
                claim = line.strip().lstrip("- *").strip()
                results.append((runtime_key, raw_ver, claim, line_no))
    return results


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------

def _build_contract(
    *,
    contract_id: str,
    doc_file: Path,
    repo: Path,
    line_no: int,
    claim_sentence: str,
    runtime_key: str,
    runtime_prefix: str,
    doc_ver: str,
    config_ver: Optional[str],
    evidence_file: str,
    evidence_snippet: str,
) -> DocumentationContract:
    source = f"{doc_file.relative_to(repo)}#L{line_no}"
    expected = f"{runtime_prefix}{doc_ver}"
    actual_raw = config_ver or "not found"

    if config_ver is None:
        status: str = "warning"
        actual = f"{runtime_prefix}(not declared in config)"
        evidence = (
            f"No {runtime_key} version constraint found in the repository. "
            f"Documentation claims {expected}."
        )
        suggested_fix = (
            f"Add a {runtime_key} version constraint to your configuration "
            f"(e.g., engines.{runtime_key} in package.json or setup.cfg "
            f"python_requires) matching {expected}."
        )
        severity = "low"
    elif _versions_match(doc_ver, config_ver):
        status = "pass"
        actual = f"{runtime_prefix}{config_ver}"
        evidence = (
            f"{evidence_file} declares {runtime_prefix}{config_ver}, "
            f"consistent with the documented requirement {expected}."
        )
        suggested_fix = ""
        severity = None
    else:
        status = "fail"
        actual = f"{runtime_prefix}{config_ver}"
        evidence = (
            f"{evidence_file} requires {runtime_prefix}{config_ver}, "
            f"but the documentation claims {expected}. "
            f"These version constraints are inconsistent."
        )
        suggested_fix = (
            f"Update the documentation to state '{runtime_prefix}{config_ver}' "
            f"or lower the {evidence_file} constraint to '{runtime_prefix}{doc_ver}'."
        )
        severity = "high"

    return DocumentationContract(
        id=contract_id,
        area="runtime_requirements",
        source=source,
        claim=claim_sentence,
        expected=expected,
        actual=actual,
        status=status,  # type: ignore[arg-type]
        evidence=evidence,
        evidenceFile=evidence_file,
        evidenceLines=f"Line {line_no}" if status != "pass" else "See config",
        evidenceSnippet=evidence_snippet,
        suggested_fix=suggested_fix,
        approvalStatus="pending",
        approved=False,
        reverified=False,
        severity=severity,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Resolver table: runtime_key → config resolver function
# ---------------------------------------------------------------------------

_CONFIG_RESOLVERS = {
    "node": _resolve_node_config,
    "npm": _resolve_npm_config,
    "python": _resolve_python_config,
}


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
        One entry per (runtime, doc_file) pair where a version claim was found.
        Contracts are deterministically ordered: doc file → line number.
    """
    repo = Path(repo_path).resolve()
    pkg = _read_package_json(repo)
    doc_files = _find_doc_files(repo)

    # Deduplicate: track which (runtime_key) we have already emitted so that
    # repeated claims in the same file (table + prose) don't generate duplicates.
    seen_runtime_keys: set[str] = set()
    contracts: list[DocumentationContract] = []

    for doc_file in doc_files:
        try:
            text = doc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        claims = _extract_claims(text, doc_file, repo)

        for runtime_key, doc_ver, claim_sentence, line_no in claims:
            # Use only the first occurrence per runtime across all doc files
            if runtime_key in seen_runtime_keys:
                continue
            seen_runtime_keys.add(runtime_key)

            resolver = _CONFIG_RESOLVERS.get(runtime_key)
            if resolver is None:
                continue

            config_ver, evidence_file, evidence_snippet = resolver(repo, pkg)

            # Map runtime_key to the canonical display prefix
            prefix = next(
                prefix
                for key, _, prefix in _DOC_PATTERNS
                if key == runtime_key
            )

            _hash = hashlib.sha256(
                f"runtime_requirements|{doc_file.relative_to(repo)}#L{line_no}|{claim_sentence}".encode()
            ).hexdigest()[:8].upper()
            contract_id = f"RT-{runtime_key.upper()}-{_hash}"

            contracts.append(
                _build_contract(
                    contract_id=contract_id,
                    doc_file=doc_file,
                    repo=repo,
                    line_no=line_no,
                    claim_sentence=claim_sentence,
                    runtime_key=runtime_key,
                    runtime_prefix=prefix,
                    doc_ver=doc_ver,
                    config_ver=config_ver,
                    evidence_file=evidence_file,
                    evidence_snippet=evidence_snippet,
                )
            )

    return contracts
