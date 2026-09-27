"""Documentation Claim Parser -- Milestone 2.

Converts developer documentation (README.md, .env.example, docs/*.md) into
structured ExtractedClaim objects that verification subagents can later
check against the repository.

This module is responsible ONLY for parsing.  It must never:
  - read the repository filesystem
  - set actual / status / evidence / suggested_fix / approval fields
  - construct a DocumentationContract

Public API:
  ParseRequest          -- input dataclass
  ExtractedClaim        -- intermediate output dataclass
  normalize_runtime()   -- pure normalization helper
  normalize_command()   -- pure normalization helper
  normalize_env_claim() -- pure normalization helper
  normalize_api_route() -- pure normalization helper
  parse_documentation() -- main entry point

Not yet implemented:
  Command/config/API extraction from Markdown, deduplication.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.core.models import VerificationArea


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParseRequest:
    """Input to the parser."""
    source_path: str
    content: str


@dataclass
class ExtractedClaim:
    """Intermediate representation produced by the parser.

    Populated fields:
        area, claim, expected, source, extraction_method

    Fields intentionally NOT populated here (owned by other modules):
        actual, status, evidence, evidenceFile, evidenceLines,
        evidenceSnippet, suggested_fix, approvalStatus, approved,
        reverified, id, severity
    """
    area: VerificationArea
    claim: str
    expected: str
    source: str
    extraction_method: str = field(default="regex")


# ---------------------------------------------------------------------------
# normalize_runtime
# ---------------------------------------------------------------------------

_RUNTIME_ALIASES: dict[str, str] = {
    "node":    "Node.js",
    "node.js": "Node.js",
    "nodejs":  "Node.js",
    "python":  "Python",
    "python3": "Python",
    "npm":     "npm",
    "pnpm":    "pnpm",
    "yarn":    "yarn",
    "ruby":    "Ruby",
    "java":    "Java",
    "go":      "Go",
    "rust":    "Rust",
    "docker":  "Docker",
}

_RUNTIME_RE = re.compile(
    r"""
    (?:requires?|needs?|use|using)?\s*
    (?P<name>
        node\.js | nodejs | node |
        python3  | python |
        pnpm     | npm    | yarn |
        ruby     | java   | go   | rust | docker
    )
    \s*
    (?P<constraint>
        >=?\s*\d[\d.]*
      | ==\s*\d[\d.]*
      | <=?\s*\d[\d.]*
      | \d[\d.]*\+
      | \d[\d.]*\s+or\s+(?:newer|higher|later|above)
      | \d[\d.]*\s+(?:newer|higher|later|above)
      | \d[\d.]*
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_QUALIFIER_TO_OP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(\d[\d.]*)\s+or\s+(?:newer|higher|later|above)", re.I), r">=\1"),
    (re.compile(r"(\d[\d.]*)\s+(?:newer|higher|later|above)", re.I),      r">=\1"),
    (re.compile(r"(\d[\d.]*)\+"),                                           r">=\1"),
    (re.compile(r">=\s*(\d[\d.]*)"),                                        r">=\1"),
    (re.compile(r"==\s*(\d[\d.]*)"),                                        r"==\1"),
    (re.compile(r">\s*(\d[\d.]*)"),                                         r">\1"),
    (re.compile(r"<=\s*(\d[\d.]*)"),                                        r"<=\1"),
    (re.compile(r"<\s*(\d[\d.]*)"),                                         r"<\1"),
]


def normalize_runtime(text: str) -> str:
    """Normalise a runtime requirement string to a canonical form.

    Examples:
        "Node.js 16+"          -> "Node.js >=16"
        "Python 3.11 or newer" -> "Python >=3.11"
        "node 18+"             -> "Node.js >=18"
        "Requires Docker"      -> "Docker"
    """
    text = re.sub(r"^[-*\s]+", "", text.strip())

    m = _RUNTIME_RE.search(text)
    if not m:
        return text

    canonical = _RUNTIME_ALIASES.get(m.group("name").lower(), m.group("name"))
    raw_constraint = (m.group("constraint") or "").strip()

    if not raw_constraint:
        return canonical

    norm = raw_constraint
    for pattern, replacement in _QUALIFIER_TO_OP:
        replaced = pattern.sub(replacement, norm)
        if replaced != norm:
            norm = replaced
            break

    norm = re.sub(r"(>=|==|<=|>|<)\s*", r"\1", norm)
    return "{} {}".format(canonical, norm)


