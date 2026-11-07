import React, { useState } from 'react';
import { AlertTriangle, Check, Database, Download, Play, Sparkles, Trash2 } from 'lucide-react';
import { useApp } from '../AppContext';
import { QUICK_ACTIONS } from '../data';
import { api, usePoll } from '../api';
import { Card, Toggle } from '../components/common';

// Settings split into two scopes, mirroring the sidebar's Workspace/Global
// grouping. Workspace tabs act on the current workspace; Global tabs affect the
// whole install (shared fetch cadence, the entire database).
const SETTINGS_GROUPS = [
  ['Workspace', [
    ['general', 'Controls'],
    ['actions', 'Actions'],
    ['ai', 'AI providers'],
    ['credentials', 'Credentials'],
    ['reset-workspace', 'Reset workspace'],
  ]],
  ['Global', [
    ['workspaces', 'Workspaces'],
    ['reset-db', 'Reset database'],
  ]],
];
const DANGER_TABS = new Set(['reset-workspace', 'reset-db']);

const inputStyle = {
  colorScheme: 'dark', background: '#0d1116', border: '1px solid #1c212a', borderRadius: 8,
  color: '#e7eaef', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, padding: '8px 11px',
};

const numStyle = { ...inputStyle, width: 90, textAlign: 'center', fontWeight: 600 };

const ACTION_ICON = { fetch_sources: Download, generate_posts: Sparkles, run_slots: Play };

// ---------------------------------------------------------------------------
// AI providers
// ---------------------------------------------------------------------------

const TEXT_PROVIDERS = [
  ['claude', 'Claude'],
  ['chatgpt', 'ChatGPT'],
  ['local', 'Local'],
  ['template', 'Template (offline)'],
];
// which credential a text provider authenticates with (null = keyless)
const TEXT_KEY_ENV = { claude: 'ANTHROPIC_API_KEY', chatgpt: 'OPENAI_API_KEY' };
// sensible default model + endpoint per provider — applied when the provider is
// switched, and shown as the placeholder.
const PROVIDER_DEFAULTS = {
  claude: { model: 'anthropic/claude-sonnet-4-6', endpoint: '' },
  chatgpt: { model: 'gpt-4o-mini', endpoint: '' },
  local: { model: 'ollama/gemma3:4b', endpoint: 'http://localhost:11434' },
  template: { model: '', endpoint: '' },
};
const MODEL_PLACEHOLDER = {
  claude: PROVIDER_DEFAULTS.claude.model,
  chatgpt: PROVIDER_DEFAULTS.chatgpt.model,
  local: PROVIDER_DEFAULTS.local.model,
};

function kv(label, node, hint) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
      <span style={{ minWidth: 0 }}>
        <span className="os-mono" style={{ fontSize: 11, color: '#7a828f' }}>{label}</span>
        {hint && <span className="os-mono" style={{ fontSize: 9, color: '#f5a524', marginLeft: 7 }}>{hint}</span>}
      </span>
      {node}
    </div>
  );
}

// Compact inline secret field — writes to /api/credentials, never reads back.
// With ``account`` the key is stored per workspace (AI keys); without it, global.
function AiKeyField({ env, set, onSaved, account }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const save = () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    const path = account ? `/api/credentials?account=${encodeURIComponent(account)}` : '/api/credentials';
    api.post(path, { key: env, value: value.trim() })
      .then(() => { setValue(''); setEditing(false); onSaved && onSaved(); })
      .catch((e) => alert(e.message)).finally(() => setBusy(false));
  };
  return kv('api key', editing ? (
    <span style={{ display: 'flex', gap: 6, flex: 1, marginLeft: 16 }}>
      <input
        autoFocus type="password" value={value} onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false); }}
        placeholder={`${env} · write-only`} style={{ ...inputStyle, flex: 1 }}
      />
      <button onClick={save} style={{ border: 'none', background: '#3ecf8e', color: '#0a0c0f', cursor: 'pointer', fontSize: 11, fontWeight: 700, padding: '0 12px', borderRadius: 7 }}>
        {busy ? '…' : 'save'}
      </button>
      <button onClick={() => setEditing(false)} style={{ border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontSize: 11, padding: '0 10px', borderRadius: 7 }}>✕</button>
    </span>
  ) : (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9 }}>
      <span className="os-mono" style={{ fontSize: 10.5, fontWeight: 600, color: set ? '#3ecf8e' : '#f5a524' }}>
        {set ? 'key set ✓' : 'not set'}
      </span>
      <button
        onClick={() => setEditing(true)}
        className="hover-bright"
        style={{
          cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 600,
          padding: '5px 11px', borderRadius: 6,
          border: `1px solid ${set ? '#232932' : 'rgba(62,207,142,.35)'}`,
          background: set ? 'transparent' : 'rgba(62,207,142,.1)', color: set ? '#9aa3af' : '#3ecf8e',
        }}
      >
        {set ? 'replace' : 'set key'}
      </button>
    </span>
  ));
}

