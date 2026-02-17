"""Tests for structured review output parsing and summary generation."""

from __future__ import annotations

import json
from pathlib import Path

from council.review import (
    JSON_CRITIQUE_SUFFIX,
    ReviewItem,
    ReviewResult,
    format_review_summary,
    parse_review,
    save_review_checklist,
)

# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseReviewJSON:
    def test_parses_full_json_block(self):
        text = """Some markdown preamble.

```json
{
  "must_fix": [
    {"description": "SQL injection in login query", "file": "src/auth.py", "line": 42}
  ],
  "should_fix": [
    {"description": "Add input validation", "file": "src/api.py", "line": null}
  ],
  "tests": [
    "pytest tests/test_auth.py -k test_sql_injection"
  ],
  "patch_suggestions": [
    "--- a/src/auth.py\\n+++ b/src/auth.py\\n@@ -42 +42 @@\\n-old\\n+new"
  ],
  "confidence": 72
}
```

More text after.
"""
        result = parse_review(text)
        assert len(result.must_fix) == 1
        assert result.must_fix[0].description == "SQL injection in login query"
        assert result.must_fix[0].file == "src/auth.py"
        assert result.must_fix[0].line == 42
        assert len(result.should_fix) == 1
        assert result.should_fix[0].file == "src/api.py"
        assert result.should_fix[0].line is None
        assert len(result.tests) == 1
        assert "test_sql_injection" in result.tests[0]
        assert len(result.patch_suggestions) == 1
        assert result.confidence == 72

    def test_parses_minimal_json(self):
        text = (
            '```json\n{"must_fix": [], "should_fix": [], "tests": [], "patch_suggestions": [], "confidence": 95}\n```'
        )
        result = parse_review(text)
        assert result.must_fix == []
        assert result.should_fix == []
        assert result.confidence == 95

    def test_clamps_confidence_to_100(self):
        text = (
            '```json\n{"must_fix": [], "should_fix": [], "tests": [], "patch_suggestions": [], "confidence": 150}\n```'
        )
        result = parse_review(text)
        assert result.confidence == 100

    def test_clamps_confidence_to_0(self):
        text = (
            '```json\n{"must_fix": [], "should_fix": [], "tests": [], "patch_suggestions": [], "confidence": -10}\n```'
        )
        result = parse_review(text)
        assert result.confidence == 0

    def test_handles_string_items(self):
        text = '```json\n{"must_fix": ["Fix the bug"], "should_fix": ["Clean up"], "tests": [], "patch_suggestions": [], "confidence": 50}\n```'
        result = parse_review(text)
        assert len(result.must_fix) == 1
        assert result.must_fix[0].description == "Fix the bug"

    def test_ignores_invalid_json(self):
        text = "```json\n{invalid json}\n```\n\n### Must-Fix Issues\n- Real issue here\n\n### Confidence Score\n80"
        result = parse_review(text)
        # Should fallback to markdown parsing.
        assert len(result.must_fix) == 1
        assert result.confidence == 80

    def test_preserves_raw_text(self):
        text = (
            '```json\n{"must_fix": [], "should_fix": [], "tests": [], "patch_suggestions": [], "confidence": 90}\n```'
        )
        result = parse_review(text)
        assert result.raw_text == text

    def test_multiple_must_fix(self):
        text = """```json
{
  "must_fix": [
    {"description": "Issue 1", "file": "a.py", "line": 1},
    {"description": "Issue 2", "file": "b.py", "line": 2},
    {"description": "Issue 3"}
  ],
  "should_fix": [],
  "tests": [],
  "patch_suggestions": [],
  "confidence": 30
}
```"""
        result = parse_review(text)
        assert len(result.must_fix) == 3
        assert result.must_fix[2].file is None
        assert result.must_fix[2].line is None


# ---------------------------------------------------------------------------
# Markdown fallback parsing
# ---------------------------------------------------------------------------


class TestParseReviewMarkdown:
    def test_parses_markdown_sections(self):
        text = """### Must-Fix Issues
- SQL injection in `src/auth.py:42`
- Missing null check in handler

### Should-Fix Issues
- Add type hints to `utils.py:10`

### Missing Tests
- Test login with empty password
- Test rate limiting

### Suggested Corrections
```diff
--- a/src/auth.py
+++ b/src/auth.py
@@ -42 +42 @@
-old
+new
```

### Confidence Score
75
"""
        result = parse_review(text)
        assert len(result.must_fix) == 2
        assert result.must_fix[0].file == "src/auth.py"
        assert result.must_fix[0].line == 42
        assert len(result.should_fix) == 1
        assert len(result.tests) == 2
        assert len(result.patch_suggestions) == 1
        assert result.confidence == 75

    def test_confidence_with_colon(self):
        text = "### Confidence Score\nConfidence: 88"
        result = parse_review(text)
        assert result.confidence == 88

    def test_confidence_with_equals(self):
        text = "confidence = 65"
        result = parse_review(text)
        assert result.confidence == 65

    def test_no_sections(self):
        text = "Just some plain feedback without any structure."
        result = parse_review(text)
        assert result.must_fix == []
        assert result.should_fix == []
        assert result.confidence is None

    def test_empty_sections(self):
        text = """### Must-Fix Issues

### Should-Fix Issues

### Confidence Score
42
"""
        result = parse_review(text)
        assert result.must_fix == []
        assert result.should_fix == []
        assert result.confidence == 42

    def test_numbered_list_items(self):
        text = """### Must-Fix Issues
1. First issue
2. Second issue
3) Third issue
"""
        result = parse_review(text)
        assert len(result.must_fix) == 3

    def test_bullet_star_items(self):
        text = """### Should-Fix Issues
* Star item one
* Star item two
"""
        result = parse_review(text)
        assert len(result.should_fix) == 2


