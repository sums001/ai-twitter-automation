import React, { useState } from 'react';
import { Search } from 'lucide-react';
import { usePoll } from '../api';
import { useApp } from '../AppContext';
import { LEVEL_COLORS } from '../data';
import { hms } from '../utils';
import { PulseDot, EmptyState, Segmented, fieldStyle } from '../components/common';

const MSG_COLOR = { error: '#f0a8b0', warn: '#e7c98a', info: '#aeb6c0' };

export default function Logs() {
  const { withAccount } = useApp();
  const [level, setLevel] = useState('all');
  const [search, setSearch] = useState('');
  const { data } = usePoll(withAccount('/api/logs?limit=300'), 3000);
  const all = data || [];

  const counts = { all: all.length, info: 0, warn: 0, error: 0 };
  all.forEach((l) => { counts[l.level] = (counts[l.level] || 0) + 1; });

  const rows = all
    .filter((l) => level === 'all' || l.level === level)
    .filter((l) => !search || l.msg.toLowerCase().includes(search.toLowerCase()))
    .slice()
    .reverse(); // newest first

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <Segmented
          options={[['all', 'All'], ['info', 'Info'], ['warn', 'Warnings'], ['error', 'Errors']].map(([k, l]) => [k, l, counts[k] || 0])}
          value={level}
          onChange={setLevel}
        />
        <div style={{ ...fieldStyle, marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, padding: '2px 11px', minWidth: 200 }}>
          <span style={{ display: 'inline-flex', color: '#5b6470' }}><Search size={13} /></span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="search logs…"
            className="os-mono"
            style={{ background: 'transparent', border: 'none', outline: 'none', color: '#e7eaef', fontSize: 11, padding: '5px 0', width: 160 }}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <PulseDot duration="1.6s" />
          <span className="os-mono" style={{ fontSize: 10, color: '#6b7280' }}>live</span>
        </div>
      </div>

      {data && !rows.length && (
        <EmptyState title="No log lines" sub="service activity (fetch, generate, publish) lands here" />
      )}

      {rows.length > 0 && (
        <div style={{
          // Fill the viewport below the ribbon (26) + topbar (56) + wrapper
          // padding + filter bar, and scroll internally instead of growing the page.
          height: 'calc(100vh - 210px)', overflowY: 'auto',
          background: '#0b0e12', border: '1px solid #1f242d', borderRadius: 12,
          padding: '8px 6px', fontFamily: "'JetBrains Mono', monospace",
        }}>
          {rows.map((l) => {
            const [fg, bg] = LEVEL_COLORS[l.level] || LEVEL_COLORS.info;
            return (
              <div key={l.id} className="hover-row" style={{ display: 'flex', alignItems: 'flex-start', gap: 11, padding: '5px 11px', borderRadius: 6 }}>
                <span style={{ fontSize: 10.5, color: '#566', flexShrink: 0, width: 72 }}>{hms(l.ts)}</span>
                <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 5, width: 40, textAlign: 'center', flexShrink: 0, color: fg, background: bg }}>{l.level}</span>
                <span style={{ fontSize: 11.5, color: MSG_COLOR[l.level] || '#aeb6c0', flex: 1, minWidth: 0, lineHeight: 1.5 }}>{l.msg}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
