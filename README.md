# CCAutoRenew

Auto-renew Claude Code 5-hour usage blocks. Python rewrite with Windows support, session resumption, and desktop notifications.

Based on [aniketkarne/CCAutoRenew](https://github.com/aniketkarne/CCAutoRenew) (MIT License).

## The Problem

Claude Code uses 5-hour usage blocks. If a block expires and you don't start a new one promptly, you lose time. Starting too early wastes hours. CCAutoRenew monitors your block timer and renews automatically at the right moment.

## Quick Start

```
pip install plyer
py manager.py start --at 09:00 --stop 17:00 --resume
```

## Commands

```
py manager.py start [options]   Start the background daemon
py manager.py stop              Stop the daemon
py manager.py restart [options] Restart with new options
py manager.py status            Show daemon state and timing
py manager.py logs [-f]         Show logs (optionally follow)
py manager.py dash              Live dashboard with progress bar
```

## Start Options

| Flag | Effect |
|------|--------|
| `--at HH:MM` | Start monitoring at this time (skips if past) |
| `--stop HH:MM` | Stop monitoring at this time |
| `--resume` | Try to continue previous session before starting new one |
| `--message "text"` | Custom message for new sessions |
| `--disable-ccusage` | Use clock-based timing only |
| `--no-notify` | Suppress desktop notifications |
| `--cwd /path` | Set working directory for Claude sessions |

## How It Works

1. Daemon polls [ccusage](https://github.com/ryoppippi/ccusage) for time remaining in current block
2. When block is about to expire (<=2 min), waits 60s for expiry, then renews
3. Renewal tries: `claude --continue` -> `claude -r <session_id>` -> `claude -p "hello"` (if `--resume` enabled)
4. Sleeps adaptively: 10min when >30min left, 2min when 6-30min, 30s when <=5min
5. At stop time, schedules restart for next day

Falls back to clock-based timing (5h from last activity) if ccusage is unavailable.

## Requirements

- Python 3.10+
- Claude CLI (`npm install -g @anthropic-ai/claude-code`)
- Optional: [ccusage](https://github.com/ryoppippi/ccusage) (`npm install -g ccusage`)
- Optional: `pip install pywin32` (if notifications don't work on Windows)

## Project Structure

```
manager.py          CLI entry point
daemon.py           Background monitoring loop
session.py          Session file scanner and resume logic
notify.py           Desktop notification wrapper
test_daemon.py      Automated tests (33 tests)
requirements.txt    Python dependencies
original/           Original bash scripts (reference)
```

## Testing

```
py -m pytest test_daemon.py -v
```

## License

MIT