# ---------------------------------------------------------------------------
# ReviewResult properties
# ---------------------------------------------------------------------------


class TestReviewResultProperties:
    def test_is_clean_no_issues(self):
        r = ReviewResult()
        assert r.is_clean is True

    def test_is_clean_with_must_fix(self):
        r = ReviewResult(must_fix=[ReviewItem(description="bug")])
        assert r.is_clean is False

    def test_is_clean_with_should_fix(self):
        r = ReviewResult(should_fix=[ReviewItem(description="improvement")])
        assert r.is_clean is False

    def test_high_confidence_true(self):
        r = ReviewResult(confidence=90)
        assert r.high_confidence is True

    def test_high_confidence_exactly_85(self):
        r = ReviewResult(confidence=85)
        assert r.high_confidence is True

    def test_high_confidence_false_low_score(self):
        r = ReviewResult(confidence=50)
        assert r.high_confidence is False

    def test_high_confidence_false_with_must_fix(self):
        r = ReviewResult(confidence=95, must_fix=[ReviewItem(description="bug")])
        assert r.high_confidence is False

    def test_high_confidence_false_no_score(self):
        r = ReviewResult()
        assert r.high_confidence is False


# ---------------------------------------------------------------------------
# to_dict / serialization
# ---------------------------------------------------------------------------


class TestReviewResultSerialization:
    def test_to_dict_roundtrip(self):
        r = ReviewResult(
            must_fix=[ReviewItem(description="bug", file="a.py", line=10)],
            should_fix=[ReviewItem(description="style")],
            tests=["pytest tests/"],
            patch_suggestions=["--- a/f\n+++ b/f"],
            confidence=80,
        )
        d = r.to_dict()
        assert d["confidence"] == 80
        assert len(d["must_fix"]) == 1
        assert d["must_fix"][0]["file"] == "a.py"
        # JSON-safe.
        json_str = json.dumps(d)
        assert "bug" in json_str

    def test_to_dict_empty(self):
        r = ReviewResult()
        d = r.to_dict()
        assert d["must_fix"] == []
        assert d["confidence"] is None


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


class TestFormatReviewSummary:
    def test_includes_confidence_bar(self):
        r = ReviewResult(confidence=80)
        summary = format_review_summary(r)
        assert "80/100" in summary
        assert "[########--]" in summary

    def test_must_fix_count(self):
        r = ReviewResult(
            must_fix=[ReviewItem(description="a"), ReviewItem(description="b")],
            confidence=40,
        )
        summary = format_review_summary(r)
        assert "MUST FIX (2)" in summary
        assert "needs revision" in summary.lower()

    def test_high_confidence_verdict(self):
        r = ReviewResult(confidence=95)
        summary = format_review_summary(r)
        assert "HIGH CONFIDENCE" in summary
        assert "ready to merge" in summary.lower()

    def test_low_confidence_verdict(self):
        r = ReviewResult(confidence=30)
        summary = format_review_summary(r)
        assert "LOW CONFIDENCE" in summary

    def test_no_confidence(self):
        r = ReviewResult()
        summary = format_review_summary(r)
        assert "VERDICT:" in summary

    def test_includes_file_locations(self):
        r = ReviewResult(
            must_fix=[ReviewItem(description="bug", file="src/x.py", line=5)],
            confidence=60,
        )
        summary = format_review_summary(r)
        assert "[src/x.py:5]" in summary


# ---------------------------------------------------------------------------
# Checklist file generation
# ---------------------------------------------------------------------------


class TestSaveReviewChecklist:
    def test_creates_checklist_file(self, tmp_path: Path):
        r = ReviewResult(
            must_fix=[ReviewItem(description="Fix SQL injection", file="auth.py", line=42)],
            should_fix=[ReviewItem(description="Add logging")],
            tests=["pytest tests/test_auth.py"],
            patch_suggestions=["--- a/f\n+++ b/f"],
            confidence=72,
        )
        out = tmp_path / "checklist.md"
        save_review_checklist(r, out)

        content = out.read_text()
        assert "# Review Checklist" in content
        assert "72/100" in content
        assert "## Must Fix" in content
        assert "- [ ] Fix SQL injection" in content
        assert "`auth.py:42`" in content
        assert "## Should Fix" in content
        assert "## Tests to Run" in content
        assert "`pytest tests/test_auth.py`" in content
        assert "## Suggested Patches" in content

    def test_empty_review(self, tmp_path: Path):
        r = ReviewResult()
        out = tmp_path / "checklist.md"
        save_review_checklist(r, out)
        content = out.read_text()
        assert "# Review Checklist" in content


# ---------------------------------------------------------------------------
# JSON_CRITIQUE_SUFFIX
# ---------------------------------------------------------------------------


class TestJSONCritiqueSuffix:
    def test_suffix_contains_json_instructions(self):
        assert "```json" in JSON_CRITIQUE_SUFFIX
        assert "must_fix" in JSON_CRITIQUE_SUFFIX
        assert "should_fix" in JSON_CRITIQUE_SUFFIX
        assert "confidence" in JSON_CRITIQUE_SUFFIX

    def test_suffix_appended_to_round2_prompt(self):
        from council.prompts import round2_prompt
        from council.types import Mode

        prompt_plain = round2_prompt(Mode.FIX, "task", "ctx", "solution")
        prompt_struct = round2_prompt(Mode.FIX, "task", "ctx", "solution", structured=True)

        assert len(prompt_struct) > len(prompt_plain)
        assert "```json" in prompt_struct
        assert "```json" not in prompt_plain
