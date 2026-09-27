"""Commands Verification Subagent.

Parses setup, build, and run command instructions from repository documentation
(README.md and any Markdown files under docs/), then validates each command
safely in the repository context by probing for structural errors (missing
scripts, missing entrypoints, missing executables) without causing side-effects.

For every command found a DocumentationContract is produced with:
  - status "pass"    when the command is structurally valid and executable
  - status "fail"    when the command has a detectable structural error
  - status "warning" when the command cannot be fully evaluated (e.g. the
                     required runtime is not installed on the host)

Public API
----------
run(repo_path: str | Path) -> list[DocumentationContract]
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from app.core.models import DocumentationContract

# ---------------------------------------------------------------------------
# Command context labels — inferred from the Markdown section heading that
# immediately precedes the fenced code block.
# ---------------------------------------------------------------------------

_SECTION_CONTEXT: dict[str, str] = {
    "install": "setup",
    "dependencies": "setup",
    "getting started": "setup",
    "setup": "setup",
    "build": "build",
    "compile": "build",
    "run": "run",
    "start": "run",
    "development server": "run",
    "backend": "run",
    "server": "run",
    "usage": "run",
}

_DEFAULT_CONTEXT = "run"


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Fenced code block: optional language tag, captures the body
_FENCE_RE = re.compile(
    r"(?:^|\n)```(?:bash|sh|shell|console|zsh)?\n(.*?)```",
    re.DOTALL,
)

# Heading immediately before a fenced block (used to infer context)
_HEADING_RE = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Parsed command dataclass (lightweight)
# ---------------------------------------------------------------------------

class _ParsedCommand:
    __slots__ = ("raw", "context", "source_file", "line_no")

    def __init__(
        self,
        raw: str,
        context: str,
        source_file: str,
        line_no: int,
    ) -> None:
        self.raw = raw
        self.context = context
        self.source_file = source_file
        self.line_no = line_no


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

def _infer_context(text: str, block_start: int) -> str:
    """Return the command context label by scanning backwards for a heading."""
    preceding = text[:block_start]
    headings = _HEADING_RE.findall(preceding)
    if not headings:
        return _DEFAULT_CONTEXT
    last_heading = headings[-1].lower()
    for keyword, ctx in _SECTION_CONTEXT.items():
        if keyword in last_heading:
            return ctx
    return _DEFAULT_CONTEXT


def _find_doc_files(repo: Path) -> list[Path]:
    """Return all Markdown files in the repo root and docs/ subdirectory."""
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


def _extract_commands(text: str, source_file: str) -> list[_ParsedCommand]:
    """Return all shell commands extracted from fenced code blocks."""
    commands: list[_ParsedCommand] = []
    for match in _FENCE_RE.finditer(text):
        block_start = match.start()
        body = match.group(1)
        context = _infer_context(text, block_start)
        # Count line number of this block's start
        line_no = text[:block_start].count("\n") + 1
        for cmd_line in body.splitlines():
            cmd = cmd_line.strip()
            # Skip empty lines and comment lines
            if not cmd or cmd.startswith("#"):
                continue
            # Skip lines that look like output (not a command)
            if cmd.startswith("$"):
                cmd = cmd[1:].strip()
            if cmd:
                commands.append(
                    _ParsedCommand(
                        raw=cmd,
                        context=context,
                        source_file=source_file,
                        line_no=line_no,
                    )
                )
    return commands


# ---------------------------------------------------------------------------
# Safe validation strategies
# ---------------------------------------------------------------------------

class _ValidationResult:
    __slots__ = ("status", "expected", "actual", "evidence", "evidence_file",
                 "evidence_lines", "evidence_snippet", "suggested_fix", "severity")

    def __init__(
        self,
        *,
        status: str,
        expected: str,
        actual: str,
        evidence: str,
        evidence_file: Optional[str] = None,
        evidence_lines: Optional[str] = None,
        evidence_snippet: Optional[str] = None,
        suggested_fix: str = "",
        severity: Optional[str] = None,
    ) -> None:
        self.status = status
        self.expected = expected
        self.actual = actual
        self.evidence = evidence
        self.evidence_file = evidence_file
        self.evidence_lines = evidence_lines
        self.evidence_snippet = evidence_snippet
        self.suggested_fix = suggested_fix
        self.severity = severity


def _validate_npm_command(cmd: str, repo: Path) -> _ValidationResult:
    """Validate an npm command structurally against package.json."""
    pkg_path = repo / "package.json"
    if not pkg_path.is_file():
        return _ValidationResult(
            status="warning",
            expected="package.json present in repo root",
            actual="package.json not found",
            evidence=(
                f"Cannot validate `{cmd}`: no package.json found in the "
                "repository root. The command may not be runnable."
            ),
            evidence_file="package.json",
            evidence_snippet="(file not found)",
            suggested_fix=(
                "Create a package.json file in the repository root, or "
                "remove this command from the documentation."
            ),
            severity="low",
        )

    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        return _ValidationResult(
            status="warning",
            expected="package.json is valid JSON",
            actual=f"package.json parse error: {exc}",
            evidence=f"Failed to parse package.json while validating `{cmd}`.",
            evidence_file="package.json",
            evidence_snippet="(parse error)",
            suggested_fix="Fix the JSON syntax in package.json.",
            severity="low",
        )

    parts = cmd.split()
    # `npm install` / `npm ci` / `npm run <script>` / `npm <script>`
    if len(parts) < 2:
        return _ValidationResult(
            status="warning",
            expected="npm sub-command present",
            actual=f"bare npm call: `{cmd}`",
            evidence=f"Command `{cmd}` has no sub-command.",
            evidence_file="package.json",
            evidence_snippet="",
            suggested_fix="Specify a sub-command, e.g. `npm install`.",
            severity="low",
        )

    sub = parts[1].lower()

    # install and ci are always structurally valid if package.json exists
    if sub in ("install", "ci", "i"):
        return _ValidationResult(
            status="pass",
            expected=f"`{cmd}` executable (package.json present)",
            actual=f"`{cmd}` — package.json found",
            evidence=(
                f"package.json exists in the repository root; `{cmd}` "
                "is structurally valid."
            ),
            evidence_file="package.json",
            evidence_snippet=f'"name": "{pkg.get("name", "(unnamed)")}"',
        )

    # `npm run <script>` or `npm <script>` (implicit run)
    script_name: Optional[str] = None
    if sub == "run" and len(parts) >= 3:
        script_name = parts[2]
    elif sub not in ("publish", "pack", "version", "test", "start", "stop"):
        # Unknown sub-command — warn but don't fail
        return _ValidationResult(
            status="warning",
            expected=f"`{cmd}` sub-command recognised",
            actual=f"sub-command `{sub}` not verified",
            evidence=(
                f"Sub-command `{sub}` is not automatically validated. "
                "Manual review recommended."
            ),
            evidence_file="package.json",
            evidence_snippet="",
            suggested_fix="",
            severity=None,
        )
    else:
        # npm start / npm test / npm stop resolve to 'start'/'test'/'stop' scripts
        script_name = sub

    if script_name is None:
        return _ValidationResult(
            status="warning",
            expected="npm script name determinable",
            actual=f"could not determine script name from `{cmd}`",
            evidence=f"Could not extract a script name from `{cmd}`.",
            evidence_file="package.json",
            evidence_snippet="",
            suggested_fix="",
        )

    scripts: dict = pkg.get("scripts", {})
    snippet = json.dumps({"scripts": scripts}, indent=2) if scripts else '{"scripts": {}}'

    if script_name in scripts:
        return _ValidationResult(
            status="pass",
            expected=f'script "{script_name}" defined in package.json',
            actual=f'scripts.{script_name} = "{scripts[script_name]}"',
            evidence=(
                f'package.json defines the "{script_name}" script; '
                f"`{cmd}` is valid."
            ),
            evidence_file="package.json",
            evidence_snippet=snippet,
        )
    else:
        return _ValidationResult(
            status="fail",
            expected=f'script "{script_name}" defined in package.json',
            actual=f'script "{script_name}" not found in package.json scripts',
            evidence=(
                f'package.json does not define a "{script_name}" script, '
                f"but the documentation instructs users to run `{cmd}`. "
                f"Defined scripts: {list(scripts.keys()) or '(none)'}."
            ),
            evidence_file="package.json",
            evidence_lines="scripts block",
            evidence_snippet=snippet,
            suggested_fix=(
                f'Add a "{script_name}" script to the "scripts" block in '
                f"package.json, or update the documentation to use one of "
                f"the existing scripts: {list(scripts.keys())}."
            ),
            severity="high",
        )


def _validate_pip_command(cmd: str, repo: Path) -> _ValidationResult:
    """Validate a pip command against requirements.txt / pyproject.toml."""
    parts = cmd.split()
    # Detect `pip install -r <file>`
    req_file: Optional[str] = None
    if "-r" in parts:
        idx = parts.index("-r")
        if idx + 1 < len(parts):
            req_file = parts[idx + 1]

    if req_file:
        req_path = repo / req_file
        if req_path.is_file():
            snippet_lines = req_path.read_text(encoding="utf-8", errors="replace").splitlines()
            # Show up to first 5 non-comment, non-empty lines
            deps = [ln for ln in snippet_lines if ln.strip() and not ln.strip().startswith("#")][:5]
            snippet = "\n".join(deps)
            return _ValidationResult(
                status="pass",
                expected=f"`{req_file}` exists in repo root",
                actual=f"`{req_file}` found",
                evidence=(
                    f"`{req_file}` exists in the repository root; "
                    f"`{cmd}` is structurally valid."
                ),
                evidence_file=req_file,
                evidence_snippet=snippet,
            )
        else:
            return _ValidationResult(
                status="fail",
                expected=f"`{req_file}` exists in repo root",
                actual=f"`{req_file}` not found",
                evidence=(
                    f"The documentation instructs users to run `{cmd}`, "
                    f"but `{req_file}` does not exist in the repository root."
                ),
                evidence_file=req_file,
                evidence_snippet="(file not found)",
                suggested_fix=(
                    f"Create `{req_file}` in the repository root, or update "
                    "the documentation to reference the correct requirements file."
                ),
                severity="high",
            )

    # Generic `pip install <package>` — warn (can't verify without running)
    return _ValidationResult(
        status="warning",
        expected=f"`{cmd}` is verifiable",
        actual="direct package install — not statically verifiable",
        evidence=(
            f"`{cmd}` installs packages directly; static validation is not "
            "possible without executing the command."
        ),
        evidence_file=None,
        evidence_snippet="",
        suggested_fix=(
            "Consider using a requirements file (`pip install -r requirements.txt`) "
            "for reproducible installs."
        ),
        severity=None,
    )


def _validate_python_module_command(cmd: str, repo: Path) -> _ValidationResult:
    """Validate `python -m <module> ...` commands."""
    parts = cmd.split()
    # find '-m' flag
    try:
        m_idx = parts.index("-m")
    except ValueError:
        return _ValidationResult(
            status="warning",
            expected="`python -m` syntax",
            actual=f"cannot parse: `{cmd}`",
            evidence=f"Could not parse module invocation in `{cmd}`.",
            evidence_file=None,
            evidence_snippet="",
            suggested_fix="",
        )

    if m_idx + 1 >= len(parts):
        return _ValidationResult(
            status="fail",
            expected="module name after `-m`",
            actual=f"no module name in `{cmd}`",
            evidence=f"`{cmd}` uses `-m` without a module name.",
            evidence_file=None,
            evidence_snippet="",
            suggested_fix=f"Provide a module name, e.g. `python -m uvicorn main:app`.",
            severity="high",
        )

    module = parts[m_idx + 1]

    # Check for uvicorn / gunicorn — validate entrypoint file
    if module in ("uvicorn", "gunicorn"):
        # Look for `<module>:<app>` argument
        app_arg: Optional[str] = None
        for part in parts[m_idx + 2:]:
            if ":" in part and not part.startswith("-"):
                app_arg = part
                break

        if app_arg:
            entrypoint_module = app_arg.split(":")[0]
            # Convert dotted module path to file path
            entrypoint_path = repo / (entrypoint_module.replace(".", "/") + ".py")
            if entrypoint_path.is_file():
                return _ValidationResult(
                    status="pass",
                    expected=f"entrypoint `{entrypoint_module}.py` exists",
                    actual=f"`{entrypoint_path.name}` found",
                    evidence=(
                        f"Entrypoint `{entrypoint_module}.py` exists in the "
                        f"repository; `{cmd}` is structurally valid."
                    ),
                    evidence_file=entrypoint_path.name,
                    evidence_snippet=f"# {entrypoint_path.name}",
                )
            else:
                return _ValidationResult(
                    status="fail",
                    expected=f"entrypoint `{entrypoint_module}.py` exists in repo root",
                    actual=f"`{entrypoint_module}.py` not found",
                    evidence=(
                        f"The documentation instructs users to run `{cmd}`, "
                        f"but the entrypoint `{entrypoint_module}.py` does not "
                        f"exist in the repository root."
                    ),
                    evidence_file=f"{entrypoint_module}.py",
                    evidence_snippet="(file not found)",
                    suggested_fix=(
                        f"Create `{entrypoint_module}.py` in the repository root "
                        f"with an `app` ASGI application object, or update the "
                        f"documentation to reference the correct entrypoint."
                    ),
                    severity="high",
                )

    # For other modules, check if the executable is available on PATH
    exe = shutil.which("python") or shutil.which("python3")
    if exe is None:
        return _ValidationResult(
            status="warning",
            expected="Python interpreter available on PATH",
            actual="python / python3 not found on PATH",
            evidence=f"Cannot validate `{cmd}`: Python not found on PATH.",
            evidence_file=None,
            evidence_snippet="",
            suggested_fix="Install Python and ensure it is on your PATH.",
            severity="low",
        )

    return _ValidationResult(
        status="pass",
        expected=f"Python module `{module}` runnable",
        actual=f"`{module}` invocable via python -m",
        evidence=f"Python is available on PATH; `{cmd}` uses a standard module invocation.",
        evidence_file=None,
        evidence_snippet="",
    )


def _validate_command(cmd: str, repo: Path) -> _ValidationResult:
    """Dispatch to the appropriate validator based on the command prefix."""
    parts = cmd.split()
    if not parts:
        return _ValidationResult(
            status="warning",
            expected="non-empty command",
            actual="(empty command)",
            evidence="Empty command line found in documentation.",
            suggested_fix="Remove the empty line from the documentation code block.",
        )

    exe = parts[0].lower()

    if exe == "npm":
        return _validate_npm_command(cmd, repo)

    if exe in ("pip", "pip3", "pip2"):
        return _validate_pip_command(cmd, repo)

    if exe in ("python", "python3", "python2"):
        if "-m" in parts:
            return _validate_python_module_command(cmd, repo)
        # Plain `python <script>.py`
        if len(parts) >= 2 and parts[1].endswith(".py"):
            script_path = repo / parts[1]
            if script_path.is_file():
                return _ValidationResult(
                    status="pass",
                    expected=f"script `{parts[1]}` exists",
                    actual=f"`{parts[1]}` found",
                    evidence=f"`{parts[1]}` exists in the repository.",
                    evidence_file=parts[1],
                    evidence_snippet=f"# {parts[1]}",
                )
            else:
                return _ValidationResult(
                    status="fail",
                    expected=f"script `{parts[1]}` exists in repo root",
                    actual=f"`{parts[1]}` not found",
                    evidence=(
                        f"The documentation instructs users to run `{cmd}`, "
                        f"but `{parts[1]}` does not exist in the repository root."
                    ),
                    evidence_file=parts[1],
                    evidence_snippet="(file not found)",
                    suggested_fix=(
                        f"Create `{parts[1]}` in the repository root or update "
                        "the documentation to reference the correct script."
                    ),
                    severity="high",
                )
        return _ValidationResult(
            status="warning",
            expected=f"`{cmd}` is verifiable",
            actual="non-module python invocation — not statically verifiable",
            evidence=f"`{cmd}` invokes Python without a `-m` module or explicit script path.",
            suggested_fix="Use `python -m <module>` or specify an explicit script path.",
        )

    # Unknown command — warn
    return _ValidationResult(
        status="warning",
        expected=f"validator available for `{exe}`",
        actual=f"no validator for `{exe}`",
        evidence=(
            f"`{cmd}` uses the `{exe}` executable which has no automated "
            "validator. Manual verification is recommended."
        ),
        suggested_fix="",
        severity=None,
    )


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------

def _build_contract(
    *,
    contract_id: str,
    parsed: _ParsedCommand,
    result: _ValidationResult,
) -> DocumentationContract:
    return DocumentationContract(
        id=contract_id,
        area="commands",
        source=f"{parsed.source_file}#L{parsed.line_no}",
        claim=parsed.raw,
        expected=result.expected,
        actual=result.actual,
        status=result.status,  # type: ignore[arg-type]
        evidence=result.evidence,
        evidenceFile=result.evidence_file,
        evidenceLines=result.evidence_lines,
        evidenceSnippet=result.evidence_snippet,
        suggested_fix=result.suggested_fix,
        approvalStatus="pending",
        approved=False,
        reverified=False,
        severity=result.severity,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Deduplication key: same (command, context) across multiple doc files
# ---------------------------------------------------------------------------

def _dedup_key(cmd: str) -> str:
    """Normalised command string used for deduplication."""
    return " ".join(cmd.lower().split())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(repo_path: str | Path) -> list[DocumentationContract]:
    """Parse and validate all documented commands in *repo_path*.

    Parameters
    ----------
    repo_path:
        Absolute or relative path to the repository root to analyse.

    Returns
    -------
    list[DocumentationContract]
        One entry per unique documented command. Contracts are ordered by
        document file, then by line number within each file.
        Status values:
          - "pass"    — command is structurally valid
          - "fail"    — command has a detectable structural error
          - "warning" — command cannot be fully evaluated statically
    """
    repo = Path(repo_path).resolve()

    if not repo.is_dir():
        raise ValueError(
            f"commands_agent: repo_path {repo!r} is not a directory."
        )

    doc_files = _find_doc_files(repo)

    seen_commands: set[str] = set()
    contracts: list[DocumentationContract] = []

    for doc_file in doc_files:
        try:
            text = doc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        rel_path = str(doc_file.relative_to(repo))
        parsed_commands = _extract_commands(text, rel_path)

        for parsed in parsed_commands:
            key = _dedup_key(parsed.raw)
            if key in seen_commands:
                continue
            seen_commands.add(key)

            result = _validate_command(parsed.raw, repo)
            _hash = hashlib.sha256(
                f"commands|{parsed.source_file}#L{parsed.line_no}|{parsed.raw}".encode()
            ).hexdigest()[:8].upper()
            contract_id = f"CMD-{_hash}"

            contracts.append(
                _build_contract(
                    contract_id=contract_id,
                    parsed=parsed,
                    result=result,
                )
            )

    return contracts
