"""Fix Suggestion Subagent.

Aggregates all failing ``DocumentationContract`` objects produced by the other
verification subagents and generates an actionable, precise fix for each one.

Each fix is returned as a ``FixSuggestion`` containing:

* ``diff``       — a unified-diff fragment showing exactly what to change
* ``raw_fix``    — the same correction as plain prose (for non-diff consumers)
* ``patch_type`` — ``"doc_edit"`` | ``"config_edit"`` | ``"code_edit"``
* ``target_file``— the file the developer should edit

Fixes are generated purely from the data already present in each contract
(``area``, ``expected``, ``actual``, ``source``, ``evidenceSnippet``,
``evidenceFile``, ``suggested_fix``).  No filesystem access is required,
which keeps the agent fast, deterministic, and easily testable in isolation.

Public API
----------
run(contracts: list[DocumentationContract]) -> list[FixSuggestion]
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from app.core.models import DocumentationContract, FixSuggestion


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _source_file(contract: DocumentationContract) -> str:
    """Return the bare file path from ``source`` (strip ``#Lnn`` suffix)."""
    return contract.source.split("#")[0]


def _make_diff(
    target_file: str,
    old_lines: list[str],
    new_lines: list[str],
    context_header: str = "",
) -> str:
    """Build a minimal unified-diff string.

    The output mirrors the format produced by ``diff -u`` / ``git diff``:

    .. code-block:: text

        --- a/path/to/file
        +++ b/path/to/file
        @@ -1,2 +1,2 @@
        -old line
        +new line

    Parameters
    ----------
    target_file:
        Path shown in the diff header (relative to repo root).
    old_lines:
        Lines to remove (without trailing newline).
    new_lines:
        Lines to add (without trailing newline).
    context_header:
        Optional ``@@ … @@`` context label (e.g. ``"Prerequisites"``).
    """
    hunk_header = f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@"
    if context_header:
        hunk_header += f" {context_header}"

    parts = [
        f"--- a/{target_file}",
        f"+++ b/{target_file}",
        hunk_header,
    ]
    for line in old_lines:
        parts.append(f"-{line}")
    for line in new_lines:
        parts.append(f"+{line}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Area-specific fix generators
# ---------------------------------------------------------------------------

def _fix_runtime_requirements(contract: DocumentationContract) -> FixSuggestion:
    """Generate a doc-edit fix that aligns the documented version to the config.

    Strategy
    --------
    The ``expected`` field holds the *documented* claim (e.g. ``Node.js >=18``).
    The ``actual`` field holds what the config declares (e.g. ``Node.js >=20``).
    We update the documentation sentence to use the config's version.
    """
    doc_file = _source_file(contract)
    claim = contract.claim        # original doc sentence
    expected = contract.expected  # e.g. "Node.js >=18"
    actual = contract.actual      # e.g. "Node.js >=20"

    # Extract just the version numbers for substitution
    expected_ver = re.search(r"([\d]+(?:\.[\d]+)*)", expected)
    actual_ver = re.search(r"([\d]+(?:\.[\d]+)*)", actual)

    if expected_ver and actual_ver:
        old_ver = expected_ver.group(1)
        new_ver = actual_ver.group(1)
        # Build the corrected sentence: replace the version in the original claim
        corrected_claim = re.sub(
            re.escape(old_ver) + r"(?!\d)",
            new_ver,
            claim,
            count=1,
        )
    else:
        corrected_claim = f"{actual}+"

    diff = _make_diff(
        target_file=doc_file,
        old_lines=[claim],
        new_lines=[corrected_claim],
        context_header=f"runtime version claim",
    )

    return FixSuggestion(
        contract_id=contract.id,
        area=contract.area,
        patch_type="doc_edit",
        target_file=doc_file,
        description=(
            f"Update the documented {expected.split()[0]} version "
            f"from {expected} to {actual} to match the project configuration."
        ),
        diff=diff,
        raw_fix=corrected_claim,
    )


def _fix_commands(contract: DocumentationContract) -> FixSuggestion:
    """Generate a doc-edit fix replacing the wrong command with the correct one.

    Strategy
    --------
    ``claim``    — the documented command (e.g. ``npm start``).
    ``expected`` — what was expected (e.g. ``script "start" defined in package.json``).
    ``actual``   — what was found  (e.g. ``script "start" not found``).
    ``suggested_fix`` — already contains the corrected text when populated by
                        commands_agent; we re-use it and also build the diff.
    ``evidenceSnippet`` — the scripts block from package.json, used to infer
                          the right replacement command.
    """
    doc_file = _source_file(contract)
    old_cmd = contract.claim

    # Derive the replacement command from existing suggested_fix or snippet
    if contract.suggested_fix:
        # Extract the first `backtick` command from the suggested_fix text
        m = re.search(r"`([^`]+)`", contract.suggested_fix)
        new_cmd = m.group(1) if m else contract.suggested_fix
    else:
        # Fall back to the actual field which describes what is present
        new_cmd = contract.actual

    diff = _make_diff(
        target_file=doc_file,
        old_lines=[old_cmd],
        new_lines=[new_cmd],
        context_header="shell command",
    )

    return FixSuggestion(
        contract_id=contract.id,
        area=contract.area,
        patch_type="doc_edit",
        target_file=doc_file,
        description=(
            f"Replace the undocumented command `{old_cmd}` "
            f"with `{new_cmd}` in {doc_file}."
        ),
        diff=diff,
        raw_fix=new_cmd,
    )


def _fix_config_env(contract: DocumentationContract) -> FixSuggestion:
    """Generate a fix for a missing or undocumented environment variable.

    Two sub-cases handled:
    * ``fail``    — key used in source but absent from ``.env.example``:
                    add an entry to ``.env.example``.
    * ``warning`` — key in ``.env.example`` but not documented:
                    add a description line to the README.

    For a ``fail`` contract the ``actual`` field contains a message like
    ``"SECRET_KEY missing from .env.example"``.
    """
    doc_file = _source_file(contract)
    claim = contract.claim
    expected = contract.expected
    actual = contract.actual
    snippet = contract.evidenceSnippet or ""

    # Extract the variable name — first ALL_CAPS token in the claim
    m = re.search(r"\b([A-Z][A-Z0-9_]+)\b", claim)
    var_name = m.group(1) if m else "MISSING_VAR"

    if "missing from .env.example" in actual or ".env.example" in expected:
        # fail case: add to .env.example
        target = ".env.example"
        new_entry = f"{var_name}=<your-value-here>"
        diff = _make_diff(
            target_file=target,
            old_lines=[],
            new_lines=[new_entry],
            context_header=f"add {var_name}",
        )
        raw_fix = (
            f"Add the following line to .env.example:\n\n    {new_entry}"
        )
        description = (
            f"Declare `{var_name}` in .env.example so that new checkouts "
            f"know this environment variable is required."
        )
        patch_type: str = "config_edit"
    else:
        # warning case: add a documentation entry
        target = doc_file if doc_file.endswith(".md") else "README.md"
        doc_line = f"- `{var_name}` — {contract.suggested_fix or 'describe this variable'}"
        diff = _make_diff(
            target_file=target,
            old_lines=[],
            new_lines=[doc_line],
            context_header="Environment Variables section",
        )
        raw_fix = (
            f"Add the following line under the Environment Variables section "
            f"of {target}:\n\n    {doc_line}"
        )
        description = (
            f"Document `{var_name}` in {target} so developers know "
            f"what value to supply."
        )
        patch_type = "doc_edit"

    return FixSuggestion(
        contract_id=contract.id,
        area=contract.area,
        patch_type=patch_type,  # type: ignore[arg-type]
        target_file=target,
        description=description,
        diff=diff,
        raw_fix=raw_fix,
    )


def _fix_api_docs(contract: DocumentationContract) -> FixSuggestion:
    """Generate a fix for a documented-but-missing API endpoint.

    Strategy
    --------
    ``claim``   — ``"DELETE /contracts/{id} is documented"``
    ``actual``  — ``"DELETE /contracts/{id} not found in backend routes"``

    We produce a diff that removes the phantom section from the API doc file,
    and also provide ``raw_fix`` as a FastAPI route stub the developer can add
    to the backend instead (if they want to keep the endpoint).
    """
    doc_file = _source_file(contract)
    claim = contract.claim
    expected = contract.expected
    actual = contract.actual

    # Extract METHOD and /path from the claim
    m = re.match(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)", claim)
    if m:
        method = m.group(1)
        path = m.group(2).rstrip(".")
    else:
        method = "GET"
        path = "/unknown"

    # Option A diff: remove the doc entry
    doc_remove_diff = _make_diff(
        target_file=doc_file,
        old_lines=[f"{method} {path}"],
        new_lines=[],
        context_header=f"remove phantom endpoint",
    )

    # Option B: FastAPI implementation stub (shown in raw_fix)
    path_for_decorator = path
    method_lower = method.lower()
    func_name = path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_") or "endpoint"
    func_name = f"{method_lower}_{func_name}"

    impl_stub = (
        f'@router.{method_lower}("{path_for_decorator}")\n'
        f"def {func_name}():\n"
        f'    """TODO: implement {method} {path}."""\n'
        f"    raise NotImplementedError"
    )

    raw_fix = (
        f"Either remove `{method} {path}` from {doc_file} "
        f"(if the endpoint is not needed), or add the following route to "
        f"the appropriate FastAPI router:\n\n"
        + "\n".join(f"    {line}" for line in impl_stub.splitlines())
    )

    return FixSuggestion(
        contract_id=contract.id,
        area=contract.area,
        patch_type="doc_edit",
        target_file=doc_file,
        description=(
            f"Remove the undocumented endpoint `{method} {path}` from "
            f"{doc_file}, or implement it in the FastAPI backend."
        ),
        diff=doc_remove_diff,
        raw_fix=raw_fix,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_AREA_GENERATORS: dict[str, Callable[[DocumentationContract], FixSuggestion]] = {
    "runtime_requirements": _fix_runtime_requirements,
    "commands": _fix_commands,
    "config_env": _fix_config_env,
    "api_docs": _fix_api_docs,
}


def _generate_fix(contract: DocumentationContract) -> Optional[FixSuggestion]:
    """Return a FixSuggestion for *contract*, or None if the area is unknown."""
    generator = _AREA_GENERATORS.get(contract.area)
    if generator is None:
        return None
    return generator(contract)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(contracts: list[DocumentationContract]) -> list[FixSuggestion]:
    """Aggregate failing contracts and generate a structured fix for each one.

    Parameters
    ----------
    contracts:
        All ``DocumentationContract`` objects from a verification run.
        Only contracts with ``status == "fail"`` are processed.

    Returns
    -------
    list[FixSuggestion]
        One ``FixSuggestion`` per failing contract, preserving the input order
        of the failing contracts.  Contracts whose area is unrecognised are
        silently skipped (no partial stubs are emitted).
    """
    suggestions: list[FixSuggestion] = []
    for contract in contracts:
        if contract.status != "fail":
            continue
        fix = _generate_fix(contract)
        if fix is not None:
            suggestions.append(fix)
    return suggestions
