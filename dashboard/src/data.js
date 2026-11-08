// Taxonomy + theming for the OpenX console.
// Live data comes from the service API (src/api.js); this module only keeps
// the navigation map, color system, and a dynamic niche registry that
// AppContext fills from GET /api/niches.

export const NAV = [
  ['dashboard', 'Dashboard'],
  ['niches', 'Niches'],
  ['queue', 'Queue'],
  ['schedule', 'Schedule'],
  ['history', 'History'],
  ['sources', 'Sources'],
  ['rawdata', 'Raw Data'],
  ['logs', 'Logs'],
  ['settings', 'Settings'],
];

// Sidebar nav, grouped: "Workspace" tabs scope to the current workspace;
// "Global" tabs (sources, raw data) are shared across all workspaces.
export const NAV_GROUPS = [
  ['Workspace', [
    ['dashboard', 'Dashboard'],
    ['niches', 'Niches'],
    ['queue', 'Queue'],
    ['schedule', 'Schedule'],
    ['history', 'History'],
    ['logs', 'Logs'],
    ['settings', 'Settings'],
  ]],
  ['Global', [
    ['sources', 'Sources'],
    ['rawdata', 'Raw Data'],
  ]],
];

// Fallback list until /api/niches loads (matches config/niches/*.json).
export let NICHES = [
  'politics', 'finance', 'tech', 'business', 'ai', 'crypto', 'news', 'sports',
  'entertainment', 'gaming', 'health', 'fitness', 'self-improvement',
  'education', 'marketing', 'startups', 'science', 'lifestyle',
];

// Global service actions, surfaced both in the top-bar Quick-actions menu and
// on the Settings → Actions tab. [command type, label, description].
export const QUICK_ACTIONS = [
  ['fetch_sources', 'Fetch sources', 'pull fresh items from every enabled source'],
  ['generate_posts', 'Write a draft', 'write one post from the best unused story'],
  ['run_slots', 'Post next now', 'publish the next post right away (within caps)'],
];

const LABELS = { tech: 'Technology', ai: 'AI' };

// golden-angle hue spacing → distinct, harmonious colors per category
function colorAt(i) {
  return `oklch(0.74 0.15 ${(i * 137 + 30) % 360})`;
}

export const NICHE_COLOR = {};
function recolor() {
  NICHES.forEach((s, i) => { NICHE_COLOR[s] = colorAt(i); });
}
recolor();

export function nicheLabel(slug) {
  if (LABELS[slug]) return LABELS[slug];
  return slug
    .split(/[_-]/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

// Called by AppContext with the live niche list [{slug, display_name, enabled}].
export function setNiches(list) {
  if (!list || !list.length) return;
  NICHES.length = 0;
  list.forEach((n) => {
    NICHES.push(n.slug);
    LABELS[n.slug] = n.display_name;
  });
  recolor();
}

export const STATUS_COLORS = {
  draft: ['#9aa3af', 'rgba(154,163,175,.13)'],
  queued: ['#7aa2f7', 'rgba(122,162,247,.14)'],
  scheduled: ['#7aa2f7', 'rgba(122,162,247,.14)'],
  pending: ['#9aa3af', 'rgba(154,163,175,.13)'],
  running: ['#7aa2f7', 'rgba(122,162,247,.14)'],
  done: ['#3ecf8e', 'rgba(62,207,142,.14)'],
  published: ['#3ecf8e', 'rgba(62,207,142,.15)'],
  rejected: ['#f5455c', 'rgba(245,69,92,.14)'],
  failed: ['#f5455c', 'rgba(245,69,92,.14)'],
  success: ['#3ecf8e', 'rgba(62,207,142,.15)'],
};

export const TYPE_COLORS = {
  news: ['#7aa2f7', 'rgba(122,162,247,.13)'],
  spotlight: ['#f5a524', 'rgba(245,165,36,.13)'],
  insight: ['#b48ef7', 'rgba(180,142,247,.13)'],
  take: ['#f5455c', 'rgba(245,69,92,.13)'],
  tip: ['#3ecf8e', 'rgba(62,207,142,.13)'],
  question: ['#5fd4e0', 'rgba(95,212,224,.13)'],
  meme: ['#f78fb3', 'rgba(247,143,179,.13)'],
};

export const LEVEL_COLORS = {
  info: ['#7aa2f7', 'rgba(122,162,247,.13)'],
  warn: ['#f5a524', 'rgba(245,165,36,.13)'],
  error: ['#f5455c', 'rgba(245,69,92,.13)'],
};

export const ROUTE_TITLES = {
  dashboard: ['Dashboard', 'today at a glance'],
  niches: ['Niches', 'the topics this account posts about'],
  queue: ['Queue', 'drafts waiting to be published'],
  history: ['History', 'every publish attempt'],
  sources: ['Sources', 'where the news comes from'],
  rawdata: ['Raw Data', 'stories and fetched items'],
  schedule: ['Schedule', 'when this account posts'],
  logs: ['Logs', 'live worker output'],
  settings: ['Settings', 'controls, AI and credentials'],
};

// Friendly names for worker commands (toasts, last-run lines).
export const COMMAND_LABELS = {
  fetch_sources: 'Fetch sources',
  generate_posts: 'Write a draft',
  run_slots: 'Post next now',
  post_now: 'Post now',
  regenerate_post: 'Regenerate draft',
};
