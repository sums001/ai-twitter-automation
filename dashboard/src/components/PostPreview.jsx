import React, { useEffect, useState } from 'react';
import { MessageCircle, Repeat2, Heart, BarChart2, Bookmark, Share, MoreHorizontal, X, Copy, Check, ExternalLink } from 'lucide-react';
import { NICHE_COLOR, nicheLabel } from '../data';
import { ago } from '../utils';

// A faithful-ish preview of how a draft will read on X (Twitter), rendered in a
// centered modal. Author identity isn't a real connected account — we present
// the niche as the author (label + a derived @handle) so the text + image read
// in context. Opened by clicking a post card in the Queue.

const VERIFIED_BLUE = '#1d9bf0';

function handleFor(niche) {
  // A plausible @handle derived from the niche slug.
  return '@' + String(niche || 'opensocial').replace(/[^a-z0-9]+/gi, '_').toLowerCase();
}

function ActionIcon({ icon: Icon, label, color = '#71767b', hover }) {
  return (
    <button
      className="x-action"
      title={label}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 7, background: 'transparent',
        border: 'none', cursor: 'default', color, fontSize: 13, padding: 0,
        ['--x-hover']: hover || VERIFIED_BLUE,
      }}
    >
      <span style={{ display: 'inline-flex' }}><Icon size={18} strokeWidth={1.8} /></span>
    </button>
  );
}

