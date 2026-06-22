import React, { useMemo, useRef, useState } from 'react';
import { Dices, CalendarRange, ChevronRight, X, Globe, Check } from 'lucide-react';
import { useApp } from '../AppContext';
import { NICHES, NICHE_COLOR, nicheLabel } from '../data';
import { api, usePoll } from '../api';
import { fmtTime, parseTime, timelinePos, isoToMinutesTz, nowMinutesTz } from '../utils';
import { Card, SectionLabel } from '../components/common';

const LABEL_W = 188;
const STATUS_W = 124;
const ADD_DEFAULT = { windows: [['09:00', '21:00']], posts_per_day: [1, 1], min_gap_minutes: 45 };

// server "HH:MM" windows ↔ minutes
const toMin = (w) => [parseTime(w[0]), parseTime(w[1])];
const toHM = (w) => [fmtTime(w[0]), fmtTime(w[1])];

const numInput = {
  width: 60, colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8,
  color: '#e7eaef', fontFamily: "'JetBrains Mono', monospace", fontSize: 13, fontWeight: 600,
  padding: '7px 9px', textAlign: 'center',
};

// ===========================================================================
// dual-thumb range slider (posts per day)
// ===========================================================================
function RangeSlider({ lo, hi, min = 0, max = 12, onChange, color = '#3ecf8e' }) {
  const ref = useRef(null);
  const drag = useRef(null);
  const latest = useRef({ lo, hi, onChange });
  latest.current = { lo, hi, onChange };

  const pct = (v) => ((v - min) / (max - min)) * 100;
  const valFromEvent = (e) => {
    const r = ref.current.getBoundingClientRect();
    const x = Math.min(Math.max(e.clientX - r.left, 0), r.width);
    return Math.round(min + (x / r.width) * (max - min));
  };
  const apply = (v) => {
    const cur = latest.current;
    if (drag.current === 'lo') cur.onChange(Math.min(v, cur.hi), cur.hi);
    else cur.onChange(cur.lo, Math.max(v, cur.lo));
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

  return (
    <div style={{ padding: '10px 9px' }}>
      <div ref={ref} onPointerDown={begin()} style={{ position: 'relative', height: 18, cursor: 'pointer' }}>
        <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: 4, transform: 'translateY(-50%)', background: '#1c222b', borderRadius: 999 }} />
        <div style={{ position: 'absolute', top: '50%', height: 4, transform: 'translateY(-50%)', left: `${pct(lo)}%`, width: `${pct(hi) - pct(lo)}%`, background: color, borderRadius: 999, zIndex: 1 }} />
        {thumb(lo, 'lo')}
        {thumb(hi, 'hi')}
      </div>
      <div className="os-mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8.5, color: '#3a424d', marginTop: 5 }}>
        <span>{min}</span><span>{Math.round((min + max) / 2)}</span><span>{max}</span>
      </div>
    </div>
  );
}

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
// timeline track (windows + slots + now line) — shared by every lane
// ===========================================================================
function Track({ cfg, slots, tz, height = 40 }) {
  const nowMin = nowMinutesTz(tz);
  const slotMins = slots.map((s) => isoToMinutesTz(s, tz)).sort((a, b) => a - b);
  const col = cfg.color;
  return (
    <div style={{ position: 'relative', flex: 1, height, background: '#0d1116', border: '1px solid #1a1f27', borderRadius: 8, overflow: 'hidden', opacity: cfg.enabled ? 1 : 0.4 }}>
      {cfg.windows.map(([a, b], i) => (
        <div key={i} style={{ position: 'absolute', top: 0, bottom: 0, background: 'rgba(255,255,255,.045)', left: `${timelinePos(a)}%`, width: `${timelinePos(b) - timelinePos(a)}%` }} />
      ))}
      {slotMins.slice(1).map((m, i) => {
        const a = timelinePos(slotMins[i]);
        const b = timelinePos(m);
        return (
          <div key={`c${i}`} style={{ position: 'absolute', top: '50%', height: 1, transform: 'translateY(-50%)', zIndex: 1, left: `${a}%`, width: `${b - a}%`, background: 'repeating-linear-gradient(90deg,#2f3947 0,#2f3947 3px,transparent 3px,transparent 6px)' }} />
        );
      })}
      {slotMins.map((m, i) => {
        const fired = m < nowMin;
        return (
          <div key={i} title={fmtTime(m)} style={{ position: 'absolute', top: 0, bottom: 0, width: 0, zIndex: 2, left: `${timelinePos(m)}%` }}>
            <span style={{ position: 'absolute', top: '50%', left: 0, transform: 'translate(-50%,-50%)', width: 11, height: 11, borderRadius: '50%', border: '2px solid #0b0e12', background: fired ? col : '#0b0e12', boxShadow: fired ? 'none' : `inset 0 0 0 2px ${col}` }} />
          </div>
        );
      })}
      <div style={{ position: 'absolute', top: 0, bottom: 0, width: 2, zIndex: 4, background: 'rgba(62,207,142,.6)', left: `${timelinePos(nowMin)}%` }} />
    </div>
  );
}

