import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, Check, Database, ExternalLink, Image as ImageIcon, KeyRound, Play, Plus,
  SlidersHorizontal, Sparkles, Trash2, Users, Zap, Download, CalendarDays,
} from 'lucide-react';
import { useApp } from '../AppContext';
import { QUICK_ACTIONS } from '../data';
import { api, usePoll } from '../api';
import { fmtTime, parseTime } from '../utils';
import {
  Button, C, Card, CardHeader, Loading, Modal, SaveButton, SettingRow, Tag, Toggle,
  fieldStyle, textareaStyle,
} from '../components/common';
import { CreateWorkspaceModal } from '../components/WorkspaceMenu';

// Settings, in five tabs:
//   Controls     every runtime knob (publishing, limits, memory, voice)
//   Actions      run worker jobs now, plus the resets
//   AI provider  pick, configure and test the model that writes drafts
//   Credentials  every key the app holds (X, AI providers, images, sources)
//   Workspaces   one workspace per X account: rename, cap, switch, delete
const TABS = [
  ['general', 'Controls', SlidersHorizontal],
  ['actions', 'Actions', Zap],
  ['ai', 'AI provider', Sparkles],
  ['credentials', 'Credentials', KeyRound],
  ['workspaces', 'Workspaces', Users],
];
// Tabs from before the redesign that now live inside "Actions".
const TAB_ALIASES = { 'reset-workspace': 'actions', 'reset-db': 'actions' };

const ACTION_ICON = { fetch_sources: Download, generate_posts: Sparkles, run_slots: Play };

// Scope chip: does this setting apply to the current workspace or everything?
function Scope({ global }) {
  const { currentWorkspace } = useApp();
  return global
    ? <Tag fg="#b48ef7" bg="rgba(180,142,247,.12)">all workspaces</Tag>
    : <Tag fg={C.blue} bg="rgba(122,162,247,.12)">@{currentWorkspace ? currentWorkspace.label : 'workspace'}</Tag>;
}

function TabIntro({ title, children }) {
  return (
    <div style={{ marginBottom: 2 }}>
      <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: '-.2px' }}>{title}</div>
      <div className="os-mono" style={{ fontSize: 10.5, color: C.faint, marginTop: 4, lineHeight: 1.6 }}>{children}</div>
    </div>
  );
}

// A number field that saves on Enter or with its own Save button.
// ``blank`` lets an empty value mean "none" (sent as null).
function NumberField({ value, onSave, min, max, unit, blank, placeholder, width = 76 }) {
  const shown = value == null ? '' : String(value);
  const [draft, setDraft] = useState(shown);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setDraft(shown); }, [shown]);
  const trimmed = draft.trim();
  const n = parseInt(trimmed, 10);
  const valid = (blank && trimmed === '') || (Number.isFinite(n) && n >= min && n <= max);
  const dirty = trimmed !== shown && valid;
  const save = () => {
    if (!dirty || busy) return;
    setBusy(true);
    Promise.resolve(onSave(trimmed === '' ? null : n))
      .catch((e) => { alert(e.message); setDraft(shown); })
      .finally(() => setBusy(false));
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <input
        type="number" min={min} max={max} value={draft} placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setDraft(shown); }}
        style={{ ...fieldStyle, width, textAlign: 'center', fontWeight: 600, borderColor: valid ? '#1c212a' : 'rgba(245,69,92,.5)' }}
      />
      {unit && <span className="os-mono" style={{ fontSize: 10.5, color: C.dim, minWidth: 34 }}>{unit}</span>}
      <Button size="sm" variant="primary" onClick={save} disabled={!dirty || busy} style={{ visibility: dirty ? 'visible' : 'hidden' }}>
        {busy ? '…' : 'Save'}
      </Button>
    </span>
  );
}

// ===========================================================================
// Credential field: write-only, shows set / not set, replace and remove
// ===========================================================================
function KeyField({ env, set, account, onChange, required = true, keyUrl }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const q = account ? `&account=${encodeURIComponent(account)}` : '';

  const save = () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    setNote(null);
    api.post(`/api/credentials${account ? `?account=${encodeURIComponent(account)}` : ''}`, { key: env, value: value.trim() })
      .then(() => { setValue(''); setEditing(false); onChange && onChange(); })
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };
  const remove = () => {
    if (!confirm(`Remove the stored ${env}?`)) return;
    setBusy(true);
    api.del(`/api/credentials?key=${encodeURIComponent(env)}${q}`)
      .then((r) => {
        if (r.set) setNote(r.removed ? 'still set by an environment variable' : 'set by an environment variable; nothing stored here');
        onChange && onChange();
      })
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };

  if (editing) {
    return (
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
        <input
          autoFocus type="password" autoComplete="new-password" value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false); }}
          placeholder={`paste ${env}`}
          style={{ ...fieldStyle, flex: 1, minWidth: 140 }}
        />
        <Button size="sm" variant="primary" onClick={save} disabled={!value.trim() || busy}>{busy ? 'Saving…' : 'Save'}</Button>
        <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setValue(''); }}>Cancel</Button>
      </span>
    );
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
      {note && <span className="os-mono" style={{ fontSize: 9.5, color: C.faint }}>{note}</span>}
      {set
        ? <Tag fg={C.green} bg="rgba(62,207,142,.12)"><Check size={10} strokeWidth={3} />set</Tag>
        : required
          ? <Tag fg={C.amber} bg="rgba(245,165,36,.12)">not set</Tag>
          : <Tag>optional</Tag>}
      {keyUrl && !set && (
        <a href={keyUrl} target="_blank" rel="noreferrer" className="hover-bright" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600, color: C.blue, textDecoration: 'none' }}>
          Get a key <ExternalLink size={10} />
        </a>
      )}
      <Button size="sm" variant={set ? 'secondary' : 'soft'} onClick={() => setEditing(true)}>{set ? 'Replace' : 'Set key'}</Button>
      {set && <Button size="sm" variant="ghost" onClick={remove} disabled={busy} title="remove stored key" icon={<Trash2 size={12} />} />}
    </span>
  );
}

