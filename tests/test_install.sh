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
if [ "$#" -eq 3 ] && [ "$1" = mcp ] && [ "$2" = list ] && [ "$3" = --json ]; then
  printf '%s\n' '[{"name":"deepwiki","enabled":true},{"name":"backlog","enabled":true}]'
  exit 0
fi
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

# Defaults seed unseen folders, while an exact folder keeps the selection it was given.
mkdir -p "$tmp_dir/project-a" "$tmp_dir/project-b"
(cd "$tmp_dir/project-a" && \
  HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" MCP_LAUNCHER_SELECT=deepwiki \
    "$prefix/bin/mcp-launcher" codex --version > "$tmp_dir/project-a-first.log")
grep -q 'mcp_servers.deepwiki.enabled=true' "$tmp_dir/project-a-first.log"
grep -q 'mcp_servers.backlog.enabled=false' "$tmp_dir/project-a-first.log"

HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" MCP_LAUNCHER_SELECT=backlog \
  "$prefix/bin/mcp-launcher" codex --mcp-default > "$tmp_dir/default.log"
! grep -q '^fake-codex:' "$tmp_dir/default.log"

if HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
  "$prefix/bin/mcp-launcher" claude --mcp-use-default --version \
    > "$tmp_dir/missing-default.log" 2> "$tmp_dir/missing-default.err"; then
  echo 'use-default unexpectedly launched without a configured default' >&2
  exit 1
fi
grep -q 'no claude MCP default is configured' "$tmp_dir/missing-default.err"
grep -q 'claude --mcp-default' "$tmp_dir/missing-default.err"
! grep -q '^fake-claude:' "$tmp_dir/missing-default.log"

(cd "$tmp_dir/project-b" && \
  HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
    "$prefix/bin/mcp-launcher" codex --mcp-last --version > "$tmp_dir/project-b.log")
grep -q 'mcp_servers.deepwiki.enabled=false' "$tmp_dir/project-b.log"
grep -q 'mcp_servers.backlog.enabled=true' "$tmp_dir/project-b.log"

(cd "$tmp_dir/project-a" && \
  HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
    "$prefix/bin/mcp-launcher" codex --mcp-last --version > "$tmp_dir/project-a-last.log")
grep -q 'mcp_servers.deepwiki.enabled=true' "$tmp_dir/project-a-last.log"
grep -q 'mcp_servers.backlog.enabled=false' "$tmp_dir/project-a-last.log"

(cd "$tmp_dir/project-a" && \
  HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
    "$prefix/bin/mcp-launcher" codex --mcp-use-default --version \
      > "$tmp_dir/project-a-default.log")
grep -q 'mcp_servers.deepwiki.enabled=false' "$tmp_dir/project-a-default.log"
grep -q 'mcp_servers.backlog.enabled=true' "$tmp_dir/project-a-default.log"
! grep -q -- '--mcp-use-default' "$tmp_dir/project-a-default.log"

(cd "$tmp_dir/project-a" && \
  HOME="$home_dir" PATH="$fake_bin:/usr/bin:/bin" \
    "$prefix/bin/mcp-launcher" codex --mcp-last --version \
      > "$tmp_dir/project-a-after-default.log")
grep -q 'mcp_servers.deepwiki.enabled=false' "$tmp_dir/project-a-after-default.log"
grep -q 'mcp_servers.backlog.enabled=true' "$tmp_dir/project-a-after-default.log"

python3 - "$home_dir/.config/mcp-launcher/state.json" "$tmp_dir/project-a" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1]))
assert state["defaults"]["codex"] == ["backlog"]
assert state["selections"]["codex"][sys.argv[2]] == ["backlog"]
PY

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
