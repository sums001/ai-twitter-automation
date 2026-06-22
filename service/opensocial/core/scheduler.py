"""Slot resolution with cached daily jitter (pure functions).

A niche's posting day is resolved to a set of slot times **once per calendar
day**: a random number of slots (within the configured range) at random times
inside the posting windows. The randomness is seeded by ``(niche, date)`` so
recomputing on every scheduler tick yields the *same* slots — the jitter is
effectively "rolled once and cached" without persisting anything, so
"is this slot due yet?" never flickers.

Posts are **not** pre-assigned to slots here; the publish engine picks the
best-scoring queued post when a slot is due. The minimum gap and caps are
enforced at publish time, not baked into slot times.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo


@dataclass
class ScheduleConfig:
    windows: list[tuple[time, time]] = field(default_factory=list)
    posts_per_day: tuple[int, int] = (1, 3)
    min_gap_minutes: int = 45

    @classmethod
    def from_niche(cls, raw: dict) -> "ScheduleConfig":
        s = (raw or {}).get("schedule")
        # A niche with no ``schedule`` block is simply not on the schedule:
        # no windows → no slots → the engine never auto-publishes it.
        if not s:
            return cls(windows=[], posts_per_day=(0, 0), min_gap_minutes=45)
        windows: list[tuple[time, time]] = []
        for win in s.get("windows", [["09:00", "21:00"]]):
            start, end = win
            windows.append((_parse_hm(start), _parse_hm(end)))
        ppd = s.get("posts_per_day", [1, 3])
        if isinstance(ppd, int):
            ppd = [ppd, ppd]
        return cls(
            windows=windows or [(_parse_hm("09:00"), _parse_hm("21:00"))],
            posts_per_day=(int(ppd[0]), int(ppd[1])),
            min_gap_minutes=int(s.get("min_gap_minutes", 45)),
        )


def _parse_hm(value: str) -> time:
    hh, mm = (int(x) for x in value.split(":"))
    # The dashboard's timeline ends at "24:00" (the right edge), but
    # ``datetime.time`` tops out at 23:59 — clamp end-of-day to the last
    # minute so a window ending at midnight resolves instead of crashing.
    if hh >= 24:
        return time(23, 59)
    return time(min(hh, 23), min(mm, 59))


def _seed(niche_slug: str, day: datetime, tz: tzinfo | None = None) -> int:
    key = f"{niche_slug}:{day.astimezone(tz).date().isoformat()}"
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % (2**32)


def resolve_slots(
    cfg: ScheduleConfig,
    niche_slug: str,
    day: datetime | None = None,
    tz: tzinfo | None = None,
) -> list[datetime]:
    """Return today's posting slot times (tz-aware, sorted), stable across ticks.

    Windows are interpreted in ``tz`` — a "09:00" window means 9 AM there, which
    is also what the dashboard displays. ``tz=None`` falls back to the
    **server's local timezone** (the historical behaviour). A deterministic
    per-day RNG picks the slot count and spreads the slots across the posting
    windows by bucketing the available time, so slots are naturally spaced and
    reproducible. The per-day seed uses ``tz``'s calendar day, so the slot set
    rolls over at local midnight in the configured zone.
    """
    day = (day or datetime.now(timezone.utc)).astimezone(tz)
    rng = random.Random(_seed(niche_slug, day, tz))

    # Build concrete (start, end) datetime windows for this calendar day.
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    spans: list[tuple[datetime, datetime]] = []
    for start_t, end_t in cfg.windows:
        start = base.replace(hour=start_t.hour, minute=start_t.minute)
        end = base.replace(hour=end_t.hour, minute=end_t.minute)
        if end > start:
            spans.append((start, end))
    if not spans:
        return []

    total_seconds = sum((e - s).total_seconds() for s, e in spans)
    lo, hi = cfg.posts_per_day
    count = rng.randint(min(lo, hi), max(lo, hi))
    if count <= 0 or total_seconds <= 0:
        return []

    # Bucket the concatenated timeline into `count` equal segments and pick a
    # random instant within each, so slots are spread out across the day.
    segment = total_seconds / count
    slots: list[datetime] = []
    for i in range(count):
        offset = (i + rng.random()) * segment
        slots.append(_offset_to_time(spans, offset))
    return sorted(slots)


def _offset_to_time(spans: list[tuple[datetime, datetime]], offset: float) -> datetime:
    """Map a seconds-offset into the concatenated windows to a real datetime."""
    for start, end in spans:
        span = (end - start).total_seconds()
        if offset <= span:
            return start + timedelta(seconds=offset)
        offset -= span
    # Clamp to the end of the last window.
    return spans[-1][1]


def due_slot_count(slots: list[datetime], now: datetime, grace: timedelta) -> int:
    """Slots that came due within the last ``grace`` (no older catch-up).

    A slot is "due now" only if its time fell inside ``(now - grace, now]``.
    Slots older than that are treated as missed and skipped, so enabling
    autopilot mid-day never replays a backlog.
    """
    now = now.astimezone(timezone.utc)
    lower = now - grace
    return sum(1 for s in slots if lower < s <= now)
