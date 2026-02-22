"""CCAutoRenew system tray icon -- persistent status + quick control."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

# Import state-reading helpers from existing modules (no duplication)
from manager import _read_pid, _is_alive, _read_config, _epoch_to_hhmm, DATA_DIR, LOG_PATH
from daemon import read_last_activity, BLOCK_DURATION

TRAY_SCRIPT = Path(__file__).resolve()
MANAGER_SCRIPT = Path(__file__).resolve().parent / "manager.py"

POLL_INTERVAL = 10  # seconds between state refreshes
ICON_SIZE = 64

# Colors for icon states
COLOR_GREEN = (76, 175, 80, 255)    # running
COLOR_YELLOW = (255, 193, 7, 255)   # waiting
COLOR_RED = (244, 67, 54, 255)      # stopped


# -- Icon generation ---------------------------------------------------------

def _make_icon(color: tuple) -> Image.Image:
    """Generate a solid colored circle on transparent background."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, ICON_SIZE - margin, ICON_SIZE - margin],
        fill=color,
    )
    return img


# -- State polling -----------------------------------------------------------

def _get_state() -> dict:
    """Read daemon state from existing files. Returns dict with keys:
    alive, waiting, tooltip, remaining_str, status_label
    """
    pid = _read_pid()
    alive = bool(pid and _is_alive(pid))

    if not alive:
        return {
            "alive": False,
            "waiting": False,
            "tooltip": "CCAutoRenew: Stopped",
            "remaining_str": "",
            "status_label": "STOPPED",
        }

    config = _read_config()
    now = time.time()
    start_epoch = config.get("start_epoch")
    stop_epoch = config.get("stop_epoch")

    # Check if before start time
    if start_epoch and now < start_epoch:
        start_hhmm = _epoch_to_hhmm(start_epoch)
        return {
            "alive": True,
            "waiting": True,
            "tooltip": f"CCAutoRenew: Waiting (starts {start_hhmm})",
            "remaining_str": f"Starts at {start_hhmm}",
            "status_label": "WAITING",
        }

    # Running -- compute remaining time
    last = read_last_activity()
    remaining_str = ""
    if last:
        elapsed = now - last
        remaining_secs = max(0, BLOCK_DURATION - elapsed)
        mins = int(remaining_secs / 60)
        h, m = divmod(mins, 60)
        remaining_str = f"{h}h {m:02d}m remaining"

        stop_str = ""
        if stop_epoch:
            stop_str = f" | stops {_epoch_to_hhmm(stop_epoch)}"

        tooltip = f"CCAutoRenew: Running | {h}h {m:02d}m remaining{stop_str}"
    else:
        tooltip = "CCAutoRenew: Running"

    return {
        "alive": True,
        "waiting": False,
        "tooltip": tooltip,
        "remaining_str": remaining_str,
        "status_label": "RUNNING",
    }


# -- Menu callbacks ----------------------------------------------------------

def _run_manager(action: str) -> None:
    """Run manager.py subcommand in a thread to avoid blocking the tray."""
    def _worker():
        try:
            subprocess.run(
                [sys.executable, str(MANAGER_SCRIPT), action],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                timeout=30,
            )
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


def _on_start(icon, item):
    _run_manager("start")


def _on_stop(icon, item):
    _run_manager("stop")


def _on_restart(icon, item):
    _run_manager("restart")


def _on_open_logs(icon, item):
    if LOG_PATH.exists():
        os.startfile(str(LOG_PATH))


def _on_exit(icon, item):
    icon.stop()


# -- Shared state for dynamic menu -------------------------------------------

_current_state = {"alive": False, "waiting": False, "status_label": "STOPPED",
                  "remaining_str": ""}


def _refresh_state() -> None:
    """Update shared state dict from daemon files."""
    s = _get_state()
    _current_state["alive"] = s["alive"]
    _current_state["waiting"] = s["waiting"]
    _current_state["status_label"] = s["status_label"]
    _current_state["remaining_str"] = s["remaining_str"]


# -- Dynamic menu builder ---------------------------------------------------

def _build_menu() -> tuple:
    """Return menu items tuple; called by pystray on each menu open."""
    _refresh_state()
    alive = _current_state["alive"]
    status_label = _current_state["status_label"]
    remaining = _current_state["remaining_str"]

    items = []

    if not alive:
        items.append(pystray.MenuItem("Start", _on_start))
    else:
        items.append(pystray.MenuItem("Stop", _on_stop))
        items.append(pystray.MenuItem("Restart", _on_restart))

    items.append(pystray.Menu.SEPARATOR)

    # Info lines (greyed out)
    items.append(pystray.MenuItem(
        f"Status: {status_label}", None, enabled=False,
    ))
    if remaining:
        items.append(pystray.MenuItem(remaining, None, enabled=False))

    items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem("Open Logs", _on_open_logs))
    items.append(pystray.MenuItem("Exit Tray", _on_exit))

    return tuple(items)


# -- Polling thread ----------------------------------------------------------

def _poll_loop(icon: pystray.Icon) -> None:
    """Background thread: update icon color + tooltip every POLL_INTERVAL."""
    while icon.visible:
        try:
            state = _get_state()
            if state["waiting"]:
                color = COLOR_YELLOW
            elif state["alive"]:
                color = COLOR_GREEN
            else:
                color = COLOR_RED

            icon.icon = _make_icon(color)
            icon.title = state["tooltip"]
        except Exception:
            pass  # never crash the poll loop
        time.sleep(POLL_INTERVAL)


# -- Entry point -------------------------------------------------------------

def main() -> None:
    initial = _get_state()
    if initial["alive"]:
        color = COLOR_YELLOW if initial["waiting"] else COLOR_GREEN
    else:
        color = COLOR_RED

    icon = pystray.Icon(
        name="CCAutoRenew",
        icon=_make_icon(color),
        title=initial["tooltip"],
        menu=pystray.Menu(_build_menu),
    )

    # Start polling thread
    poller = threading.Thread(target=_poll_loop, args=(icon,), daemon=True)
    poller.start()

    # icon.run() blocks (Windows message pump) -- must be on main thread
    icon.run()


def _get_pythonw() -> str | None:
    """Return path to pythonw.exe next to current interpreter."""
    exe = Path(sys.executable)
    pythonw = exe.parent / exe.name.replace("python", "pythonw")
    if pythonw.exists():
        return str(pythonw)
    return None


if __name__ == "__main__":
    # Re-launch with pythonw (no console window) if running under python.exe
    if sys.platform == "win32" and "pythonw" not in sys.executable.lower():
        pythonw = _get_pythonw()
        if pythonw:
            subprocess.Popen(
                [pythonw, str(TRAY_SCRIPT)],
                creationflags=subprocess.DETACHED_PROCESS,
            )
            sys.exit(0)
    main()
