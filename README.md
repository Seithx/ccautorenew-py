# ccautorenew-py

Windows-first Python daemon that auto-renews Claude Code 5-hour usage blocks. Detects rate limits passively from JSONL logs, bulk-renews all active sessions on reset, and resumes them in-place via a VS Code keystroke macro.

> **Separate project**, inspired by [aniketkarne/CCAutoRenew](https://github.com/aniketkarne/CCAutoRenew) (MIT). The original is a set of bash scripts for Linux/macOS. This is a ground-up Python reimplementation with a different detection model, multi-session support, and Windows-native integration. Not a drop-in replacement -- see [Relationship to the original](#relationship-to-the-original).

## The Problem

Claude Code uses 5-hour usage blocks. When you hit a rate limit, every active session is blocked until the timer resets. ccautorenew-py watches your sessions, reads the reset time directly from Claude's own logs, and renews every session the moment the block lifts -- so you don't lose hours to a browser tab you forgot to reopen.

## Quick Start

```
git clone https://github.com/Seithx/ccautorenew-py.git
cd ccautorenew-py
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

## Why ccautorenew-py

Most renewal scripts use a simple timer and restart a single session. This project takes a different approach:

- **Multi-session bulk renewal** -- detects and renews every active session across every project, not just the one you were last using
- **Passive detection** -- reads rate limit timestamps directly from Claude's JSONL logs; no fragile text parsing of CLI output, no wasted API calls
- **In-place VS Code resume** -- sessions resume in their original terminals with full context and permissions intact; no orphaned windows
- **Weekly limit awareness** -- distinguishes 5-hour blocks from weekly limits; notifies without wasting renewal attempts on limits that can't be bypassed
- **Windows-native** -- `SendInput`, `tasklist`, `taskkill`, detached processes; no WSL or cygwin required
- **System tray** -- colored status icon with start/stop/logs menu

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

- **VS Code mode** (preferred): sends `Ctrl+Alt+Shift+R` to all VS Code windows via Win32 `SendInput`. Triggers a [macro-commander](https://github.com/jeff-hykin/macro-commander) macro that sends `/continue` to every open terminal -- sessions resume in-place, no extra windows.
- **CMD fallback**: for each session: `claude --resume <session_id> -p "continue working"` in a separate CMD window. If all resumes fail: `claude --continue -p "continue working"` as last resort.
- Claude has full conversation context when resuming
- Adaptive polling: 10min when >30min left, 2min when 6-30min, 30s when <=5min

### Scheduling

- At stop time, automatically advances schedule to tomorrow (+24h)
- Within 10 minutes of stop time, skips new renewals to avoid wasting a block
- Retry cap: 5 consecutive failures triggers a 30-minute cooldown

## VS Code Integration (macro-commander)

One-time setup:
1. Install: `code --install-extension jeff-hykin.macro-commander`
2. Add the `dismissRateLimitAll` macro to VS Code User Settings JSON (see RESEARCH.md)
3. Add keybinding: `Ctrl+Alt+Shift+R` -> `macros.dismissRateLimitAll`

The daemon auto-detects VS Code windows and sends the keystroke on rate limit reset. Falls back to CMD windows if no VS Code is running.

Manual trigger: `py vscode_trigger.py` or press `Ctrl+Alt+Shift+R` in VS Code.

## System Tray

- Green = running, Yellow = waiting, Red = stopped
- Right-click menu: Start/Stop/Restart, status info, Open Logs, Exit

## Known Limitations

- Some older sessions may return "No conversation found" when resumed (JSONL files persist longer than Claude's resumable session data). The `--continue` fallback handles this.
- ccusage may report "No active block" immediately after renewal. The daemon falls back to clock-based timing in this case.
- VS Code mode requires macro-commander extension and keybinding setup. Without it, CMD windows are used.

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
original/           Original bash scripts (reference, patched for Windows)
```

## Testing

```
py -m pytest test_daemon.py -v
```

## Relationship to the original

This started as a Windows-compatibility fork of [aniketkarne/CCAutoRenew](https://github.com/aniketkarne/CCAutoRenew), then grew into a separate project. The original bash scripts are preserved in `original/` for reference and credit.

**What's the same**: the goal (keep 5-hour blocks renewed), ccusage as an optional timing source, `claude --continue` as a fallback strategy.

**What's different**:

| | aniketkarne/CCAutoRenew | ccautorenew-py |
|---|:---:|:---:|
| Language | bash | Python 3.10+ |
| Platform | Linux / macOS | Windows-first (cross-platform Python) |
| Detection | Timer + ccusage polling | JSONL log scanning + ccusage |
| Sessions renewed | Single (last used) | All active across all projects |
| Rate limit handling | Reactive (on expiry) | Proactive (reads reset time from logs) |
| Weekly limit awareness | -- | Yes |
| VS Code integration | -- | macro-commander + SendInput |
| System tray | -- | Yes |
| UI | CLI + dashboard | CLI + dashboard + tray |
| Tests | -- | 68 pytest |

The two projects have diverged enough that upstreaming isn't practical -- use whichever fits your environment. Bug fixes that apply to both are welcome in both places.

## License

MIT -- same as the original. See `LICENSE`.
