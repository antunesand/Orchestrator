"""Context gathering: git info, file inclusion, truncation, and budget management."""

from __future__ import annotations

import fnmatch
import platform
import subprocess
import sys
from pathlib import Path

from council.compat import as_posix, normalize_glob, normalize_path_str
from council.smart_context import FileRef, extract_file_refs, extract_scope, resolve_ref
from council.types import ContextMode, ContextSource, DiffScope, GatheredContext, RunOptions

# Files/patterns that are always excluded from inclusion.
ALWAYS_EXCLUDE = [
    ".env",
    "*.pem",
    "*.key",
    "id_rsa*",
    "credentials*",
    "secrets*",
    "token*",
    "node_modules",
    ".git",
]

# Common binary extensions to skip.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".pyc", ".pyo", ".class", ".wasm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".sqlite", ".db",
}

# Type alias for a context section: (label, content, priority, optional source ref).
Section = tuple[str, str, int, ContextSource | None]


def _is_binary(path: Path) -> bool:
    """Heuristic check if a file is binary."""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        # Check for null bytes (common binary indicator).
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True


def _matches_exclude(name: str) -> bool:
    """Check if a filename or any path component matches an always-exclude pattern.

    Handles nested paths like 'node_modules/react/index.js' by checking
    each component of the path against all exclude patterns.
    """
    # Normalise separators so Windows paths work too.
    normalised = normalize_path_str(name)
    parts = normalised.split("/")

    for part in parts:
        for pattern in ALWAYS_EXCLUDE:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def _run_git(args: list[str], cwd: Path | None = None) -> str | None:
    """Run a git command, return stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _truncate_content(content: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate content keeping head and tail, with a marker."""
    encoded = content.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return content, False

    # Keep roughly 70% head, 30% tail.
    head_size = int(max_bytes * 0.7)
    tail_size = max_bytes - head_size - 200  # Reserve space for marker.
    if tail_size < 0:
        tail_size = 0

    head = encoded[:head_size].decode("utf-8", errors="replace")
    tail = encoded[-tail_size:].decode("utf-8", errors="replace") if tail_size > 0 else ""

    total_kb = len(encoded) / 1024
    marker = (
        f"\n\n... TRUNCATED ({total_kb:.1f} KB total, "
        f"showing first {head_size / 1024:.1f} KB + last {tail_size / 1024:.1f} KB) ...\n\n"
    )
    return head + marker + tail, True


def _read_file_safe(path: Path, max_file_kb: int) -> tuple[str, int, bool]:
    """Read a file, truncating if over max_file_kb. Returns (content, original_size, truncated)."""
    try:
        raw = path.read_bytes()
    except (OSError, PermissionError):
        return "", 0, False

    original_size = len(raw)
    text = raw.decode("utf-8", errors="replace")
    max_bytes = max_file_kb * 1024

    if original_size > max_bytes:
        text, _ = _truncate_content(text, max_bytes)
        return text, original_size, True
    return text, original_size, False


def _collect_changed_files(repo_root: Path) -> list[str]:
    """Build changed file list as union of staged, unstaged, and untracked files.

    Works even in repos with no commits.
    """
    changed: set[str] = set()

    # Staged changes.
    staged = _run_git(["diff", "--name-only", "--staged"], cwd=repo_root)
    if staged:
        for line in staged.strip().splitlines():
            if line.strip():
                changed.add(line.strip())

    # Unstaged changes (against index; works with no commits).
    unstaged = _run_git(["diff", "--name-only"], cwd=repo_root)
    if unstaged:
        for line in unstaged.strip().splitlines():
            if line.strip():
                changed.add(line.strip())

    # Untracked files from git status --porcelain.
    status = _run_git(["status", "--porcelain"], cwd=repo_root)
    if status:
        for line in status.strip().splitlines():
            if line.startswith("?? "):
                # Strip the "?? " prefix; path may be quoted for special chars.
                path = line[3:].strip().strip('"')
                if path:
                    changed.add(path)

    return sorted(changed)


