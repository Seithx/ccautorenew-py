"""CCAutoRenew background daemon -- monitors and renews Claude 5h usage blocks."""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from notify import notify
from session import get_active_sessions, get_latest_session_id, scan_for_rate_limit

DATA_DIR = Path.home() / ".claude-autorenew"
CONFIG_PATH = DATA_DIR / "config.json"
PID_PATH = DATA_DIR / "daemon.pid"
ACTIVITY_PATH = DATA_DIR / "last_activity"
LOG_PATH = DATA_DIR / "daemon.log"

BLOCK_DURATION = 18000  # 5 hours in seconds

log = logging.getLogger("ccautorenew")


def _find_cmd(name: str) -> str:
    """Resolve a command name, preferring .cmd on Windows for npm-installed tools."""
    if sys.platform == "win32":
        import shutil
        # shutil.which finds .cmd/.exe on Windows
        found = shutil.which(name)
        if found:
            return found
    return name


# -- Config -----------------------------------------------------------------

def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    defaults = {
        "start_epoch": None,
        "stop_epoch": None,
        "resume_enabled": False,
        "message": None,
        "disable_ccusage": False,
        "notifications_enabled": True,
        "cwd": os.getcwd(),
    }
    if path.exists():
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        defaults.update(stored)
    return defaults


