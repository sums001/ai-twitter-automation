import React, { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
import { api, usePoll } from './api';
import { setNiches } from './data';

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [route, setRoute] = useState('dashboard');
  const [niche, setNiche] = useState('all');
  // The CURRENT workspace (one X account). The whole app operates within it.
  // Persisted so a reload returns to the same workspace. '' until accounts load.
  const [account, setAccount] = useState(() => {
    try { return localStorage.getItem('os_workspace') || ''; } catch { return ''; }
  });
  useEffect(() => {
    try {
      if (account) localStorage.setItem('os_workspace', account);
    } catch { /* ignore */ }
  }, [account]);

  // ---- enrolled workspaces (accounts) ---------------------------------------
  // Manual state (not usePoll) so create/delete can await a refresh before
  // switching the current workspace — avoids a stale-list selection race.
  const [accountsData, setAccountsData] = useState(null);
  const reloadAccounts = useCallback(
    () => api.get('/api/accounts').then((d) => { setAccountsData(d); return d; }).catch(() => null),
    []
  );
  useEffect(() => {
    reloadAccounts();
    const t = setInterval(reloadAccounts, 15000);
    return () => clearInterval(t);
  }, [reloadAccounts]);
  const accounts = accountsData || [];
  // Always keep one valid current workspace selected (default = first). If the
  // current one is deleted, fall back to the first remaining workspace.
  useEffect(() => {
    if (!accountsData) return;
    if (accountsData.length === 0) {
      if (account) setAccount('');
    } else if (!accountsData.some((a) => a.id === account)) {
      setAccount(accountsData[0].id);
    }
  }, [account, accountsData]);

  // Append the current workspace scope to an API path.
  const withWorkspace = useCallback(
    (path) => {
      if (!account) return path;
      return path + (path.includes('?') ? '&' : '?') + 'account=' + encodeURIComponent(account);
    },
    [account]
  );

  // ---- service status (dry-run, app mode) — scoped to current workspace -----
  const { data: status, reload: reloadStatus } = usePoll(withWorkspace('/api/status'), 5000);
  const dryRun = status ? status.dry_run : true;
  const autopilot = status ? status.app_mode === 'auto' : false;

  const toggleDry = useCallback(() => {
    api.patch(withWorkspace('/api/settings'), { dry_run: !dryRun }).then(reloadStatus).catch(() => {});
  }, [dryRun, reloadStatus, withWorkspace]);
  const toggleAutopilot = useCallback(() => {
    api
      .patch(withWorkspace('/api/settings'), { app_mode: autopilot ? 'manual' : 'auto' })
      .then(reloadStatus)
      .catch(() => {});
  }, [autopilot, reloadStatus, withWorkspace]);

  // ---- live niche registry --------------------------------------------------
  const { data: nichesData } = usePoll('/api/niches', 60000);
  const [nichesVersion, setNichesVersion] = useState(0);
  useEffect(() => {
    if (nichesData) {
      setNiches(nichesData);
      setNichesVersion((v) => v + 1);
    }
  }, [nichesData]);

  // ---- real command queue: enqueue via API, poll until done/failed ---------
  const [commands, setCommands] = useState([]);
  const runCommand = useCallback((type, payload) => {
    // Commands run within the current workspace (generate/publish scope to it;
    // shared fetch ignores it). post_now/regenerate derive it from the post.
    const body = { ...(payload || {}) };
    if (account) body.workspace_id = account;
    const tempId = 'tmp_' + Math.random().toString(36).slice(2, 8);
    setCommands((cs) => [
      { id: tempId, type, payload: body, status: 'pending', result: null },
      ...cs,
    ].slice(0, 40));
    api
      .post('/api/commands', { type, payload: body })
      .then((row) =>
        setCommands((cs) => cs.map((c) => (c.id === tempId ? { ...c, id: row.id, status: row.status } : c)))
      )
      .catch((e) =>
        setCommands((cs) =>
          cs.map((c) => (c.id === tempId ? { ...c, status: 'failed', result: { error: e.message } } : c))
        )
      );
  }, [account]);

  useEffect(() => {
    const t = setInterval(() => {
      setCommands((cs) => {
        const open = cs.filter((c) => typeof c.id === 'number' && (c.status === 'pending' || c.status === 'running'));
        if (open.length) {
          api
            .get(`/api/commands?ids=${open.map((c) => c.id).join(',')}`)
            .then((rows) =>
              setCommands((cur) =>
                cur.map((c) => {
                  const row = rows.find((r) => r.id === c.id);
                  return row ? { ...c, status: row.status, result: row.result } : c;
                })
              )
            )
            .catch(() => {});
        }
        return cs;
      });
    }, 1500);
    return () => clearInterval(t);
  }, []);

  // ---- settings navigation (deep-link into the niche editor) ----------------
  const [settingsTab, setSettingsTab] = useState('general');
  const [settingsNiche, setSettingsNiche] = useState(null); // open niche on the Niches page

  // Niche config now lives on the Niches page; deep-link selects it there.
  const editNiche = useCallback((n) => {
    setRoute('niches');
    setSettingsNiche(n);
  }, []);

  // Create a named workspace, then drop into its fresh dashboard. Refreshes the
  // account list *before* switching so the new id is already known-valid.
  const createWorkspace = useCallback(async (label, cap) => {
    const body = { label: (label || '').trim() };
    const c = cap == null ? '' : String(cap).trim();
    if (c !== '') body.daily_post_cap = Math.max(0, parseInt(c, 10) || 0);
    const row = await api.post('/api/accounts', body);
    await reloadAccounts();
    setAccount(row.id);
    setRoute('dashboard');
    return row;
  }, [reloadAccounts]);

  // Current workspace id (alias of ``account``) + scope helper. ``withAccount``
  // is kept as an alias so existing pages keep working.
  const workspace = account;
  const setWorkspace = setAccount;
  const currentWorkspace = accounts.find((a) => a.id === account) || null;

  const value = useMemo(
    () => ({
      route, setRoute,
      niche, setNiche,
      account, setAccount, accounts, reloadAccounts,
      workspace, setWorkspace, currentWorkspace, createWorkspace,
      withAccount: withWorkspace, withWorkspace,
      status, reloadStatus,
      dryRun, toggleDry,
      autopilot, toggleAutopilot,
      commands, runCommand,
      nichesVersion,
      settingsTab, setSettingsTab, settingsNiche, setSettingsNiche,
      editNiche,
    }),
    [route, niche, account, accounts, reloadAccounts, withWorkspace, currentWorkspace, createWorkspace,
     status, reloadStatus, dryRun, toggleDry, autopilot, toggleAutopilot,
     commands, runCommand, nichesVersion, settingsTab, settingsNiche, editNiche]
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  return useContext(AppContext);
}
