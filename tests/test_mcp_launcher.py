import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB_DIR))

import mcp_launcher  # noqa: E402

from mcp_launcher import (  # noqa: E402
    LauncherError,
    apply_claude_selection,
    build_gum_command,
    claude_current_selection,
    codex_override_args,
    configured_default_selection,
    discover_claude,
    load_state,
    managed_claude_names,
    merge_preference,
    parse_wrapper_args,
    remember_default_selection,
    remember_folder_selection,
    retain_preference,
    selection_for_folder,
    show_help,
)


class ResumePermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / "home"
        self.cwd = Path("/work/project")
        self.claude_project = self.home / ".claude" / "projects" / "-work-project"
        self.claude_project.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_claude_exact_resume_restores_original_bypass_mode(self):
        restore_resume_permissions = getattr(
            mcp_launcher, "restore_resume_permissions", None
        )
        self.assertIsNotNone(restore_resume_permissions)
        session_id = "002fdc59-744f-4c85-9d26-a40573d216e0"
        transcript = self.claude_project / f"{session_id}.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "permission-mode",
                            "sessionId": session_id,
                            "permissionMode": "bypassPermissions",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "user",
                            "sessionId": session_id,
                            "cwd": str(self.cwd),
                            "permissionMode": "manual",
                        }
                    ),
                ]
            )
        )

        restored = restore_resume_permissions(
            "claude",
            ["--resume", session_id, "--model", "opus"],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, session_id)
        self.assertEqual(
            restored.args,
            ("--permission-mode", "bypassPermissions"),
        )

    def test_explicit_claude_permission_mode_wins_over_recorded_mode(self):
        session_id = "002fdc59-744f-4c85-9d26-a40573d216e0"
        transcript = self.claude_project / f"{session_id}.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "permission-mode",
                    "sessionId": session_id,
                    "permissionMode": "bypassPermissions",
                }
            )
        )

        restored = mcp_launcher.restore_resume_permissions(
            "claude",
            ["--permission-mode", "plan", "--resume", session_id],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNone(restored)

    def test_claude_continue_restores_latest_session_for_folder(self):
        old_id = "002fdc59-744f-4c85-9d26-a40573d216e1"
        latest_id = "002fdc59-744f-4c85-9d26-a40573d216e2"
        old_transcript = self.claude_project / f"{old_id}.jsonl"
        latest_transcript = self.claude_project / f"{latest_id}.jsonl"
        old_transcript.write_text(
            json.dumps({"sessionId": old_id, "permissionMode": "plan"})
        )
        latest_transcript.write_text(
            json.dumps({"sessionId": latest_id, "permissionMode": "dontAsk"})
        )
        os.utime(old_transcript, (10, 10))
        os.utime(latest_transcript, (20, 20))

        restored = mcp_launcher.restore_resume_permissions(
            "claude",
            ["--continue", "--model", "opus"],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, latest_id)
        self.assertEqual(restored.args, ("--permission-mode", "dontAsk"))

    def test_claude_named_resume_resolves_custom_title(self):
        session_id = "002fdc59-744f-4c85-9d26-a40573d216e3"
        transcript = self.claude_project / f"{session_id}.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"sessionId": session_id, "permissionMode": "default"}
                    ),
                    json.dumps(
                        {
                            "type": "custom-title",
                            "sessionId": session_id,
                            "customTitle": "nightly-refactor",
                        }
                    ),
                ]
            )
        )

        restored = mcp_launcher.restore_resume_permissions(
            "claude",
            ["--resume", "nightly-refactor"],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, session_id)
        self.assertEqual(restored.args, ("--permission-mode", "manual"))

    def test_codex_exact_resume_restores_original_yolo_policy(self):
        session_id = "019fc365-cb2b-77c3-bb45-0e002a982cae"
        session_dir = self.home / ".codex" / "sessions" / "2026" / "08" / "04"
        session_dir.mkdir(parents=True)
        transcript = session_dir / f"rollout-2026-08-04T10-00-00-{session_id}.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": session_id, "cwd": str(self.cwd), "source": "cli"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {
                                "approval_policy": "never",
                                "sandbox_policy": {"type": "danger-full-access"},
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {
                                "approval_policy": "on-request",
                                "sandbox_policy": {"type": "workspace-write"},
                            },
                        }
                    ),
                ]
            )
        )

        restored = mcp_launcher.restore_resume_permissions(
            "codex",
            ["resume", session_id],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, session_id)
        self.assertEqual(
            restored.args,
            ("--dangerously-bypass-approvals-and-sandbox",),
        )

    def test_codex_named_resume_uses_native_session_index(self):
        session_id = "019fc365-cb2b-77c3-bb45-0e002a982cb4"
        session_dir = self.home / ".codex" / "sessions" / "2026" / "08" / "04"
        session_dir.mkdir(parents=True)
        transcript = session_dir / f"rollout-2026-08-04T10-30-00-{session_id}.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": session_id,
                                "cwd": str(self.cwd),
                                "source": "cli",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {
                                "approval_policy": "untrusted",
                                "sandbox_policy": {"type": "read-only"},
                            },
                        }
                    ),
                ]
            )
        )
        (self.home / ".codex" / "session_index.jsonl").write_text(
            json.dumps(
                {
                    "id": session_id,
                    "thread_name": "nightly-refactor",
                    "updated_at": "2026-08-04T10:31:00Z",
                }
            )
        )

        restored = mcp_launcher.restore_resume_permissions(
            "codex",
            ["resume", "nightly-refactor"],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, session_id)
        self.assertEqual(
            restored.args,
            ("--sandbox", "read-only", "--ask-for-approval", "untrusted"),
        )

    def test_codex_exact_resume_restores_sandbox_approval_and_network(self):
        session_id = "019fc365-cb2b-77c3-bb45-0e002a982caf"
        session_dir = self.home / ".codex" / "sessions" / "2026" / "08" / "04"
        session_dir.mkdir(parents=True)
        transcript = session_dir / f"rollout-2026-08-04T11-00-00-{session_id}.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": session_id, "cwd": str(self.cwd), "source": "cli"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {
                                "approval_policy": "on-request",
                                "sandbox_policy": {
                                    "type": "workspace-write",
                                    "network_access": True,
                                },
                            },
                        }
                    ),
                ]
            )
        )

        restored = mcp_launcher.restore_resume_permissions(
            "codex",
            ["resume", session_id],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(
            restored.args,
            (
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "on-request",
                "-c",
                "sandbox_workspace_write.network_access=true",
            ),
        )

    def test_explicit_codex_sandbox_wins_while_approval_is_restored(self):
        session_id = "019fc365-cb2b-77c3-bb45-0e002a982cb0"
        session_dir = self.home / ".codex" / "sessions" / "2026" / "08" / "04"
        session_dir.mkdir(parents=True)
        transcript = session_dir / f"rollout-2026-08-04T12-00-00-{session_id}.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": session_id,
                                "cwd": str(self.cwd),
                                "source": "cli",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {
                                "approval_policy": "never",
                                "sandbox_policy": {"type": "danger-full-access"},
                            },
                        }
                    ),
                ]
            )
        )

        restored = mcp_launcher.restore_resume_permissions(
            "codex",
            ["--sandbox", "read-only", "resume", session_id],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.args, ("--ask-for-approval", "never"))

    def test_codex_last_restores_latest_session_for_folder(self):
        session_dir = self.home / ".codex" / "sessions" / "2026" / "08" / "04"
        session_dir.mkdir(parents=True)

        def write_session(
            session_id: str,
            session_cwd: Path,
            modified: int,
            approval: str,
            sandbox: str,
        ) -> None:
            transcript = session_dir / f"rollout-2026-08-04T13-00-00-{session_id}.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": session_id,
                                    "cwd": str(session_cwd),
                                    "source": "cli",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "turn_context",
                                "payload": {
                                    "approval_policy": approval,
                                    "sandbox_policy": {"type": sandbox},
                                },
                            }
                        ),
                    ]
                )
            )
            os.utime(transcript, (modified, modified))

        old_id = "019fc365-cb2b-77c3-bb45-0e002a982cb1"
        latest_id = "019fc365-cb2b-77c3-bb45-0e002a982cb2"
        other_id = "019fc365-cb2b-77c3-bb45-0e002a982cb3"
        write_session(old_id, self.cwd, 10, "never", "danger-full-access")
        write_session(latest_id, self.cwd, 20, "untrusted", "read-only")
        write_session(other_id, Path("/work/other"), 30, "never", "danger-full-access")

        restored = mcp_launcher.restore_resume_permissions(
            "codex",
            ["resume", "--last", "continue the work"],
            cwd=self.cwd,
            home=self.home,
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.session_id, latest_id)
        self.assertEqual(
            restored.args,
            ("--sandbox", "read-only", "--ask-for-approval", "untrusted"),
        )


