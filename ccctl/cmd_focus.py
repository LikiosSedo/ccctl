"""focus command — switch to a running session's terminal tab."""

from __future__ import annotations

import os
import subprocess
import sys

from ccctl.output import applescript_str
from ccctl.sources import check_alive, find_session, read_sessions
from ccctl.store import load_names


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
    safe_tty = applescript_str(tty)
    script = f'''
        tell application "iTerm2"
            activate
            repeat with w in windows
                tell w
                    repeat with i from 1 to count of tabs
                        set t to tab i
                        repeat with s in sessions of t
                            if tty of s is "{safe_tty}" then
                                select t
                                set index of w to 1
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


def _check_foreground(pid: int) -> bool:
    """Check if pid is the foreground process on its TTY."""
    try:
        r = subprocess.run(
            ["ps", "-o", "tpgid=,pgid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        parts = r.stdout.strip().split()
        return len(parts) >= 2 and parts[0] == parts[1]
    except (subprocess.TimeoutExpired, OSError):
        return False


def _send_prompt_iterm(tty: str, prompt: str) -> bool:
    """Send a prompt to an iTerm2 session by TTY."""
    safe_tty = applescript_str(tty)
    safe_prompt = applescript_str(prompt)
    script = f'''
        tell application "iTerm2"
            repeat with w in windows
                repeat with t in tabs of w
                    repeat with s in sessions of t
                        if tty of s is "{safe_tty}" then
                            tell s to write text (ASCII character 27) & "i" & "{safe_prompt}"
                            return "ok"
                        end if
                    end repeat
                end repeat
            end repeat
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
    target = find_session(sessions, ccctl_names, args.target)

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
        return

    # Send prompt if provided
    prompt = getattr(args, "prompt", None)
    if prompt:
        if not _check_foreground(pid):
            print(f"Session not at prompt (not foreground process), skipping send", file=sys.stderr)
            return
        if _send_prompt_iterm(tty, prompt):
            print(f"Sent → {prompt[:60]}{'…' if len(prompt) > 60 else ''}")
        else:
            print(f"Failed to send prompt", file=sys.stderr)
