import React from 'react';
import { LoaderCircle } from 'lucide-react';
import { useApp } from '../AppContext';
import { STATUS_COLORS, COMMAND_LABELS } from '../data';

// command lifecycle toasts: pending → running → failed (done toasts disappear)
export default function Toasts() {
  const { commands } = useApp();
  const visible = commands.filter((c) => c.status !== 'done').slice(0, 4);

  return (
    <div style={{ position: 'fixed', right: 20, bottom: 18, display: 'flex', flexDirection: 'column', gap: 8, zIndex: 50, pointerEvents: 'none' }}>
      {visible.map((c) => {
        const running = c.status === 'running';
        const badgeKey = running ? 'scheduled' : c.status === 'pending' ? 'draft' : 'failed';
        const [fg, bg] = STATUS_COLORS[badgeKey];
        const err = c.status === 'failed' && c.result && c.result.error;
        return (
          <div
            key={c.id}
            style={{
              display: 'flex', alignItems: 'center', gap: 10, background: '#161b22', border: '1px solid #2a313c',
              borderRadius: 10, padding: '10px 14px', minWidth: 240, boxShadow: '0 8px 30px rgba(0,0,0,.4)',
              animation: 'os-toast .25s ease',
            }}
          >
            {running && (
              <span style={{ display: 'flex', color: '#7aa2f7', animation: 'os-spin 1s linear infinite' }}>
                <LoaderCircle size={13} />
              </span>
            )}
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#e7eaef' }}>{COMMAND_LABELS[c.type] || c.type}</div>
              <div className="os-mono" style={{ fontSize: 10, color: err ? '#f5455c' : '#6b7280', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {running ? 'service executing…' : c.status === 'pending' ? 'waiting for worker…' : err || 'see logs'}
              </div>
            </div>
            <span
              className="os-mono"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, fontWeight: 600,
                padding: '2px 8px', borderRadius: 999, letterSpacing: '.2px', color: fg, background: bg,
              }}
            >
              {c.status}
            </span>
          </div>
        );
      })}
    </div>
  );
}
