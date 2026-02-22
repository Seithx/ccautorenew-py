"""CCAutoRenew manager CLI -- start/stop/status/logs/dash for the daemon."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / ".claude-autorenew"
CONFIG_PATH = DATA_DIR / "config.json"
PID_PATH = DATA_DIR / "daemon.pid"
ACTIVITY_PATH = DATA_DIR / "last_activity"
LOG_PATH = DATA_DIR / "daemon.log"

DAEMON_SCRIPT = Path(__file__).resolve().parent / "daemon.py"
TRAY_SCRIPT = Path(__file__).resolve().parent / "tray.py"


# -- Helpers -----------------------------------------------------------------

def _read_pid() -> int | None:
    if PID_PATH.exists():
        try:
            return int(PID_PATH.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def _is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _hhmm_to_epoch(hhmm: str) -> float:
    """Convert HH:MM string to today's epoch. Returns past time as-is."""
    h, m = map(int, hhmm.split(":"))
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target.timestamp()


def _epoch_to_hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


def _read_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _tail_lines(path: Path, n: int = 50) -> list[str]:
    """Read last n lines of a file."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        return lines[-n:]
    except OSError:
        return []


def _ensure_ccusage() -> None:
    """Install ccusage globally via npm if not already present."""
    if shutil.which("ccusage"):
        return
    npm = shutil.which("npm")
    if not npm:
        print("Warning: npm not found -- cannot install ccusage. Daemon will use npx fallback (slower).")
        return
    print("ccusage not found, installing globally...")
    try:
        subprocess.run([npm, "install", "-g", "ccusage"], check=True, timeout=60)
        print("ccusage installed successfully.")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Warning: failed to install ccusage ({exc}). Daemon will use npx fallback.")


# -- Commands ----------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    if not args.disable_ccusage:
        _ensure_ccusage()

    # Check if already running
    pid = _read_pid()
    if pid and _is_alive(pid):
        print(f"Daemon already running (PID {pid})")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Build config
    config = {}
    if args.at:
        epoch = _hhmm_to_epoch(args.at)
        if epoch > time.time():
            config["start_epoch"] = epoch
        # else: past time, start immediately (no start_epoch set)
    if args.stop:
        config["stop_epoch"] = _hhmm_to_epoch(args.stop)
    config["resume_enabled"] = args.resume
    if args.message:
        config["message"] = args.message
    config["disable_ccusage"] = args.disable_ccusage
    config["notifications_enabled"] = not args.no_notify
    config["cwd"] = os.path.abspath(args.cwd or os.getcwd())

    # Save config
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    # Launch daemon as detached process
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [sys.executable, str(DAEMON_SCRIPT), "--config", str(CONFIG_PATH)],
        creationflags=creation_flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for daemon to write PID file
    for _ in range(30):  # up to 3 seconds
        time.sleep(0.1)
        pid = _read_pid()
        if pid and _is_alive(pid):
            break

    if pid and _is_alive(pid):
        print(f"Daemon started (PID {pid})")
        if config.get("start_epoch"):
            print(f"Schedule: {_epoch_to_hhmm(config['start_epoch'])}", end="")
        else:
            print("Schedule: now", end="")
        if config.get("stop_epoch"):
            print(f" - {_epoch_to_hhmm(config['stop_epoch'])}")
        else:
            print(" - continuous")
        print(f"Resume: {'enabled' if config['resume_enabled'] else 'disabled'}")
        print(f"CWD: {config['cwd']}")
    else:
        print("Failed to start daemon. Check logs:")
        print(f"  {LOG_PATH}")


def cmd_stop(args: argparse.Namespace) -> None:
    pid = _read_pid()
    if not pid:
        print("No daemon running (no PID file)")
        return
    if not _is_alive(pid):
        print(f"Daemon not running (stale PID {pid}), cleaning up")
        PID_PATH.unlink(missing_ok=True)
        return

    # Terminate process
    try:
        if sys.platform == "win32":
            # Detached processes can't receive CTRL_BREAK_EVENT; use taskkill
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            # Wait for graceful exit
            for _ in range(30):
                time.sleep(0.1)
                if not _is_alive(pid):
                    break
            if _is_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        print(f"Failed to terminate: {exc}")

    # Clean up PID file (daemon may not have cleaned it)
    PID_PATH.unlink(missing_ok=True)
    print(f"Daemon stopped (PID {pid})")


def cmd_restart(args: argparse.Namespace) -> None:
    cmd_stop(args)
    time.sleep(0.5)
    cmd_start(args)


def cmd_status(args: argparse.Namespace) -> None:
    pid = _read_pid()
    if pid and _is_alive(pid):
        print(f"Daemon: RUNNING (PID {pid})")
    elif pid:
        print(f"Daemon: DEAD (stale PID {pid})")
    else:
        print("Daemon: STOPPED")
        return

    config = _read_config()
    # Schedule
    start = config.get("start_epoch")
    stop = config.get("stop_epoch")
    now = time.time()
    if start and now < start:
        print(f"Status: WAITING - starts at {_epoch_to_hhmm(start)}")
    elif stop and now >= stop:
        print("Status: INACTIVE - past stop time")
    else:
        print("Status: ACTIVE - monitoring enabled")

    if start or stop:
        s = _epoch_to_hhmm(start) if start else "now"
        e = _epoch_to_hhmm(stop) if stop else "continuous"
        remaining = ""
        if stop:
            mins_left = max(0, int((stop - now) / 60))
            remaining = f" (stops in {mins_left // 60}h {mins_left % 60}m)"
        print(f"Schedule: {s} - {e}{remaining}")

    # Next renewal estimate
    last = None
    if ACTIVITY_PATH.exists():
        try:
            last = float(ACTIVITY_PATH.read_text().strip())
        except (ValueError, OSError):
            pass
    if last:
        elapsed = now - last
        est_remaining = max(0, int((18000 - elapsed) / 60))
        est_time = datetime.fromtimestamp(last + 18000).strftime("%H:%M")
        print(f"Next renewal: ~{est_remaining}m (estimated {est_time})")

    print(f"Resume: {'enabled' if config.get('resume_enabled') else 'disabled'}")
    print(f"ccusage: {'disabled' if config.get('disable_ccusage') else 'enabled'}")

    # Recent log lines
    recent = _tail_lines(LOG_PATH, 5)
    if recent:
        print("\nRecent:")
        for line in recent:
            print(f"  {line.rstrip()}")


def cmd_logs(args: argparse.Namespace) -> None:
    if not LOG_PATH.exists():
        print("No log file found")
        return

    lines = _tail_lines(LOG_PATH, 50)
    for line in lines:
        print(line.rstrip())

    if args.follow:
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                f.seek(0, 2)  # seek to end
                while True:
                    line = f.readline()
                    if line:
                        print(line.rstrip())
                    else:
                        time.sleep(1)
        except KeyboardInterrupt:
            pass


def cmd_dash(args: argparse.Namespace) -> None:
    try:
        while True:
            if sys.platform == "win32":
                os.system("cls")
            else:
                os.system("clear")

            print("=" * 50)
            print("  CCAutoRenew Dashboard")
            print("=" * 50)
            print()

            # Daemon status
            pid = _read_pid()
            alive = pid and _is_alive(pid)
            print(f"  Daemon: {'RUNNING' if alive else 'STOPPED'}" +
                  (f" (PID {pid})" if pid else ""))
            print()

            # Time to next reset
            config = _read_config()
            last = None
            if ACTIVITY_PATH.exists():
                try:
                    last = float(ACTIVITY_PATH.read_text().strip())
                except (ValueError, OSError):
                    pass

            if last:
                now = time.time()
                elapsed = now - last
                remaining = max(0, 18000 - elapsed)
                pct = min(100, int((elapsed / 18000) * 100))
                bar_len = 30
                filled = int(bar_len * pct / 100)
                bar = "#" * filled + "-" * (bar_len - filled)
                mins = int(remaining / 60)
                print(f"  Block: [{bar}] {pct}%")
                print(f"  Time remaining: {mins // 60}h {mins % 60}m")
            else:
                print("  Block: no data yet")
            print()

            # Schedule
            start = config.get("start_epoch")
            stop = config.get("stop_epoch")
            if start or stop:
                s = _epoch_to_hhmm(start) if start else "now"
                e = _epoch_to_hhmm(stop) if stop else "continuous"
                print(f"  Schedule: {s} - {e}")
            print(f"  Resume: {'on' if config.get('resume_enabled') else 'off'}")
            print(f"  ccusage: {'off' if config.get('disable_ccusage') else 'on'}")
            print()

            # Recent logs
            recent = _tail_lines(LOG_PATH, 5)
            if recent:
                print("  Recent logs:")
                for line in recent:
                    print(f"    {line.rstrip()}")
            print()
            print("  (Ctrl+C to exit, refreshes every 60s)")
            time.sleep(60)
    except KeyboardInterrupt:
        print()


def _get_pythonw() -> str:
    """Return pythonw.exe path (no console window) or fall back to python.exe."""
    exe = Path(sys.executable)
    pythonw = exe.parent / exe.name.replace("python", "pythonw")
    return str(pythonw) if pythonw.exists() else sys.executable


def cmd_tray(args: argparse.Namespace) -> None:
    creation_flags = 0
    interpreter = sys.executable
    if sys.platform == "win32":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        interpreter = _get_pythonw()

    subprocess.Popen(
        [interpreter, str(TRAY_SCRIPT)],
        creationflags=creation_flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("System tray icon launched")


# -- Main --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="manager",
        description="CCAutoRenew - Claude usage block renewal manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = sub.add_parser("start", help="Start the daemon")
    p_start.add_argument("--at", metavar="HH:MM", help="Start monitoring at this time")
    p_start.add_argument("--stop", metavar="HH:MM", help="Stop monitoring at this time")
    p_start.add_argument("--resume", action="store_true", help="Enable session resumption")
    p_start.add_argument("--message", help="Custom message for new sessions")
    p_start.add_argument("--disable-ccusage", action="store_true", help="Use clock-based timing only")
    p_start.add_argument("--no-notify", action="store_true", help="Disable desktop notifications")
    p_start.add_argument("--cwd", help="Working directory for Claude sessions")

    # stop
    sub.add_parser("stop", help="Stop the daemon")

    # restart
    p_restart = sub.add_parser("restart", help="Restart the daemon")
    p_restart.add_argument("--at", metavar="HH:MM", help="Start monitoring at this time")
    p_restart.add_argument("--stop", metavar="HH:MM", help="Stop monitoring at this time")
    p_restart.add_argument("--resume", action="store_true", help="Enable session resumption")
    p_restart.add_argument("--message", help="Custom message for new sessions")
    p_restart.add_argument("--disable-ccusage", action="store_true", help="Use clock-based timing only")
    p_restart.add_argument("--no-notify", action="store_true", help="Disable desktop notifications")
    p_restart.add_argument("--cwd", help="Working directory for Claude sessions")

    # status
    sub.add_parser("status", help="Show daemon status")

    # logs
    p_logs = sub.add_parser("logs", help="Show daemon logs")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow log output")

    # dash
    sub.add_parser("dash", help="Live dashboard")

    # tray
    sub.add_parser("tray", help="Launch system tray icon")

    args = parser.parse_args()
    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "logs": cmd_logs,
        "dash": cmd_dash,
        "tray": cmd_tray,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
