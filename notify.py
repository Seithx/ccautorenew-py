"""Desktop notification wrapper. Never crashes the caller."""

import logging

log = logging.getLogger("ccautorenew")


def notify(title: str, message: str, enabled: bool = True) -> None:
    """Show a desktop toast notification. Silently fails on error."""
    if not enabled:
        return
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=5)
    except Exception as exc:
        log.debug("Notification failed: %s", exc)
