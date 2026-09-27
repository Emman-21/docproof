"""Safe application of human-approved DocProof fixes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.models import FixSuggestion


class FixApplicationError(Exception):
    """Raised when a fix cannot be applied safely."""


@dataclass(frozen=True)
class AppliedFix:
    """Result of applying one fix to a repository file."""

    target_path: Path
    original_content: str
    updated_content: str
    changed: bool


def _resolve_target(
    repository_path: str | Path,
    target_file: str,
) -> Path:
    """Resolve a fix target while preventing repository escape."""

    root = Path(repository_path).resolve()

    if not root.is_dir():
        raise FixApplicationError(
            f"Repository does not exist: {root}"
        )

    relative_target = Path(target_file)

    if relative_target.is_absolute():
        raise FixApplicationError(
            "Absolute target paths are not allowed."
        )

    target = (root / relative_target).resolve()

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise FixApplicationError(
            "Fix target escapes the repository root."
        ) from exc

    if not target.exists():
        raise FixApplicationError(
            f"Target file does not exist: {target_file}"
        )

    if not target.is_file():
        raise FixApplicationError(
            f"Fix target is not a file: {target_file}"
        )

    return target


def _parse_diff(
    diff: str,
) -> tuple[list[str], list[str]]:
    """Extract removed and added lines from a DocProof fix diff."""

    removed: list[str] = []
    added: list[str] = []

    for line in diff.splitlines():
        if line.startswith("--- "):
            continue

        if line.startswith("+++ "):
            continue

        if line.startswith("@@"):
            continue

        if line.startswith("-"):
            removed.append(line[1:])
            continue

        if line.startswith("+"):
            added.append(line[1:])

    if not removed and not added:
        raise FixApplicationError(
            "Fix does not contain any file changes."
        )

    return removed, added


def _apply_content_change(
    content: str,
    removed: list[str],
    added: list[str],
) -> tuple[str, bool]:
    """Apply one deterministic patch to text content."""

    old_block = "\n".join(removed)
    new_block = "\n".join(added)

    # Replacement or deletion.
    if removed:
        occurrences = content.count(old_block)

        if occurrences == 1:
            updated = content.replace(
                old_block,
                new_block,
                1,
            )

            return updated, updated != content

        # Idempotent case:
        # old text is already gone and replacement is present.
        if (
            occurrences == 0
            and added
            and new_block in content
        ):
            return content, False

        if occurrences == 0:
            raise FixApplicationError(
                "The expected original text was not found. "
                "The file may have changed since verification."
            )

        raise FixApplicationError(
            "The expected original text occurs more than once. "
            "Refusing to apply an ambiguous fix."
        )

    # Pure insertion.
    if new_block in content:
        return content, False

    if content and not content.endswith("\n"):
        updated = content + "\n" + new_block + "\n"
    else:
        updated = content + new_block + "\n"

    return updated, True


def apply_fix(
    repository_path: str | Path,
    fix: FixSuggestion,
) -> AppliedFix:
    """Apply exactly one approved FixSuggestion to a repository file."""

    target = _resolve_target(
        repository_path,
        fix.target_file,
    )

    removed, added = _parse_diff(
        fix.diff
    )

    original_content = target.read_text(
        encoding="utf-8"
    )

    updated_content, changed = _apply_content_change(
        original_content,
        removed,
        added,
    )

    if changed:
        target.write_text(
            updated_content,
            encoding="utf-8",
        )

    return AppliedFix(
        target_path=target,
        original_content=original_content,
        updated_content=updated_content,
        changed=changed,
    )


def rollback_fix(
    applied_fix: AppliedFix,
) -> None:
    """Restore a file to its exact pre-fix contents."""

    applied_fix.target_path.write_text(
        applied_fix.original_content,
        encoding="utf-8",
    )
