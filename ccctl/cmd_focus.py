"""focus command — switch to a running session's terminal tab."""

from __future__ import annotations

import os
import subprocess
import sys

from ccctl.sources import check_alive, read_sessions
from ccctl.store import load_names


def _find_live_session(sessions: list[dict], ccctl_names: dict[str, str], query: str) -> dict | None:
    """Find a live session by PID, name, or session_id prefix."""
    for s in sessions:
        if str(s.get("pid")) == query:
            return s
    for s in sessions:
        sid = s.get("sessionId", "")
        if ccctl_names.get(sid) == query:
            return s
    for s in sessions:
        if s.get("name") == query:
            return s
    for s in sessions:
        if s.get("sessionId", "").startswith(query):
            return s
    return None


def _get_tty(pid: int) -> str | None:
    try:
        r = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        tty = r.stdout.strip()
        if tty and tty != "??":
            return f"/dev/{tty}"
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _focus_iterm(tty: str) -> bool:
    script = f'''
        tell application "iTerm2"
            activate
            repeat with w in windows
                tell w
                    repeat with i from 1 to count of tabs
                        set t to tab i
                        repeat with s in sessions of t
                            if tty of s is "{tty}" then
                                select t
                                return "ok"
                            end if
                        end repeat
                    end repeat
                end tell
            end repeat
            return "not found"
        end tell
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() == "ok"
    except (subprocess.TimeoutExpired, OSError):
        return False


def run(args):
    sessions = read_sessions(args.claude_dir)
    ccctl_names = load_names(args.claude_dir)
    target = _find_live_session(sessions, ccctl_names, args.target)

    if not target:
        print(f"No live session found: {args.target}", file=sys.stderr)
        sys.exit(1)

    pid = target.get("pid")
    sid = target.get("sessionId", "")
    name = ccctl_names.get(sid) or target.get("name") or sid[:8]

    if not check_alive(pid):
        print(f"Session '{name}' (PID {pid}) is not running.", file=sys.stderr)
        print(f"Use: ccctl resume {name}")
        sys.exit(1)

    tty = _get_tty(pid)
    if not tty:
        print(f"Cannot find TTY for PID {pid}", file=sys.stderr)
        sys.exit(1)

    term = os.environ.get("TERM_PROGRAM", "")
    if "iTerm" in term:
        if _focus_iterm(tty):
            print(f"Focused → {name} (PID {pid})")
        else:
            print(f"Session found but tab not located in iTerm2", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Focus not supported in {term or 'this terminal'}.")
        print(f"Session '{name}' is on {tty}")
