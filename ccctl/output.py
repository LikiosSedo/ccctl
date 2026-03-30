"""Output formatting utilities."""

from __future__ import annotations

import os

def applescript_str(s: str) -> str:
    """Escape a string for safe embedding in AppleScript double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


_STATUS_ICON = {
    "active": "●",
    "idle": "○",
    "stale": "◌",
    "dead": "✕",
    "unknown": "?",
}


def format_ago(ts: float | None, now: float) -> str:
    if ts is None:
        return "-"
    d = now - ts
    if d < 0:
        return "now"
    if d < 60:
        return f"{int(d)}s"
    if d < 3600:
        return f"{int(d / 60)}m"
    if d < 86400:
        return f"{int(d / 3600)}h"
    return f"{int(d / 86400)}d"


def shorten_path(path: str, max_len: int = 32) -> str:
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]

    # Worktree: ~/x/.claude/worktrees/name → x⊕name
    wt = "/.claude/worktrees/"
    if wt in path:
        base, name = path.split(wt, 1)
        project = os.path.basename(base)
        return f"{project}⊕{name.rstrip('/')}"

    if len(path) <= max_len:
        return path

    parts = path.split("/")
    for i in range(1, len(parts)):
        short = "…/" + "/".join(parts[i:])
        if len(short) <= max_len:
            return short
    return "…" + path[-(max_len - 1):]


def _trunc(text: str, n: int) -> str:
    if not text:
        return "-"
    text = text.replace("\n", " ").strip()
    return text[: n - 1] + "…" if len(text) > n else text


def print_session_table(rows: list[dict], now: float):
    if not rows:
        print("No sessions found.")
        return

    cols = [
        ("s", ""),
        ("pid", "PID"),
        ("name", "NAME"),
        ("project", "PROJECT"),
        ("active", "ACTIVE"),
        ("status", "STATUS"),
        ("input", "LAST INPUT"),
    ]

    table = []
    for r in rows:
        table.append(
            {
                "s": _STATUS_ICON.get(r["status"], "?"),
                "pid": str(r["pid"] or "-"),
                "name": r["name"] or "-",
                "project": r.get("project", ""),
                "active": format_ago(r["last_active"], now),
                "status": r["status"],
                "input": _trunc(r["last_input"], 45),
            }
        )

    widths = {k: max(len(h), *(len(row[k]) for row in table)) for k, h in cols}

    header = "  ".join(h.ljust(widths[k]) for k, h in cols)
    print(header)
    print("─" * len(header))

    for row in table:
        print("  ".join(row[k].ljust(widths[k]) for k, _ in cols))

    # summary
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    parts = [f"{len(rows)} sessions"]
    for s in ("active", "idle", "stale", "dead"):
        if n := counts.get(s):
            parts.append(f"{n} {s}")
    print(f"\n{', '.join(parts)}")


def print_group_table(groups: dict[str, list[dict]], now: float):
    """Print sessions grouped by project."""
    if not groups:
        print("No sessions found.")
        return

    # Sort groups: most active first, then by total count
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: (
            -sum(1 for s in kv[1] if s["status"] == "active"),
            -len(kv[1]),
        ),
    )

    for project, members in sorted_groups:
        status_counts: dict[str, int] = {}
        for m in members:
            status_counts[m["status"]] = status_counts.get(m["status"], 0) + 1
        status_str = ", ".join(
            f"{n} {s}" for s in ("active", "idle", "stale", "dead")
            if (n := status_counts.get(s))
        )
        print(f"\n{project}  ({len(members)} sessions: {status_str})")
        print("─" * 70)

        for m in members:
            icon = _STATUS_ICON.get(m["status"], "?")
            name = m["name"] or "-"
            ago = format_ago(m["last_active"], now)
            inp = _trunc(m["last_input"], 40)
            print(f"  {icon} {m['pid']:>5}  {name:<25} {ago:>5}  {inp}")

    total = sum(len(v) for v in groups.values())
    print(f"\n{total} sessions across {len(groups)} projects")
