# MCP Launcher Maintainer Handoff

## Purpose

This repository provides one dependency-light launcher for Claude Code and Codex CLI. Before either real CLI starts, the launcher lets the user select which discovered MCP servers to enable and keeps the picker in a saved preference order. All ordinary CLI arguments must pass through unchanged.

Keep the project a small Python 3.11+ standard-library program with POSIX shell installation. `gum` is an optional UI enhancement; the numbered text picker is the required fallback. Do not add a package manager or runtime dependency unless the existing design cannot support the feature.

`CLAUDE.md` is a symlink to this file. Maintain the shared instructions here rather than creating divergent agent guidance.

## Repository Map

- `mcp_launcher.py`: executable entry point, discovery, selection UI, default and per-folder state, Claude state updates, and Codex launch overrides.
- `install.sh`: copies the launcher to `<prefix>/lib/mcp-launcher/`, creates `<prefix>/bin/mcp-launcher`, and replaces one marked block in the selected shell RC file.
- `uninstall.sh`: removes only launcher-owned installation artifacts and the marked shell block; preferences survive unless `--purge` is supplied.
- `tests/test_mcp_launcher.py`: unit coverage for argument parsing, discovery, selection, ordering, and state mutation.
- `tests/test_install.sh`: isolated temporary-HOME smoke test for dry-run safety, installation, real-binary discovery, argument passthrough, idempotency, and uninstall.
- `tests/test_doctor.sh`: verifies that `make doctor` accepts either supported CLI and rejects a machine with neither.
- `Makefile`: public operator interface for checks, diagnostics, installation, and removal.
- `VERSION` and `CHANGELOG.md`: release version and user-visible history.

## Runtime Flow

1. Shell functions installed in `.zshrc` or `.bashrc` call `mcp-launcher claude ...` or `mcp-launcher codex ...`.
2. The launcher resolves the real binary from `PATH`, with `MCP_LAUNCHER_REAL_CLAUDE` and `MCP_LAUNCHER_REAL_CODEX` as explicit overrides. It must reject resolution back to itself.
3. Claude MCPs are discovered from `~/.claude.json` plus `.mcp.json` files from the current directory through its ancestors. The selection is applied through Claude's project-scoped disabled-server lists, including `.claude/settings.local.json` for `.mcp.json` servers.
4. Codex MCPs are discovered with `codex mcp list --json` and ordered using `~/.codex/config.toml` when available. Selection is passed only to the launched process through `-c mcp_servers.<name>...` overrides.
5. Picker preferences, per-tool defaults, and remembered selections for both clients live in `~/.config/mcp-launcher/state.json`, written atomically with mode `0600`.

The launcher may cache MCP names, selections, and preference order. It must never copy MCP definitions, OAuth data, credentials, headers, or secrets into its state or process arguments.

## Commands

Run before every commit:

```bash
make check
make doctor
make dry-run
```

Install or update the current checkout:

```bash
make install
source ~/.zshrc
```

For Bash or an isolated test prefix:

```bash
make install SHELL_RC="$HOME/.bashrc"
make install PREFIX=/tmp/mcp-launcher-prefix SHELL_RC=/tmp/mcp-launcher-rc
```

Uninstall conservatively with `make uninstall`; use `./uninstall.sh --purge` only when the user explicitly wants saved preferences deleted.

## Change Rules

- Start launcher behavior changes with a failing test in `tests/test_mcp_launcher.py`.
- Extend `tests/test_install.sh` for installer, shell-block, binary-resolution, or uninstall changes.
- Preserve arbitrary Claude/Codex arguments after removing documented `--mcp-*` launcher controls.
- Preserve shell configuration outside `# >>> mcp-launcher >>>` and `# <<< mcp-launcher <<<` exactly, including file mode.
- Keep install/update idempotent. Refuse to overwrite a non-symlink at the launcher path.
- Preserve unrelated disabled MCP entries when updating Claude configuration.
- Use atomic, locked JSON updates for configuration touched by concurrent sessions.
- Do not hardcode machine-specific paths. Use `HOME`, `CODEX_HOME`, `PREFIX`, `SHELL_RC`, and the documented environment overrides.

## Verification and Releases

`make check` is the minimum verification and must continue to include Python compilation, all unit tests, shell syntax checks, doctor coverage, and the temporary-HOME install/uninstall smoke test.

For changes affecting installed behavior, also reinstall the checkout and exercise the real wrapper path with a non-interactive selector, for example:

```bash
make install
MCP_LAUNCHER_SELECT=none mcp-launcher claude --version
MCP_LAUNCHER_SELECT=none mcp-launcher codex --version
```

Before a release:

1. Update `VERSION` and `CHANGELOG.md`.
2. Run the required checks and real wrapper verification.
3. Verify the documented install flow from a fresh clone.
4. Commit normally, push `main`, create an annotated `vX.Y.Z` tag, and publish the matching GitHub release.

Never amend or force-push a published commit, and never move an existing release tag. Use a follow-up commit and version instead.
