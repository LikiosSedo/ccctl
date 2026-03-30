# ccctl

Claude Code session & task control plane.

A local CLI + web dashboard for observing, managing, and dispatching Claude Code sessions — designed to be called by a coordinator AI session or used directly by humans.

## Install

```bash
# From source (Python 3.10+, no dependencies)
git clone https://github.com/LikiosSedo/ccctl.git
cd ccctl

# Option A: shell wrapper (no pip required)
chmod +x bin/ccctl
ln -s $(pwd)/bin/ccctl ~/.local/bin/ccctl

# Option B: pip
pip install -e .
```

## Setup (optional but recommended)

### Status detection hooks

Add to `~/.claude/settings.json` to enable ready/busy detection in the dashboard:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "mkdir -p ~/.claude/ccctl/status && echo idle > ~/.claude/ccctl/status/$PPID"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "mkdir -p ~/.claude/ccctl/status && echo working > ~/.claude/ccctl/status/$PPID"
          }
        ]
      }
    ]
  }
}
```

This writes `idle`/`working` to `~/.claude/ccctl/status/<pid>` on Claude Code events. The dashboard reads these files to show green (ready) or orange (working) indicators.

### Coordinator setup

Add to `~/.claude/CLAUDE.md` so any session knows how to use ccctl:

```markdown
## ccctl — Session Management CLI

You have access to `ccctl`, a CLI for managing Claude Code sessions.

# Observe
ccctl ps                         # List live sessions
ccctl ps --json                  # JSON output
ccctl show <name|pid>            # Session details
ccctl summary -v                 # Full project topology

# Manage
ccctl name <target> <name>       # Name a session
ccctl stop <target>...           # Stop sessions
ccctl gc                         # Cleanup stale/dead sessions

# Dispatch
ccctl new --name <n> --cwd <dir> "prompt"  # New session
ccctl resume <name>              # Resume stopped session
ccctl focus <name> "prompt"      # Switch to session + send prompt
ccctl dashboard                  # Open web dashboard
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
ccctl resume <name>                        # Resume session in new terminal tab
ccctl new --name my-task --cwd ~/project   # Launch new session
ccctl focus <name>                         # Switch to session's terminal tab
ccctl focus <name> "do something"          # Switch + send prompt
ccctl dashboard                            # Open web dashboard (localhost:8420)
```

### Dashboard

```bash
ccctl dashboard              # Start on default port 8420
ccctl dashboard --port 9000  # Custom port
```

Features:
- Live session cards with ready/busy indicators (requires hooks)
- Click card to focus session, Send button to dispatch prompts
- Stop sessions with confirmation dialog
- History tab with search and resume
- Coordinator mode: pin a session, dispatch instructions from top bar
- Rename sessions inline (hover card → click ✎)
- View modes: Flat / By Project / By Status

## Design

ccctl is a **dispatch layer**, not a data layer.

- Reads Claude Code's native data (`sessions/*.json`, `history.jsonl`, process table) in real-time
- Only owned data: session names (`~/.claude/ccctl/names.json`) and coordinator config
- Status detection via Claude Code hooks (file-based, zero overhead)
- iTerm2 integration: focus tabs, inject prompts, sync `/rename`
- `--json` output on every command for AI coordinator consumption
- Pure Python stdlib, zero dependencies
- Single-file HTTP dashboard (no frontend framework, no build step)

## Requirements

- Python 3.10+
- macOS (iTerm2 recommended for full features; Terminal.app and tmux partially supported)
- Claude Code

## License

MIT