# ---------------------------------------------------------------------------
# normalize_command
# ---------------------------------------------------------------------------

_BACKTICK_RE = re.compile(r"`([^`]+)`")

_CMD_RE = re.compile(
    r"""
    (?P<cmd>
        (?:npm|pnpm|yarn|npx)
        (?:
            \s+run\s+\S+
          | \s+(?:install|start|test|build|preview|typecheck|lint|dev
                 |ci|publish|pack|update|outdated|audit|init)
        )?
      | (?:python3?|pip3?)\s+\S+
      | uvicorn\s+\S+
      | docker\s+\S+(?:\s+\S+)?
      | make\s+\S+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_command(text: str) -> str:
    """Extract and normalise a command string."""
    text = text.strip()

    for bt in _BACKTICK_RE.finditer(text):
        inner = bt.group(1).strip()
        m = _CMD_RE.match(inner)
        if m:
            return m.group("cmd").strip()

    if text.startswith("`") and text.endswith("`") and len(text) > 2:
        text = text[1:-1].strip()

    m = _CMD_RE.search(text)
    if m:
        return m.group("cmd").strip()

    return text


# ---------------------------------------------------------------------------
# normalize_env_claim
# ---------------------------------------------------------------------------

_VAR_NAME_RE = re.compile(r"`?([A-Z_][A-Z0-9_]*)`?", re.IGNORECASE)
_REQUIRED_RE = re.compile(r"\b(required|mandatory|must\s+be\s+set)\b", re.I)
_DEFAULT_RE  = re.compile(
    r"(?:default\s+is|defaults?\s+to|default:?)\s+(?P<val>\S+)",
    re.I,
)


def normalize_env_claim(text: str) -> str:
    """Normalise an environment-variable claim to a machine-comparable form."""
    text = text.strip()

    if "=" in text:
        idx = text.index("=")
        raw_key = text[:idx].strip()
        raw_val = re.sub(r"\s+#.*$", "", text[idx + 1:]).strip()
        key = raw_key.upper()
        if not key or not re.match(r"^[A-Z_][A-Z0-9_]*$", key):
            return ""
        return "{}={}".format(key, raw_val)

    name_m = _VAR_NAME_RE.search(text)
    if not name_m:
        return ""
    key = name_m.group(1).upper()

    if _REQUIRED_RE.search(text):
        return "{} required".format(key)

    default_m = _DEFAULT_RE.search(text)
    if default_m:
        val = default_m.group("val").rstrip(".,;")
        return "{}={}".format(key, val)

    return key


# ---------------------------------------------------------------------------
# normalize_api_route
# ---------------------------------------------------------------------------

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

_ROUTE_RE = re.compile(
    r"""
    (?:\|\s*)?
    (?P<method>[A-Za-z]+)
    \s*\|?\s*
    (?P<route>/[^\s|]*)
    """,
    re.VERBOSE,
)


def normalize_api_route(text: str) -> str:
    """Normalise an API route claim to "METHOD /route"."""
    text = text.strip()
    m = _ROUTE_RE.search(text)
    if not m:
        return ""
    method = m.group("method").upper()
    if method not in _HTTP_METHODS:
        return ""
    route = m.group("route").rstrip("|").strip()
    return "{} {}".format(method, route)


# ---------------------------------------------------------------------------
# Internal: file-type detection
# ---------------------------------------------------------------------------

def _normalize_source_path(source_path: str) -> str:
    """Normalise Windows backslashes to forward slashes."""
    return source_path.replace("\\", "/")


def _detect_file_type(source_path: str) -> str:
    """Return "env", "markdown", or "unknown"."""
    path = _normalize_source_path(source_path)
    filename = path.split("/")[-1]

    if filename == ".env" or filename.startswith(".env.") or filename.endswith(".env"):
        return "env"
    if filename.endswith(".md"):
        return "markdown"
    return "unknown"


# ---------------------------------------------------------------------------
# Internal: env-file parser
# ---------------------------------------------------------------------------

def _parse_env_file(request: ParseRequest) -> List[ExtractedClaim]:
    """Parse a .env file into config_env ExtractedClaim objects."""
    source_path = _normalize_source_path(request.source_path)
    content = request.content.replace("\r\n", "\n")

    claims: List[ExtractedClaim] = []
    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.rstrip()

        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue

        idx = line.index("=")
        raw_key = line[:idx].strip()
        if not raw_key:
            continue

        expected = normalize_env_claim(line)
        if not expected:
            continue

        claims.append(ExtractedClaim(
            area="config_env",
            claim=line,
            expected=expected,
            source="{}#L{}".format(source_path, lineno),
        ))

    return claims


# ---------------------------------------------------------------------------
# Internal: Markdown section detection
# ---------------------------------------------------------------------------

# Heading-to-area mapping.  Each entry is (area, set_of_lowercase_keyword_patterns).
# Matched case-insensitively against the stripped heading text.
# Priority order: runtime_requirements > commands > config_env > api_docs.
_HEADING_MAP: list[tuple[str, frozenset[str]]] = [
    ("runtime_requirements", frozenset({
        "requirements", "prerequisites", "dependencies", "runtime", "tech stack",
    })),
    ("commands", frozenset({
        "usage", "commands", "getting started", "running", "scripts",
        "run", "start", "installation", "install", "setup",
        "development", "build", "testing", "tests",
    })),
    ("config_env", frozenset({
        "environment", "configuration", "env", "environment variables", "config",
    })),
    ("api_docs", frozenset({
        "api", "api reference", "endpoints", "routes", "rest api",
    })),
]

# Matches any ATX heading: one or more # followed by text.
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")

# Matches opening/closing of a fenced code block (``` or ~~~).
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


@dataclass
class _Section:
    area: str                                # VerificationArea value
    heading_line: int                        # 1-based line of the opening heading
    body_lines: list[tuple[int, str]] = field(default_factory=list)
    # (1-based line number, text) -- includes nested headings and their bodies


def _map_heading_to_area(heading_text: str) -> Optional[str]:
    """Return a VerificationArea string if the heading maps to one, else None."""
    text = heading_text.strip().lower()
    for area, keywords in _HEADING_MAP:
        if text in keywords:
            return area
    return None


def _detect_sections(lines: list[tuple[int, str]]) -> list[_Section]:
    """Scan lines and return a list of _Section objects.

    Rules:
    - Lines inside fenced code blocks are never treated as headings.
    - A heading whose text maps to a known area opens a new section (closing
      any previously open section).
    - A heading whose text does NOT map to any area is appended as a body
      line to the currently open section (if one is open), or ignored.
    - Non-heading lines are appended to the currently open section body.
    """
    sections: list[_Section] = []
    current: Optional[_Section] = None
    in_fence = False
    fence_marker: str = ""

    for lineno, text in lines:
        # Track fenced code block boundaries
        fence_m = _FENCE_RE.match(text)
        if fence_m:
            marker = fence_m.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0] * len(marker)  # normalise to same char
            elif text.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            # The fence delimiter line itself goes into the current body if open
            if current is not None:
                current.body_lines.append((lineno, text))
            continue

        # Inside a fenced block: body content only
        if in_fence:
            if current is not None:
                current.body_lines.append((lineno, text))
            continue

        # Check for a heading
        heading_m = _HEADING_RE.match(text)
        if heading_m:
            heading_text = heading_m.group(1).strip()
            area = _map_heading_to_area(heading_text)
            if area is not None:
                # Open a new section
                current = _Section(area=area, heading_line=lineno)
                sections.append(current)
            else:
                # Unrecognised heading: append to current body if one is open
                if current is not None:
                    current.body_lines.append((lineno, text))
            continue

        # Regular body line
        if current is not None:
            current.body_lines.append((lineno, text))

    return sections


# ---------------------------------------------------------------------------
# Internal: runtime extractor
# ---------------------------------------------------------------------------

def _extract_runtime(
    body_lines: list[tuple[int, str]],
    source_path: str,
) -> list[ExtractedClaim]:
    """Extract runtime-requirement claims from a section body.

    Scans each line (and each line inside fenced blocks) for a recognisable
    runtime name.  Uses normalize_runtime() to produce the expected value.
    Skips lines where normalize_runtime() returns only whitespace or the
    original text unchanged AND that text contains no digit (bare unknown word).
    """
    claims: list[ExtractedClaim] = []
    in_fence = False
    fence_marker = ""

    for lineno, text in body_lines:
        stripped = text.strip()

        # Track fenced block state
        fence_m = _FENCE_RE.match(stripped)
        if fence_m:
            marker = fence_m.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0] * len(marker)
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue

        # Skip empty lines
        if not stripped:
            continue

        # Skip table separator lines (|---|)
        if re.match(r"^\|[-|\s:]+\|?$", stripped):
            continue

        # Check if line contains a recognisable runtime
        m = _RUNTIME_RE.search(stripped)
        if not m:
            continue

        expected = normalize_runtime(stripped)

        # Skip if normalization produced empty or only whitespace
        if not expected.strip():
            continue

        claims.append(ExtractedClaim(
            area="runtime_requirements",
            claim=stripped,
            expected=expected,
            source="{}#L{}".format(source_path, lineno),
        ))

    return claims


# ---------------------------------------------------------------------------
# Internal: command extractor
# ---------------------------------------------------------------------------

def _extract_commands(
    body_lines: list[tuple[int, str]],
    source_path: str,
) -> list[ExtractedClaim]:
    """Extract command claims from a section body.

    Inside fenced code blocks every non-empty line is a command candidate;
    outside them, _CMD_RE must find a recognised command (supporting plain
    prose and inline backticks).  Only emits a claim when _CMD_RE actually
    matches a supported command.

    Local deduplication within this section via ``seen`` keyed on expected.
    """
    claims: list[ExtractedClaim] = []
    seen: set[str] = set()
    in_fence = False
    fence_marker = ""

    for lineno, text in body_lines:
        stripped = text.strip()

        # Track fenced block state; fence delimiter lines are never candidates
        fence_m = _FENCE_RE.match(stripped)
        if fence_m:
            marker = fence_m.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0] * len(marker)
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue

        # Skip empty lines
        if not stripped:
            continue

        if in_fence:
            # Inside fenced block: only emit when _CMD_RE recognises a command
            m = _CMD_RE.search(stripped)
            if not m:
                continue
            expected = m.group("cmd").strip()
        else:
            # Outside fenced block: prose + inline-backtick support via
            # normalize_command, but only when _CMD_RE actually matched
            if not _CMD_RE.search(stripped):
                continue
            expected = normalize_command(stripped)
            # Confirm normalize_command resolved to a recognised command token
            if not _CMD_RE.match(expected):
                continue

        if not expected:
            continue

        if expected in seen:
            continue
        seen.add(expected)

        claims.append(ExtractedClaim(
            area="commands",
            claim=stripped,
            expected=expected,
            source="{}#L{}".format(source_path, lineno),
        ))

    return claims


# ---------------------------------------------------------------------------
# Internal: Markdown config_env extractor
# ---------------------------------------------------------------------------

# Matches a Markdown table row: | cell | cell | ...
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
# Matches a table separator row: |---|---|...
_TABLE_SEP_RE = re.compile(r"^\|[-|\s:]+\|$")
# Valid env-variable name (for quick guard check)
_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$", re.IGNORECASE)


def _extract_config_from_markdown(
    body_lines: list[tuple[int, str]],
    source_path: str,
) -> list[ExtractedClaim]:
    """Extract config_env claims from a Markdown section body.

    Supports two source forms:

    1. Prose / bullet lines  (uses normalize_env_claim)
       - `PORT` -- default is 8080          -> PORT=8080
       - `DATABASE_URL` is required.        -> DATABASE_URL required
       - `LOG_LEVEL` -- optional.           -> LOG_LEVEL

    2. Markdown tables with columns Variable / Required / Default
       | DATABASE_URL | Yes | --   | ...   -> DATABASE_URL required
       | PORT         | No  | 8080 | ...   -> PORT=8080

    Blank lines, table header rows, and separator rows are skipped.
    Lines/rows without a recognisable env-variable name are skipped.
    """
    claims: list[ExtractedClaim] = []

    # Detect whether this section has a table by scanning for table rows.
    # We do a single pass and handle both prose and table rows inline.
    # Table column positions are determined from the header row.

    # State for table parsing
    table_header: list[str] | None = None  # lowercase column names
    col_var: int = -1
    col_req: int = -1
    col_def: int = -1

    for lineno, text in body_lines:
        stripped = text.strip()

        if not stripped:
            continue

        # --- Table separator row: reset header context, skip ---
        if _TABLE_SEP_RE.match(stripped):
            continue

        # --- Table row ---
        row_m = _TABLE_ROW_RE.match(stripped)
        if row_m:
            cells = [c.strip() for c in stripped.strip("|").split("|")]

            # Try to detect a header row by looking for known column keywords
            lowered = [c.lower() for c in cells]
            if any(k in lowered for k in ("variable", "name", "key")):
                # This is the header row — record column positions
                table_header = lowered
                col_var = next((i for i, h in enumerate(lowered) if h in ("variable", "name", "key")), -1)
                col_req = next((i for i, h in enumerate(lowered) if "req" in h), -1)
                col_def = next((i for i, h in enumerate(lowered) if "default" in h or h == "def"), -1)
                continue

            # Data row — requires at least a variable column
            if table_header is not None and col_var >= 0 and col_var < len(cells):
                var_name = cells[col_var].strip("`").strip()
                if not var_name or not _ENV_VAR_NAME_RE.match(var_name):
                    continue
                var_name = var_name.upper()

                required_val = cells[col_req].strip() if col_req >= 0 and col_req < len(cells) else ""
                default_val  = cells[col_def].strip() if col_def >= 0 and col_def < len(cells) else ""

                is_required = required_val.lower() in ("yes", "true", "required", "1")
                has_default = bool(default_val) and default_val not in ("--", "-", "n/a", "none", "")

                if is_required and not has_default:
                    expected = "{} required".format(var_name)
                elif has_default:
                    expected = "{}={}".format(var_name, default_val)
                else:
                    expected = var_name

                claims.append(ExtractedClaim(
                    area="config_env",
                    claim=stripped,
                    expected=expected,
                    source="{}#L{}".format(source_path, lineno),
                ))
            continue

        # --- Prose / bullet line ---
        # Only process when the line contains something that looks like an
        # env variable name (backtick-quoted or UPPER_SNAKE_CASE).
        if not re.search(r"`[A-Z_][A-Z0-9_]*`|(?<![a-z])[A-Z_][A-Z0-9_]{2,}", stripped):
            continue

        expected = normalize_env_claim(stripped)
        if not expected:
            continue

        claims.append(ExtractedClaim(
            area="config_env",
            claim=stripped,
            expected=expected,
            source="{}#L{}".format(source_path, lineno),
        ))

    return claims


# ---------------------------------------------------------------------------
# Internal: API route extractor
# ---------------------------------------------------------------------------

# Matches a Markdown heading that contains a route: ### GET /health
_API_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")

# Table column header keywords for method and route
_API_METHOD_HEADERS = frozenset({"method", "http method", "verb"})
_API_ROUTE_HEADERS  = frozenset({"route", "path", "endpoint", "url"})


def _extract_api_routes(
    body_lines: list[tuple[int, str]],
    source_path: str,
) -> list[ExtractedClaim]:
    """Extract api_docs claims from a Markdown section body.

    Supports four source forms:

    1. Nested headings already preserved as body lines:
           ### GET /health   ->  GET /health
    2. Plain text route lines:
           GET /health       ->  GET /health
    3. Prose containing a route:
           GET /contracts returns docs  ->  GET /contracts
    4. Markdown tables with Method + Route columns:
           | GET | /contracts | ...  ->  GET /contracts

    Local deduplication via ``seen`` keyed on expected.
    """
    claims: list[ExtractedClaim] = []
    seen: set[str] = set()

    # Table parsing state
    table_header: list[str] | None = None
    col_method: int = -1
    col_route: int = -1

    for lineno, text in body_lines:
        stripped = text.strip()

        if not stripped:
            continue

        # --- Table separator row ---
        if _TABLE_SEP_RE.match(stripped):
            continue

        # --- Table row ---
        if _TABLE_ROW_RE.match(stripped):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            lowered = [c.lower() for c in cells]

            # Detect header row
            if any(h in _API_METHOD_HEADERS for h in lowered) or any(h in _API_ROUTE_HEADERS for h in lowered):
                table_header = lowered
                col_method = next((i for i, h in enumerate(lowered) if h in _API_METHOD_HEADERS), -1)
                col_route  = next((i for i, h in enumerate(lowered) if h in _API_ROUTE_HEADERS), -1)
                continue

            # Data row
            if table_header is not None and col_method >= 0 and col_route >= 0:
                if col_method < len(cells) and col_route < len(cells):
                    method = cells[col_method].upper().strip()
                    route  = cells[col_route].strip()
                    if method in _HTTP_METHODS and route.startswith("/"):
                        expected = "{} {}".format(method, route)
                        if expected not in seen:
                            seen.add(expected)
                            claims.append(ExtractedClaim(
                                area="api_docs",
                                claim=stripped,
                                expected=expected,
                                source="{}#L{}".format(source_path, lineno),
                            ))
            continue

        # --- Nested heading line (already in body as raw text) ---
        heading_m = _API_HEADING_RE.match(stripped)
        if heading_m:
            heading_text = heading_m.group(1).strip()
            expected = normalize_api_route(heading_text)
            if expected:
                if expected not in seen:
                    seen.add(expected)
                    claims.append(ExtractedClaim(
                        area="api_docs",
                        claim=stripped,
                        expected=expected,
                        source="{}#L{}".format(source_path, lineno),
                    ))
            continue

        # --- Plain text / prose line ---
        expected = normalize_api_route(stripped)
        if expected:
            if expected not in seen:
                seen.add(expected)
                claims.append(ExtractedClaim(
                    area="api_docs",
                    claim=stripped,
                    expected=expected,
                    source="{}#L{}".format(source_path, lineno),
                ))

    return claims


# ---------------------------------------------------------------------------
# Internal: markdown parser
# ---------------------------------------------------------------------------

def _parse_markdown_file(request: ParseRequest) -> List[ExtractedClaim]:
    """Parse a Markdown documentation file into ExtractedClaim objects."""
    source_path = _normalize_source_path(request.source_path)
    content = request.content.replace("\r\n", "\n")

    # Build (lineno, text) pairs -- 1-based
    lines: list[tuple[int, str]] = [
        (i, line) for i, line in enumerate(content.splitlines(), start=1)
    ]

    sections = _detect_sections(lines)

    claims: List[ExtractedClaim] = []
    command_seen: set[tuple[str, str]] = set()
    config_seen: set[tuple[str, str]] = set()
    api_seen: set[tuple[str, str]] = set()

    for section in sections:
        if section.area == "runtime_requirements":
            claims.extend(_extract_runtime(section.body_lines, source_path))

        elif section.area == "commands":
            command_claims = _extract_commands(section.body_lines, source_path)
            for claim in command_claims:
                key = (claim.area, claim.expected)
                if key not in command_seen:
                    command_seen.add(key)
                    claims.append(claim)

        elif section.area == "config_env":
            config_claims = _extract_config_from_markdown(section.body_lines, source_path)
            for claim in config_claims:
                key = (claim.area, claim.expected)
                if key not in config_seen:
                    config_seen.add(key)
                    claims.append(claim)

        elif section.area == "api_docs":
            api_claims = _extract_api_routes(section.body_lines, source_path)
            for claim in api_claims:
                key = (claim.area, claim.expected)
                if key not in api_seen:
                    api_seen.add(key)
                    claims.append(claim)

    return claims


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_documentation(request: ParseRequest) -> List[ExtractedClaim]:
    """Parse a documentation file into a list of ExtractedClaim objects."""
    if not request.content or not request.content.strip():
        return []

    file_type = _detect_file_type(request.source_path)

    if file_type == "env":
        return _parse_env_file(request)

    if file_type == "markdown":
        return _parse_markdown_file(request)

    return []
