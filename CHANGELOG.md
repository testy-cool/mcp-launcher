# Changelog

## Unreleased

- Apply saved per-tool MCP defaults to plain Claude and Codex launches without
  a picker, while preserving explicit folder choices for `--mcp-last`.
- Show setup guidance when a default is unset; an intentionally empty default
  disables all discovered MCPs without a hint.
- Keep explicit picker and selection controls available to override defaults.

## 0.2.0 - 2026-08-04

- Restore a resumed Claude or Codex session's first recorded permission state
  for exact/named resumes, Claude `--continue`, and Codex `resume --last`, while
  honoring explicit permission overrides.
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
