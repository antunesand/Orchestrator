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

# Or with pipx (no venv needed)
pipx install -e .

# Or install dev dependencies for testing and linting
pip install -e ".[dev]"
```

The `council` command will be available after installation.

## Prerequisites

You need at least one of these CLI tools installed and authenticated:

- **Claude Code CLI** (`claude`) — [Install docs](https://docs.anthropic.com/en/docs/claude-code)
- **Codex CLI** (`codex`) — [Install docs](https://github.com/openai/codex)

Council works best with both, but will gracefully degrade to a single tool if one is unavailable.

## Authentication

**Council CLI does not require API keys.** It shells out to `claude` and `codex` CLI tools, which handle their own authentication.

If you're signed into the CLIs via subscription (e.g., Claude Max or ChatGPT Pro), council will use your subscription — no API keys needed.

> **Warning — API key environment variables:**
> If you set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in your shell or `.council.yml` env, the respective CLI tool may use API billing instead of your subscription login. Only set these if you intend to use API-based billing.

> **Warning — do not commit secrets:**
> Never put API keys in `.council.yml`. The `.gitignore` excludes `.council.yml` and `council.yml` by default, but double-check your repo. Use `council init` to set this up safely.

## Quick Start

```bash
# 1. Initialize config (creates .council.yml + updates .gitignore)
council init

# 2. Verify your setup
council doctor

# 3. Fix a bug (auto-gathers git context)
council fix "TypeError in auth handler: 'NoneType' has no attribute 'email'"

# 4. Implement a feature with specific files included
council feature --include src/auth.py --include src/models/user.py \
  "Add rate limiting to the login endpoint"

# 5. Review staged changes
council review --diff staged \
  "Review these changes for correctness, security issues, and missing tests"

# 6. Read task from a file
council fix --task-file bug_report.md

# 7. Dry run: see what prompts would be sent without calling tools
council fix --dry-run --print-prompts "Fix the broken test"
```

## CLI Reference

### Subcommands

| Command | Description | Default diff scope |
|---------|-------------|--------------------|
| `council fix "..."` | Fix bugs and errors | `--diff all` |
| `council feature "..."` | Implement new functionality | `--diff all` |
| `council review "..."` | Review code changes | `--diff staged` |
| `council resume <run_dir>` | Resume an interrupted or failed run | — |
| `council apply <run_dir>` | Apply a patch from a previous run | — |
| `council list` | List recent runs with status | — |
| `council init` | Create `.council.yml` and update `.gitignore` | — |
| `council doctor` | Check tool availability and configuration | — |

### Options (for fix/feature/review)

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
| `--verbose` | `false` | Verbose output (context stats, prompt sizes, command details) |
| `--no-save` | `false` | Only save final output and a minimal manifest |
| `--redact-paths` | `false` | Replace absolute paths with `<REDACTED>/basename` in saved artifacts |
| `--smart-context` / `--no-smart-context` | varies | Auto-include files referenced in tracebacks/logs (`fix` enables by default) |
| `--structured-review` / `--no-structured-review` | varies | Request JSON-structured critique in Round 2 (`review` enables by default) |
| `--claude-n N` | `1` | Number of Claude candidates to generate in Round 0 (1-5) |
| `--codex-n N` | `1` | Number of Codex candidates to generate in Round 0 (1-5) |
| `--version` | — | Show version and exit |
| `--config PATH` | — | Path to config file |

### council resume

Resume an interrupted or failed council run from its last checkpoint.

```bash
# Resume from where the run left off
council resume runs/2025-06-15_143022_fix_broken_auth/

# Only retry rounds that failed (preserve successful rounds)
council resume --retry-failed runs/2025-06-15_143022_fix_broken_auth/
```

| Flag | Default | Description |
|------|---------|-------------|
| `--retry-failed` | `false` | Only re-run failed rounds; skip already-succeeded ones |
| `--timeout-sec N` | `180` | Timeout per tool call |
| `--verbose` | `false` | Verbose output |
| `--config PATH` | — | Path to config file |

### council apply

Apply a patch from a previous council run to the current repository.

```bash
# Interactive: shows diff, asks for confirmation
council apply runs/2025-06-15_143022_fix_broken_auth/