function AiProvidersTab() {
  const { withWorkspace, account } = useApp();
  const { data, reload } = usePoll(withWorkspace('/api/ai'), 0);
  const [cfg, setCfg] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  React.useEffect(() => { if (data) { setCfg(data); setDirty(false); } }, [data]);

  const set = (section, key, value) => {
    setCfg((c) => ({ ...c, [section]: { ...c[section], [key]: value } }));
    setDirty(true);
  };

  // Switching provider applies that provider's default model + endpoint so the
  // fields are sensible out of the box (overwrite-able afterwards).
  const changeProvider = (provider) => {
    const d = PROVIDER_DEFAULTS[provider] || { model: '', endpoint: '' };
    setCfg((c) => ({ ...c, text: { ...c.text, provider, model: d.model, endpoint: d.endpoint } }));
    setDirty(true);
  };

  const text = (cfg && cfg.text) || {};
  const keyStatus = (cfg && cfg.key_status) || {};

  const textKeyEnv = TEXT_KEY_ENV[text.provider];
  const localMissingEndpoint = text.provider === 'local' && !((text.endpoint || '').trim());
  const blocked = localMissingEndpoint;

  // Connection test runs against the form's current values (saved or not).
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  React.useEffect(() => { setTestResult(null); }, [text.provider, text.model, text.endpoint]);
  const testConnection = () => {
    if (!cfg || testing || blocked) return;
    setTesting(true);
    setTestResult(null);
    api.post(withWorkspace('/api/ai/test'), { text: cfg.text })
      .then(setTestResult)
      .catch((e) => setTestResult({ ok: false, error: e.message }))
      .finally(() => setTesting(false));
  };

  const save = () => {
    if (!cfg || saving || !dirty || blocked) return;
    setSaving(true);
    api.put(withWorkspace('/api/ai'), { text: cfg.text })
      .then((saved) => { setCfg(saved); setDirty(false); reload(); })
      .catch((e) => alert(e.message)).finally(() => setSaving(false));
  };

  return (
    <Card style={{ padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>AI providers</span>
        <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>per workspace · applies to this workspace's niches</span>
        <button
          onClick={save}
          className="run-btn"
          style={{
            marginLeft: 'auto', border: 'none', cursor: dirty && !blocked ? 'pointer' : 'default', fontSize: 12, fontWeight: 700,
            padding: '7px 14px', borderRadius: 8,
            background: dirty && !blocked ? '#3ecf8e' : '#1c222b', color: dirty && !blocked ? '#0a0c0f' : '#7a828f',
          }}
        >
          {saving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
        </button>
      </div>

      {!cfg ? (
        <span className="os-mono" style={{ fontSize: 11, color: '#5b6470' }}>loading…</span>
      ) : (
        <div style={{ maxWidth: 460 }}>
          {/* ---- text ---- */}
          <div>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 12 }}>Text generation</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
              {kv('provider', (
                <select value={text.provider || 'local'} onChange={(e) => changeProvider(e.target.value)} style={{ ...inputStyle, padding: '6px 9px', width: 200 }}>
                  {TEXT_PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              ))}
              {text.provider !== 'template' && (
                <>
                  {kv('model', (
                    <input value={text.model || ''} onChange={(e) => set('text', 'model', e.target.value)} placeholder={MODEL_PLACEHOLDER[text.provider] || 'model id'} style={{ ...inputStyle, width: 200 }} />
                  ))}
                  {kv('endpoint', (
                    <input value={text.endpoint || ''} onChange={(e) => set('text', 'endpoint', e.target.value)} placeholder={text.provider === 'local' ? 'http://localhost:11434' : 'optional · custom gateway'} style={{ ...inputStyle, width: 200, borderColor: localMissingEndpoint ? 'rgba(245,69,92,.5)' : '#1c212a' }} />
                  ), text.provider === 'local' ? 'required' : null)}
                  {textKeyEnv && (
                    <AiKeyField env={textKeyEnv} set={!!keyStatus[textKeyEnv]} onSaved={reload} account={account} />
                  )}
                  {kv('temperature', (
                    <input type="number" min="0" max="2" step="0.05" value={text.temperature != null ? text.temperature : 0.7} onChange={(e) => set('text', 'temperature', +e.target.value)} style={numStyle} />
                  ))}
                </>
              )}
            </div>
            {text.provider === 'local' && (
              <div className="os-mono" style={{ fontSize: 9.5, color: '#5b6470', marginTop: 11, lineHeight: 1.5 }}>
                point at an Ollama or any OpenAI-compatible server — no key needed.
              </div>
            )}
            {text.provider === 'template' && (
              <div className="os-mono" style={{ fontSize: 9.5, color: '#5b6470', marginTop: 11, lineHeight: 1.5 }}>
                deterministic offline writer — no model, no key, no network.
              </div>
            )}
            {text.provider !== 'template' && (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginTop: 14 }}>
                <button
                  onClick={testConnection}
                  disabled={testing || blocked}
                  className="hover-bright"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0,
                    cursor: testing || blocked ? 'default' : 'pointer', fontFamily: 'inherit', fontSize: 11.5, fontWeight: 600,
                    padding: '6px 12px', borderRadius: 7, border: '1px solid rgba(62,207,142,.35)',
                    background: 'rgba(62,207,142,.1)', color: '#3ecf8e', opacity: blocked ? 0.5 : 1,
                  }}
                >
                  <Play size={11} />
                  {testing ? 'Testing…' : 'Test connection'}
                </button>
                {testResult && (
                  <div className="os-mono" style={{ fontSize: 10.5, lineHeight: 1.5, paddingTop: 5, color: testResult.ok ? '#3ecf8e' : '#f5455c', wordBreak: 'break-word' }}>
                    {testResult.ok
                      ? <>✓ connected · {testResult.model} · {testResult.latency_ms} ms{testResult.reply ? <span style={{ color: '#7a828f' }}> · replied “{testResult.reply}”</span> : null}</>
                      : <>✗ {testResult.error}</>}
                  </div>
                )}
              </div>
            )}
            <div className="os-mono" style={{ fontSize: 9.5, color: '#5b6470', marginTop: 16, lineHeight: 1.5 }}>
              images are set per-niche (Niches page → Content → Image source: Unsplash, Content, or None).
            </div>
          </div>
        </div>
      )}

      {localMissingEndpoint && (
        <div className="os-mono" style={{ fontSize: 10.5, color: '#f5455c', marginTop: 14 }}>
          the local provider needs an endpoint URL before it can be saved.
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Reset modal + Danger zone
// ---------------------------------------------------------------------------

function ResetModal({ onClose, onDone }) {
  const [clearCreds, setClearCreds] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const armed = phrase.trim().toUpperCase() === 'RESET';

  const run = async () => {
    if (!armed || busy) return;
    setBusy(true);
    try {
      const r = await api.post('/api/reset', { confirm: true, clear_credentials: clearCreds });
      const total = Object.values(r.deleted || {}).reduce((a, b) => a + b, 0);
      setDone(total);
      setTimeout(onDone, 1400);
    } catch (e) {
      alert(e.message);
      setBusy(false);
    }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(5,7,10,.66)', display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(2px)' }}>
      <div onClick={(e) => e.stopPropagation()} className="lane-open" style={{ width: 440, maxWidth: '92vw', background: '#111419', border: '1px solid #3a232b', borderRadius: 14, padding: '22px 24px', boxShadow: '0 24px 70px rgba(0,0,0,.6)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
          <span style={{ display: 'inline-flex', width: 34, height: 34, borderRadius: 9, alignItems: 'center', justifyContent: 'center', background: 'rgba(245,69,92,.12)', color: '#f5455c', flexShrink: 0 }}>
            <AlertTriangle size={18} />
          </span>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>Reset database</div>
            <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>clears all content, drafts, history &amp; logs</div>
          </div>
        </div>

        {done == null ? (
          <>
            <div style={{ marginTop: 18, fontSize: 12.5, color: '#aeb6c0', lineHeight: 1.6 }}>
              This wipes every fetched item, generated post, publish record, log line and source
              fetch status. Your <strong style={{ color: '#cfd6df' }}>niche config files are kept</strong>. This cannot be undone.
            </div>

            <label
              onClick={() => setClearCreds((v) => !v)}
              style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 16, padding: '11px 13px', background: '#0d1116', border: `1px solid ${clearCreds ? 'rgba(245,69,92,.4)' : '#1c212a'}`, borderRadius: 10, cursor: 'pointer' }}
            >
              <span style={{ display: 'inline-flex', width: 18, height: 18, borderRadius: 5, alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: `1.5px solid ${clearCreds ? '#f5455c' : '#3b414b'}`, background: clearCreds ? '#f5455c' : 'transparent', color: '#0a0c0f' }}>
                {clearCreds && <Check size={13} strokeWidth={3} />}
              </span>
              <div>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: '#e7eaef' }}>Also clear credentials</div>
                <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>API keys &amp; X account</div>
              </div>
            </label>

            <div style={{ marginTop: 16 }}>
              <div className="os-mono" style={{ fontSize: 10, color: '#7a828f', marginBottom: 7 }}>
                type <span style={{ color: '#f5455c', fontWeight: 700 }}>RESET</span> to confirm
              </div>
              <input
                autoFocus value={phrase} onChange={(e) => setPhrase(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') run(); }}
                placeholder="RESET"
                style={{ ...inputStyle, width: '100%' }}
              />
            </div>

            <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
              <button onClick={onClose} className="hover-border-text" style={{ flex: 1, border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontSize: 12.5, fontWeight: 600, padding: '10px', borderRadius: 9 }}>Cancel</button>
              <button onClick={run} disabled={!armed || busy} style={{ flex: 1.4, border: 'none', cursor: armed && !busy ? 'pointer' : 'default', fontSize: 12.5, fontWeight: 700, padding: '10px', borderRadius: 9, background: armed && !busy ? '#f5455c' : '#2a1c20', color: armed && !busy ? '#fff' : '#7a5560' }}>
                {busy ? 'Resetting…' : clearCreds ? 'Reset everything' : 'Reset database'}
              </button>
            </div>
          </>
        ) : (
          <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', gap: 10, color: '#3ecf8e' }}>
            <Check size={18} strokeWidth={3} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Cleared {done} row{done === 1 ? '' : 's'}. Reloading…</span>
          </div>
        )}
      </div>
    </div>
  );
}

