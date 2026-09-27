from __future__ import annotations

from pathlib import Path

import pytest

from app.core.models import FixSuggestion
from app.fixes.approval_flow import (
    FixApplicationError,
    apply_fix,
    rollback_fix,
)


def make_fix(
    *,
    target_file: str = "README.md",
    diff: str,
) -> FixSuggestion:
    return FixSuggestion(
        contract_id="TEST-001",
        area="runtime_requirements",
        patch_type="doc_edit",
        target_file=target_file,
        description="Test fix",
        diff=diff,
        raw_fix="Test replacement",
    )


def test_apply_fix_replaces_exact_text(tmp_path: Path):
    readme = tmp_path / "README.md"

    readme.write_text(
        "Requires Node.js 18+\n",
        encoding="utf-8",
    )

    fix = make_fix(
        diff="""--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-Requires Node.js 18+
+Requires Node.js 20+"""
    )

    result = apply_fix(
        tmp_path,
        fix,
    )

    assert result.changed is True

    assert readme.read_text(
        encoding="utf-8"
    ) == "Requires Node.js 20+\n"


def test_apply_fix_can_insert_text(tmp_path: Path):
    env_file = tmp_path / ".env.example"

    env_file.write_text(
        "DATABASE_URL=example\n",
        encoding="utf-8",
    )

    fix = make_fix(
        target_file=".env.example",
        diff="""--- a/.env.example
+++ b/.env.example
@@ -1,0 +1,1 @@
+JWT_SECRET=<your-value-here>""",
    )

    result = apply_fix(
        tmp_path,
        fix,
    )

    assert result.changed is True

    content = env_file.read_text(
        encoding="utf-8"
    )

    assert "DATABASE_URL=example" in content
    assert "JWT_SECRET=<your-value-here>" in content


def test_apply_fix_can_delete_text(tmp_path: Path):
    api_file = tmp_path / "api.md"

    api_file.write_text(
        "GET /contracts\nDELETE /contracts/{id}\n",
        encoding="utf-8",
    )

    fix = make_fix(
        target_file="api.md",
        diff="""--- a/api.md
+++ b/api.md
@@ -1,1 +1,0 @@
-DELETE /contracts/{id}""",
    )

    result = apply_fix(
        tmp_path,
        fix,
    )

    assert result.changed is True

    content = api_file.read_text(
        encoding="utf-8"
    )

    assert "DELETE /contracts/{id}" not in content


def test_apply_fix_rejects_path_traversal(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"

    outside.write_text(
        "secret",
        encoding="utf-8",
    )

    fix = make_fix(
        target_file="../outside.txt",
        diff="""--- a/outside.txt
+++ b/outside.txt
@@ -1,1 +1,1 @@
-secret
+changed""",
    )

    with pytest.raises(
        FixApplicationError,
        match="escapes",
    ):
        apply_fix(
            tmp_path,
            fix,
        )

    assert outside.read_text(
        encoding="utf-8"
    ) == "secret"


def test_apply_fix_rejects_missing_original_text(
    tmp_path: Path,
):
    readme = tmp_path / "README.md"

    readme.write_text(
        "Something different\n",
        encoding="utf-8",
    )

    fix = make_fix(
        diff="""--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-Requires Node.js 18+
+Requires Node.js 20+"""
    )

    with pytest.raises(
        FixApplicationError,
        match="not found",
    ):
        apply_fix(
            tmp_path,
            fix,
        )


def test_apply_fix_rejects_ambiguous_replacement(
    tmp_path: Path,
):
    readme = tmp_path / "README.md"

    readme.write_text(
        "npm start\nnpm start\n",
        encoding="utf-8",
    )

    fix = make_fix(
        diff="""--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-npm start
+npm run dev"""
    )

    with pytest.raises(
        FixApplicationError,
        match="more than once",
    ):
        apply_fix(
            tmp_path,
            fix,
        )


def test_rollback_restores_original_content(
    tmp_path: Path,
):
    readme = tmp_path / "README.md"

    original = "Requires Node.js 18+\n"

    readme.write_text(
        original,
        encoding="utf-8",
    )

    fix = make_fix(
        diff="""--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-Requires Node.js 18+
+Requires Node.js 20+"""
    )

    result = apply_fix(
        tmp_path,
        fix,
    )

    assert "20+" in readme.read_text(
        encoding="utf-8"
    )

    rollback_fix(result)

    assert readme.read_text(
        encoding="utf-8"
    ) == original