// One credential row: service name + env var on the left, the field on the right.
function KeyRow({ label, env, extra, last, ...field }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 0', borderBottom: last ? 'none' : `1px solid ${C.line}`, flexWrap: 'wrap' }}>
      <div style={{ minWidth: 180, flex: '0 0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
          {extra}
        </div>
        <div className="os-mono" style={{ fontSize: 9.5, color: C.faint, marginTop: 2 }}>{env}</div>
      </div>
      <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', minWidth: 0 }}>
        <KeyField env={env} {...field} />
      </div>
    </div>
  );
}

// ===========================================================================
// Controls
// ===========================================================================
function PublishingCard() {
  const { dryRun, toggleDry, autopilot, toggleAutopilot, currentWorkspace, setSettingsTab } = useApp();
  const live = !dryRun;
  const noCreds = currentWorkspace && !currentWorkspace.configured;
  return (
    <Card style={{ padding: '16px 18px 4px' }}>
      <CardHeader title="Publishing" right={<Scope />} style={{ marginBottom: 2 }} />
      <SettingRow title="Dry-run" hint="simulate publishing: drafts are marked posted, nothing goes to X">
        <span className="os-mono" style={{ fontSize: 10.5, fontWeight: 600, color: dryRun ? C.amber : C.green }}>{dryRun ? 'simulated' : 'live'}</span>
        <Toggle on={dryRun} onClick={toggleDry} onColor={C.amber} />
      </SettingRow>
      <SettingRow title="Autopilot" hint="at each posting mark, also publish the best fresh draft · off: drafts wait in the Queue" last={!(live && noCreds)}>
        <span className="os-mono" style={{ fontSize: 10.5, fontWeight: 600, color: autopilot ? C.green : C.dim }}>{autopilot ? 'unattended' : 'manual'}</span>
        <Toggle on={autopilot} onClick={toggleAutopilot} />
      </SettingRow>
      {live && noCreds && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '12px 0 14px', padding: '10px 12px', borderRadius: 9, background: 'rgba(245,165,36,.08)', border: '1px solid rgba(245,165,36,.25)' }}>
          <AlertTriangle size={15} color={C.amber} style={{ flexShrink: 0 }} />
          <span style={{ fontSize: 12, color: '#e7c98a', flex: 1 }}>Live mode, but this workspace has no X credentials. Posts are held until you add them.</span>
          <Button size="sm" variant="secondary" onClick={() => setSettingsTab('credentials')}>Add credentials</Button>
        </div>
      )}
    </Card>
  );
}

function LimitsCard() {
  const { status, reloadStatus, currentWorkspace, reloadAccounts } = useApp();
  const patchSettings = (body) => api.patch('/api/settings', body).then(reloadStatus);
  const saveWsCap = (v) =>
    api.patch(`/api/accounts/${currentWorkspace.id}`, v == null ? { clear_cap: true } : { daily_post_cap: v })
      .then(() => reloadAccounts());
  return (
    <Card style={{ padding: '16px 18px 4px' }}>
      <CardHeader title="Limits & memory" style={{ marginBottom: 2 }} />
      {!status ? <Loading /> : (
        <>
          <SettingRow title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>Daily post cap <Scope /></span>} hint="most posts this account publishes per day · blank = no cap of its own">
            <NumberField
              value={currentWorkspace ? currentWorkspace.daily_post_cap : null}
              onSave={saveWsCap} min={0} max={1000} unit="posts" blank placeholder="∞"
            />
          </SettingRow>
          <SettingRow title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>Global daily cap <Scope global /></span>} hint="backstop across every workspace · 0 = none">
            <NumberField value={status.global_daily_cap} onSave={(v) => patchSettings({ global_daily_cap: v })} min={0} max={1000} unit="posts" />
          </SettingRow>
          <SettingRow title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>Publish attempts <Scope global /></span>} hint="retries before a draft that keeps failing is marked failed">
            <NumberField value={status.max_post_attempts} onSave={(v) => patchSettings({ max_post_attempts: v })} min={1} max={10} unit="tries" />
          </SettingRow>
          <SettingRow title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>Story memory <Scope global /></span>} hint="unused stories are forgotten after this long, keeping the knowledge base small" last>
            <NumberField value={status.story_ttl_hours} onSave={(v) => patchSettings({ story_ttl_hours: v })} min={1} max={720} unit="hours" />
          </SettingRow>
        </>
      )}
    </Card>
  );
}

