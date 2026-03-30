# ccctl

Claude Code session & task control plane.

A local CLI for observing, managing, and dispatching Claude Code sessions — designed to be called by a coordinator AI session or used directly by humans.

## Install

```bash
# From source (Python 3.9+, no dependencies)
git clone https://github.com/LikiosSedo/ccctl.git
cd ccctl

# Option A: shell wrapper
chmod +x bin/ccctl
ln -s $(pwd)/bin/ccctl ~/.local/bin/ccctl

# Option B: pip
pip install -e .
```

## Commands

### Observe

```bash
ccctl ps                    # List live sessions
ccctl ps -g                 # Group by project
ccctl ps --json             # JSON output (for AI consumption)
ccctl show <pid|name>       # Session details: messages, process info, git status
ccctl summary               # Full project topology and activity overview
ccctl summary --days 14 -v  # Last 14 days, per-session detail
```

### Manage

```bash
ccctl name                     # List all session names
ccctl name --auto              # Auto-name unnamed sessions
ccctl name <target> <name>     # Name a session (PID, session_id prefix, or existing name)
ccctl stop <target>...         # Stop sessions (SIGTERM, data preserved)
ccctl gc                       # Stop stale sessions + clean dead session files
ccctl gc --dry-run             # Preview what gc would do
```

### Dispatch

```bash
ccctl resume <name>                      # Resume session in new terminal tab
ccctl new --name my-task --cwd ~/project # Launch new session in new terminal
```

## Design

ccctl is a **dispatch layer**, not a data layer.

- Reads Claude Code's native data (`sessions/*.json`, `history.jsonl`, process table) in real-time
- Only owned data: session names (`~/.claude/ccctl/names.json`)
- `--json` output on every command for AI coordinator consumption
- Pure Python stdlib, zero dependencies

## License

MIT
