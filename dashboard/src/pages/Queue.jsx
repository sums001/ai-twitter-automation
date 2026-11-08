import React, { useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, Copy, Check, ExternalLink } from 'lucide-react';
import { useApp } from '../AppContext';
import { NICHES, NICHE_COLOR, nicheLabel } from '../data';
import { api, usePoll } from '../api';
import { ago, daysAgo, dayLabel, hm } from '../utils';
import { Button, Card, EmptyState, MediaThumb, NicheTag, Segmented, StatusBadge, Tag, TypeBadge, fieldStyle } from '../components/common';
import PostPreview from '../components/PostPreview';

// searchable single-select category dropdown (scales past 18 categories)
export function CategoryDropdown() {
  const { niche, setNiche } = useApp();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');

  const options = [{ slug: 'all', label: 'All niches' }]
    .concat(NICHES.map((s) => ({ slug: s, label: nicheLabel(s) })))
    .filter((o) => !search || o.label.toLowerCase().includes(search.toLowerCase()));

  const pick = (slug) => {
    setNiche(slug);
    setOpen(false);
    setSearch('');
  };

  return (
    <div style={{ position: 'relative', zIndex: 20 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="hover-border"
        style={{
          display: 'flex', alignItems: 'center', gap: 9, background: '#0d1116', border: '1px solid #1c212a',
          borderRadius: 8, padding: '7px 12px', cursor: 'pointer', minWidth: 180, fontFamily: 'inherit',
        }}
      >
        {niche !== 'all' && <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: NICHE_COLOR[niche] }} />}
        <span style={{ flex: 1, textAlign: 'left', fontSize: 12.5, fontWeight: 600, color: '#e7eaef' }}>
          {niche === 'all' ? 'All niches' : nicheLabel(niche)}
        </span>
        <span style={{ display: 'inline-flex', color: '#7a828f' }}><ChevronDown size={14} /></span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div
            style={{
              position: 'absolute', top: 'calc(100% + 5px)', left: 0, zIndex: 41, width: 250,
              background: '#141922', border: '1px solid #2a313c', borderRadius: 10,
              boxShadow: '0 12px 36px rgba(0,0,0,.5)', padding: 6,
            }}
          >
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search niches…"
              style={{
                width: '100%', colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a',
                borderRadius: 7, color: '#e7eaef', fontFamily: 'inherit', fontSize: 12, padding: '7px 10px', marginBottom: 5,
              }}
            />
            <div style={{ maxHeight: 264, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 }}>
              {options.map((o) => {
                const active = niche === o.slug;
                const col = o.slug === 'all' ? null : NICHE_COLOR[o.slug];
                return (
                  <button
                    key={o.slug}
                    onClick={() => pick(o.slug)}
                    className="hover-opt"
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left',
                      border: 'none', cursor: 'pointer', padding: '8px 11px', borderRadius: 7, fontSize: 12.5,
                      background: active ? 'rgba(62,207,142,.08)' : 'transparent', color: active ? '#e7eaef' : '#9aa3af',
                    }}
                  >
                    <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: col || 'transparent', border: col ? 'none' : '1px solid #3b414b' }} />
                    <span style={{ flex: 1 }}>{o.label}</span>
                    <span style={{ color: '#3ecf8e', fontWeight: 800, fontSize: 11 }}>{active ? '✓' : ''}</span>
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

function QueueCard({ post, reload, maxAttempts }) {
  const { commands, runCommand } = useApp();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(post.text);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyText = () => {
    const write = navigator.clipboard?.writeText
      ? navigator.clipboard.writeText(post.text)
      : Promise.reject();
    write
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        // Fallback for non-secure contexts where the Clipboard API is unavailable.
        const ta = document.createElement('textarea');
        ta.value = post.text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
        document.body.removeChild(ta);
      });
  };

  const inflight = (type) =>
    commands.find(
      (c) => c.type === type && c.payload.generated_post_id === post.id && (c.status === 'pending' || c.status === 'running')
    );
  const posting = inflight('post_now');
  const regenerating = inflight('regenerate_post');

  const act = (action, body) => {
    setBusy(true);
    api
      .post(`/api/posts/${post.id}/${action}`, body)
      .then(() => reload())
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };

  const saveEdit = () => {
    if (!draft.trim()) return;
    act('edit', { text: draft.trim() });
    setEditing(false);
  };

  const actionable = !['published', 'rejected'].includes(post.status);

  return (
    <Card
      className="hover-card"
      onClick={() => !editing && setPreview(true)}
      title="Open X preview"
      style={{ display: 'flex', gap: 14, padding: '14px 15px', cursor: editing ? 'default' : 'pointer' }}
    >
      {preview && <PostPreview post={post} onClose={() => setPreview(false)} />}
      {post.media_url && <MediaThumb url={post.media_url} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <TypeBadge type={post.type} />
          <StatusBadge status={post.status} />
          {!post.independent
            ? <Tag fg="#7aa2f7" bg="rgba(122,162,247,.1)">{post.source}</Tag>
            : <Tag fg="#b48ef7" bg="rgba(180,142,247,.1)">synthesis</Tag>}
          <NicheTag niche={post.niche} />
          <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>{post.id.slice(0, 10)}</span>
        </div>

        {!editing ? (
          <div style={{ fontSize: 13.5, lineHeight: 1.5, color: '#d5dae1', textWrap: 'pretty' }}>{post.text}</div>
        ) : (
          <div onClick={(e) => e.stopPropagation()}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={4}
              style={{ ...fieldStyle, width: '100%', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5, resize: 'vertical' }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 7 }}>
              <Button size="sm" variant="primary" onClick={saveEdit}>Save</Button>
              <Button size="sm" onClick={() => { setEditing(false); setDraft(post.text); }}>Cancel</Button>
              <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: draft.length > 280 ? '#f5455c' : '#5b6470' }}>{draft.length}/280</span>
            </div>
          </div>
        )}

        {post.error && (
          <div
            className="os-mono"
            style={{
              marginTop: 8, display: 'flex', alignItems: 'center', gap: 8, fontSize: 10, color: '#f5455c',
              background: 'rgba(245,69,92,.08)', border: '1px solid rgba(245,69,92,.2)', borderRadius: 7, padding: '6px 9px',
            }}
          >
            <span>attempt {post.attempts}/{maxAttempts} · {post.error}</span>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 11, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 128 }}>
            <span className="os-mono" style={{ fontSize: 9, color: '#5b6470' }}>PRIORITY</span>
            <div style={{ width: 48, height: 4, background: '#1a1f27', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ width: `${Math.round((post.priority || 0) * 100)}%`, height: '100%', background: '#3ecf8e' }} />
            </div>
            <span className="os-mono" style={{ fontSize: 11, fontWeight: 600, color: '#3ecf8e' }}>
              {post.priority != null ? post.priority.toFixed(2) : '—'}
            </span>
          </div>
          <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>created {ago(post.created_at)} ago</span>
          {post.scheduled_at && (
            <span className="os-mono" style={{ fontSize: 10, color: '#7aa2f7' }}>→ {hm(post.scheduled_at)}</span>
          )}
          {!editing && (
            <div onClick={(e) => e.stopPropagation()} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Button size="sm" variant="ghost" onClick={copyText} title="Copy post text" icon={copied ? <Check size={11} /> : <Copy size={11} />} style={copied ? { color: '#3ecf8e' } : null}>
                {copied ? 'Copied' : 'Copy'}
              </Button>
              {post.source_url && (
                <a
                  href={post.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover-bright"
                  title={post.source_url}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 5, textDecoration: 'none', fontSize: 11, fontWeight: 600, color: '#7a828f', padding: '5px 10px', borderRadius: 7 }}
                >
                  <ExternalLink size={11} />
                  Source
                </a>
              )}
            </div>
          )}
          {actionable && !editing && (
            <div onClick={(e) => e.stopPropagation()} style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <Button size="sm" variant="soft" onClick={() => !posting && runCommand('post_now', { generated_post_id: post.id })}>
                {posting ? 'Posting…' : 'Post now'}
              </Button>
              <Button size="sm" onClick={() => setEditing(true)}>Edit</Button>
              <Button size="sm" onClick={() => !regenerating && runCommand('regenerate_post', { generated_post_id: post.id })}>
                {regenerating ? 'Regenerating…' : 'Regenerate'}
              </Button>
              <Button size="sm" variant="danger" onClick={() => !busy && act('reject')}>Reject</Button>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

const STATUS_FILTERS = [['all', 'All'], ['draft', 'Drafts'], ['published', 'Published'], ['rejected', 'Rejected']];

export default function Queue() {
  const { niche, status, withAccount } = useApp();
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState('all');
  const { data: posts, reload } = usePoll(
    withAccount(niche === 'all' ? '/api/posts' : `/api/posts?niche=${encodeURIComponent(niche)}`),
    5000
  );
  const everything = posts || [];
  const all = everything.filter((p) => statusFilter === 'all' || p.status === statusFilter);
  const count = (k) => (k === 'all' ? everything.length : everything.filter((p) => p.status === k).length);

  const maxDay = all.reduce((m, p) => Math.max(m, daysAgo(p.created_at)), 0);
  const cur = Math.min(page, maxDay);
  const rows = all.filter((p) => daysAgo(p.created_at) === cur); // API is newest-first

  return (
    <div>
      {/* one filter row: niche, status, then day paging (newest day first) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
        <CategoryDropdown />
        <Segmented
          options={STATUS_FILTERS.map(([k, l]) => [k, l, count(k)])}
          value={statusFilter}
          onChange={(f) => { setStatusFilter(f); setPage(0); }}
        />
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <Button size="sm" onClick={() => setPage(cur + 1)} disabled={cur >= maxDay} title="older day" icon={<ChevronLeft size={13} />} />
          <span className="os-mono" style={{ minWidth: 110, textAlign: 'center', fontSize: 11, fontWeight: 600, color: '#cfd6df' }}>
            {dayLabel(cur)} · {rows.length}
          </span>
          <Button size="sm" onClick={() => setPage(cur - 1)} disabled={cur === 0} title="newer day" icon={<ChevronRight size={13} />} />
        </div>
      </div>

      {rows.length === 0 && (
        <EmptyState
          title={`No posts on ${dayLabel(cur)}`}
          sub={posts ? 'drafts are written at each posting mark · or use Quick actions → Write a draft' : 'loading…'}
        />
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {rows.map((p) => (
          <QueueCard key={p.id} post={p} reload={reload} maxAttempts={status ? status.max_post_attempts : 3} />
        ))}
      </div>
    </div>
  );
}