function ScheduleSummaryCard() {
  const { withWorkspace, setRoute } = useApp();
  const { data: sched } = usePoll(withWorkspace('/api/schedule'), 0);
  const c = sched && sched.cadence;
  const items = c ? [
    ['window', `${c.window[0]}–${c.window[1]}`],
    ['every', `~${c.every_minutes} min`],
    ['randomness', `±${c.jitter_pct}%`],
    ['min gap', `${c.min_gap_minutes} min`],
    ['timezone', sched.timezone ? sched.timezone.replace(/_/g, ' ') : 'server local'],
  ] : [];
  const span = c ? (parseTime(c.window[1]) - parseTime(c.window[0])) : 0;
  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader
        title="Schedule"
        sub={c && span > 0 ? `≈ ${Math.max(1, Math.round(span / c.every_minutes))} marks a day · next ${sched.next_post ? fmtTime(sched.next_post.minute) : '—'}` : null}
        right={<><Scope /><Button size="sm" icon={<CalendarDays size={12} />} onClick={() => setRoute('schedule')}>Edit schedule</Button></>}
      />
      {!c ? <Loading /> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
          {items.map(([k, v]) => (
            <div key={k} style={{ background: C.inset, border: '1px solid #171c24', borderRadius: 9, padding: '9px 12px' }}>
              <div className="os-mono" style={{ fontSize: 9, color: C.faint, textTransform: 'uppercase', letterSpacing: '.5px' }}>{k}</div>
              <div className="os-mono" style={{ fontSize: 12.5, fontWeight: 600, color: C.text, marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{v}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// Voice samples: real posts the writer imitates (rhythm, casing, line breaks),
// per workspace, since one X account has one voice. A niche can override them.
function VoiceSamplesCard() {
  const { withAccount } = useApp();
  const [saved, setSaved] = useState(null); // { samples, default }
  const [draft, setDraft] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get(withAccount('/api/voice')).then((v) => { setSaved(v); setDraft(v.samples); }).catch(() => {});
  }, [withAccount]);
  useEffect(load, [load]);

  const clean = draft.map((s) => s.trim()).filter(Boolean);
  const dirty = !!saved && JSON.stringify(clean) !== JSON.stringify(saved.samples);

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

  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader
        title="Voice samples"
        sub={saved ? (saved.default ? 'using the built-in defaults' : `${saved.samples.length} custom`) : null}
        right={<Scope />}
        style={{ marginBottom: 6 }}
      />
      <div className="os-mono" style={{ fontSize: 10, color: C.faint, marginBottom: 12, lineHeight: 1.6 }}>
        Real posts this account sounds like. The writer copies their style (short paragraphs, casing, line breaks),
        never their topics. Keep 3 to 5. A niche can override them on the Niches page.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {draft.map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <span className="os-mono" style={{ fontSize: 10, color: C.faint, width: 14, paddingTop: 11, textAlign: 'right', flexShrink: 0 }}>{i + 1}</span>
            <textarea value={s} onChange={(e) => setAt(i, e.target.value)} rows={3} style={textareaStyle} />
            <Button size="sm" variant="ghost" title="remove sample" onClick={() => setDraft((d) => d.filter((_, j) => j !== i))} icon={<Trash2 size={12} />} style={{ marginTop: 5 }} />
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
        {draft.length < 8 && (
          <Button size="sm" icon={<Plus size={12} />} onClick={() => setDraft((d) => [...d, ''])}>Add sample</Button>
        )}
        <span style={{ flex: 1 }} />
        {saved && !saved.default && <Button onClick={reset} disabled={busy}>Use defaults</Button>}
        <SaveButton dirty={dirty} saving={busy} onClick={save} label="Save samples" />
      </div>
    </Card>
  );
}

function ControlsTab() {
  return (
    <>
      <TabIntro title="Controls">Everything that decides what the app does, saved instantly without a restart.</TabIntro>
      <PublishingCard />
      <LimitsCard />
      <ScheduleSummaryCard />
      <VoiceSamplesCard />
    </>
  );
}

// ===========================================================================
// Actions (run now + resets)
// ===========================================================================
function ResetDatabaseModal({ onClose }) {
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
      setDone(Object.values(r.deleted || {}).reduce((a, b) => a + b, 0));
      setTimeout(() => window.location.reload(), 1400);
    } catch (e) {
      alert(e.message);
      setBusy(false);
    }
  };

  return (
    <Modal onClose={onClose} tone="danger" icon={<AlertTriangle size={18} />} title="Reset database" sub="every workspace · niche config files are kept">
      {done == null ? (
        <>
          <div style={{ marginTop: 18, fontSize: 12.5, color: '#aeb6c0', lineHeight: 1.6 }}>
            This wipes every fetched item, story, draft, publish record, log line and source status for
            <strong style={{ color: C.textSoft }}> all workspaces</strong>. Niche config files are kept. This cannot be undone.
          </div>
          <label
            onClick={() => setClearCreds((v) => !v)}
            style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 16, padding: '11px 13px', background: C.inset, border: `1px solid ${clearCreds ? 'rgba(245,69,92,.4)' : '#1c212a'}`, borderRadius: 10, cursor: 'pointer' }}
          >
            <span style={{ display: 'inline-flex', width: 18, height: 18, borderRadius: 5, alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: `1.5px solid ${clearCreds ? C.red : '#3b414b'}`, background: clearCreds ? C.red : 'transparent', color: '#0a0c0f' }}>
              {clearCreds && <Check size={13} strokeWidth={3} />}
            </span>
            <div>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>Also clear credentials</div>
              <div className="os-mono" style={{ fontSize: 10, color: C.faint, marginTop: 2 }}>API keys and X accounts</div>
            </div>
          </label>
          <div style={{ marginTop: 16 }}>
            <div className="os-mono" style={{ fontSize: 10, color: C.dim, marginBottom: 7 }}>
              type <span style={{ color: C.red, fontWeight: 700 }}>RESET</span> to confirm
            </div>
            <input autoFocus value={phrase} onChange={(e) => setPhrase(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') run(); }} placeholder="RESET" style={{ ...fieldStyle, width: '100%' }} />
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
            <Button onClick={onClose} style={{ flex: 1, padding: 10 }}>Cancel</Button>
            <Button variant="dangerSolid" onClick={run} disabled={!armed || busy} style={{ flex: 1.4, padding: 10 }}>
              {busy ? 'Resetting…' : clearCreds ? 'Reset everything' : 'Reset database'}
            </Button>
          </div>
        </>
      ) : <ResetDone count={done} />}
    </Modal>
  );
}

function ResetWorkspaceModal({ workspace, onClose }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const run = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.post(`/api/accounts/${workspace.id}/reset`);
      setDone(Object.values(r.deleted || {}).reduce((a, b) => a + b, 0));
      setTimeout(() => window.location.reload(), 1300);
    } catch (e) {
      alert(e.message);
      setBusy(false);
    }
  };
  return (
    <Modal onClose={onClose} tone="danger" icon={<Trash2 size={18} />} title={`Reset @${workspace.label}`} sub="this workspace only">
      {done == null ? (
        <>
          <div style={{ marginTop: 18, fontSize: 12.5, color: '#aeb6c0', lineHeight: 1.6 }}>
            Removes <strong style={{ color: C.textSoft }}>@{workspace.label}</strong>'s drafts, publish history and settings
            (dry-run, autopilot, AI, selected niches go back to defaults). Niche files, the shared content pool and other
            workspaces are kept. This cannot be undone.
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
            <Button onClick={onClose} style={{ flex: 1, padding: 10 }}>Cancel</Button>
            <Button variant="dangerSolid" onClick={run} disabled={busy} style={{ flex: 1.4, padding: 10 }}>{busy ? 'Resetting…' : 'Reset workspace'}</Button>
          </div>
        </>
      ) : <ResetDone count={done} />}
    </Modal>
  );
}

function ResetDone({ count }) {
  return (
    <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', gap: 10, color: C.green }}>
      <Check size={18} strokeWidth={3} />
      <span style={{ fontSize: 13, fontWeight: 600 }}>Cleared {count} row{count === 1 ? '' : 's'}. Reloading…</span>
    </div>
  );
}

function ActionRow({ icon, title, hint, children, last }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '13px 0', borderBottom: last ? 'none' : `1px solid ${C.line}` }}>
      {icon}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
        <div className="os-mono" style={{ fontSize: 10, color: C.faint, marginTop: 3, lineHeight: 1.5 }}>{hint}</div>
      </div>
      {children}
    </div>
  );
}