// Reset only the current workspace: its drafts, history, and settings. The
// workspace, its niche files, and the shared content pool are kept.
function ResetWorkspaceModal({ workspace, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const name = workspace ? workspace.label : 'this workspace';

  const run = async () => {
    if (busy || !workspace) return;
    setBusy(true);
    try {
      const r = await api.post(`/api/accounts/${workspace.id}/reset`);
      const total = Object.values(r.deleted || {}).reduce((a, b) => a + b, 0);
      setDone(total);
      setTimeout(onDone, 1300);
    } catch (e) {
      alert(e.message);
      setBusy(false);
    }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(5,7,10,.66)', display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(2px)' }}>
      <div onClick={(e) => e.stopPropagation()} className="lane-open" style={{ width: 440, maxWidth: '92vw', background: '#111419', border: '1px solid #3a232b', borderRadius: 14, padding: '22px 24px', boxShadow: '0 24px 70px rgba(0,0,0,.6)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
          <span style={{ display: 'inline-flex', width: 34, height: 34, borderRadius: 9, alignItems: 'center', justifyContent: 'center', background: 'rgba(245,69,92,.12)', color: '#f5455c', flexShrink: 0 }}>
            <AlertTriangle size={18} />
          </span>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>Reset workspace @{name}</div>
            <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>clears this workspace's drafts, history &amp; settings</div>
          </div>
        </div>

        {done == null ? (
          <>
            <div style={{ marginTop: 18, fontSize: 12.5, color: '#aeb6c0', lineHeight: 1.6 }}>
              This removes <strong style={{ color: '#cfd6df' }}>@{name}</strong>'s generated posts, its publish history,
              and its settings (dry-run, autopilot, AI, selected niches revert to defaults). Its{' '}
              <strong style={{ color: '#cfd6df' }}>niche config files, the shared content pool, and other workspaces are kept</strong>. This cannot be undone.
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
              <button onClick={onClose} className="hover-border-text" style={{ flex: 1, border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontSize: 12.5, fontWeight: 600, padding: '10px', borderRadius: 9 }}>Cancel</button>
              <button onClick={run} disabled={busy || !workspace} style={{ flex: 1.4, border: 'none', cursor: busy || !workspace ? 'default' : 'pointer', fontSize: 12.5, fontWeight: 700, padding: '10px', borderRadius: 9, background: busy || !workspace ? '#2a1c20' : '#f5455c', color: busy || !workspace ? '#7a5560' : '#fff' }}>
                {busy ? 'Resetting…' : 'Reset workspace'}
              </button>
            </div>
          </>
        ) : (
          <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', gap: 10, color: '#3ecf8e' }}>
            <Check size={18} strokeWidth={3} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Cleared {done} row{done === 1 ? '' : 's'}. Reloading…</span>
          </div>
        )}
      </div>
    </div>
  );
}

// Workspace-scoped reset (amber): only the current workspace's data.
function WorkspaceResetTab() {
  const { currentWorkspace } = useApp();
  const [resettingWs, setResettingWs] = useState(false);
  const wsName = currentWorkspace ? currentWorkspace.label : null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 11 }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: '#f5a524' }}>This workspace</span>
          <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>scoped reset · niche files &amp; shared content preserved</span>
        </div>
        <Card style={{ padding: '16px 18px', border: '1px solid #2a2418', display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ display: 'inline-flex', width: 36, height: 36, borderRadius: 9, alignItems: 'center', justifyContent: 'center', background: 'rgba(245,165,36,.1)', color: '#f5a524', flexShrink: 0 }}>
            <Trash2 size={18} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Reset workspace{wsName ? ` @${wsName}` : ''}</div>
            <div className="os-mono" style={{ fontSize: 10.5, color: '#5b6470', marginTop: 3, lineHeight: 1.4 }}>
              clear only this workspace's drafts, history &amp; settings — other workspaces untouched
            </div>
          </div>
          <button
            onClick={() => setResettingWs(true)}
            disabled={!currentWorkspace}
            className="hover-bright"
            style={{ flexShrink: 0, display: 'inline-flex', alignItems: 'center', gap: 7, border: '1px solid rgba(245,165,36,.4)', background: 'rgba(245,165,36,.08)', color: '#f5a524', cursor: currentWorkspace ? 'pointer' : 'default', fontSize: 12, fontWeight: 700, padding: '9px 15px', borderRadius: 8, opacity: currentWorkspace ? 1 : 0.5 }}
          >
            <Trash2 size={14} /> Reset…
          </button>
        </Card>
      </div>

      {resettingWs && <ResetWorkspaceModal workspace={currentWorkspace} onClose={() => setResettingWs(false)} onDone={() => window.location.reload()} />}
    </div>
  );
}

