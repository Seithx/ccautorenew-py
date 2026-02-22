# CCAutoRenew - Full Improvement Plan

Base: https://github.com/aniketkarne/CCAutoRenew (cloned to this repo)
Goal: Windows-compatible Python rewrite with session continuation and desktop notifications.

---

## Environment Facts (this machine)

- OS: Windows 10, Git Bash (MINGW64)
- Python: `py` launcher, Python 3.13.5 available
- Claude CLI: v2.1.49 at `/c/Users/asafl/AppData/Roaming/npm/claude`
- Session files: `~/.claude/projects/<project-slug>/<uuid>.jsonl`
- Project slug format: full path, colons removed, slashes become dashes (e.g. `C:\Users\asafl\Desktop\CCAUTORENEW` -> `C--Users-asafl-Desktop-CCAUTORENEW`)
- ccusage: NOT globally installed
- jq: NOT installed (not needed -- Python stdlib `json` replaces it)
- Resume flags confirmed in `claude --help`: `-c/--continue`, `-r/--resume <id>`
- Non-interactive flag: `-p/--print` (exits after response, no interactive session)

---

## How It Works (Process Flow)

### What you do (user perspective)

```
1. You open a terminal in your project folder.

2. You run:
     py manager.py start --at 09:00 --stop 17:00 --resume

3. Output:
     Daemon started (PID 12345)
     Schedule: 09:00 - 17:00
     Resume: enabled
     A Windows toast notification pops up: "CCAutoRenew - Monitoring active"

4. You close the terminal and go about your day.
   The daemon runs silently in the background.

5. At any point you can check on it:
     py manager.py status       -> one-line summary + recent log
     py manager.py dash         -> live dashboard with progress bar
     py manager.py logs -f      -> streaming log output

6. When you're done:
     py manager.py stop
```

### What the daemon does (system perspective)

```
STARTUP
  |
  v
Set working directory to your project folder
Write PID file so manager can find us
  |
  v
MAIN LOOP (runs forever until stopped)
  |
  +---> Is it before start time (e.g. 09:00)?
  |       YES -> sleep until start time
  |       NO  |
  |           v
  +---> Is it after stop time (e.g. 17:00)?
  |       YES -> schedule restart for tomorrow, sleep
  |       NO  |
  |           v
  +---> Ask ccusage: "how many minutes left in this 5h block?"
  |       |
  |       +-- ccusage available? -> parse JSON, get minutes remaining
  |       +-- ccusage unavailable? -> estimate from last_activity timestamp
  |       |
  |       v
  +---> More than 5 minutes left?
  |       YES -> sleep (10min if >30m, 2min if 6-30m, 30s if <=5m)
  |              loop back
  |       NO  |
  |           v
  +---> RENEWAL TIME
  |       Wait 60s (let old block fully expire)
  |       |
  |       v
  |       Try to keep your session alive:
  |         1st: claude --continue  (picks up where you left off)
  |         2nd: claude -r <id>     (resume specific session by UUID)
  |         3rd: claude -p "hello"  (start fresh -- last resort)
  |       |
  |       v
  |       Success? -> toast notification, update timestamp, cooldown 5min
  |       Failed?  -> toast notification, retry in 60s
  |       |
  |       v
  +---> Loop back to top
```

### A typical day

```
08:55  You run: py manager.py start --at 09:00 --stop 17:00 --resume
       Daemon starts, sees it's before 09:00, sleeps.

09:00  Toast: "Monitoring active"
       No active block yet -> starts initial session.
       Toast: "New session started"

09:10  Checks ccusage -> 290 min left. Sleeps 10 min.
  ...
13:56  Checks ccusage -> 4 min left. Sleeps 30s.
13:58  2 min left -> RENEWAL TRIGGERED.
       Waits 60s for block to expire...
13:59  Runs: claude --continue
       Success! Toast: "Previous session resumed"
       New 5h block begins. Cooldown 5 min.

14:04  Checks ccusage -> 295 min left. Sleeps 10 min.
  ...
16:55  Stop time is <10 min away. Skips renewal.
17:00  Toast: "Monitoring stopped for today"
       Schedules restart for tomorrow 09:00, sleeps.
```

---

## Architecture Decision

**Rewrite daemon and manager in Python** (keep .sh files as reference only).

Benefits:
- Native Windows process management (no `nohup` issues)
- stdlib `json` parsing (no `jq` needed)
- `plyer` for cross-platform desktop notifications
- Easier to extend and test

### New File Structure