def gather_context(opts: RunOptions, repo_root: Path | None) -> GatheredContext:
    """Gather all context according to run options."""
    ctx = GatheredContext()
    sections: list[Section] = []
    sources: list[ContextSource] = []

    if opts.context_mode == ContextMode.NONE:
        ctx.text = ""
        ctx.sources = []
        return ctx

    # --- Git context ---
    if repo_root is not None:
        # Git status
        status = _run_git(["status", "--porcelain"], cwd=repo_root)
        if status is not None:
            src = ContextSource(
                source_type="git_status",
                original_size=len(status.encode()),
                included_size=len(status.encode()),
            )
            sources.append(src)
            sections.append(("## Git Status\n```\n" + status + "```", status, 50, src))

        # Diffs
        if opts.diff_scope in (DiffScope.STAGED, DiffScope.ALL):
            diff = _run_git(["diff", "--staged"], cwd=repo_root)
            if diff:
                src = ContextSource(
                    source_type="diff_staged",
                    original_size=len(diff.encode()),
                    included_size=len(diff.encode()),
                )
                sources.append(src)
                sections.append(("## Staged Diff\n```diff\n" + diff + "```", diff, 90, src))

        if opts.diff_scope in (DiffScope.UNSTAGED, DiffScope.ALL):
            diff = _run_git(["diff"], cwd=repo_root)
            if diff:
                src = ContextSource(
                    source_type="diff_unstaged",
                    original_size=len(diff.encode()),
                    included_size=len(diff.encode()),
                )
                sources.append(src)
                sections.append(("## Unstaged Diff\n```diff\n" + diff + "```", diff, 85, src))

        # Changed files list (for --include-from-diff).
        ctx.changed_files = _collect_changed_files(repo_root)

        # Repo tree snapshot (lightweight).
        tree = _run_git(["ls-files"], cwd=repo_root)
        if tree:
            lines = tree.strip().splitlines()
            if len(lines) > 200:
                summary = "\n".join(lines[:200]) + f"\n... ({len(lines)} files total)"
            else:
                summary = "\n".join(lines)
            src = ContextSource(
                source_type="tree",
                original_size=len(summary.encode()),
                included_size=len(summary.encode()),
            )
            sources.append(src)
            sections.append(("## Repository File Tree\n```\n" + summary + "\n```", summary, 10, src))

    # --- Environment info ---
    env_info = (
        f"Python: {sys.version.split()[0]}\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"CWD: {Path.cwd()}"
    )
    src = ContextSource(
        source_type="env",
        original_size=len(env_info.encode()),
        included_size=len(env_info.encode()),
    )
    sources.append(src)
    sections.append(("## Environment\n```\n" + env_info + "\n```", env_info, 5, src))

    # --- Explicit file includes ---
    for include_path in opts.include_paths:
        _include_file(Path(include_path), opts, sections, sources, priority=60, explicit=True)

    # --- Glob includes ---
    for pattern in opts.include_globs:
        matched = normalize_glob(pattern, recursive=True)
        for m in matched:
            _include_file(Path(m), opts, sections, sources, priority=30, explicit=False)

    # --- Include from diff ---
    if opts.include_from_diff and repo_root:
        for changed in ctx.changed_files:
            fpath = repo_root / changed
            _include_file(fpath, opts, sections, sources, priority=40, explicit=False)

    # --- Smart context: auto-include files referenced in task text ---
    if opts.smart_context:
        _include_from_task_refs(opts, repo_root, sections, sources)

    # --- Budget enforcement ---
    max_bytes = opts.max_context_kb * 1024
    _enforce_budget(sections, max_bytes)

    # Assemble final text.
    assembled = "\n\n".join(label for label, _, _, _ in sections)
    ctx.text = assembled
    ctx.sources = sources
    ctx.total_size = len(assembled.encode())
    return ctx