const iconBox = (Icon, fg, bg) => (
  <span style={{ display: 'inline-flex', width: 34, height: 34, borderRadius: 9, flexShrink: 0, alignItems: 'center', justifyContent: 'center', background: bg, color: fg }}>
    <Icon size={16} />
  </span>
);

function ActionsTab() {
  const { runCommand, commands, currentWorkspace } = useApp();
  const [resetWs, setResetWs] = useState(false);
  const [resetDb, setResetDb] = useState(false);
  const lastOf = (type) => commands.find((c) => c.type === type);

  return (
    <>
      <TabIntro title="Actions">Run a worker job now instead of waiting for the next mark. Progress shows in the corner and in Logs.</TabIntro>
      <Card style={{ padding: '16px 18px 4px' }}>
        <CardHeader title="Run now" right={<Scope />} style={{ marginBottom: 2 }} />
        {QUICK_ACTIONS.map(([type, label, desc], i) => {
          const last = lastOf(type);
          const busy = last && (last.status === 'pending' || last.status === 'running');
          return (
            <ActionRow key={type} icon={iconBox(ACTION_ICON[type] || Play, C.green, 'rgba(62,207,142,.1)')} title={label} hint={desc} last={i === QUICK_ACTIONS.length - 1}>
              {last && !busy && (
                <span className="os-mono" style={{ fontSize: 10, color: last.status === 'failed' ? C.red : C.faint }}>last: {last.status}</span>
              )}
              <Button variant="primary" onClick={() => runCommand(type)} disabled={busy} icon={<Play size={12} />}>
                {busy ? 'Running…' : 'Run'}
              </Button>
            </ActionRow>
          );
        })}
      </Card>

      <Card style={{ padding: '16px 18px 4px', borderColor: '#2a1c20' }}>
        <CardHeader title={<span style={{ color: C.red }}>Danger zone</span>} sub="cannot be undone" style={{ marginBottom: 2 }} />
        <ActionRow
          icon={iconBox(Trash2, C.amber, 'rgba(245,165,36,.1)')}
          title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>Reset workspace <Scope /></span>}
          hint="clear this workspace's drafts, history and settings · other workspaces untouched"
        >
          <Button variant="danger" onClick={() => setResetWs(true)} disabled={!currentWorkspace}>Reset…</Button>
        </ActionRow>
        <ActionRow
          icon={iconBox(Database, C.red, 'rgba(245,69,92,.1)')}
          title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>Reset database <Scope global /></span>}
          hint="wipe all content, drafts, history and logs · optionally clear credentials too"
          last
        >
          <Button variant="danger" onClick={() => setResetDb(true)}>Reset…</Button>
        </ActionRow>
      </Card>

      {resetWs && currentWorkspace && <ResetWorkspaceModal workspace={currentWorkspace} onClose={() => setResetWs(false)} />}
      {resetDb && <ResetDatabaseModal onClose={() => setResetDb(false)} />}
    </>
  );
}

