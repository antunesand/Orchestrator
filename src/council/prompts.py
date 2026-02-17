"""Prompt templates for each pipeline mode and round."""

from __future__ import annotations

from council.review import JSON_CRITIQUE_SUFFIX
from council.types import Mode

# ---------------------------------------------------------------------------
# Shared preamble injected into every prompt.
# ---------------------------------------------------------------------------
_PREAMBLE = """\
IMPORTANT RULES:
- Do NOT invent files, functions, or variables not shown in the provided context. \
If information is not shown, explicitly state "not shown in provided context".
- When proposing code changes, output them as unified diff format whenever possible.
- Be precise: include exact file paths and line numbers.
- Keep changes minimal and safe. Do not refactor unrelated code.
"""

# ---------------------------------------------------------------------------
# Mode-specific task framing.
# ---------------------------------------------------------------------------
_MODE_FRAME: dict[Mode, str] = {
    Mode.FIX: (
        "You are helping fix a bug or error. "
        "The user has described the problem below along with relevant context. "
        "Focus on identifying the root cause and producing a safe, minimal fix."
    ),
    Mode.FEATURE: (
        "You are helping implement a new feature. "
        "The user has described the desired functionality below along with relevant context. "
        "Focus on a clean, minimal implementation that integrates with the existing codebase."
    ),
    Mode.REVIEW: (
        "You are performing a code review. "
        "The user has provided changes (diffs) and/or code below. "
        "Focus on correctness, edge cases, security, performance, and test coverage."
    ),
    Mode.ASK: (
        "You are answering a question about the codebase. "
        "The user has asked a question below along with relevant context. "
        "Provide a clear, thorough, and accurate answer based on the provided context."
    ),
}

# ---------------------------------------------------------------------------
# Round 0: Generate (sent to both tools in parallel).
# ---------------------------------------------------------------------------
_ROUND0_SUFFIX = """\

Provide your response in the following structured format:

### Summary
3-7 bullet points summarizing your analysis and proposed changes.

### Risks / Gotchas
List potential risks, edge cases, or gotchas.

### Patch
Provide changes as a unified diff (```diff ... ```) with file paths. \
If a diff is not applicable, provide explicit edit instructions with exact file paths and line references.

### Tests
List exact test commands and what each validates.

### Rollback Plan
How to revert these changes safely if something goes wrong.
"""

_ROUND0_ASK_SUFFIX = """\

Provide your response in the following structured format:

### Answer
A clear, thorough answer to the question.

### Key Details
Bullet points with specific file paths, functions, or code references that support your answer.

### Related Areas
Other parts of the codebase that are relevant or connected.
"""


def round0_prompt(mode: Mode, task: str, context: str) -> str:
    """Build the Round 0 prompt for initial generation."""
    suffix = _ROUND0_ASK_SUFFIX if mode == Mode.ASK else _ROUND0_SUFFIX
    return f"{_PREAMBLE}\n\n{_MODE_FRAME[mode]}\n\n## Task\n{task}\n\n## Context\n{context}\n\n{suffix}"


# ---------------------------------------------------------------------------
# Round 1: Claude improves (given Codex output + own output).
# ---------------------------------------------------------------------------


def round1_prompt(mode: Mode, task: str, context: str, codex_output: str, claude_output: str) -> str:
    """Build Round 1 prompt: Claude evaluates Codex and improves."""
    return (
        f"{_PREAMBLE}\n\n"
        f"{_MODE_FRAME[mode]}\n\n"
        f"## Task\n{task}\n\n"
        f"## Context\n{context}\n\n"
        f"## Your Previous Analysis\n{claude_output}\n\n"
        f"## Alternative Analysis (from another LLM)\n{codex_output}\n\n"
        "## Instructions\n"
        "You have two analyses above: your own previous analysis and an alternative one.\n"
        "1. Critically evaluate the alternative analysis. Categorize issues as MUST-FIX or SHOULD-FIX.\n"
        "2. Integrate the best parts of the alternative into your analysis.\n"
        "3. Produce an IMPROVED version with:\n"
        "   - Summary (3-7 bullets)\n"
        "   - Improved patch as unified diff\n"
        "   - Updated test plan\n"
        "4. Keep changes minimal and safe. Do not add unnecessary modifications.\n"
    )


# ---------------------------------------------------------------------------
# Round 2: Codex critiques Claude's improved output.
# ---------------------------------------------------------------------------


def round2_prompt(mode: Mode, task: str, context: str, claude_improved: str, *, structured: bool = False) -> str:
    """Build Round 2 prompt: Codex provides adversarial critique.

    When *structured* is True, appends instructions for JSON output so
    the critique can be parsed into a ``ReviewResult``.
    """
    base = (
        f"{_PREAMBLE}\n\n"
        f"{_MODE_FRAME[mode]}\n\n"
        f"## Task\n{task}\n\n"
        f"## Context\n{context}\n\n"
        f"## Proposed Solution\n{claude_improved}\n\n"
        "## Instructions\n"
        "Perform an adversarial code review of the proposed solution above.\n\n"
        "Provide your critique in this format:\n\n"
        "### Must-Fix Issues\n"
        "Critical issues that must be addressed before merging.\n\n"
        "### Should-Fix Issues\n"
        "Non-critical improvements that should be considered.\n\n"
        "### Missing Tests\n"
        "Test cases that are missing or insufficient.\n\n"
        "### Suggested Corrections\n"
        "Provide diff snippets for any corrections you recommend.\n\n"
        "### Confidence Score\n"
        "Rate your confidence in the proposed solution: 0-100\n"
        "(0 = fundamentally broken, 100 = production-ready with no changes needed)\n"
    )
    if structured:
        base += JSON_CRITIQUE_SUFFIX
    return base


# ---------------------------------------------------------------------------
# Round 3: Claude finalizes (given improved output + Codex critique).
# ---------------------------------------------------------------------------


def round3_prompt(mode: Mode, task: str, context: str, claude_improved: str, codex_critique: str) -> str:
    """Build Round 3 prompt: Claude finalizes the best result."""
    return (
        f"{_PREAMBLE}\n\n"
        f"{_MODE_FRAME[mode]}\n\n"
        f"## Task\n{task}\n\n"
        f"## Context\n{context}\n\n"
        f"## Your Improved Solution\n{claude_improved}\n\n"
        f"## Critique from Adversarial Review\n{codex_critique}\n\n"
        "## Instructions\n"
        "Review the critique above and apply all valid corrections.\n"
        "Produce the FINAL result with this exact structure:\n\n"
        "### Final Decision Summary\n"
        "Brief summary of what was decided and why.\n\n"
        "### Final Patch\n"
        "The definitive unified diff (```diff ... ```) incorporating all improvements.\n\n"
        "### Test Plan\n"
        "Exact test commands and what each validates.\n\n"
        "### Production Checklist\n"
        "- [ ] Validation steps\n"
        "- [ ] Logging / metrics considerations (if relevant)\n"
        "- [ ] Rollout plan\n"
        "- [ ] Rollback plan\n"
    )