# Skip confirmation prompt
council apply runs/2025-06-15_143022_fix_broken_auth/ --yes

# Dry-run: verify the patch applies cleanly without modifying files
council apply runs/2025-06-15_143022_fix_broken_auth/ --check

# Apply to a new branch (creates and checks out the branch)
council apply runs/2025-06-15_143022_fix_broken_auth/ --apply-to fix/auth --yes

# Force apply even with uncommitted changes
council apply runs/2025-06-15_143022_fix_broken_auth/ --yes --force
```

| Flag | Default | Description |
|------|---------|-------------|
| `--apply-to BRANCH` | — | Create a new branch, apply the patch there |
| `--check` | `false` | Dry-run: verify the patch applies cleanly without modifying files |
| `--diff` | `false` | Show syntax-highlighted preview of the patch |
| `--yes` / `-y` | `false` | Skip confirmation prompt |
| `--force` | `false` | Apply even if the working tree has uncommitted changes |

By default, `council apply` refuses to apply patches when the working tree has uncommitted changes. Use `--force` to override this safety check, or commit/stash your changes first.

## Configuration

Council looks for configuration in this order:
1. `--config` CLI flag
2. `.council.yml` in the repo root
3. `council.yml` in the repo root
4. `~/.council.yml` in your home directory
5. Built-in defaults

Run `council init` to generate a starter config from the bundled template.

### Sample `.council.yml`

The built-in defaults use the recommended automation-friendly invocations for both CLIs.
Run `council init` to generate a starter config, or copy this:

```yaml
tools:
  claude:
    description: "Claude Code CLI (headless print mode)"
    command: ["claude"]
    input_mode: "stdin"
    extra_args:
      - "-p"                     # print mode: non-interactive
      # Query argument required by `claude -p "query"`.
      # Council pipes the full prompt via stdin; this constant satisfies
      # the positional arg so the CLI accepts piped input.
      - "Use the piped input as the full task instructions. Produce the best possible answer."
    env: {}                      # do NOT put API keys here

  codex:
    description: "Codex CLI (non-interactive exec)"
    command: ["codex", "exec"]   # exec subcommand is the automation-friendly mode
    input_mode: "stdin"
    extra_args:
      - "--ask-for-approval"
      - "never"                  # prevents interactive approval pauses
      - "--sandbox"
      - "read-only"              # safer default: no file writes by Codex
      - "--color"
      - "never"                  # keeps saved artifacts free of ANSI codes
      # IMPORTANT: "-" MUST be last. It tells Codex to read the prompt from stdin.
      - "-"
    env: {}
```

#### Why these flags?

- **Claude `-p "query"`** (print mode): Runs non-interactively, prints response to stdout. Council pipes the full prompt via stdin and passes a short print-mode query argument. The official CLI pattern is `claude -p "query"`, where piped stdin is processed as additional context.
- **Codex `exec`**: The automation-friendly subcommand (vs the interactive default). Use `codex exec` for scripted/CI-style runs.
- **Codex `--ask-for-approval never`**: Prevents Codex from pausing to ask for user confirmation mid-run, which would hang the pipeline.
- **Codex `--sandbox read-only`**: Safer default — Codex can read your repo but won't write files or run commands.
- **Codex `--color never`**: Disables ANSI color escape codes in stdout, keeping saved run artifacts clean and readable.
- **Codex `-` (last arg)**: This is the PROMPT positional argument. The literal `-` tells Codex to read the prompt from stdin. **It must be the last argument.**

> **Note:** If your config file has a syntax error, council prints a warning and falls back to defaults.

> **Note:** You only need to specify the fields you want to change. Omitted fields for known tools (`claude`, `codex`) inherit their correct defaults — e.g., codex always defaults to `command: ["codex", "exec"]`, not `["claude"]`.

### Input Modes

- **`stdin`** (default): Pipes the prompt to the tool's stdin. Most CLI tools support this.
- **`file`**: Writes the prompt to a temporary file and passes the path via `prompt_file_arg`.

### Adapting to Your CLI Setup

If your `claude` binary is at a custom path:

```yaml
tools:
  claude:
    command: ["/usr/local/bin/claude"]
    extra_args: ["-p", "Use the piped input as the full task instructions. Produce the best possible answer."]
