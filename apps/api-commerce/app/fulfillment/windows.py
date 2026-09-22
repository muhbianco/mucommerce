"""Pickup/delivery time slots from the store's weekly windows (pure).

Windows are wall-clock times in the store's time zone. A slot is offered when it starts at
least `min_lead_minutes` from now and within `days_ahead` days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.tenancy.settings_schemas import FulfillmentMode, SchedulingSettings


@dataclass(frozen=True, slots=True)
class Slot:
    date: date
    start: str  # "HH:MM", store time
    end: str
    starts_at: datetime  # aware, UTC
    ends_at: datetime


def _at(day: date, clock: str, tz: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=tz).astimezone(ZoneInfo("UTC"))


def slots(
    cfg: SchedulingSettings, tz: ZoneInfo, now: datetime, mode: FulfillmentMode
) -> list[Slot]:
    """Offered slots, soonest first."""
    if not cfg.enabled:
        return []
    earliest = now + timedelta(minutes=cfg.min_lead_minutes)
    today = now.astimezone(tz).date()
    result: list[Slot] = []
    for offset in range(cfg.days_ahead + 1):
        day = today + timedelta(days=offset)
        for window in cfg.windows:
            if window.weekday != day.weekday() or mode not in window.modes:
                continue
            starts_at = _at(day, window.start, tz)
            if starts_at < earliest:
                continue
            result.append(Slot(day, window.start, window.end, starts_at, _at(day, window.end, tz)))
    return sorted(result, key=lambda slot: slot.starts_at)


def find_slot(
    cfg: SchedulingSettings,
    tz: ZoneInfo,
    now: datetime,
    mode: FulfillmentMode,
    *,
    day: date,
    start: str,
) -> Slot | None:
    """The offered slot that starts on `day` at `start`, or None (taken from the list, so a
    slot the store does not offer — or no longer offers — is never accepted)."""
    return next((s for s in slots(cfg, tz, now, mode) if s.date == day and s.start == start), None)


def needs_slot(cfg: SchedulingSettings, mode: FulfillmentMode) -> bool:
    return cfg.enabled and any(mode in window.modes for window in cfg.windows)
