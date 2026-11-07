import React, { useMemo, useRef, useState } from 'react';
import { Globe, Check } from 'lucide-react';
import { useApp } from '../AppContext';
import { NICHE_COLOR } from '../data';
import { api, usePoll } from '../api';
import { fmtTime, parseTime, timelinePos } from '../utils';
import { Card, SectionLabel } from '../components/common';

// One account, one rhythm: a daily posting window, "post every X minutes" with
// random jitter, and safety limits. The service rolls each next post time and
// writes the post from the knowledge base just before it goes out.

// ===========================================================================
// time-of-day range slider (a posting window) — drag two thumbs on a 24h track
// ===========================================================================
function TimeRangeSlider({ lo, hi, step = 15, color = '#3ecf8e', onChange }) {
  const ref = useRef(null);
  const drag = useRef(null);
  const latest = useRef({ lo, hi, onChange });
  latest.current = { lo, hi, onChange };
  const MAX = 1440;

  const pct = (v) => (v / MAX) * 100;
  const snap = (v) => Math.round(v / step) * step;
  const valFromEvent = (e) => {
    const r = ref.current.getBoundingClientRect();
    const x = Math.min(Math.max(e.clientX - r.left, 0), r.width);
    return Math.min(MAX, Math.max(0, snap((x / r.width) * MAX)));
  };
  const apply = (v) => {
    const cur = latest.current;
    // keep at least one step between the two edges
    if (drag.current === 'lo') cur.onChange(Math.min(v, cur.hi - step), cur.hi);
    else cur.onChange(cur.lo, Math.max(v, cur.lo + step));
  };
  const move = (e) => apply(valFromEvent(e));
  const end = () => {
    drag.current = null;
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', end);
  };
  const begin = (forced) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const v = valFromEvent(e);
    const cur = latest.current;
    drag.current = forced || (Math.abs(v - cur.lo) <= Math.abs(v - cur.hi) ? 'lo' : 'hi');
    apply(v);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
  };

  const thumb = (v, which) => (
    <span
      className="rng-thumb"
      onPointerDown={begin(which)}
      style={{
        position: 'absolute', top: '50%', left: `${pct(v)}%`, transform: 'translate(-50%,-50%)',
        width: 18, height: 18, borderRadius: '50%', background: '#e7eaef', border: `3px solid ${color}`,
        boxShadow: '0 1px 5px rgba(0,0,0,.5)', zIndex: 3,
      }}
    />
  );

  const ticks = [0, 6, 12, 18, 24];
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span className="os-mono" style={{ fontSize: 13, fontWeight: 700, color: '#e7eaef' }}>{fmtTime(lo)}</span>
        <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>{Math.round((hi - lo) / 60 * 10) / 10}h window</span>
        <span className="os-mono" style={{ fontSize: 13, fontWeight: 700, color: '#e7eaef' }}>{fmtTime(hi)}</span>
      </div>
      <div ref={ref} onPointerDown={begin()} style={{ position: 'relative', height: 18, cursor: 'pointer' }}>
        <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: 4, transform: 'translateY(-50%)', background: '#1c222b', borderRadius: 999 }} />
        <div style={{ position: 'absolute', top: '50%', height: 4, transform: 'translateY(-50%)', left: `${pct(lo)}%`, width: `${pct(hi) - pct(lo)}%`, background: color, borderRadius: 999, zIndex: 1 }} />
        {thumb(lo, 'lo')}
        {thumb(hi, 'hi')}
      </div>
      <div className="os-mono" style={{ position: 'relative', height: 11, marginTop: 5 }}>
        {ticks.map((h) => (
          <span key={h} style={{ position: 'absolute', left: `${(h / 24) * 100}%`, transform: h === 0 ? 'none' : h === 24 ? 'translateX(-100%)' : 'translateX(-50%)', fontSize: 8.5, color: '#3a424d' }}>
            {h === 24 ? '24' : `${h < 10 ? '0' : ''}${h}`}
          </span>
        ))}
      </div>
    </div>
  );
}

