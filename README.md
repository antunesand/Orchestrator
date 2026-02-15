# Council CLI

Multi-LLM ping-pong workflow between Claude Code CLI and Codex CLI for code review, feature implementation, and bug fixing.

Council sends the same task to two LLM CLI tools in parallel, then orchestrates a 4-round exchange where they evaluate, improve, critique, and finalize each other's work — producing a single best-possible result.

## How It Works

```
Round 0: Generate    → Both tools analyze the task in parallel
Round 1: Improve     → Claude evaluates Codex's answer and improves its own
Round 2: Critique    → Codex performs adversarial review of Claude's improved answer
Round 3: Finalize    → Claude incorporates valid critique and produces final result
```

**Safe by default**: no auto-apply patches, no commits, no test execution unless you explicitly do so.

## Installation

```bash
# Clone and install in a virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .

# Or install dev dependencies for testing
pip install -e ".[dev]"
```

The `council` command will be available after installation.

## Prerequisites

You need at least one of these CLI tools installed and authenticated:

- **Claude Code CLI** (`claude`) — [Install docs](https://docs.anthropic.com/en/docs/claude-code)
- **Codex CLI** (`codex`) — [Install docs](https://github.com/openai/codex)

Council works best with both, but will gracefully degrade to a single tool if one is unavailable.

## Quick Start

```bash
# Fix a bug (auto-gathers git context)
council fix "TypeError in auth handler: 'NoneType' has no attribute 'email'"

# Implement a feature with specific files included
council feature --include src/auth.py --include src/models/user.py \
  "Add rate limiting to the login endpoint"

# Review staged changes
council review --diff staged \
  "Review these changes for correctness, security issues, and missing tests"

# Read task from a file
council fix --task-file bug_report.md

# Dry run: see what prompts would be sent without calling tools
council fix --dry-run --print-prompts "Fix the broken test"
```

## CLI Reference

### Subcommands

| Command | Description | Default diff scope |
|---------|-------------|--------------------|
| `council fix "..."` | Fix bugs and errors | `--diff all` |
| `council feature "..."` | Implement new functionality | `--diff all` |
| `council review "..."` | Review code changes | `--diff staged` |

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--task-file PATH` | — | Read task from a file instead of CLI argument |
| `--context auto\|none` | `auto` | Context gathering mode |
| `--diff none\|staged\|unstaged\|all` | varies | Which git diffs to include |
| `--include PATH` | — | Include file content (repeatable) |
| `--include-glob "GLOB"` | — | Include files matching glob (repeatable) |
| `--include-from-diff` | `false` | Include full contents of changed files |
| `--max-context-kb N` | `300` | Max total context size in KB |
| `--max-file-kb N` | `60` | Max single file size in KB |
| `--timeout-sec N` | `180` | Timeout per tool invocation in seconds |
| `--outdir PATH` | `runs` | Output directory for run artifacts |
| `--tools LIST` | `claude,codex` | Comma-separated tool names |
| `--dry-run` | `false` | Write prompts/context only, don't invoke tools |
| `--print-prompts` | `false` | Print prompts to terminal (still saves to files) |
| `--verbose` | `false` | Verbose output |
| `--config PATH` | — | Path to config file |

## Configuration

Council looks for configuration in this order:
1. `--config` CLI flag
2. `.council.yml` in the repo root
3. `council.yml` in the repo root
4. `~/.council.yml` in your home directory
5. Built-in defaults

### Sample `.council.yml`

```yaml
tools:
  claude:
    description: "Claude Code CLI"
    command: ["claude"]
    input_mode: "stdin"        # stdin or file
    prompt_file_arg: null      # if input_mode=file, e.g. "--prompt-file"
    extra_args: ["-p"]         # appended to command
    env: {}                    # additional env vars

  codex:
    description: "Codex CLI"
    command: ["codex"]
    input_mode: "stdin"
    prompt_file_arg: null
    extra_args: []
    env: {}
```

### Input Modes

- **`stdin`** (default): Pipes the prompt to the tool's stdin. Most CLI tools support this.
- **`file`**: Writes the prompt to a temporary file and passes the path via `prompt_file_arg`.

### Adapting to Your CLI Setup

If your `claude` binary is at a custom path or needs specific flags:

```yaml
tools:
  claude:
    command: ["/usr/local/bin/claude"]
    extra_args: ["-p", "--no-color"]
    env:
      ANTHROPIC_API_KEY: "sk-..."
```

If your tool requires file-based input:

```yaml
tools:
  codex:
    command: ["codex"]
    input_mode: "file"
    prompt_file_arg: "--input"
    extra_args: ["--model", "gpt-4"]
```

## Run Artifacts

Each invocation creates a timestamped folder:

```
runs/2025-06-15_143022_fix_broken_auth/
  manifest.json          # Full metadata: timing, commands, exit codes, context stats
  task.md                # The task description
  context.md             # All gathered context
  context_sources.json   # What was gathered, sizes, truncation decisions
  rounds/
    0_generate/
      prompt_claude.md   # Prompt sent to Claude
      prompt_codex.md    # Prompt sent to Codex
      claude_stdout.md   # Claude's response
      claude_stderr.txt
      codex_stdout.md    # Codex's response
      codex_stderr.txt
    1_claude_improve/
      prompt.md          # Prompt for improvement round
      stdout.md
      stderr.txt
    2_codex_critique/
      prompt.md          # Prompt for critique round
      stdout.md
      stderr.txt
    3_claude_finalize/
      prompt.md          # Prompt for finalization
      stdout.md
      stderr.txt
  final/
    final.md             # The final result
    final.patch          # Extracted unified diff (if any)
    summary.md           # Short summary
```

## Context Gathering

When `--context auto` is set (the default) and you're in a git repo, council automatically collects:

- `git status` output
- Diffs (staged, unstaged, or both depending on `--diff`)
- List of changed files
- Lightweight repo file tree
- Python version and OS info

### File Inclusion Safety

Council **never** includes:
- Binary files
- `.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `secrets*`, `token*`
- `node_modules/`, `.git/`

If you explicitly `--include` a sensitive file, council prints a warning but includes it.

### Truncation

- Files exceeding `--max-file-kb` are truncated (head + tail with a `...TRUNCATED...` marker)
- Total context is capped at `--max-context-kb`; items are dropped in priority order:
  1. Repo tree snapshot
  2. Glob-included files
  3. Diff-included files
  4. Diffs (truncated, keeping at least 120 KB)

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests use mocked subprocesses — no real LLM calls are made.

## Examples

### Fix with full context

```bash
council fix --diff all --context auto \
  "Users are getting 500 errors on /api/login. Stack trace: ..."
```

### Feature with included source files

```bash
council feature --include src/api/routes.py --include src/models/user.py \
  "Add a /api/users/me endpoint that returns the current user profile"
```

### Review staged changes before committing

```bash
git add -p  # stage your changes
council review --diff staged \
  "Review these changes for correctness, security, and test coverage"
```

### Dry run to inspect prompts

```bash
council fix --dry-run --print-prompts "Fix the flaky test in test_auth.py"
```

### Use only Claude (skip Codex)

```bash
council fix --tools claude "Fix the bug"
```