class StateTests(unittest.TestCase):
    def test_new_state_has_defaults_and_folder_selections_for_both_tools(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = load_state(Path(temp_dir) / "state.json")

        self.assertEqual(state.get("defaults"), {})
        self.assertEqual(state.get("selections"), {"claude": {}, "codex": {}})

    def test_existing_state_gains_claude_folder_storage_without_losing_codex(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "preference": {"claude": [], "codex": []},
                        "catalog": {"claude": []},
                        "selections": {"codex": {"/work/old": ["deepwiki"]}},
                    }
                )
            )

            state = load_state(state_path)

        self.assertEqual(state["selections"].get("claude"), {})
        self.assertEqual(state["selections"]["codex"]["/work/old"], ["deepwiki"])

    def test_new_folder_starts_from_the_tool_default(self):
        state = {
            "defaults": {"codex": ["backlog", "unavailable"]},
            "selections": {"claude": {}, "codex": {}},
        }

        selected = selection_for_folder(
            state,
            tool="codex",
            cwd_key="/work/new",
            ordered=["deepwiki", "backlog"],
            fallback={"deepwiki"},
        )

        self.assertEqual(selected, {"backlog"})

    def test_remembered_folder_selection_overrides_the_tool_default(self):
        state = {
            "defaults": {"claude": ["backlog"]},
            "selections": {
                "claude": {"/work/project": ["deepwiki", "unavailable"]},
                "codex": {},
            },
        }

        selected = selection_for_folder(
            state,
            tool="claude",
            cwd_key="/work/project",
            ordered=["deepwiki", "backlog"],
            fallback={"backlog"},
        )

        self.assertEqual(selected, {"deepwiki"})

    def test_remembers_selection_under_the_exact_folder(self):
        state = {
            "defaults": {},
            "selections": {
                "claude": {},
                "codex": {"/work/other": ["backlog"]},
            },
        }

        remember_folder_selection(
            state,
            tool="codex",
            cwd_key="/work/project",
            ordered=["backlog", "deepwiki"],
            selected={"deepwiki"},
        )

        self.assertEqual(state["selections"]["codex"]["/work/project"], ["deepwiki"])
        self.assertEqual(state["selections"]["codex"]["/work/other"], ["backlog"])

    def test_remembers_an_empty_default_as_an_explicit_choice(self):
        state = {
            "defaults": {"claude": ["deepwiki"]},
            "selections": {"claude": {}, "codex": {}},
        }

        remember_default_selection(
            state,
            tool="claude",
            ordered=["deepwiki", "backlog"],
            selected=set(),
        )

        self.assertIn("claude", state["defaults"])
        self.assertEqual(state["defaults"]["claude"], [])

    def test_missing_default_means_every_mcp_is_disabled(self):
        try:
            selected = configured_default_selection(
                {"defaults": {}},
                tool="claude",
                ordered=["deepwiki", "backlog"],
            )
        except LauncherError as exc:
            self.fail(f"missing default should select none, not fail: {exc}")

        self.assertEqual(selected, set())


