"""Extract unified diff blocks from LLM output text."""

from __future__ import annotations

import re

# Pattern to match fenced diff blocks: ```diff ... ```
_FENCED_DIFF = re.compile(
    r"```diff\s*\n(.*?)```",
    re.DOTALL,
)

# Pattern to match raw unified diff headers (--- a/... +++ b/...).
_RAW_DIFF_HEADER = re.compile(
    r"^(---\s+\S+.*\n\+\+\+\s+\S+.*\n(?:@@\s.*@@.*\n(?:[ +\-].*\n?)*)*)$",
    re.MULTILINE,
)


def extract_diffs(text: str) -> list[str]:
    """Extract all unified diff blocks from text.

    Looks for:
    1. Fenced code blocks with ```diff ... ```
    2. Raw unified diff blocks (--- a/file ... +++ b/file ... @@ ... @@)

    Returns a list of diff strings, deduplicated.
    """
    diffs: list[str] = []
    seen: set[str] = set()

    # First: fenced diff blocks.
    for match in _FENCED_DIFF.finditer(text):
        diff = match.group(1).strip()
        if diff and diff not in seen:
            diffs.append(diff)
            seen.add(diff)

    # Second: raw diff blocks (not inside fences).
    # Remove fenced blocks first to avoid duplicates.
    defenced = _FENCED_DIFF.sub("", text)
    for match in _RAW_DIFF_HEADER.finditer(defenced):
        diff = match.group(0).strip()
        if diff and diff not in seen:
            diffs.append(diff)
            seen.add(diff)

    return diffs


def combine_diffs(diffs: list[str]) -> str:
    """Combine multiple diff blocks into a single patch string."""
    if not diffs:
        return ""
    return "\n\n".join(diffs) + "\n"


def extract_and_save(text: str, output_path: str | None = None) -> str | None:
    """Extract diffs from text, optionally save to file.

    Returns the combined patch string, or None if no diffs found.
    """
    diffs = extract_diffs(text)
    if not diffs:
        return None

    combined = combine_diffs(diffs)

    if output_path:
        from pathlib import Path
        Path(output_path).write_text(combined, encoding="utf-8")

    return combined