export default function PostPreview({ post, onClose }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const copyText = () => {
    const text = post?.text || '';
    const write = navigator.clipboard?.writeText ? navigator.clipboard.writeText(text) : Promise.reject();
    write
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })
      .catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
        document.body.removeChild(ta);
      });
  };

  if (!post) return null;
  const color = NICHE_COLOR[post.niche] || '#7a828f';
  const name = nicheLabel(post.niche);
  const initial = (name || '?').trim()[0]?.toUpperCase() || '?';
  const overLimit = (post.text || '').length > 280;

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,.7)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        padding: '64px 16px', overflowY: 'auto', backdropFilter: 'blur(2px)',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 558, background: '#000', borderRadius: 16,
          border: '1px solid #2f3336', boxShadow: '0 24px 70px rgba(0,0,0,.6)', overflow: 'hidden',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        }}
      >
        {/* modal chrome */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderBottom: '1px solid #16181c' }}>
          <span className="os-mono" style={{ fontSize: 10, color: '#71767b', letterSpacing: '.4px' }}>X PREVIEW</span>
          <button
            onClick={onClose}
            title="Close (Esc)"
            style={{ display: 'inline-flex', background: 'transparent', border: 'none', color: '#71767b', cursor: 'pointer', padding: 4, borderRadius: 999 }}
          >
            <X size={18} />
          </button>
        </div>

        {/* the tweet */}
        <div style={{ padding: '14px 16px 4px' }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <div
              style={{
                width: 44, height: 44, borderRadius: '50%', flexShrink: 0, background: color,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#000', fontWeight: 800, fontSize: 19,
              }}
            >
              {initial}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                <span style={{ color: '#e7e9ea', fontWeight: 700, fontSize: 15 }}>{name}</span>
                <svg viewBox="0 0 22 22" width="18" height="18" aria-label="Verified" style={{ flexShrink: 0 }}>
                  <path fill={VERIFIED_BLUE} d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44C11.275 1.215 10.646 1.018 10 1c-.646.018-1.275.215-1.816.57-.54.354-.972.852-1.246 1.438-.607-.223-1.264-.27-1.897-.14-.634.131-1.218.437-1.687.882-.445.47-.75 1.053-.882 1.687-.13.633-.083 1.29.14 1.897-.587.274-1.086.705-1.44 1.246C1.215 10.275 1.018 10.904 1 11.55c.018.646.215 1.275.57 1.816.354.54.852.972 1.438 1.246-.223.607-.27 1.264-.14 1.897.131.634.437 1.218.882 1.687.47.445 1.053.75 1.687.882.633.13 1.29.083 1.897-.14.274.587.705 1.086 1.246 1.44.541.354 1.17.551 1.816.569.646-.018 1.275-.215 1.816-.57.54-.354.972-.852 1.246-1.438.606.239 1.27.3 1.897.14.634-.131 1.218-.437 1.687-.882.445-.47.75-1.053.882-1.687.16-.627.1-1.291-.14-1.897.587-.274 1.086-.705 1.44-1.246.354-.541.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794 1.347 1.246z" />
                </svg>
                <span style={{ color: '#71767b', fontSize: 15 }}>{handleFor(post.niche)}</span>
                <span style={{ color: '#71767b', fontSize: 15 }}>·</span>
                <span style={{ color: '#71767b', fontSize: 15 }}>{ago(post.created_at) || 'now'}</span>
                <span style={{ marginLeft: 'auto', color: '#71767b', display: 'inline-flex' }}><MoreHorizontal size={18} /></span>
              </div>

              <div
                style={{
                  color: '#e7e9ea', fontSize: 15, lineHeight: 1.45, marginTop: 4,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}
              >
                {post.text}
              </div>

              {post.media_url && (
                <div style={{ marginTop: 12, borderRadius: 16, overflow: 'hidden', border: '1px solid #2f3336', position: 'relative' }}>
                  <img
                    src={post.media_url}
                    alt=""
                    style={{ display: 'block', width: '100%', maxHeight: 510, objectFit: 'cover' }}
                    onError={(e) => { e.currentTarget.parentElement.style.display = 'none'; }}
                  />
                </div>
              )}

              {post.media_attribution && (
                <div style={{ marginTop: 6, fontSize: 12, color: '#71767b' }}>{post.media_attribution}</div>
              )}

              <div style={{ color: '#71767b', fontSize: 14, marginTop: 14, paddingBottom: 12, borderBottom: '1px solid #16181c' }}>
                {new Date(post.created_at).toLocaleString(undefined, { hour: 'numeric', minute: '2-digit' })} · {new Date(post.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
              </div>

              {/* action bar */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', maxWidth: 420, padding: '10px 0 12px' }}>
                <ActionIcon icon={MessageCircle} label="Reply" hover="#1d9bf0" />
                <ActionIcon icon={Repeat2} label="Repost" hover="#00ba7c" />
                <ActionIcon icon={Heart} label="Like" hover="#f91880" />
                <ActionIcon icon={BarChart2} label="Views" hover="#1d9bf0" />
                <ActionIcon icon={Bookmark} label="Bookmark" hover="#1d9bf0" />
                <ActionIcon icon={Share} label="Share" hover="#1d9bf0" />
              </div>
            </div>
          </div>
        </div>

        {/* footer: real draft metadata, so the preview stays honest */}
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
            borderTop: '1px solid #16181c', background: '#0a0a0a',
          }}
        >
          <span className="os-mono" style={{ fontSize: 10, color: '#71767b', textTransform: 'capitalize' }}>{post.type}</span>
          <span className="os-mono" style={{ fontSize: 10, color: '#71767b' }}>{post.status?.replace('_', ' ')}</span>
          <button
            onClick={copyText}
            title="Copy post text"
            className="os-mono"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 5, background: 'transparent',
              border: '1px solid #2f3336', borderRadius: 7, cursor: 'pointer', padding: '4px 9px',
              fontSize: 10, color: copied ? '#00ba7c' : '#71767b',
            }}
          >
            {copied ? <Check size={11} /> : <Copy size={11} />}
            {copied ? 'copied' : 'copy text'}
          </button>
          {post.source_url && (
            <a
              href={post.source_url}
              target="_blank"
              rel="noopener noreferrer"
              title={post.source_url}
              className="os-mono"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5, textDecoration: 'none',
                border: '1px solid #2f3336', borderRadius: 7, padding: '4px 9px',
                fontSize: 10, color: '#71767b',
              }}
            >
              <ExternalLink size={11} />
              source
            </a>
          )}
          <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: overLimit ? '#f5455c' : '#71767b' }}>
            {(post.text || '').length}/280
          </span>
        </div>
      </div>
    </div>
  );
}
