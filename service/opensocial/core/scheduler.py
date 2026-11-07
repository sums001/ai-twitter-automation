"""Per-account posting cadence: a daily window, "every X minutes", jitter.

Each workspace (= X account) posts on its own rhythm: inside its posting
window, one post roughly every ``every_minutes``, each gap randomized by
``jitter_pct`` so the timing never looks robotic. The next post time is rolled
once and stored (``ws:<id>:next_post_at``), so it is stable across worker
ticks and visible on the dashboard.

Safety limits ride along: ``min_gap_minutes`` (never two posts closer than
this, even when posting manually) and, in manual mode, ``max_pending_drafts``
(stop drafting once this many drafts await review, so an idle queue costs no
tokens). The per-account daily cap lives on the account itself.

Pure functions plus tiny load/save helpers over ``app_settings``.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo

DEFAULT_WINDOW = ("09:00", "21:00")


@dataclass
class Cadence:
    window: tuple[str, str] = DEFAULT_WINDOW
    every_minutes: int = 60
    jitter_pct: int = 20
    min_gap_minutes: int = 20
    max_pending_drafts: int = 3

    def validate(self) -> "Cadence":
        start, end = self.window
        if _parse_hm(start) >= _parse_hm(end):
            raise ValueError("window start must be before end")
        if not 5 <= int(self.every_minutes) <= 24 * 60:
            raise ValueError("every_minutes must be between 5 and 1440")
        if not 0 <= int(self.jitter_pct) <= 50:
            raise ValueError("jitter_pct must be between 0 and 50")
        if not 0 <= int(self.min_gap_minutes) <= 24 * 60:
            raise ValueError("min_gap_minutes must be between 0 and 1440")
        if not 1 <= int(self.max_pending_drafts) <= 50:
            raise ValueError("max_pending_drafts must be between 1 and 50")
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d["window"] = list(self.window)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Cadence":
        base = cls()
        win = data.get("window") or base.window
        return cls(
            window=(str(win[0]), str(win[1])),
            every_minutes=int(data.get("every_minutes", base.every_minutes)),
            jitter_pct=int(data.get("jitter_pct", base.jitter_pct)),
            min_gap_minutes=int(data.get("min_gap_minutes", base.min_gap_minutes)),
            max_pending_drafts=int(data.get("max_pending_drafts", base.max_pending_drafts)),
        )


def _parse_hm(value: str) -> time:
    hh, mm = (int(x) for x in value.split(":"))
    # "24:00" (the timeline's right edge) clamps to the last minute of the day.
    if hh >= 24:
        return time(23, 59)
    return time(min(hh, 23), min(mm, 59))


def window_bounds(cadence: Cadence, day: datetime, tz: tzinfo | None = None) -> tuple[datetime, datetime]:
    """Today's window (tz-aware) for the calendar day of ``day`` in ``tz``."""
    local = day.astimezone(tz)
    base = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_t, end_t = (_parse_hm(v) for v in cadence.window)
    return (
        base.replace(hour=start_t.hour, minute=start_t.minute),
        base.replace(hour=end_t.hour, minute=end_t.minute),
    )


def in_window(cadence: Cadence, now: datetime, tz: tzinfo | None = None) -> bool:
    start, end = window_bounds(cadence, now, tz)
    return start <= now.astimezone(tz) <= end


def _hm_minutes(value: str) -> int:
    t = _parse_hm(value)
    return t.hour * 60 + t.minute


def slot_minutes(cadence: Cadence) -> list[float]:
    """The day's post slots, as minutes after midnight: evenly spaced in the window.

    The window is cut into ``round(length / every_minutes)`` equal parts and a
    slot sits in the middle of each, so the posts spread across the whole
    window (start to end) at roughly the requested rhythm.
    """
    start = _hm_minutes(cadence.window[0])
    end = _hm_minutes(cadence.window[1])
    length = end - start
    if length <= 0:
        return []
    n = max(1, round(length / cadence.every_minutes))
    step = length / n
    return [start + step * (i + 0.5) for i in range(n)]


def _slot_step(cadence: Cadence) -> float:
    slots = slot_minutes(cadence)
    if len(slots) > 1:
        return slots[1] - slots[0]
    start, end = (_hm_minutes(v) for v in cadence.window)
    return float(max(end - start, 1))


def _slot_times(cadence: Cadence, day: datetime, tz: tzinfo | None) -> list[datetime]:
    """Slot datetimes (tz-aware) for the calendar day of ``day`` in ``tz``."""
    base = day.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return [base + timedelta(minutes=m) for m in slot_minutes(cadence)]


def _next_slot(cadence: Cadence, after: datetime, tz: tzinfo | None, days: int = 3) -> datetime | None:
    """The first slot strictly after ``after`` (unjittered), looking a few days ahead."""
    for d in range(days):
        for t in _slot_times(cadence, after + timedelta(days=d), tz):
            if t > after:
                return t
    return None


