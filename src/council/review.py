"""Structured review output parsing and summary generation.

Parses Markdown or JSON critique output from the LLM rounds into a
structured ``ReviewResult`` object with typed fields:

- ``must_fix``: Critical issues that must be addressed
- ``should_fix``: Non-critical improvements
- ``tests``: Missing or suggested test cases
- ``patch_suggestions``: Diff snippets or code suggestions
- ``confidence``: 0–100 score
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ReviewItem:
    """A single review finding."""

    description: str
    file: str | None = None
    line: int | None = None


@dataclass
class ReviewResult:
    """Structured output from a critique / review round."""

    must_fix: list[ReviewItem] = field(default_factory=list)
    should_fix: list[ReviewItem] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    patch_suggestions: list[str] = field(default_factory=list)
    confidence: int | None = None
    raw_text: str = ""

    @property
    def is_clean(self) -> bool:
        """True when there are no must-fix or should-fix issues."""
        return not self.must_fix and not self.should_fix

    @property
    def high_confidence(self) -> bool:
        """True when confidence >= 85 and there are no must-fix issues."""
        return not self.must_fix and self.confidence is not None and self.confidence >= 85

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        d: dict = {
            "must_fix": [asdict(i) for i in self.must_fix],
            "should_fix": [asdict(i) for i in self.should_fix],
            "tests": self.tests,
            "patch_suggestions": self.patch_suggestions,
            "confidence": self.confidence,
        }
        return d


# ---------------------------------------------------------------------------
# JSON output prompt suffix — injected into critique rounds.
# ---------------------------------------------------------------------------

JSON_CRITIQUE_SUFFIX = """\

IMPORTANT: After your analysis, output a JSON block fenced with ```json ... ```
containing EXACTLY this structure:
```json
{
  "must_fix": [
    {"description": "...", "file": "path/to/file.py", "line": 42}
  ],
  "should_fix": [
    {"description": "...", "file": "path/to/file.py", "line": null}
  ],
  "tests": [
    "pytest tests/test_auth.py -k test_login_failure"
  ],
  "patch_suggestions": [
    "--- a/src/auth.py\\n+++ b/src/auth.py\\n@@ -10,3 +10,3 @@\\n-old\\n+new"
  ],
  "confidence": 75
}
```

Rules for the JSON:
- must_fix: critical issues that MUST be fixed before merge
- should_fix: non-critical improvements worth considering
- tests: exact test commands to run
- patch_suggestions: unified diff snippets for suggested fixes
- confidence: 0-100 (0 = fundamentally broken, 100 = production-ready)
- "file" and "line" are optional (use null if unknown)
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Extract fenced JSON blocks.
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# Markdown-based confidence score: "Confidence Score" or "confidence:" etc.
_CONFIDENCE_RE = re.compile(
    r"(?:confidence\s*(?:score)?)\s*[:=]?\s*(\d{1,3})",
    re.IGNORECASE,
)

# Markdown section headers for structured parsing.
_MUST_FIX_HEADER = re.compile(r"^#{1,4}\s*Must[- ]Fix", re.IGNORECASE | re.MULTILINE)
_SHOULD_FIX_HEADER = re.compile(r"^#{1,4}\s*Should[- ]Fix", re.IGNORECASE | re.MULTILINE)
_MISSING_TESTS_HEADER = re.compile(r"^#{1,4}\s*(?:Missing\s+)?Tests", re.IGNORECASE | re.MULTILINE)
_SUGGESTIONS_HEADER = re.compile(r"^#{1,4}\s*(?:Suggested\s+)?Corrections", re.IGNORECASE | re.MULTILINE)


def parse_review(text: str) -> ReviewResult:
    """Parse LLM critique output into a ``ReviewResult``.

    Tries JSON first (from fenced blocks), then falls back to
    Markdown section parsing.
    """
    result = ReviewResult(raw_text=text)

    # --- Try JSON extraction first ---
    json_result = _try_parse_json(text)
    if json_result is not None:
        return json_result

    # --- Fallback: Markdown parsing ---
    _parse_markdown_sections(text, result)
    return result


def _try_parse_json(text: str) -> ReviewResult | None:
    """Extract and parse a JSON block from the text."""
    for m in _JSON_BLOCK_RE.finditer(text):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if not isinstance(data, dict):
            continue

        result = ReviewResult(raw_text=text)

        # must_fix
        for item in data.get("must_fix", []):
            if isinstance(item, dict):
                result.must_fix.append(
                    ReviewItem(
                        description=str(item.get("description", "")),
                        file=item.get("file"),
                        line=item.get("line"),
                    )
                )
            elif isinstance(item, str):
                result.must_fix.append(ReviewItem(description=item))

        # should_fix
        for item in data.get("should_fix", []):
            if isinstance(item, dict):
                result.should_fix.append(
                    ReviewItem(
                        description=str(item.get("description", "")),
                        file=item.get("file"),
                        line=item.get("line"),
                    )
                )
            elif isinstance(item, str):
                result.should_fix.append(ReviewItem(description=item))

        # tests
        for t in data.get("tests", []):
            if isinstance(t, str):
                result.tests.append(t)

        # patch_suggestions
        for p in data.get("patch_suggestions", []):
            if isinstance(p, str):
                result.patch_suggestions.append(p)

        # confidence
        conf = data.get("confidence")
        if isinstance(conf, (int, float)):
            result.confidence = max(0, min(100, int(conf)))

        return result

    return None


