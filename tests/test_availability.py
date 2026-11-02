from datetime import date

from app.availability import bookable_reason, grid_starts, open_starts
from app.config import SHOP_TZ
from app.models import Busy
from conftest import NOW, at

THU = date(2026, 11, 5)


def reason(start, busy=(), minutes=60, bays=1, ignore=None):
    return bookable_reason(start, minutes, list(busy), NOW, bays=bays, ignore_event_id=ignore)


def test_last_start_must_finish_before_close():
    starts = grid_starts(THU, 60, SHOP_TZ)
    assert starts[0] == at(5, 9) and starts[-1] == at(5, 17)  # 17:30 would end after the 18:00 close


def test_overlapping_event_blocks_slot():
    busy = [Busy(at(5, 15, 30), at(5, 16, 30), "a")]
    assert reason(at(5, 15, 30), busy) == "taken"
    assert reason(at(5, 15), busy) == "taken"    # 15:00-16:00 overlaps the first half hour
    assert reason(at(5, 14, 30), busy) is None   # ends exactly when the other starts
    assert reason(at(5, 16, 30), busy) is None   # starts exactly when the other ends


def test_closed_sunday_and_saturday_early_close():
    assert reason(at(8, 10)) == "closed"
    assert reason(at(7, 15)) is None             # Sat 3:00-4:00 fits
    assert reason(at(7, 15, 30)) == "invalid_time"  # would end 4:30, after the 4:00 close


def test_off_grid_time_rejected():
    assert reason(at(5, 15, 15)) == "invalid_time"


def test_minimum_notice_today():
    assert reason(at(2, 13)) == "too_soon"       # now is 12:30, need an hour
    assert reason(at(2, 13, 30)) is None
    assert reason(at(2, 9)) == "too_soon"        # already past


def test_two_bays_use_concurrency_not_event_count():
    # two back-to-back 30-min jobs both overlap a 15:00-16:00 slot but never at the same time
    busy = [Busy(at(5, 15), at(5, 15, 30), "a"), Busy(at(5, 15, 30), at(5, 16), "b")]
    assert reason(at(5, 15), busy, bays=2) is None
    assert reason(at(5, 15), busy, bays=1) == "taken"
    # two jobs at the same time fill both bays
    both = [Busy(at(5, 15), at(5, 16), "a"), Busy(at(5, 15), at(5, 16), "b")]
    assert reason(at(5, 15), both, bays=2) == "taken"


def test_ignore_own_event_for_reschedule():
    busy = [Busy(at(5, 15), at(5, 16), "mine")]
    assert reason(at(5, 15, 30), busy) == "taken"
    assert reason(at(5, 15, 30), busy, ignore="mine") is None


def test_open_starts_excludes_taken():
    busy = [Busy(at(5, 15, 30), at(5, 16, 30), "a")]
    starts = open_starts(THU, 60, busy, NOW, SHOP_TZ)
    assert at(5, 15, 30) not in starts and at(5, 16, 30) in starts
