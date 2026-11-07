import React, { useEffect, useState } from 'react';
import { useApp } from '../AppContext';
import { NICHE_COLOR, nicheLabel } from '../data';
import { api } from '../api';
import { Card, Toggle } from './common';

// Per-niche post text controls (Voice column).
const POST_STYLES = [
  ['casual', 'Casual'], ['funny', 'Funny'], ['informative', 'Informative'],
  ['professional', 'Professional'], ['question', 'Question'], ['supportive', 'Supportive'],
];
const POST_LENGTHS = [
  ['very_short', 'Very Short', '2–5 words'], ['short', 'Short', 'A sentence'],
  ['medium', 'Medium', '1–2 lines'], ['long', 'Long', '3–4 lines'],
];

const inputStyle = {
  colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8,
  color: '#e7eaef', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, padding: '8px 11px',
};
const numStyle = { ...inputStyle, width: 86, textAlign: 'center', fontWeight: 600 };

const chip = (fg, bg, border) => ({
  fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: fg, background: bg,
  border: border || 'none', padding: '4px 10px', borderRadius: 7,
});

// chips with add/remove backed by a string array in the config
function ChipList({ items, onChange, fg, bg, border, placeholder }) {
  const [adding, setAdding] = useState(false);
  const [value, setValue] = useState('');
  const add = () => {
    const v = value.trim().toLowerCase();
    if (v && !items.includes(v)) onChange([...items, v]);
    setValue('');
    setAdding(false);
  };
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
      {items.map((w) => (
        <span key={w} style={{ ...chip(fg, bg, border), display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          {w}
          <span
            onClick={() => onChange(items.filter((x) => x !== w))}
            style={{ cursor: 'pointer', opacity: 0.7, fontWeight: 700 }}
            title="remove"
          >
            ×
          </span>
        </span>
      ))}
      {adding ? (
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') add(); if (e.key === 'Escape') setAdding(false); }}
          onBlur={add}
          placeholder={placeholder}
          style={{ ...inputStyle, padding: '4px 9px', fontSize: 11, width: 140 }}
        />
      ) : (
        <span onClick={() => setAdding(true)} style={{ ...chip('#5b6470', 'transparent', '1px dashed #2a313c'), cursor: 'pointer' }}>+ add</span>
      )}
    </div>
  );
}

// A grid of single-select option buttons (post style / length).
function SelectGrid({ options, value, onChange, color, cols = 3 }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 9 }}>
      {options.map(([key, label, hint]) => {
        const on = value === key;
        return (
          <button
            key={key}
            onClick={() => onChange(on ? '' : key)}
            style={{
              cursor: 'pointer', textAlign: 'center', padding: '12px 10px', borderRadius: 10,
              background: on ? `color-mix(in oklch, ${color} 14%, transparent)` : '#0d1116',
              border: on ? `1px solid ${color}` : '1px solid #1c212a',
              color: on ? '#e7eaef' : '#9aa3af', transition: 'all .12s',
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>
            {hint && <div className="os-mono" style={{ fontSize: 9.5, color: on ? '#9aa3af' : '#5b6470', marginTop: 3 }}>{hint}</div>}
          </button>
        );
      })}
    </div>
  );
}

const label = { fontSize: 13, fontWeight: 600, marginBottom: 4 };
const hintStyle = { fontSize: 10, color: '#5b6470', marginBottom: 10 };
const colTitle = {
  fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700, letterSpacing: '.08em',
  textTransform: 'uppercase', color: '#7a828f', paddingBottom: 10, marginBottom: 6, borderBottom: '1px solid #1a1f27',
};

