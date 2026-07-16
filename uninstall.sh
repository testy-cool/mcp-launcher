#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: ./uninstall.sh [options]

Options:
  --prefix PATH     installation prefix (default: $HOME/.local)
  --shell-rc PATH   shell startup file to clean (default: .zshrc or .bashrc)
  --purge           also delete saved MCP preferences
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
purge=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix)
      [ "$#" -ge 2 ] || { echo "uninstall.sh: --prefix requires a path" >&2; exit 2; }
      prefix=$2
      shift 2
      ;;
    --shell-rc)
      [ "$#" -ge 2 ] || { echo "uninstall.sh: --shell-rc requires a path" >&2; exit 2; }
      shell_rc=$2
      shift 2
      ;;
    --purge)
      purge=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "uninstall.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

install_dir="$prefix/lib/mcp-launcher"
installed_file="$install_dir/mcp_launcher.py"
launcher="$prefix/bin/mcp-launcher"

if [ -L "$launcher" ] && [ "$(readlink "$launcher")" = "$installed_file" ]; then
  rm -f "$launcher"
fi
rm -rf "$install_dir"

if [ -f "$shell_rc" ]; then
  rc_dir=$(dirname -- "$shell_rc")
  rc_mode=$(stat -c '%a' "$shell_rc" 2>/dev/null || stat -f '%Lp' "$shell_rc")
  temporary=$(mktemp "$rc_dir/.mcp-launcher-rc.XXXXXX")
  trap 'rm -f "$temporary"' EXIT HUP INT TERM
  awk '
    $0 == "# >>> mcp-launcher >>>" { skipping = 1; next }
    $0 == "# <<< mcp-launcher <<<" { skipping = 0; next }
    !skipping { print }
  ' "$shell_rc" > "$temporary"
  chmod "$rc_mode" "$temporary"
  mv "$temporary" "$shell_rc"
  trap - EXIT HUP INT TERM
fi

if [ "$purge" -eq 1 ]; then
  rm -rf "$HOME/.config/mcp-launcher"
fi

printf 'Removed mcp-launcher from %s\n' "$prefix"
if [ "$purge" -eq 0 ]; then
  printf 'Kept preferences in %s\n' "$HOME/.config/mcp-launcher"
fi