// ===========================================================================
// timezone picker — windows + slots are interpreted/displayed in this zone
// ===========================================================================
function tzOptions() {
  try {
    if (typeof Intl.supportedValuesOf === 'function') return Intl.supportedValuesOf('timeZone');
  } catch { /* older browser */ }
  return [
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Africa/Cairo', 'Asia/Kolkata',
    'Asia/Dubai', 'Asia/Singapore', 'Asia/Tokyo', 'Asia/Shanghai', 'Australia/Sydney',
  ];
}

function TimezonePicker({ value, onChange, disabled }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const zones = useMemo(tzOptions, []);
  const q = search.trim().toLowerCase().replace(/\s+/g, '_');
  const opts = zones.filter((z) => !q || z.toLowerCase().includes(q));
  const rows = [{ z: '', label: 'Server local' }, ...opts.map((z) => ({ z, label: z.replace(/_/g, ' ') }))];

  const pick = async (z) => {
    if (z === value) { setOpen(false); return; }
    setBusy(true);
    try {
      await onChange(z);
      setOpen(false);
      setSearch('');
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: 'relative', zIndex: 24 }}>
      <button
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        className="hover-border-text"
        title="Timezone posting windows are interpreted in"
        style={{ display: 'flex', alignItems: 'center', gap: 7, border: '1px solid #2a313c', background: 'transparent', borderRadius: 9, padding: '8px 12px', cursor: disabled ? 'default' : 'pointer', fontSize: 12, fontWeight: 600, color: '#cfd6df' }}
      >
        <Globe size={14} style={{ color: '#5b6470' }} />
        <span className="os-mono" style={{ fontSize: 11 }}>{value ? value.replace(/_/g, ' ') : 'Server local'}</span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div style={{ position: 'absolute', top: 'calc(100% + 5px)', right: 0, zIndex: 41, width: 280, background: '#141922', border: '1px solid #2a313c', borderRadius: 10, boxShadow: '0 12px 36px rgba(0,0,0,.5)', padding: 6 }}>
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search timezones…"
              style={{ width: '100%', colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 7, color: '#e7eaef', fontFamily: 'inherit', fontSize: 12, padding: '7px 10px', marginBottom: 5 }}
            />
            <div style={{ maxHeight: 300, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 }}>
              {rows.length === 1 && search && <div className="os-mono" style={{ fontSize: 10.5, color: '#5b6470', padding: '10px' }}>no match</div>}
              {rows.map(({ z, label }) => {
                const active = z === (value || '');
                return (
                  <button
                    key={z || '_local'}
                    onClick={() => pick(z)}
                    disabled={busy}
                    className="hover-opt"
                    style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', border: 'none', background: active ? 'rgba(62,207,142,.1)' : 'transparent', cursor: busy ? 'default' : 'pointer', padding: '8px 10px', borderRadius: 7, fontSize: 12, color: active ? '#3ecf8e' : '#cfd6df' }}
                  >
                    <span style={{ width: 14, flexShrink: 0, display: 'inline-flex' }}>{active && <Check size={13} />}</span>
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ===========================================================================
// the timeline: window band, posts made, projected post marks, now
// ===========================================================================
// The day's post slots, evenly spaced from window start to end: the window is
// cut into round(length / every) equal parts with a slot in the middle of
// each. Same math as scheduler.slot_minutes on the service, which takes the
// next slot the clock reaches.
function projectMarks({ window: [wa, wb], every, nowMin }) {
  const len = wb - wa;
  if (!every || len <= 0) return { marks: [], nextIdx: -1 };
  const n = Math.max(1, Math.round(len / every));
  const step = len / n;
  const marks = Array.from({ length: n }, (_, i) => wa + step * (i + 0.5));
  // first slot still ahead today; if today's are all gone, tomorrow's first
  let nextIdx = marks.findIndex((m) => m > nowMin);
  if (nextIdx < 0) nextIdx = 0;
  return { marks, nextIdx, past: (m) => nowMin <= wb && m <= nowMin };
}

function Timeline({ sched, cfg, proj, paused }) {
  const hours = [];
  for (let h = 0; h <= 24; h += 3) hours.push(h);
  const [wa, wb] = cfg.window;
  const nowMin = sched.now_minute;

  return (
    <div>
      <div style={{ position: 'relative', height: 14, marginBottom: 8 }}>
        {hours.map((h) => (
          <span key={h} className="os-mono" style={{ position: 'absolute', fontSize: 9, color: '#5b6470', transform: h === 0 ? 'none' : h === 24 ? 'translateX(-100%)' : 'translateX(-50%)', left: `${timelinePos(h * 60)}%` }}>
            {h < 10 ? `0${h}` : h}:00
          </span>
        ))}
      </div>
      <div style={{ position: 'relative', height: 46, background: '#0d1116', border: '1px solid #1a1f27', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: 0, bottom: 0, background: 'rgba(62,207,142,.07)', borderLeft: '1px solid rgba(62,207,142,.25)', borderRight: '1px solid rgba(62,207,142,.25)', left: `${timelinePos(wa)}%`, width: `${timelinePos(wb) - timelinePos(wa)}%` }} />
        {sched.posted_today.map((p, i) => (
          <div key={`p${i}`} title={`posted ${fmtTime(p.minute)} · ${p.text}`} style={{ position: 'absolute', top: '50%', left: `${timelinePos(p.minute)}%`, transform: 'translate(-50%,-50%)', width: 11, height: 11, borderRadius: '50%', background: '#3ecf8e', border: '2px solid #0b0e12', zIndex: 2 }} />
        ))}
        {proj.marks.map((m, i) => {
          const first = i === proj.nextIdx;
          const gone = proj.past(m);
          const ring = paused ? '#5b6470' : first ? '#3ecf8e' : '#3a424d';
          return (
            <div key={i} title={`${gone ? 'slot passed' : first ? 'next' : 'slot'} ${fmtTime(m)}${paused ? ' (paused)' : ''}`} style={{ position: 'absolute', top: '50%', left: `${timelinePos(m)}%`, transform: 'translate(-50%,-50%)', width: first ? 13 : 9, height: first ? 13 : 9, borderRadius: '50%', background: '#0b0e12', boxShadow: `inset 0 0 0 2px ${ring}`, opacity: gone ? 0.35 : paused ? 0.6 : 1, zIndex: 2 }} />
          );
        })}
        <div style={{ position: 'absolute', top: 0, bottom: 0, width: 2, zIndex: 4, background: 'rgba(245,165,36,.7)', left: `${timelinePos(nowMin)}%` }} />
      </div>
      <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <Legend dot={<span style={{ width: 9, height: 9, borderRadius: '50%', background: '#3ecf8e' }} />} label="posted" />
        <Legend dot={<span style={{ width: 9, height: 9, borderRadius: '50%', boxShadow: 'inset 0 0 0 2px #3ecf8e' }} />} label="next post" />
        <Legend dot={<span style={{ width: 9, height: 9, borderRadius: '50%', boxShadow: 'inset 0 0 0 2px #3a424d' }} />} label="approx. later" />
        <Legend dot={<span style={{ width: 14, height: 9, borderRadius: 2, background: 'rgba(62,207,142,.1)', border: '1px solid rgba(62,207,142,.3)' }} />} label="posting window" />
        <Legend dot={<span style={{ width: 2, height: 11, background: 'rgba(245,165,36,.7)' }} />} label="now" />
      </div>
    </div>
  );
}

function SliderRow({ label, value, display, suffix, hint, min, max, step, onChange }) {
  return (
    <div style={{ background: '#0d1116', border: '1px solid #171c24', borderRadius: 11, padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 4 }}>
        <SectionLabel>{label}</SectionLabel>
        <span className="os-mono" style={{ fontSize: 14, fontWeight: 700, color: '#e7eaef' }}>
          {display ?? value}<span style={{ fontSize: 10, color: '#5b6470', fontWeight: 500 }}> {suffix}</span>
        </span>
      </div>
      <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginBottom: 10 }}>{hint}</div>
      <input type="range" className="os-range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} />
    </div>
  );
}

// ===========================================================================
// page
// ===========================================================================
export default function Schedule() {
  const { withAccount } = useApp();
  const { data: sched, reload } = usePoll(withAccount('/api/schedule'), 30000);
  const [edit, setEdit] = useState(null); // unsaved cadence edits
  const [saving, setSaving] = useState(false);

  const tzName = (sched && sched.timezone) || '';
  const server = sched ? sched.cadence : null;
  const cur = server ? { ...server, ...(edit || {}) } : null;
  const cfg = cur ? { ...cur, window: [parseTime(cur.window[0]), parseTime(cur.window[1])] } : null;
  const dirty = !!edit;

  const patch = (p) => setEdit((e) => ({ ...(e || {}), ...p }));
  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await api.put(withAccount('/api/schedule'), cur);
      setEdit(null);
      reload();
    } catch (e) {
      alert(e.message);
    } finally {
      setSaving(false);
    }
  };
  const setTimezone = async (tz) => {
    await api.put('/api/schedule/timezone', { timezone: tz });
    reload();
  };

  if (!sched || !cfg) {
    return <Card style={{ padding: 24 }}><div className="os-mono" style={{ fontSize: 11, color: '#5b6470', textAlign: 'center' }}>loading schedule…</div></Card>;
  }

  const nowMin = sched.now_minute;
  const next = sched.next_post;
  const cap = sched.effective_cap;
  const auto = sched.mode === 'auto';
  const winLen = (cfg.window[1] - cfg.window[0]) / 60;
  const perDay = Math.max(0, Math.round((cfg.window[1] - cfg.window[0]) / cur.every_minutes));
  const idle = sched.followed_niches.length === 0;
  const status = sched.status || { state: 'ok', message: '' };
  const blocked = ['paused', 'capped', 'held', 'idle'].includes(status.state);
  const dayWord = (d) => (d === 0 ? 'today' : d === 1 ? 'tomorrow' : `in ${d} days`);
  const proj = projectMarks({ window: cfg.window, every: cur.every_minutes, nowMin });
  // While editing, the headline follows the preview; otherwise the server's rolled
  // time (the slot plus its random nudge).
  const previewNext = proj.marks.length ? proj.marks[proj.nextIdx] : null;
  const nextMin = dirty ? previewNext : next ? next.minute : null;
  const nextDay = dirty ? (previewNext != null && previewNext <= nowMin ? 1 : 0) : next ? next.day : 0;
  const nextLabel = `${auto ? 'next post' : 'next draft'}${nextMin != null ? ` · ${dayWord(nextDay)}` : ''}${blocked ? ' · paused' : ''}`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* ===== summary ===== */}
      <Card style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 22, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Posting schedule</div>
          <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>
            now · {fmtTime(nowMin)} · {tzName ? tzName.replace(/_/g, ' ') : 'server-local time'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 26, marginLeft: 4, flexWrap: 'wrap' }}>
          <Stat value={nextMin != null ? fmtTime(nextMin) : '—'} label={nextLabel} color={blocked ? '#7a828f' : '#3ecf8e'} />
          <Stat value={cap ? `${sched.posted_today.length}/${cap}` : sched.posted_today.length} label="posted today" color="#e7eaef" />
          <Stat value={sched.pending_drafts} label="drafts waiting" color={sched.pending_drafts ? '#f5a524' : '#e7eaef'} />
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <TimezonePicker value={tzName} onChange={setTimezone} />
        </div>
      </Card>

      {/* ===== timeline ===== */}
      <Card style={{ padding: '16px 20px' }}>
        <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginBottom: 12, lineHeight: 1.6 }}>
          {idle
            ? 'Follow at least one niche on the Niches page to start posting.'
            : auto
              ? `Autopilot posts about every ${cur.every_minutes} min (±${cur.jitter_pct}%) inside the window. Each post is written from the freshest unused story, fetched ~5 min before.`
              : `Manual mode: a draft is written about every ${cur.every_minutes} min for you to review, until ${cur.max_pending_drafts} are waiting. Switch on Auto in the top bar to publish unattended.`}
        </div>
        {status.message && status.state !== 'idle' && (
          <div style={{ fontSize: 12, lineHeight: 1.5, color: '#f5a524', background: 'rgba(245,165,36,.08)', border: '1px solid rgba(245,165,36,.25)', borderRadius: 8, padding: '9px 12px', marginBottom: 12 }}>
            {status.message}
          </div>
        )}
        <Timeline sched={sched} cfg={cfg} proj={proj} paused={blocked} />
        {dirty && (
          <div className="os-mono" style={{ fontSize: 10, color: '#f5a524', marginTop: 8 }}>
            preview of unsaved changes · save to apply
          </div>
        )}
      </Card>

      {/* ===== cadence editor ===== */}
      <Card style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ flex: '1.4 1 320px', background: '#0d1116', border: '1px solid #171c24', borderRadius: 11, padding: '14px 16px' }}>
            <SectionLabel style={{ marginBottom: 3 }}>POSTING WINDOW</SectionLabel>
            <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginBottom: 14 }}>posts only go out between these times</div>
            <TimeRangeSlider
              lo={cfg.window[0]} hi={cfg.window[1]}
              onChange={(lo, hi) => patch({ window: [fmtTime(lo), fmtTime(Math.min(hi, 1439))] })}
            />
          </div>
          <div style={{ flex: '1 1 260px', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <SliderRow
              label="POST EVERY" value={cur.every_minutes} suffix="min" min={15} max={240} step={5}
              hint={`≈ ${perDay} post${perDay === 1 ? '' : 's'} across a ${Math.round(winLen * 10) / 10}h window`}
              onChange={(v) => patch({ every_minutes: v })}
            />
            <SliderRow
              label="RANDOMNESS" value={cur.jitter_pct} display={`±${cur.jitter_pct}`} suffix="%" min={0} max={50} step={5}
              hint="each gap varies by this much, so timing never looks robotic"
              onChange={(v) => patch({ jitter_pct: v })}
            />
          </div>
          <div style={{ flex: '1 1 260px', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <SliderRow
              label="MINIMUM GAP" value={cur.min_gap_minutes} suffix="min" min={0} max={120} step={5}
              hint="safety: never two posts closer than this"
              onChange={(v) => patch({ min_gap_minutes: v })}
            />
            <SliderRow
              label="DRAFTS TO REVIEW" value={cur.max_pending_drafts} suffix="max" min={1} max={10} step={1}
              hint="manual mode stops writing at this many waiting (saves tokens)"
              onChange={(v) => patch({ max_pending_drafts: v })}
            />
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
          <span className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', flex: 1, lineHeight: 1.5 }}>
            {cap ? `Daily cap: ${cap} posts (set per account in Settings).` : 'No daily cap set.'} Saving re-rolls the next post time.
          </span>
          <button onClick={() => setEdit(null)} disabled={!dirty} className="hover-border-text" style={{ border: '1px solid #232932', background: 'transparent', color: dirty ? '#9aa3af' : '#41474f', cursor: dirty ? 'pointer' : 'default', fontSize: 12, fontWeight: 600, padding: '8px 16px', borderRadius: 8 }}>Reset</button>
          <button onClick={save} className="run-btn" style={{ border: 'none', cursor: dirty ? 'pointer' : 'default', fontSize: 12, fontWeight: 700, padding: '8px 20px', borderRadius: 8, background: dirty ? '#3ecf8e' : '#1c222b', color: dirty ? '#0a0c0f' : '#7a828f' }}>
            {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
          </button>
        </div>
      </Card>

      {/* ===== today's posts ===== */}
      {sched.posted_today.length > 0 && (
        <Card style={{ padding: '16px 20px' }}>
          <SectionLabel style={{ marginBottom: 10 }}>POSTED TODAY</SectionLabel>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sched.posted_today.slice().reverse().map((p) => (
              <div key={p.post_id} style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
                <span className="os-mono" style={{ fontSize: 11, color: '#9aa3af', flexShrink: 0 }}>{fmtTime(p.minute)}</span>
                <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: NICHE_COLOR[p.niche] || '#7a828f' }} />
                <span style={{ fontSize: 12, color: '#cfd6df', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.text.replace(/\s+/g, ' ')}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function Stat({ value, label, color }) {
  return (
    <div>
      <div className="os-mono" style={{ fontSize: 20, fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
      <div className="os-mono" style={{ fontSize: 9, color: '#5b6470', marginTop: 4 }}>{label}</div>
    </div>
  );
}

function Legend({ dot, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#9aa3af' }}>
      {dot}{label}
    </span>
  );
}
