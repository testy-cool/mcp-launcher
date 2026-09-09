#!/usr/bin/env python3
"""MCP selection and permission-preserving resume for Claude Code and Codex."""

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
SECRETS_RESOLVED_ENV = "MCP_LAUNCHER_SECRETS_RESOLVED"
SERVICE_ACCOUNT_TOKEN_ENV = "OP_SERVICE_ACCOUNT_TOKEN"
BIOMETRIC_UNLOCK_ENV = "OP_BIOMETRIC_UNLOCK_ENABLED"
KEYRING_ATTRIBUTES = (
    "application",
    "mcp-launcher",
    "credential",
    "op-service-account",
)


class LauncherError(RuntimeError):
    pass


@dataclass(frozen=True)
class Control:
    mode: str = "passthrough"
    refresh: bool = False


@dataclass(frozen=True)
class PermissionRestore:
    session_id: str
    args: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class SecretReexec:
    binary: Path
    arguments: tuple[str, ...]
    environment: dict[str, str]


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
    mode = "use_default"
    refresh = False
    passthrough: list[str] = []
    controls = {
        "--mcp-launcher": "prompt",
        "--mcp-all": "all",
        "--mcp-none": "none",
        "--mcp-last": "last",
        "--mcp-default": "default",
        "--mcp-use-default": "use_default",
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
            if mode == "use_default":
                mode = "prompt"
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


def configured_default_selection(state: dict, tool: str, ordered: list[str]) -> set[str]:
    defaults = state.get("defaults", {})
    default = defaults.get(tool) if isinstance(defaults, dict) else None
    if not isinstance(default, list):
        return set()
    return set(default) & set(ordered)


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


def args_before_separator(args: list[str]) -> list[str]:
    return args[: args.index("--")] if "--" in args else args


def iter_json_lines(path: Path) -> Iterable[dict]:
    try:
        with path.open() as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


def claude_project_directory(home: Path, cwd: Path) -> Path:
    project_name = re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))
    return home / ".claude" / "projects" / project_name


def claude_resume_transcript(
    controls: list[str], home: Path, cwd: Path
) -> Path | None:
    projects_root = home / ".claude" / "projects"
    current_project = claude_project_directory(home, cwd)
    if any(arg in {"--continue", "-c"} for arg in controls):
        candidates = list(current_project.glob("*.jsonl"))
        return max(candidates, key=lambda path: path.stat().st_mtime, default=None)

    reference: str | None = None
    for index, arg in enumerate(controls):
        if arg.startswith("--resume="):
            reference = arg.split("=", 1)[1]
            break
        if arg in {"--resume", "-r"} and index + 1 < len(controls):
            candidate = controls[index + 1]
            if candidate and not candidate.startswith("-"):
                reference = candidate
            break
    if not reference:
        return None

    candidates = list(projects_root.glob("*/*.jsonl"))
    for transcript in candidates:
        if transcript.stem == reference:
            return transcript

    def candidate_rank(path: Path) -> tuple[bool, float]:
        return path.parent == current_project, path.stat().st_mtime

    for transcript in sorted(candidates, key=candidate_rank, reverse=True):
        for event in iter_json_lines(transcript):
            if (
                event.get("type") == "custom-title"
                and event.get("customTitle") == reference
            ):
                return transcript
    return None


def restore_claude_resume_permissions(
    passthrough: list[str], cwd: Path, home: Path
) -> PermissionRestore | None:
    controls = args_before_separator(passthrough)
    if any(
        arg == "--dangerously-skip-permissions"
        or arg == "--permission-mode"
        or arg.startswith("--permission-mode=")
        for arg in controls
    ):
        return None
    transcript = claude_resume_transcript(controls, home, cwd)
    if transcript is None:
        return None

    mode: str | None = None
    session_id = transcript.stem
    for event in iter_json_lines(transcript):
        candidate = event.get("permissionMode")
        if isinstance(candidate, str):
            mode = candidate
            break
    if mode == "default":
        mode = "manual"
    valid_modes = {
        "acceptEdits",
        "auto",
        "bypassPermissions",
        "dontAsk",
        "manual",
        "plan",
    }
    if mode not in valid_modes:
        return None
    return PermissionRestore(
        session_id=session_id,
        args=("--permission-mode", mode),
        description=mode,
    )