// ===========================================================================
// collapsed lane row
// ===========================================================================
function LaneRow({ cfg, slots, owes, open, dirty, tz, onToggle }) {
  const slotCount = slots.length;
  return (
    <div
      className="lane-row"
      onClick={onToggle}
      style={{ display: 'grid', gridTemplateColumns: `${LABEL_W}px 1fr ${STATUS_W}px`, alignItems: 'center', padding: '8px 12px', borderRadius: 9, cursor: 'pointer' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, paddingRight: 12, minWidth: 0 }}>
        <span className={`lane-caret${open ? ' open' : ''}`} style={{ display: 'inline-flex', color: open ? '#3ecf8e' : '#5b6470', flexShrink: 0 }}><ChevronRight size={14} /></span>
        <span style={{ width: 9, height: 9, borderRadius: 2, flexShrink: 0, background: cfg.color }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: open ? 700 : 600, color: open ? '#fff' : '#cfd6df', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {cfg.label}{dirty ? ' *' : ''}
          </div>
          <div className="os-mono" style={{ fontSize: 9, color: '#5b6470', marginTop: 1 }}>
            {cfg.ppdMin === cfg.ppdMax ? cfg.ppdMin : `${cfg.ppdMin}–${cfg.ppdMax}`}/day · gap {cfg.minGap}m
          </div>
        </div>
      </div>
      <Track cfg={cfg} slots={slots} tz={tz} />
      <div style={{ textAlign: 'right', paddingLeft: 12 }}>
        <div className="os-mono" style={{ fontSize: 11, fontWeight: 600, color: cfg.enabled ? '#e7eaef' : '#5b6470' }}>
          {cfg.enabled ? `${slotCount} slot${slotCount === 1 ? '' : 's'}` : 'disabled'}
        </div>
        {owes > 0 && (
          <div className="os-mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 4, fontSize: 8.5, fontWeight: 700, color: '#f5a524', background: 'rgba(245,165,36,.12)', border: '1px solid rgba(245,165,36,.25)', padding: '1px 6px', borderRadius: 5 }}>
            ⟲ owes {owes}
          </div>
        )}
      </div>
    </div>
  );
}

// ===========================================================================
// inline expanded editor
// ===========================================================================
function LaneEditor({ cfg, dirty, saving, onPatch, onReset, onSave, onRemove }) {
  const setWindowRange = (i, lo, hi) => {
    const w = cfg.windows.map((x) => x.slice());
    w[i] = [lo, hi];
    onPatch({ windows: w });
  };
  const block = { background: '#0d1116', border: '1px solid #171c24', borderRadius: 11, padding: '14px 16px' };

  return (
    <div className="lane-open" style={{ padding: '4px 12px 16px', borderBottom: '1px solid #161b22', marginBottom: 4 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {/* windows */}
        <div style={{ ...block, flex: '1.5 1 320px' }}>
          <SectionLabel style={{ marginBottom: 3 }}>POSTING WINDOWS</SectionLabel>
          <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginBottom: 14 }}>drag the handles — slots only fall inside these ranges</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {cfg.windows.map(([a, b], i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <TimeRangeSlider lo={a} hi={b} color={cfg.color} onChange={(lo, hi) => setWindowRange(i, lo, hi)} />
                </div>
                <button onClick={() => onPatch({ windows: cfg.windows.filter((_, j) => j !== i) })} className="hover-remove" title="remove window" style={{ display: 'inline-flex', alignItems: 'center', border: 'none', background: 'transparent', color: '#f5455c', cursor: 'pointer', padding: '5px 6px', borderRadius: 6, flexShrink: 0, marginTop: 2 }}><X size={14} /></button>
              </div>
            ))}
            <button onClick={() => onPatch({ windows: [...cfg.windows.map((x) => x.slice()), [9 * 60, 12 * 60]] })} className="hover-add" style={{ alignSelf: 'flex-start', border: '1px dashed #2a313c', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace", fontSize: 11, padding: '6px 11px', borderRadius: 7 }}>+ add window</button>
          </div>
        </div>

        {/* posts per day + min gap */}
        <div style={{ flex: '1 1 280px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={block}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 2 }}>
              <SectionLabel>POSTS PER DAY</SectionLabel>
              <span className="os-mono" style={{ fontSize: 14, fontWeight: 700, color: '#3ecf8e' }}>
                {cfg.ppdMin === cfg.ppdMax ? cfg.ppdMin : `${cfg.ppdMin}–${cfg.ppdMax}`}
              </span>
            </div>
            <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginBottom: 2 }}>rolled fresh each day · anti-bot variance</div>
            <RangeSlider lo={cfg.ppdMin} hi={cfg.ppdMax} min={0} max={12} color={cfg.color} onChange={(lo, hi) => onPatch({ ppdMin: lo, ppdMax: hi })} />
          </div>
          <div style={block}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12 }}>
              <SectionLabel>MINIMUM GAP</SectionLabel>
              <span className="os-mono" style={{ fontSize: 14, fontWeight: 700, color: '#e7eaef' }}>{cfg.minGap}<span style={{ fontSize: 10, color: '#5b6470', fontWeight: 500 }}> min</span></span>
            </div>
            <input type="range" className="os-range" min="0" max="240" step="5" value={cfg.minGap} onChange={(e) => onPatch({ minGap: +e.target.value })} />
            <div className="os-mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8.5, color: '#3a424d', marginTop: 5 }}>
              <span>0m</span><span>2h</span><span>4h</span>
            </div>
          </div>
        </div>
      </div>

      {/* actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
        <button onClick={onRemove} className="hover-danger" style={{ border: '1px solid #232932', background: 'transparent', color: '#f5455c', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: '8px 14px', borderRadius: 8 }}>Remove from schedule</button>
        <span className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', flex: 1, lineHeight: 1.5, textAlign: 'right' }}>
          Slot times are jittered automatically and re-resolve on save.
        </span>
        <button onClick={onReset} disabled={!dirty} className="hover-border-text" style={{ border: '1px solid #232932', background: 'transparent', color: dirty ? '#9aa3af' : '#41474f', cursor: dirty ? 'pointer' : 'default', fontSize: 12, fontWeight: 600, padding: '8px 16px', borderRadius: 8 }}>Reset</button>
        <button onClick={() => !saving && dirty && onSave()} className="run-btn" style={{ border: 'none', cursor: dirty ? 'pointer' : 'default', fontSize: 12, fontWeight: 700, padding: '8px 20px', borderRadius: 8, background: dirty ? '#3ecf8e' : '#1c222b', color: dirty ? '#0a0c0f' : '#7a828f' }}>
          {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
        </button>
      </div>
    </div>
  );
}

// ===========================================================================
// add-niche picker (searchable dropdown of niches not yet on the schedule)
// ===========================================================================
function AddNichePicker({ candidates, onAdd, disabled }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const opts = candidates.filter((s) => !search || nicheLabel(s).toLowerCase().includes(search.toLowerCase()));

  return (
    <div style={{ position: 'relative', zIndex: 25 }}>
      <button
        onClick={() => !disabled && setOpen((o) => !o)}
        className="run-btn"
        disabled={disabled}
        style={{ display: 'flex', alignItems: 'center', gap: 7, border: 'none', borderRadius: 9, padding: '9px 15px', cursor: disabled ? 'default' : 'pointer', fontSize: 12.5, fontWeight: 700, background: disabled ? '#1c222b' : '#3ecf8e', color: disabled ? '#5b6470' : '#0a0c0f' }}
      >
        + Add niche
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div style={{ position: 'absolute', top: 'calc(100% + 5px)', right: 0, zIndex: 41, width: 250, background: '#141922', border: '1px solid #2a313c', borderRadius: 10, boxShadow: '0 12px 36px rgba(0,0,0,.5)', padding: 6 }}>
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search categories…"
              style={{ width: '100%', colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 7, color: '#e7eaef', fontFamily: 'inherit', fontSize: 12, padding: '7px 10px', marginBottom: 5 }}
            />
            <div style={{ maxHeight: 264, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 }}>
              {opts.length === 0 && <div className="os-mono" style={{ fontSize: 10.5, color: '#5b6470', padding: '10px' }}>all categories scheduled</div>}
              {opts.map((s) => (
                <button
                  key={s}
                  onClick={() => { onAdd(s); setOpen(false); setSearch(''); }}
                  className="hover-opt"
                  style={{ display: 'flex', alignItems: 'center', gap: 9, width: '100%', textAlign: 'left', border: 'none', background: 'transparent', cursor: 'pointer', padding: '8px 10px', borderRadius: 7, fontSize: 12.5, color: '#cfd6df' }}
                >
                  <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: NICHE_COLOR[s] }} />
                  {nicheLabel(s)}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ===========================================================================
// randomize modal
// ===========================================================================
function RandomizeModal({ count, onClose, onSubmit }) {
  const [winLo, setWinLo] = useState(9 * 60);
  const [winHi, setWinHi] = useState(21 * 60);
  const [total, setTotal] = useState(Math.max(count, 6));
  const [gap, setGap] = useState(45);
  const [busy, setBusy] = useState(false);
  const valid = count > 0 && winLo < winHi && total >= 0;

  const submit = async () => {
    if (!valid || busy) return;
    setBusy(true);
    try {
      await onSubmit({ window: [fmtTime(winLo), fmtTime(winHi)], total_posts: total, min_gap_minutes: gap });
      onClose();
    } catch (e) {
      alert(e.message);
      setBusy(false);
    }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(5,7,10,.66)', display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(2px)' }}>
      <div onClick={(e) => e.stopPropagation()} className="lane-open" style={{ width: 440, maxWidth: '92vw', background: '#111419', border: '1px solid #232b35', borderRadius: 14, padding: '22px 24px', boxShadow: '0 24px 70px rgba(0,0,0,.6)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ display: 'inline-flex', color: '#3ecf8e' }}><Dices size={20} /></span>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>Randomize schedule</div>
            <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>
              spreads a daily total randomly across {count} scheduled niche{count === 1 ? '' : 's'}
            </div>
          </div>
        </div>

        <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <SectionLabel style={{ marginBottom: 12 }}>POSTING TIME RANGE</SectionLabel>
            <TimeRangeSlider lo={winLo} hi={winHi} color="#3ecf8e" onChange={(lo, hi) => { setWinLo(lo); setWinHi(hi); }} />
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 }}>
              <SectionLabel>TOTAL POSTS PER DAY</SectionLabel>
              <span className="os-mono" style={{ fontSize: 16, fontWeight: 700, color: '#3ecf8e' }}>{total}</span>
            </div>
            <input type="range" className="os-range" min="0" max="48" step="1" value={total} onChange={(e) => setTotal(+e.target.value)} />
            <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginTop: 7 }}>
              ≈ {count ? (total / count).toFixed(1) : 0} per niche on average · each gets a random share
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <SectionLabel>MINIMUM GAP</SectionLabel>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="number" min="0" max="240" step="5" value={gap} onChange={(e) => setGap(Math.max(0, +e.target.value || 0))} style={numInput} />
              <span className="os-mono" style={{ fontSize: 11, color: '#5b6470' }}>min</span>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
          <button onClick={onClose} className="hover-border-text" style={{ flex: 1, border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontSize: 12.5, fontWeight: 600, padding: '10px', borderRadius: 9 }}>Cancel</button>
          <button onClick={submit} disabled={!valid || busy} className="run-btn" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7, flex: 1.4, border: 'none', cursor: valid && !busy ? 'pointer' : 'default', fontSize: 12.5, fontWeight: 700, padding: '10px', borderRadius: 9, background: valid && !busy ? '#3ecf8e' : '#1c222b', color: valid && !busy ? '#0a0c0f' : '#7a828f' }}>
            {busy ? 'Randomizing…' : <><Dices size={15} /> Randomize</>}
          </button>
        </div>
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
// page
// ===========================================================================
export default function Schedule() {
  const { nichesVersion, withAccount } = useApp();
  const { data: sched, reload } = usePoll(withAccount('/api/schedule'), 30000);
  const [edits, setEdits] = useState({}); // slug -> {windows(min), ppdMin, ppdMax, minGap}
  const [open, setOpen] = useState(null); // expanded slug
  const [saving, setSaving] = useState(null);
  const [query, setQuery] = useState('');
  const [randomizing, setRandomizing] = useState(false);

  const niches = sched ? sched.niches : [];
  const tzName = (sched && sched.timezone) || ''; // '' = server-local
  const scheduledSlugs = useMemo(() => new Set(niches.map((n) => n.slug)), [niches]);
  // niches not yet on the schedule (NICHES is filled by AppContext; nichesVersion forces refresh)
  const candidates = useMemo(
    () => NICHES.filter((s) => !scheduledSlugs.has(s)),
    [scheduledSlugs, nichesVersion]
  );

  const cfgOf = (n) => {
    const base = {
      enabled: n.enabled,
      label: nicheLabel(n.slug),
      color: NICHE_COLOR[n.slug] || '#7a828f',
      windows: n.windows.map(toMin),
      ppdMin: n.posts_per_day[0],
      ppdMax: n.posts_per_day[1],
      minGap: n.min_gap_minutes,
    };
    return { ...base, ...(edits[n.slug] || {}) };
  };

  const patch = (slug, partial) =>
    setEdits((all) => {
      const n = niches.find((x) => x.slug === slug);
      const cur = cfgOf(n);
      return {
        ...all,
        [slug]: {
          windows: cur.windows, ppdMin: cur.ppdMin, ppdMax: cur.ppdMax, minGap: cur.minGap,
          ...(all[slug] || {}),
          ...partial,
        },
      };
    });

  const resetSlug = (slug) =>
    setEdits((all) => { const next = { ...all }; delete next[slug]; return next; });

  const save = async (slug) => {
    setSaving(slug);
    try {
      const c = cfgOf(niches.find((x) => x.slug === slug));
      await api.put(`/api/niches/${slug}/schedule`, {
        windows: c.windows.map(toHM),
        posts_per_day: [Math.min(c.ppdMin, c.ppdMax), Math.max(c.ppdMin, c.ppdMax)],
        min_gap_minutes: c.minGap,
      });
      resetSlug(slug);
      reload();
    } catch (e) {
      alert(e.message);
    } finally {
      setSaving(null);
    }
  };

  const addNiche = async (slug) => {
    try {
      await api.put(`/api/niches/${slug}/schedule`, ADD_DEFAULT);
      reload();
      setOpen(slug);
    } catch (e) {
      alert(e.message);
    }
  };

  const removeNiche = async (slug) => {
    try {
      await api.del(`/api/niches/${slug}/schedule`);
      resetSlug(slug);
      if (open === slug) setOpen(null);
      reload();
    } catch (e) {
      alert(e.message);
    }
  };

  const randomize = async (body) => {
    await api.post('/api/schedule/randomize', body);
    setEdits({}); // server rewrote every lane
    reload();
  };

  const setTimezone = async (tz) => {
    await api.put('/api/schedule/timezone', { timezone: tz });
    reload(); // slots re-resolve in the new zone server-side
  };

  const hours = [];
  for (let h = 0; h <= 24; h += 3) hours.push(h);

  const visible = niches.filter((n) => !query || nicheLabel(n.slug).toLowerCase().includes(query.toLowerCase()));
  const totalSlots = niches.reduce((s, n) => s + (n.enabled ? n.slots.length : 0), 0);
  const owedTotal = niches.reduce((s, n) => s + (n.owes || 0), 0);
  const dirtyCount = Object.keys(edits).length;
  const empty = !!sched && niches.length === 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* ===== summary header ===== */}
      <Card style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 22, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Today's schedule</div>
          <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>now · {fmtTime(nowMinutesTz(tzName))} · {tzName ? tzName.replace(/_/g, ' ') : 'server-local time'}</div>
        </div>
        <div style={{ display: 'flex', gap: 26, marginLeft: 4 }}>
          <Stat value={totalSlots} label="slots today" color="#e7eaef" />
          <Stat value={niches.length} label="on schedule" color="#e7eaef" />
          <Stat value={owedTotal} label="owed" color={owedTotal > 0 ? '#f5a524' : '#3ecf8e'} />
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          <TimezonePicker value={tzName} onChange={setTimezone} disabled={!sched} />
          <button
            onClick={() => setRandomizing(true)}
            disabled={niches.length === 0}
            className="hover-border-text"
            style={{ display: 'flex', alignItems: 'center', gap: 7, border: '1px solid #2a313c', background: 'transparent', borderRadius: 9, padding: '8px 14px', cursor: niches.length ? 'pointer' : 'default', fontSize: 12.5, fontWeight: 600, color: niches.length ? '#cfd6df' : '#4b5563' }}
          >
            <Dices size={15} /> Randomize
          </button>
          <AddNichePicker candidates={candidates} onAdd={addNiche} disabled={!sched} />
        </div>
      </Card>

      {/* ===== timeline + lanes ===== */}
      <Card style={{ padding: '16px 20px 10px' }}>
        {!empty && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 13, marginBottom: 12, flexWrap: 'wrap' }}>
            <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>click any lane to edit it inline ↓</span>
            {niches.length > 4 && (
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter…"
                style={{ width: 150, colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8, color: '#e7eaef', fontFamily: 'inherit', fontSize: 11, padding: '6px 10px' }}
              />
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 13 }}>
              <Legend dot={<span style={{ width: 9, height: 9, borderRadius: '50%', background: '#3ecf8e' }} />} label="fired" />
              <Legend dot={<span style={{ width: 9, height: 9, borderRadius: '50%', border: '2px solid #6b7280', boxSizing: 'border-box' }} />} label="upcoming" />
              <Legend dot={<span style={{ width: 14, height: 9, borderRadius: 2, background: 'rgba(255,255,255,.06)', border: '1px solid #232b35' }} />} label="window" />
            </div>
          </div>
        )}

        {!sched && <div className="os-mono" style={{ fontSize: 11, color: '#5b6470', padding: '24px 0', textAlign: 'center' }}>loading schedule…</div>}

        {empty && (
          <div style={{ textAlign: 'center', padding: '52px 20px' }}>
            <div style={{ display: 'inline-flex', color: '#3b414b', marginBottom: 12 }}><CalendarRange size={34} /></div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#cfd6df' }}>No niches on the schedule yet</div>
            <div className="os-mono" style={{ fontSize: 11, color: '#5b6470', marginTop: 7, lineHeight: 1.6, maxWidth: 420, margin: '7px auto 0' }}>
              Add a category to start posting, then hit <span style={{ color: '#9aa3af' }}>Randomize</span> to spread a daily total across them at random times.
            </div>
            <div style={{ display: 'inline-flex', marginTop: 18 }}>
              <AddNichePicker candidates={candidates} onAdd={addNiche} disabled={!sched} />
            </div>
          </div>
        )}

        {!empty && sched && (
          <>
            {/* hour ruler — aligned to the track column */}
            <div style={{ position: 'relative', height: 14, marginLeft: LABEL_W, marginRight: STATUS_W, marginBottom: 8 }}>
              {hours.map((h) => (
                <span key={h} className="os-mono" style={{ position: 'absolute', fontSize: 9, color: '#5b6470', transform: h === 0 ? 'none' : h === 24 ? 'translateX(-100%)' : 'translateX(-50%)', left: `${timelinePos(h * 60)}%` }}>
                  {h < 10 ? `0${h}` : h}:00
                </span>
              ))}
            </div>

            {visible.length === 0 && (
              <div className="os-mono" style={{ fontSize: 11, color: '#5b6470', padding: '24px 0', textAlign: 'center' }}>no categories match this filter</div>
            )}

            {visible.map((n) => {
              const cfg = cfgOf(n);
              const isOpen = open === n.slug;
              return (
                <div key={n.slug} style={{ borderRadius: 10, background: isOpen ? 'rgba(255,255,255,.022)' : 'transparent', margin: '0 -8px' }}>
                  <LaneRow cfg={cfg} slots={n.slots} owes={n.owes} open={isOpen} dirty={!!edits[n.slug]} tz={tzName} onToggle={() => setOpen(isOpen ? null : n.slug)} />
                  {isOpen && (
                    <LaneEditor
                      cfg={cfg}
                      dirty={!!edits[n.slug]}
                      saving={saving === n.slug}
                      onPatch={(p) => patch(n.slug, p)}
                      onReset={() => resetSlug(n.slug)}
                      onSave={() => save(n.slug)}
                      onRemove={() => removeNiche(n.slug)}
                    />
                  )}
                </div>
              );
            })}

            {/* footer */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, paddingTop: 12, borderTop: '1px solid #161b22' }}>
              <span style={{ width: 2, height: 13, background: 'rgba(62,207,142,.6)' }} />
              <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>
                {owedTotal > 0
                  ? `${owedTotal} due slot${owedTotal > 1 ? 's' : ''} not yet published — catch-up runs on the next tick`
                  : 'caught up — no missed slots'}
              </span>
              {dirtyCount > 0 && (
                <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#f5a524' }}>
                  {dirtyCount} unsaved {dirtyCount > 1 ? 'edits' : 'edit'}
                </span>
              )}
            </div>
          </>
        )}
      </Card>

      {randomizing && (
        <RandomizeModal count={niches.length} onClose={() => setRandomizing(false)} onSubmit={randomize} />
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
