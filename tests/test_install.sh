#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

home_dir="$tmp_dir/home"
prefix="$home_dir/.local"
shell_rc="$home_dir/.zshrc"
fake_bin="$tmp_dir/fake-bin"
mkdir -p "$home_dir" "$fake_bin"

printf '%s\n' '# existing shell config' 'export KEEP_ME=yes' > "$shell_rc"
cp "$shell_rc" "$tmp_dir/original.zshrc"

cat > "$fake_bin/claude" <<'EOF'
#!/bin/sh
printf 'fake-claude:%s\n' "$*"
EOF

cat > "$fake_bin/codex" <<'EOF'
#!/bin/sh
printf 'fake-codex:%s\n' "$*"
EOF
chmod +x "$fake_bin/claude" "$fake_bin/codex"

HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
  "$repo_dir/install.sh" --dry-run --prefix "$prefix" --shell-rc "$shell_rc" \
  > "$tmp_dir/dry-run.log"
test ! -e "$prefix/bin/mcp-launcher"
cmp "$shell_rc" "$tmp_dir/original.zshrc"

HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
  "$repo_dir/install.sh" --prefix "$prefix" --shell-rc "$shell_rc"

test -x "$prefix/lib/mcp-launcher/mcp_launcher.py"
test -L "$prefix/bin/mcp-launcher"
test "$(readlink "$prefix/bin/mcp-launcher")" = "$prefix/lib/mcp-launcher/mcp_launcher.py"
test "$(grep -c '^# >>> mcp-launcher >>>$' "$shell_rc")" = 1
grep -q '^export KEEP_ME=yes$' "$shell_rc"

HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" MCP_LAUNCHER_SELECT=none \
  "$prefix/bin/mcp-launcher" claude --version > "$tmp_dir/launch.log"
grep -q '^fake-claude:--version$' "$tmp_dir/launch.log"

# Installation is idempotent and never duplicates the shell block.
HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
  "$repo_dir/install.sh" --prefix "$prefix" --shell-rc "$shell_rc"
test "$(grep -c '^# >>> mcp-launcher >>>$' "$shell_rc")" = 1

HOME="$home_dir" "$repo_dir/uninstall.sh" --prefix "$prefix" --shell-rc "$shell_rc"
test ! -e "$prefix/bin/mcp-launcher"
test ! -e "$prefix/lib/mcp-launcher"
! grep -q '^# >>> mcp-launcher >>>$' "$shell_rc"
grep -q '^export KEEP_ME=yes$' "$shell_rc"
test -f "$home_dir/.config/mcp-launcher/state.json"

printf '%s\n' 'install smoke test: PASS'
