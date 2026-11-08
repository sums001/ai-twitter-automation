import React, { useEffect } from 'react';
import { STATUS_COLORS, TYPE_COLORS, NICHE_COLOR, nicheLabel } from '../data';

// ---------------------------------------------------------------------------
// Shared UI primitives. Every page builds from these so buttons, filters,
// headers, inputs and modals look and behave the same everywhere.
// ---------------------------------------------------------------------------

export const C = {
  bg: '#0a0c0f',
  surface: '#111419',
  inset: '#0d1116',
  line: '#1a1f27',
  lineStrong: '#232932',
  text: '#e7eaef',
  textSoft: '#cfd6df',
  muted: '#9aa3af',
  dim: '#7a828f',
  faint: '#5b6470',
  green: '#3ecf8e',
  amber: '#f5a524',
  red: '#f5455c',
  blue: '#7aa2f7',
};

export const MONO = "'JetBrains Mono', monospace";

// Text inputs, selects and textareas.
export const fieldStyle = {
  colorScheme: 'dark', background: C.inset, border: `1px solid #1c212a`, borderRadius: 8,
  color: C.text, fontFamily: MONO, fontSize: 12, padding: '8px 11px', outline: 'none',
};
export const textareaStyle = {
  ...fieldStyle, width: '100%', fontFamily: 'inherit', fontSize: 12.5, lineHeight: 1.55,
  padding: '10px 12px', resize: 'vertical',
};

export function Card({ style, className = '', children, ...rest }) {
  return (
    <div className={`os-card ${className}`} style={style} {...rest}>
      {children}
    </div>
  );
}

// Title + muted subtitle on one baseline, optional right-hand slot.
export function CardHeader({ title, sub, right, icon, style }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, minHeight: 22, ...style }}>
      {icon && <span style={{ display: 'inline-flex', color: C.green, flexShrink: 0 }}>{icon}</span>}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, fontSize: 13.5, color: C.text, whiteSpace: 'nowrap' }}>{title}</span>
        {sub && <span className="os-mono" style={{ fontSize: 10, color: C.faint }}>{sub}</span>}
      </div>
      {right && <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>{right}</div>}
    </div>
  );
}

const BTN_VARIANTS = {
  primary: { background: C.green, color: '#0a0c0f', border: '1px solid transparent' },
  secondary: { background: 'transparent', color: C.muted, border: `1px solid ${C.lineStrong}` },
  soft: { background: 'rgba(62,207,142,.1)', color: C.green, border: '1px solid rgba(62,207,142,.35)' },
  danger: { background: 'rgba(245,69,92,.08)', color: C.red, border: '1px solid rgba(245,69,92,.35)' },
  dangerSolid: { background: C.red, color: '#fff', border: '1px solid transparent' },
  ghost: { background: 'transparent', color: C.dim, border: '1px solid transparent' },
};
const BTN_HOVER = {
  primary: 'run-btn', secondary: 'hover-border-text', soft: 'hover-green-btn',
  danger: 'hover-danger', dangerSolid: 'hover-bright', ghost: 'hover-bright',
};
const BTN_SIZES = {
  sm: { fontSize: 11, padding: '5px 10px', borderRadius: 7, gap: 5 },
  md: { fontSize: 12, padding: '7px 14px', borderRadius: 8, gap: 7 },
};

export function Button({ variant = 'secondary', size = 'md', icon, disabled, onClick, children, style, title, type = 'button', ...rest }) {
  const v = BTN_VARIANTS[variant] || BTN_VARIANTS.secondary;
  return (
    <button
      type={type}
      title={title}
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={disabled ? undefined : BTN_HOVER[variant]}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', whiteSpace: 'nowrap',
        fontFamily: 'inherit', fontWeight: 600, cursor: disabled ? 'default' : 'pointer', flexShrink: 0,
        transition: 'background .12s, border-color .12s, color .12s',
        ...BTN_SIZES[size], ...v,
        ...(disabled ? { background: '#161b22', color: '#4b5361', border: '1px solid #1c222b' } : null),
        ...style,
      }}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
}