def codex_session_files(codex_home: Path) -> list[Path]:
    return list((codex_home / "sessions").glob("*/*/*/*.jsonl"))


def codex_session_metadata(transcript: Path) -> dict:
    for event in iter_json_lines(transcript):
        if event.get("type") == "session_meta" and isinstance(
            event.get("payload"), dict
        ):
            return event["payload"]
    return {}


def codex_effective_cwd(controls: list[str], cwd: Path) -> Path:
    for index, arg in enumerate(controls):
        value: str | None = None
        if arg.startswith("--cd="):
            value = arg.split("=", 1)[1]
        elif arg in {"--cd", "-C"} and index + 1 < len(controls):
            value = controls[index + 1]
        if value:
            requested = Path(value)
            if requested.is_absolute():
                return requested.resolve()
            return (cwd / requested).resolve()
    return cwd.resolve()


def codex_resume_reference(controls: list[str], resume_index: int) -> str | None:
    value_options = {
        "--add-dir",
        "--ask-for-approval",
        "--cd",
        "--color",
        "--config",
        "--disable",
        "--enable",
        "--image",
        "--model",
        "--oss-provider",
        "--profile",
        "--sandbox",
        "-C",
        "-a",
        "-c",
        "-i",
        "-m",
        "-p",
        "-s",
    }
    index = resume_index + 1
    while index < len(controls):
        arg = controls[index]
        if arg in value_options:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg
    return None


def codex_transcript_for_resume(
    controls: list[str], resume_index: int, codex_home: Path, cwd: Path
) -> Path | None:
    candidates = codex_session_files(codex_home)
    reference = (
        None
        if "--last" in controls
        else codex_resume_reference(controls, resume_index)
    )
    if reference:
        for transcript in candidates:
            if transcript.name.endswith(f"-{reference}.jsonl"):
                return transcript
        for transcript in candidates:
            metadata = codex_session_metadata(transcript)
            if metadata.get("id") == reference or metadata.get("session_id") == reference:
                return transcript

        index_path = codex_home / "session_index.jsonl"
        named_session_id: str | None = None
        for entry in iter_json_lines(index_path):
            if entry.get("thread_name") == reference and isinstance(
                entry.get("id"), str
            ):
                named_session_id = entry["id"]
        if named_session_id:
            for transcript in candidates:
                metadata = codex_session_metadata(transcript)
                if named_session_id in {metadata.get("id"), metadata.get("session_id")}:
                    return transcript
        return None

    if "--last" not in controls:
        return None
    include_all_folders = "--all" in controls
    include_non_interactive = "--include-non-interactive" in controls
    effective_cwd = codex_effective_cwd(controls, cwd)
    matching: list[Path] = []
    for transcript in candidates:
        metadata = codex_session_metadata(transcript)
        if not include_non_interactive and metadata.get("source") not in {None, "cli"}:
            continue
        session_cwd = metadata.get("cwd")
        if not include_all_folders and (
            not isinstance(session_cwd, str)
            or Path(session_cwd).resolve() != effective_cwd
        ):
            continue
        matching.append(transcript)
    return max(matching, key=lambda path: path.stat().st_mtime, default=None)


def codex_explicit_permissions(controls: list[str]) -> tuple[bool, bool, bool]:
    if any(
        arg in {
            "--dangerously-bypass-approvals-and-sandbox",
            "--full-auto",
            "--yolo",
        }
        for arg in controls
    ):
        return True, True, True

    sandbox = any(
        arg in {"--sandbox", "-s"} or arg.startswith("--sandbox=")
        for arg in controls
    )
    approval = any(
        arg in {"--ask-for-approval", "-a"}
        or arg.startswith("--ask-for-approval=")
        for arg in controls
    )
    network = False
    for index, arg in enumerate(controls):
        config: str | None = None
        if arg.startswith("--config="):
            config = arg.split("=", 1)[1]
        elif arg in {"--config", "-c"} and index + 1 < len(controls):
            config = controls[index + 1]
        if not config:
            continue
        key = config.split("=", 1)[0].strip()
        sandbox = sandbox or key in {"sandbox_mode", "sandbox_policy"}
        approval = approval or key == "approval_policy"
        network = network or key == "sandbox_workspace_write.network_access"
    return sandbox, approval, network


