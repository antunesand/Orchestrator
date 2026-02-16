"""Smart context targeting: traceback parsing and scope extraction.

Parses task text (error messages, tracebacks, log output) to find
file-path + line-number references, then extracts just the enclosing
function/class/block instead of the whole file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from council.compat import normalize_path_str


@dataclass(frozen=True)
class FileRef:
    """A file reference extracted from task text."""

    path: str
    line: int | None = None
    column: int | None = None


# ---------------------------------------------------------------------------
# Pattern matchers — ordered from most specific to most generic.
# ---------------------------------------------------------------------------

# Python traceback: '  File "src/foo.py", line 42, in func'
_PY_TB = re.compile(
    r'File "([^"]+)"'
    r"(?:,\s*line\s+(\d+))?"
)

# Node.js / V8 stack:  'at Object.<anonymous> (/home/user/app.js:12:5)'
#                   or  'at /home/user/app.js:12:5'
_NODE_STACK = re.compile(
    r"at\s+(?:[^\(]*\()?"          # optional "at FuncName ("
    r"([^\s\):]+\.(?:js|ts|mjs|cjs|jsx|tsx))"
    r":(\d+)(?::(\d+))?"           # :line[:col]
    r"\)?"
)

# Go panic / runtime:  'goroutine 1 [running]:' then 'main.go:42 +0x1a2'
#                 or    '/home/user/project/main.go:42'
_GO_PANIC = re.compile(
    r"([^\s:]+\.go):(\d+)"
)

# Rust panic:  'thread 'main' panicked at src/main.rs:15:5'
_RUST_PANIC = re.compile(
    r"panicked at (?:'[^']*', )?([^\s:]+\.rs):(\d+)(?::(\d+))?"
)

# Java/Kotlin stack: 'at com.example.App.main(App.java:10)'
_JAVA_STACK = re.compile(
    r"at\s+[^\(]+\(([^\s\):]+\.(?:java|kt|scala)):(\d+)\)"
)

# Ruby: 'from /home/user/app.rb:12:in `method''
_RUBY_STACK = re.compile(
    r"from\s+([^\s:]+\.rb):(\d+)"
)

# Generic "path:line" or "path:line:col" — catches most remaining formats.
# Matches paths that contain at least one '/' or '\' and end with a known
# source extension.
_GENERIC_FILE_LINE = re.compile(
    r"(?:^|[\s\"'(,])("
    r"[^\s:\"'(,]+"                # path (at least one char)
    r"\.(?:py|js|ts|jsx|tsx|go|rs|rb|java|kt|scala|c|cpp|cc|h|hpp|cs|php|swift|sh|yml|yaml|toml|json|sql|vue|svelte)"
    r")"
    r":(\d+)"                      # :line
    r"(?::(\d+))?"                 # optional :col
)


_PATTERNS: list[tuple[re.Pattern[str], bool]] = [
    # (pattern, has_col_group)
    (_PY_TB, False),
    (_NODE_STACK, True),
    (_GO_PANIC, False),
    (_RUST_PANIC, True),
    (_JAVA_STACK, False),
    (_RUBY_STACK, False),
    (_GENERIC_FILE_LINE, True),
]


def extract_file_refs(text: str) -> list[FileRef]:
    """Extract unique file references from text (tracebacks, logs, etc.).

    Returns a deduplicated list ordered by first appearance.
    """
    seen: set[tuple[str, int | None]] = set()
    refs: list[FileRef] = []

    for pattern, has_col in _PATTERNS:
        for m in pattern.finditer(text):
            path = normalize_path_str(m.group(1))
            line_str = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            line = int(line_str) if line_str else None
            col = None
            if has_col and m.lastindex and m.lastindex >= 3 and m.group(3):
                col = int(m.group(3))

            key = (path, line)
            if key not in seen:
                seen.add(key)
                refs.append(FileRef(path=path, line=line, column=col))

    return refs


def resolve_ref(ref: FileRef, repo_root: Path | None) -> Path | None:
    """Resolve a FileRef to an actual file on disk.

    Tries the path as-is first, then relative to repo_root.
    """
    p = Path(ref.path)
    if p.is_file():
        return p
    if repo_root is not None:
        candidate = repo_root / ref.path
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Scope extraction — find the enclosing function/class around a line.
# ---------------------------------------------------------------------------

# How many context lines to include above the first line of a scope block
# and below the last line.
_CONTEXT_LINES_ABOVE = 3
_CONTEXT_LINES_BELOW = 3


def extract_scope(
    source: str,
    target_line: int,
    context_above: int = _CONTEXT_LINES_ABOVE,
    context_below: int = _CONTEXT_LINES_BELOW,
) -> tuple[str, int, int]:
    """Extract the enclosing scope around *target_line* from source code.

    Returns ``(snippet, start_line, end_line)`` where start/end are
    1-based line numbers.  Falls back to a context window around the
    target line if no scope block is detected.
    """
    lines = source.splitlines(keepends=True)
    if not lines:
        return "", 1, 1

    # Clamp target to valid range (1-based).
    target_line = max(1, min(target_line, len(lines)))
    target_idx = target_line - 1  # 0-based

    # --- Step 1: Find the enclosing scope block ---
    scope_start = _find_scope_start(lines, target_idx)
    scope_end = _find_scope_end(lines, scope_start, target_idx)

    # Add context padding, but stop at adjacent scope boundaries.
    start = _expand_upward(lines, scope_start, context_above)
    end = _expand_downward(lines, scope_end, context_below)

    snippet = "".join(lines[start:end])
    return snippet, start + 1, end


def _find_scope_start(lines: list[str], target_idx: int) -> int:
    """Walk backwards from target_idx to find the start of the enclosing scope.

    Looks for Python def/class, JS/TS function/class, Go func, Rust fn/impl,
    Java/Kotlin method signatures, etc.
    """
    scope_re = re.compile(
        r"^(\s*)"
        r"(?:"
        r"(?:async\s+)?def\s+\w+"                    # Python def / async def
        r"|class\s+\w+"                                # Python / JS / Java class
        r"|(?:async\s+)?function\s*\w*\s*\("          # JS function
        r"|(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\b"  # JS export
        r"|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>"  # JS arrow
        r"|func\s+(?:\([^)]*\)\s*)?\w+"               # Go func
        r"|(?:pub\s+)?(?:async\s+)?fn\s+\w+"          # Rust fn
        r"|impl\b"                                     # Rust impl
        r"|(?:pub(?:lic)?|priv(?:ate)?|prot(?:ected)?|static|override|abstract|final|open|suspend|inline)?\s*"
        r"(?:fun|func|def|void|int|string|bool|var|val)\s+\w+"  # Kotlin / Java / Swift
        r"|@\w+"                                       # Decorator (include it)
        r")"
    )

    target_indent = _indent_level(lines[target_idx])

    for i in range(target_idx, -1, -1):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped:
            continue

        m = scope_re.match(line)
        if m:
            indent = len(m.group(1))
            # The scope definition must be at the same or lower indent
            # than the target line to be enclosing.
            if indent <= target_indent:
                # Include any preceding decorators/annotations.
                while i > 0:
                    prev = lines[i - 1].rstrip()
                    if prev.lstrip().startswith("@") or prev.lstrip().startswith("//"):
                        i -= 1
                    else:
                        break
                return i

    # No scope found — fall back to a window around the target.
    return max(0, target_idx - 15)


def _find_scope_end(lines: list[str], scope_start: int, target_idx: int) -> int:
    """Find the end of the scope block starting at scope_start.

    Uses indentation: the scope ends when we encounter a non-empty line
    at the same or lesser indent after the body has started.  Also handles
    brace-delimited languages.
    """
    if scope_start >= len(lines):
        return min(len(lines) - 1, target_idx + 15)

    start_indent = _indent_level(lines[scope_start])
    uses_braces = "{" in lines[scope_start] if scope_start < len(lines) else False

    if uses_braces:
        return _find_brace_end(lines, scope_start)

    # Indentation-based: body lines have indent > start_indent.
    body_started = False
    last_nonempty = target_idx

    for i in range(scope_start + 1, len(lines)):
        stripped = lines[i].rstrip()
        if not stripped:
            continue
        indent = _indent_level(lines[i])
        if indent > start_indent:
            body_started = True
            last_nonempty = i
        elif body_started:
            # Back to same/lower indent — scope ended.
            break
        else:
            # Still on the signature line (e.g. multi-line function args).
            last_nonempty = i

    # Don't include trailing blank lines as part of the scope itself —
    # context_below will add lines after the last real content line.
    return last_nonempty


def _find_brace_end(lines: list[str], start: int) -> int:
    """Find the closing brace that matches the opening one on/after start."""
    depth = 0
    found_open = False
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i
    return len(lines) - 1


_SCOPE_BOUNDARY_RE = re.compile(
    r"^\s*(?:"
    r"(?:async\s+)?def\s+\w+"
    r"|class\s+\w+"
    r"|(?:async\s+)?function\s+\w+"
    r"|func\s+(?:\([^)]*\)\s*)?\w+"
    r"|(?:pub\s+)?(?:async\s+)?fn\s+\w+"
    r"|impl\b"
    r")"
)


def _expand_upward(lines: list[str], scope_start: int, n: int) -> int:
    """Expand *n* lines above scope_start but stop at another scope boundary."""
    start = scope_start
    for i in range(scope_start - 1, max(-1, scope_start - 1 - n), -1):
        if i < 0:
            break
        if _SCOPE_BOUNDARY_RE.match(lines[i]):
            break
        start = i
    return start


def _expand_downward(lines: list[str], scope_end: int, n: int) -> int:
    """Expand *n* lines below scope_end but stop at another scope boundary."""
    end = scope_end + 1
    for i in range(scope_end + 1, min(len(lines), scope_end + 1 + n)):
        if _SCOPE_BOUNDARY_RE.match(lines[i]):
            break
        end = i + 1
    return end


def _indent_level(line: str) -> int:
    """Count leading whitespace characters."""
    return len(line) - len(line.lstrip())