def save_config(config: dict, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# -- Activity tracking ------------------------------------------------------

def read_last_activity() -> float | None:
    if ACTIVITY_PATH.exists():
        try:
            return float(ACTIVITY_PATH.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def write_last_activity() -> None:
    ACTIVITY_PATH.write_text(str(int(time.time())))


# -- ccusage ----------------------------------------------------------------

def _parse_iso(s: str) -> datetime:
    """Parse ISO8601 string to timezone-aware datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def query_ccusage(config: dict) -> dict | None:
    """Query ccusage for the active block. Returns the block dict or None."""
    if config.get("disable_ccusage"):
        return None

    for cmd in [
        [_find_cmd("ccusage"), "blocks", "--active", "--json"],
        [_find_cmd("npx"), "ccusage@latest", "blocks", "--active", "--json"],
    ]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=15 if "npx" in cmd else 10,
            )
            if result.returncode != 0:
                log.debug("ccusage command failed (%s): %s", cmd[0], result.stderr.strip())
                continue

            log.debug("ccusage raw output: %s", result.stdout.strip())
            data = json.loads(result.stdout)
            blocks = data.get("blocks", [])
            if not blocks:
                log.debug("ccusage returned empty blocks list")
                continue

            return blocks[0]

        except subprocess.TimeoutExpired:
            log.debug("ccusage timed out (%s)", cmd[0])
        except OSError as exc:
            log.debug("ccusage command not found (%s): %s", cmd[0], exc)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
            log.debug("ccusage parse error: %s", exc)

    return None


def get_minutes_until_reset(block: dict | None) -> int | None:
    """Compute minutes remaining from a ccusage block dict. Returns None if unavailable."""
    if block is None:
        return None
    reset_str = block.get("usageLimitResetTime") or block.get("endTime")
    if not reset_str:
        return None
    reset_time = _parse_iso(reset_str)
    remaining = (reset_time - datetime.now(timezone.utc)).total_seconds() / 60
    return max(0, int(remaining))


def is_block_exhausted(block: dict | None) -> bool:
    """Return True if a completed block ended well before its scheduled endTime.

    Only checks completed blocks. Active blocks are not checked here --
    rate limit detection is handled by JSONL scanning instead.
    """
    if block is None:
        return False
    if block.get("isActive"):
        return False

    # Completed block: check if actualEndTime is well before endTime
    actual_str = block.get("actualEndTime")
    end_str = block.get("endTime")
    if not actual_str or not end_str:
        return False
    actual = _parse_iso(actual_str)
    end = _parse_iso(end_str)
    exhausted = actual < end - timedelta(minutes=5)
    if exhausted:
        log.info("Block exhausted early (actual=%s, planned=%s)", actual_str, end_str)
    return exhausted


# -- Timing ------------------------------------------------------------------

def get_sleep_duration(minutes_remaining: int | None) -> int:
    """Return seconds to sleep before next check."""
    if minutes_remaining is None:
        # Clock fallback
        last = read_last_activity()
        if last is None:
            return 300  # 5 min -- no data yet
        elapsed = time.time() - last
        remaining_secs = BLOCK_DURATION - elapsed
        minutes_remaining = max(0, int(remaining_secs / 60))

    if minutes_remaining > 30:
        return 600
    if minutes_remaining >= 6:
        return 120
    return 30


def is_monitoring_active(config: dict) -> str | None:
    """Return None if active, or a reason string if inactive."""
    now = time.time()
    start = config.get("start_epoch")
    stop = config.get("stop_epoch")
    if start and now < start:
        return "before start time"
    if stop and now >= stop:
        return "past stop time"
    return None


def _clean_env() -> dict:
    """Return env dict without CLAUDECODE to avoid nested-session block."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def _try_claude(cmd: list[str], env: dict, label: str) -> bool | None:
    """Run a claude CLI command. Returns True=success, False=failed, None=not found."""
    try:
        log.info("Trying %s: %s", label, " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode == 0:
            log.info("%s succeeded", label)
            return True
        log.warning("%s failed (exit %d): %s", label, result.returncode, result.stderr.strip())
        return False
    except subprocess.TimeoutExpired:
        log.info("%s timed out (30s), treating as success", label)
        return True
    except OSError as exc:
        log.error("%s command not found: %s", label, exc)
        return None


def _run_renewal(config: dict, session_id: str | None = None) -> bool:
    """Renew Claude session. Tries resume -> fork -> continue fallback chain.

    session_id: if provided, targets that specific session.
    Returns True on success.
    """
    notif = config.get("notifications_enabled", True)
    env = _clean_env()  # strip CLAUDECODE to avoid nested-session block
    claude = _find_cmd("claude")

    strategies = []
    if session_id:
        # 1st: resume the exact session
        strategies.append(
            ([claude, "--resume", session_id, "-p", "continue working"], "resume")
        )
    # 2nd fallback: continue most recent session
    strategies.append(
        ([claude, "--continue", "-p", "continue working"], "continue")
    )

    for cmd, label in strategies:
        result = _try_claude(cmd, env, label)
        if result is None:
            # claude not found -- no point trying other strategies
            notify("CCAutoRenew", "Renewal failed - claude not found", notif)
            return False
        if result is True:
            write_last_activity()
            notify("CCAutoRenew", f"Session renewed ({label})", notif)
            return True
        # result is False -- try next strategy
        log.info("Strategy '%s' failed, trying next", label)

    log.error("All renewal strategies failed")
    notify("CCAutoRenew", "Renewal failed - will retry", notif)
    return False


def _launch_visible(cmd: list[str], env: dict, label: str) -> bool | None:
    """Launch a claude CLI command in a visible console window (fire-and-forget).

    Returns True=launched, False=failed to start, None=not found.
    """
    try:
        log.info("Launching visible %s: %s", label, " ".join(cmd))
        flags = subprocess.CREATE_NEW_CONSOLE
        timestamp = datetime.now().strftime("%H:%M:%S")
        title = f"CCAutoRenew - {label} - {timestamp}"
        # Write a temp .cmd script so title/banner don't interfere with stdout
        import uuid as _uuid
        script = DATA_DIR / f"_launch_{_uuid.uuid4().hex[:8]}.cmd"
        cmd_str = subprocess.list2cmdline(cmd)
        # Extract session ID from cmd args (after --resume)
        sid_full = ""
        for i, arg in enumerate(cmd):
            if arg == "--resume" and i + 1 < len(cmd):
                sid_full = cmd[i + 1]
                break
        banner_lines = [
            f"@echo off",
            f"title {title}",
            f"echo === CCAutoRenew ===",
            f"echo Session: {sid_full}" if sid_full else "",
            f"echo Project: {label}",
            f"echo Time:    {timestamp}",
            f"echo ===================",
            f"echo.",
            cmd_str,
            f"echo.",
            f"echo === Session ended ===",
            f"pause",
        ]
        script.write_text("\n".join(line for line in banner_lines if line) + "\n", encoding="utf-8")
        proc = subprocess.Popen(
            ["cmd", "/k", str(script)], env=env, creationflags=flags,
        )
        log.info("%s launched (PID %d)", label, proc.pid)
        return True
    except OSError as exc:
        log.error("%s command not found: %s", label, exc)
        return None


def _run_bulk_renewal(config: dict, sessions: list[dict]) -> tuple[int, int]:
    """Resume all active sessions in visible console windows.

    Returns (success_count, fail_count).
    """
    notif = config.get("notifications_enabled", True)
    env = _clean_env()
    claude = _find_cmd("claude")
    successes, failures = 0, 0

    for s in sessions:
        sid = s["session_id"]
        slug = s.get("slug", "unknown")
        result = _launch_visible(
            [claude, "--resume", sid, "-p", "continue working"],
            env, f"{slug} ({sid[:8]})",
        )
        if result is None:
            notify("CCAutoRenew", "Bulk renewal failed - claude not found", notif)
            return (successes, failures + len(sessions) - successes - failures)
        if result is True:
            successes += 1
        else:
            failures += 1
            log.warning("Failed to launch session %s (slug %s)", sid[:8], s["slug"])

    # Last resort: if ALL launches failed, try --continue
    if successes == 0 and sessions:
        log.info("All resumes failed, trying --continue as last resort")
        result = _launch_visible(
            [claude, "--continue", "-p", "continue working"],
            env, "continue-fallback",
        )
        if result is True:
            successes = 1

    if successes > 0:
        write_last_activity()
    summary = f"Renewed {successes}/{successes + failures} sessions"
    log.info(summary)
    notify("CCAutoRenew", summary, notif)
    return (successes, failures)


# -- Main loop ---------------------------------------------------------------

def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)


def cleanup() -> None:
    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    log.info("Daemon stopped")


def should_restart_tomorrow(config: dict) -> bool:
    stop = config.get("stop_epoch")
    if not stop:
        return False
    return time.time() >= stop


def advance_to_tomorrow(config: dict) -> None:
    for key in ("start_epoch", "stop_epoch"):
        if config.get(key):
            config[key] += 86400
    save_config(config)
    log.info("Advanced schedule to tomorrow")


def _near_stop_time(config: dict) -> bool:
    """Return True if within 10 minutes of stop time (don't renew)."""
    stop = config.get("stop_epoch")
    return bool(stop and (stop - time.time()) <= 600)


MAX_RETRIES = 5  # after this many consecutive failures, long cooldown


def main_loop(config: dict) -> None:
    notif = config.get("notifications_enabled", True)
    consecutive_failures = 0

    # Set CWD
    cwd = config.get("cwd", os.getcwd())
    os.chdir(os.path.normpath(cwd))
    log.info("CWD set to: %s", os.getcwd())

    # Write PID
    PID_PATH.write_text(str(os.getpid()))
    log.info("Daemon started (PID %d)", os.getpid())
    notify("CCAutoRenew", "Monitoring active", notif)

    while True:
        # -- Schedule checks (unchanged) --
        if should_restart_tomorrow(config):
            notify("CCAutoRenew", "Monitoring stopped for today", notif)
            advance_to_tomorrow(config)
            start = config.get("start_epoch")
            if start:
                sleep_secs = max(0, start - time.time())
                notify("CCAutoRenew", f"Will resume at {_epoch_to_hhmm(start)}", notif)
                log.info("Sleeping until tomorrow start: %ds", sleep_secs)
                time.sleep(sleep_secs)
            continue

        reason = is_monitoring_active(config)
        if reason:
            start = config.get("start_epoch")
            if reason == "before start time" and start:
                sleep_secs = max(0, start - time.time())
                log.info("Before start time, sleeping %ds", sleep_secs)
                time.sleep(sleep_secs)
            else:
                log.info("Inactive: %s, sleeping 60s", reason)
                time.sleep(60)
            continue

        # -- Layer 1: JSONL scan for rate_limit errors --
        limit = scan_for_rate_limit()
        if limit is not None and not _near_stop_time(config):
            # Weekly limit: notify only, no renewal possible
            if limit.get("is_weekly"):
                log.info("Weekly limit detected, resets at %s -- notify only", limit["reset_str"])
                notify("CCAutoRenew", f"Weekly limit hit, resets {limit['reset_str']}", notif)
                time.sleep(600)  # check again in 10min
                continue

            # Collect all active sessions for bulk renewal
            sessions = get_active_sessions(since_epoch=time.time() - BLOCK_DURATION)
            log.info("Found %d active sessions", len(sessions))

            wait_secs = limit["reset_epoch"] - time.time() + 120
            if wait_secs > 0:
                log.info("Rate limited, resets at %s, sleeping %ds", limit["reset_str"], int(wait_secs))
                notify("CCAutoRenew", f"Rate limited, resets at {limit['reset_str']}", notif)
                time.sleep(wait_secs)

            # Close to reset -- bulk renew all active sessions
            if sessions:
                log.info("Rate limit about to reset, renewing %d sessions", len(sessions))
                successes, failures = _run_bulk_renewal(config, sessions)
            else:
                # Fallback: no sessions found, try single renewal
                sid = limit.get("session_id")
                log.info("No active sessions found, falling back to single renewal (session %s)", sid)
                successes = 1 if _run_renewal(config, session_id=sid) else 0
                failures = 0 if successes else 1

            if successes > 0:
                log.info("Renewal complete, cooldown 300s")
                consecutive_failures = 0
                time.sleep(300)
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_RETRIES:
                    log.error("Renewal failed %d times, cooling down 30min", consecutive_failures)
                    notify("CCAutoRenew", "Renewal failed repeatedly, pausing 30min", notif)
                    time.sleep(1800)
                    consecutive_failures = 0
                else:
                    log.info("Renewal failed (%d/%d), retrying in 60s", consecutive_failures, MAX_RETRIES)
                    time.sleep(60)
            continue

        # -- Layer 2: ccusage timing for natural block expiry --
        block = query_ccusage(config)
        minutes = get_minutes_until_reset(block)
        log.info("Minutes remaining: %s", minutes if minutes is not None else "unknown (clock fallback)")

        # Check completed-block early end as secondary signal
        if is_block_exhausted(block) and not _near_stop_time(config):
            sid = get_latest_session_id()
            log.info("Completed block exhausted early, waiting 60s then renewing (session %s)", sid)
            time.sleep(60)
            if _run_renewal(config, session_id=sid):
                log.info("Renewal complete, cooldown 300s")
                consecutive_failures = 0
                time.sleep(300)
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_RETRIES:
                    log.error("Renewal failed %d times, cooling down 30min", consecutive_failures)
                    notify("CCAutoRenew", "Renewal failed repeatedly, pausing 30min", notif)
                    time.sleep(1800)
                    consecutive_failures = 0
                else:
                    log.info("Renewal failed (%d/%d), retrying in 60s", consecutive_failures, MAX_RETRIES)
                    time.sleep(60)
            continue

        # Decide if close to natural expiry
        needs_renew = False
        if minutes is not None:
            needs_renew = minutes <= 2
        else:
            # Clock fallback (ccusage unavailable)
            last = read_last_activity()
            if last is None:
                needs_renew = True  # no history -- start initial session
            else:
                needs_renew = (time.time() - last) >= BLOCK_DURATION

        if needs_renew and not _near_stop_time(config):
            sid = get_latest_session_id()
            log.info("Renewal triggered (session %s), waiting 60s for block expiry", sid)
            time.sleep(60)
            if _run_renewal(config, session_id=sid):
                log.info("Renewal complete, cooldown 300s")
                consecutive_failures = 0
                time.sleep(300)
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_RETRIES:
                    log.error("Renewal failed %d times, cooling down 30min", consecutive_failures)
                    notify("CCAutoRenew", "Renewal failed repeatedly, pausing 30min", notif)
                    time.sleep(1800)
                    consecutive_failures = 0
                else:
                    log.info("Renewal failed (%d/%d), retrying in 60s", consecutive_failures, MAX_RETRIES)
                    time.sleep(60)
            continue

        sleep_secs = min(get_sleep_duration(minutes), 120)  # cap for JSONL scan frequency
        log.info("Sleeping %ds", sleep_secs)
        time.sleep(sleep_secs)


def _epoch_to_hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="CCAutoRenew daemon")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)

    # Signal handlers
    def handle_shutdown(signum, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_shutdown)

    try:
        main_loop(config)
    except Exception as exc:
        log.exception("Daemon crashed: %s", exc)
        cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
