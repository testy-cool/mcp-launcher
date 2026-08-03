# MCP Launcher

Choose which MCP servers Claude Code and Codex CLI start for each session.

Running `claude` or `codex` opens a checklist before the real CLI starts. Your normal arguments still pass through unchanged:

```bash
claude --dangerously-skip-permissions
codex resume --last
```

This is useful when every agent session would otherwise start its own copy of resource-heavy MCP servers.

## Install

Requirements:

- Python 3.11 or newer
- Claude Code and/or Codex CLI available on `PATH`
- Zsh or Bash
- [`gum`](https://github.com/charmbracelet/gum) is optional; without it, the launcher uses a plain numbered picker

Clone the repo and run the checked installer:

```bash
git clone https://github.com/testy-cool/mcp-launcher.git
cd mcp-launcher
make doctor
make install
source ~/.zshrc
```

For Bash, install into `.bashrc` instead:

```bash
make install SHELL_RC="$HOME/.bashrc"
source ~/.bashrc
```

The installer places the launcher under `~/.local`, adds small `claude` and `codex` functions to your shell startup file, and preserves everything outside its marked block. Running it again updates the launcher without duplicating the shell configuration.

Preview every change without writing anything:

```bash
make dry-run
```

## Use

Run either CLI normally:

```bash
claude
codex --yolo
```

Press Space to enable or disable an MCP, then Enter to launch. The launcher remembers that selection for the exact folder you launched from and always displays MCPs in your saved preference order. A folder without a remembered selection starts from the tool's default when one has been set.

Launcher-only controls are removed before the real CLI receives its arguments:

```bash
claude --mcp-order       # set the preferred MCP order
codex --mcp-order

claude --mcp-default     # pick the default for new folders, then exit
codex --mcp-default

claude --mcp-use-default # launch with the saved default, without a picker
codex --mcp-use-default

claude --mcp-last        # reuse this folder's selection, or the default
claude --mcp-none        # launch with no discovered MCPs
claude --mcp-all         # launch with every discovered MCP
claude --mcp-refresh     # refresh Claude.ai-managed connectors
claude --mcp-help        # show all launcher controls
```

For scripts and automation:

```bash
MCP_LAUNCHER_SELECT=none codex --version
MCP_LAUNCHER_SELECT=deepwiki,backlog claude --version
```

## How selection works

- Both tools remember selected MCP names by exact resolved working directory. A remembered folder selection takes precedence over the tool default; without either, the launcher's previous native/current behavior is preserved.
- `--mcp-use-default` bypasses the picker and any remembered folder choice, launches with the saved tool default, and remembers that choice for the folder. It reports an error if no default has been configured.
- Claude also applies the choice through its native per-project `disabledMcpServers` and `disabledMcpjsonServers` state. Claude.ai connectors remain available in the picker, and running sessions are not modified.
- Codex receives launch-scoped `mcp_servers.<name>.enabled` overrides. Plugin-contributed MCPs are supported without disabling the rest of their plugin.
- MCP definitions, OAuth data, headers, and credentials are never copied into the launcher state.
- Defaults, per-folder selections, and picker preferences live in `~/.config/mcp-launcher/state.json` with mode `0600`.

## Update or uninstall

Update from the checkout:

```bash
git pull --ff-only
make install
```

Uninstall while keeping preferences:

```bash
make uninstall
```

Remove preferences too:

```bash
./uninstall.sh --purge
```

## Custom installation paths

```bash
make install PREFIX="$HOME/.local" SHELL_RC="$HOME/.zshrc"
```

The equivalent direct command is:

```bash
./install.sh --prefix "$HOME/.local" --shell-rc "$HOME/.zshrc"
```

## Development

```bash
make check    # unit tests, shell syntax, and isolated install smoke test
make doctor   # verify Python, Claude, Codex, and optional gum
make dry-run  # preview installation
```

The install smoke test uses a temporary HOME and fake Claude/Codex binaries. It verifies dry-run safety, PATH-based binary discovery, idempotent installation, argument passthrough, default and per-folder selection precedence, clean uninstall, and preference preservation.
