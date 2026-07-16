import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB_DIR))

from mcp_launcher import (  # noqa: E402
    apply_claude_selection,
    build_gum_command,
    claude_current_selection,
    codex_override_args,
    discover_claude,
    managed_claude_names,
    merge_preference,
    parse_wrapper_args,
    retain_preference,
)


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