class PreferenceTests(unittest.TestCase):
    def test_preserves_saved_preference_and_appends_new_servers(self):
        self.assertEqual(
            merge_preference(
                ["backlog", "deepwiki", "removed"],
                ["arxiv", "deepwiki", "backlog", "dataforseo"],
            ),
            ["backlog", "deepwiki", "arxiv", "dataforseo"],
        )

    def test_retains_temporarily_unavailable_project_preferences(self):
        self.assertEqual(
            retain_preference(
                ["project-a", "deepwiki"],
                ["deepwiki", "project-b"],
            ),
            ["project-a", "deepwiki", "project-b"],
        )

    def test_only_claude_ai_connectors_enter_the_cross_project_catalog(self):
        self.assertEqual(
            managed_claude_names(["backlog", "claude.ai Gmail", "playwright"]),
            ["claude.ai Gmail"],
        )

    def test_normal_picker_preserves_preference_display_order(self):
        command = build_gum_command(
            gum="/usr/bin/gum",
            ordered=["deepwiki", "backlog", "arxiv"],
            selected={"deepwiki", "arxiv"},
            header="Choose",
            preselect=True,
            selection_order=False,
        )

        self.assertNotIn("--ordered", command)
        self.assertEqual(command[-3:], ["deepwiki", "backlog", "arxiv"])

    def test_reorder_picker_records_toggle_order(self):
        command = build_gum_command(
            gum="/usr/bin/gum",
            ordered=["deepwiki", "backlog", "arxiv"],
            selected=set(),
            header="Reorder",
            preselect=False,
            selection_order=True,
        )

        self.assertIn("--ordered", command)