```
CCAUTORENEW/
+-- daemon.py              # Background daemon process
+-- manager.py             # CLI entry point (start/stop/status/dash/logs)
+-- session.py             # Session detection + continuation logic
+-- notify.py              # Desktop notification wrapper
+-- requirements.txt       # plyer
+-- PLAN.md                # This file
+-- TODO.md                # Progress tracking
+-- (original .sh files kept for reference)
```

### Runtime Data Directory

All runtime state stored in `~/.claude-autorenew/`:

| File | Format | Purpose |
|------|--------|---------|
| `config.json` | JSON (schema below) | Persisted daemon settings |
| `daemon.pid` | Plain text, single integer | PID of running daemon process |
| `last_activity` | Plain text, single integer (unix epoch) | Epoch of last successful renewal |
| `daemon.log` | Text log, rotated via `RotatingFileHandler(maxBytes=1MB, backupCount=3)` | Daemon activity log |

### config.json Schema

```json
{
  "start_epoch": 1740000000,
  "stop_epoch": 1740028800,
  "resume_enabled": true,
  "message": "continue working on the project",
  "disable_ccusage": false,
  "notifications_enabled": true,
  "cwd": "C:\\Users\\asafl\\Desktop\\MyProject"
}
```

All fields are optional. Defaults:
- `start_epoch`: null (start monitoring immediately)
- `stop_epoch`: null (monitor continuously)
- `resume_enabled`: false
- `message`: null (use random greeting)
- `disable_ccusage`: false
- `notifications_enabled`: true
- `cwd`: directory where `manager.py start` was invoked

### Python Dependencies

```
plyer>=2.1.0
```

Install: `py -m pip install plyer`

If notifications fail on Windows, also install: `py -m pip install pywin32`

No other non-stdlib deps needed.

---

## ccusage Approach

ccusage provides the most accurate "minutes until reset" data.

**Important: `blocks --live` was removed in v18.0.0.** The original bash script's fallback to `--live` no longer works. Use `blocks --active` instead.

### Installing ccusage

**Option A - Global install (recommended for reliability + speed):**
```
npm install -g ccusage
```
After this, `ccusage blocks --active` is a direct command, fast (~100ms).

**Option B - npx (no install required):**
```
npx ccusage@latest blocks --active
```
`npx` downloads ccusage on first run and caches it in npm's local cache (`~/.npm/_npx/`). Subsequent calls use the cache and are fast (~200ms). No global install needed, but requires network on first call and npm cache must not be cleared.

### The command we use

```
ccusage blocks --active --json
```

This returns ONLY the currently active block as structured JSON. The `--active` flag filters out historical blocks.

### ccusage JSON schema (confirmed from source: `apps/ccusage/src/commands/blocks.ts`)

```json
{
  "blocks": [
    {
      "id": 1,
      "startTime": "2026-02-20T09:00:00.000Z",
      "endTime": "2026-02-20T14:00:00.000Z",
      "actualEndTime": null,
      "isActive": true,
      "isGap": false,
      "entries": 42,
      "totalTokens": 291894,
      "costUSD": 156.40,
      "models": ["opus-4", "sonnet-4"],
      "burnRate": { "tokensPerMinute": 2100 },
      "projection": { "totalTokens": 450000 },
      "usageLimitResetTime": "2026-02-20T14:00:00.000Z"
    }
  ]
}
```

Key fields for our daemon:
- `isActive` -- confirms this is the current block
- `endTime` -- when the 5h block expires. **Compute remaining: `endTime - now`**
- `usageLimitResetTime` -- when the usage limit resets (may differ from endTime if rate-limited). Use this if present, otherwise fall back to endTime.

**There is NO `timeRemaining` field.** We compute it ourselves from the timestamps.

### How the daemon uses ccusage (in order, stops at first success)

1. `ccusage blocks --active --json` -- global install, fastest
2. `npx ccusage@latest blocks --active --json` -- cached/downloaded via npm
3. Clock-based fallback -- if both fail OR `--disable-ccusage` flag is set

Clock-based fallback: tracks `last_activity` epoch, renews when `now - last_activity >= 18000` (5 hours).

**Known inaccuracy on first run:** If no `last_activity` file exists and ccusage is unavailable, the daemon starts an initial session immediately. This sets `last_activity` to now. If you were already mid-block (e.g. 3h into a 5h block), the clock will be off -- it will think the block expires in 5h, not 2h. This is acceptable because ccusage corrects it once available, and the worst case is a slightly late renewal.

### Note on `--session-length`

