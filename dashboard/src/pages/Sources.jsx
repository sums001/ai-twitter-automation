import React, { useMemo, useState } from 'react';
import { ChevronRight, Cog, Info, KeyRound, RefreshCw, Star } from 'lucide-react';
import { useApp } from '../AppContext';
import { NICHE_COLOR, nicheLabel } from '../data';
import { api, usePoll } from '../api';
import { ago } from '../utils';
import { Card, Toggle, SectionLabel } from '../components/common';

const LABEL_W = 250;

const inputStyle = {
  width: '100%', colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8,
  color: '#e7eaef', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, padding: '8px 10px',
};
const block = { background: '#0d1116', border: '1px solid #171c24', borderRadius: 11, padding: '14px 16px' };

// Fallback cap if the service doesn't send max_origins.
const MAX_ORIGINS = 20;

// ===========================================================================
// "how it works" popover (anchored under the ⓘ button)
// ===========================================================================
function InfoPopover({ src }) {
  const [open, setOpen] = useState(false);
  if (!src.description) return null;
  const settings = src.settings || [];
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title="how it works"
        className="hover-border-text"
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20,
          borderRadius: '50%', border: `1px solid ${open ? '#2f3947' : '#232932'}`, background: open ? '#1a212b' : 'transparent',
          color: open ? '#3ecf8e' : '#7a828f', cursor: 'pointer', flexShrink: 0,
        }}
      >
        <Info size={13} />
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div className="lane-open" style={{ position: 'absolute', top: 'calc(100% + 7px)', right: 0, zIndex: 41, width: 326, background: '#141922', border: '1px solid #2a313c', borderRadius: 11, boxShadow: '0 14px 40px rgba(0,0,0,.55)', padding: '14px 15px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 11 }}>
              <span style={{ display: 'inline-flex', color: '#3ecf8e' }}><Cog size={14} /></span>
              <span style={{ fontSize: 12.5, fontWeight: 700, color: '#fff' }}>How {src.name} works</span>
            </div>
            <div style={{ fontSize: 11.5, color: '#cfd6df', lineHeight: 1.5 }}>{src.description}</div>
            {settings.length > 0 && (
              <div style={{ marginTop: 11 }}>
                <div className="os-mono" style={{ fontSize: 8.5, fontWeight: 700, color: '#7aa2f7', letterSpacing: '.4px', marginBottom: 5 }}>PER-NICHE SETTINGS</div>
                {settings.map((st) => (
                  <div key={st.name} style={{ display: 'flex', gap: 8, fontSize: 10.5, lineHeight: 1.5 }}>
                    <span className="os-mono" style={{ width: 92, flexShrink: 0, color: '#e7eaef' }}>{st.name}</span>
                    <span style={{ color: '#9aa3af' }}>{st.help}</span>
                  </div>
                ))}
              </div>
            )}
            {src.key_help && (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 10.5, color: '#9aa3af', lineHeight: 1.5, marginTop: 12, paddingTop: 10, borderTop: '1px solid #232b35' }}>
                <KeyRound size={12} style={{ flexShrink: 0, marginTop: 2 }} />{src.key_help}
              </div>
            )}
          </div>
        </>
      )}
    </span>
  );
}

// A friendly label for a credential env var (REDDIT_CLIENT_ID → "client id").
const envLabel = (env) =>
  env.replace(/_API_KEY$|_TOKEN$/i, '').replace(/^[A-Z]+_/, '').replace(/_/g, ' ').toLowerCase() ||
  env.toLowerCase();

function keyBadgeFor(src) {
  if (!src.key) return { text: 'no key needed', style: { background: '#161b22', color: '#5b6470', fontWeight: 400 } };
  if (src.key_set) return { text: 'key set ✓', style: { background: 'rgba(62,207,142,.12)', color: '#3ecf8e', fontWeight: 600 } };
  return src.key === 'optional'
    ? { text: 'key · optional', style: { background: '#161b22', color: '#9aa3af', fontWeight: 400 } }
    : { text: 'key required', style: { background: 'rgba(245,69,92,.12)', color: '#f5455c', fontWeight: 600 } };
}

