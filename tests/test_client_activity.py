from datetime import datetime, timedelta, timezone

from app.services.client_activity import is_recently_active


def test_currently_active_is_always_recent():
    assert is_recently_active(True, None, within_hours=24) is True


def test_inactive_with_no_history_is_not_recent():
    assert is_recently_active(False, None, within_hours=24) is False


def test_inactive_seen_within_window_is_recent():
    last_seen = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert is_recently_active(False, last_seen, within_hours=24) is True


def test_inactive_seen_outside_window_is_not_recent():
    last_seen = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    assert is_recently_active(False, last_seen, within_hours=24) is False


def test_malformed_timestamp_is_not_recent():
    assert is_recently_active(False, "not-a-timestamp", within_hours=24) is False