The 5-hour block duration is the default and matches Claude's billing cycles. ccusage supports `--session-length` to override this, but we use the default.

---

## Phase 1: Notifications (`notify.py`)

Build first because all other modules import it.

Thin wrapper -- notifications are always optional and must never crash the daemon.

```python
def notify(title: str, message: str) -> None:
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=5)
    except Exception:
        pass  # log the error to daemon.log, but never crash
```

`plyer` on Windows uses the Windows 10 toast notification system (shown in notification center).

### Notification events

| Trigger | Title | Body |
|---------|-------|------|
| Daemon starts | CCAutoRenew | Monitoring active |
| Waiting for start time | CCAutoRenew | Will activate at HH:MM |
| Session renewed (new) | CCAutoRenew | New session started |
| Session resumed | CCAutoRenew | Previous session resumed |
| Renewal failed | CCAutoRenew | Renewal failed - will retry |
| Stop time reached | CCAutoRenew | Monitoring stopped for today |
| New day, restarting | CCAutoRenew | Resuming monitoring |

---

## Phase 2: Session Module (`session.py`)

Build second because `daemon.py` imports it for resume logic.

```python
def get_claude_data_dirs() -> list[Path]:
    # Returns all existing candidate dirs, in order:
    # 1. ~/.claude/projects  (confirmed on this machine)
    # 2. ~/.config/claude/projects  (Linux/mac)
    # 3. $CLAUDE_CONFIG_DIR/projects  (if env var set)

def get_latest_session_id() -> str | None:
    # Scans all *.jsonl across data dirs
    # Reads last non-empty line of each, parses JSON, reads "timestamp" key
    # Returns UUID (filename stem) of file with newest timestamp
    # Returns None if no files found or none have valid timestamps

def get_project_sessions(project_cwd: Path) -> list[tuple[str, float]]:
    # Returns sessions only for the given project directory
    # Converts project_cwd to slug:
    #   Path("C:\\Users\\asafl\\Desktop\\CCAUTORENEW")
    #   -> "C--Users-asafl-Desktop-CCAUTORENEW"
    #   (colons to dashes, backslashes+forward slashes to dashes)
    # Looks in <data_dir>/<slug>/*.jsonl
    # Returns [(session_id, epoch), ...] sorted newest-first
```

### JSONL file format (confirmed from this machine)

File path: `~/.claude/projects/<project-slug>/<uuid>.jsonl`
- Each line = one JSON object
- Key field: `"timestamp"` (ISO 8601 string, e.g. `"2025-02-20T01:37:00.000Z"`)
- Session ID = UUID portion of the filename (e.g. file `e345f66d-fbd8-4a89-95f2-1478b53a6b60.jsonl` -> session ID `e345f66d-fbd8-4a89-95f2-1478b53a6b60`)
- Usage limit errors: lines where message content contains `"Claude AI usage limit reached"`

---

## Phase 3: Core Daemon (`daemon.py`)

Port all logic from `claude-auto-renew-daemon.sh` to Python.

### Functions

**`get_minutes_until_reset(config) -> int | None`**
- If `config.disable_ccusage` is True: return None immediately (forces clock fallback)
- Use `--active --json` flags for structured, active-block-only output:
  1. Try `subprocess.run(["ccusage", "blocks", "--active", "--json"], capture_output=True, timeout=10)`
  2. If that fails, try `subprocess.run(["npx", "ccusage@latest", "blocks", "--active", "--json"], capture_output=True, timeout=15)`
- Parse JSON:
  ```python
  data = json.loads(stdout)
  block = data["blocks"][0]  # --active returns only the current block
  # Prefer usageLimitResetTime (accounts for rate limits), fall back to endTime
  reset_str = block.get("usageLimitResetTime") or block["endTime"]
  reset_time = datetime.fromisoformat(reset_str)
  remaining = (reset_time - datetime.now(timezone.utc)).total_seconds() / 60
  ```
- If JSON parsing fails (bad output, no active block, empty blocks list), fall back to regex on plain-text `ccusage blocks --active` output: pattern `(\d+)h\s*(\d+)m` or `(\d+)m` on a line containing "Active"
- Log the raw ccusage output at DEBUG level for troubleshooting
- Return remaining minutes as int, or None if all parsing fails

**`get_sleep_duration(minutes_remaining: int | None, config) -> int`**

If `minutes_remaining` is not None (ccusage available):

| minutes_remaining | sleep duration |
|-------------------|---------------|
| > 30 | 600s (10 min) |
| 6 -- 30 | 120s (2 min) |
| <= 5 | 30s |