// Global reset (red): the entire database across every workspace.
function DatabaseResetTab() {
  const [resetting, setResetting] = useState(false);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 11 }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: '#f5455c' }}>Danger zone</span>
          <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>entire database · all workspaces · niche config files preserved</span>
        </div>
        <Card style={{ padding: '16px 18px', border: '1px solid #2a1c20', display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ display: 'inline-flex', width: 36, height: 36, borderRadius: 9, alignItems: 'center', justifyContent: 'center', background: 'rgba(245,69,92,.1)', color: '#f5455c', flexShrink: 0 }}>
            <Database size={18} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Reset database</div>
            <div className="os-mono" style={{ fontSize: 10.5, color: '#5b6470', marginTop: 3, lineHeight: 1.4 }}>
              wipe all content, drafts, history &amp; logs across every workspace — optionally clear stored credentials too
            </div>
          </div>
          <button
            onClick={() => setResetting(true)}
            className="hover-danger"
            style={{ flexShrink: 0, display: 'inline-flex', alignItems: 'center', gap: 7, border: '1px solid rgba(245,69,92,.4)', background: 'rgba(245,69,92,.08)', color: '#f5455c', cursor: 'pointer', fontSize: 12, fontWeight: 700, padding: '9px 15px', borderRadius: 8 }}
          >
            <Trash2 size={14} /> Reset…
          </button>
        </Card>
      </div>

      {resetting && <ResetModal onClose={() => setResetting(false)} onDone={() => window.location.reload()} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// General (service controls)
// ---------------------------------------------------------------------------

// Voice samples: real posts the writer imitates (rhythm, casing, line breaks),
// per workspace, since one X account has one voice. A niche can override them.
function VoiceSamplesCard() {
  const { withAccount, currentWorkspace } = useApp();
  const [saved, setSaved] = useState(null); // { samples, default }
  const [draft, setDraft] = useState([]);
  const [busy, setBusy] = useState(false);
  const wsName = currentWorkspace ? `@${currentWorkspace.label}` : 'this workspace';

  const load = React.useCallback(() => {
    api.get(withAccount('/api/voice')).then((v) => { setSaved(v); setDraft(v.samples); }).catch(() => {});
  }, [withAccount]);
  React.useEffect(load, [load]);

  const clean = draft.map((s) => s.trim()).filter(Boolean);
  const dirty = saved && JSON.stringify(clean) !== JSON.stringify(saved.samples);

  const save = () => {
    if (!dirty || busy) return;
    setBusy(true);
    api.put(withAccount('/api/voice'), { samples: clean })
      .then((v) => { setSaved(v); setDraft(v.samples); })
      .catch((e) => alert(e.message)).finally(() => setBusy(false));
  };
  const reset = () => {
    setBusy(true);
    api.del(withAccount('/api/voice'))
      .then((v) => { setSaved(v); setDraft(v.samples); })
      .catch((e) => alert(e.message)).finally(() => setBusy(false));
  };
  const setAt = (i, v) => setDraft((d) => d.map((x, j) => (j === i ? v : x)));

  const area = {
    ...inputStyle, width: '100%', minHeight: 74, resize: 'vertical', lineHeight: 1.5,
    fontFamily: 'inherit', fontSize: 12.5,
  };

  return (
    <Card style={{ padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Voice samples</span>
        <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>
          posts {wsName} sounds like · {saved && saved.default ? 'using the defaults' : 'custom'}
        </span>
      </div>
      <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginBottom: 12, lineHeight: 1.6 }}>
        The writer copies their style (short paragraphs, casing, line breaks), never their topics.
        Keep 3 to 5 real posts. Blank lines inside a sample are kept.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
        {draft.map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <textarea value={s} onChange={(e) => setAt(i, e.target.value)} style={area} />
            <button
              onClick={() => setDraft((d) => d.filter((_, j) => j !== i))}
              className="hover-remove" title="remove sample"
              style={{ border: 'none', background: 'transparent', color: '#f5455c', cursor: 'pointer', padding: '6px', fontSize: 14 }}
            >×</button>
          </div>
        ))}
        {draft.length < 8 && (
          <button
            onClick={() => setDraft((d) => [...d, ''])}
            className="hover-add"
            style={{ alignSelf: 'flex-start', border: '1px dashed #2a313c', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace", fontSize: 11, padding: '6px 11px', borderRadius: 7 }}
          >+ add sample</button>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 12 }}>
        {saved && !saved.default && (
          <button onClick={reset} disabled={busy} className="hover-border-text" style={{ border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: '8px 14px', borderRadius: 8 }}>
            Use defaults
          </button>
        )}
        <button
          onClick={save}
          style={{
            border: 'none', cursor: dirty && !busy ? 'pointer' : 'default', fontSize: 12, fontWeight: 700,
            padding: '8px 16px', borderRadius: 8,
            background: dirty && !busy ? '#3ecf8e' : '#1c222b', color: dirty && !busy ? '#0a0c0f' : '#7a828f',
          }}
        >
          {busy ? 'Saving…' : dirty ? 'Save samples' : 'Saved'}
        </button>
      </div>
    </Card>
  );
}

function GeneralTab() {
  const { dryRun, toggleDry, autopilot, toggleAutopilot, currentWorkspace } = useApp();
  const row = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '13px 0', borderBottom: '1px solid #1a1f27' };
  const wsName = currentWorkspace ? `@${currentWorkspace.label}` : 'this workspace';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Card style={{ padding: '16px 18px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Service controls</span>
          <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>dry-run &amp; autopilot apply to {wsName} · saved without restart</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={row}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Dry-run</div>
              <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>simulate publishing — no live posts to X</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
              <span className="os-mono" style={{ fontSize: 11, fontWeight: 600, color: dryRun ? '#f5a524' : '#3ecf8e' }}>
                {dryRun ? 'ON · simulated' : 'OFF · live'}
              </span>
              <Toggle on={dryRun} onClick={toggleDry} onColor="#f5a524" />
            </div>
          </div>
          <div style={{ ...row, borderBottom: 'none', paddingBottom: 4 }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Autopilot</div>
              <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>posts every X minutes inside the window, set on the Schedule page (app_mode=auto)</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
              <span className="os-mono" style={{ fontSize: 11, fontWeight: 600, color: autopilot ? '#3ecf8e' : '#9aa3af' }}>
                {autopilot ? 'ON · unattended' : 'OFF · manual'}
              </span>
              <Toggle on={autopilot} onClick={toggleAutopilot} />
            </div>
          </div>
        </div>
      </Card>
      <VoiceSamplesCard />
    </div>
  );
}

function ActionsTab() {
  const { runCommand, commands } = useApp();
  const last = commands[0];
  return (
    <Card style={{ padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Service actions</span>
        <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>dispatched to the background worker · watch progress in the toast / logs</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {QUICK_ACTIONS.map(([type, label, desc], i) => {
          const Icon = ACTION_ICON[type] || Play;
          return (
            <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '13px 0', borderBottom: i < QUICK_ACTIONS.length - 1 ? '1px solid #1a1f27' : 'none' }}>
              <span style={{ display: 'inline-flex', width: 34, height: 34, borderRadius: 9, flexShrink: 0, alignItems: 'center', justifyContent: 'center', background: 'rgba(62,207,142,.1)', color: '#3ecf8e' }}>
                <Icon size={16} />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>
                <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>{desc}</div>
              </div>
              <button
                onClick={() => runCommand(type)}
                className="run-btn"
                style={{
                  flexShrink: 0, display: 'inline-flex', alignItems: 'center', gap: 7, border: 'none', background: '#3ecf8e',
                  color: '#0a0c0f', cursor: 'pointer', fontSize: 12, fontWeight: 700, padding: '8px 15px', borderRadius: 8,
                }}
              >
                <Icon size={13} /> Run
              </button>
            </div>
          );
        })}
      </div>
      {last && (
        <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 13 }}>
          last dispatched: <span style={{ color: '#9aa3af' }}>{last.type}</span> · {last.status}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

// Manage enrolled workspaces: per-workspace daily cap, switch, and delete.
function AccountsManager() {
  const { reloadAccounts, account, setAccount } = useApp();
  const { data: accounts, reload } = usePoll('/api/accounts', 15000);
  const [capEdits, setCapEdits] = useState({}); // id -> string

  const refresh = () => { reload(); reloadAccounts && reloadAccounts(); };

  const saveCap = (a) => {
    const raw = capEdits[a.id];
    if (raw === undefined) return;
    const trimmed = raw.trim();
    const body = trimmed === ''
      ? { clear_cap: true }
      : { daily_post_cap: Math.max(0, parseInt(trimmed, 10) || 0) };
    api.patch(`/api/accounts/${a.id}`, body)
      .then(() => { setCapEdits((c) => { const n = { ...c }; delete n[a.id]; return n; }); refresh(); })
      .catch((e) => alert(e.message));
  };

  const remove = (a) => {
    if (!confirm(`Delete workspace @${a.label}? This permanently removes its niches, drafts, history, and settings. The shared content pool and sources are kept.`)) return;
    api.del(`/api/accounts/${a.id}`).then(refresh).catch((e) => alert(e.message));
  };

  if (!accounts || accounts.length === 0) return null;

  return (
    <Card style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 11 }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: '#e7eaef' }}>Workspaces</span>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>click to switch · cap empty = unlimited</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {accounts.map((a) => {
          const editing = capEdits[a.id] !== undefined;
          const capVal = editing ? capEdits[a.id] : (a.daily_post_cap == null ? '' : String(a.daily_post_cap));
          const isCurrent = a.id === account;
          return (
            <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '10px 12px', background: isCurrent ? 'rgba(122,162,247,.08)' : '#0d1116', border: `1px solid ${isCurrent ? 'rgba(122,162,247,.4)' : '#1c212a'}`, borderRadius: 9 }}>
              <span
                onClick={() => setAccount(a.id)}
                title={isCurrent ? 'current workspace' : 'switch to this workspace'}
                style={{ width: 9, height: 9, borderRadius: '50%', flexShrink: 0, cursor: 'pointer', background: isCurrent ? '#7aa2f7' : '#3b414b' }}
              />
              <span onClick={() => setAccount(a.id)} style={{ fontSize: 12.5, fontWeight: 600, color: '#e7eaef', minWidth: 120, cursor: 'pointer' }}>
                @{a.label}{isCurrent ? ' ·' : ''}
                {isCurrent && <span className="os-mono" style={{ fontSize: 9, color: '#7aa2f7', marginLeft: 4 }}>current</span>}
              </span>
              <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>
                {a.niche_count} niche{a.niche_count === 1 ? '' : 's'} · {a.published_today} today
              </span>
              <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 7 }}>
                <span className="os-mono" style={{ fontSize: 10, color: '#7a828f' }}>cap</span>
                <input
                  value={capVal}
                  onChange={(e) => setCapEdits((c) => ({ ...c, [a.id]: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveCap(a); }}
                  placeholder="∞"
                  style={{ ...inputStyle, width: 70, textAlign: 'center', padding: '6px 8px' }}
                />
                {editing && (
                  <button
                    onClick={() => saveCap(a)}
                    style={{ border: 'none', background: '#3ecf8e', color: '#0a0c0f', cursor: 'pointer', fontSize: 10, fontWeight: 700, padding: '6px 11px', borderRadius: 6 }}
                  >
                    save
                  </button>
                )}
                <button
                  onClick={() => remove(a)}
                  className="hover-bright"
                  title="delete account"
                  style={{ border: '1px solid rgba(245,69,92,.3)', background: 'transparent', color: '#f5455c', cursor: 'pointer', fontSize: 10, fontWeight: 600, padding: '6px 10px', borderRadius: 6 }}
                >
                  delete
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

const X_FIELDS = ['api_key', 'api_secret', 'access_token', 'access_token_secret'];
const EMPTY_X = { api_key: '', api_secret: '', access_token: '', access_token_secret: '' };

// The current workspace's own X credentials (the workspace IS one X account).
// Enrolling/replacing writes only to this workspace — never a global list.
function WorkspaceXCard() {
  const { currentWorkspace, reloadAccounts } = useApp();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(EMPTY_X);
  const [busy, setBusy] = useState(false);

  if (!currentWorkspace) {
    return (
      <Card style={{ padding: '14px 16px' }}>
        <span className="os-mono" style={{ fontSize: 11, color: '#5b6470' }}>create a workspace first to enroll its X credentials</span>
      </Card>
    );
  }
  const configured = !!currentWorkspace.configured;

  const save = () => {
    if (busy || X_FIELDS.some((f) => !form[f].trim())) return;
    setBusy(true);
    api
      .post(`/api/accounts/${currentWorkspace.id}/credentials`, form)
      .then(() => { setEditing(false); setForm(EMPTY_X); reloadAccounts && reloadAccounts(); })
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <Card style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 11 }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, flexShrink: 0, background: '#7aa2f7' }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: '#e7eaef' }}>X account</span>
        <span className="os-mono" style={{ fontSize: 10, color: '#7aa2f7' }}>@{currentWorkspace.label}</span>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, fontWeight: 600, color: configured ? '#3ecf8e' : '#f5a524' }}>
          {configured ? 'configured · encrypted' : 'not enrolled'}
        </span>
      </div>
      {!editing ? (
        <button
          onClick={() => setEditing(true)}
          className="hover-bright"
          style={{
            cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 600,
            padding: '6px 12px', borderRadius: 6,
            border: `1px solid ${configured ? '#232932' : 'rgba(62,207,142,.35)'}`,
            background: configured ? 'transparent' : 'rgba(62,207,142,.1)', color: configured ? '#9aa3af' : '#3ecf8e',
          }}
        >
          {configured ? 'replace credentials' : 'enroll this workspace'}
        </button>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          {X_FIELDS.map((f) => (
            <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
              <span className="os-mono" style={{ fontSize: 11, color: '#7a828f', minWidth: 190 }}>{f}</span>
              <input
                type="password" value={form[f]} onChange={(e) => setForm({ ...form, [f]: e.target.value })}
                placeholder="write-only" style={{ ...inputStyle, flex: 1 }}
              />
            </div>
          ))}
          <div style={{ display: 'flex', gap: 7, marginTop: 4 }}>
            <button
              onClick={save}
              style={{ border: 'none', background: '#3ecf8e', color: '#0a0c0f', cursor: 'pointer', fontSize: 11, fontWeight: 700, padding: '8px 16px', borderRadius: 7 }}
            >
              {busy ? 'saving…' : 'save encrypted'}
            </button>
            <button
              onClick={() => { setEditing(false); setForm(EMPTY_X); }}
              style={{ border: '1px solid #232932', background: 'transparent', color: '#9aa3af', cursor: 'pointer', fontSize: 11, padding: '8px 13px', borderRadius: 7 }}
            >
              cancel
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

// AI provider keys for the current workspace (OpenAI / Anthropic). Per workspace
// so each can authenticate with its own account; never injected into the env.
const AI_KEYS = [['OPENAI_API_KEY', 'OpenAI'], ['ANTHROPIC_API_KEY', 'Anthropic']];

function WorkspaceAiKeysCard() {
  const { withWorkspace, account } = useApp();
  const { data, reload } = usePoll(withWorkspace('/api/ai'), 0);
  const keyStatus = (data && data.key_status) || {};

  return (
    <Card style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, flexShrink: 0, background: '#b48ef7' }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: '#e7eaef' }}>AI keys</span>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>per workspace · used by this workspace's generation</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
        {AI_KEYS.map(([env, label]) => (
          <div key={env} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <span style={{ minWidth: 0 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: '#cfd6df' }}>{label}</span>
              <span className="os-mono" style={{ fontSize: 9.5, color: '#5b6470', marginLeft: 8 }}>{env}</span>
            </span>
            <AiKeyField env={env} set={!!keyStatus[env]} onSaved={reload} account={account} />
          </div>
        ))}
      </div>
      <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginTop: 12, lineHeight: 1.5 }}>
        source API keys (YouTube, The Guardian) are shared across workspaces — set them on the Sources tab.
      </div>
    </Card>
  );
}

