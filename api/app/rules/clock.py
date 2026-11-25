"""The one source of "now". Nothing else in the app reads the system clock.

- `RunningClock` (the app): starts at APP_NOW when the process boots, then advances
  with real elapsed time, so a meeting timer can tick while "today" stays the seed's day.
- `FrozenClock` (tests and the rules report): always returns the same moment.
"""
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from .weeks import local_date, parse_utc

DEFAULT_APP_NOW = "2026-11-11T15:00:00Z"


def app_now_from_env() -> datetime:
    return parse_utc(os.environ.get("APP_NOW") or DEFAULT_APP_NOW)


class Clock(Protocol):
    def now(self) -> datetime: ...


class FrozenClock:
    def __init__(self, moment: datetime):
        self._moment = moment.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._moment


class RunningClock:
    def __init__(self, start: datetime):
        self._start = start.astimezone(timezone.utc)
        self._anchor = time.monotonic()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=time.monotonic() - self._anchor)


def today(clock: Clock) -> date:
    """The date of 'now' in company time."""
    return local_date(clock.now())