function Field({ title, hint, right, children }) {
  return (
    <div style={{ paddingTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div>
          <div style={label}>{title}</div>
          {hint && <div className="os-mono" style={{ ...hintStyle, marginBottom: children ? 10 : 0 }}>{hint}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

// Column 1 — who the niche is and how its posts sound.
function VoiceColumn({ cfg, set, color }) {
  const persona = cfg.persona || {};
  return (
    <div>
      <div style={colTitle}>Voice</div>
      <Field title="Display name" hint={`slug · ${cfg.slug}`}>
        <input
          value={cfg.display_name || ''}
          onChange={(e) => set(['display_name'], e.target.value)}
          style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }}
        />
      </Field>
      <Field title="Post style" hint="the voice every post is written in">
        <SelectGrid options={POST_STYLES} value={persona.style || ''} onChange={(v) => set(['persona', 'style'], v)} color={color} cols={2} />
      </Field>
      <Field title="Post length">
        <SelectGrid options={POST_LENGTHS} value={persona.length || ''} onChange={(v) => set(['persona', 'length'], v)} color={color} cols={2} />
      </Field>
    </div>
  );
}

// Column 2 — extra guidance, images, and an optional voice override.
function ContentColumn({ cfg, set }) {
  const persona = cfg.persona || {};
  // Optional per-niche voice samples, edited as one text block split on '---'.
  const examples = Array.isArray(persona.examples) ? persona.examples : [];
  const textarea = {
    width: '100%', boxSizing: 'border-box', colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a',
    borderRadius: 10, padding: 12, fontSize: 13, lineHeight: 1.55, color: '#d5dae1',
    fontFamily: 'inherit', resize: 'vertical',
  };
  return (
    <div>
      <div style={colTitle}>Content</div>
      <Field title="Custom instructions" hint="optional · appended to every generation prompt">
        <textarea
          value={persona.instructions || ''}
          onChange={(e) => set(['persona', 'instructions'], e.target.value)}
          rows={4}
          placeholder={`e.g. Always include a concrete example for ${nicheLabel(cfg.slug)}…`}
          style={textarea}
        />
      </Field>
      <Field title="Image source" hint="where post images come from">
        <select
          value={cfg.image_source === 'unsplash' || cfg.image_source === 'content' ? cfg.image_source : 'none'}
          onChange={(e) => set(['image_source'], e.target.value)}
          style={{ ...inputStyle, padding: '7px 10px', width: '100%' }}
        >
          <option value="none">None</option>
          <option value="unsplash">Unsplash</option>
          <option value="content">Content (source media)</option>
        </select>
      </Field>
      <Field
        title="Voice override"
        hint="optional · this niche's own sample posts, separated by a line with ---; empty uses the account's samples (Settings → General)"
      >
        <textarea
          value={examples.join('\n---\n')}
          onChange={(e) => {
            const parts = e.target.value.split(/\n-{3,}\n/);
            set(['persona', 'examples'], parts.every((x) => !x.trim()) ? [] : parts);
          }}
          rows={4}
          placeholder={'a post this niche should sound like\n---\nanother one'}
          style={textarea}
        />
      </Field>
    </div>
  );
}

// Column 3 — what gets through, and from where.
function FilterColumn({ cfg, set }) {
  const f = cfg.filters || {};
  const sources = cfg.sources || {};
  const names = Object.keys(sources);
  return (
    <div>
      <div style={colTitle}>Filtering &amp; sources</div>
      <Field title="Relevance keywords">
        <ChipList
          items={f.relevance_keywords || []}
          onChange={(v) => set(['filters', 'relevance_keywords'], v)}
          fg="#3ecf8e" bg="rgba(62,207,142,.08)" border="1px solid rgba(62,207,142,.2)"
          placeholder="keyword…"
        />
      </Field>
      <Field title="Blocklist">
        <ChipList
          items={f.blocklist || []}
          onChange={(v) => set(['filters', 'blocklist'], v)}
          fg="#f0a8b0" bg="rgba(245,69,92,.08)" border="1px solid rgba(245,69,92,.2)"
          placeholder="blocked keyword…"
        />
      </Field>
      <Field title="Sources" hint="origins (feeds / subreddits / channels) live on the Sources page">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {names.map((name) => {
            const enabled = (sources[name] || {}).enabled !== false;
            return (
              <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 11px', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: enabled ? '#3ecf8e' : '#3b414b' }} />
                <span style={{ flex: 1, fontSize: 12.5, fontWeight: 600, color: '#e7eaef' }}>{name}</span>
                <Toggle on={enabled} onClick={() => set(['sources', name, 'enabled'], !enabled)} size={16} />
              </div>
            );
          })}
          {!names.length && (
            <span className="os-mono" style={{ fontSize: 11, color: '#5b6470' }}>no sources configured for this niche</span>
          )}
        </div>
      </Field>
    </div>
  );
}

// immutable deep-set
function deepSet(obj, path, value) {
  if (!path.length) return value;
  const [head, ...rest] = path;
  const base = obj && typeof obj === 'object' ? obj : {};
  return { ...base, [head]: deepSet(base[head], rest, value) };
}

export default function NicheEditor() {
  const { settingsNiche } = useApp();
  const color = NICHE_COLOR[settingsNiche];
  const [cfg, setCfg] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setCfg(null);
    setDirty(false);
    setError(null);
    api.get(`/api/niches/${settingsNiche}`).then(setCfg).catch((e) => setError(e.message));
  }, [settingsNiche]);

  const set = (path, value) => {
    setCfg((c) => deepSet(c, path, value));
    setDirty(true);
  };

  const save = () => {
    if (!cfg || saving) return;
    setSaving(true);
    api
      .put(`/api/niches/${settingsNiche}`, cfg)
      .then((saved) => { setCfg(saved); setDirty(false); })
      .catch((e) => alert(e.message))
      .finally(() => setSaving(false));
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 13, marginBottom: 14 }}>
        <span style={{ width: 11, height: 11, borderRadius: 3, background: color }} />
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: '-.3px' }}>{nicheLabel(settingsNiche)}</div>
          <div className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>
            editing config/niches/{settingsNiche}.json{dirty ? ' · unsaved changes' : ''}
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button
            onClick={save}
            className="run-btn"
            style={{
              border: 'none', cursor: dirty ? 'pointer' : 'default', fontSize: 12, fontWeight: 700,
              padding: '8px 16px', borderRadius: 8,
              background: dirty ? '#3ecf8e' : '#1c222b', color: dirty ? '#0a0c0f' : '#7a828f',
            }}
          >
            {saving ? 'Saving…' : dirty ? 'Save config' : 'Saved'}
          </button>
        </div>
      </div>

      <Card style={{ padding: '6px 22px 22px' }}>
        {error && <div className="os-mono" style={{ fontSize: 11, color: '#f5455c', paddingTop: 16 }}>{error}</div>}
        {!cfg && !error && <div className="os-mono" style={{ fontSize: 11, color: '#5b6470', paddingTop: 16 }}>loading config…</div>}
        {cfg && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '18px 32px', alignItems: 'start', paddingTop: 10 }}>
            <VoiceColumn cfg={cfg} set={set} color={color} />
            <ContentColumn cfg={cfg} set={set} />
            <FilterColumn cfg={cfg} set={set} />
          </div>
        )}
      </Card>
    </div>
  );
}
