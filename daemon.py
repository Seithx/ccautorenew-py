"""CCAutoRenew background daemon -- monitors and renews Claude 5h usage blocks."""

import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from notify import notify
from session import get_latest_session_id

DATA_DIR = Path.home() / ".claude-autorenew"
CONFIG_PATH = DATA_DIR / "config.json"
PID_PATH = DATA_DIR / "daemon.pid"
ACTIVITY_PATH = DATA_DIR / "last_activity"
LOG_PATH = DATA_DIR / "daemon.log"

BLOCK_DURATION = 18000  # 5 hours in seconds
GREETINGS = ["hi", "hello", "hey there", "good day", "greetings"]

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
    """Return True if the block was terminated early by rate limits."""
    if block is None:
        return False

    # Active block: check tokenLimitStatus projection
    if block.get("isActive"):
        tls = block.get("tokenLimitStatus", {})
        if tls.get("status") in ("exceeds", "warning"):
            log.info("Block projected to hit limit (status: %s)", tls["status"])
            return True
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


def should_renew(block: dict | None, minutes_remaining: int | None, config: dict) -> bool:
    """Decide whether to trigger a renewal now."""
    now = time.time()
    stop = config.get("stop_epoch")
    if stop and (stop - now) <= 600:
        return False  # within 10 min of stop time

    # Block exhausted by rate limits -> renew
    if is_block_exhausted(block):
        return True

    # Block still active with time left -> don't renew
    if minutes_remaining is not None:
        if minutes_remaining <= 2:
            return True
        return False

    # Clock fallback (ccusage unavailable)
    last = read_last_activity()
    if last is None:
        return True  # no history -- start initial session
    return (now - last) >= BLOCK_DURATION


# -- Session renewal ---------------------------------------------------------

def start_claude_session(config: dict) -> bool:
    """Attempt to renew the Claude session. Returns True on success."""
    notif = config.get("notifications_enabled", True)

    if config.get("resume_enabled"):
        # Attempt 1: --continue
        try:
            log.info("Attempting resume via --continue")
            result = subprocess.run(
                [_find_cmd("claude"), "--continue", "-p", "continue working"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                log.info("Resumed via --continue")
                write_last_activity()
                notify("CCAutoRenew", "Previous session resumed", notif)
                return True
            log.info("--continue failed (exit %d): %s", result.returncode, result.stderr.strip())
        except subprocess.TimeoutExpired:
            log.info("--continue timed out, treating as success")
            write_last_activity()
            notify("CCAutoRenew", "Previous session resumed", notif)
            return True
        except OSError as exc:
            log.error("claude command not found for --continue: %s", exc)

        # Attempt 2: -r <session_id>
        session_id = get_latest_session_id()
        if session_id:
            try:
                log.info("Attempting resume via -r %s", session_id)
                result = subprocess.run(
                    [_find_cmd("claude"), "-r", session_id, "-p", "continue working"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    log.info("Resumed session %s", session_id)
                    write_last_activity()
                    notify("CCAutoRenew", "Previous session resumed", notif)
                    return True
                log.info("-r failed (exit %d): %s", result.returncode, result.stderr.strip())
            except subprocess.TimeoutExpired:
                log.info("-r timed out, treating as success")
                write_last_activity()
                notify("CCAutoRenew", "Previous session resumed", notif)
                return True
            except OSError as exc:
                log.error("claude command not found for -r: %s", exc)
        else:
            log.info("No session files found, falling back to new session")

    # New session (fallback or resume_enabled=False)
    msg = config.get("message") or random.choice(GREETINGS)
    try:
        log.info("Starting new session with: %s", msg)
        result = subprocess.run(
            [_find_cmd("claude"), "-p", msg],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            log.info("New session started")
            write_last_activity()
            notify("CCAutoRenew", "New session started", notif)
            return True
        log.error("Failed to start session (exit %d): %s", result.returncode, result.stderr.strip())
        notify("CCAutoRenew", "Renewal failed - will retry", notif)
        return False
    except subprocess.TimeoutExpired:
        log.info("Session timed out (30s) but may have started")
        write_last_activity()
        return True
    except OSError as exc:
        log.error("claude command not found: %s", exc)
        notify("CCAutoRenew", "Renewal failed - claude not found", notif)
        return False


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


def main_loop(config: dict) -> None:
    notif = config.get("notifications_enabled", True)

    # Set CWD
    cwd = config.get("cwd", os.getcwd())
    os.chdir(os.path.normpath(cwd))
    log.info("CWD set to: %s", os.getcwd())

    # Write PID
    PID_PATH.write_text(str(os.getpid()))
    log.info("Daemon started (PID %d)", os.getpid())
    notify("CCAutoRenew", "Monitoring active", notif)

    while True:
        # Check if past stop time -> schedule tomorrow
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

        # Check if within active window
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

        block = query_ccusage(config)
        minutes = get_minutes_until_reset(block)
        log.info("Minutes remaining: %s", minutes if minutes is not None else "unknown (clock fallback)")

        if should_renew(block, minutes, config):
            # Wait for old block to fully expire before sending message.
            # Sending during the old block just uses it without triggering renewal.
            log.info("Renewal triggered, waiting 60s for block expiry")
            time.sleep(60)

            success = start_claude_session(config)
            if success:
                log.info("Renewal complete, cooldown 300s")
                time.sleep(300)
            else:
                log.info("Renewal failed, retrying in 60s")
                time.sleep(60)
            continue

        sleep_secs = get_sleep_duration(minutes)
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
