import React, { useEffect, useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { Modal } from './common';

// Plain-language help for each page, opened from the "How it works?" tag in
// the top bar. Keep each entry short: what the page is for, then what happens.
export const HOW_IT_WORKS = {
  dashboard: {
    intro: 'A quick look at what your account is doing today.',
    steps: [
      'The top cards show stories, items fetched, drafts ready, posts published today, and active sources.',
      '"Next posts" lists the upcoming posting marks from your Schedule.',
      'Activity is a live feed of what the worker just did: fetches, drafts, posts and problems.',
      'Quick actions (top right) let you fetch news, write a draft, or post right now without waiting for a mark.',
    ],
  },
  niches: {
    intro: 'Niches are the topics your account posts about.',
    steps: [
      'Turn on the niches you want. Only stories from followed niches are used for drafts.',
      'Open a niche to change its keywords (what counts as relevant), its blocklist (what to skip), its tone, and its voice.',
      'Niches are shared by all accounts, but each account picks its own.',
    ],
  },
  queue: {
    intro: 'Drafts waiting to be published.',
    steps: [
      'A new draft is written at every posting mark, from the best story your account has not posted yet.',
      'Edit a draft, regenerate it (rewrite from the same story), reject it, or post it now.',
      'With Autopilot on, each mark publishes the best fresh draft: the highest priority one written in the last 6 hours.',
      'With Autopilot off, nothing is published until you press "post now".',
    ],
  },
  schedule: {
    intro: 'When your account posts.',
    steps: [
      'Posting marks are spread evenly between the window start and end, about one every "post every" minutes.',
      'About 5 minutes before a mark, the latest news is fetched.',
      'At each mark, one new draft is written from the best unused story.',
      'Autopilot on: the best fresh draft (highest priority, under 6 hours old) is published. Autopilot off: the draft waits in the Queue.',
      'Randomness nudges each mark a little so timing never looks robotic. Minimum gap and the daily cap still stop posts that come too close or too many.',
      'If the app was off and missed marks, it does not catch up with a burst. It just continues from the next mark.',
    ],
  },
  history: {
    intro: 'Everything that was published (or simulated in dry-run).',
    steps: [
      'Each row is one publish attempt with its time, result, and link to the post on X.',
      'Failed attempts show the error so you can see what went wrong.',
      'AI cost per post is shown when the model reports it.',
    ],
  },
  sources: {
    intro: 'Where the news comes from.',
    steps: [
      'Turn a source on to include it in fetches. Some need an API key, which you add here or in Settings → Credentials.',
      'Every fetch pulls new items from all enabled sources. One broken source never stops the others.',
      'New items are grouped into stories in the knowledge base. A story covered by several outlets ranks higher.',
    ],
  },
  rawdata: {
    intro: 'What the app has collected, before it becomes a post.',
    steps: [
      'Items: every article fetched from your sources.',
      'Stories: items about the same event grouped together. These are what drafts are written from.',
      'Stories not used within the set number of hours are forgotten automatically, so the database stays small.',
    ],
  },
  logs: {
    intro: 'A live tail of what the background worker is doing.',
    steps: [
      'Info lines show normal work: fetches, drafts, posts, cleanups.',
      'Warnings and errors explain why something did not happen, for example a cap was reached or a source failed.',
    ],
  },
  settings: {
    intro: 'Everything that controls the app, in five tabs.',
    steps: [
      'Controls: dry-run, autopilot, daily caps, publish retries, how long stories are remembered, and voice samples (real posts the writer copies the style of).',
      'Actions: fetch, write a draft or post right now, plus resetting this workspace or the whole database.',
      'AI provider: pick a provider, set its model and key, and test the connection before saving. "Offline template" needs no AI at all.',
      'Credentials: every key the app holds (X account, AI providers, Unsplash, content sources), stored encrypted and never shown again.',
      'Workspaces: one workspace is one X account. Rename, cap, switch or delete them here.',
    ],
  },
};

export default function HowItWorksButton({ route }) {
  const [open, setOpen] = useState(false);
  const help = HOW_IT_WORKS[route];

  useEffect(() => { setOpen(false); }, [route]);

  if (!help) return null;
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hover-bright"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer', fontSize: 11, fontWeight: 600,
          padding: '4px 9px', borderRadius: 999, border: '1px solid #2a313c', background: '#141922', color: '#9aa3af',
        }}
      >
        <HelpCircle size={12} />
        How it works?
      </button>
      {open && (
        <Modal onClose={() => setOpen(false)} tone="green" icon={<HelpCircle size={18} />} title="How it works" sub={help.intro} width={520}>
          <ol style={{ margin: '16px 0 0', paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {help.steps.map((step, i) => (
              <li key={i} style={{ fontSize: 12.5, lineHeight: 1.55, color: '#9aa3af' }}>{step}</li>
            ))}
          </ol>
        </Modal>
      )}
    </>
  );
}
