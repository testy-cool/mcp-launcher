# MCP Launcher

Choose which MCP servers Claude Code and Codex CLI start, and resume sessions
with the permissions they originally used.

Running `claude` or `codex` automatically applies the saved tool default without a picker.
With no saved default, all discovered MCPs are disabled. Add
`--mcp-launcher` when you want the MCP checklist:

```bash
claude --mcp-launcher
codex --mcp-launcher --yolo
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

Choose your default MCPs once for each client you use:

```bash
claude --mcp-default
codex --mcp-default
```

These defaults apply to every plain launch. After updating, existing native or
folder selections are not automatically imported as defaults. Until you set a
default, plain launches disable all discovered MCPs and show a setup hint.
Saving an empty default intentionally disables all MCPs without that hint.

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

Run either CLI normally to apply its saved default without a picker:

```bash
claude
codex --yolo
```

Open the picker only when you want to manage MCPs:

```bash
claude --mcp-launcher
codex --mcp-launcher
```

Press Space to enable or disable an MCP, then Enter to launch. The launcher
remembers that selection for the exact folder you launched from and always
displays MCPs in your saved preference order. A folder without a remembered
selection starts from the tool's default when one has been set.

Launcher-only controls are removed before the real CLI receives its arguments:

```bash
claude --mcp-launcher    # open the MCP picker, remember, then launch
codex --mcp-launcher

claude --mcp-order       # set the preferred MCP order
codex --mcp-order

claude --mcp-default     # pick the default for every plain launch, then exit
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
MCP_LAUNCHER_SELECT=none codex --mcp-launcher --version
MCP_LAUNCHER_SELECT=deepwiki,backlog claude --mcp-launcher --version
```

## Project secrets without prompts

When a repository contains `.env.op`, the launcher resolves its 1Password
references before starting Claude or Codex. Store a read-only 1Password service
account token in the unlocked GNOME keyring once:

```bash
secret-tool store --label="1Password service account - Claude and Codex" \
  application mcp-launcher credential op-service-account
```

Then add reference-only variables to the repository:

```dotenv
SERVICE_TOKEN="op://API Keys/example/credential"
```

Projects using this feature require `op`, `gdbus`, and `secret-tool`. The
launcher refuses to invoke 1Password when the keyring is locked or unavailable,
so it cannot fall back to a desktop authorization prompt. It also disables
biometric unlock and removes the service-account token before starting the
agent; only the secrets named in `.env.op` reach that process. Nested folders
inherit the nearest `.env.op` within their Git repository.

## Resume with original permissions

Use the CLIs' normal resume commands:

```bash
claude --resume <session-id-or-name>
claude --continue
codex resume <session-id-or-name>
codex resume --last
```

For an exact or named resume, the launcher finds the native Claude/Codex
transcript. For `claude --continue` and `codex resume --last`, it finds the most
recent native session for the current folder. It then passes the first recorded
permission mode back to the real CLI. Codex sandboxed sessions also recover the
recorded approval policy and workspace network setting when available.

Permission flags supplied on the resume command take precedence. For Codex,
that precedence is per setting: an explicit sandbox can still be paired with
the session's original approval policy. A bare resume command that opens the
CLI's interactive session picker is left untouched because the selected session
is not known until after the launcher has exited.

## How selection works

- Plain `claude` and `codex` invocations apply the saved tool default without replacing remembered folder selections. Use `--mcp-last` to reuse a folder choice.
  Permission-preserving resume still applies when resuming a native session.
- `--mcp-launcher` opts into the picker. Any explicit `--mcp-*` selection
  control overrides the automatic default without requiring the picker flag.
- Both tools remember selected MCP names by exact resolved working directory. With `--mcp-launcher` or `--mcp-last`, a remembered folder selection takes precedence over the tool default; without either, the launcher's previous native/current behavior is preserved.
- `--mcp-use-default` bypasses the picker and any remembered folder choice, launches with the saved tool default, and remembers that choice for the folder. If no default has been configured, it launches with every discovered MCP disabled.
- Claude also applies the choice through its native per-project `disabledMcpServers` and `disabledMcpjsonServers` state. Claude.ai connectors remain available in the picker, and running sessions are not modified.
- Codex receives launch-scoped `mcp_servers.<name>.enabled` overrides. Plugin-contributed MCPs are supported without disabling the rest of their plugin.
- MCP definitions, OAuth data, headers, credentials, transcripts, and permission
  metadata are never copied into launcher state. Resume metadata is read directly
  from the CLIs' native files at launch time.
- Defaults, per-folder selections, and picker preferences live in `~/.config/mcp-launcher/state.json` with mode `0600`.

To bypass the installed shell functions, including if MCP discovery fails:

```bash
command claude
command codex
```

This starts the native CLI with its native settings, without launcher MCP
selection or permission recovery. Native help/version commands also work this way.

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

The install smoke test uses a temporary HOME and fake Claude/Codex binaries. It
verifies dry-run safety, PATH-based binary discovery, idempotent installation,
plain-command bypass, explicit picker activation, argument passthrough,
original-permission resume, default and per-folder selection precedence, clean
uninstall, and preference preservation.