If `minutes_remaining` is None (clock fallback):
- Read `last_activity` epoch from file
- Compute `remaining_secs = 18000 - (now - last_activity)`
- Convert to minutes, apply same table above
- If no `last_activity` file exists: return 300s (check again in 5 min)

**`is_monitoring_active(config) -> bool`**
- If `config.start_epoch` is set and `now < start_epoch`: return False (before window)
- If `config.stop_epoch` is set and `now >= stop_epoch`: return False (past window)
- Otherwise: return True

**`should_renew(minutes_remaining: int | None, config) -> bool`**
- If `config.stop_epoch` is set and `stop_epoch - now <= 600`: return False (within 10 min of stop time -- don't start a renewal that'll be wasted)
- If ccusage data available (`minutes_remaining` is not None): return `minutes_remaining <= 2`
- Clock fallback: read `last_activity`, return `now - last_activity >= 18000`
- If no `last_activity` file exists: return True (start initial session)

**`start_claude_session(config) -> bool`**
Full decision tree documented below in "Session Renewal Decision Tree".

**`main_loop(config)`**
```
on startup:
  os.chdir(os.path.normpath(config.cwd))  # normpath handles mixed slashes on Windows
  write os.getpid() to daemon.pid
  register signal handlers (SIGINT, SIGTERM, and SIGBREAK on Windows)
  log f"CWD set to: {os.getcwd()}"  # confirm path resolved correctly

loop:
  if should_restart_tomorrow(config):
    advance start_epoch/stop_epoch by 86400
    save updated config to config.json
    notify("Scheduled restart for tomorrow")
    sleep until new start_epoch
    continue

  if not is_monitoring_active(config):
    log reason: "before start time" or "past stop time"
    sleep 60s (or smarter: sleep until start_epoch if known)
    continue

  minutes_remaining = get_minutes_until_reset(config)

  if should_renew(minutes_remaining, config):
    sleep 60s  # WHY: wait for old block to fully expire so the new session opens a NEW 5h block.
               # Sending a message while the old block is still active just uses the old block
               # and doesn't trigger renewal. The 60s buffer ensures we're past the expiry boundary.
    success = start_claude_session(config)
    if success: sleep 300s (5 min cooldown to avoid double-renewal), continue
    else: sleep 60s (retry soon), continue

  sleep get_sleep_duration(minutes_remaining, config)

on clean exit:
  delete daemon.pid
  log "daemon stopped"
```

### Signal handling

```python
import signal

def handle_shutdown(signum, frame):
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)
if hasattr(signal, 'SIGBREAK'):  # Windows only
    signal.signal(signal.SIGBREAK, handle_shutdown)
```

### PID file ownership

**Only `daemon.py` writes the PID file** (using `os.getpid()`). `manager.py` waits up to 3 seconds after Popen for the PID file to appear, then reads it to confirm startup.

Reason: `Popen.pid` is the wrapper process PID on some Windows configs; the daemon's own `os.getpid()` is always correct.

### How `manager.py` launches the daemon

```python
proc = subprocess.Popen(
    [sys.executable, str(daemon_py_path), "--config", str(config_path)],
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
# Wait up to 3s for daemon.pid file to appear
# Read PID from file, verify process is alive with os.kill(pid, 0)
```

---

## Session Renewal Decision Tree

This is the complete flow for `start_claude_session(config) -> bool`.

All subprocess calls use `try/except subprocess.TimeoutExpired` (Python raises an exception on timeout, NOT exit code 124 like bash).

