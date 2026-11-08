import React, { useEffect, useState } from 'react';
import { useApp } from '../AppContext';
import { nicheLabel, TYPE_COLORS } from '../data';
import { api } from '../api';
import { C, SaveButton, Toggle, fieldStyle, textareaStyle } from './common';

// The seven tones (core/posttypes.ALL_TONES); hints follow the shapes in
// ai/prompts.POST_TYPE_TEMPLATES.
const TONES = [
  ['news', 'News', 'what happened + your reaction'],
  ['spotlight', 'Spotlight', 'why this caught your eye'],
  ['insight', 'Insight', 'what it means, where it leads'],
  ['take', 'Take', 'pick a side and commit'],
  ['tip', 'Tip', 'one practical takeaway'],
  ['question', 'Question', 'the question it raises'],
  ['meme', 'Meme', 'a dry one-liner'],
];

// Per-niche post text controls (Voice column).
const POST_STYLES = [
  ['casual', 'Casual'], ['funny', 'Funny'], ['informative', 'Informative'],
  ['professional', 'Professional'], ['question', 'Question'], ['supportive', 'Supportive'],
];
const POST_LENGTHS = [
  ['very_short', 'Very Short', '2–5 words'], ['short', 'Short', 'A sentence'],
  ['medium', 'Medium', '1–2 lines'], ['long', 'Long', '3–4 lines'],
];

const inputStyle = fieldStyle;

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
function SelectGrid({ options, value, onChange, cols = 3 }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 9 }}>
      {options.map(([key, label, hint]) => {
        const on = value === key;
        return (
          <button
            key={key}
            onClick={() => onChange(on ? '' : key)}
            style={{
              cursor: 'pointer', textAlign: 'center', padding: '10px 10px', borderRadius: 9,
              fontFamily: 'inherit',
              background: on ? 'rgba(62,207,142,.1)' : C.inset,
              border: on ? '1px solid rgba(62,207,142,.45)' : '1px solid #1c212a',
              color: on ? C.text : C.muted, transition: 'all .12s',
            }}
          >
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</div>
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
function VoiceColumn({ cfg, set }) {
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
        <SelectGrid options={POST_STYLES} value={persona.style || ''} onChange={(v) => set(['persona', 'style'], v)} cols={2} />
      </Field>
      <Field title="Post length">
        <SelectGrid options={POST_LENGTHS} value={persona.length || ''} onChange={(v) => set(['persona', 'length'], v)} cols={2} />
      </Field>
    </div>
  );
}

// Column 2 — extra guidance, images, and which tones posts are written in.
function ContentColumn({ cfg, set }) {
  const persona = cfg.persona || {};
  const textarea = textareaStyle;
  // Mirrors PostTypesConfig.enabled_tones: no block (or nothing enabled) = all.
  const pt = cfg.post_types || {};
  const declared = TONES.map(([k]) => k).filter((k) => pt[k] && pt[k].enabled !== false);
  const on = declared.length ? declared : TONES.map(([k]) => k);
  // Always write the full block so the saved file states every tone explicitly.
  const toggleTone = (key) => {
    const next = on.includes(key) ? on.filter((k) => k !== key) : [...on, key];
    set(['post_types'], Object.fromEntries(TONES.map(([k]) => [k, { enabled: next.includes(k) }])));
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
      <Field title="Tones" hint={`each post uses one of these, never the same twice in a row · ${on.length} of ${TONES.length} on`}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 9 }}>
          {TONES.map(([key, name, hint]) => {
            const active = on.includes(key);
            const last = active && on.length === 1;
            return (
              <button
                key={key}
                onClick={() => !last && toggleTone(key)}
                title={last ? 'at least one tone stays on' : undefined}
                style={{
                  cursor: last ? 'not-allowed' : 'pointer', textAlign: 'left', padding: '9px 11px', borderRadius: 9,
                  fontFamily: 'inherit',
                  background: active ? 'rgba(62,207,142,.1)' : C.inset,
                  border: active ? '1px solid rgba(62,207,142,.45)' : '1px solid #1c212a',
                  color: active ? C.text : C.muted, transition: 'all .12s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 600 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 999, background: active ? TYPE_COLORS[key][0] : '#3a414c' }} />
                  {name}
                </div>
                <div className="os-mono" style={{ fontSize: 9.5, color: active ? '#9aa3af' : '#5b6470', marginTop: 3 }}>{hint}</div>
              </button>
            );
          })}
        </div>
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
              <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 11px', background: C.inset, border: '1px solid #1c212a', borderRadius: 8 }}>
                <span style={{ flex: 1, fontSize: 12.5, fontWeight: 600, color: enabled ? C.text : C.dim }}>{name}</span>
                <Toggle on={enabled} onClick={() => set(['sources', name, 'enabled'], !enabled)} size={18} />
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

  // The niche's name is already on the row above, so the editor opens straight
  // into its fields, with the file path and save action in a slim bar.
  return (
    <div style={{ background: C.inset, border: '1px solid #171c24', borderRadius: 11, padding: '4px 20px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 0', borderBottom: `1px solid ${C.line}` }}>
        <span className="os-mono" style={{ fontSize: 10, color: C.faint }}>config/niches/{settingsNiche}.json</span>
        {dirty && <span className="os-mono" style={{ fontSize: 10, color: C.amber }}>· unsaved changes</span>}
        <div style={{ marginLeft: 'auto' }}>
          <SaveButton dirty={dirty} saving={saving} onClick={save} label="Save niche" disabled={!cfg} />
        </div>
      </div>
      {error && <div className="os-mono" style={{ fontSize: 11, color: C.red, paddingTop: 16 }}>{error}</div>}
      {!cfg && !error && <div className="os-mono" style={{ fontSize: 11, color: C.faint, paddingTop: 16 }}>loading config…</div>}
      {cfg && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '18px 32px', alignItems: 'start', paddingTop: 14 }}>
          <VoiceColumn cfg={cfg} set={set} />
          <ContentColumn cfg={cfg} set={set} />
          <FilterColumn cfg={cfg} set={set} />
        </div>
      )}
    </div>
  );
}
