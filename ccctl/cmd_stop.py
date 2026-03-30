"""stop / gc — session lifecycle management.

stop: SIGTERM a session process. All session data (files, names, history)
      is preserved so the session can be resumed later.

gc:   Bulk cleanup. Stops all stale sessions, then removes session files
      for processes that are no longer running.
"""

from __future__ import annotations

import os
import signal
import sys
import time

from ccctl.cmd_ps import classify
from ccctl.output import format_ago
from ccctl.sources import check_alive, read_last_messages, read_sessions
from ccctl.store import load_names


def _find_session(sessions: list[dict], ccctl_names: dict[str, str], query: str) -> dict | None:
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


def _sigterm(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except OSError as e:
        print(f"  Failed to signal PID {pid}: {e}", file=sys.stderr)
        return False


def _remove_session_file(claude_dir, pid: int):
    f = claude_dir / "sessions" / f"{pid}.json"
    if f.exists():
        f.unlink()


def run_stop(args):
    """Stop specific sessions. Process is terminated, data is preserved."""
    sessions = read_sessions(args.claude_dir)
    ccctl_names = load_names(args.claude_dir)

    stopped = 0
    for target in args.targets:
        s = _find_session(sessions, ccctl_names, target)
        if not s:
            print(f"  ? No session found: {target}", file=sys.stderr)
            continue

        pid = s.get("pid")
        sid = s.get("sessionId", "")
        name = ccctl_names.get(sid) or s.get("name") or sid[:8]

        if not check_alive(pid):
            print(f"  - {name} (PID {pid}) already stopped")
            continue

        if _sigterm(pid):
            print(f"  ✓ {name} (PID {pid}) stopped")
            stopped += 1

    print(f"\nStopped {stopped} sessions. Session data preserved for resume.")


def run_gc(args):
    """Stop stale sessions, then remove session files for dead processes."""
    sessions = read_sessions(args.claude_dir)
    if not sessions:
        print("No sessions.")
        return

    sids = {s["sessionId"] for s in sessions if "sessionId" in s}
    last_msgs = read_last_messages(args.claude_dir, sids)
    ccctl_names = load_names(args.claude_dir)
    now = time.time()

    to_stop = []   # stale but alive → stop
    to_clean = []  # dead → remove session file

    for s in sessions:
        pid = s.get("pid")
        sid = s.get("sessionId", "")
        alive = check_alive(pid) if pid else False

        if not alive:
            to_clean.append(s)
            continue

        msg = last_msgs.get(sid)
        last_active = (msg["timestamp"] / 1000) if (msg and msg.get("timestamp")) else s.get("_mtime")
        if classify(alive, last_active) == "stale":
            to_stop.append(s)

    if not to_stop and not to_clean:
        print("Nothing to clean up.")
        return

    # Preview
    if to_stop:
        print(f"Stale sessions to stop ({len(to_stop)}):\n")
        for s in to_stop:
            pid = s.get("pid")
            sid = s.get("sessionId", "")
            name = ccctl_names.get(sid) or s.get("name") or "-"
            msg = last_msgs.get(sid)
            la = (msg["timestamp"] / 1000) if (msg and msg.get("timestamp")) else s.get("_mtime")
            print(f"  STOP  PID {pid:>5}  {name:<25} last active: {format_ago(la, now)} ago")

    if to_clean:
        print(f"\nDead session files to remove ({len(to_clean)}):\n")
        for s in to_clean:
            pid = s.get("pid")
            sid = s.get("sessionId", "")
            name = ccctl_names.get(sid) or s.get("name") or "-"
            print(f"  CLEAN PID {pid:>5}  {name}")

    if args.dry_run:
        print(f"\nDry run. Run 'ccctl gc' to execute.")
        return

    # Execute
    stopped = 0
    for s in to_stop:
        pid = s.get("pid")
        if pid and _sigterm(pid):
            stopped += 1

    cleaned = 0
    for s in to_clean:
        pid = s.get("pid")
        if pid:
            _remove_session_file(args.claude_dir, pid)
            cleaned += 1

    print(f"\nDone: stopped {stopped}, cleaned {cleaned} session files.")
    if stopped:
        print("Stopped sessions can be resumed with: ccctl resume <name>")
