"""Configuration loading, validation, and defaults for council."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from council.types import InputMode


class ToolConfig(BaseModel):
    """Configuration for a single LLM CLI tool."""

    description: str = ""
    command: list[str] = Field(default_factory=lambda: ["claude"])
    input_mode: InputMode = InputMode.STDIN
    prompt_file_arg: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class CouncilConfig(BaseModel):
    """Top-level council configuration."""

    tools: dict[str, ToolConfig] = Field(default_factory=dict)

    @classmethod
    def defaults(cls) -> CouncilConfig:
        """Return a config with sensible defaults for claude and codex.

        Uses the recommended automation-friendly invocations:
        - Claude Code: ``claude -p "query"`` (headless print mode).
          The full prompt is piped via stdin; a short constant query
          argument satisfies the required positional arg for ``-p``.
        - Codex: ``codex exec`` with ``--ask-for-approval never``,
          ``--sandbox read-only``, ``--color never``, and ``-``
          (read prompt from stdin)
        """
        return cls(
            tools={
                "claude": ToolConfig(
                    description="Claude Code CLI",
                    command=["claude"],
                    input_mode=InputMode.STDIN,
                    extra_args=[
                        "-p",
                        "Use the piped input as the full task instructions."
                        " Produce the best possible answer.",
                    ],
                ),
                "codex": ToolConfig(
                    description="Codex CLI",
                    command=["codex", "exec"],
                    input_mode=InputMode.STDIN,
                    extra_args=[
                        "--ask-for-approval", "never",
                        "--sandbox", "read-only",
                        # Disable ANSI color codes so saved artifacts are clean.
                        "--color", "never",
                        "-",
                    ],
                ),
            }
        )


# Sensitive key suffixes that should be redacted in manifests.
_REDACT_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIALS")


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of env with sensitive values redacted."""
    redacted: dict[str, str] = {}
    for k, v in env.items():
        upper = k.upper()
        if any(upper.endswith(s) for s in _REDACT_SUFFIXES):
            redacted[k] = "***REDACTED***"
        else:
            redacted[k] = v
    return redacted


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and return parsed YAML from a file.

    Returns an empty dict on parse errors (with a warning to stderr).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        print(
            f"Warning: failed to parse config '{path}': {exc}\n"
            f"  Falling back to default configuration.",
            file=sys.stderr,
        )
        return {}
    except OSError as exc:
        print(
            f"Warning: could not read config '{path}': {exc}\n"
            f"  Falling back to default configuration.",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def load_config(
    cli_path: Path | None = None,
    repo_root: Path | None = None,
) -> CouncilConfig:
    """Load config from the first available source.

    Search order:
    1. Explicit CLI flag path
    2. Repo root .council.yml
    3. Repo root council.yml
    4. Home directory ~/.council.yml
    5. Built-in defaults
    """
    candidates: list[Path] = []

    if cli_path is not None:
        candidates.append(cli_path)

    if repo_root is not None:
        candidates.append(repo_root / ".council.yml")
        candidates.append(repo_root / "council.yml")

    home = Path.home()
    candidates.append(home / ".council.yml")

    for path in candidates:
        if path.is_file():
            raw = _load_yaml(path)
            return _parse_config(raw)

    return CouncilConfig.defaults()


def _parse_config(raw: dict[str, Any]) -> CouncilConfig:
    """Parse raw YAML dict into a CouncilConfig, merging with defaults."""
    defaults = CouncilConfig.defaults()

    tools_raw = raw.get("tools", {})
    if not isinstance(tools_raw, dict):
        return defaults

    tools: dict[str, ToolConfig] = {}
    for name, tool_data in tools_raw.items():
        if isinstance(tool_data, dict):
            try:
                # For known tools, merge user overrides on top of defaults
                # so that omitted fields (e.g. command) keep correct values.
                if name in defaults.tools:
                    base = defaults.tools[name].model_dump()
                    base.update(tool_data)
                    tools[name] = ToolConfig(**base)
                else:
                    tools[name] = ToolConfig(**tool_data)
            except (ValidationError, TypeError) as exc:
                print(
                    f"Warning: invalid config for tool '{name}': {exc}\n"
                    f"  Using defaults for this tool.",
                    file=sys.stderr,
                )

    # Merge: keep defaults for tools not specified in config.
    for name, default_tool in defaults.tools.items():
        if name not in tools:
            tools[name] = default_tool

    return CouncilConfig(tools=tools)


def find_repo_root() -> Path | None:
    """Walk up from cwd to find the nearest .git directory."""
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None