class WrapperArgumentTests(unittest.TestCase):
    def test_strips_only_wrapper_controls_and_preserves_cli_arguments(self):
        control, passthrough = parse_wrapper_args(
            [
                "--mcp-none",
                "--dangerously-skip-permissions",
                "--model",
                "opus",
                "--",
                "--mcp-all",
            ]
        )

        self.assertEqual(control.mode, "none")
        self.assertFalse(control.refresh)
        self.assertEqual(
            passthrough,
            ["--dangerously-skip-permissions", "--model", "opus", "--", "--mcp-all"],
        )

    def test_refresh_can_be_combined_with_the_normal_picker(self):
        control, passthrough = parse_wrapper_args(["--mcp-refresh", "resume", "--last"])

        self.assertEqual(control.mode, "prompt")
        self.assertTrue(control.refresh)
        self.assertEqual(passthrough, ["resume", "--last"])

    def test_default_control_is_removed_before_launch_arguments(self):
        control, passthrough = parse_wrapper_args(
            ["--mcp-default", "--dangerously-skip-permissions"]
        )

        self.assertEqual(control.mode, "default")
        self.assertEqual(passthrough, ["--dangerously-skip-permissions"])

    def test_use_default_control_is_removed_before_launch_arguments(self):
        control, passthrough = parse_wrapper_args(
            ["--mcp-use-default", "resume", "--last"]
        )

        self.assertEqual(control.mode, "use_default")
        self.assertEqual(passthrough, ["resume", "--last"])

    def test_help_explains_default_and_folder_memory(self):
        output = io.StringIO()

        with redirect_stdout(output):
            show_help()

        self.assertIn("--mcp-default", output.getvalue())
        self.assertIn("--mcp-use-default", output.getvalue())
        self.assertIn("without opening the picker", output.getvalue())
        self.assertIn("all MCPs disabled", output.getvalue())
        self.assertIn("new folders", output.getvalue())
        self.assertIn("per folder", output.getvalue())


