from __future__ import annotations

import textwrap

import pytest

from app.verification.doc_parser import (
    ExtractedClaim,
    ParseRequest,
    normalize_api_route,
    normalize_command,
    normalize_env_claim,
    normalize_runtime,
    parse_documentation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse(source_path, content):
    """Convenience wrapper around parse_documentation."""
    return parse_documentation(ParseRequest(source_path=source_path, content=content))


def dedent(text):
    return textwrap.dedent(text).lstrip("\n")


# ---------------------------------------------------------------------------
# Suite 1 -- File-type dispatch
# ---------------------------------------------------------------------------

class TestFileTypeDispatch:

    def test_env_file_produces_config_env_claims_only(self):
        result = parse(".env.example", "PORT=3000\nDATABASE_URL=postgres://x")
        assert result, "expected at least one claim"
        assert all(c.area == "config_env" for c in result)

    def test_markdown_file_dispatches_to_markdown_parser(self):
        result = parse("README.md", "## Requirements\n- Node.js 18+\n")
        assert result != []

    def test_unknown_extension_returns_empty(self):
        result = parse("config.yaml", "node: 18")
        assert result == []

    def test_env_with_dot_prefix_extension(self):
        result = parse(".env", "KEY=value")
        assert result != []

    def test_docs_subfolder_path_dispatches_markdown(self):
        result = parse("docs/api.md", "## API\nGET /health\n")
        assert result != []

    def test_env_example_extension_dispatches_env(self):
        result = parse(".env.example", "SECRET=abc")
        assert len(result) == 1
        assert result[0].area == "config_env"


# ---------------------------------------------------------------------------
# Suite 2 -- Markdown section detection
# ---------------------------------------------------------------------------

class TestMarkdownSectionDetection:

    def test_requirements_heading_maps_to_runtime_area(self):
        result = parse("README.md", "## Requirements\n- Node.js 18+\n")
        assert any(c.area == "runtime_requirements" for c in result)

    def test_prerequisites_heading_maps_to_runtime_area(self):
        result = parse("README.md", "## Prerequisites\n- Python 3.11+\n")
        assert any(c.area == "runtime_requirements" for c in result)

    def test_usage_heading_maps_to_commands_area(self):
        result = parse("README.md", "## Usage\n```bash\nnpm run dev\n```\n")
        assert any(c.area == "commands" for c in result)

    def test_environment_variables_heading_maps_to_config_env(self):
        result = parse("README.md", "## Environment Variables\n- `PORT` -- default is 3000\n")
        assert any(c.area == "config_env" for c in result)

    def test_api_heading_maps_to_api_docs(self):
        result = parse("README.md", "## API\nGET /health\n")
        assert any(c.area == "api_docs" for c in result)

    def test_unrecognized_heading_produces_no_claims(self):
        result = parse("README.md", "## Contributing\nSee CONTRIBUTING.md.\n")
        assert result == []

    def test_multiple_recognized_sections_all_parsed(self):
        content = (
            "## Requirements\n"
            "- Node.js 18+\n"
            "\n"
            "## Usage\n"
            "```bash\n"
            "npm run dev\n"
            "```\n"
            "\n"
            "## Environment Variables\n"
            "- `PORT` -- default is 3000\n"
            "\n"
            "## API\n"
            "GET /health\n"
        )
        result = parse("README.md", content)
        areas = {c.area for c in result}
        assert "runtime_requirements" in areas
        assert "commands" in areas
        assert "config_env" in areas
        assert "api_docs" in areas

    def test_heading_inside_fenced_block_not_treated_as_section(self):
        content = "```bash\n## Not a heading\nnpm install\n```\n"
        result = parse("README.md", content)
        assert result == []

    def test_installation_heading_maps_to_commands_area(self):
        result = parse("README.md", "## Installation\n```bash\nnpm install\n```\n")
        assert any(c.area == "commands" and c.expected == "npm install" for c in result)

    def test_setup_heading_maps_to_commands_area(self):
        result = parse("README.md", "## Setup\n```bash\npnpm install\n```\n")
        assert any(c.area == "commands" for c in result)

    def test_development_heading_maps_to_commands_area(self):
        result = parse("README.md", "## Development\n```bash\nnpm run dev\n```\n")
        assert any(c.area == "commands" for c in result)

    def test_build_heading_maps_to_commands_area(self):
        result = parse("README.md", "## Build\n```bash\nnpm run build\n```\n")
        assert any(c.area == "commands" for c in result)

    def test_testing_heading_maps_to_commands_area(self):
        result = parse("README.md", "## Testing\n```bash\nnpm test\n```\n")
        assert any(c.area == "commands" for c in result)

    def test_api_subheading_remains_inside_api_section(self):
        content = "## API\n### GET /health\nReturns status.\n"
        result = parse("README.md", content)
        assert any(c.expected == "GET /health" and c.area == "api_docs" for c in result)

    def test_multiple_api_subheadings_all_extracted(self):
        content = (
            "## API\n"
            "### GET /health\n"
            "Returns status.\n"
            "### POST /widgets\n"
            "Creates widget.\n"
        )
        result = parse("README.md", content)
        expecteds = {c.expected for c in result if c.area == "api_docs"}
        assert "GET /health" in expecteds
        assert "POST /widgets" in expecteds

    def test_recognized_heading_closes_previous_area(self):
        content = (
            "## API\n"
            "GET /health\n"
            "\n"
            "## Environment Variables\n"
            "- `PORT` -- default is 3000\n"
        )
        result = parse("README.md", content)
        api_claims = [c for c in result if c.area == "api_docs"]
        env_claims = [c for c in result if c.area == "config_env"]
        assert any(c.expected == "GET /health" for c in api_claims)
        assert any("PORT" in c.expected for c in env_claims)

    def test_unrecognized_subheading_body_also_included(self):
        content = (
            "## API\n"
            "### Authentication\n"
            "POST /auth/login accepts credentials.\n"
        )
        result = parse("README.md", content)
        assert any(c.expected == "POST /auth/login" and c.area == "api_docs" for c in result)

    def test_nested_heading_line_number_preserved_in_source(self):
        content = "## API\n### GET /health\nReturns status.\n"
        result = parse("docs/api.md", content)
        api_claims = [c for c in result if c.expected == "GET /health"]
        assert api_claims, "expected a GET /health claim"
        assert api_claims[0].source == "docs/api.md#L2"


# ---------------------------------------------------------------------------
# Suite 3 -- Runtime requirement extraction
# ---------------------------------------------------------------------------

class TestRuntimeExtraction:

    def _runtime_claims(self, body):
        content = "## Requirements\n" + body + "\n"
        return [c for c in parse("README.md", content) if c.area == "runtime_requirements"]

    def test_extracts_nodejs_plus_notation(self):
        claims = self._runtime_claims("- Node.js 16+")
        assert any(c.expected == "Node.js >=16" for c in claims)

    def test_extracts_nodejs_gte_notation(self):
        claims = self._runtime_claims("- Node.js >= 20.0.0")
        assert any(c.expected == "Node.js >=20.0.0" for c in claims)

    def test_extracts_python_or_newer(self):
        claims = self._runtime_claims("- Python 3.11 or newer")
        assert any(c.expected == "Python >=3.11" for c in claims)

    def test_extracts_npm_or_higher(self):
        claims = self._runtime_claims("- npm 8 or higher")
        assert any(c.expected == "npm >=8" for c in claims)

    def test_extracts_runtime_from_fenced_code_block(self):
        claims = self._runtime_claims("```\nnode >= 18\n```")
        assert any(c.expected == "Node.js >=18" for c in claims)

    def test_node_alias_canonicalized(self):
        claims = self._runtime_claims("- node 18+")
        assert any(c.expected.startswith("Node.js") for c in claims)

    def test_runtime_without_version_included_with_bare_name(self):
        claims = self._runtime_claims("- Requires Docker")
        assert any(c.expected == "Docker" for c in claims)


# ---------------------------------------------------------------------------
# Suite 4 -- Command extraction
# ---------------------------------------------------------------------------

class TestCommandExtraction:

    def _cmd_claims(self, body):
        content = "## Usage\n" + body + "\n"
        return [c for c in parse("README.md", content) if c.area == "commands"]

    def test_extracts_npm_start_from_prose(self):
        claims = self._cmd_claims("Start the server using npm start.")
        assert any(c.expected == "npm start" for c in claims)

    def test_extracts_command_from_fenced_bash_block(self):
        claims = self._cmd_claims("```bash\nnpm run dev\n```")
        assert any(c.expected == "npm run dev" for c in claims)

    def test_extracts_inline_code_command(self):
        claims = self._cmd_claims("Run `npm run build` to build.")
        assert any(c.expected == "npm run build" for c in claims)

    def test_multiple_commands_in_one_section(self):
        claims = self._cmd_claims("```bash\nnpm install\nnpm run dev\n```")
        expecteds = {c.expected for c in claims}
        assert "npm install" in expecteds
        assert "npm run dev" in expecteds

    def test_pnpm_install_extracted(self):
        claims = self._cmd_claims("```\npnpm install\n```")
        assert any(c.expected == "pnpm install" for c in claims)

    def test_yarn_build_extracted(self):
        claims = self._cmd_claims("```\nyarn build\n```")
        assert any(c.expected == "yarn build" for c in claims)

    def test_prose_command_deduped_when_also_in_code_fence(self):
        claims = self._cmd_claims("Run `npm run dev`\n```bash\nnpm run dev\n```")
        dev_claims = [c for c in claims if c.expected == "npm run dev"]
        assert len(dev_claims) == 1, "expected exactly 1, got {}".format(len(dev_claims))


# ---------------------------------------------------------------------------
# Suite 5 -- Config/env extraction
# ---------------------------------------------------------------------------

class TestConfigEnvExtraction:

    def _env_claims(self, body):
        content = "## Environment Variables\n" + body + "\n"
        return [c for c in parse("README.md", content) if c.area == "config_env"]

    def test_env_file_key_value_preserved_in_expected(self):
        result = parse(".env.example", "PORT=3000")
        assert any(c.expected == "PORT=3000" for c in result)

    def test_env_file_debug_value_preserved(self):
        result = parse(".env.example", "DEBUG=false")
        assert any(c.expected == "DEBUG=false" for c in result)

    def test_env_file_database_url_preserved(self):
        result = parse(".env.example", "DATABASE_URL=postgres://localhost/db")
        assert any(c.expected == "DATABASE_URL=postgres://localhost/db" for c in result)

    def test_env_file_empty_value_preserved(self):
        result = parse(".env.example", "JWT_SECRET=")
        assert any(c.expected == "JWT_SECRET=" for c in result)

    def test_env_file_key_normalized_to_uppercase(self):
        result = parse(".env.example", "port=3000")
        assert any(c.expected == "PORT=3000" for c in result)

    def test_env_file_inline_comment_stripped_from_value(self):
        result = parse(".env.example", "PORT=3000 # server port")
        assert any(c.expected == "PORT=3000" for c in result)

    def test_env_file_skips_comment_lines(self):
        result = parse(".env.example", "# comment\nPORT=3000")
        assert len(result) == 1
        assert result[0].expected == "PORT=3000"

    def test_env_file_skips_blank_lines(self):
        result = parse(".env.example", "\n\nPORT=3000")
        assert len(result) == 1
        assert result[0].expected == "PORT=3000"

    def test_env_file_malformed_no_key_skipped(self):
        result = parse(".env.example", "=badvalue")
        assert result == []

    def test_env_file_multiple_vars_all_preserved(self):
        result = parse(".env.example", "A=1\nB=2\nC=3")
        expecteds = {c.expected for c in result}
        assert expecteds == {"A=1", "B=2", "C=3"}

    def test_env_file_only_comments_returns_empty(self):
        result = parse(".env.example", "# comment only")
        assert result == []

    def test_markdown_port_default_value_extracted(self):
        claims = self._env_claims("- `PORT` -- default is 8080")
        assert any(c.expected == "PORT=8080" for c in claims)

    def test_markdown_debug_defaults_to_false(self):
        claims = self._env_claims("- `DEBUG` defaults to false")
        assert any(c.expected == "DEBUG=false" for c in claims)

    def test_markdown_required_var_no_default(self):
        claims = self._env_claims("- `DATABASE_URL` is required. PostgreSQL connection string.")
        assert any(c.expected == "DATABASE_URL required" for c in claims)

    def test_markdown_required_var_dash_description(self):
        claims = self._env_claims("- `JWT_SECRET` -- required. Token signing key.")
        assert any(c.expected == "JWT_SECRET required" for c in claims)

    def test_markdown_optional_var_no_value(self):
        claims = self._env_claims("- `LOG_LEVEL` -- optional.")
        assert any(c.expected == "LOG_LEVEL" for c in claims)

    def test_markdown_table_required_yes_no_default(self):
        content = (
            "## Environment Variables\n"
            "| Variable | Required | Default | Description |\n"
            "|---|---|---|---|\n"
            "| DATABASE_URL | Yes | -- | PostgreSQL connection string |\n"
        )
        result = [c for c in parse("README.md", content) if c.area == "config_env"]
        assert any(c.expected == "DATABASE_URL required" for c in result)

    def test_markdown_table_optional_with_default(self):
        content = (
            "## Environment Variables\n"
            "| Variable | Required | Default | Description |\n"
            "|---|---|---|---|\n"
            "| PORT | No | 8080 | HTTP server port |\n"
        )
        result = [c for c in parse("README.md", content) if c.area == "config_env"]
        assert any(c.expected == "PORT=8080" for c in result)

    def test_markdown_table_optional_false_default(self):
        content = (
            "## Environment Variables\n"
            "| Variable | Required | Default | Description |\n"
            "|---|---|---|---|\n"
            "| DEBUG | No | false | Enable verbose logging |\n"
        )
        result = [c for c in parse("README.md", content) if c.area == "config_env"]
        assert any(c.expected == "DEBUG=false" for c in result)

    def test_env_claim_text_is_verbatim_prose(self):
        claims = self._env_claims("- `PORT` -- default is 8080")
        port_claims = [c for c in claims if "PORT" in c.expected]
        assert port_claims, "no PORT claim found"
        assert "`PORT`" in port_claims[0].claim

    def test_env_file_claim_text_is_raw_line(self):
        result = parse(".env.example", "PORT=3000")
        assert result[0].claim == "PORT=3000"


# ---------------------------------------------------------------------------
# Suite 6 -- API route extraction
# ---------------------------------------------------------------------------

class TestApiRouteExtraction:

    def _api_claims(self, body):
        content = "## API\n" + body + "\n"
        return [c for c in parse("README.md", content) if c.area == "api_docs"]

    def test_extracts_get_route_from_heading(self):
        content = "## API\n\n### GET /health\nReturns status.\n"
        result = [c for c in parse("README.md", content) if c.area == "api_docs"]
        assert any(c.expected == "GET /health" for c in result)

    def test_extracts_post_route_from_heading(self):
        content = "## API\n### POST /verify\nQueues verification.\n"
        result = [c for c in parse("README.md", content) if c.area == "api_docs"]
        assert any(c.expected == "POST /verify" for c in result)

    def test_extracts_route_from_markdown_table(self):
        body = (
            "| Method | Route | Description |\n"
            "|---|---|---|\n"
            "| GET | /contracts | List all contracts |\n"
        )
        claims = self._api_claims(body)
        assert any(c.expected == "GET /contracts" for c in claims)

    def test_extracts_route_with_path_param(self):
        claims = self._api_claims("POST /approve/{contract_id}")
        assert any(c.expected == "POST /approve/{contract_id}" for c in claims)

    def test_method_normalized_to_uppercase(self):
        claims = self._api_claims("get /health")
        assert any(c.expected == "GET /health" for c in claims)

    def test_invalid_method_not_extracted(self):
        claims = self._api_claims("FETCH /contracts")
        assert not any("FETCH" in c.expected for c in claims)

    def test_route_description_not_included_in_expected(self):
        claims = self._api_claims("GET /contracts returns documentation contracts")
        assert any(c.expected == "GET /contracts" for c in claims)
        assert not any(
            c.expected == "GET /contracts returns documentation contracts"
            for c in claims
        )

    def test_multiple_routes_in_table_produces_multiple_claims(self):
        body = (
            "| Method | Route | Description |\n"
            "|---|---|---|\n"
            "| GET | /health | Health check |\n"
            "| POST | /verify | Run verification |\n"
            "| DELETE | /contracts/{id} | Delete contract |\n"
        )
        claims = self._api_claims(body)
        assert len(claims) >= 3


# ---------------------------------------------------------------------------
# Suite 7 -- Source line numbers, edge cases, robustness
# ---------------------------------------------------------------------------

class TestSourceAndRobustness:

    def test_source_format_is_filename_hash_line(self):
        content = "## Requirements\n- Node.js 18+\n"
        result = parse("README.md", content)
        runtime_claims = [c for c in result if c.area == "runtime_requirements"]
        assert runtime_claims, "no runtime claim found"
        assert runtime_claims[0].source.startswith("README.md#L")
        lineno = int(runtime_claims[0].source.split("#L")[1])
        assert lineno >= 1

    def test_source_line_points_to_claim_not_heading(self):
        content = "## Requirements\n- Node.js 18+\n"
        result = parse("README.md", content)
        runtime_claims = [c for c in result if c.area == "runtime_requirements"]
        assert runtime_claims
        assert runtime_claims[0].source == "README.md#L2"

    def test_nested_path_preserved_in_source(self):
        content = "## API\nGET /health\n"
        result = parse("docs/api.md", content)
        api_claims = [c for c in result if c.area == "api_docs"]
        assert api_claims
        assert api_claims[0].source.startswith("docs/api.md#L")

    def test_empty_content_returns_empty_list(self):
        assert parse("README.md", "") == []

    def test_whitespace_only_content_returns_empty_list(self):
        assert parse("README.md", "   \n\n\t\n") == []

    def test_crlf_line_endings_normalized(self):
        lf_content   = "## Requirements\n- Node.js 18+\n"
        crlf_content = "## Requirements\r\n- Node.js 18+\r\n"
        lf_result    = parse("README.md", lf_content)
        crlf_result  = parse("README.md", crlf_content)
        assert len(lf_result) == len(crlf_result)
        assert lf_result[0].expected == crlf_result[0].expected

    def test_crlf_line_numbers_correct(self):
        crlf_content = "## Requirements\r\n- Node.js 18+\r\n"
        result = parse("README.md", crlf_content)
        runtime_claims = [c for c in result if c.area == "runtime_requirements"]
        assert runtime_claims
        assert runtime_claims[0].source == "README.md#L2"

    def test_duplicate_claims_deduplicated(self):
        content = "## Usage\n`npm run dev` is the dev command.\n```bash\nnpm run dev\n```\n"
        result = parse("README.md", content)
        dev_claims = [c for c in result if c.area == "commands" and c.expected == "npm run dev"]
        assert len(dev_claims) == 1

    def test_all_claims_have_extraction_method_regex(self):
        content = (
            "## Requirements\n"
            "- Node.js 18+\n"
            "\n"
            "## Usage\n"
            "```bash\n"
            "npm run dev\n"
            "```\n"
        )
        result = parse("README.md", content)
        assert result
        assert all(c.extraction_method == "regex" for c in result)

    def test_claim_text_is_not_empty_string(self):
        content = (
            "## Requirements\n"
            "- Node.js 18+\n"
            "\n"
            "## Usage\n"
            "```bash\n"
            "npm run dev\n"
            "```\n"
        )
        result = parse("README.md", content)
        assert result
        for c in result:
            assert c.claim.strip() != "", "empty claim: {}".format(c)
            assert c.expected.strip() != "", "empty expected: {}".format(c)

    def test_env_file_with_only_comments_returns_empty(self):
        result = parse(".env.example", "# comment only\n# another comment")
        assert result == []

    def test_windows_backslash_path_normalized_in_source(self):
        result = parse("docs\\api.md", "## API\nGET /health\n")
        api_claims = [c for c in result if c.area == "api_docs"]
        assert api_claims
        assert "\\" not in api_claims[0].source

    def test_env_file_no_equals_sign_skipped(self):
        result = parse(".env.example", "JUST_A_KEY_NO_EQUALS")
        assert result == []

    def test_env_file_first_equals_used_for_split(self):
        result = parse(".env.example", "DATABASE_URL=postgres://host/db?ssl=true")
        assert any(c.expected == "DATABASE_URL=postgres://host/db?ssl=true" for c in result)


# ---------------------------------------------------------------------------
# Normalization unit tests
# ---------------------------------------------------------------------------

class TestNormalizeRuntime:

    def test_nodejs_plus(self):
        assert normalize_runtime("Node.js 16+") == "Node.js >=16"

    def test_nodejs_gte_with_patch(self):
        assert normalize_runtime("Node.js >= 20.0.0") == "Node.js >=20.0.0"

    def test_python_or_newer(self):
        assert normalize_runtime("Python 3.11 or newer") == "Python >=3.11"

    def test_npm_or_higher(self):
        assert normalize_runtime("npm 8 or higher") == "npm >=8"

    def test_node_alias_resolved(self):
        assert normalize_runtime("node 18+").startswith("Node.js")

    def test_no_version_returns_bare_name(self):
        assert normalize_runtime("Requires Docker") == "Docker"

    def test_pnpm_version(self):
        assert normalize_runtime("pnpm 8 or newer") == "pnpm >=8"


class TestNormalizeCommand:

    def test_npm_start_bare(self):
        assert normalize_command("npm start") == "npm start"

    def test_npm_run_dev(self):
        assert normalize_command("npm run dev") == "npm run dev"

    def test_strips_leading_prose(self):
        assert normalize_command("Start the server using npm start") == "npm start"

    def test_inline_code_backticks_stripped(self):
        assert normalize_command("`npm run build`") == "npm run build"

    def test_pnpm_install(self):
        assert normalize_command("pnpm install") == "pnpm install"

    def test_yarn_build(self):
        assert normalize_command("yarn build") == "yarn build"


class TestNormalizeEnvClaim:

    def test_env_file_line_key_value(self):
        assert normalize_env_claim("PORT=3000") == "PORT=3000"

    def test_env_file_key_uppercased(self):
        assert normalize_env_claim("port=3000") == "PORT=3000"

    def test_inline_comment_stripped(self):
        assert normalize_env_claim("PORT=3000 # server port") == "PORT=3000"

    def test_empty_value(self):
        assert normalize_env_claim("JWT_SECRET=") == "JWT_SECRET="

    def test_malformed_no_key(self):
        assert normalize_env_claim("=badvalue") == ""

    def test_required_prose(self):
        assert normalize_env_claim("`DATABASE_URL` is required") == "DATABASE_URL required"

    def test_default_value_prose(self):
        assert normalize_env_claim("`PORT` default is 8080") == "PORT=8080"

    def test_defaults_to_prose(self):
        assert normalize_env_claim("`DEBUG` defaults to false") == "DEBUG=false"

    def test_optional_no_value(self):
        assert normalize_env_claim("`LOG_LEVEL` -- optional.") == "LOG_LEVEL"

    def test_first_equals_used_for_split(self):
        result = normalize_env_claim("DATABASE_URL=postgres://host/db?ssl=true")
        assert result == "DATABASE_URL=postgres://host/db?ssl=true"


class TestNormalizeApiRoute:

    def test_get_route(self):
        assert normalize_api_route("GET /contracts") == "GET /contracts"

    def test_post_route_lowercase(self):
        assert normalize_api_route("post /verify") == "POST /verify"

    def test_path_param_preserved(self):
        assert normalize_api_route("POST /approve/{contract_id}") == "POST /approve/{contract_id}"

    def test_invalid_method_returns_empty(self):
        assert normalize_api_route("FETCH /contracts") == ""

    def test_table_row_format(self):
        assert normalize_api_route("| POST | /widgets |") == "POST /widgets"

    def test_description_stripped(self):
        result = normalize_api_route("GET /contracts returns all contracts")
        assert result == "GET /contracts"

    def test_delete_method_valid(self):
        assert normalize_api_route("DELETE /widgets/{id}") == "DELETE /widgets/{id}"
