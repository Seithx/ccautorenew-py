"""Send keystrokes to VS Code windows via Win32 API.

Finds all VS Code windows by process name (Code.exe), brings each to
foreground, and sends Ctrl+Alt+Shift+R to trigger macro-commander's
dismissRateLimitAll macro.

Usage:
    py vscode_trigger.py              # send to all VS Code windows
    py vscode_trigger.py --debug      # verbose logging
    py vscode_trigger.py --dry-run    # find windows but don't send keys
"""

import ctypes
import ctypes.wintypes
import logging
import sys
import time

log = logging.getLogger("vscode_trigger")

# -- Win32 constants --------------------------------------------------------

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

VK_CTRL = 0x11
VK_ALT = 0x12
VK_SHIFT = 0x10
VK_R = 0x52

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# -- Win32 structs ----------------------------------------------------------

ULONG_PTR = ctypes.c_void_p

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", RECT),
    ]


# -- Setup argtypes/restypes -----------------------------------------------

user32.SendInput.argtypes = (ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
user32.SendInput.restype = ctypes.c_uint
user32.GetGUIThreadInfo.argtypes = (
    ctypes.c_ulong, ctypes.POINTER(GUITHREADINFO),
)
user32.GetGUIThreadInfo.restype = ctypes.c_bool
kernel32.GetLastError.restype = ctypes.c_uint
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

# -- Low-level helpers ------------------------------------------------------


def _send_vk(vk: int, up: bool = False) -> int:
    """Send a single virtual key event. Returns SendInput result (1=ok)."""
    flags = KEYEVENTF_KEYUP if up else 0
    inp = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(ki=KEYBDINPUT(
            wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None,
        )),
    )
    res = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    err = kernel32.GetLastError()
    log.debug("SendInput vk=%s up=%s res=%d err=%d", hex(vk), up, res, err)
    return res


def _send_mouse(flags: int, dx: int = 0, dy: int = 0) -> int:
    """Send a mouse event. Returns SendInput result."""
    inp = INPUT(
        type=INPUT_MOUSE,
        union=INPUT_UNION(mi=MOUSEINPUT(
            dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0,
            dwExtraInfo=None,
        )),
    )
    res = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    err = kernel32.GetLastError()
    log.debug("SendInput mouse flags=%s res=%d err=%d", hex(flags), res, err)
    return res


def _click_center(hwnd: int):
    """Click the center of a window to ensure child focus."""
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        log.debug("GetWindowRect failed for %s", hex(hwnd))
        return
    x = (rect.left + rect.right) // 2
    y = (rect.top + rect.bottom) // 2
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    ax = int(x * 65535 / max(sw - 1, 1))
    ay = int(y * 65535 / max(sh - 1, 1))
    log.debug("Click center screen=(%d,%d) abs=(%d,%d)", x, y, ax, ay)
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)
    _send_mouse(MOUSEEVENTF_LEFTDOWN)
    _send_mouse(MOUSEEVENTF_LEFTUP)


def _get_focus_info(hwnd: int):
    """Return (ok, active_hwnd, focus_hwnd) for the thread owning hwnd."""
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    ok = user32.GetGUIThreadInfo(tid, ctypes.byref(info))
    return ok, info.hwndActive, info.hwndFocus


# -- Window finder ----------------------------------------------------------


def _get_process_name(pid: int) -> str:
    """Get the executable name for a PID. Returns '' on failure."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if ok:
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_vscode_windows() -> list[int]:
    """Find all top-level VS Code (Code.exe) window handles."""
    results = []
    seen_pids = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = pid.value
        if pid_val in seen_pids:
            return True
        name = _get_process_name(pid_val)
        if name == "code.exe":
            # Get window title for logging
            title_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title_buf, 256)
            title = title_buf.value
            log.debug("Found VS Code window: hwnd=%s pid=%d title='%s'",
                       hex(hwnd), pid_val, title[:80])
            results.append(hwnd)
            seen_pids.add(pid_val)
        return True

    user32.EnumWindows(callback, None)
    log.info("Found %d VS Code window(s)", len(results))
    return results


# -- Chord sender -----------------------------------------------------------


def send_chord(hwnd: int, delay: float = 0.2) -> bool:
    """Focus a window and send Ctrl+Alt+Shift+R. Returns True on success."""
    log.info("Sending chord to hwnd=%s", hex(hwnd))

    # Bring to foreground
    res = user32.SetForegroundWindow(hwnd)
    log.debug("SetForegroundWindow(%s) = %s", hex(hwnd), res)
    if not res:
        log.warning("SetForegroundWindow failed for %s", hex(hwnd))
        return False
    time.sleep(delay)

    # Check if focus is on top-level (needs click to focus child)
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        log.warning("Foreground is %s, expected %s -- aborting", hex(fg), hex(hwnd))
        return False

    ok, _active, focus = _get_focus_info(hwnd)
    if ok and (not focus or focus == hwnd):
        log.debug("Focus is top-level, clicking to set child focus")
        _click_center(hwnd)
        time.sleep(delay)

    # Send Ctrl+Alt+Shift+R (down then up in reverse order)
    for vk in (VK_CTRL, VK_ALT, VK_SHIFT, VK_R):
        _send_vk(vk, up=False)
    for vk in (VK_R, VK_SHIFT, VK_ALT, VK_CTRL):
        _send_vk(vk, up=True)

    log.info("Chord sent to %s", hex(hwnd))
    return True


# -- Public API -------------------------------------------------------------


def trigger_dismiss_all(delay_between: float = 0.5) -> tuple[int, int]:
    """Find all VS Code windows and send dismiss chord to each.

    Returns (success_count, fail_count).
    """
    windows = find_vscode_windows()
    if not windows:
        log.warning("No VS Code windows found")
        return 0, 0

    success = 0
    fail = 0
    for hwnd in windows:
        if send_chord(hwnd):
            success += 1
        else:
            fail += 1
        if len(windows) > 1:
            time.sleep(delay_between)

    log.info("Dismiss result: %d success, %d fail", success, fail)
    return success, fail


# -- CLI entry point --------------------------------------------------------


if __name__ == "__main__":
    level = logging.DEBUG if "--debug" in sys.argv else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    dry_run = "--dry-run" in sys.argv

    windows = find_vscode_windows()
    if not windows:
        print("No VS Code windows found.")
        sys.exit(1)

    for hwnd in windows:
        title_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, 256)
        print(f"  [{hex(hwnd)}] {title_buf.value[:80]}")

    if dry_run:
        print("Dry run -- not sending keystrokes.")
        sys.exit(0)

    print(f"Sending Ctrl+Alt+Shift+R to {len(windows)} window(s)...")
    success, fail = trigger_dismiss_all()
    print(f"Done: {success} success, {fail} fail")