class CodexTests(unittest.TestCase):
    def test_builds_launch_scoped_overrides_in_preference_order(self):
        self.assertEqual(
            codex_override_args(
                ["deepwiki", "keywords-everywhere", "arxiv"],
                {"deepwiki", "arxiv"},
            ),
            [
                "-c",
                "mcp_servers.deepwiki.enabled=true",
                "-c",
                "mcp_servers.keywords-everywhere.enabled=false",
                "-c",
                "mcp_servers.arxiv.enabled=true",
            ],
        )

    def test_completes_plugin_mcp_transport_before_disabling_it(self):
        self.assertEqual(
            codex_override_args(
                ["arxiv", "cloudflare-api"],
                {"arxiv"},
                standalone_transports={
                    "cloudflare-api": {
                        "type": "streamable_http",
                        "url": "https://mcp.cloudflare.test/mcp",
                    }
                },
            ),
            [
                "-c",
                "mcp_servers.arxiv.enabled=true",
                "-c",
                'mcp_servers.cloudflare-api.url="https://mcp.cloudflare.test/mcp"',
                "-c",
                "mcp_servers.cloudflare-api.enabled=false",
            ],
        )


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / "home"
        self.cwd = self.home / "Work" / "project"
        self.cwd.mkdir(parents=True)
        (self.home / ".claude").mkdir()

        claude_state = {
            "mcpServers": {
                "deepwiki": {"type": "http", "url": "https://example.test/mcp"},
                "backlog": {"command": "backlog", "args": ["mcp", "start"]},
            },
            "projects": {
                str(self.cwd): {
                    "mcpServers": {"stitch": {"type": "http", "url": "https://stitch.test"}},
                    "disabledMcpServers": ["claude.ai Gmail", "unknown-disabled"],
                }
            },
        }
        self.claude_state_path = self.home / ".claude.json"
        self.claude_state_path.write_text(json.dumps(claude_state))
        self.claude_state_path.chmod(0o600)

        (self.home / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"playwright": {"command": "npx", "args": ["playwright"]}}})
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_discovers_user_project_managed_and_mcp_json_servers(self):
        inventory = discover_claude(
            home=self.home,
            cwd=self.cwd,
            cached_names=["claude.ai Google Drive", "deepwiki"],
        )

        self.assertEqual(
            inventory.names,
            [
                "deepwiki",
                "backlog",
                "stitch",
                "playwright",
                "claude.ai Gmail",
                "unknown-disabled",
                "claude.ai Google Drive",
            ],
        )
        self.assertEqual(inventory.mcp_json_roots["playwright"], [self.home])

    def test_applies_native_claude_disable_lists_without_losing_unknown_entries(self):
        inventory = discover_claude(
            home=self.home,
            cwd=self.cwd,
            cached_names=["claude.ai Gmail"],
        )
        order = [
            "backlog",
            "deepwiki",
            "stitch",
            "claude.ai Gmail",
            "playwright",
            "unknown-disabled",
        ]

        apply_claude_selection(
            home=self.home,
            inventory=inventory,
            order=order,
            selected={"backlog", "claude.ai Gmail"},
        )

        state = json.loads(self.claude_state_path.read_text())
        project = state["projects"][str(self.cwd)]
        self.assertEqual(
            project["disabledMcpServers"],
            ["deepwiki", "stitch", "unknown-disabled"],
        )
        self.assertEqual(project["disabledMcpjsonServers"], ["playwright"])
        self.assertEqual(stat.S_IMODE(self.claude_state_path.stat().st_mode), 0o600)

        local_settings_path = self.home / ".claude" / "settings.local.json"
        local_settings = json.loads(local_settings_path.read_text())
        self.assertEqual(local_settings["disabledMcpjsonServers"], ["playwright"])
        self.assertEqual(stat.S_IMODE(local_settings_path.stat().st_mode), 0o600)
        self.assertNotIn("playwright", claude_current_selection(self.home, inventory))


if __name__ == "__main__":
    unittest.main()