def _jittered(cadence: Cadence, slot: datetime, not_before: datetime, tz, rng: random.Random) -> datetime:
    """Nudge a slot by up to ``±jitter_pct`` of half the spacing (neighbours never cross)."""
    j = cadence.jitter_pct / 100.0
    offset = timedelta(minutes=_slot_step(cadence) / 2 * rng.uniform(-j, j))
    start, end = window_bounds(cadence, slot, tz)
    t = min(max(slot + offset, start), end)
    return max(t, not_before).astimezone(timezone.utc)


def first_post_time(
    cadence: Cadence, now: datetime, tz: tzinfo | None = None, rng: random.Random | None = None
) -> datetime:
    """When to post first, given no (or a stale) schedule: the next slot from now."""
    rng = rng or random.Random()
    slot = _next_slot(cadence, now, tz)
    return _jittered(cadence, slot, now + timedelta(minutes=1), tz, rng)


def next_post_time(
    cadence: Cadence, after: datetime, tz: tzinfo | None = None, rng: random.Random | None = None
) -> datetime:
    """The post after one at ``after``: the next slot the clock reaches.

    ``after`` may be a little early or late against its own slot (jitter), so
    the search starts half a spacing later and never lands on the same slot.
    """
    rng = rng or random.Random()
    slot = _next_slot(cadence, after + timedelta(minutes=_slot_step(cadence) / 2), tz)
    return _jittered(cadence, slot, after + timedelta(minutes=1), tz, rng)


def projected_times(
    cadence: Cadence,
    next_at: datetime | None,
    now: datetime,
    tz: tzinfo | None = None,
    *,
    days: int = 2,
) -> list[datetime]:
    """Upcoming post times for display: ``next_at``, then the following slots.

    Covers today and the following ``days - 1`` days in ``tz``.
    """
    if next_at is None:
        return []
    last_day = now.astimezone(tz).date() + timedelta(days=days - 1)
    out = [next_at.astimezone(timezone.utc)]
    t = next_at + timedelta(minutes=_slot_step(cadence) / 2)
    while len(out) < 200:
        slot = _next_slot(cadence, t, tz)
        if slot is None or slot.date() > last_day:
            break
        out.append(slot.astimezone(timezone.utc))
        t = slot
    return out


# ---------------------------------------------------------------------------
# Storage (per workspace, in app_settings)
# ---------------------------------------------------------------------------


def _seed_window(session, workspace_id: str | None, config_dir) -> tuple[str, str]:
    """Pre-cadence installs kept windows per niche: span them as the default."""
    if config_dir is None:
        return DEFAULT_WINDOW
    from opensocial.core.config import load_all_niches
    from opensocial.core.settings import get_followed_niches

    followed = set(get_followed_niches(session, workspace_id))
    starts, ends = [], []
    for n in load_all_niches(config_dir):
        sched = (n.raw or {}).get("schedule") or {}
        if n.slug not in followed or not sched.get("windows"):
            continue
        for start, end in sched["windows"]:
            starts.append(start)
            ends.append(end)
    if not starts:
        return DEFAULT_WINDOW
    return min(starts), max(ends)


def load_cadence(session, workspace_id: str | None, *, config_dir=None) -> Cadence:
    """A workspace's cadence (stored JSON, else defaults seeded from legacy config)."""
    from opensocial.core.db import get_app_setting
    from opensocial.core.settings import get_scoped_setting

    raw = get_scoped_setting(session, workspace_id, "cadence")
    if raw:
        try:
            return Cadence.from_dict(json.loads(raw))
        except (TypeError, ValueError, IndexError, KeyError):
            pass
    cadence = Cadence(window=_seed_window(session, workspace_id, config_dir))
    # The old global "fetch every X minutes" setting was the autopilot rhythm.
    legacy = get_app_setting(session, "autopilot_fetch_minutes")
    if legacy and legacy.isdigit() and int(legacy) >= 5:
        cadence.every_minutes = int(legacy)
    return cadence


def save_cadence(session, workspace_id: str | None, cadence: Cadence) -> Cadence:
    from opensocial.core.settings import set_scoped_setting

    cadence.validate()
    set_scoped_setting(session, workspace_id, "cadence", json.dumps(cadence.to_dict()))
    # A new rhythm takes effect from now: forget the old rolled time.
    clear_next_post_at(session, workspace_id)
    return cadence


def get_next_post_at(session, workspace_id: str | None) -> datetime | None:
    from opensocial.core.settings import get_scoped_setting

    raw = get_scoped_setting(session, workspace_id, "next_post_at") if workspace_id else None
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def set_next_post_at(session, workspace_id: str | None, when: datetime) -> None:
    from opensocial.core.settings import set_scoped_setting

    set_scoped_setting(
        session, workspace_id, "next_post_at", when.astimezone(timezone.utc).isoformat()
    )


def clear_next_post_at(session, workspace_id: str | None) -> None:
    from opensocial.core.settings import set_scoped_setting

    set_scoped_setting(session, workspace_id, "next_post_at", "")