def restore_codex_resume_permissions(
    passthrough: list[str], cwd: Path, home: Path
) -> PermissionRestore | None:
    controls = args_before_separator(passthrough)
    try:
        resume_index = controls.index("resume")
    except ValueError:
        return None
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    transcript = codex_transcript_for_resume(
        controls, resume_index, codex_home, cwd
    )
    if transcript is None:
        return None

    approval_policy: str | None = None
    sandbox_mode: str | None = None
    network_access: bool | None = None
    for event in iter_json_lines(transcript):
        if event.get("type") != "turn_context":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        approval = payload.get("approval_policy")
        sandbox = payload.get("sandbox_policy")
        if isinstance(approval, str):
            approval_policy = approval
        if isinstance(sandbox, dict) and isinstance(sandbox.get("type"), str):
            sandbox_mode = sandbox["type"]
            if isinstance(sandbox.get("network_access"), bool):
                network_access = sandbox["network_access"]
        break

    metadata = codex_session_metadata(transcript)
    session_id = metadata.get("id") or metadata.get("session_id") or transcript.stem
    if not isinstance(session_id, str):
        return None
    explicit_sandbox, explicit_approval, explicit_network = codex_explicit_permissions(
        controls
    )
    if explicit_sandbox and explicit_approval:
        return None
    if (
        not explicit_sandbox
        and not explicit_approval
        and approval_policy == "never"
        and sandbox_mode == "danger-full-access"
    ):
        return PermissionRestore(
            session_id=session_id,
            args=("--dangerously-bypass-approvals-and-sandbox",),
            description="approval=never, sandbox=danger-full-access",
        )

    permission_args: list[str] = []
    if not explicit_sandbox and sandbox_mode in {
        "read-only",
        "workspace-write",
        "danger-full-access",
    }:
        permission_args.extend(["--sandbox", sandbox_mode])
    if not explicit_approval and approval_policy in {"untrusted", "on-request", "never"}:
        permission_args.extend(["--ask-for-approval", approval_policy])
    if (
        not explicit_sandbox
        and not explicit_network
        and sandbox_mode == "workspace-write"
        and network_access is not None
    ):
        enabled = "true" if network_access else "false"
        permission_args.extend(
            ["-c", f"sandbox_workspace_write.network_access={enabled}"]
        )
    if not permission_args:
        return None
    return PermissionRestore(
        session_id=session_id,
        args=tuple(permission_args),
        description=f"approval={approval_policy}, sandbox={sandbox_mode}",
    )


def restore_resume_permissions(
    tool: str,
    passthrough: list[str],
    cwd: Path,
    home: Path,
) -> PermissionRestore | None:
    if tool == "claude":
        return restore_claude_resume_permissions(passthrough, cwd, home)
    if tool == "codex":
        return restore_codex_resume_permissions(passthrough, cwd, home)
    return None


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


