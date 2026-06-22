import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, X } from 'lucide-react';
import { usePoll } from '../api';
import { ago, hms } from '../utils';
import { NICHE_COLOR, nicheLabel } from '../data';
import { Card, EmptyState, SectionLabel, NicheTag } from '../components/common';

const selectStyle = {
  colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8,
  color: '#e7eaef', fontFamily: 'inherit', fontSize: 12, padding: '7px 10px',
};

// Niche filter: a styled checkbox dropdown. Options are scoped by the caller
// (e.g. to the niches that actually use the selected source), so the list only
// ever offers niches that can return rows.
function NicheMultiSelect({ options, value, onChange, scoped }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const toggle = (slug) =>
    onChange(value.includes(slug) ? value.filter((s) => s !== slug) : [...value, slug]);

  const label = value.length === 0 ? (scoped ? 'All scoped niches' : 'All niches')
    : value.length === 1 ? nicheLabel(value[0])
    : `${value.length} niches`;

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="hover-border-text"
        style={{
          ...selectStyle, minWidth: 168, display: 'inline-flex', alignItems: 'center',
          justifyContent: 'space-between', gap: 9, cursor: 'pointer',
          borderColor: open ? '#2c3442' : '#1c212a',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          {value.length > 0 && (
            <span style={{ display: 'inline-flex', gap: 3, flexShrink: 0 }}>
              {value.slice(0, 3).map((s) => (
                <span key={s} style={{ width: 7, height: 7, borderRadius: 2, background: NICHE_COLOR[s] }} />
              ))}
            </span>
          )}
          <span style={{ color: value.length ? '#e7eaef' : '#7a828f', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {label}
          </span>
        </span>
        <ChevronDown size={13} style={{ flexShrink: 0, color: '#5b6470', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 40, minWidth: 210,
            maxHeight: 320, overflowY: 'auto', background: '#0d1116', border: '1px solid #232932',
            borderRadius: 10, padding: 6, boxShadow: '0 14px 36px rgba(0,0,0,.55)',
          }}
        >
          {value.length > 0 && (
            <button
              onClick={() => onChange([])}
              className="hover-bright"
              style={{
                display: 'flex', alignItems: 'center', gap: 8, width: '100%', border: 'none',
                background: 'transparent', color: '#7a828f', cursor: 'pointer', fontFamily: 'inherit',
                fontSize: 11, padding: '7px 9px', borderRadius: 7, marginBottom: 2,
              }}
            >
              <X size={12} /> clear selection
            </button>
          )}
          {options.length === 0 && (
            <div className="os-mono" style={{ fontSize: 10.5, color: '#5b6470', padding: '10px 9px', textAlign: 'center' }}>
              no niches use this source
            </div>
          )}
          {options.map((n) => {
            const on = value.includes(n.slug);
            return (
              <button
                key={n.slug}
                onClick={() => toggle(n.slug)}
                className="hover-bright"
                style={{
                  display: 'flex', alignItems: 'center', gap: 9, width: '100%', border: 'none',
                  background: on ? 'rgba(62,207,142,.08)' : 'transparent', cursor: 'pointer',
                  fontFamily: 'inherit', fontSize: 12, padding: '7px 9px', borderRadius: 7,
                }}
              >
                <span style={{ width: 8, height: 8, borderRadius: 3, flexShrink: 0, background: NICHE_COLOR[n.slug] }} />
                <span style={{ flex: 1, textAlign: 'left', color: on ? '#fff' : '#cfd6df', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {n.display_name || nicheLabel(n.slug)}
                </span>
                {on && <Check size={13} style={{ flexShrink: 0, color: '#3ecf8e' }} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// One field in the expanded raw view. `mono` for code-ish values.
function Field({ label, children, mono = true }) {
  if (children == null || children === '' || (Array.isArray(children) && !children.length)) return null;
  return (
    <div style={{ display: 'flex', gap: 12, padding: '5px 0', borderBottom: '1px solid #131820' }}>
      <span className="os-mono" style={{ width: 110, flexShrink: 0, fontSize: 10, color: '#5b6470', letterSpacing: '.4px', paddingTop: 2 }}>{label}</span>
      <span className={mono ? 'os-mono' : ''} style={{ flex: 1, minWidth: 0, fontSize: 11.5, color: '#cfd6df', lineHeight: 1.55, wordBreak: 'break-word' }}>
        {children}
      </span>
    </div>
  );
}

// A rendered thumbnail for the entry's featured image. Picks the first
// http(s) URL in media_urls and hides itself if that URL doesn't actually load
// as an image (e.g. NASA video-day links or YouTube page URLs), so it degrades
// to just the MEDIA URLS list below.
function EntryImage({ urls }) {
  const candidate = (urls || []).find((u) => typeof u === 'string' && /^https?:\/\//i.test(u));
  const [failed, setFailed] = useState(false);
  if (!candidate || failed) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <SectionLabel style={{ marginBottom: 6 }}>FEATURED IMAGE</SectionLabel>
      <a href={candidate} target="_blank" rel="noreferrer" style={{ display: 'inline-block', lineHeight: 0 }}>
        <img
          src={candidate}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          style={{
            maxWidth: 280, maxHeight: 200, width: 'auto', height: 'auto', objectFit: 'cover',
            borderRadius: 8, border: '1px solid #1c212a', display: 'block',
          }}
        />
      </a>
    </div>
  );
}

function JsonBlock({ value }) {
  if (value == null || (typeof value === 'object' && !Object.keys(value).length)) return null;
  return (
    <pre
      className="os-mono"
      style={{
        margin: 0, marginTop: 4, padding: '10px 12px', background: '#0b0e12', border: '1px solid #171c24',
        borderRadius: 8, fontSize: 10.5, color: '#9aa3af', lineHeight: 1.5, maxHeight: 320,
        overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// Compact niche pill shown on each row: color dot + label, tinted to the niche.
function NicheBadge({ niche, status }) {
  const color = NICHE_COLOR[niche];
  return (
    <span
      title={`${nicheLabel(niche)} · ${status}`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0, whiteSpace: 'nowrap',
        fontSize: 10.5, color: '#cfd6df', padding: '3px 8px', borderRadius: 6,
        background: 'rgba(255,255,255,.035)', border: '1px solid #1e242e',
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 2, background: color }} />
      {nicheLabel(niche)}
    </span>
  );
}

function ItemRow({ item, open, onToggle }) {
  return (
    <div style={{ borderBottom: '1px solid #131820' }}>
      <div
        className="lane-row"
        onClick={onToggle}
        style={{ display: 'grid', gridTemplateColumns: '20px 110px 1fr auto auto', alignItems: 'center', gap: 14, padding: '11px 14px', cursor: 'pointer' }}
      >
        <span className={`lane-caret${open ? ' open' : ''}`} style={{ display: 'inline-flex', color: open ? '#3ecf8e' : '#5b6470' }}>
          <ChevronRight size={14} />
        </span>
        <span className="os-mono" style={{ fontSize: 10.5, color: '#7aa2f7', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.source}
        </span>
        <span style={{ minWidth: 0, fontSize: 13, fontWeight: open ? 700 : 500, color: open ? '#fff' : '#e7eaef', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.title}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, justifyContent: 'flex-end' }}>
          {(item.niches || []).slice(0, 2).map((n) => (
            <NicheBadge key={n.niche} niche={n.niche} status={n.status} />
          ))}
          {(item.niches || []).length > 2 && (
            <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>+{item.niches.length - 2}</span>
          )}
        </div>
        <span className="os-mono" style={{ fontSize: 10, color: '#5b6470', width: 34, textAlign: 'right', flexShrink: 0 }}>{ago(item.fetched_at)} ago</span>
      </div>

      {open && (
        <div className="lane-open" style={{ padding: '4px 14px 18px 48px', background: 'rgba(255,255,255,.015)' }}>
          <Field label="title" mono={false}>{item.title}</Field>
          <Field label="url">
            <a href={item.url} target="_blank" rel="noreferrer" style={{ color: '#7aa2f7', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              {item.url}<ExternalLink size={11} />
            </a>
          </Field>
          <Field label="source">{item.source} · {item.category}</Field>
          <Field label="author">{item.author}</Field>
          <Field label="published">{item.published_at}</Field>
          <Field label="fetched">{item.fetched_at}</Field>
          <Field label="language">{item.language}</Field>
          <Field label="sentiment">{item.sentiment == null ? null : String(item.sentiment)}</Field>
          <Field label="tags">{(item.tags || []).join(', ')}</Field>
          <Field label="niches">
            <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 12 }}>
              {(item.niches || []).map((n) => (
                <span key={n.niche} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <NicheTag niche={n.niche} />
                  <span className="os-mono" style={{ fontSize: 9.5, color: '#5b6470' }}>{n.status}</span>
                </span>
              ))}
            </span>
          </Field>
          <Field label="summary" mono={false}>{item.summary}</Field>
          <Field label="body" mono={false}>
            {item.body ? (item.body.length > 1200 ? item.body.slice(0, 1200) + ' …' : item.body) : null}
          </Field>

          <EntryImage urls={item.media_urls} />
          {(item.media_urls || []).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <SectionLabel style={{ marginBottom: 6 }}>MEDIA URLS</SectionLabel>
              <JsonBlock value={item.media_urls} />
            </div>
          )}
          {item.engagement && (
            <div style={{ marginTop: 10 }}>
              <SectionLabel style={{ marginBottom: 6 }}>ENGAGEMENT</SectionLabel>
              <JsonBlock value={item.engagement} />
            </div>
          )}
          <div style={{ marginTop: 10 }}>
            <SectionLabel style={{ marginBottom: 6 }}>RAW METADATA</SectionLabel>
            <JsonBlock value={item.raw_metadata} />
          </div>
        </div>
      )}
    </div>
  );
}

const PAGE_SIZE = 50;

export default function RawData() {
  const [source, setSource] = useState('');
  const [nicheSel, setNicheSel] = useState([]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(null);
  const [page, setPage] = useState(0);

  const { data: sources } = usePoll('/api/sources', 30000);
  const { data: niches } = usePoll('/api/niches', 30000);

  const sourceOpts = useMemo(
    () => (sources || []).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [sources],
  );

  // Scope the niche options to the niches that actually use the chosen source.
  const selectedSource = useMemo(() => (sources || []).find((s) => s.id === source), [sources, source]);
  const nicheOpts = useMemo(() => {
    const all = niches || [];
    if (!source || !selectedSource) return all;
    const allowed = new Set(selectedSource.niches || []);
    return all.filter((n) => allowed.has(n.slug));
  }, [niches, source, selectedSource]);

  // Picking a source prunes any selected niche that's no longer in scope.
  const onSourceChange = (e) => {
    const v = e.target.value;
    setSource(v);
    setOpen(null);
    const src = (sources || []).find((s) => s.id === v);
    if (v && src) {
      const allowed = new Set(src.niches || []);
      setNicheSel((sel) => sel.filter((s) => allowed.has(s)));
    }
  };

  // Any filter change resets to the first page so the offset stays valid.
  useEffect(() => { setPage(0); setOpen(null); }, [source, nicheSel, query]);

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
  if (source) params.set('source', source);
  if (nicheSel.length) params.set('niche', nicheSel.join(','));
  if (query.trim()) params.set('q', query.trim());
  const { data, reload } = usePoll(`/api/content?${params.toString()}`, 8000);

  const rows = data?.items || [];
  const total = data?.total || 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <select value={source} onChange={onSourceChange} style={{ ...selectStyle, minWidth: 150 }}>
          <option value="">All sources</option>
          {sourceOpts.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <NicheMultiSelect options={nicheOpts} value={nicheSel} onChange={(v) => { setNicheSel(v); setOpen(null); }} scoped={!!source} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search title…"
          style={{ ...selectStyle, width: 200 }}
        />
        <button
          onClick={reload}
          className="hover-border-text"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace", fontSize: 11, padding: '7px 12px', borderRadius: 8 }}
        >
          <RefreshCw size={12} /> refresh
        </button>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 11, color: '#5b6470' }}>
          {total === 0 ? '0 items' : `${from}–${to} of ${total}`} · content_items
        </span>
      </div>

      {data && !rows.length && (
        <EmptyState title="No fetched content for this filter" sub="run Fetch sources, or widen the filters above" />
      )}

      {rows.length > 0 && (
        <Card style={{ overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '20px 110px 1fr auto auto', gap: 14, padding: '11px 14px', borderBottom: '1px solid #1c212a', background: '#0d1116' }}>
            {['', 'SOURCE', 'TITLE', 'NICHES', 'FETCHED'].map((h, i) => (
              <span key={i} className="os-mono" style={{ fontSize: 9, color: '#5b6470', letterSpacing: '.4px', textAlign: i >= 3 ? 'right' : 'left' }}>{h}</span>
            ))}
          </div>
          {rows.map((item) => (
            <ItemRow key={item.id} item={item} open={open === item.id} onToggle={() => setOpen(open === item.id ? null : item.id)} />
          ))}
        </Card>
      )}

      {pageCount > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14, marginTop: 16 }}>
          <PagerButton disabled={page === 0} onClick={() => { setPage((p) => Math.max(0, p - 1)); setOpen(null); }}>
            <ChevronLeft size={13} /> prev
          </PagerButton>
          <span className="os-mono" style={{ fontSize: 11, color: '#7a828f' }}>
            page {page + 1} / {pageCount}
          </span>
          <PagerButton disabled={page >= pageCount - 1} onClick={() => { setPage((p) => Math.min(pageCount - 1, p + 1)); setOpen(null); }}>
            next <ChevronRight size={13} />
          </PagerButton>
        </div>
      )}
    </div>
  );
}

function PagerButton({ disabled, onClick, children }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={disabled ? '' : 'hover-border-text'}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, border: '1px solid #232932',
        background: 'transparent', color: disabled ? '#3a414c' : '#9aa3af',
        cursor: disabled ? 'default' : 'pointer', fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11, padding: '7px 14px', borderRadius: 8, opacity: disabled ? 0.5 : 1,
      }}
    >
      {children}
    </button>
  );
}
