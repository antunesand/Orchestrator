"""Context gathering: git info, file inclusion, truncation, and budget management."""

from __future__ import annotations

import fnmatch
import glob as globmod
import os
import platform
import subprocess
import sys
from pathlib import Path

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
    """Check if a filename matches any always-exclude pattern."""
    for pattern in ALWAYS_EXCLUDE:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(os.path.basename(name), pattern):
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


def gather_context(opts: RunOptions, repo_root: Path | None) -> GatheredContext:
    """Gather all context according to run options."""
    ctx = GatheredContext()
    sections: list[tuple[str, str, int]] = []  # (label, content, priority) lower=drop first
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
            sections.append(("## Git Status\n```\n" + status + "```", status, 50))

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
                sections.append(("## Staged Diff\n```diff\n" + diff + "```", diff, 90))

        if opts.diff_scope in (DiffScope.UNSTAGED, DiffScope.ALL):
            diff = _run_git(["diff"], cwd=repo_root)
            if diff:
                src = ContextSource(
                    source_type="diff_unstaged",
                    original_size=len(diff.encode()),
                    included_size=len(diff.encode()),
                )
                sources.append(src)
                sections.append(("## Unstaged Diff\n```diff\n" + diff + "```", diff, 85))

        # Changed files list (for --include-from-diff).
        changed_out = _run_git(["diff", "--name-only", "HEAD"], cwd=repo_root)
        if changed_out:
            ctx.changed_files = [
                f.strip() for f in changed_out.strip().splitlines() if f.strip()
            ]

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
            sections.append(("## Repository File Tree\n```\n" + summary + "\n```", summary, 10))

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
    sections.append(("## Environment\n```\n" + env_info + "\n```", env_info, 5))

    # --- Explicit file includes ---
    for include_path in opts.include_paths:
        _include_file(Path(include_path), opts, sections, sources, priority=60, explicit=True)

    # --- Glob includes ---
    for pattern in opts.include_globs:
        matched = sorted(globmod.glob(pattern, recursive=True))
        for m in matched:
            _include_file(Path(m), opts, sections, sources, priority=30, explicit=False)

    # --- Include from diff ---
    if opts.include_from_diff and repo_root:
        for changed in ctx.changed_files:
            fpath = repo_root / changed
            _include_file(fpath, opts, sections, sources, priority=40, explicit=False)

    # --- Budget enforcement ---
    max_bytes = opts.max_context_kb * 1024
    _enforce_budget(sections, sources, max_bytes)

    # Assemble final text.
    assembled = "\n\n".join(label for label, _, _ in sections)
    ctx.text = assembled
    ctx.sources = sources
    ctx.total_size = len(assembled.encode())
    return ctx


def _include_file(
    path: Path,
    opts: RunOptions,
    sections: list[tuple[str, str, int]],
    sources: list[ContextSource],
    priority: int,
    explicit: bool,
) -> None:
    """Include a single file into the context sections."""
    if not path.is_file():
        return

    name = str(path)

    # Exclude check.
    if _matches_exclude(name) or _matches_exclude(path.name):
        if explicit:
            # Warn but still include for explicit includes.
            import sys as _sys
            print(
                f"WARNING: Including sensitive file '{name}' (matched exclude pattern)",
                file=_sys.stderr,
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
    sections.append((label, content, priority))


def _enforce_budget(
    sections: list[tuple[str, str, int]],
    sources: list[ContextSource],
    max_bytes: int,
) -> None:
    """Drop lowest-priority items until total fits within budget.

    Drop order (by source_type):
    1. tree (priority ~10)
    2. glob files (priority ~30)
    3. diff-included files (priority ~40)
    4. truncate diffs (keep at least 120 KB)
    """
    def _total() -> int:
        return sum(len(label.encode()) for label, _, _ in sections)

    if _total() <= max_bytes:
        return

    # Sort by priority ascending (lowest priority dropped first).
    indexed = sorted(enumerate(sections), key=lambda x: x[1][2])

    to_remove: list[int] = []
    for idx, (label, content, priority) in indexed:
        if _total() <= max_bytes:
            break
        # Don't drop diffs below 120 KB.
        if priority >= 85:  # diffs
            continue
        to_remove.append(idx)

    # Remove in reverse order to maintain indices.
    for idx in sorted(to_remove, reverse=True):
        sections.pop(idx)

    # If still over budget, truncate diffs.
    if _total() > max_bytes:
        min_diff_bytes = 120 * 1024
        for i, (label, content, priority) in enumerate(sections):
            if priority >= 85 and _total() > max_bytes:
                # Keep at least min_diff_bytes.
                keep = max(min_diff_bytes, max_bytes - _total() + len(label.encode()))
                truncated_label, was_truncated = _truncate_content(label, keep)
                sections[i] = (truncated_label, content, priority)