def load_service_account_token(
    *,
    gdbus_binary: Path | None = None,
    secret_tool_binary: Path | None = None,
    runner=None,
) -> str:
    runner = subprocess.run if runner is None else runner
    if gdbus_binary is None:
        discovered = shutil.which("gdbus")
        if not discovered:
            raise LauncherError("gdbus not found; refusing interactive 1Password fallback")
        gdbus_binary = Path(discovered)
    if secret_tool_binary is None:
        discovered = shutil.which("secret-tool")
        if not discovered:
            raise LauncherError(
                "secret-tool not found; refusing interactive 1Password fallback"
            )
        secret_tool_binary = Path(discovered)

    try:
        status = runner(
            [
                str(gdbus_binary),
                "call",
                "--session",
                "--dest",
                "org.freedesktop.secrets",
                "--object-path",
                "/org/freedesktop/secrets/aliases/default",
                "--method",
                "org.freedesktop.DBus.Properties.Get",
                "org.freedesktop.Secret.Collection",
                "Locked",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise LauncherError(f"cannot check the system keyring: {exc}") from exc
    if status.returncode != 0 or "<false>" not in status.stdout:
        if status.returncode == 0 and "<true>" in status.stdout:
            raise LauncherError(
                "system keyring is locked; refusing interactive 1Password fallback"
            )
        raise LauncherError(
            "cannot confirm that the system keyring is unlocked; "
            "refusing interactive 1Password fallback"
        )

    try:
        lookup = runner(
            [str(secret_tool_binary), "lookup", *KEYRING_ATTRIBUTES],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise LauncherError(f"cannot read the system keyring: {exc}") from exc
    token = lookup.stdout.strip()
    if (
        lookup.returncode != 0
        or not token.startswith("ops_")
        or "\n" in token
        or "\r" in token
    ):
        raise LauncherError(
            "system keyring has an invalid service account token or no token; "
            "refusing interactive 1Password fallback"
        )
    return token


def project_env_file(cwd: Path) -> Path | None:
    directory = cwd.resolve()
    candidates = (directory, *directory.parents)
    repository_root = next(
        (candidate for candidate in candidates if (candidate / ".git").exists()),
        None,
    )
    if repository_root is None:
        env_file = directory / ".env.op"
        return env_file if env_file.is_file() else None

    for candidate in candidates:
        env_file = candidate / ".env.op"
        if env_file.is_file():
            return env_file
        if candidate == repository_root:
            break
    return None


def project_secret_reexec(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    op_binary: Path | None = None,
    launcher_path: Path | None = None,
    token_loader=load_service_account_token,
) -> SecretReexec | None:
    cwd = Path.cwd() if cwd is None else cwd
    environment = os.environ if environment is None else environment
    environment[BIOMETRIC_UNLOCK_ENV] = "false"

    if environment.pop(SECRETS_RESOLVED_ENV, None) == "1":
        environment.pop(SERVICE_ACCOUNT_TOKEN_ENV, None)
        return None

    env_file = project_env_file(cwd)
    if env_file is None:
        return None

    if op_binary is None:
        discovered = shutil.which("op")
        if not discovered:
            raise LauncherError("1Password CLI not found; refusing interactive fallback")
        op_binary = Path(discovered)
    if launcher_path is None:
        launcher_path = Path(__file__).resolve()

    token = token_loader()
    child_environment = dict(environment)
    for name in list(child_environment):
        if name in {"OP_CONNECT_HOST", "OP_CONNECT_TOKEN"} or name.startswith(
            "OP_SESSION_"
        ):
            child_environment.pop(name)
    child_environment[SERVICE_ACCOUNT_TOKEN_ENV] = token
    child_environment[BIOMETRIC_UNLOCK_ENV] = "false"
    child_environment[SECRETS_RESOLVED_ENV] = "1"
    command = (
        str(op_binary),
        "run",
        f"--env-file={env_file}",
        "--no-masking",
        "--",
        str(launcher_path),
        *arguments,
    )
    return SecretReexec(
        binary=op_binary,
        arguments=command,
        environment=child_environment,
    )


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
  --mcp-launcher  open the MCP picker, remember the selection, then launch
  --mcp-all       enable every discovered MCP without opening the picker
  --mcp-none      disable every discovered MCP without opening the picker
  --mcp-last      reuse the folder/default selection without opening the picker
  --mcp-default   set the selection used for new folders, then exit
  --mcp-use-default
                   launch with the saved default without opening the picker;
                   an unset default means all MCPs disabled
  --mcp-order     set persistent picker/preference order, then exit
  --mcp-refresh   refresh Claude.ai-managed connector discovery before picking
  --mcp-help      show this help

Plain claude and codex invocations use the saved tool default without a picker.
Use --mcp-launcher to open the picker and remember its selection per folder.
All other arguments are forwarded unchanged.
Exact/named resumes, Claude --continue, and Codex resume --last restore the
session's first recorded permission state unless explicit permission flags win.
For automation, MCP_LAUNCHER_SELECT overrides the selection and accepts all, none, or
comma-separated names.
"""
    )


def report_selection(tool: str, ordered: list[str], selected: set[str]) -> None:
    active = [name for name in ordered if name in selected]
    value = ", ".join(active) if active else "none"
    print(f"{tool} MCPs ({len(active)}/{len(ordered)}): {value}", file=sys.stderr)


def report_permission_restore(tool: str, restore: PermissionRestore) -> None:
    print(
        f"{tool} permissions ({restore.session_id}): {restore.description}",
        file=sys.stderr,
    )


def run_passthrough(tool: str, passthrough: list[str]) -> None:
    home = Path.home()
    cwd = Path.cwd()
    binary = real_binary(tool)
    restore = restore_resume_permissions(tool, passthrough, cwd, home)
    restore_args = restore.args if restore else ()
    if restore:
        report_permission_restore(tool, restore)
    os.execvpe(str(binary), [str(binary), *restore_args, *passthrough], os.environ)


def run_claude(control: Control, passthrough: list[str], state: dict) -> None:
    home = Path.home()
    cwd = Path.cwd()
    binary = real_binary("claude")
    cached = state["catalog"].get("claude", [])
    if control.refresh:
        cached = managed_claude_names(refresh_claude_catalog(binary))
    inventory = discover_claude(home=home, cwd=cwd, cached_names=cached)
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
    selected = (
        configured_default_selection(state, "claude", ordered)
        if control.mode == "use_default" and "MCP_LAUNCHER_SELECT" not in os.environ
        else choose_selection(control, ordered, current, "claude")
    )
    if control.mode == "default":
        remember_default_selection(state, "claude", ordered, selected)
        save_state(state)
        report_selection("claude default", ordered, selected)
        return
    apply_claude_selection(home, inventory, ordered, selected)
    remember_folder_selection(state, "claude", cwd_key, ordered, selected)
    save_state(state)
    report_selection("claude", ordered, selected)
    restore = restore_resume_permissions("claude", passthrough, cwd, home)
    restore_args = restore.args if restore else ()
    if restore:
        report_permission_restore("claude", restore)
    os.execvpe(str(binary), [str(binary), *restore_args, *passthrough], os.environ)


def run_codex(control: Control, passthrough: list[str], state: dict) -> None:
    home = Path.home()
    cwd = Path.cwd()
    binary = real_binary("codex")
    inventory = discover_codex(binary)
    saved_preference = state["preference"].get("codex", [])
    ordered = merge_preference(saved_preference, inventory.names)
    state["preference"]["codex"] = retain_preference(saved_preference, inventory.names)
    cwd_key = str(cwd.resolve())
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

    selected = (
        configured_default_selection(state, "codex", ordered)
        if control.mode == "use_default" and "MCP_LAUNCHER_SELECT" not in os.environ
        else choose_selection(control, ordered, current, "codex")
    )
    if control.mode == "default":
        remember_default_selection(state, "codex", ordered, selected)
        save_state(state)
        report_selection("codex default", ordered, selected)
        return
    remember_folder_selection(state, "codex", cwd_key, ordered, selected)
    save_state(state)
    report_selection("codex", ordered, selected)
    overrides = codex_override_args(ordered, selected, inventory.standalone_transports)
    restore = restore_resume_permissions("codex", passthrough, cwd, home)
    restore_args = restore.args if restore else ()
    if restore:
        report_permission_restore("codex", restore)
    os.execvpe(
        str(binary),
        [str(binary), *overrides, *restore_args, *passthrough],
        os.environ,
    )


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
        secret_reexec = project_secret_reexec([tool, *arguments])
        if secret_reexec is not None:
            os.execvpe(
                str(secret_reexec.binary),
                list(secret_reexec.arguments),
                secret_reexec.environment,
            )
            return 0
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