// A save button that reads "Saved" when there's nothing to save.
export function SaveButton({ dirty, saving, onClick, label = 'Save changes', disabled }) {
  return (
    <Button variant="primary" onClick={onClick} disabled={!dirty || saving || disabled}>
      {saving ? 'Saving…' : dirty ? label : 'Saved'}
    </Button>
  );
}

// Segmented control: one choice out of a few (filters, view tabs).
// options: [[value, label, count?], ...]
export function Segmented({ options, value, onChange, style }) {
  return (
    <div style={{ display: 'inline-flex', gap: 2, background: C.inset, border: `1px solid ${C.line}`, borderRadius: 9, padding: 3, ...style }}>
      {options.map(([val, label, count]) => {
        const active = value === val;
        return (
          <button
            key={val}
            type="button"
            onClick={() => onChange(val)}
            className={active ? undefined : 'hover-bright'}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, border: 'none', cursor: 'pointer',
              fontFamily: 'inherit', fontSize: 11.5, fontWeight: 600, padding: '5px 11px', borderRadius: 7,
              background: active ? '#1c222b' : 'transparent', color: active ? C.text : C.dim, whiteSpace: 'nowrap',
            }}
          >
            {label}
            {count != null && (
              <span className="os-mono" style={{ fontSize: 9.5, color: active ? C.faint : '#444b55' }}>{count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// An on/off filter pill ("Selected niches only").
export function FilterChip({ active, onClick, icon, children, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={active ? undefined : 'hover-bright'}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 7, cursor: 'pointer', fontSize: 11.5, fontWeight: 600,
        fontFamily: 'inherit', padding: '6px 12px', borderRadius: 8, whiteSpace: 'nowrap',
        border: `1px solid ${active ? 'rgba(62,207,142,.35)' : '#1c212a'}`,
        background: active ? 'rgba(62,207,142,.1)' : C.inset,
        color: active ? C.green : C.dim,
      }}
    >
      {icon}
      {children}
    </button>
  );
}

// Big number + small label (page summary strips).
export function Stat({ value, label, color = C.text }) {
  return (
    <div>
      <div className="os-mono" style={{ fontSize: 20, fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
      <div className="os-mono" style={{ fontSize: 9.5, color: C.faint, marginTop: 5 }}>{label}</div>
    </div>
  );
}

// Small rounded label (source, key status, upcoming…).
export function Tag({ fg = C.muted, bg = 'rgba(154,163,175,.12)', children, title, style }) {
  return (
    <span
      className="os-mono"
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 9.5, fontWeight: 600, whiteSpace: 'nowrap',
        padding: '2px 8px', borderRadius: 999, letterSpacing: '.2px', color: fg, background: bg, ...style,
      }}
    >
      {children}
    </span>
  );
}

export function Badge({ map, value }) {
  const c = map[value] || ['#9aa3af', 'rgba(154,163,175,.13)'];
  return <Tag fg={c[0]} bg={c[1]}>{value.replace('_', ' ')}</Tag>;
}

export const StatusBadge = ({ status }) => <Badge map={STATUS_COLORS} value={status} />;
export const TypeBadge = ({ type }) => <Badge map={TYPE_COLORS} value={type} />;

export function Toggle({ on, onClick, size = 20, onColor = C.green, title, disabled = false }) {
  const w = Math.round(size * 1.8);
  const knob = size - 6;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!on}
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={title}
      style={{
        position: 'relative', width: w, height: size, borderRadius: 999, border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer', flexShrink: 0, transition: 'background .15s',
        background: on ? onColor : '#262c36', opacity: disabled ? 0.4 : 1,
      }}
    >
      <span
        style={{
          position: 'absolute', top: 3, left: on ? w - knob - 3 : 3, width: knob, height: knob,
          borderRadius: '50%', background: '#0a0c0f', transition: 'left .15s',
        }}
      />
    </button>
  );
}

// A labelled setting row: title + hint on the left, control on the right.
export function SettingRow({ title, hint, children, last, align = 'center' }) {
  return (
    <div style={{ display: 'flex', alignItems: align, justifyContent: 'space-between', gap: 20, padding: '14px 0', borderBottom: last ? 'none' : `1px solid ${C.line}` }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{title}</div>
        {hint && <div className="os-mono" style={{ fontSize: 10, color: C.faint, marginTop: 3, lineHeight: 1.5 }}>{hint}</div>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>{children}</div>
    </div>
  );
}

const MODAL_TONES = {
  neutral: { border: C.lineStrong, fg: C.blue, bg: 'rgba(122,162,247,.12)' },
  green: { border: C.lineStrong, fg: C.green, bg: 'rgba(62,207,142,.12)' },
  danger: { border: '#3a232b', fg: C.red, bg: 'rgba(245,69,92,.12)' },
};

export function Modal({ onClose, title, sub, icon, tone = 'neutral', width = 440, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const t = MODAL_TONES[tone] || MODAL_TONES.neutral;
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 120, background: 'rgba(5,7,10,.66)', display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(2px)', padding: 16 }}>
      <div
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={typeof title === 'string' ? title : undefined}
        className="lane-open"
        style={{ width, maxWidth: '100%', maxHeight: '88vh', overflowY: 'auto', background: C.surface, border: `1px solid ${t.border}`, borderRadius: 14, padding: '22px 24px', boxShadow: '0 24px 70px rgba(0,0,0,.6)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
          {icon && (
            <span style={{ display: 'inline-flex', width: 34, height: 34, borderRadius: 9, alignItems: 'center', justifyContent: 'center', background: t.bg, color: t.fg, flexShrink: 0 }}>
              {icon}
            </span>
          )}
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
            {sub && <div className="os-mono" style={{ fontSize: 10, color: C.faint, marginTop: 2 }}>{sub}</div>}
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function NicheDot({ niche, size = 7, radius = 2 }) {
  return (
    <span style={{ width: size, height: size, borderRadius: radius, flexShrink: 0, background: NICHE_COLOR[niche] }} />
  );
}

export function NicheTag({ niche }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: C.textSoft }}>
      <NicheDot niche={niche} />
      <span>{nicheLabel(niche)}</span>
    </span>
  );
}

export function MediaThumb({ size = 84, url }) {
  return (
    <div
      style={{
        width: size, height: size, flexShrink: 0, borderRadius: 9, border: `1px solid ${C.lineStrong}`,
        background: 'repeating-linear-gradient(45deg,#14181e,#14181e 6px,#171c23 6px,#171c23 12px)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center', paddingBottom: 6,
        overflow: 'hidden', position: 'relative',
      }}
    >
      {url && (
        <img
          src={url}
          alt=""
          loading="lazy"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }}
          onError={(e) => { e.target.style.display = 'none'; }}
        />
      )}
      {!url && <span className="os-mono" style={{ fontSize: 8, color: '#4b5563' }}>media_url</span>}
    </div>
  );
}

export function SectionLabel({ children, style }) {
  return (
    <div className="os-mono" style={{ fontSize: 10, color: C.faint, letterSpacing: '.5px', textTransform: 'uppercase', ...style }}>
      {children}
    </div>
  );
}

export function EmptyState({ title, sub }) {
  return (
    <div style={{ textAlign: 'center', padding: '56px 20px', border: `1px dashed ${C.lineStrong}`, borderRadius: 12 }}>
      <div style={{ fontSize: 14, color: C.muted, fontWeight: 600 }}>{title}</div>
      {sub && <div className="os-mono" style={{ fontSize: 11, color: C.faint, marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

export function Loading({ children = 'loading…' }) {
  return <div className="os-mono" style={{ fontSize: 11, color: C.faint, padding: '24px 0', textAlign: 'center' }}>{children}</div>;
}

export function PulseDot({ color = C.green, size = 7, duration = '2s' }) {
  return (
    <span
      style={{
        width: size, height: size, borderRadius: '50%', background: color,
        animation: `os-pulse ${duration} infinite`, flexShrink: 0, display: 'inline-block',
      }}
    />
  );
}
