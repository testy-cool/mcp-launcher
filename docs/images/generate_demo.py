#!/usr/bin/env python3
"""Record the real picker in Kitty with fake servers and an isolated home.

Linux recording tools: Kitty, Xvfb, xdotool, ffmpeg, and gum.
These are documentation tools, not launcher runtime dependencies.
"""
import json
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent


def main():
    binaries = {}
    for name in ("kitty", "Xvfb", "xdotool", "ffmpeg", "gum", "bash"):
        binaries[name] = shutil.which(name)
        if not binaries[name]:
            raise SystemExit(f"Missing recording tool: {name}")
    processes = []
    with tempfile.TemporaryDirectory(prefix="mcp-terminal-recording-") as folder:
        home = Path(folder)
        bin_dir = home / "bin"
        bin_dir.mkdir()
        (bin_dir / "gum").symlink_to(binaries["gum"])
        client = bin_dir / "demo-client"
        client.write_text('#!/bin/sh\nprintf "Demo client started (no account connected).\\n"\n')
        client.chmod(0o700)
        wrapper = bin_dir / "claude"
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
            f"{shlex.quote(str(ROOT / 'mcp_launcher.py'))} claude \"$@\"\n"
        )
        wrapper.chmod(0o700)
        (home / ".claude.json").write_text(json.dumps({
            "mcpServers": {name: {"type": "http", "url": "https://example.test/mcp"}
                           for name in ("deepwiki", "backlog", "browser")}
        }))
        state_path = home / ".config/mcp-launcher/state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps({"version": 1, "defaults": {"claude": []}}))
        # An allowlist keeps real credentials, profiles, and shell setup out.
        env = {"HOME": folder, "PATH": f"{bin_dir}:/usr/bin:/bin",
               "MCP_LAUNCHER_REAL_CLAUDE": str(client),
               "TERM": "xterm-256color", "LANG": "C.UTF-8",
               "PS1": "$ ", "LIBGL_ALWAYS_SOFTWARE": "1"}
        log = open(home / "recording.log", "w")
        try:
            display = subprocess.Popen(
                [binaries["Xvfb"], "-displayfd", "1", "-screen", "0", "1120x500x24", "-nolisten", "tcp"],
                stdout=subprocess.PIPE, stderr=log, text=True, env=env,
            )
            processes.append(display)
            env["DISPLAY"] = ":" + display.stdout.readline().strip()
            terminal = subprocess.Popen([
                binaries["kitty"], "--config", "NONE",
                "-o", "linux_display_server=x11", "-o", "font_size=18",
                "-o", "initial_window_width=1120", "-o", "initial_window_height=500",
                "-o", "remember_window_size=no", "-o", "window_padding_width=16",
                "-o", "background=#1e1e2e", "-o", "foreground=#cdd6f4",
                "-o", "cursor_blink_interval=0", "-o", "enable_audio_bell=no",
                binaries["bash"], "--noprofile", "--norc", "-i",
            ], env=env, cwd=folder, stdout=log, stderr=log)
            processes.append(terminal)
            time.sleep(2)

            def keys(*args):
                subprocess.run([binaries["xdotool"], *args], env=env, check=True, timeout=10)

            def type_command(text):
                keys("type", "--clearmodifiers", "--delay", "65", text)
                keys("key", "Return")

            keys("search", "--sync", "--class", "kitty", "windowfocus")
            type_command("# Demo: 3 mock MCP servers; no account connected")
            video = home / "demo.mkv"
            recorder = subprocess.Popen([
                binaries["ffmpeg"], "-y", "-loglevel", "error", "-f", "x11grab",
                "-video_size", "1120x500", "-framerate", "12", "-i", env["DISPLAY"],
                "-c:v", "ffv1", str(video),
            ], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            processes.append(recorder)
            time.sleep(1)
            type_command("claude --mcp-default")
            time.sleep(2)
            keys("key", "space")
            time.sleep(2)
            subprocess.run([
                binaries["ffmpeg"], "-y", "-loglevel", "error", "-f", "x11grab",
                "-video_size", "1120x500", "-i", env["DISPLAY"], "-frames:v", "1",
                str(OUTPUT / "picker.png"),
            ], env=env, check=True, stdout=log, stderr=log)
            keys("key", "Return")
            time.sleep(1)
            assert json.loads(state_path.read_text())["defaults"]["claude"] == ["deepwiki"]
            type_command("claude")
            time.sleep(4)
            recorder.send_signal(signal.SIGINT)
            recorder.wait(timeout=10)
            subprocess.run([
                binaries["ffmpeg"], "-y", "-loglevel", "error", "-i", str(video),
                "-filter_complex", "split[a][b];[a]palettegen[p];[b][p]paletteuse",
                "-loop", "0", str(OUTPUT / "default-demo.gif"),
            ], check=True, stdout=log, stderr=log)
            assert folder not in json.loads(state_path.read_text())["selections"]["claude"]
            print("Recorded real Kitty/gum interaction; verified saved DeepWiki default.")
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            log.close()


if __name__ == "__main__":
    main()
