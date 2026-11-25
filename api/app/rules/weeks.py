"""Calendar math in company time.

Three kinds of time, stored three ways:
- a moment (created, done, solved) is a timezone-aware UTC datetime;
- a calendar date (a due date) is a plain `date`, meaning a day in company time;
- a week is the plain `date` of its Monday in company time (`week_start`).

Every conversion goes through the time zone database. Never add fixed hours:
the week of 2026-10-26 is 169 hours long because DST ends on Nov 1.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

COMPANY_TZ = ZoneInfo("America/Chicago")
WEEKS_ON_SCORECARD = 13


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("moments must be timezone-aware (UTC)")


def local_date(moment: datetime, tz: ZoneInfo = COMPANY_TZ) -> date:
    """The calendar date of a moment in company time."""
    _require_aware(moment)
    return moment.astimezone(tz).date()


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_of(moment: datetime, tz: ZoneInfo = COMPANY_TZ) -> date:
    """The week (its Monday) that a moment falls in, in company time."""
    return monday_of(local_date(moment, tz))


def start_of_day_utc(day: date, tz: ZoneInfo = COMPANY_TZ) -> datetime:
    """Midnight at the start of `day` in company time, as UTC.

    US DST changes happen at 02:00, so local midnight always exists exactly once.
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def week_bounds(week_start: date, tz: ZoneInfo = COMPANY_TZ) -> tuple[datetime, datetime]:
    """[start, end) of a week in UTC. End is exclusive: next Monday 00:00 company time."""
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday, got {week_start}")
    return start_of_day_utc(week_start, tz), start_of_day_utc(week_start + timedelta(days=7), tz)


def scorecard_weeks(now: datetime, count: int = WEEKS_ON_SCORECARD) -> list[date]:
    """The `count` weeks ending with the current week, oldest first."""
    current = week_of(now)
    return [current - timedelta(weeks=count - 1 - i) for i in range(count)]


def last_closed_week(now: datetime) -> date:
    """The full week before the current one."""
    return week_of(now) - timedelta(weeks=1)


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 timestamp ('...Z' or with offset) into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_aware(parsed)
    return parsed.astimezone(timezone.utc)
