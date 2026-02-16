"""Shared data types for the council pipeline."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class Mode(str, enum.Enum):
    """Pipeline mode determines prompt templates."""

    FIX = "fix"
    FEATURE = "feature"
    REVIEW = "review"


class DiffScope(str, enum.Enum):
    """Which git diffs to include."""

    NONE = "none"
    STAGED = "staged"
    UNSTAGED = "unstaged"
    ALL = "all"


class ContextMode(str, enum.Enum):
    """Context gathering strategy."""

    AUTO = "auto"
    NONE = "none"


class InputMode(str, enum.Enum):
    """How to send prompts to a tool."""

    STDIN = "stdin"
    FILE = "file"


@dataclass
class ToolResult:
    """Result from running a single tool invocation."""

    tool_name: str
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False


@dataclass
class RoundResult:
    """Result of a single pipeline round."""

    round_name: str
    results: dict[str, ToolResult] = field(default_factory=dict)


@dataclass
class ContextSource:
    """Metadata about a gathered context source."""

    source_type: str  # "git_status", "diff_staged", "diff_unstaged", "file", "tree", "env"
    path: str | None = None
    original_size: int = 0
    included_size: int = 0
    truncated: bool = False
    excluded: bool = False
    reason: str | None = None


@dataclass
class GatheredContext:
    """All context gathered for the pipeline."""

    text: str = ""
    sources: list[ContextSource] = field(default_factory=list)
    total_size: int = 0
    changed_files: list[str] = field(default_factory=list)


class RoundStatus(str, enum.Enum):
    """Status of a pipeline round in the checkpoint state."""

    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class RunOptions:
    """All options for a single pipeline run."""

    mode: Mode
    task: str
    task_file: Path | None = None
    context_mode: ContextMode = ContextMode.AUTO
    diff_scope: DiffScope = DiffScope.ALL
    include_paths: list[str] = field(default_factory=list)
    include_globs: list[str] = field(default_factory=list)
    include_from_diff: bool = False
    max_context_kb: int = 300
    max_file_kb: int = 60
    timeout_sec: int = 180
    outdir: Path = field(default_factory=lambda: Path("runs"))
    tools: list[str] = field(default_factory=lambda: ["claude", "codex"])
    dry_run: bool = False
    print_prompts: bool = False
    verbose: bool = False
    no_save: bool = False
    redact_paths: bool = False
    config_path: Path | None = None
