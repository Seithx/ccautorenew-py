# CCAutoRenew

Auto-renew Claude Code 5-hour usage blocks. Python rewrite with Windows support, multi-session bulk renewal, rate limit detection, and desktop notifications.

Based on [aniketkarne/CCAutoRenew](https://github.com/aniketkarne/CCAutoRenew) (MIT License).

## The Problem

Claude Code uses 5-hour usage blocks. When you hit a rate limit, all your active sessions are blocked until the timer resets. CCAutoRenew monitors your sessions, detects rate limits passively from JSONL logs, and bulk-renews all active sessions the moment the block lifts.

## Quick Start

```
pip install -r requirements.txt
py manager.py start --at 09:00 --stop 17:00
```

## Commands

```
py manager.py start [options]   Start the background daemon
py manager.py stop              Stop the daemon
py manager.py restart [options] Restart with new options
py manager.py status            Show daemon state and timing
py manager.py logs [-f]         Show logs (optionally follow)
py manager.py dash              Live dashboard with progress bar
py manager.py tray              Launch system tray icon
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

## Why CCAutoRenew

Most renewal scripts use a simple timer and restart a single session. CCAutoRenew takes a different approach:

| Capability | Timer scripts | Bash functions | CCAutoRenew |
|------------|:---:|:---:|:---:|
| Multi-session bulk renewal | -- | -- | Yes |
| Passive detection (no CLI probing) | -- | -- | Yes |
| In-place VS Code resume | -- | -- | Yes |
| Weekly limit awareness | -- | -- | Yes |
| Background daemon | -- | -- | Yes |
| Adaptive polling | -- | -- | Yes |
| Desktop notifications | -- | -- | Yes |
| System tray status | -- | -- | Yes |

- **Multi-session**: Detects and renews all active sessions across all projects, not just the one you were last using.
- **Passive detection**: Reads rate limit timestamps directly from Claude's JSONL logs. No fragile text parsing of CLI output, no wasted API calls.
- **In-place resume**: With VS Code integration, sessions resume in their original terminals with full context and permissions intact. No orphaned windows.
- **Weekly limits**: Distinguishes 5-hour blocks from weekly limits. Notifies without wasting renewal attempts on limits that can't be bypassed.

## How It Works

The daemon uses a two-layer detection system:

### Layer 1: Passive rate limit detection (JSONL scanning)

1. Scans Claude's JSONL session files for `"error":"rate_limit"` entries
2. Parses the reset time from the message (e.g. "resets 2pm (Asia/Jerusalem)")
3. Differentiates 5-hour blocks from weekly limits
4. Collects all active sessions across all projects
5. Sleeps until reset time + 2 minute buffer
6. Bulk-renews every active session via `claude --resume <session_id> -p "continue working"`
7. Weekly limits: notifies only (no renewal possible until the weekly timer resets)

### Layer 2: ccusage timing (natural block expiry)

1. Polls [ccusage](https://github.com/ryoppippi/ccusage) for time remaining in current block
2. When block is about to expire (<=2 min), waits 60s for expiry, then renews the latest session
3. Falls back to clock-based timing (5h from last activity) if ccusage is unavailable

### Renewal strategy

- **VS Code mode** (preferred): Sends `Ctrl+Alt+Shift+R` to all VS Code windows via Win32 `SendInput`. This triggers a [macro-commander](https://github.com/jeff-hykin/macro-commander) macro that sends `/continue` to every open terminal -- sessions resume in-place, no extra windows.
- **CMD fallback**: For each session: `claude --resume <session_id> -p "continue working"` in a separate CMD window. If all resumes fail: `claude --continue -p "continue working"` as last resort.
- Claude has full conversation context when resuming -- it continues where it left off
- Adaptive polling: 10min when >30min left, 2min when 6-30min, 30s when <=5min

### Scheduling

- At stop time, automatically advances schedule to tomorrow (+24h)
- Within 10 minutes of stop time, skips new renewals to avoid wasting a block
- Retry cap: 5 consecutive failures triggers a 30-minute cooldown

## VS Code Integration (macro-commander)

Requires one-time setup:
1. Install: `code --install-extension jeff-hykin.macro-commander`
2. Add the `dismissRateLimitAll` macro to VS Code User Settings JSON (see RESEARCH.md)
3. Add keybinding: `Ctrl+Alt+Shift+R` -> `macros.dismissRateLimitAll`

The daemon auto-detects VS Code windows and sends the keystroke on rate limit reset. Falls back to CMD windows if no VS Code is running.

Manual trigger: `py vscode_trigger.py` or press `Ctrl+Alt+Shift+R` in VS Code.

## Known Limitations

- Some older sessions may return "No conversation found" when resumed (JSONL files persist longer than Claude's resumable session data). The `--continue` fallback handles this.
- ccusage may report "No active block" immediately after renewal. The daemon falls back to clock-based timing in this case.
- VS Code mode requires macro-commander extension and keybinding setup. Without it, CMD windows are used.

## System Tray

The tray icon provides at-a-glance status:
- Green = running, Yellow = waiting, Red = stopped
- Right-click menu: Start/Stop/Restart, status info, Open Logs, Exit

## Requirements

- Python 3.10+
- Claude CLI (`npm install -g @anthropic-ai/claude-code`)
- Optional: [ccusage](https://github.com/ryoppippi/ccusage) (`npm install -g ccusage`)
- Windows: `pip install -r requirements.txt` (includes plyer, pystray, Pillow, tzdata)

## Project Structure

```
manager.py          CLI entry point (start/stop/restart/status/logs/dash/tray)
daemon.py           Background monitoring loop (two-layer detection, bulk renewal)
session.py          JSONL scanner, rate limit parser, session UUID finder
notify.py           Desktop notification wrapper (plyer, never crashes)
tray.py             System tray icon (pystray, colored status circles)
vscode_trigger.py   VS Code keystroke sender (Win32 SendInput, macro-commander)
test_daemon.py      Automated tests (68 tests)
requirements.txt    Python dependencies
original/           Original bash scripts (reference)
```

## Testing

```
py -m pytest test_daemon.py -v
```

## License

MIT