// Image-provider keys for the current workspace (Unsplash). Per workspace like
// the AI keys — read by niches whose Image source is Unsplash. Status comes from
// the same /api/ai key_status payload (which reports every per-workspace key).
const IMAGE_KEYS = [['UNSPLASH_ACCESS_KEY', 'Unsplash']];

function WorkspaceImageKeysCard() {
  const { withWorkspace, account } = useApp();
  const { data, reload } = usePoll(withWorkspace('/api/ai'), 0);
  const keyStatus = (data && data.key_status) || {};

  return (
    <Card style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, flexShrink: 0, background: '#3ecf8e' }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: '#e7eaef' }}>Image keys</span>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>per workspace · used for Unsplash photos</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
        {IMAGE_KEYS.map(([env, label]) => (
          <div key={env} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <span style={{ minWidth: 0 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: '#cfd6df' }}>{label}</span>
              <span className="os-mono" style={{ fontSize: 9.5, color: '#5b6470', marginLeft: 8 }}>{env}</span>
            </span>
            <AiKeyField env={env} set={!!keyStatus[env]} onSaved={reload} account={account} />
          </div>
        ))}
      </div>
      <div className="os-mono" style={{ fontSize: 9.5, color: '#4b5563', marginTop: 12, lineHeight: 1.5 }}>
        applies to niches with Image source = Unsplash (per-niche, Niches page → Content). Without a key those niches fall back to no image.
      </div>
    </Card>
  );
}