def _include_file(
    path: Path,
    opts: RunOptions,
    sections: list[Section],
    sources: list[ContextSource],
    priority: int,
    explicit: bool,
) -> None:
    """Include a single file into the context sections."""
    if not path.is_file():
        return

    name = as_posix(path)

    # Exclude check.
    if _matches_exclude(name) or _matches_exclude(path.name):
        if explicit:
            # Warn but still include for explicit includes.
            print(
                f"WARNING: Including sensitive file '{name}' (matched exclude pattern)",
                file=sys.stderr,
            )
        else:
            sources.append(ContextSource(
                source_type="file",
                path=name,
                excluded=True,
                reason="matched exclude pattern",
            ))
            return

    if _is_binary(path):
        sources.append(ContextSource(
            source_type="file",
            path=name,
            excluded=True,
            reason="binary file",
        ))
        return

    content, orig_size, truncated = _read_file_safe(path, opts.max_file_kb)
    src = ContextSource(
        source_type="file",
        path=name,
        original_size=orig_size,
        included_size=len(content.encode()),
        truncated=truncated,
    )
    sources.append(src)

    label = f"## File: {name}\n```\n{content}\n```"
    sections.append((label, content, priority, src))


def _include_from_task_refs(
    opts: RunOptions,
    repo_root: Path | None,
    sections: list[Section],
    sources: list[ContextSource],
) -> None:
    """Parse the task text for file:line references and include relevant scopes.

    For each reference found:
    - If a line number is present, extract just the enclosing function/class.
    - If no line number, include the whole file (subject to truncation).

    Already-included paths are skipped to avoid duplication.
    """
    refs = extract_file_refs(opts.task)
    if not refs:
        return

    # Track paths already in sections to avoid duplicates.
    already_included: set[str] = set()
    for _, _, _, src in sections:
        if src is not None and src.path:
            already_included.add(src.path)

    for ref in refs:
        resolved = resolve_ref(ref, repo_root)
        if resolved is None:
            continue

        name = as_posix(resolved)
        if name in already_included:
            continue
        already_included.add(name)

        # Exclude/binary checks.
        if _matches_exclude(name) or _matches_exclude(resolved.name):
            sources.append(ContextSource(
                source_type="file",
                path=name,
                excluded=True,
                reason="matched exclude pattern (smart-context)",
            ))
            continue

        if _is_binary(resolved):
            continue

        if ref.line is not None:
            _include_file_scope(
                resolved, ref.line, name, opts, sections, sources,
            )
        else:
            _include_file(resolved, opts, sections, sources, priority=55, explicit=False)


def _include_file_scope(
    path: Path,
    target_line: int,
    display_name: str,
    opts: RunOptions,
    sections: list[Section],
    sources: list[ContextSource],
) -> None:
    """Include just the enclosing scope around *target_line* from *path*.

    Higher priority (70) than generic file includes so scope snippets
    survive budget enforcement.
    """
    try:
        raw = path.read_bytes()
    except (OSError, PermissionError):
        return

    source_text = raw.decode("utf-8", errors="replace")
    snippet, start_line, end_line = extract_scope(source_text, target_line)

    if not snippet.strip():
        return

    # Respect per-file size cap.
    max_bytes = opts.max_file_kb * 1024
    snippet_bytes = len(snippet.encode("utf-8", errors="replace"))
    truncated = False
    if snippet_bytes > max_bytes:
        snippet, truncated = _truncate_content(snippet, max_bytes)

    src = ContextSource(
        source_type="file",
        path=display_name,
        original_size=len(raw),
        included_size=len(snippet.encode("utf-8", errors="replace")),
        truncated=truncated,
    )
    sources.append(src)

    label = (
        f"## File: {display_name} (lines {start_line}-{end_line}, "
        f"around line {target_line})\n```\n{snippet}\n```"
    )
    sections.append((label, snippet, 70, src))


