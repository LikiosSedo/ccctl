"""resume / new commands — open sessions in new terminal windows."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

from ccctl.sources import check_alive, lookup_session_project, read_sessions
from ccctl.store import load_names


def _detect_terminal() -> str:
    term = os.environ.get("TERM_PROGRAM", "")
    if "iTerm" in term:
        return "iterm"
    if "Apple_Terminal" in term:
        return "terminal"
    if os.environ.get("TMUX"):
        return "tmux"
    return "terminal"


def _applescript_str(s: str) -> str:
    """Escape a string for embedding in AppleScript double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _open_in_new_window(cmd: str, cwd: str, title: str = ""):
    """Open a shell command in a new terminal window/tab."""
    terminal = _detect_terminal()
    full_cmd = f"cd {shlex.quote(cwd)} && {cmd}"
    escaped = _applescript_str(full_cmd)

    if terminal == "iterm":
        apple_script = f'''
            tell application "iTerm2"
                tell current window
                    create tab with default profile
                    tell current session
                        write text "{escaped}"
                    end tell
                end tell
                activate
            end tell
        '''
        subprocess.run(["osascript", "-e", apple_script], check=True)
        where = "iTerm2 tab"
    elif terminal == "tmux":
        subprocess.run(
            ["tmux", "new-window", "-n", title or "claude", full_cmd],
            check=True,
        )
        where = "tmux window"
    else:
        apple_script = f'''
            tell application "Terminal"
                do script "{escaped}"
                activate
            end tell
        '''
        subprocess.run(["osascript", "-e", apple_script], check=True)
        where = "Terminal window"

    return where


def _find_session(sessions: list[dict], ccctl_names: dict[str, str], query: str) -> dict | None:
    """Find session by PID, name, or session ID prefix.

    Searches live sessions first, then falls back to ccctl name store
    (for sessions that were stopped and whose files Claude Code cleaned up).
    """
    # Live: by PID
    for s in sessions:
        if str(s.get("pid")) == query:
            return s
    # Live: by ccctl name
    for s in sessions:
        sid = s.get("sessionId", "")
        if ccctl_names.get(sid) == query:
            return s
    # Live: by native name
    for s in sessions:
        if s.get("name") == query:
            return s
    # Live: by session ID prefix
    for s in sessions:
        if s.get("sessionId", "").startswith(query):
            return s
    # Name store fallback — session file gone, but we can recover cwd from history
    name_to_sid = {v: k for k, v in ccctl_names.items()}
    if query in name_to_sid:
        sid = name_to_sid[query]
        return {"sessionId": sid, "name": query, "_needs_cwd_lookup": True}
    return None


def run_resume(args):
    sessions = read_sessions(args.claude_dir)
    ccctl_names = load_names(args.claude_dir)
    target = _find_session(sessions, ccctl_names, args.target)

    if not target:
        print(f"No session found: {args.target}", file=sys.stderr)
        sys.exit(1)

    pid = target.get("pid")
    sid = target["sessionId"]
    name = ccctl_names.get(sid) or target.get("name") or sid[:8]
    alive = check_alive(pid) if pid else False

    # Resolve cwd: live session file > history.jsonl > current dir
    cwd = target.get("cwd")
    if not cwd or target.get("_needs_cwd_lookup"):
        cwd = lookup_session_project(args.claude_dir, sid) or os.getcwd()

    if alive:
        print(f"⚠ Session '{name}' (PID {pid}) is still running.")
        print(f"  Resuming will fork the conversation.")
        print(f"  To stop first: ccctl stop {name}")
        if not args.force:
            print(f"  Use --force to resume anyway.")
            sys.exit(1)

    cmd = f"claude --resume {shlex.quote(sid)}"
    if name:
        cmd += f" --name {shlex.quote(name)}"

    print(f"Resuming '{name}' ...")
    print(f"  CWD: {cwd}")
    where = _open_in_new_window(cmd, cwd, title=name)
    print(f"  Opened in {where}")


def run_new(args):
    parts = ["claude"]

    if args.name:
        parts.append(f"--name {shlex.quote(args.name)}")

    if args.prompt:
        parts.append(shlex.quote(args.prompt))

    cmd = " ".join(parts)
    cwd = args.cwd or os.getcwd()

    print(f"Starting new session ...")
    if args.name:
        print(f"  Name: {args.name}")
    print(f"  CWD:  {cwd}")
    if args.prompt:
        print(f"  Prompt: {args.prompt[:60]}{'...' if len(args.prompt) > 60 else ''}")

    where = _open_in_new_window(cmd, cwd, title=args.name or "claude")
    print(f"  Opened in {where}")