```

If your tool requires file-based input:

```yaml
tools:
  codex:
    command: ["codex", "exec"]
    input_mode: "file"
    prompt_file_arg: "--input"
    extra_args: ["--ask-for-approval", "never", "--sandbox", "read-only", "--color", "never"]
```

## Run Artifacts

Each invocation creates a timestamped folder:

```
runs/2025-06-15_143022_fix_broken_auth/
  manifest.json          # Full metadata: timing, commands, exit codes, context stats
  state.json             # Checkpoint state for resumable runs (round statuses)
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
      # When using --claude-n or --codex-n, additional candidate files:
      # claude_2_stdout.md, claude_2_stderr.txt, etc.
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
    review_checklist.md  # Structured review checklist (when --structured-review)
    review.json          # Machine-readable review output (when --structured-review)
```

Commands in `manifest.json` are automatically redacted: flags containing KEY, TOKEN, SECRET, PASSWORD, or CREDENTIAL (and short flags `-k`, `-t`) have their values replaced with `***REDACTED***`. Tool config `command` and `extra_args` are also redacted in the manifest.

When `--no-save` is active, only the final output (`final/`), a minimal manifest, and `state.json` are retained; intermediate round artifacts are cleaned up.

## Context Gathering

When `--context auto` is set (the default) and you're in a git repo, council automatically collects:

- `git status` output
- Diffs (staged, unstaged, or both depending on `--diff`)
- List of changed files
- Lightweight repo file tree
- Python version and OS info

### File Inclusion Safety

Council **never** includes (unless explicitly `--include`d):
- Binary files
- `.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `secrets*`, `token*`
- Any file under `node_modules/` or `.git/` (nested path detection)

If you explicitly `--include` a sensitive file, council prints a warning but includes it.

### Truncation

- Files exceeding `--max-file-kb` are truncated (head + tail with a `...TRUNCATED...` marker)
- Total context is capped at `--max-context-kb`; items are dropped in priority order:
  1. Repo tree snapshot
  2. Glob-included files
  3. Diff-included files
  4. Diffs (truncated, keeping up to 120 KB per diff when possible, but always respecting the total `--max-context-kb` budget)

## Development

```bash
pip install -e ".[dev]"

# Run tests (all use mocked subprocesses — no real LLM calls)
pytest

# Lint
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).

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

### Multi-candidate generation

Generate multiple candidates per tool and let council pick the best one:

```bash
council fix --claude-n 3 --codex-n 2 "Fix the flaky auth test"
```

### Smart context (auto-include referenced files)

When fixing bugs, council automatically parses tracebacks and file references in the task description to include relevant source files:

```bash
# --smart-context is enabled by default for `fix`
council fix "TypeError in src/auth.py:42 — 'NoneType' has no attribute 'email'"
```

### Structured review output

Get a machine-readable JSON critique with confidence score and categorized findings:

```bash
council review --structured-review --diff staged "Review for security issues"
# Produces review.json and review_checklist.md in the run directory
```

### Resume an interrupted run

```bash
# Resume from where it left off
council resume runs/2025-06-15_143022_fix_broken_auth/

# Only retry failed rounds
council resume --retry-failed runs/2025-06-15_143022_fix_broken_auth/
```

### Apply a patch from a run

```bash
council apply runs/2025-06-15_143022_fix_broken_auth/ --yes
```
