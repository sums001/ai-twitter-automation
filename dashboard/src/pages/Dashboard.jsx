import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowRight, Check, Share2, Star } from 'lucide-react';
import { useApp } from '../AppContext';
import { NICHE_COLOR, nicheLabel, LEVEL_COLORS } from '../data';
import { usePoll } from '../api';
import { fmtTime, relTime, hms } from '../utils';
import { Button, Card, CardHeader, NicheTag } from '../components/common';

// Chart colors — validated (dataviz validate_palette.js, dark, surface #111419):
// lightness band, chroma, CVD and normal-vision separation all pass.
const C_ITEMS = '#5a84e6';
const C_STORIES = '#24a872';
// Post-status segments, in this order (adjacent pairs validated).
const STATUS_SEGMENTS = [
  ['published', 'Published', '#24a872'],
  ['draft', 'Drafts', '#b8801c'],
  ['rejected', 'Rejected', '#3a414c'],
  ['failed', 'Failed', '#d9435a'],
];
const GRID = '#1c212a';
const INK_MUTED = '#5b6470';

const fmtNum = (n) => (n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}K` : n.toLocaleString());
const shortDate = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
function ago(iso) {
  if (!iso) return '';
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (m < 60) return `${m}m ago`;
  if (m < 1440) return `${Math.floor(m / 60)}h ago`;
  return `${Math.floor(m / 1440)}d ago`;
}
// clean axis max: 1, 2, 5 × 10^n at or above v
function niceMax(v) {
  if (v <= 4) return 4;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= v);
}
// column with a 4px rounded data-end, square at the baseline
function colPath(x, y, w, base) {
  const h = base - y;
  if (h <= 0) return '';
  const r = Math.min(4, h, w / 2);
  return `M${x},${base}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${base}Z`;
}

function useBoxSize() {
  const ref = useRef(null);
  const [size, setSize] = useState([0, 0]);
  useEffect(() => {
    if (!ref.current) return undefined;
    const r = ref.current.getBoundingClientRect();
    setSize([Math.floor(r.width), Math.floor(r.height)]); // first paint, before any resize
    const ro = new ResizeObserver(([e]) => setSize([Math.floor(e.contentRect.width), Math.floor(e.contentRect.height)]));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, size[0], size[1]];
}


// 14-point trend in a de-emphasis gray, today in the accent.
function Sparkline({ values, accent }) {
  if (!values || values.length < 2) return null;
  const W = 100, H = 26;
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * W, H - 2 - (v / max) * (H - 6)]);
  const last = pts[pts.length - 1];
  // The line stretches to the tile width (preserveAspectRatio="none"), which
  // would squash an SVG circle into an oval — so the end dot is an HTML element
  // positioned in percentages over the line instead.
  return (
    <div style={{ position: 'relative', height: H }} aria-hidden="true">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: H, display: 'block', overflow: 'visible' }}>
        <polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke="#3a424d" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </svg>
      <span style={{
        position: 'absolute', left: `${(last[0] / W) * 100}%`, top: last[1],
        width: 7, height: 7, borderRadius: '50%', background: accent,
        boxShadow: '0 0 0 2px #111419', transform: 'translate(-50%, -50%)',
      }} />
    </div>
  );
}

function KpiRow({ ov, dash }) {
  const t = dash && dash.totals;
  const series = (k) => (dash ? dash.daily.map((d) => d[k]) : null);
  const kpis = [
    { label: 'Stories', value: t ? fmtNum(t.stories) : '—', sub: t ? `+${t.stories_today} today` : '', accent: C_STORIES, spark: series('stories') },
    { label: 'Items fetched', value: t ? fmtNum(t.items) : '—', sub: t ? `+${t.items_today} today` : '', accent: C_ITEMS, spark: series('items') },
    { label: 'Drafts ready', value: ov ? String(ov.pending_total) : '—', sub: t ? `${t.drafts_today} written today` : '', accent: '#b8801c', spark: series('drafts') },
    { label: 'Published today', value: ov ? String(ov.published_today) : '—', unit: ov ? `/ ${ov.global_daily_cap} cap` : '', sub: t ? `${fmtNum(t.published)} all-time` : '', accent: '#3ecf8e', spark: series('published') },
    { label: 'Sources active', value: ov ? String(ov.sources_active) : '—', unit: ov ? `/ ${ov.sources_total}` : '', sub: ov ? `${ov.sources_total - ov.sources_active} off` : '', accent: '#7aa2f7' },
  ];
  return (
    <div className="os-grid-kpi" style={{ marginBottom: 14 }}>
      {kpis.map((k) => (
        <Card key={k.label} className="hover-card" style={{ padding: '13px 15px 11px', display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: k.accent, flexShrink: 0 }} />
            <div className="os-mono" style={{ fontSize: 9.5, color: '#6b7280', letterSpacing: '.5px', textTransform: 'uppercase' }}>{k.label}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 7 }}>
            <span style={{ fontSize: 25, fontWeight: 700, letterSpacing: '-.5px', color: '#e7eaef', lineHeight: 1 }}>{k.value}</span>
            {k.unit && <span className="os-mono" style={{ fontSize: 11, color: INK_MUTED }}>{k.unit}</span>}
          </div>
          <div className="os-mono" style={{ fontSize: 10, color: '#6b7280', marginTop: 5 }}>{k.sub}</div>
          <div style={{ marginTop: 'auto', paddingTop: 9, minHeight: 26 }}>
            {k.spark && <Sparkline values={k.spark} accent={k.accent} />}
          </div>
        </Card>
      ))}
    </div>
  );
}

function Legend({ items }) {
  return (
    <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
      {items.map(([label, color]) => (
        <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#9aa3af' }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: color }} />
          {label}
        </span>
      ))}
    </div>
  );
}

// Grouped columns: items fetched vs new stories per day (one axis, both counts).
function DailyChart({ dash }) {
  const [ref, width, boxH] = useBoxSize();
  const [hover, setHover] = useState(null);
  const daily = (dash && dash.daily) || [];
  const H = Math.max(190, boxH), ml = 34, mr = 6, mt = 8, mb = 22;
  const plotW = Math.max(0, width - ml - mr);
  const plotH = H - mt - mb;
  const max = niceMax(Math.max(0, ...daily.map((d) => Math.max(d.items, d.stories))));
  const y = (v) => mt + plotH - (v / max) * plotH;
  const band = daily.length ? plotW / daily.length : 0;
  const bw = Math.max(3, Math.min(12, band * 0.3));
  const labelEvery = Math.max(1, Math.ceil(daily.length / 7));
  const hov = hover != null ? daily[hover] : null;
  const total = daily.reduce((s, d) => s + d.items, 0);

  return (
    <Card style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column' }}>
      <CardHeader
        title="Ingestion"
        sub={`last ${daily.length || 14} days · ${fmtNum(total)} items`}
        right={<Legend items={[['Items fetched', C_ITEMS], ['New stories', C_STORIES]]} />}
      />
      <div ref={ref} style={{ position: 'relative', flex: 1, minHeight: 190 }}>
        {width > 0 && (
          <svg width={width} height={H} style={{ position: 'absolute', inset: 0 }} role="img" aria-label="Items fetched and new stories per day" onMouseLeave={() => setHover(null)}>
            {[0, max / 2, max].map((v) => (
              <g key={v}>
                <line x1={ml} x2={width - mr} y1={y(v)} y2={y(v)} stroke={GRID} strokeWidth="1" />
                <text x={ml - 7} y={y(v) + 3.5} textAnchor="end" fontSize="10" fill={INK_MUTED} fontFamily="'JetBrains Mono', monospace" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtNum(v)}</text>
              </g>
            ))}
            {daily.map((d, i) => {
              const cx = ml + band * i + band / 2;
              const base = mt + plotH;
              return (
                <g key={d.date}>
                  {hover === i && <rect x={ml + band * i} y={mt} width={band} height={plotH} fill="rgba(255,255,255,.035)" />}
                  <path d={colPath(cx - bw - 1, y(d.items), bw, base)} fill={C_ITEMS} />
                  <path d={colPath(cx + 1, y(d.stories), bw, base)} fill={C_STORIES} />
                  {(i % labelEvery === 0 || i === daily.length - 1) && (i === daily.length - 1 || daily.length - 1 - i >= labelEvery / 2) && (
                    <text x={cx} y={H - 6} textAnchor="middle" fontSize="10" fill={INK_MUTED} fontFamily="'JetBrains Mono', monospace">
                      {i === daily.length - 1 ? 'today' : shortDate(d.date)}
                    </text>
                  )}
                  <rect x={ml + band * i} y={mt} width={band} height={plotH + mb} fill="transparent" onMouseEnter={() => setHover(i)} />
                </g>
              );
            })}
          </svg>
        )}
        {hov && (
          <div
            className="os-mono"
            style={{
              position: 'absolute', top: 4, pointerEvents: 'none', zIndex: 2,
              left: Math.min(Math.max(0, ml + band * hover + band / 2 - 70), Math.max(0, width - 140)),
              width: 140, padding: '8px 10px', borderRadius: 8, background: '#1a1f27', border: '1px solid #2a313c',
              fontSize: 10.5, color: '#cfd6df', lineHeight: 1.6, boxShadow: '0 6px 20px rgba(0,0,0,.35)',
            }}
          >
            <div style={{ color: '#e7eaef', fontWeight: 700, marginBottom: 2 }}>{hover === daily.length - 1 ? 'Today' : shortDate(hov.date)}</div>
            {[['Items', hov.items, C_ITEMS], ['Stories', hov.stories, C_STORIES], ['Drafts', hov.drafts, '#b8801c'], ['Published', hov.published, '#3ecf8e']].map(([l, v, c]) => (
              <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 7, height: 7, borderRadius: 2, background: c }} />
                <span style={{ color: '#9aa3af' }}>{l}</span>
                <span style={{ marginLeft: 'auto', fontWeight: 700 }}>{v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

// The knowledge base's best unused stories for this workspace, right now.
function TopStories({ dash }) {
  const rows = (dash && dash.top_stories) || [];
  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader title="Top stories" sub="best unused · what gets posted next" style={{ marginBottom: 8 }} />
      {rows.map((s, i) => (
        <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderTop: i ? '1px solid #161b22' : 'none' }}>
          <span className="os-mono" style={{ width: 16, fontSize: 11, color: INK_MUTED, textAlign: 'right', flexShrink: 0 }}>{i + 1}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div title={s.title} style={{ fontSize: 12.5, color: '#dfe4ea', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.title}</div>
            <div className="os-mono" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 10, color: INK_MUTED, marginTop: 3 }}>
              <NicheTag niche={s.niche} />
              <span>{s.outlets} outlet{s.outlets === 1 ? '' : 's'}</span>
              <span>{ago(s.first_seen)}</span>
            </div>
          </div>
          <div style={{ width: 54, flexShrink: 0 }} title={`score ${s.score}`}>
            <div style={{ height: 6, borderRadius: 3, background: '#1c222b', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${Math.round(Math.min(1, s.score) * 100)}%`, background: C_STORIES, borderRadius: 3 }} />
            </div>
            <div className="os-mono" style={{ fontSize: 9.5, color: INK_MUTED, textAlign: 'right', marginTop: 3 }}>{s.score.toFixed(2)}</div>
          </div>
        </div>
      ))}
      {!rows.length && (
        <div className="os-mono" style={{ fontSize: 11, color: INK_MUTED, padding: '8px 0' }}>
          no unused stories — run a fetch to fill the knowledge base
        </div>
      )}
    </Card>
  );
}

