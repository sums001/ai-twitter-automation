import React, { useEffect, useMemo, useState } from 'react';
import { ChevronRight, Filter } from 'lucide-react';
import { api, usePoll } from '../api';
import { NICHE_COLOR, nicheLabel } from '../data';
import { useApp } from '../AppContext';
import { Card, CardHeader, EmptyState, FilterChip, Tag, Toggle } from '../components/common';
import NicheEditor from '../components/NicheEditor';

// The Niches page: select the niches you post in, then configure each one
// inline. Only selected niches are fetched, ranked, and drafted for —
// everything else stays dormant. There's no hard cap, but fewer focused niches
// produce sharper, more relevant posts. Selection is persisted server-side via
// /api/niches/followed; the open niche (settingsNiche) renders its config
// editor right under its row, exactly like the Sources tab.
export default function Niches() {
  const { settingsNiche, setSettingsNiche, withWorkspace } = useApp();
  const { data: allNiches } = usePoll('/api/niches', 60000);
  const { data: followedData, reload } = usePoll(withWorkspace('/api/niches/followed'), 0);
  // Niches are a shared catalog — every workspace sees all of them. Selection
  // (followed) is per workspace, so two workspaces can select the same niche
  // and each generates independently. No ownership filtering here.
  const niches = useMemo(() => allNiches || [], [allNiches]);

  const [followed, setFollowed] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [supportedOnly, setSupportedOnly] = useState(true); // hide niches with no enabled source

  // Seed local selection from the server, then let the user edit it.
  useEffect(() => {
    if (followedData) setFollowed(followedData.followed || []);
  }, [followedData]);

  const followedSet = useMemo(() => new Set(followed), [followed]);

  const persist = (next) => {
    setFollowed(next);
    setSaving(true);
    setError(null);
    api
      .put(withWorkspace('/api/niches/followed'), { slugs: next })
      .then((res) => {
        setFollowed(res.followed || []);
        setSaving(false);
        reload();
      })
      .catch((e) => {
        setError(e.message);
        setSaving(false);
        reload(); // resync from the server on failure
      });
  };

  const toggleSelect = (slug) => {
    if (followedSet.has(slug)) persist(followed.filter((s) => s !== slug));
    else persist([...followed, slug]);
  };

  // Expand/collapse a niche's inline config editor.
  const toggleEditor = (slug) => {
    setSettingsNiche(settingsNiche === slug ? null : slug);
  };

  const list = niches || [];
  // A niche is "supported" if it has at least one enabled source feeding it.
  const isSupported = (n) => n.has_enabled_source !== false;
  // Don't filter until at least one source is enabled anywhere — otherwise a
  // fresh DB (no sources on) would hide every niche and block selection.
  const anySupported = list.some(isSupported);
  const applySupported = supportedOnly && anySupported;
  const visibleList = applySupported ? list.filter(isSupported) : list;
  const hiddenCount = applySupported ? list.length - visibleList.length : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card style={{ padding: '16px 18px' }}>
        <CardHeader
          title="Selected niches"
          sub="only selected niches are fetched, ranked and drafted for · click a row to configure"
          right={<Tag fg="#3ecf8e" bg="rgba(62,207,142,.14)" style={{ fontSize: 11, padding: '3px 10px' }}>{followed.length} selected</Tag>}
          style={{ marginBottom: 12 }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <FilterChip
            active={supportedOnly}
            onClick={() => setSupportedOnly((v) => !v)}
            icon={<Filter size={12} style={{ flexShrink: 0 }} />}
            title="show only niches that have at least one enabled source"
          >
            Supported by sources only
          </FilterChip>
          <span className="os-mono" style={{ fontSize: 10, color: '#5b6470' }}>
            {supportedOnly && !anySupported
              ? 'no sources enabled yet · showing all'
              : applySupported && hiddenCount > 0
                ? `${hiddenCount} niche${hiddenCount > 1 ? 's' : ''} hidden · no enabled source`
                : `${visibleList.length} niche${visibleList.length === 1 ? '' : 's'}`}
          </span>
          <span className="os-mono" style={{ marginLeft: 'auto', fontSize: 10, color: '#7a828f' }}>
            <span style={{ color: '#f5a524', fontWeight: 700 }}>tip</span> · a few focused niches beat many thin ones
          </span>
        </div>
        {error && <div className="os-mono" style={{ fontSize: 11, color: '#f5455c', marginTop: 10 }}>{error}</div>}
      </Card>

      {list.length === 0 ? (
        <EmptyState title="No niches" sub="config/niches/*.json is empty or the service is unreachable" />
      ) : visibleList.length === 0 ? (
        <EmptyState title="No supported niches" sub="no niche has an enabled source — turn the filter off or enable sources" />
      ) : (
        <Card style={{ padding: 0 }}>
          {visibleList.map((n, i) => {
            const on = followedSet.has(n.slug);
            const isOpen = n.slug === settingsNiche;
            return (
              <div
                key={n.slug}
                style={{
                  borderTop: i ? '1px solid #171c24' : 'none',
                  background: isOpen ? 'rgba(255,255,255,.022)' : 'transparent',
                }}
              >
                {/* row — click anywhere (except the select toggle) to expand */}
                <div
                  onClick={() => toggleEditor(n.slug)}
                  className="lane-row"
                  style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 16px', cursor: 'pointer' }}
                >
                  <span
                    className={`lane-caret${isOpen ? ' open' : ''}`}
                    style={{ display: 'inline-flex', color: isOpen ? '#3ecf8e' : '#5b6470', flexShrink: 0 }}
                  >
                    <ChevronRight size={14} />
                  </span>
                  <span style={{ width: 9, height: 9, borderRadius: 2, flexShrink: 0, background: NICHE_COLOR[n.slug] }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: isOpen ? 700 : 600, color: isOpen ? '#fff' : '#cfd6df' }}>
                      {n.display_name || nicheLabel(n.slug)}
                    </div>
                    <div className="os-mono" style={{ fontSize: 10, color: '#5b6470', marginTop: 2 }}>{n.slug}</div>
                  </div>
                  <span onClick={(e) => e.stopPropagation()} style={{ display: 'inline-flex' }}>
                    <Toggle
                      on={on}
                      onClick={() => !saving && toggleSelect(n.slug)}
                      title={on ? 'deselect' : 'select'}
                    />
                  </span>
                </div>

                {/* inline config editor, right under this row */}
                {isOpen && (
                  <div className="lane-open" style={{ padding: '4px 16px 18px 42px' }}>
                    <NicheEditor />
                  </div>
                )}
              </div>
            );
          })}
        </Card>
      )}
    </div>
  );
}