```
IF config.resume_enabled is False:
    -> jump to [NEW SESSION] below

------------------------------------------------------------
RESUME ATTEMPT 1: claude --continue
------------------------------------------------------------
What it does:
  Continues the most recent session that was started in the daemon's CWD.
  The daemon's CWD is set to config.cwd on startup.

Command:
  subprocess.run(["claude", "--continue", "-p", "continue working"], timeout=30)

Why try this first:
  Simplest method. No need to scan files or know session IDs.
  Claude CLI handles finding the right session internally.

If succeeds (exit code 0):

  log "resumed via --continue"
  write current epoch to last_activity file
  notify("Previous session resumed")
  RETURN True

If fails (non-zero exit or TimeoutExpired):
  log "continue failed: <reason>"
  -> fall through to Attempt 2

------------------------------------------------------------
RESUME ATTEMPT 2: claude -r <session_id>
------------------------------------------------------------
What it does:
  Resumes a specific session by its UUID. CWD-independent.
  We find the UUID by scanning JSONL session files.

Step 2a - Find session ID:
  session_id = session.get_latest_session_id()
    - Scans ~/.claude/projects/**/*.jsonl
    - Reads last line of each file, parses JSON timestamp
    - Returns UUID (filename stem) of most recent file

If session_id is None:
  log "no session files found"
  -> fall through to [NEW SESSION]

Step 2b - Resume it:
  subprocess.run(["claude", "-r", session_id, "-p", "continue working"], timeout=30)

If succeeds (exit code 0):
  log "resumed session <session_id>"
  write current epoch to last_activity file
  notify("Previous session resumed")
  RETURN True

If fails:
  log "resume failed for session <session_id>, falling back to new session"
  -> fall through to [NEW SESSION]

------------------------------------------------------------
[NEW SESSION] - fallback (also the only path when resume_enabled=False)
------------------------------------------------------------
What it does:
  Starts a brand new Claude session with a short message.
  The message triggers a Claude API call, which starts a new 5h block.

Choose message:
  IF config.message is set and non-empty: use config.message
  ELSE: random.choice(["hi", "hello", "hey there", "good day", "greetings"])

Command:
  subprocess.run(["claude", "-p", msg], timeout=30)
  Note: prompt passed as CLI argument, NOT piped via stdin.
  This is cleaner than the bash `echo "msg" | claude` approach.

If succeeds (exit code 0):
  log "new session started with: <msg>"
  write current epoch to last_activity file
  notify("Session renewed")
  RETURN True

If TimeoutExpired:
  log "session timed out (30s) but may have started"
  write current epoch to last_activity file  # optimistic
  RETURN True

If fails (non-zero exit):
  log "ERROR: failed to start session (exit <N>)"
  notify("Renewal failed - will retry")
  RETURN False
```

---

## Phase 4: Manager CLI (`manager.py`)

Single entry point with subcommands via `argparse`.

### Commands

```
py manager.py start [options]   # Build config.json, launch daemon in background
py manager.py stop              # Read daemon.pid, kill process, clean up files
py manager.py restart [options] # stop + start
py manager.py status            # Show daemon state and timing info
py manager.py logs [-f]         # Print last 50 lines; -f polls for new lines every 1s
py manager.py dash              # Live dashboard, refreshes every 60s, Ctrl+C to exit
```

### Start options

| Flag | Effect |
|------|--------|
| `--at HH:MM` | Set `start_epoch` to today at HH:MM. If that time has already passed, monitoring starts immediately (same as no `--at`). |
| `--stop HH:MM` | Set `stop_epoch` to today at HH:MM. Must be after `--at` if both are given. |
| `--resume` | Set `resume_enabled=true`. On renewal, try `--continue` and `-r <id>` before starting a new session. |
| `--message "text"` | Set `message`. Used only for new sessions (ignored when resume succeeds). |
| `--disable-ccusage` | Set `disable_ccusage=true`. Skip ccusage, use clock-based timing only. |
| `--no-notify` | Set `notifications_enabled=false`. Suppress desktop notifications. |
| `--cwd /path` | Override CWD for the daemon. Default: directory where `manager.py start` was run. |

### `status` command -- what it reads and shows

1. Read `daemon.pid` -> check if process is alive (`os.kill(pid, 0)`)
2. Read `config.json` -> show start/stop times, flags
3. Read `last_activity` -> compute estimated time until next renewal
4. Read last 5 lines of `daemon.log` -> show recent activity

Output example:
```
Daemon: RUNNING (PID 12345)
Status: ACTIVE - monitoring enabled
Schedule: 09:00 - 17:00 (stops in 3h 15m)
Next renewal: ~2h 40m (estimated 14:30)
Resume: enabled
ccusage: enabled

Recent:
  [2026-02-20 11:50:01] Time remaining: 162 minutes
  [2026-02-20 11:40:01] Time remaining: 172 minutes
```

### `logs -f` implementation

Python doesn't have `tail -f`. Implement as:
```python
with open(log_path) as f:
    f.seek(0, 2)  # seek to end
    while True:
        line = f.readline()
        if line:
            print(line, end='')
        else:
            time.sleep(1)
```

### `dash` display

- ASCII-only progress bar (avoids Windows cp1252 encoding errors):
  ```
  [##########----------] 50% (2h 30m remaining)
  ```
- Sections: DAEMON STATUS | TIME TO NEXT RESET | TODAY'S PLAN | RECENT LOGS
- Refreshes every 60s, clears screen each cycle (`os.system('cls')` on Windows)
- Ctrl+C exits cleanly