// ===========================================================================
// niche chips (collapsed preview + editor list)
// ===========================================================================
function NicheChips({ niches, max = 10 }) {
  if (!niches.length) {
    return <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>no niches yet</span>;
  }
  const shown = niches.slice(0, max);
  const rest = niches.length - shown.length;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 13px' }}>
      {shown.map((n) => (
        <span key={n} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#9aa3af' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: NICHE_COLOR[n] }} />
          {nicheLabel(n)}
        </span>
      ))}
      {rest > 0 && <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>+{rest}</span>}
    </div>
  );
}

// ===========================================================================
// inline API-key editor — set credentials without leaving the Sources tab
// ===========================================================================
function KeyEditor({ src, keySet, onSaved }) {
  const envs = src.key_envs || [];
  const allSet = envs.length > 0 && envs.every((e) => keySet[e]);
  const [open, setOpen] = useState(!allSet); // start open when something's missing
  const [vals, setVals] = useState({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  if (!envs.length) return null;

  const filled = envs.filter((e) => (vals[e] || '').trim());
  const canSave = filled.length > 0 && !busy;

  const save = async () => {
    if (!canSave) return;
    setBusy(true);
    try {
      for (const e of filled) {
        await api.post('/api/credentials', { key: e, value: vals[e].trim() });
      }
      setVals({});
      setDone(true);
      onSaved();
      setTimeout(() => setDone(false), 1800);
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };

  const tone = src.key === 'optional' ? '#9aa3af' : '#f5a524';
  return (
    <div style={{ ...block, borderColor: allSet ? '#171c24' : 'rgba(245,165,36,.22)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ display: 'inline-flex', color: '#9aa3af' }}><KeyRound size={14} /></span>
        <SectionLabel style={{ fontSize: 10 }}>API CREDENTIALS</SectionLabel>
        <span className="os-mono" style={{ fontSize: 9.5, color: allSet ? '#3ecf8e' : tone, marginLeft: 2 }}>
          {allSet ? 'all set ✓' : src.key === 'optional' ? 'optional — improves results' : 'required to fetch'}
        </span>
        <button
          onClick={() => setOpen((o) => !o)}
          className="os-mono hover-bright"
          style={{ marginLeft: 'auto', border: 'none', background: 'transparent', color: '#7a828f', cursor: 'pointer', fontSize: 10 }}
        >
          {open ? 'hide' : allSet ? 'replace' : 'set keys'}
        </button>
      </div>

      {open && (
        <div className="lane-open" style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 9 }}>
          {envs.map((e) => (
            <div key={e} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span className="os-mono" style={{ width: 104, flexShrink: 0, fontSize: 10.5, color: '#9aa3af', textTransform: 'capitalize' }}>
                {envLabel(e)}
              </span>
              <input
                type="password"
                autoComplete="new-password"
                value={vals[e] || ''}
                onChange={(ev) => setVals((v) => ({ ...v, [e]: ev.target.value }))}
                onKeyDown={(ev) => { if (ev.key === 'Enter') save(); }}
                placeholder={keySet[e] ? '•••••••• already set — type to replace' : `enter ${envLabel(e)}`}
                style={{ ...inputStyle, flex: 1, width: 'auto', padding: '7px 10px', fontSize: 11.5 }}
              />
              {keySet[e] && <span className="os-mono" style={{ fontSize: 9, color: '#3ecf8e', flexShrink: 0 }}>set ✓</span>}
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
            <span className="os-mono" style={{ fontSize: 9, color: '#4b5563', flex: 1 }}>
              stored encrypted · also editable in Settings → Credentials
            </span>
            <button
              onClick={save}
              className="run-btn"
              style={{ border: 'none', cursor: canSave ? 'pointer' : 'default', fontSize: 11.5, fontWeight: 700, padding: '7px 16px', borderRadius: 8, background: canSave ? '#3ecf8e' : '#1c222b', color: canSave ? '#0a0c0f' : '#7a828f' }}
            >
              {busy ? 'Saving…' : done ? 'Saved ✓' : 'Save keys'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// one saved origin (feed / subreddit / channel) with inline edit + remove
// ===========================================================================
function OriginRow({ srcId, origin, niches, reload }) {
  const [editing, setEditing] = useState(false);
  const [niche, setNiche] = useState(origin.niche);
  const [value, setValue] = useState(origin.value);
  const [busy, setBusy] = useState(false);

  const rowBase = { display: 'flex', alignItems: 'center', gap: 9, padding: '7px 10px', background: '#0b0e12', border: '1px solid #171c24', borderRadius: 8 };

  const save = () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    api
      .put(`/api/sources/${srcId}/origins`, {
        niche: origin.niche,
        value: origin.value,
        url: value.trim(),
        new_niche: niche !== origin.niche ? niche : undefined,
      })
      .then(() => { setEditing(false); reload(); })
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };
  const cancel = () => { setEditing(false); setNiche(origin.niche); setValue(origin.value); };
  const remove = () => {
    api.del(`/api/sources/${srcId}/origins`, { niche: origin.niche, value: origin.value })
      .then(reload).catch((e) => alert(e.message));
  };

  if (editing) {
    return (
      <div style={rowBase}>
        <select value={niche} onChange={(e) => setNiche(e.target.value)} style={{ ...inputStyle, width: 124, flexShrink: 0, padding: '5px 7px', fontSize: 11 }}>
          {niches.map((n) => <option key={n.slug} value={n.slug}>{n.display_name || nicheLabel(n.slug)}</option>)}
        </select>
        <input
          autoFocus value={value} onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') cancel(); }}
          style={{ ...inputStyle, flex: 1, width: 'auto', padding: '5px 8px', fontSize: 11 }}
        />
        <button onClick={save} className="run-btn" style={{ border: 'none', background: '#3ecf8e', color: '#0a0c0f', cursor: 'pointer', fontSize: 10.5, fontWeight: 700, padding: '5px 11px', borderRadius: 6 }}>{busy ? 'saving…' : 'save'}</button>
        <button onClick={cancel} className="os-mono hover-bright" style={{ border: 'none', background: 'transparent', color: '#7a828f', cursor: 'pointer', fontSize: 10, padding: '5px 5px' }}>cancel</button>
      </div>
    );
  }
  return (
    <div className="hover-border" style={rowBase}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0, width: 116 }}>
        <span style={{ width: 6, height: 6, borderRadius: 2, flexShrink: 0, background: NICHE_COLOR[origin.niche] }} />
        <span className="os-mono" style={{ fontSize: 10, color: '#9aa3af', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{nicheLabel(origin.niche)}</span>
      </span>
      <span className="os-mono" style={{ flex: 1, minWidth: 0, fontSize: 11, color: '#cfd6df', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{origin.display}</span>
      <span onClick={() => setEditing(true)} title="edit origin" className="os-mono hover-bright" style={{ cursor: 'pointer', color: '#7a828f', fontSize: 10 }}>edit</span>
      <span onClick={remove} title="remove origin" className="hover-bright" style={{ cursor: 'pointer', color: '#f5455c', fontWeight: 700, padding: '0 4px' }}>×</span>
    </div>
  );
}

// ===========================================================================
// add-origin form (dynamic sources)
// ===========================================================================
function AddOrigin({ src, niches, reload, count, max }) {
  const [niche, setNiche] = useState('');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const target = niche || (niches[0] && niches[0].slug) || '';
  const atCap = count >= max;
  const ready = !atCap && target && url.trim();

  const add = () => {
    if (!ready || busy) return;
    setBusy(true);
    api
      .post(`/api/sources/${src.id}/origins`, { niche: target, url: url.trim() })
      .then(() => { setUrl(''); reload(); })
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div>
      <SectionLabel style={{ fontSize: 10, marginBottom: 8 }}>ADD ORIGIN</SectionLabel>
      {atCap ? (
        <div className="os-mono" style={{ fontSize: 10.5, color: '#f5a524', background: 'rgba(245,165,36,.08)', border: '1px solid rgba(245,165,36,.2)', borderRadius: 8, padding: '8px 11px' }}>
          Reached the {max}-origin cap — remove one to add another.
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 8 }}>
          <select value={target} onChange={(e) => setNiche(e.target.value)} style={{ ...inputStyle, width: 138, flexShrink: 0, padding: '8px 9px' }}>
            {niches.map((n) => <option key={n.slug} value={n.slug}>{n.display_name || nicheLabel(n.slug)}</option>)}
          </select>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') add(); }}
            placeholder={src.origin_label || 'origin URL'}
            style={{ ...inputStyle, flex: 1, width: 'auto' }}
          />
          <button
            onClick={add}
            className="run-btn"
            style={{ border: 'none', background: ready ? '#3ecf8e' : '#1c222b', color: ready ? '#0a0c0f' : '#7a828f', cursor: ready ? 'pointer' : 'default', fontSize: 12, fontWeight: 700, padding: '8px 16px', borderRadius: 8, whiteSpace: 'nowrap' }}
          >
            {busy ? 'adding…' : '+ Add'}
          </button>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// collapsed source row
// ===========================================================================
function SourceRow({ src, open, onToggle, onFetch, onToggleEnabled, fetching }) {
  const badge = keyBadgeFor(src);
  const upcoming = src.upcoming;
  const dot = !src.enabled ? '#3b414b' : src.ok ? '#3ecf8e' : '#f5a524';
  const isDynamic = src.kind === 'dynamic';
  const originCount = (src.origins || []).length;
  const nicheCount = isDynamic ? [...new Set((src.origins || []).map((o) => o.niche))].length : [...new Set(src.niches)].length;
  const lastInfo = !src.enabled ? 'disabled' : src.last_fetch_at ? `fetched ${ago(src.last_fetch_at)} ago` : 'never fetched';

  return (
    <div
      className="lane-row"
      onClick={onToggle}
      style={{ display: 'grid', gridTemplateColumns: `${LABEL_W}px 1fr auto`, alignItems: 'center', gap: 14, padding: '10px 12px', borderRadius: 9, cursor: 'pointer' }}
    >
      {/* identity */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
        <span className={`lane-caret${open ? ' open' : ''}`} style={{ display: 'inline-flex', color: open ? '#3ecf8e' : '#5b6470', flexShrink: 0 }}><ChevronRight size={14} /></span>
        <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: dot }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ fontSize: 13, fontWeight: open ? 700 : 600, color: open ? '#fff' : '#e7eaef', whiteSpace: 'nowrap' }}>{src.name}</span>
            {upcoming && (
              <span className="os-mono" style={{ fontSize: 8.5, padding: '2px 6px', borderRadius: 5, background: 'rgba(99,102,241,.14)', color: '#a5b4fc', fontWeight: 600, letterSpacing: '.04em' }}>UPCOMING</span>
            )}
            <span className="os-mono" style={{ fontSize: 8.5, padding: '2px 6px', borderRadius: 5, ...badge.style }}>{badge.text}</span>
          </div>
          <div style={{ fontSize: 10, color: '#5b6470', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={src.description}>
            {src.description}
          </div>
        </div>
      </div>

      {/* middle: usage summary */}
      <div style={{ minWidth: 0, overflow: 'hidden' }}>
        {isDynamic ? (
          originCount ? (
            <span className="os-mono" style={{ fontSize: 10.5, color: '#9aa3af' }}>
              {originCount} origin{originCount === 1 ? '' : 's'} · {nicheCount} niche{nicheCount === 1 ? '' : 's'}
            </span>
          ) : (
            <span className="os-mono" style={{ fontSize: 10.5, color: '#5b6470' }}>no origins — expand to add one</span>
          )
        ) : (
          <NicheChips niches={[...new Set(src.niches)]} max={6} />
        )}
      </div>

      {/* right: status + actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'flex-end' }} onClick={(e) => e.stopPropagation()}>
        <span className="os-mono" style={{ fontSize: 9.5, color: !src.enabled ? '#5b6470' : src.ok ? '#5b6470' : '#f5a524', whiteSpace: 'nowrap' }}>{lastInfo}</span>
        <InfoPopover src={src} />
        <button
          onClick={() => src.enabled && !fetching && onFetch()}
          disabled={!src.enabled}
          className={src.enabled ? 'hover-border-text' : undefined}
          title={upcoming ? 'coming soon — not available yet' : src.enabled ? 'fetch this source now' : 'source is disabled — enable it first ↑'}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: '1px solid #232932', background: 'transparent', color: !src.enabled ? '#3b414b' : fetching ? '#5b6470' : '#9aa3af', cursor: src.enabled ? 'pointer' : 'not-allowed', fontFamily: "'JetBrains Mono', monospace", fontSize: 10, padding: '6px 11px', borderRadius: 7, whiteSpace: 'nowrap' }}
        >
          <RefreshCw size={11} style={fetching ? { animation: 'os-spin 1s linear infinite' } : undefined} />
          {fetching ? 'fetching…' : 'fetch'}
        </button>
        <Toggle on={src.enabled} onClick={onToggleEnabled} size={20} disabled={upcoming} title={upcoming ? 'coming soon — can’t be enabled yet' : 'enable / disable globally'} />
      </div>
    </div>
  );
}

// ===========================================================================
// expanded inline editor
// ===========================================================================
function SourceEditor({ src, niches, keySet, reload, reloadCreds }) {
  const isDynamic = src.kind === 'dynamic';
  const origins = src.origins || [];
  const max = src.max_origins || MAX_ORIGINS;
  const uniqueNiches = isDynamic
    ? [...new Set(origins.map((o) => o.niche))]
    : [...new Set(src.niches)];

  return (
    <div className="lane-open" style={{ padding: '4px 12px 16px', borderBottom: '1px solid #161b22', marginBottom: 2 }}>
      {src.last_status && !src.ok && (
        <div className="os-mono" style={{ fontSize: 10, color: '#f5a524', background: 'rgba(245,165,36,.08)', border: '1px solid rgba(245,165,36,.2)', borderRadius: 8, padding: '7px 11px', marginBottom: 12 }}>
          last fetch: {src.last_status}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-start' }}>
        {/* origins / niches */}
        <div style={{ ...block, flex: '1.6 1 360px' }}>
          {isDynamic ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <SectionLabel style={{ fontSize: 10 }}>ORIGINS</SectionLabel>
                <span className="os-mono" style={{ fontSize: 9, color: origins.length >= max ? '#f5a524' : '#5b6470' }}>· {origins.length} / {max}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
                {origins.length ? origins.map((o, i) => (
                  <OriginRow key={`${o.niche}:${o.value}:${i}`} srcId={src.id} origin={o} niches={niches} reload={reload} />
                )) : (
                  <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>no origins yet — add one below</span>
                )}
              </div>
              <AddOrigin src={src} niches={niches} reload={reload} count={origins.length} max={max} />
            </>
          ) : (
            <>
              <SectionLabel style={{ fontSize: 10, marginBottom: 10 }}>USED BY NICHES</SectionLabel>
              <NicheChips niches={uniqueNiches} max={50} />
              <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginTop: 12, lineHeight: 1.6 }}>
                Runs for every niche that lists it, using that niche's own settings
                (see ⓘ for what each setting does).
              </div>
            </>
          )}
        </div>

        {/* credentials */}
        {src.key && (
          <div style={{ flex: '1 1 300px' }}>
            <KeyEditor src={src} keySet={keySet} onSaved={() => { reloadCreds(); reload(); }} />
          </div>
        )}
      </div>
    </div>
  );
}

// ===========================================================================
// page
// ===========================================================================
export default function Sources() {
  const { runCommand, commands, withWorkspace } = useApp();
  const { data: sources, reload } = usePoll('/api/sources', 10000);
  // Scope to the current workspace so each niche's ``followed`` flag reflects
  // *this* workspace's selection — that drives the "Selected niches only" filter.
  const { data: niches } = usePoll(withWorkspace('/api/niches'), 30000);
  const { data: creds, reload: reloadCreds } = usePoll('/api/credentials', 30000);

  const [open, setOpen] = useState(null);
  const [filter, setFilter] = useState('all'); // all | free | keyed
  const [query, setQuery] = useState('');
  const [selectedOnly, setSelectedOnly] = useState(true); // hide sources that don't support a selected niche

  const list = sources || [];
  const nicheList = niches || [];

  // The niches the user has selected (followed) on the Niches page.
  const selectedSet = useMemo(
    () => new Set(nicheList.filter((n) => n.followed).map((n) => n.slug)),
    [nicheList]
  );
  // A (static) source "supports" a selected niche if that niche references it.
  const supportsSelected = (s) =>
    s.kind === 'dynamic' || (s.niches || []).some((slug) => selectedSet.has(slug));

  // env -> is the credential set (from the credentials endpoint)
  const keySet = useMemo(() => {
    const m = {};
    (creds || []).forEach((g) => (g.keys || []).forEach((k) => { m[k.name] = k.set; }));
    return m;
  }, [creds]);

  const normalized = list;
  // "free" = fetches without a key (an optional key only lifts rate limits).
  const needsKeyGroup = (s) => s.auth === 'required';
  const inFilter = (s) => filter === 'all' || (filter === 'keyed' ? needsKeyGroup(s) : !needsKeyGroup(s));

  const fetchingId = (id) =>
    commands.find((c) => c.type === 'fetch_sources' && c.payload.source === id && (c.status === 'pending' || c.status === 'running'));

  const toggleEnabled = (src) =>
    api.patch(`/api/sources/${src.id}`, { enabled: !src.enabled }).then(reload).catch((e) => alert(e.message));

  // Don't filter by selection until the user has actually selected a niche —
  // otherwise a fresh DB (nothing selected) would hide every static source.
  const applySelected = selectedOnly && selectedSet.size > 0;
  const visible = normalized
    .filter(inFilter)
    .filter((s) => !applySelected || supportsSelected(s))
    .filter((s) => !query || s.name.toLowerCase().includes(query.toLowerCase()) || s.id.toLowerCase().includes(query.toLowerCase()));
  const hiddenBySelected = applySelected
    ? normalized.filter((s) => !supportsSelected(s)).length
    : 0;

  const total = normalized.length;
  const active = normalized.filter((s) => s.enabled).length;
  const originsTotal = normalized.reduce((a, s) => a + (s.origins || []).length, 0);
  const missingKeys = normalized.filter((s) => s.needs_key && !s.upcoming).length;

  const seg = (id, label, count) => (
    <button
      key={id}
      onClick={() => setFilter(id)}
      className={filter === id ? '' : 'hover-bright'}
      style={{
        border: 'none', cursor: 'pointer', fontSize: 11.5, fontWeight: 600, padding: '6px 13px', borderRadius: 7,
        background: filter === id ? '#1c222b' : 'transparent', color: filter === id ? '#e7eaef' : '#7a828f',
        fontFamily: 'inherit',
      }}
    >
      {label} <span className="os-mono" style={{ fontSize: 9.5, color: filter === id ? '#5b6470' : '#444b55' }}>{count}</span>
    </button>
  );

  return (
    <Card style={{ padding: '4px 20px 8px' }}>
      {/* ===== summary header ===== */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 22, flexWrap: 'wrap', padding: '16px 0', borderBottom: '1px solid #1a1f27' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Content sources</div>
          <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>where post material comes from · click a row to configure</div>
        </div>
        <div style={{ display: 'flex', gap: 26, marginLeft: 4 }}>
          <Stat value={active} label={`active / ${total}`} color="#e7eaef" />
          <Stat value={originsTotal} label="origins" color="#e7eaef" />
          <Stat value={missingKeys} label="keys missing" color={missingKeys > 0 ? '#f5a524' : '#3ecf8e'} />
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14 }}>
          {[['healthy', '#3ecf8e'], ['error', '#f5a524'], ['off', '#3b414b']].map(([label, color]) => (
            <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#9aa3af' }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: color }} />
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* ===== controls + list ===== */}
      <div style={{ paddingTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 2, background: '#0d1116', border: '1px solid #1a1f27', borderRadius: 9, padding: 3 }}>
            {seg('all', 'All', total)}
            {seg('free', 'No key needed', normalized.filter((s) => !needsKeyGroup(s)).length)}
            {seg('keyed', 'Needs a key', normalized.filter(needsKeyGroup).length)}
          </div>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter sources…"
            style={{ width: 170, colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8, color: '#e7eaef', fontFamily: 'inherit', fontSize: 11.5, padding: '7px 11px' }}
          />
          <button
            onClick={() => setSelectedOnly((v) => !v)}
            className={selectedOnly ? '' : 'hover-bright'}
            title="show only sources that support a selected niche (dynamic sources always shown)"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 7, cursor: 'pointer', fontSize: 11.5, fontWeight: 600,
              fontFamily: 'inherit', padding: '6px 12px', borderRadius: 8,
              border: `1px solid ${selectedOnly ? 'rgba(62,207,142,.35)' : '#1c212a'}`,
              background: selectedOnly ? 'rgba(62,207,142,.1)' : '#0d1116',
              color: selectedOnly ? '#3ecf8e' : '#7a828f',
            }}
          >
            <Star size={12} style={{ flexShrink: 0 }} />
            Selected niches only
          </button>
          <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>
            {selectedOnly && selectedSet.size === 0
              ? 'no niches selected — showing all sources'
              : hiddenBySelected > 0
                ? `${hiddenBySelected} source${hiddenBySelected > 1 ? 's' : ''} hidden — not in a selected niche`
                : missingKeys > 0 ? `${missingKeys} source${missingKeys > 1 ? 's' : ''} need a key — set it inline ↓` : 'click any row to edit it inline ↓'}
          </span>
        </div>

        {!sources && <div className="os-mono" style={{ fontSize: 11, color: '#5b6470', padding: '24px 0', textAlign: 'center' }}>loading sources…</div>}

        {sources && visible.length === 0 && (
          <div className="os-mono" style={{ fontSize: 11, color: '#5b6470', padding: '28px 0', textAlign: 'center' }}>no sources match this filter</div>
        )}

        {visible.map((s) => {
          const isOpen = open === s.id;
          return (
            <div key={s.id} style={{ borderRadius: 10, background: isOpen ? 'rgba(255,255,255,.022)' : 'transparent', margin: '0 -8px' }}>
              <SourceRow
                src={s}
                open={isOpen}
                onToggle={() => setOpen(isOpen ? null : s.id)}
                onFetch={() => runCommand('fetch_sources', { source: s.id })}
                onToggleEnabled={() => toggleEnabled(s)}
                fetching={!!fetchingId(s.id)}
              />
              {isOpen && (
                <SourceEditor src={s} niches={nicheList} keySet={keySet} reload={reload} reloadCreds={reloadCreds} />
              )}
            </div>
          );
        })}
      </div>
    </Card>
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
