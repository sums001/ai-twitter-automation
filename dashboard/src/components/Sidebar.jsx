import React from 'react';
import { LayoutGrid, Star, ListOrdered, CalendarDays, History, Share2, Database, ScrollText, Settings } from 'lucide-react';
import { NAV_GROUPS } from '../data';
import { useApp } from '../AppContext';
import { PulseDot } from './common';
import WorkspaceMenu from './WorkspaceMenu';

const ICONS = {
  dashboard: <LayoutGrid size={16} />,
  niches: <Star size={16} />,
  queue: <ListOrdered size={16} />,
  schedule: <CalendarDays size={16} />,
  history: <History size={16} />,
  sources: <Share2 size={16} />,
  rawdata: <Database size={16} />,
  logs: <ScrollText size={16} />,
  settings: <Settings size={16} />,
};

export default function Sidebar() {
  const { route, setRoute, status } = useApp();
  const NAV_BADGE = { queue: status && status.queue_pending ? String(status.queue_pending) : null };
  return (
    <aside style={{ width: 228, flexShrink: 0, background: '#0c0f13', borderRight: '1px solid #1a1f27', display: 'flex', flexDirection: 'column' }}>
      <div style={{ height: 56, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px', borderBottom: '1px solid #1a1f27' }}>
        <div style={{ width: 22, height: 22, borderRadius: 6, background: '#3ecf8e', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <div style={{ width: 9, height: 9, borderRadius: '50%', background: '#0a0c0f' }} />
        </div>
        <div style={{ fontWeight: 700, fontSize: 15, letterSpacing: '-.2px' }}>OpenX</div>
        <div className="os-mono" style={{ marginLeft: 'auto', fontSize: 9, color: '#4b5563', letterSpacing: '.5px' }}>v0.9</div>
      </div>
      <nav style={{ flex: 1, overflowY: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 2 }}>
        {NAV_GROUPS.map(([group, items], gi) => (
          <React.Fragment key={group}>
            <div
              className="os-mono"
              style={{
                fontSize: 8.5, fontWeight: 700, letterSpacing: '.6px', color: '#4b5563',
                padding: gi ? '14px 13px 6px' : '4px 13px 6px', textTransform: 'uppercase',
              }}
            >
              {group}
            </div>
            {items.map(([id, label]) => {
              const active = route === id;
              const badge = NAV_BADGE[id];
              return (
                <button
                  key={id}
                  onClick={() => setRoute(id)}
                  className={`nav-btn${active ? ' active' : ''}`}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 11, padding: '8px 11px', borderRadius: 8,
                    border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 500, width: '100%',
                    background: active ? '#15201b' : 'transparent', color: active ? '#3ecf8e' : '#8b93a0',
                  }}
                >
                  <span style={{ display: 'flex', width: 16, height: 16, flexShrink: 0, opacity: 0.9 }}>{ICONS[id]}</span>
                  <span style={{ flex: 1, textAlign: 'left' }}>{label}</span>
                  {badge && (
                    <span
                      className="os-mono"
                      style={{
                        fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 999,
                        background: active ? 'rgba(62,207,142,.16)' : '#1c222b', color: active ? '#3ecf8e' : '#8b93a0',
                      }}
                    >
                      {badge}
                    </span>
                  )}
                </button>
              );
            })}
          </React.Fragment>
        ))}
      </nav>
      <WorkspaceMenu />
      <div style={{ padding: '12px 16px', borderTop: '1px solid #1a1f27', display: 'flex', alignItems: 'center', gap: 8 }}>
        <PulseDot color={status ? '#3ecf8e' : '#f5455c'} />
        <span className="os-mono" style={{ fontSize: 10, color: '#6b7280' }}>
          {status ? 'service · polling' : 'service unreachable'}
        </span>
      </div>
    </aside>
  );
}
