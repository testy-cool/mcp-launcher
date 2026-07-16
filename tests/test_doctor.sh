#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM
fake_bin="$tmp_dir/bin"
mkdir -p "$fake_bin"

cat > "$fake_bin/claude" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$fake_bin/claude"

PATH="$fake_bin:/usr/bin:/bin" make -s -C "$repo_dir" doctor > "$tmp_dir/one-cli.log"
grep -q "claude: $fake_bin/claude" "$tmp_dir/one-cli.log"
grep -q '^codex: missing (wrapper will activate after installation)$' "$tmp_dir/one-cli.log"

rm -f "$fake_bin/claude"
if PATH="$fake_bin:/usr/bin:/bin" make -s -C "$repo_dir" doctor > "$tmp_dir/no-cli.log" 2>&1; then
  echo 'doctor unexpectedly passed without Claude or Codex' >&2
  exit 1
fi
grep -q '^missing: install Claude Code and/or Codex CLI$' "$tmp_dir/no-cli.log"

printf '%s\n' 'doctor smoke test: PASS'