def _truncate_fenced_diff(label: str, max_total_bytes: int) -> tuple[str, bool]:
    """Truncate a fenced diff section while preserving header + fences.

    Expects label in the form::

        ## Section Title
        ```diff
        <diff body>
        ```

    Truncates only the body, keeping opening/closing fences intact.
    Falls back to plain truncation for non-fenced content.
    """
    fence_open = "```diff\n"
    fence_close = "\n```"

    open_idx = label.find(fence_open)
    if open_idx == -1:
        # Not a fenced diff — fall back to plain truncation.
        return _truncate_content(label, max_total_bytes)

    header = label[: open_idx + len(fence_open)]
    rest = label[open_idx + len(fence_open) :]

    # Find the last closing fence.
    close_idx = rest.rfind("```")
    if close_idx == -1:
        body = rest
        closing = fence_close
    else:
        body = rest[:close_idx]
        closing = rest[close_idx:]  # preserve "```" and anything after

    header_bytes = len(header.encode("utf-8", errors="replace"))
    closing_bytes = len(closing.encode("utf-8", errors="replace"))
    overhead = header_bytes + closing_bytes

    if overhead >= max_total_bytes:
        # Budget too small even for fences — return minimal stub.
        stub = header + "(truncated)" + closing
        return stub, True

    body_budget = max_total_bytes - overhead
    truncated_body, was_truncated = _truncate_content(body, body_budget)
    return header + truncated_body + closing, was_truncated


def _enforce_budget(
    sections: list[Section],
    max_bytes: int,
) -> None:
    """Drop lowest-priority items until total fits within budget.

    Drop order (by priority, ascending):
    1. env/tree (priority ~5-10)
    2. glob files (priority ~30)
    3. diff-included files (priority ~40)
    4. truncate diffs (prefer keeping at least 120 KB, but respect max_bytes)

    After all passes the total rendered context is guaranteed <= max_bytes.

    Updates the linked ContextSource objects to reflect what was
    actually dropped or truncated.
    """
    def _total() -> int:
        return sum(len(label.encode()) for label, _, _, _ in sections)

    if _total() <= max_bytes:
        return

    # Sort by priority ascending (lowest priority dropped first).
    indexed = sorted(enumerate(sections), key=lambda x: x[1][2])

    to_remove: list[int] = []
    for idx, (_label, _content, priority, src) in indexed:
        if _total() <= max_bytes:
            break
        # Don't drop diffs below 120 KB.
        if priority >= 85:  # diffs
            continue
        to_remove.append(idx)
        # Mark the source as dropped.
        if src is not None:
            src.excluded = True
            src.included_size = 0
            src.reason = "dropped due to context budget"

    # Remove in reverse order to maintain indices.
    for idx in sorted(to_remove, reverse=True):
        sections.pop(idx)

    # If still over budget, truncate diff sections.
    if _total() > max_bytes:
        # Preferred minimum per diff, but never more than total budget.
        min_diff_bytes = min(120 * 1024, max_bytes)

        for i, (label, content, priority, src) in enumerate(sections):
            if priority >= 85 and _total() > max_bytes:
                label_size = len(label.encode())
                keep = max(min_diff_bytes, max_bytes - _total() + label_size)
                truncated_label, was_truncated = _truncate_fenced_diff(label, keep)
                sections[i] = (truncated_label, content, priority, src)
                if was_truncated and src is not None:
                    src.truncated = True
                    src.included_size = len(truncated_label.encode())

    # Final guarantee: if still over budget (e.g. multiple large diffs each
    # kept at min_diff_bytes, or non-diff sections still too large), force
    # proportional truncation until we fit.
    while _total() > max_bytes and sections:
        current_total = _total()
        scale = max_bytes / current_total if current_total > 0 else 1.0
        made_progress = False
        for i, (label, content, priority, src) in enumerate(sections):
            label_bytes = len(label.encode())
            target = int(label_bytes * scale)
            if target < label_bytes:
                if priority >= 85:
                    new_label, was_truncated = _truncate_fenced_diff(label, target)
                else:
                    new_label, was_truncated = _truncate_content(label, target)
                if len(new_label.encode()) < label_bytes:
                    made_progress = True
                    sections[i] = (new_label, content, priority, src)
                    if src is not None:
                        src.truncated = True
                        src.included_size = len(new_label.encode())
        if not made_progress:
            break