// ===========================================================================
// AI provider
// ===========================================================================
function ProviderTile({ p, selected, active, keySet, onClick }) {
  const keyless = !p.key_env;
  const keyState = keyless ? ['no key', C.faint]
    : keySet ? ['key set', C.green]
      : p.key_required ? ['needs key', C.amber] : ['key optional', C.faint];
  return (
    <button
      type="button"
      onClick={onClick}
      className={selected ? undefined : 'hover-card'}
      style={{
        position: 'relative', textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit',
        padding: '12px 13px', borderRadius: 10, minHeight: 78, display: 'flex', flexDirection: 'column',
        background: selected ? 'rgba(62,207,142,.07)' : C.inset,
        border: `1px solid ${selected ? 'rgba(62,207,142,.5)' : '#1c212a'}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{p.label}</span>
        {active && <Tag fg={C.green} bg="rgba(62,207,142,.14)" style={{ marginLeft: 'auto' }}>in use</Tag>}
      </div>
      <div style={{ fontSize: 11, color: C.dim, marginTop: 3, lineHeight: 1.4 }}>{p.tagline}</div>
      <div className="os-mono" style={{ marginTop: 'auto', paddingTop: 8, fontSize: 9.5, color: keyState[1] }}>{keyState[0]}</div>
    </button>
  );
}

function Field({ label, hint, children, right }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 7 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</span>
        {hint && <span className="os-mono" style={{ fontSize: 9.5, color: C.faint }}>{hint}</span>}
        {right && <span style={{ marginLeft: 'auto' }}>{right}</span>}
      </div>
      {children}
    </div>
  );
}

function SuggestionChips({ items, value, onPick }) {
  if (!items || !items.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
      {items.map((m) => {
        const on = m === value;
        return (
          <button
            key={m}
            type="button"
            onClick={() => onPick(m)}
            className={on ? undefined : 'hover-border-text'}
            style={{
              cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace", fontSize: 10.5, padding: '3px 9px', borderRadius: 999,
              border: `1px solid ${on ? 'rgba(62,207,142,.45)' : '#232932'}`,
              background: on ? 'rgba(62,207,142,.1)' : 'transparent', color: on ? C.green : C.muted,
            }}
          >
            {m}
          </button>
        );
      })}
    </div>
  );
}

// Mirrors ai/text.clean_field: an account id pasted as a URL becomes the id.
const cleanField = (key, v) => {
  let out = String(v || '').trim();
  if (key === 'account_id' && out.includes('/accounts/')) out = out.split('/accounts/')[1].split('/')[0];
  return out;
};
// Mirrors ai/text.resolve_endpoint for the address preview under the fields.
const templateEndpoint = (spec, text) => {
  if (!spec.endpoint_template) return null;
  const vals = Object.fromEntries((spec.fields || []).map((f) => [f.key, cleanField(f.key, text[f.key])]));
  if (Object.values(vals).some((v) => !v)) return null;
  return spec.endpoint_template.replace(/\{(\w+)\}/g, (_, k) => vals[k] || '');
};

function AiTab() {
  const { withWorkspace, account } = useApp();
  const { data: providers } = usePoll('/api/ai/providers', 0);
  const { data: server, reload } = usePoll(withWorkspace('/api/ai'), 0);
  const [edit, setEdit] = useState(null); // unsaved text config
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // a workspace switch drops unsaved edits
  useEffect(() => { setEdit(null); setResult(null); }, [account]);

  const saved = server ? server.text : null;
  const text = edit || saved || {};
  const keyStatus = (server && server.key_status) || {};
  const byId = useMemo(() => Object.fromEntries((providers || []).map((p) => [p.id, p])), [providers]);
  const pid = text.provider === 'openai' ? 'chatgpt' : text.provider === 'anthropic' ? 'claude' : (text.provider || 'local');
  const spec = byId[pid];
  const dirty = !!edit && JSON.stringify(edit) !== JSON.stringify(saved);

  useEffect(() => { setResult(null); }, [text.provider, text.model, text.endpoint]);

  const set = (k, v) => setEdit({ ...text, [k]: v });
  const choose = (p) => {
    if (p.id === pid) return;
    // back to the saved provider: restore its saved values; else its defaults
    if (saved && saved.provider === p.id) { setEdit(null); return; }
    setEdit({
      ...text,
      provider: p.id,
      model: p.default_model || '',
      endpoint: p.default_endpoint || '',
    });
    setShowAdvanced(false);
  };

  const endpointMissing = !!(spec && spec.endpoint_required && !(text.endpoint || '').trim());
  const modelMissing = !!(spec && spec.model_required && !(text.model || '').trim());
  const fieldsMissing = ((spec && spec.fields) || []).filter((f) => f.required && !cleanField(f.key, text[f.key])).map((f) => f.label);
  const blocked = endpointMissing || modelMissing || fieldsMissing.length > 0;
  const keyMissing = !!(spec && spec.key_env && spec.key_required && !keyStatus[spec.key_env]);

  const save = () => {
    if (!dirty || saving || blocked) return;
    setSaving(true);
    api.put(withWorkspace('/api/ai'), { text })
      .then(() => { setEdit(null); reload(); })
      .catch((e) => alert(e.message))
      .finally(() => setSaving(false));
  };
  const test = () => {
    if (testing || blocked) return;
    setTesting(true);
    setResult(null);
    api.post(withWorkspace('/api/ai/test'), { text })
      .then(setResult)
      .catch((e) => setResult({ ok: false, error: e.message }))
      .finally(() => setTesting(false));
  };

  if (!providers || !server) return (<><TabIntro title="AI provider">The model that writes this account's drafts.</TabIntro><Card style={{ padding: 18 }}><Loading /></Card></>);

  const isTemplate = pid === 'template';
  const isLocal = pid === 'local';
  const needsEndpoint = !!(spec && spec.endpoint_required);

  return (
    <>
      <TabIntro title="AI provider">
        The model that writes this account's drafts. A post costs at most two short calls, so a small fast model is usually plenty.
      </TabIntro>

      <Card style={{ padding: '16px 18px' }}>
        <CardHeader title="Choose a provider" right={<Scope />} />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(168px, 1fr))', gap: 10 }}>
          {providers.map((p) => (
            <ProviderTile
              key={p.id} p={p} selected={p.id === pid} active={saved && saved.provider === p.id}
              keySet={p.key_env ? !!keyStatus[p.key_env] : false} onClick={() => choose(p)}
            />
          ))}
        </div>
      </Card>

      {spec && (
        <Card style={{ padding: '16px 18px' }}>
          <CardHeader
            title={`Configure ${spec.label}`}
            sub={dirty ? 'unsaved changes' : saved && saved.provider === pid ? 'in use' : 'not saved yet'}
          />

          {isTemplate ? (
            <div className="os-mono" style={{ fontSize: 11, color: C.dim, lineHeight: 1.6 }}>
              A deterministic offline writer: it turns the story title into a post with no model, no key and no network.
              Useful for testing the pipeline; drafts will read flat.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              {isLocal && (
                <Field label="Server" hint="pick yours to fill the endpoint and model">
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {(spec.presets || []).map((pr) => {
                      const on = (text.endpoint || '') === pr.endpoint;
                      return (
                        <Button key={pr.label} size="sm" variant={on ? 'soft' : 'secondary'} onClick={() => setEdit({ ...text, endpoint: pr.endpoint, model: pr.model })}>
                          {pr.label}
                        </Button>
                      );
                    })}
                  </div>
                </Field>
              )}

              {needsEndpoint && (
                <Field label="Endpoint" hint={isLocal ? "required · the server's base URL (OpenAI-compatible servers usually end in /v1)" : "required · the API's base URL, usually ending in /v1"}>
                  <input
                    value={text.endpoint || ''} onChange={(e) => set('endpoint', e.target.value)}
                    placeholder={spec.endpoint_placeholder || spec.default_endpoint}
                    style={{ ...fieldStyle, width: '100%', borderColor: endpointMissing ? 'rgba(245,69,92,.5)' : '#1c212a' }}
                  />
                </Field>
              )}

              {(spec.fields || []).map((f) => {
                const missing = fieldsMissing.includes(f.label);
                const built = templateEndpoint(spec, text);
                return (
                  <Field key={f.key} label={f.label} hint={`${f.required ? 'required' : 'optional'} · ${f.hint}`}>
                    <input
                      value={text[f.key] || ''} onChange={(e) => set(f.key, e.target.value)}
                      placeholder={f.placeholder}
                      style={{ ...fieldStyle, width: '100%', borderColor: missing ? 'rgba(245,69,92,.5)' : '#1c212a' }}
                    />
                    {built && !(text.endpoint || '').trim() && (
                      <div className="os-mono" style={{ fontSize: 9.5, color: C.faint, marginTop: 6, wordBreak: 'break-all' }}>calls {built}</div>
                    )}
                  </Field>
                );
              })}

              <Field
                label="Model"
                hint={isLocal ? 'ollama/<name> for Ollama, openai/<name> for OpenAI-compatible servers'
                  : spec.model_required ? 'required · the id exactly as your endpoint lists it'
                    : 'any model id the provider offers · the prefix is added for you'}
              >
                <input
                  value={text.model || ''} onChange={(e) => set('model', e.target.value)}
                  placeholder={spec.model_placeholder || spec.default_model} list={`models-${pid}`}
                  style={{ ...fieldStyle, width: '100%', borderColor: modelMissing ? 'rgba(245,69,92,.5)' : '#1c212a' }}
                />
                <datalist id={`models-${pid}`}>{(spec.models || []).map((m) => <option key={m} value={m} />)}</datalist>
                <SuggestionChips items={spec.models} value={text.model} onPick={(m) => set('model', m)} />
              </Field>

              {spec.key_env && (
                <Field label="API key" hint={spec.key_required ? `${spec.key_env} · stored encrypted for this workspace` : `${spec.key_env} · optional, if your ${isLocal ? 'server' : 'endpoint'} checks one`}>
                  <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                    <KeyField env={spec.key_env} set={!!keyStatus[spec.key_env]} account={account} onChange={reload} required={spec.key_required} keyUrl={spec.key_url} />
                  </div>
                </Field>
              )}

              <Field label="Creativity" hint="temperature · lower is steadier, higher is looser" right={<span className="os-mono" style={{ fontSize: 12, fontWeight: 700 }}>{Number(text.temperature ?? 0.7).toFixed(2)}</span>}>
                <input type="range" className="os-range" min={0} max={1.5} step={0.05} value={text.temperature ?? 0.7} onChange={(e) => set('temperature', +e.target.value)} />
              </Field>

              {!needsEndpoint && (
                <div>
                  <Button size="sm" variant="ghost" onClick={() => setShowAdvanced((v) => !v)}>
                    {showAdvanced || text.endpoint ? 'Advanced' : 'Advanced…'}
                  </Button>
                  {(showAdvanced || text.endpoint) && (
                    <div style={{ marginTop: 10 }}>
                      <Field label="Custom base URL" hint="optional · a proxy or gateway in front of the provider">
                        <input value={text.endpoint || ''} onChange={(e) => set('endpoint', e.target.value)} placeholder={spec.endpoint_template ? 'leave blank to use the address built above' : "leave blank for the provider's own API"} style={{ ...fieldStyle, width: '100%' }} />
                      </Field>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* test + save */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 18, paddingTop: 14, borderTop: `1px solid ${C.line}`, flexWrap: 'wrap' }}>
            {!isTemplate && (
              <Button variant="soft" onClick={test} disabled={testing || blocked || keyMissing} icon={<Play size={12} />}
                title={keyMissing ? 'set the API key first' : fieldsMissing.length ? `set the ${fieldsMissing.join(', ')} first` : endpointMissing ? 'set the endpoint first' : modelMissing ? 'set the model first' : 'send a tiny prompt with the values above'}>
                {testing ? 'Testing…' : 'Test connection'}
              </Button>
            )}
            <span className="os-mono" style={{ flex: 1, minWidth: 200, fontSize: 10.5, lineHeight: 1.5, wordBreak: 'break-word', color: result ? (result.ok ? C.green : C.red) : C.faint }}>
              {result
                ? (result.ok
                  ? <>connected · {result.model} · {result.latency_ms} ms{result.reply ? <span style={{ color: C.dim }}> · replied “{result.reply}”</span> : null}</>
                  : <>✗ {result.error}</>)
                : keyMissing ? `set the ${spec.key_env} key to test and use ${spec.label}`
                  : fieldsMissing.length ? `add the ${fieldsMissing.join(', ')} to continue`
                  : endpointMissing ? 'add the endpoint to continue'
                    : modelMissing ? 'add the model id to continue'
                    : isTemplate ? '' : 'tests the values above, saved or not'}
            </span>
            {dirty && <Button onClick={() => setEdit(null)}>Discard</Button>}
            <SaveButton dirty={dirty} saving={saving} onClick={save} disabled={blocked} label={saved && saved.provider !== pid ? `Use ${spec.label}` : 'Save changes'} />
          </div>
        </Card>
      )}

      <div className="os-mono" style={{ fontSize: 10, color: C.faint, lineHeight: 1.6 }}>
        Post images are chosen per niche (Niches → Content → Image source); the Unsplash key is under Credentials.
      </div>
    </>
  );
}

// ===========================================================================
// Credentials
// ===========================================================================
const X_FIELDS = [['api_key', 'API key'], ['api_secret', 'API secret'], ['access_token', 'Access token'], ['access_token_secret', 'Access token secret']];
const EMPTY_X = { api_key: '', api_secret: '', access_token: '', access_token_secret: '' };

function XAccountCard() {
  const { currentWorkspace, reloadAccounts } = useApp();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(EMPTY_X);
  const [busy, setBusy] = useState(false);
  const configured = !!(currentWorkspace && currentWorkspace.configured);
  const complete = X_FIELDS.every(([f]) => form[f].trim());

  const save = () => {
    if (busy || !complete || !currentWorkspace) return;
    setBusy(true);
    api.post(`/api/accounts/${currentWorkspace.id}/credentials`, form)
      .then(() => { setEditing(false); setForm(EMPTY_X); reloadAccounts && reloadAccounts(); })
      .catch((e) => alert(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <Card style={{ padding: '16px 18px' }}>
      <CardHeader
        title="X account"
        sub="the account this workspace posts as"
        right={<>
          {configured ? <Tag fg={C.green} bg="rgba(62,207,142,.12)"><Check size={10} strokeWidth={3} />enrolled</Tag> : <Tag fg={C.amber} bg="rgba(245,165,36,.12)">not enrolled</Tag>}
          <Scope />
        </>}
        style={{ marginBottom: editing ? 14 : 8 }}
      />
      {!currentWorkspace ? (
        <span className="os-mono" style={{ fontSize: 11, color: C.faint }}>create a workspace first</span>
      ) : !editing ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="os-mono" style={{ flex: 1, fontSize: 10, color: C.faint, lineHeight: 1.5 }}>
            {configured
              ? 'four OAuth 1.0a keys stored encrypted · replacing overwrites all four'
              : 'from developer.x.com: your app’s API key and secret, plus an access token and secret with read and write permission'}
          </span>
          <Button variant={configured ? 'secondary' : 'soft'} onClick={() => setEditing(true)}>{configured ? 'Replace' : 'Enroll account'}</Button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {X_FIELDS.map(([f, label]) => (
            <div key={f} style={{ display: 'grid', gridTemplateColumns: '170px 1fr', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 12, color: C.muted }}>{label}</span>
              <input type="password" autoComplete="new-password" value={form[f]} onChange={(e) => setForm({ ...form, [f]: e.target.value })} placeholder="write-only" style={fieldStyle} />
            </div>
          ))}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 6 }}>
            <Button onClick={() => { setEditing(false); setForm(EMPTY_X); }}>Cancel</Button>
            <Button variant="primary" onClick={save} disabled={!complete || busy}>{busy ? 'Saving…' : 'Save encrypted'}</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function CredentialsTab() {
  const { withWorkspace, account } = useApp();
  const { data: providers } = usePoll('/api/ai/providers', 0);
  const { data: ai, reload: reloadAi } = usePoll(withWorkspace('/api/ai'), 0);
  const { data: sources } = usePoll('/api/sources', 0);
  const { data: creds, reload: reloadCreds } = usePoll('/api/credentials', 0);

  const keyStatus = (ai && ai.key_status) || {};
  const activeProvider = ai && ai.text ? ai.text.provider : null;
  const aiRows = (providers || []).filter((p) => p.key_env);
  const globalSet = useMemo(() => {
    const m = {};
    (creds || []).forEach((g) => (g.keys || []).forEach((k) => { m[k.name] = k.set; }));
    return m;
  }, [creds]);
  const sourceRows = (sources || []).filter((s) => (s.key_envs || []).length)
    .flatMap((s) => s.key_envs.map((env) => ({ src: s, env })));

  return (
    <>
      <TabIntro title="Credentials">
        Every key the app uses, stored encrypted. Keys are write-only: once saved they are never shown again, only replaced or removed.
      </TabIntro>

      <XAccountCard />

      <Card style={{ padding: '16px 18px 4px' }}>
        <CardHeader title="AI providers" sub="only the provider in use needs a key" right={<Scope />} style={{ marginBottom: 2 }} />
        {!providers || !ai ? <Loading /> : aiRows.map((p, i) => (
          <KeyRow
            key={p.key_env} label={p.label} env={p.key_env} last={i === aiRows.length - 1}
            extra={activeProvider === p.id ? <Tag fg={C.green} bg="rgba(62,207,142,.14)">in use</Tag> : null}
            set={!!keyStatus[p.key_env]} account={account} onChange={reloadAi}
            required={p.key_required} keyUrl={activeProvider === p.id ? p.key_url : null}
          />
        ))}
      </Card>

      <Card style={{ padding: '16px 18px 4px' }}>
        <CardHeader title="Images" sub="used by niches whose image source is Unsplash" right={<Scope />} style={{ marginBottom: 2 }} />
        <KeyRow
          label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><ImageIcon size={13} color={C.dim} />Unsplash</span>}
          env="UNSPLASH_ACCESS_KEY" last set={!!keyStatus.UNSPLASH_ACCESS_KEY} account={account} onChange={reloadAi}
          keyUrl="https://unsplash.com/oauth/applications"
        />
      </Card>

      <Card style={{ padding: '16px 18px 4px' }}>
        <CardHeader title="Content sources" sub="shared, so one key serves every workspace" right={<Scope global />} style={{ marginBottom: 2 }} />
        {!sources ? <Loading /> : sourceRows.length === 0
          ? <div className="os-mono" style={{ fontSize: 11, color: C.faint, padding: '12px 0' }}>no source needs a key</div>
          : sourceRows.map(({ src, env }, i) => (
            <KeyRow
              key={env} label={src.name} env={env} last={i === sourceRows.length - 1}
              extra={src.auth === 'optional' ? <Tag>optional</Tag> : null}
              set={!!globalSet[env]} onChange={reloadCreds} required={src.auth === 'required'}
            />
          ))}
      </Card>
    </>
  );
}

// ===========================================================================
// Workspaces
// ===========================================================================
function WorkspaceRow({ a, current, onSwitch, onChanged, last }) {
  const [label, setLabel] = useState(a.label);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setLabel(a.label); }, [a.label]);
  const renamed = label.trim() && label.trim() !== a.label;

  const rename = () => {
    if (!renamed || busy) return;
    setBusy(true);
    api.patch(`/api/accounts/${a.id}`, { label: label.trim() })
      .then(onChanged).catch((e) => { alert(e.message); setLabel(a.label); }).finally(() => setBusy(false));
  };
  const saveCap = (v) => api.patch(`/api/accounts/${a.id}`, v == null ? { clear_cap: true } : { daily_post_cap: v }).then(onChanged);
  const remove = () => {
    if (!confirm(`Delete workspace @${a.label}? Its drafts, history and settings are removed permanently. Shared content and sources are kept.`)) return;
    api.del(`/api/accounts/${a.id}`).then(onChanged).catch((e) => alert(e.message));
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', borderBottom: last ? 'none' : `1px solid ${C.line}`, flexWrap: 'wrap' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: current ? C.blue : '#3b414b' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 200, flex: '1 1 200px' }}>
        <span style={{ fontSize: 13, color: C.faint }}>@</span>
        <input
          value={label} onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') rename(); if (e.key === 'Escape') setLabel(a.label); }}
          onBlur={rename}
          title="rename"
          size={Math.min(28, Math.max(6, label.length + 1))}
          style={{ ...fieldStyle, fontFamily: 'inherit', fontSize: 13, fontWeight: 600, background: 'transparent', borderColor: 'transparent', padding: '5px 6px' }}
          className="hover-border"
        />
        {current && <Tag fg={C.blue} bg="rgba(122,162,247,.12)">current</Tag>}
      </div>
      <span className="os-mono" style={{ fontSize: 10, color: C.faint, minWidth: 150 }}>
        {a.niche_count} niche{a.niche_count === 1 ? '' : 's'} · {a.published_today} today · {a.configured ? 'X enrolled' : 'no X keys'}
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span className="os-mono" style={{ fontSize: 10, color: C.dim }}>cap</span>
        <NumberField value={a.daily_post_cap} onSave={saveCap} min={0} max={1000} blank placeholder="∞" width={64} />
      </span>
      {!current && <Button size="sm" onClick={onSwitch}>Switch</Button>}
      <Button size="sm" variant="ghost" onClick={remove} title="delete workspace" icon={<Trash2 size={12} />} />
    </div>
  );
}

function WorkspacesTab() {
  const { accounts, account, setAccount, reloadAccounts } = useApp();
  const [creating, setCreating] = useState(false);
  return (
    <>
      <TabIntro title="Workspaces">One workspace is one X account, with its own niches, schedule, AI provider, keys and drafts. Sources and fetched content are shared.</TabIntro>
      <Card style={{ padding: '16px 18px 4px' }}>
        <CardHeader
          title="All workspaces"
          sub={`${accounts.length} total · click a name to rename · blank cap = none`}
          right={<><Scope global /><Button size="sm" variant="soft" icon={<Plus size={12} />} onClick={() => setCreating(true)}>New workspace</Button></>}
          style={{ marginBottom: 2 }}
        />
        {accounts.map((a, i) => (
          <WorkspaceRow key={a.id} a={a} current={a.id === account} onSwitch={() => setAccount(a.id)} onChanged={reloadAccounts} last={i === accounts.length - 1} />
        ))}
        {!accounts.length && <Loading>no workspaces yet</Loading>}
      </Card>
      {creating && <CreateWorkspaceModal onClose={() => setCreating(false)} />}
    </>
  );
}

// ===========================================================================
// page
// ===========================================================================
export default function Settings() {
  const { settingsTab, setSettingsTab } = useApp();
  const tab = TAB_ALIASES[settingsTab] || (TABS.some(([k]) => k === settingsTab) ? settingsTab : 'general');

  return (
    <div className="os-settings">
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, background: '#0c0f13', border: `1px solid ${C.line}`, borderRadius: 12, padding: 8, position: 'sticky', top: 0 }}>
        {TABS.map(([k, label, Icon]) => {
          const active = tab === k;
          return (
            <button
              key={k}
              type="button"
              onClick={() => setSettingsTab(k)}
              className={`hover-tab${active ? ' active' : ''}`}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left', border: 'none',
                cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, fontWeight: active ? 600 : 500, padding: '9px 11px', borderRadius: 8,
                background: active ? '#15201b' : 'transparent', color: active ? C.green : '#8b93a0',
              }}
            >
              <Icon size={15} style={{ flexShrink: 0 }} />
              {label}
            </button>
          );
        })}
      </nav>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0, maxWidth: 900 }}>
        {tab === 'general' && <ControlsTab />}
        {tab === 'actions' && <ActionsTab />}
        {tab === 'ai' && <AiTab />}
        {tab === 'credentials' && <CredentialsTab />}
        {tab === 'workspaces' && <WorkspacesTab />}
      </div>
    </div>
  );
}
