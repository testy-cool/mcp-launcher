#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --prefix PATH     install under PATH (default: $HOME/.local)
  --shell-rc PATH   shell startup file to update (default: .zshrc or .bashrc)
  --dry-run         print planned changes without writing
  -h, --help        show this help
EOF
}

default_shell_rc() {
  case "${SHELL:-}" in
    */bash) printf '%s\n' "$HOME/.bashrc" ;;
    *) printf '%s\n' "$HOME/.zshrc" ;;
  esac
}

prefix=${PREFIX:-"$HOME/.local"}
shell_rc=${MCP_LAUNCHER_SHELL_RC:-$(default_shell_rc)}
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix)
      [ "$#" -ge 2 ] || { echo "install.sh: --prefix requires a path" >&2; exit 2; }
      prefix=$2
      shift 2
      ;;
    --shell-rc)
      [ "$#" -ge 2 ] || { echo "install.sh: --shell-rc requires a path" >&2; exit 2; }
      shell_rc=$2
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "install.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_file="$repo_dir/mcp_launcher.py"
install_dir="$prefix/lib/mcp-launcher"
installed_file="$install_dir/mcp_launcher.py"
bin_dir="$prefix/bin"
launcher="$bin_dir/mcp-launcher"

[ -f "$source_file" ] || { echo "install.sh: missing $source_file" >&2; exit 1; }

if [ "$dry_run" -eq 1 ]; then
  printf 'Would install %s\n' "$installed_file"
  printf 'Would link %s -> %s\n' "$launcher" "$installed_file"
  printf 'Would update %s with Claude/Codex shell functions\n' "$shell_rc"
  exit 0
fi

if [ -e "$launcher" ] && [ ! -L "$launcher" ]; then
  echo "install.sh: refusing to replace non-symlink $launcher" >&2
  exit 1
fi

install -d -m 755 "$install_dir" "$bin_dir"
install -m 755 "$source_file" "$installed_file"
rm -f "$launcher"
ln -s "$installed_file" "$launcher"

rc_dir=$(dirname -- "$shell_rc")
mkdir -p "$rc_dir"
[ -e "$shell_rc" ] || : > "$shell_rc"
rc_mode=$(stat -c '%a' "$shell_rc" 2>/dev/null || stat -f '%Lp' "$shell_rc")
temporary=$(mktemp "$rc_dir/.mcp-launcher-rc.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM

awk '
  $0 == "# >>> mcp-launcher >>>" { skipping = 1; next }
  $0 == "# <<< mcp-launcher <<<" { skipping = 0; next }
  !skipping { print }
' "$shell_rc" > "$temporary"

if [ -s "$temporary" ]; then
  printf '\n' >> "$temporary"
fi
{
  printf '%s\n' '# >>> mcp-launcher >>>'
  printf '%s\n' '# Interactive MCP selection for Claude Code and Codex CLI.'
  printf 'claude() {\n  "%s" claude "$@"\n}\n\n' "$launcher"
  printf 'codex() {\n  "%s" codex "$@"\n}\n' "$launcher"
  printf '%s\n' '# <<< mcp-launcher <<<'
} >> "$temporary"

chmod "$rc_mode" "$temporary"
mv "$temporary" "$shell_rc"
trap - EXIT HUP INT TERM

printf 'Installed mcp-launcher at %s\n' "$launcher"
printf 'Updated %s\n' "$shell_rc"
printf 'Open a new shell or run: source %s\n' "$shell_rc"
