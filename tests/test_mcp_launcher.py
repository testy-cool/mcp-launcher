import io
import json
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB_DIR))

from mcp_launcher import (  # noqa: E402
    apply_claude_selection,
    build_gum_command,
    claude_current_selection,
    codex_override_args,
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
