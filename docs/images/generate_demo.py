#!/usr/bin/env python3
"""Render a README example using the real launcher and isolated mock data."""
import html
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def capture():
    with tempfile.TemporaryDirectory(prefix="mcp-launcher-demo-") as folder:
        home = Path(folder)
        client = home / "demo-claude"
        client.write_text('#!/bin/sh\nprintf "Demo client started.\\n"\n')
        client.chmod(0o700)
        (home / ".claude.json").write_text(json.dumps({
            "mcpServers": {name: {"type": "http", "url": "https://example.test/mcp"}
                           for name in ("deepwiki", "backlog", "browser")}
        }))
        # Do not inherit credentials, client configuration, or launcher controls.
        env = {"HOME": folder, "PATH": "/usr/bin:/bin",
               "MCP_LAUNCHER_REAL_CLAUDE": str(client)}
        base = [sys.executable, str(ROOT / "mcp_launcher.py"), "claude"]
        saved = subprocess.run(
            [*base, "--mcp-default"], cwd=folder,
            env={**env, "MCP_LAUNCHER_SELECT": "deepwiki"},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True,
        ).stdout.strip()
        launched = subprocess.run(
            base, cwd=folder, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=True,
        ).stdout.strip().splitlines()
        assert saved == "claude default MCPs (1/3): deepwiki", saved
        assert launched == ["claude MCPs (1/3): deepwiki", "Demo client started."], launched
        state = json.loads((home / ".config/mcp-launcher/state.json").read_text())
        assert state["defaults"]["claude"] == ["deepwiki"]
        return saved, launched


def main():
    saved, launched = capture()
    lines = [
        (32, 46, "title", "Choose once. Run as usual."),
        (32, 78, "note", "Example with 3 mock MCP servers and a demo client"),
        (32, 130, "note", "1. Save DeepWiki as the default"),
        (32, 165, "command", "$ MCP_LAUNCHER_SELECT=deepwiki \\"),
        (32, 195, "command", "  claude --mcp-default"),
        (32, 228, "result", saved),
        (32, 286, "note", "2. Launch normally — no extra flag"),
        (32, 321, "command", "$ claude"),
        (32, 354, "result", launched[0]),
        (32, 384, "note", launched[1]),
        (32, 438, "note", "Only DeepWiki is selected. The other 2 MCPs stay off."),
    ]
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="472" viewBox="0 0 720 472" role="img" aria-labelledby="title desc">
<title id="title">Save DeepWiki once, then launch Claude normally</title>
<desc id="desc">Mock-data example from the real MCP Launcher. Save DeepWiki as the only default among three servers. A plain Claude launch selects only DeepWiki and starts a demo client.</desc>
<style>
text { font-family: monospace; font-size: 20px; }
.title { fill: #cdd6f4; font-family: sans-serif; font-size: 26px; font-weight: 600; }
.note { fill: #bac2de; font-family: sans-serif; font-size: 20px; }
.command { fill: #cdd6f4; }
.result { fill: #a6e3a1; }
</style>
<rect width="720" height="472" rx="12" fill="#1e1e2e"/>
'''
    for x, y, kind, value in lines:
        svg += f'<text x="{x}" y="{y}" class="{kind}" xml:space="preserve">{html.escape(value)}</text>\n'
    svg += '</svg>\n'
    target = Path(__file__).with_name("default-demo.svg")
    target.write_text(svg)
    print(f"Generated {target.name}; mock default and plain-launch checks passed.")


if __name__ == "__main__":
    main()