// Every post this workspace has made, split by status (thin segmented bar).
function PostPipeline({ dash }) {
  const st = (dash && dash.post_status) || {};
  const parts = STATUS_SEGMENTS.map(([k, label, color]) => ({ k, label, color, v: st[k] || 0 }));
  const total = parts.reduce((s, p) => s + p.v, 0);
  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader title="Posts" sub={`${total} total · this workspace`} />
      <div role="img" aria-label={parts.map((p) => `${p.label} ${p.v}`).join(', ')} style={{ display: 'flex', gap: 2, height: 8, borderRadius: 4, overflow: 'hidden', background: total ? 'transparent' : '#1c222b' }}>
        {parts.filter((p) => p.v > 0).map((p) => (
          <div key={p.k} title={`${p.label}: ${p.v} (${Math.round((p.v / total) * 100)}%)`} style={{ flex: p.v, background: p.color }} />
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px', marginTop: 13 }}>
        {parts.map((p) => (
          <div key={p.k} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, color: '#9aa3af' }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color, flexShrink: 0 }} />
            {p.label}
            <span className="os-mono" style={{ marginLeft: 'auto', color: '#e7eaef', fontWeight: 600 }}>{p.v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// Items per source plugin over the last 7 days (single series → one hue).
function SourceMix({ dash }) {
  const rows = (dash && dash.by_source) || [];
  const max = Math.max(1, ...rows.map((r) => r.items));
  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader title="Items by source" sub="last 7 days" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {rows.map((r) => (
          <div key={r.name} style={{ display: 'grid', gridTemplateColumns: '82px 1fr 34px', alignItems: 'center', gap: 10 }} title={`${r.name}: ${r.items} items`}>
            <span style={{ fontSize: 11.5, color: '#cfd6df', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name}</span>
            <div style={{ height: 10 }}>
              <div style={{ height: '100%', width: `${Math.max(2, (r.items / max) * 100)}%`, background: C_ITEMS, borderRadius: '0 4px 4px 0' }} />
            </div>
            <span className="os-mono" style={{ fontSize: 11, color: '#cfd6df', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.items}</span>
          </div>
        ))}
        {!rows.length && <span className="os-mono" style={{ fontSize: 11, color: INK_MUTED }}>nothing fetched this week</span>}
      </div>
    </Card>
  );
}

// Followed niches over the last 7 days: stories found → drafts → published.
function NicheBreakdown({ dash }) {
  const { editNiche } = useApp();
  const rows = (dash && dash.by_niche) || [];
  const max = Math.max(1, ...rows.map((r) => r.stories));
  const grid = { display: 'grid', gridTemplateColumns: 'minmax(90px, 140px) 1fr 44px 44px 44px', gap: 12, alignItems: 'center' };
  const head = { fontSize: 9, color: INK_MUTED, letterSpacing: '.4px', textAlign: 'right' };
  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader title="Niches" sub="last 7 days · click to configure" />
      <div style={{ ...grid, paddingBottom: 8, borderBottom: `1px solid ${GRID}` }}>
        <span className="os-mono" style={{ ...head, textAlign: 'left' }}>NICHE</span>
        <span className="os-mono" style={{ ...head, textAlign: 'left' }}>STORIES</span>
        <span className="os-mono" style={head}>ITEMS</span>
        <span className="os-mono" style={head}>DRAFTS</span>
        <span className="os-mono" style={head}>PUB</span>
      </div>
      {rows.map((r) => (
        <div key={r.slug} onClick={() => editNiche(r.slug)} className="hover-row" style={{ ...grid, padding: '9px 0', borderBottom: '1px solid #161b22', cursor: 'pointer' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: NICHE_COLOR[r.slug] }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: '#cfd6df', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.display_name || nicheLabel(r.slug)}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ flex: 1, height: 8 }}>
              {r.stories > 0 && <div style={{ height: '100%', width: `${(r.stories / max) * 100}%`, background: NICHE_COLOR[r.slug] || C_STORIES, borderRadius: '0 4px 4px 0' }} />}
            </div>
            <span className="os-mono" style={{ fontSize: 11.5, color: '#e7eaef', fontWeight: 600, width: 26, textAlign: 'right' }}>{r.stories}</span>
          </div>
          {[r.items, r.drafts, r.published].map((v, i) => (
            <span key={i} className="os-mono" style={{ fontSize: 11.5, color: v ? '#cfd6df' : '#3f4650', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{v}</span>
          ))}
        </div>
      ))}
      {!rows.length && <div className="os-mono" style={{ fontSize: 11, color: INK_MUTED, padding: '12px 0' }}>select a niche to see its numbers</div>}
    </Card>
  );
}

// The account's next post, then the projected marks after it (server-rolled).
function NextPosts({ sched }) {
  const nowMin = sched ? sched.now_minute : 0;
  const times = sched ? sched.upcoming.slice(0, 6) : [];
  const mode = sched ? sched.mode : null;
  const every = sched ? sched.cadence.every_minutes : null;
  const idle = sched && sched.followed_niches.length === 0;
  const status = (sched && sched.status) || { state: 'ok', message: '' };
  const blocked = ['capped', 'held'].includes(status.state); // drafts still get written; publishing waits
  const [first, ...rest] = times;
  const rel = (u) => relTime(Math.round(u.day * 1440 + u.minute - nowMin));

  return (
    <Card style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column' }}>
      <CardHeader
        title="Next posts"
        sub={every ? `every ~${every}m · ${mode === 'auto' ? 'auto-publish' : 'drafts for review'}` : null}
      />
      {blocked && status.message && (
        <div style={{ fontSize: 11, lineHeight: 1.5, color: '#f5a524', marginBottom: 10 }}>{status.message}</div>
      )}
      {first && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 9, background: 'rgba(62,207,142,.07)', border: '1px solid rgba(62,207,142,.2)', marginBottom: 8 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#3ecf8e', flexShrink: 0 }} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: '#e7eaef' }}>{mode === 'auto' ? 'Next post' : 'Next draft'}</span>
          {first.day > 0 && <span className="os-mono" style={{ fontSize: 9.5, color: '#5b6470' }}>tomorrow</span>}
          <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 15, fontWeight: 700, color: '#e7eaef' }}>{fmtTime(first.minute)}</span>
          <span className="os-mono" style={{ fontSize: 10, color: '#5b6470', width: 62, textAlign: 'right' }}>{rel(first)}</span>
        </div>
      )}
      {rest.length > 0 && (
        <>
          <div className="os-mono" style={{ fontSize: 9.5, color: '#5b6470', letterSpacing: '.5px', textTransform: 'uppercase', margin: '4px 2px 4px' }}>then, approximately</div>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {rest.map((u, i) => (
              <div key={u.at} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 2px', borderTop: i ? '1px solid #161b22' : 'none' }}>
                <span className="os-mono" style={{ fontSize: 12, fontWeight: 600, color: '#cfd6df' }}>{fmtTime(u.minute)}</span>
                {u.day > 0 && <span className="os-mono" style={{ fontSize: 9.5, color: '#5b6470' }}>tomorrow</span>}
                <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>{rel(u)}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {!times.length && (
        <span className="os-mono" style={{ fontSize: 11, color: '#5b6470' }}>
          {idle ? 'select a niche to start posting' : status.message || 'waiting for the worker to schedule the next post'}
        </span>
      )}
    </Card>
  );
}

function ActivityFeed({ ov }) {
  const rows = ov ? ov.activity : [];
  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader title="Activity" sub="live" />
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {rows.map((e) => {
          const [fg, bg] = LEVEL_COLORS[e.level] || LEVEL_COLORS.info;
          return (
            <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0', borderBottom: '1px solid #161b22' }}>
              <span className="os-mono" style={{ fontSize: 10, color: '#5b6470', width: 62, flexShrink: 0 }}>{hms(e.ts)}</span>
              <span
                className="os-mono"
                style={{ fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 5, width: 42, textAlign: 'center', color: fg, background: bg }}
              >
                {e.level}
              </span>
              <span style={{ fontSize: 12.5, color: '#aeb6c0', flex: 1, minWidth: 0 }}>{e.msg}</span>
              {e.niche && <span style={{ fontSize: 11, textTransform: 'capitalize', color: NICHE_COLOR[e.niche] }}>{e.niche}</span>}
            </div>
          );
        })}
        {!rows.length && (
          <span className="os-mono" style={{ fontSize: 11, color: '#5b6470', padding: '10px 0' }}>
            no activity yet — run a fetch to get started
          </span>
        )}
      </div>
    </Card>
  );
}

// First-run onboarding — replaces the whole dashboard until the user has both
// enabled a source and selected a niche, then tells them exactly what to do.
function SetupGuide({ setup }) {
  const { setRoute } = useApp();
  const steps = [
    {
      done: setup.enabled_sources > 0,
      icon: Share2,
      title: 'Enable a content source',
      desc: 'Sources are what OpenX fetches posts from (RSS, Hacker News, Reddit…). Turn at least one on.',
      route: 'sources',
      cta: 'Go to Sources',
    },
    {
      done: setup.selected_niches > 0,
      icon: Star,
      title: 'Select a niche',
      desc: 'Niches define your topics and voice. Select the ones you post in — only selected niches are fetched and drafted for.',
      route: 'niches',
      cta: 'Go to Niches',
    },
  ];
  const nextStep = steps.find((s) => !s.done);

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      {/* top note */}
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '13px 16px', marginBottom: 18,
          background: 'rgba(245,165,36,.09)', border: '1px solid rgba(245,165,36,.28)', borderRadius: 11,
        }}
      >
        <span style={{ display: 'inline-flex', color: '#f5a524', flexShrink: 0 }}><AlertTriangle size={18} /></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: '#f5d8a4' }}>Finish setup to get started</div>
          <div className="os-mono" style={{ fontSize: 11, color: '#b89a6a', marginTop: 2 }}>
            OpenX needs at least one enabled source and one selected niche before it can do anything.
          </div>
        </div>
      </div>

      <Card style={{ padding: '8px 0' }}>
        {steps.map((s, i) => {
          const Icon = s.icon;
          const isNext = s === nextStep;
          return (
            <div
              key={s.route}
              style={{
                display: 'flex', alignItems: 'center', gap: 14, padding: '16px 20px',
                borderTop: i ? '1px solid #161b22' : 'none',
              }}
            >
              <span
                style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 38, height: 38,
                  borderRadius: 10, flexShrink: 0,
                  background: s.done ? 'rgba(62,207,142,.12)' : 'rgba(122,162,247,.1)',
                  color: s.done ? '#3ecf8e' : '#7aa2f7',
                }}
              >
                {s.done ? <Check size={18} strokeWidth={3} /> : <Icon size={17} />}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: s.done ? '#7a828f' : '#e7eaef' }}>
                    {s.title}
                  </span>
                  {s.done && (
                    <span className="os-mono" style={{ fontSize: 9, fontWeight: 700, padding: '1px 7px', borderRadius: 999, color: '#3ecf8e', background: 'rgba(62,207,142,.13)' }}>
                      done
                    </span>
                  )}
                </div>
                <div className="os-mono" style={{ fontSize: 10.5, color: '#5b6470', marginTop: 3, lineHeight: 1.5 }}>
                  {s.desc}
                </div>
              </div>
              <Button variant={isNext ? 'primary' : 'secondary'} onClick={() => setRoute(s.route)}>
                {s.cta} <ArrowRight size={13} />
              </Button>
            </div>
          );
        })}
      </Card>

      <div className="os-mono" style={{ fontSize: 10, color: '#4b5563', textAlign: 'center', marginTop: 16 }}>
        once both are done, your dashboard, queue, and schedule come to life here.
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { withAccount } = useApp();
  const { data: ov } = usePoll(withAccount('/api/overview'), 5000);
  const { data: dash } = usePoll(withAccount('/api/dashboard'), 15000);
  const { data: sched } = usePoll(withAccount('/api/schedule'), 60000);

  if (ov && ov.setup && ov.setup.needs_setup) {
    return <SetupGuide setup={ov.setup} />;
  }

  return (
    <div>
      <KpiRow ov={ov} dash={dash} />
      {/* row 1: what came in vs. what goes out next (equal height) */}
      <div className="os-grid-main" style={{ marginBottom: 14, alignItems: 'stretch' }}>
        <DailyChart dash={dash} />
        <NextPosts sched={sched} />
      </div>
      {/* row 2: what gets posted next, and the numbers behind it */}
      <div className="os-grid-main" style={{ marginBottom: 14 }}>
        <TopStories dash={dash} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <PostPipeline dash={dash} />
          <NicheBreakdown dash={dash} />
          <SourceMix dash={dash} />
        </div>
      </div>
      <ActivityFeed ov={ov} />
    </div>
  );
}
