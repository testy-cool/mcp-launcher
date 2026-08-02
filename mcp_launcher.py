#!/usr/bin/env python3
"""Interactive, preference-ordered MCP selection for Claude Code and Codex."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_REAL_BINARIES = {
    "claude": Path.home() / ".local/bin/claude",
    "codex": Path.home() / ".local/share/pnpm/codex",
}
STATE_PATH = Path.home() / ".config/mcp-launcher/state.json"
STATE_VERSION = 1


class LauncherError(RuntimeError):
    pass


@dataclass(frozen=True)
class Control:
    mode: str = "prompt"
    refresh: bool = False


@dataclass(frozen=True)
class ClaudeInventory:
    names: list[str]
    generic_names: list[str]
    mcp_json_roots: dict[str, list[Path]]
    project_key: str


@dataclass(frozen=True)
class CodexInventory:
    names: list[str]
    enabled: set[str]
    standalone_transports: dict[str, dict]


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def merge_preference(preferred: list[str], discovered: list[str]) -> list[str]:
    available = set(discovered)
    return unique([name for name in preferred if name in available] + discovered)


def retain_preference(preferred: list[str], discovered: list[str]) -> list[str]:
    return unique(preferred + discovered)


def managed_claude_names(names: list[str]) -> list[str]:
    return [name for name in names if name.startswith("claude.ai ")]


def parse_wrapper_args(args: list[str]) -> tuple[Control, list[str]]:
    mode = "prompt"
    refresh = False
    passthrough: list[str] = []
    controls = {
        "--mcp-all": "all",
        "--mcp-none": "none",
        "--mcp-last": "last",
        "--mcp-default": "default",
        "--mcp-order": "order",
        "--mcp-help": "help",
    }
    parsing_controls = True

    for arg in args:
        if parsing_controls and arg == "--":
            parsing_controls = False
            passthrough.append(arg)
        elif parsing_controls and arg in controls:
            mode = controls[arg]
        elif parsing_controls and arg == "--mcp-refresh":
            refresh = True
        else:
            passthrough.append(arg)

    return Control(mode=mode, refresh=refresh), passthrough


def codex_override_args(
    ordered: list[str],
    selected: set[str],
    standalone_transports: dict[str, dict] | None = None,
) -> list[str]:
    standalone_transports = standalone_transports or {}
    result: list[str] = []
    for name in ordered:
        if "." in name or any(char in name for char in "\n\r\x00"):
            raise LauncherError(
                f"Codex MCP name {name!r} cannot be represented safely as a dotted CLI override"
            )
        if name in standalone_transports:
            if name in selected:
                continue
            transport = standalone_transports[name]
            transport_type = transport.get("type")
            if transport_type == "streamable_http" and isinstance(transport.get("url"), str):
                value = json.dumps(transport["url"], ensure_ascii=False)
                result.extend(["-c", f"mcp_servers.{name}.url={value}"])
            elif transport_type == "stdio" and isinstance(transport.get("command"), str):
                value = json.dumps(transport["command"], ensure_ascii=False)
                result.extend(["-c", f"mcp_servers.{name}.command={value}"])
            else:
                raise LauncherError(
                    f"Cannot safely disable plugin-contributed Codex MCP {name!r}: "
                    f"unsupported transport {transport_type!r}"
                )
            result.extend(["-c", f"mcp_servers.{name}.enabled=false"])
            continue
        enabled = "true" if name in selected else "false"
        result.extend(["-c", f"mcp_servers.{name}.enabled={enabled}"])
    return result


def read_json(path: Path, default: object | None = None) -> object:
    if not path.exists():
        if default is not None:
            return default
        raise LauncherError(f"Missing JSON file: {path}")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"Cannot read JSON from {path}: {exc}") from exc


def write_json_atomic(path: Path, data: object, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else mode
    compact = False
    trailing_newline = True
    if path.exists():
        try:
            original = path.read_text()
            compact = "\n" not in original.rstrip("\n")
            trailing_newline = original.endswith("\n")
        except OSError:
            pass

    if compact:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
    if trailing_newline:
        payload += "\n"

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, existing_mode)
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_state(path: Path = STATE_PATH) -> dict:
    default = {
        "version": STATE_VERSION,
        "preference": {"claude": [], "codex": []},
        "catalog": {"claude": []},
        "defaults": {},
        "selections": {"claude": {}, "codex": {}},
    }
    if not path.exists():
        return default
    data = read_json(path)
    if not isinstance(data, dict):
        raise LauncherError(f"Launcher state must be a JSON object: {path}")
    if data.get("version") != STATE_VERSION:
        raise LauncherError(f"Unsupported launcher state version in {path}")
    for key, value in default.items():
        data.setdefault(key, value)
    selections = data.get("selections")
    if not isinstance(selections, dict):
        raise LauncherError(f"Launcher selections must be a JSON object: {path}")
    selections.setdefault("claude", {})
    selections.setdefault("codex", {})
    return data


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    write_json_atomic(path, state, mode=0o600)


def selection_for_folder(
    state: dict,
    tool: str,
    cwd_key: str,
    ordered: list[str],
    fallback: set[str],
) -> set[str]:
    available = set(ordered)
    selections = state.get("selections", {})
    tool_selections = selections.get(tool, {}) if isinstance(selections, dict) else {}
    saved = tool_selections.get(cwd_key) if isinstance(tool_selections, dict) else None
    if isinstance(saved, list):
        return set(saved) & available
    default = state.get("defaults", {}).get(tool)
    if isinstance(default, list):
        return set(default) & available
    return fallback & available


def remember_folder_selection(
    state: dict,
    tool: str,
    cwd_key: str,
    ordered: list[str],
    selected: set[str],
) -> None:
    tool_selections = state.setdefault("selections", {}).setdefault(tool, {})
    tool_selections[cwd_key] = [name for name in ordered if name in selected]


def remember_default_selection(
    state: dict,
    tool: str,
    ordered: list[str],
    selected: set[str],
) -> None:
    state.setdefault("defaults", {})[tool] = [
        name for name in ordered if name in selected
    ]


def mapping_names(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [str(name) for name, config in value.items() if isinstance(config, dict)]


def ancestor_paths(cwd: Path) -> list[Path]:
    current = cwd.resolve()
    result = [current]
    while current.parent != current:
        current = current.parent
        result.append(current)
    return result


def discover_claude(home: Path, cwd: Path, cached_names: list[str]) -> ClaudeInventory:
    state_path = home / ".claude.json"
    state = read_json(state_path, default={})
    if not isinstance(state, dict):
        raise LauncherError(f"Claude state must be a JSON object: {state_path}")

    project_key = str(cwd.resolve())
    projects = state.get("projects", {})
    project = projects.get(project_key, {}) if isinstance(projects, dict) else {}
    if not isinstance(project, dict):
        project = {}

    configured_generic_names = mapping_names(state.get("mcpServers"))
    configured_generic_names += mapping_names(project.get("mcpServers"))

    mcp_json_roots: dict[str, list[Path]] = {}
    mcp_json_names: list[str] = []
    for root in ancestor_paths(cwd):
        config_path = root / ".mcp.json"
        if not config_path.exists():
            continue
        config = read_json(config_path)
        if not isinstance(config, dict):
            continue
        servers = config.get("mcpServers", config)
        for name in mapping_names(servers):
            mcp_json_names.append(name)
            mcp_json_roots.setdefault(name, []).append(root)

    disabled = project.get("disabledMcpServers", [])
    disabled_names: list[str] = []
    if isinstance(disabled, list):
        disabled_names = [str(name) for name in disabled if isinstance(name, str)]

    cached_generic_names = [name for name in cached_names if name not in mcp_json_roots]
    generic_names = unique(configured_generic_names + disabled_names + cached_generic_names)
    names = unique(
        configured_generic_names + mcp_json_names + disabled_names + cached_generic_names
    )
    return ClaudeInventory(
        names=names,
        generic_names=generic_names,
        mcp_json_roots=mcp_json_roots,
        project_key=project_key,
    )


def update_json_locked(path: Path, updater) -> None:
    lock_path = path.with_name(f".{path.name}.mcp-launcher.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(path, default={})
        if not isinstance(data, dict):
            raise LauncherError(f"JSON root must be an object: {path}")
        before = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        updated = updater(data)
        after = json.dumps(updated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if after != before or not path.exists():
            write_json_atomic(path, updated, mode=0o600)


def apply_claude_selection(
    home: Path,
    inventory: ClaudeInventory,
    order: list[str],
    selected: set[str],
) -> None:
    state_path = home / ".claude.json"
    generic = set(inventory.generic_names)
    mcp_json = set(inventory.mcp_json_roots)

    def update_state(state: dict) -> dict:
        projects = state.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise LauncherError(f"Claude projects state is not an object: {state_path}")
        project = projects.setdefault(inventory.project_key, {})
        if not isinstance(project, dict):
            raise LauncherError(f"Claude project state is not an object: {inventory.project_key}")
        existing = project.get("disabledMcpServers", [])
        existing_list = [str(name) for name in existing] if isinstance(existing, list) else []
        disabled = [name for name in order if name in generic and name not in selected]
        disabled += [name for name in existing_list if name not in generic]
        project["disabledMcpServers"] = unique(disabled)

        existing_json = project.get("disabledMcpjsonServers", [])
        existing_json_list = (
            [str(name) for name in existing_json] if isinstance(existing_json, list) else []
        )
        disabled_json = [name for name in order if name in mcp_json and name not in selected]
        disabled_json += [name for name in existing_json_list if name not in mcp_json]
        project["disabledMcpjsonServers"] = unique(disabled_json)
        return state

    update_json_locked(state_path, update_state)

    roots: dict[Path, list[str]] = {}
    for name, name_roots in inventory.mcp_json_roots.items():
        for root in name_roots:
            roots.setdefault(root, []).append(name)

    for root, names in roots.items():
        settings_path = root / ".claude" / "settings.local.json"
        relevant = set(names)

        def update_settings(settings: dict, relevant=relevant) -> dict:
            existing = settings.get("disabledMcpjsonServers", [])
            existing_list = [str(name) for name in existing] if isinstance(existing, list) else []
            disabled = [name for name in order if name in relevant and name not in selected]
            disabled += [name for name in existing_list if name not in relevant]
            settings["disabledMcpjsonServers"] = unique(disabled)
            return settings

        update_json_locked(settings_path, update_settings)


def claude_current_selection(home: Path, inventory: ClaudeInventory) -> set[str]:
    state = read_json(home / ".claude.json", default={})
    project: dict = {}
    if isinstance(state, dict) and isinstance(state.get("projects"), dict):
        candidate = state["projects"].get(inventory.project_key, {})
        if isinstance(candidate, dict):
            project = candidate
    generic_disabled_value = project.get("disabledMcpServers", [])
    generic_disabled = (
        {str(name) for name in generic_disabled_value}
        if isinstance(generic_disabled_value, list)
        else set()
    )
    mcp_json_disabled_value = project.get("disabledMcpjsonServers", [])
    mcp_json_disabled = (
        {str(name) for name in mcp_json_disabled_value}
        if isinstance(mcp_json_disabled_value, list)
        else set()
    )
    selected = {name for name in inventory.generic_names if name not in generic_disabled}

    for name, roots in inventory.mcp_json_roots.items():
        disabled = name in mcp_json_disabled
        for root in roots:
            settings = read_json(root / ".claude/settings.local.json", default={})
            disabled_value = settings.get("disabledMcpjsonServers", []) if isinstance(settings, dict) else []
            if isinstance(disabled_value, list) and name in disabled_value:
                disabled = True
        if not disabled:
            selected.add(name)
    return selected


def real_binary(tool: str) -> Path:
    override = os.environ.get(f"MCP_LAUNCHER_REAL_{tool.upper()}")
    if override:
        path = Path(override).expanduser()
        if not path.exists():
            raise LauncherError(f"Configured real {tool} binary not found: {path}")
    else:
        discovered = shutil.which(tool)
        path = Path(discovered) if discovered else DEFAULT_REAL_BINARIES[tool]
    try:
        launcher = Path(__file__).resolve()
        if path.resolve() == launcher:
            raise LauncherError(f"Real {tool} binary resolves back to the MCP launcher")
    except OSError:
        pass
    if not path.exists():
        raise LauncherError(f"Real {tool} binary not found: {path}")
    return path


def discover_codex(binary: Path) -> CodexInventory:
    completed = subprocess.run(
        [str(binary), "mcp", "list", "--json"],
        text=True,
        capture_output=True,
        env=os.environ,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise LauncherError(f"Cannot discover Codex MCPs: {detail}")
    try:
        entries = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LauncherError(f"Codex returned invalid MCP JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise LauncherError("Codex MCP list was not a JSON array")

    listed_names = [
        str(entry["name"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]
    enabled = {
        str(entry["name"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("enabled") is True
    }

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    config_path = codex_home / "config.toml"
    configured_order: list[str] = []
    if config_path.exists():
        try:
            with config_path.open("rb") as handle:
                config = tomllib.load(handle)
            servers = config.get("mcp_servers", {})
            if isinstance(servers, dict):
                configured_order = [str(name) for name in servers]
        except (OSError, tomllib.TOMLDecodeError):
            pass

    configured_set = set(configured_order)
    standalone_transports: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("name") in configured_set:
            continue
        name = entry.get("name")
        transport = entry.get("transport")
        if isinstance(name, str) and isinstance(transport, dict):
            standalone_transports[name] = transport

    return CodexInventory(
        names=unique([name for name in configured_order if name in listed_names] + listed_names),
        enabled=enabled,
        standalone_transports=standalone_transports,
    )


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def refresh_claude_catalog(binary: Path) -> list[str]:
    completed = subprocess.run(
        [str(binary), "mcp", "list"],
        text=True,
        capture_output=True,
        env=os.environ,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise LauncherError(f"Cannot refresh Claude MCPs: {detail}")
    names: list[str] = []
    for raw_line in completed.stdout.splitlines():
        line = ANSI_ESCAPE.sub("", raw_line).strip()
        if not line or line.startswith("Checking MCP"):
            continue
        match = re.match(r"(.+?):\s", line)
        if match:
            names.append(match.group(1))
    return unique(names)


def build_gum_command(
    gum: str,
    ordered: list[str],
    selected: set[str],
    header: str,
    preselect: bool = True,
    selection_order: bool = False,
) -> list[str]:
    command = [
        gum,
        "choose",
        "--no-limit",
        f"--height={min(max(len(ordered) + 2, 8), 24)}",
        f"--header={header}",
    ]
    if selection_order:
        command.append("--ordered")
    if preselect and selected:
        command.append(f"--selected={','.join(name for name in ordered if name in selected)}")
    command.extend(ordered)
    return command


def gum_choose(
    ordered: list[str],
    selected: set[str],
    header: str,
    preselect: bool = True,
    selection_order: bool = False,
) -> list[str] | None:
    gum = shutil.which("gum")
    if not gum:
        return text_choose(ordered, selected, header)
    try:
        tty_in = open("/dev/tty", "r")
        tty_err = open("/dev/tty", "w")
    except OSError:
        return None
    command = build_gum_command(
        gum,
        ordered,
        selected,
        header,
        preselect=preselect,
        selection_order=selection_order,
    )
    with tty_in, tty_err:
        completed = subprocess.run(
            command,
            stdin=tty_in,
            stdout=subprocess.PIPE,
            stderr=tty_err,
            text=True,
        )
    if completed.returncode != 0:
        raise LauncherError("MCP selection cancelled")
    return [line for line in completed.stdout.splitlines() if line]


def text_choose(ordered: list[str], selected: set[str], header: str) -> list[str] | None:
    try:
        tty = open("/dev/tty", "r+")
    except OSError:
        return None
    with tty:
        tty.write(f"{header}\n")
        for index, name in enumerate(ordered, 1):
            marker = "x" if name in selected else " "
            tty.write(f"  {index:>2}. [{marker}] {name}\n")
        tty.write("Numbers to enable, comma-separated; Enter keeps current: ")
        tty.flush()
        answer = tty.readline().strip()
    if not answer:
        return [name for name in ordered if name in selected]
    try:
        indexes = {int(value.strip()) for value in answer.split(",") if value.strip()}
    except ValueError as exc:
        raise LauncherError("Selection must contain comma-separated numbers") from exc
    return [name for index, name in enumerate(ordered, 1) if index in indexes]


def selection_from_environment(ordered: list[str]) -> set[str] | None:
    value = os.environ.get("MCP_LAUNCHER_SELECT")
    if value is None:
        return None
    if value == "all":
        return set(ordered)
    if value in {"", "none"}:
        return set()
    requested = {name.strip() for name in value.split(",") if name.strip()}
    unknown = requested - set(ordered)
    if unknown:
        raise LauncherError(f"Unknown MCP selection: {', '.join(sorted(unknown))}")
    return requested


def choose_selection(
    control: Control,
    ordered: list[str],
    current: set[str],
    tool: str,
) -> set[str]:
    environment_selection = selection_from_environment(ordered)
    if environment_selection is not None:
        return environment_selection
    if control.mode == "all":
        return set(ordered)
    if control.mode == "none":
        return set()
    if control.mode == "last":
        return current
    label = f"{tool} default" if control.mode == "default" else tool
    result = gum_choose(
        ordered,
        current,
        f"{label}: Space toggles MCPs · Enter saves · sorted by saved preference"
        if control.mode == "default"
        else f"{label}: Space toggles MCPs · Enter launches · sorted by saved preference",
    )
    if result is None:
        print(f"mcp-launcher: no TTY; reusing last {tool} selection", file=sys.stderr)
        return current
    return set(result)


def update_preference(tool: str, ordered: list[str], state: dict) -> None:
    picked = gum_choose(
        ordered,
        set(),
        f"{tool}: select MCPs in preference order · unselected entries stay last",
        preselect=False,
        selection_order=True,
    )
    if picked is None:
        raise LauncherError("A TTY is required to reorder MCPs")
    existing = state["preference"].get(tool, [])
    unavailable = [name for name in existing if name not in set(ordered)]
    state["preference"][tool] = unique(picked + ordered + unavailable)
    save_state(state)
    print(f"{tool} MCP preference: {', '.join(state['preference'][tool])}")


def show_help() -> None:
    print(
        """MCP launcher controls (removed before invoking Claude/Codex):
  --mcp-all       enable every discovered MCP without opening the picker
  --mcp-none      disable every discovered MCP without opening the picker
  --mcp-last      reuse the folder/default selection without opening the picker
  --mcp-default   set the selection used for new folders, then exit
  --mcp-order     set persistent picker/preference order, then exit
  --mcp-refresh   refresh Claude.ai-managed connector discovery before picking
  --mcp-help      show this help

