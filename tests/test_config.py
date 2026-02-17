"""Tests for configuration loading, validation, and error handling."""

from __future__ import annotations

from pathlib import Path

from council.config import CouncilConfig, _load_yaml, load_config


class TestLoadYamlErrors:
    def test_invalid_yaml_returns_empty_dict(self, tmp_path: Path):
        """Malformed YAML should return empty dict with warning, not crash."""
        bad_yaml = tmp_path / "bad.yml"
        bad_yaml.write_text("tools:\n  claude:\n    command: [unclosed", encoding="utf-8")
        result = _load_yaml(bad_yaml)
        assert result == {}

    def test_non_dict_yaml_returns_empty_dict(self, tmp_path: Path):
        """YAML that parses to a list (not dict) should return empty dict."""
        list_yaml = tmp_path / "list.yml"
        list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")
        result = _load_yaml(list_yaml)
        assert result == {}


class TestLoadConfigFallback:
    def test_invalid_yaml_falls_back_to_defaults(self, tmp_path: Path):
        """If config file has invalid YAML, load_config should return defaults."""
        bad_yaml = tmp_path / ".council.yml"
        bad_yaml.write_text("{{{{not valid yaml", encoding="utf-8")
        config = load_config(cli_path=bad_yaml)
        assert "claude" in config.tools
        assert "codex" in config.tools

    def test_invalid_tool_config_falls_back_per_tool(self, tmp_path: Path):
        """If one tool has invalid config, others should still load."""
        yaml_content = (
            "tools:\n"
            "  claude:\n"
            "    command: ['claude']\n"
            "    input_mode: 'not_a_valid_mode'\n"
            "  codex:\n"
            "    command: ['codex']\n"
        )
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        config = load_config(cli_path=cfg_file)
        # claude should fall back to default (validation error on input_mode).
        assert "claude" in config.tools
        # codex should load fine from the file.
        assert "codex" in config.tools

    def test_no_config_returns_defaults(self, tmp_path: Path):
        """When no config file exists, defaults are used."""
        config = load_config(repo_root=tmp_path)
        assert "claude" in config.tools
        assert "codex" in config.tools


class TestDefaults:
    def test_defaults_claude_uses_print_mode_with_query(self):
        """Defaults should include -p and a query argument for Claude."""
        config = CouncilConfig.defaults()
        args = config.tools["claude"].extra_args
        assert args[0] == "-p"
        # The query argument must follow -p (official CLI pattern).
        assert len(args) == 2
        assert "piped input" in args[1].lower()
        assert config.tools["claude"].command == ["claude"]

    def test_defaults_codex_uses_exec_mode(self):
        """Defaults should use codex exec with safety flags, --color never, and stdin placeholder."""
        config = CouncilConfig.defaults()
        args = config.tools["codex"].extra_args
        assert config.tools["codex"].command == ["codex", "exec"]
        assert "--sandbox" in args
        assert "read-only" in args
        # codex exec is already non-interactive; --ask-for-approval is not accepted.
        assert "--ask-for-approval" not in args
        # --color never keeps saved artifacts free of ANSI codes.
        color_idx = args.index("--color")
        assert args[color_idx + 1] == "never"
        # "-" must be last (stdin PROMPT placeholder).
        assert args[-1] == "-"


class TestPartialToolConfig:
    """Issue 1: partial tool configs must merge on top of per-tool defaults."""

    def test_codex_partial_keeps_codex_exec_command(self, tmp_path: Path):
        """Codex with only extra_args should still have command=['codex', 'exec']."""
        yaml_content = "tools:\n  codex:\n    extra_args: ['--foo']\n"
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        config = load_config(cli_path=cfg_file)
        assert config.tools["codex"].command == ["codex", "exec"]
        assert config.tools["codex"].extra_args == ["--foo"]

    def test_claude_partial_keeps_claude_command(self, tmp_path: Path):
        """Claude with only extra_args should still have command=['claude']."""
        yaml_content = "tools:\n  claude:\n    extra_args: ['-p']\n"
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        config = load_config(cli_path=cfg_file)
        assert config.tools["claude"].command == ["claude"]
        assert config.tools["claude"].extra_args == ["-p"]

    def test_partial_config_preserves_default_description(self, tmp_path: Path):
        """Partial override should keep the default description."""
        yaml_content = "tools:\n  codex:\n    extra_args: ['--bar']\n"
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        config = load_config(cli_path=cfg_file)
        assert config.tools["codex"].description == "Codex CLI"

    def test_explicit_command_override_respected(self, tmp_path: Path):
        """If user explicitly sets command, it should win over defaults."""
        yaml_content = "tools:\n  codex:\n    command: ['my-codex-wrapper']\n"
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        config = load_config(cli_path=cfg_file)
        assert config.tools["codex"].command == ["my-codex-wrapper"]

    def test_unknown_tool_uses_toolconfig_defaults(self, tmp_path: Path):
        """Unknown tools (not claude/codex) use ToolConfig defaults."""
        yaml_content = "tools:\n  custom_tool:\n    extra_args: ['--custom']\n"
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        config = load_config(cli_path=cfg_file)
        # Unknown tool falls back to ToolConfig defaults (command=["claude"]).
        assert config.tools["custom_tool"].command == ["claude"]
        assert config.tools["custom_tool"].extra_args == ["--custom"]
