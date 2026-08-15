from datetime import datetime, timedelta, timezone


def is_recently_active(device_active: bool, last_active_at: str | None, within_hours: int) -> bool:
    """A device counts as "recent" if it's online right now, or was seen
    online within the given window. `last_active_at` is tracked by us
    across poll cycles (see poller.py) since the router's API only
    reports current state, not history.
    """
    if device_active:
        return True
    if not last_active_at:
        return False
    try:
        last_seen = datetime.fromisoformat(last_active_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_seen <= timedelta(hours=within_hours)