def _parse_markdown_sections(text: str, result: ReviewResult) -> None:
    """Parse Markdown-formatted critique into ReviewResult fields."""
    # Extract confidence from anywhere in the text.
    conf_match = _CONFIDENCE_RE.search(text)
    if conf_match:
        val = int(conf_match.group(1))
        result.confidence = max(0, min(100, val))

    # Extract bullet items from known sections.
    result.must_fix = _extract_section_items(text, _MUST_FIX_HEADER)
    result.should_fix = _extract_section_items(text, _SHOULD_FIX_HEADER)

    # Tests section — treat as plain strings.
    test_items = _extract_section_items(text, _MISSING_TESTS_HEADER)
    result.tests = [item.description for item in test_items]

    # Patch suggestions — extract diff blocks from the suggestions section.
    sugg_start = _SUGGESTIONS_HEADER.search(text)
    if sugg_start:
        section_text = _extract_until_next_header(text, sugg_start.end())
        # Find fenced diff blocks.
        for m in re.finditer(r"```(?:diff)?\s*\n(.*?)```", section_text, re.DOTALL):
            result.patch_suggestions.append(m.group(1).strip())
        # If no fenced blocks, include the whole section.
        if not result.patch_suggestions and section_text.strip():
            items = _extract_section_items(text, _SUGGESTIONS_HEADER)
            result.patch_suggestions = [item.description for item in items]


def _extract_section_items(text: str, header_re: re.Pattern[str]) -> list[ReviewItem]:
    """Extract bullet-point items from a Markdown section."""
    match = header_re.search(text)
    if not match:
        return []

    section_text = _extract_until_next_header(text, match.end())
    items: list[ReviewItem] = []

    for line in section_text.splitlines():
        stripped = line.strip()
        # Match bullet points: -, *, 1., 1)
        bullet_match = re.match(r"^[-*]\s+(.+)$|^\d+[.)]\s+(.+)$", stripped)
        if bullet_match:
            desc = bullet_match.group(1) or bullet_match.group(2)
            # Try to extract file:line from the description.
            file_ref = re.search(r"`?([^\s`]+\.\w+):(\d+)`?", desc)
            if file_ref:
                items.append(
                    ReviewItem(
                        description=desc,
                        file=file_ref.group(1),
                        line=int(file_ref.group(2)),
                    )
                )
            else:
                items.append(ReviewItem(description=desc))

    return items


def _extract_until_next_header(text: str, start: int) -> str:
    """Extract text from start until the next Markdown header or end of text."""
    next_header = re.search(r"^#{1,4}\s+\S", text[start:], re.MULTILINE)
    if next_header:
        return text[start : start + next_header.start()]
    return text[start:]


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


def format_review_summary(review: ReviewResult) -> str:
    """Format a ReviewResult into a human-readable summary."""
    lines: list[str] = []

    # Header with confidence.
    if review.confidence is not None:
        emoji_bar = _confidence_bar(review.confidence)
        lines.append(f"Confidence: {review.confidence}/100 {emoji_bar}")
    lines.append("")

    # Must-fix.
    if review.must_fix:
        lines.append(f"MUST FIX ({len(review.must_fix)}):")
        for i, item in enumerate(review.must_fix, 1):
            loc = f" [{item.file}:{item.line}]" if item.file else ""
            lines.append(f"  {i}. {item.description}{loc}")
        lines.append("")
    else:
        lines.append("MUST FIX: (none)")
        lines.append("")

    # Should-fix.
    if review.should_fix:
        lines.append(f"SHOULD FIX ({len(review.should_fix)}):")
        for i, item in enumerate(review.should_fix, 1):
            loc = f" [{item.file}:{item.line}]" if item.file else ""
            lines.append(f"  {i}. {item.description}{loc}")
        lines.append("")

    # Tests.
    if review.tests:
        lines.append(f"TESTS ({len(review.tests)}):")
        for t in review.tests:
            lines.append(f"  - {t}")
        lines.append("")

    # Patches.
    if review.patch_suggestions:
        lines.append(f"PATCH SUGGESTIONS ({len(review.patch_suggestions)}):")
        lines.append("")

    # Verdict.
    if review.high_confidence:
        lines.append("VERDICT: HIGH CONFIDENCE — ready to merge.")
    elif review.must_fix:
        lines.append(f"VERDICT: {len(review.must_fix)} must-fix issue(s) — needs revision.")
    elif review.confidence is not None and review.confidence < 60:
        lines.append("VERDICT: LOW CONFIDENCE — needs more review.")
    else:
        lines.append("VERDICT: Review complete.")

    return "\n".join(lines)


def _confidence_bar(score: int) -> str:
    """Build a simple text-based confidence bar."""
    filled = score // 10
    empty = 10 - filled
    return "[" + "#" * filled + "-" * empty + "]"


def save_review_checklist(review: ReviewResult, path: Path) -> None:
    """Write a Markdown checklist file from a ReviewResult."""
    lines: list[str] = ["# Review Checklist", ""]

    if review.confidence is not None:
        lines.append(f"**Confidence:** {review.confidence}/100")
        lines.append("")

    if review.must_fix:
        lines.append("## Must Fix")
        for item in review.must_fix:
            loc = f" (`{item.file}:{item.line}`)" if item.file else ""
            lines.append(f"- [ ] {item.description}{loc}")
        lines.append("")

    if review.should_fix:
        lines.append("## Should Fix")
        for item in review.should_fix:
            loc = f" (`{item.file}:{item.line}`)" if item.file else ""
            lines.append(f"- [ ] {item.description}{loc}")
        lines.append("")

    if review.tests:
        lines.append("## Tests to Run")
        for t in review.tests:
            lines.append(f"- [ ] `{t}`")
        lines.append("")

    if review.patch_suggestions:
        lines.append("## Suggested Patches")
        for i, p in enumerate(review.patch_suggestions, 1):
            lines.append(f"### Patch {i}")
            lines.append(f"```diff\n{p}\n```")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
