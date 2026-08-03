# Changelog

## Unreleased

- Add per-tool MCP defaults for folders without a saved selection.
- Remember selections by exact working directory for both Claude Code and Codex CLI.
- Add `--mcp-use-default` to launch directly with a saved default without opening the picker, falling back to all MCPs disabled when unset.

## 0.1.1 - 2026-07-16

- Let `make doctor` pass when either Claude Code or Codex CLI is installed.

## 0.1.0 - 2026-07-16

- Add interactive MCP selection for Claude Code and Codex CLI.
- Add persistent preference ordering and per-directory selections.
- Support Claude.ai connectors, project `.mcp.json` servers, and Codex plugin MCPs.
- Add idempotent Bash/Zsh installation, dry-run, doctor, and uninstall flows.
