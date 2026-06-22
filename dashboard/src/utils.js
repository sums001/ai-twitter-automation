// Time/format helpers for live API data (ISO-8601 UTC strings from the service).

export function fmtTime(mins) {
  const m = Math.round(mins);
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h < 10 ? '0' : ''}${h}:${mm < 10 ? '0' : ''}${mm}`;
}

export function parseTime(v) {
  if (!v || v.indexOf(':') < 0) return NaN;
  const [a, b] = v.split(':');
  return +a * 60 + +b;
}

// Minutes since local midnight for an ISO timestamp (for the timeline).
export function isoToMinutes(iso) {
  const d = new Date(iso);
  return d.getHours() * 60 + d.getMinutes();
}

// Minutes since midnight for an ISO timestamp, rendered in an IANA timezone.
// Falls back to the browser-local clock when `tz` is empty/unset, so the
// schedule view stays correct whether or not a timezone is configured.
export function isoToMinutesTz(iso, tz) {
  if (!tz) return isoToMinutes(iso);
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date(iso));
    const h = (+parts.find((p) => p.type === 'hour').value) % 24; // 24:xx → 00:xx
    const m = +parts.find((p) => p.type === 'minute').value;
    return h * 60 + m;
  } catch {
    return isoToMinutes(iso);
  }
}

// Current minutes since midnight in an IANA timezone (browser-local if unset).
export function nowMinutesTz(tz) {
  if (!tz) return nowMinutes();
  return isoToMinutesTz(new Date().toISOString(), tz);
}

// "HH:MM" local clock for an ISO timestamp.
export function hm(iso) {
  if (!iso) return null;
  return isoToMinutes(iso) >= 0 ? fmtTime(isoToMinutes(iso)) : null;
}

// "HH:MM:SS" local clock for an ISO timestamp.
export function hms(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const p = (n) => `${n < 10 ? '0' : ''}${n}`;
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// Compact relative age: "4m", "2h", "3d".
export function ago(iso) {
  if (!iso) return '';
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins}m`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h`;
  return `${Math.floor(mins / 1440)}d`;
}

// Whole local-calendar days ago (0 = today, 1 = yesterday …).
export function daysAgo(iso) {
  const d = new Date(iso);
  const now = new Date();
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((b - a) / 86400000);
}

export function dayLabel(n) {
  if (n === 0) return 'Today';
  if (n === 1) return 'Yesterday';
  const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${WD[d.getDay()]}, ${MO[d.getMonth()]} ${d.getDate()}`;
}

export function relTime(mins) {
  if (mins <= 0) return 'now';
  if (mins < 60) return `in ${mins}m`;
  return `in ${Math.floor(mins / 60)}h ${mins % 60}m`;
}

// Current minutes since local midnight.
export function nowMinutes() {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

// timeline maps 06:00 → 24:00 to 0 → 100%
export function timelinePos(m) {
  // Full day 00:00–24:00 so early-morning / late-night windows stay visible.
  return (m / 1440) * 100;
}