// Workspace-scoped: only the current workspace's own X + AI credentials. The
// list of all workspaces lives in the Global → Workspaces tab.
function CredentialsTab() {
  const { currentWorkspace } = useApp();
  const wsName = currentWorkspace ? `@${currentWorkspace.label}` : 'this workspace';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>API keys &amp; secrets</span>
        <span className="os-mono" style={{ fontSize: 10, color: '#3ecf8e' }}>encrypted at rest ✓</span>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>{wsName} only · write-only</span>
      </div>
      <WorkspaceXCard />
      <WorkspaceAiKeysCard />
      <WorkspaceImageKeysCard />
    </div>
  );
}

// Global: manage every workspace (switch, set daily cap, delete).
function WorkspacesTab() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Workspaces</span>
        <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#5b6470' }}>all workspaces · switch, cap &amp; delete</span>
      </div>
      <AccountsManager />
    </div>
  );
}

export default function Settings() {
  const { settingsTab, setSettingsTab } = useApp();
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '194px 1fr', gap: 20, alignItems: 'start' }}>
        {/* left tab rail — grouped by scope (workspace vs global) */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, background: '#0c0f13', border: '1px solid #1a1f27', borderRadius: 12, padding: 8 }}>
          {SETTINGS_GROUPS.map(([group, items], gi) => (
            <React.Fragment key={group}>
              <div className="os-mono" style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '.5px', color: '#4b5563', padding: gi === 0 ? '4px 10px 5px' : '12px 10px 5px' }}>
                {group.toUpperCase()}
              </div>
              {items.map(([k, label]) => {
                const active = settingsTab === k;
                const danger = DANGER_TABS.has(k);
                return (
                  <button
                    key={k}
                    onClick={() => setSettingsTab(k)}
                    className={`hover-tab${active ? ' active' : ''}`}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left', border: 'none',
                      cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 500, padding: '9px 12px', borderRadius: 8,
                      background: active ? (danger ? '#211519' : '#15201b') : 'transparent',
                      color: active ? (danger ? '#f5455c' : '#3ecf8e') : (danger ? '#8a6a70' : '#8b93a0'),
                    }}
                  >
                    <span style={{ width: 5, height: 5, borderRadius: '50%', flexShrink: 0, background: active ? (danger ? '#f5455c' : '#3ecf8e') : '#3b414b' }} />
                    {label}
                  </button>
                );
              })}
            </React.Fragment>
          ))}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {settingsTab === 'general' && <GeneralTab />}
          {settingsTab === 'actions' && <ActionsTab />}
          {settingsTab === 'ai' && <AiProvidersTab />}
          {settingsTab === 'credentials' && <CredentialsTab />}
          {settingsTab === 'reset-workspace' && <WorkspaceResetTab />}
          {settingsTab === 'workspaces' && <WorkspacesTab />}
          {settingsTab === 'reset-db' && <DatabaseResetTab />}
        </div>
      </div>
    </div>
  );
}
