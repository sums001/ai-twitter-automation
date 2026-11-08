import React, { useState } from 'react';
import { Zap, Download, Sparkles, Play, ChevronDown } from 'lucide-react';
import { ROUTE_TITLES, QUICK_ACTIONS } from '../data';
import { useApp } from '../AppContext';
import HowItWorksButton from './HowItWorks';

export function SafetyRibbon() {
  const { dryRun } = useApp();
  const style = dryRun
    ? { background: 'rgba(245,165,36,.1)', color: '#f5a524', borderBottom: '1px solid rgba(245,165,36,.25)' }
    : { background: 'rgba(62,207,142,.1)', color: '#3ecf8e', borderBottom: '1px solid rgba(62,207,142,.25)' };
  return (
    <div style={{ height: 26, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9, ...style }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'currentColor', animation: 'os-pulse 1.6s infinite' }} />
      <span className="os-mono" style={{ fontSize: 11, fontWeight: 600, letterSpacing: '.4px' }}>
        {dryRun
          ? 'DRY-RUN — actions are simulated, no posts are published to X'
          : 'LIVE — publish actions will post to X immediately'}
      </span>
    </div>
  );
}

const ACTION_ICON = { fetch_sources: Download, generate_posts: Sparkles, run_slots: Play };

function QuickActions() {
  const { runCommand } = useApp();
  const [open, setOpen] = useState(false);

  const fire = (type) => { runCommand(type); setOpen(false); };

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="run-btn"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 7, border: 'none', cursor: 'pointer',
          fontSize: 12, fontWeight: 700, padding: '7px 13px', borderRadius: 8, background: '#3ecf8e', color: '#0a0c0f',
        }}
      >
        <Zap size={13} fill="#0a0c0f" strokeWidth={0} />
        Quick actions
        <ChevronDown size={13} style={{ transition: 'transform .15s', transform: open ? 'rotate(180deg)' : 'none' }} />
      </button>

      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 60 }} />
          <div
            className="lane-open"
            style={{
              position: 'absolute', right: 0, top: 'calc(100% + 8px)', zIndex: 61, width: 264,
              background: '#111419', border: '1px solid #232932', borderRadius: 12, padding: 6,
              boxShadow: '0 18px 50px rgba(0,0,0,.55)',
            }}
          >
            {QUICK_ACTIONS.map(([type, label, desc]) => {
              const Icon = ACTION_ICON[type] || Play;
              return (
                <button
                  key={type}
                  onClick={() => fire(type)}
                  className="hover-tab"
                  style={{
                    display: 'flex', alignItems: 'center', gap: 11, width: '100%', textAlign: 'left',
                    border: 'none', background: 'transparent', cursor: 'pointer', padding: '9px 10px', borderRadius: 8,
                  }}
                >
                  <span style={{ display: 'inline-flex', width: 28, height: 28, borderRadius: 8, flexShrink: 0, alignItems: 'center', justifyContent: 'center', background: 'rgba(62,207,142,.1)', color: '#3ecf8e' }}>
                    <Icon size={14} />
                  </span>
                  <span>
                    <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: '#e7eaef' }}>{label}</span>
                    <span className="os-mono" style={{ display: 'block', fontSize: 9.5, color: '#5b6470', marginTop: 1 }}>{desc}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function ToggleSwitch({ label, on, onToggle, color, title }) {
  const accent = `rgb(${color})`;
  return (
    <button
      onClick={onToggle}
      className="hover-bright"
      title={title}
      role="switch"
      aria-checked={on}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer',
        padding: '5px 11px', borderRadius: 8,
        border: `1px solid ${on ? `rgba(${color},.4)` : '#2a313c'}`,
        background: on ? `rgba(${color},.1)` : '#141922',
      }}
    >
      <span className="os-mono" style={{ fontSize: 9, fontWeight: 700, letterSpacing: '.4px', color: '#9aa3af' }}>{label}</span>
      <span style={{ position: 'relative', width: 30, height: 17, borderRadius: 999, flexShrink: 0, transition: 'background .15s', background: on ? accent : '#2a313c' }}>
        <span style={{ position: 'absolute', top: 2.5, left: on ? 15 : 2.5, width: 12, height: 12, borderRadius: '50%', background: '#0a0c0f', transition: 'left .15s' }} />
      </span>
      <span className="os-mono" style={{ fontSize: 10, fontWeight: 700, minWidth: 20, color: on ? accent : '#7a828f' }}>
        {on ? 'ON' : 'OFF'}
      </span>
    </button>
  );
}

export default function TopBar() {
  const { route, dryRun, toggleDry, autopilot, toggleAutopilot } = useApp();
  const [title, sub] = ROUTE_TITLES[route] || ['', ''];

  return (
    <header style={{ height: 56, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 16, padding: '0 20px', background: '#0c0f13', borderBottom: '1px solid #1a1f27' }}>
      <div style={{ minWidth: 150 }}>
        <div style={{ fontSize: 15, fontWeight: 700, letterSpacing: '-.2px' }}>{title}</div>
        <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 1 }}>{sub}</div>
      </div>
      <HowItWorksButton route={route} />

      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        <ToggleSwitch
          label="DRY-RUN"
          on={dryRun}
          onToggle={toggleDry}
          color="245,165,36"
          title="When on, publish actions are simulated and nothing is posted to X. Turn off to go live."
        />
        <ToggleSwitch
          label="AUTOPILOT"
          on={autopilot}
          onToggle={toggleAutopilot}
          color="62,207,142"
          title="Every posting mark writes a draft. When on, the best fresh draft is also published at each mark."
        />

        <QuickActions />
      </div>
    </header>
  );
}