---

## Phase 5: Bash `.sh` Compatibility Fixes

Quick patches to the original shell scripts for Windows/Git Bash users:

1. **Progress bar Unicode**: Replace `\u2588` and `\u2591` with `#` and `-` to avoid cp1252 encoding errors
2. **`date -j` fallback**: Wrap macOS-only `date -j` calls in `if [[ "$OSTYPE" == "darwin"* ]]` guard
3. **`generate_day_plan()` date calc**: `date -d "$current_date 00:00:00"` can fail in Git Bash; replace with `date -d "${current_date}T00:00:00"`
4. **Windows detection warning**: Add at top of each .sh: `[[ "$(uname -o 2>/dev/null)" == *"Msys"* ]] && echo "NOTE: On Windows, prefer: py manager.py"`

---

## Phase 6: Testing

**Automated: `test_daemon.py`**
- `test_get_sleep_duration_table()` -- verify all threshold boundaries
- `test_should_renew_no_ccusage()` -- mock last_activity at 5h ago, verify returns True
- `test_should_renew_near_stop_time()` -- verify returns False within 10min of stop
- `test_get_latest_session_id()` -- create temp JSONL files with different timestamps, verify newest wins
- `test_session_json_parsing()` -- verify graceful handling of corrupt/empty lines
- `test_project_slug_format()` -- verify `C:\Users\foo\bar` becomes `C--Users-foo-bar`
- `test_windows_path_chdir()` -- verify `os.chdir(os.path.normpath(path))` works with `C:\\`, `C:/`, and mixed-slash paths

**Manual smoke tests:**
- [ ] `py manager.py start --at <2min from now>` -> status shows WAITING
- [ ] At trigger time: status shows ACTIVE, log shows "Start time reached"
- [ ] `py manager.py dash` renders cleanly in Windows Terminal (no encoding errors)
- [ ] `py manager.py stop` -> PID file removed, process gone, log shows "daemon stopped"
- [ ] `py manager.py start --resume` -> on renewal, log shows "attempting --continue"
- [ ] Notification toast appears in Windows notification center on renewal
- [ ] After stop time, daemon logs "scheduling restart for tomorrow" and advances epochs
- [ ] npx cache miss: clear npm cache (`npm cache clean --force`), run daemon with ccusage, verify it downloads and works (then re-caches)

---

## Implementation Order

| Step | File | Depends on |
|------|------|-----------|
| 1 | `requirements.txt` + `py -m pip install plyer` | nothing |
| 2 | `notify.py` | plyer installed |
| 3 | `session.py` | nothing (stdlib only) |
| 4 | `daemon.py` | notify.py, session.py |
| 5 | `manager.py` | daemon.py |
| 6 | Bash `.sh` fixes | nothing |
| 7 | `test_daemon.py` + manual smoke tests | all above |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| `claude --continue` uses daemon's CWD, not the user's project dir | Resumes wrong session or fails | Daemon does `os.chdir(os.path.normpath(config.cwd))` on startup. Manager stores CWD in config. `--cwd` flag allows override. |
| Windows paths with mixed slashes or backslashes in `config.cwd` | `os.chdir` fails or resolves wrong dir | Always `os.path.normpath()` before `chdir`. Store paths via `os.path.abspath()` in manager. Verify in startup log. |
| 30s subprocess timeout too short for slow API | Missed renewal | Treat TimeoutExpired as success (session likely started). Could make timeout configurable later. |
| Clock fallback inaccurate on first run (no `last_activity`, no ccusage) | Renewal may be late or early | Acceptable tradeoff. ccusage corrects timing once available. |
| Daemon crashes unexpectedly, no auto-restart | Gap in monitoring until user notices | v1: rely on manual restart. v2: could add Windows Task Scheduler watchdog. |
| `plyer` needs `pywin32` on some Windows setups | Notifications silently fail | Catch all exceptions in notify(). Log the error. User can install pywin32 manually. |
| Windows `SIGTERM` maps to `TerminateProcess` (abrupt) | PID file may not be cleaned up | `manager.py stop` deletes PID file itself after kill. Daemon also registers `SIGBREAK` handler. |
| `Popen.pid` may differ from daemon's actual PID on Windows | `stop` kills wrong process | Daemon writes its own PID via `os.getpid()`. Manager reads from file, not from Popen. |

---

## Original Attribution

Base project by Aniket Karne (MIT License): https://github.com/aniketkarne/CCAutoRenew