Normal invocations open the picker and remember the chosen selection per folder.
All other arguments are forwarded unchanged.
For automation, MCP_LAUNCHER_SELECT accepts all, none, or comma-separated names.
"""
    )


def report_selection(tool: str, ordered: list[str], selected: set[str]) -> None:
    active = [name for name in ordered if name in selected]
    value = ", ".join(active) if active else "none"
    print(f"{tool} MCPs ({len(active)}/{len(ordered)}): {value}", file=sys.stderr)


def run_claude(control: Control, passthrough: list[str], state: dict) -> None:
    home = Path.home()
    binary = real_binary("claude")
    cached = state["catalog"].get("claude", [])
    if control.refresh:
        cached = managed_claude_names(refresh_claude_catalog(binary))
    inventory = discover_claude(home=home, cwd=Path.cwd(), cached_names=cached)
    state["catalog"]["claude"] = managed_claude_names(inventory.names)
    saved_preference = state["preference"].get("claude", [])
    ordered = merge_preference(saved_preference, inventory.names)
    state["preference"]["claude"] = retain_preference(saved_preference, inventory.names)
    save_state(state)

    if control.mode == "order":
        update_preference("claude", ordered, state)
        return

    cwd_key = inventory.project_key
    current = selection_for_folder(
        state,
        tool="claude",
        cwd_key=cwd_key,
        ordered=ordered,
        fallback=claude_current_selection(home, inventory),
    )
    selected = choose_selection(control, ordered, current, "claude")
    if control.mode == "default":
        remember_default_selection(state, "claude", ordered, selected)
        save_state(state)
        report_selection("claude default", ordered, selected)
        return
    apply_claude_selection(home, inventory, ordered, selected)
    remember_folder_selection(state, "claude", cwd_key, ordered, selected)
    save_state(state)
    report_selection("claude", ordered, selected)
    os.execvpe(str(binary), [str(binary), *passthrough], os.environ)


def run_codex(control: Control, passthrough: list[str], state: dict) -> None:
    binary = real_binary("codex")
    inventory = discover_codex(binary)
    saved_preference = state["preference"].get("codex", [])
    ordered = merge_preference(saved_preference, inventory.names)
    state["preference"]["codex"] = retain_preference(saved_preference, inventory.names)
    cwd_key = str(Path.cwd().resolve())
    current = selection_for_folder(
        state,
        tool="codex",
        cwd_key=cwd_key,
        ordered=ordered,
        fallback=inventory.enabled,
    )
    save_state(state)

    if control.mode == "order":
        update_preference("codex", ordered, state)
        return

    selected = choose_selection(control, ordered, current, "codex")
    if control.mode == "default":
        remember_default_selection(state, "codex", ordered, selected)
        save_state(state)
        report_selection("codex default", ordered, selected)
        return
    remember_folder_selection(state, "codex", cwd_key, ordered, selected)
    save_state(state)
    report_selection("codex", ordered, selected)
    overrides = codex_override_args(ordered, selected, inventory.standalone_transports)
    os.execvpe(str(binary), [str(binary), *overrides, *passthrough], os.environ)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"claude", "codex"}:
        print("Usage: mcp-launcher {claude|codex} [MCP controls] [CLI arguments...]", file=sys.stderr)
        return 2
    tool = arguments.pop(0)
    control, passthrough = parse_wrapper_args(arguments)
    if control.mode == "help":
        show_help()
        return 0

    try:
        state = load_state()
        if tool == "claude":
            run_claude(control, passthrough, state)
        else:
            run_codex(control, passthrough, state)
    except LauncherError as exc:
        print(f"mcp-launcher: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
