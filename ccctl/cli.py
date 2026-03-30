"""ccctl — Claude Code session & task control plane."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="ccctl",
        description="Claude Code session & task control plane",
    )
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=Path.home() / ".claude",
        help="Claude Code data directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {_get_version()}"
    )

    sub = parser.add_subparsers(dest="command")

    # --- observe ---
    ps_p = sub.add_parser("ps", help="List all sessions")
    ps_p.add_argument("-a", "--all", action="store_true", help="Include dead sessions")
    ps_p.add_argument("-g", "--group", action="store_true", help="Group by project")
    ps_p.add_argument("--json", action="store_true", help="JSON output")
    ps_p.add_argument(
        "--sort", choices=["status", "active", "started", "name"],
        default="status", help="Sort order (default: status)",
    )

    show_p = sub.add_parser("show", help="Show session details (messages, process info, git)")
    show_p.add_argument("id", help="PID, name, or session ID prefix")
    show_p.add_argument("--json", action="store_true", help="JSON output")

    # --- manage ---
    name_p = sub.add_parser("name", help="List or set session names")
    name_p.add_argument("pid", nargs="?", help="PID to name")
    name_p.add_argument("name_value", nargs="?", metavar="name", help="Name to assign")
    name_p.add_argument("--auto", action="store_true", help="Auto-name all unnamed alive sessions")

    stop_p = sub.add_parser("stop", help="Stop sessions (preserves data for resume)")
    stop_p.add_argument("targets", nargs="+", help="PIDs, names, or session ID prefixes")

    gc_p = sub.add_parser("gc", help="Stop stale sessions + remove dead session files")
    gc_p.add_argument("--dry-run", action="store_true", help="Preview without executing")

    # --- dispatch ---
    resume_p = sub.add_parser("resume", help="Resume a session in a new terminal window")
    resume_p.add_argument("target", help="PID, name, or session ID prefix")
    resume_p.add_argument("--force", action="store_true", help="Resume even if session is alive")

    new_p = sub.add_parser("new", help="Start a new Claude Code session in a new terminal")
    new_p.add_argument("prompt", nargs="?", help="Initial prompt")
    new_p.add_argument("--name", help="Session name")
    new_p.add_argument("--cwd", help="Working directory (default: current)")

    # --- intelligence ---
    sum_p = sub.add_parser("summary", help="Full topology and activity overview")
    sum_p.add_argument("--scope", help="Root directory to scan (default: ~/project)")
    sum_p.add_argument("--days", type=int, help="Limit to last N days")
    sum_p.add_argument("-v", "--verbose", action="store_true", help="Show per-session details")
    sum_p.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # dispatch
    handlers = {
        "ps":     lambda: _lazy("ccctl.cmd_ps", "run", args),
        "show":   lambda: _lazy("ccctl.cmd_show", "run", args),
        "name":   lambda: _lazy("ccctl.cmd_name", "run", args),
        "stop":   lambda: _lazy("ccctl.cmd_stop", "run_stop", args),
        "gc":     lambda: _lazy("ccctl.cmd_stop", "run_gc", args),
        "resume": lambda: _lazy("ccctl.cmd_open", "run_resume", args),
        "new":    lambda: _lazy("ccctl.cmd_open", "run_new", args),
        "summary": lambda: _lazy("ccctl.cmd_summary", "run", args),
    }
    handlers[args.command]()


def _lazy(module: str, func: str, args):
    import importlib
    mod = importlib.import_module(module)
    getattr(mod, func)(args)


def _get_version() -> str:
    from ccctl import __version__
    return __version__


if __name__ == "__main__":
    main()
