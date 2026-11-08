# OpenX — Architecture

> **Status:** the MVP is built. The full pipeline (fetch → filter → generate → schedule → publish), the FastAPI dashboard backend, and the React dashboard all exist. This document was written as a forward plan and has since been reconciled with what actually shipped; section "Build Phasing" at the end records the original phase ordering for context. Where the build diverged from the plan (config moved YAML→JSON, AI image generation dropped, GDELT/Finnhub replaced by Google News, a real HTTP API added), the prose below reflects the shipped behavior.

## Context

OpenX is an open-source, plugin-based tool for automated, AI-driven posting to **X (Twitter)**. Any "niche" (crypto, tech, finance, etc.) can be configured with its own content sources, AI persona/prompts, posting schedule with anti-bot randomness, optional Telegram approval gate, and a dashboard for full configuration. 18 niche profiles ship out of the box.



## Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Language/runtime | Python 3.12, asyncio | Every library needed (X API, AI) is Python-first; runs on a cheap VPS or Raspberry Pi |
| HTTP client | httpx (async) | Concurrent fetching across all sources |
| Reddit | asyncpraw | Official async fork of PRAW |
| RSS/Atom | feedparser | Universal parser for the generic RSS plugin |
| YouTube | google-api-python-client + youtube-transcript-api | Channel data + transcripts |
| Scheduler | APScheduler (async, DB-backed job store) | Persists jobs in the same SQLite database, no Redis/Celery needed |
| Database | SQLite | Single file, zero-config, shared directly with the separate dashboard project |
| AI text | LiteLLM | One interface across OpenAI/Anthropic/Gemini/Ollama — fully swappable providers; default is a local Ollama model (no key). An offline template provider is available for tests/offline runs. |
| Images | Unsplash (stock) or the source item's own media | AI image *generation* (Pollinations/DALL·E) was dropped after Pollinations moved to a paid, rate-limited model; images now come only from real sources |
| X posting | Tweepy | Covers posting + media upload |
| Dashboard API | FastAPI + uvicorn | Thin HTTP layer the browser dashboard calls (`/api/*`) |
| Secrets | Encrypted at rest (Fernet) | API keys and platform credentials stored encrypted, key from `OPENSOCIAL_SECRET_KEY` (or a key file) |

---

## High-Level Structure

The system is organized as a set of plugin packages built around a shared "content item" format:

- **Sources** — one plugin per content source (or a generic RSS plugin reused across many feeds). Each plugin fetches and normalizes content into a common format. Adding a new source means adding a new plugin, with no changes elsewhere.
- **AI (text)** — generates post text from a content item and a niche's persona/prompt, via a swappable AI provider (Ollama by default — no API key required).
- **AI (images)** — supplies an image for a post, either from a stock photo provider (Unsplash) or the source item's own media, behind a single interface.
- **Publisher** — posts to X (via Tweepy), built behind a publisher interface so other platforms could be added later as additional plugins.
- **Scheduler** — decides when posts go out, with randomized timing/jitter to avoid bot-like patterns, and enforces daily post limits.
- **Core** — shared data models, the SQLite database layer, and the pipeline that ties fetch → filter → generate → schedule → publish together.

---

## Project Structure

OpenX is split into two independently-deployable projects sharing one SQLite database:

- **service** (Python) — everything described above in High-Level Structure: source plugins, filtering/dedup, AI text & image attachment, the X publisher, scheduler, CLI, **and** a FastAPI app that backs the dashboard. It owns the content/post/history tables and reads the configuration to know what to do.
- **dashboard** (React + Vite, separate project) — the configuration & monitoring UI described later. It calls the service's HTTP API (`/api/*`) for everything; it contains no automation logic and does not touch SQLite directly.

This split keeps automation and configuration cleanly separated: `service` owns *what happens*, `dashboard` owns *what's configured* and *what's shown*.

The dashboard does not write to SQLite itself (the original plan had it sharing the file directly). Instead the service exposes a small HTTP API: reads query the DB; fast state changes (approve/reject/edit, config edits, toggles) apply synchronously; slow actions (fetch/generate/publish) are enqueued in the `commands` table and run by the background worker that `opensocial serve` starts alongside the API.

---

## Core Data Model

Every source plugin returns content normalized into a common "content item" shape, which is the only thing the rest of the pipeline understands. It includes: a stable ID (used for de-duplication), the source name/category, title, body/summary text, URL, author, published/fetched timestamps, any media URLs, tags, language, an optional sentiment score, optional engagement stats (e.g. score/comments), and the raw original data for reference.

AI generation always works from this normalized item — it never needs to know which source it came from.

**Field availability varies by source.** Full `body` text is available from Reddit, YouTube (via transcript), Medium and most `content:encoded` feeds (Decrypt, YC Blog, Ars Technica, Futurism, Nature, Phys.org, ScienceDaily, WHO), Dev.to, and the Guardian; the rest (Google News, CoinDesk, The Block, MarketWatch, CNBC, HBR, Fast Company, Fortune, Entrepreneur, ArXiv abstracts, yfinance) only provide a `summary`/excerpt — so the standard fallback is `content_for_ai = item.body or item.summary or item.title`. No shipped source currently populates `sentiment`, so it stays null and the prioritization blend redistributes its weight when no `sentiment_target` is set. `engagement` is a flexible JSON dict whose shape differs per source (YouTube: views/likes/comments; Hacker News: score/comments; Dev.to: reactions/comments/reading_time) — Reddit (public RSS) and the RSS-based sources leave it empty. `language` is rarely provided per item (the Guardian is an exception) and otherwise defaults to `en`.

---

## Content Sources

10 source plugins ship, registered via `@register` on the `Source` ABC (`opensocial/sources/`). Each plugin also describes itself on its class — `label`, `description`, `auth` (`none` / `optional` / `required`), `key_envs`, `key_help`, the niche-config `settings` it reads, and (for user-fed sources) an origin spec. The dashboard API, the credentials list and the fetch runner read only these attributes. The generic `rss` plugin is reused across many feeds, so the per-niche source list covers far more outlets than there are plugins (TechCrunch, Ars Technica, Futurism, CoinDesk, Decrypt, The Block, MarketWatch, CNBC, HBR, Fast Company, Fortune, Entrepreneur, YC Blog, Nature, Phys.org, ScienceDaily, WHO are all `rss` feeds configured per niche).

| Plugin | Source | Auth |
|---|---|---|
| `googlenews` | Google News RSS — global news/trend feed via per-query search RSS | none |
| `rss` | Generic RSS/Atom parser, reused for most feed-based outlets | none |
| `reddit` | Reddit (public `.rss` Atom feeds; `.json` is blocked unauthenticated) | none |
| `youtube` | YouTube Data API (with transcripts) | API key |
| `medium` | Medium RSS (by tag/category) | none |
| `hackernews` | Hacker News API | none |
| `devto` | Dev.to API | none |
| `arxiv` | ArXiv API | none |
| `guardian` | The Guardian API | API key |
| `yfinance` | Yahoo Finance / yfinance (unofficial) | none |

**Removed:** GDELT (its DOC 2.0 API rate-limited at 1 req/5s and starved most niches — replaced by Google News), Finnhub, Product Hunt (token-only, commercial-use restrictions), GitHub Releases, and NASA APOD (its `count` mode returns random archive entries that the age filter always dropped). Don't reintroduce them.

A fetch that runs nothing reports why (source turned off, required key missing, no followed niche) as a failed command, so the dashboard toast shows the reason. Sources with `auth = required` and no key are skipped up front rather than attempted and recorded as errors.

---

## Niche Profiles

Each niche (e.g. "crypto", "tech") is a **JSON** configuration profile under `config/niches/<slug>.json` (the source of truth; also mirrored into `niche_profiles`). It defines:

- **Persona** — style, length and custom instructions used to write posts (standalone text, never assuming a link is attached), plus optional `examples` (voice samples that override the account's).
- **Image source** — the per-niche `image_source` field: `unsplash` (stock), `content` (the source item's own media), or `none`.
- **Filters** — keyword blocklist, relevance keywords/threshold (whole-word matching; headline hits count 3x), max age, and near-duplicate threshold/window.
- **Post types** — which of the seven tones are enabled (each tone is a post "shape").
- **Sources** — which source plugins are enabled for this niche, with source-specific settings (subreddits, channel IDs, feed URLs, search queries, limits).
- **Schedule** — posting time windows, posts-per-day range, and minimum gap between posts.
- **Approval** — optional per-niche Telegram gate (`required`, `timeout_minutes`, `on_timeout`).
- **Posting** — whether the post includes the source link (off by default, to keep X cost at $0.015/post).

18 niche profiles ship (ai, business, crypto, education, entertainment, finance, fitness, gaming, health, lifestyle, marketing, news, politics, science, self-improvement, sports, startups, tech). The dashboard lets users create new niches and edit every one of these settings.

---

## Post Types

Every generated post has a **post type** that selects the prompt template and the visual rule. The taxonomy (absorbed from a working reference implementation) is:

| Type | Intent | Visual rule | Source |
|---|---|---|---|
| `news` | A timely development; lead with the implication, not the headline. | always | source-derived |
| `spotlight` | Highlight a tool/repo/paper/product worth knowing. | always | source-derived |
| `insight` | A non-obvious observation or synthesis. | optional | source-derived **or** independent |
| `take` | A bold, opinionated stance on a topic or trend. | optional | source-derived **or** independent |
| `tip` | A specific, actionable how-to. | optional | source-derived **or** independent |
| `question` | Provokes genuine debate/replies. | rarely | source-derived **or** independent |
| `meme` | Witty, relatable to the niche (the image is the point). | always | source-derived **or** independent |

The post type is chosen per candidate during generation (the persona prompt is given the type and writes accordingly). Each niche's `post_types` config enables a subset, sets an optional **per-type daily cap**, and overrides the default visual rule. Visual rules resolve to `always` / `content_based` / `never` server-side (not trusted to the model) so the type→visual mapping stays consistent.

Cross-cutting generation rules baked into every persona prompt (these directly serve the "standalone, no-link" design in the Niche Profiles section):

- Never reference or hint at the source ("according to", outlet/account names, "saw this", links). Write it as an original thought.
- Strong scroll-stopping hook on the first line; no corporate filler ("excited to share").
- Respect the platform character limit, counting every URL as a flat 23 chars (t.co wrapping); one rewrite-to-fit pass before accepting an over-limit draft.
- Strip wrapping quotes/backticks the model adds around the text.

### Synthesis takes (replaced the daily independent Take)

Posts are written from knowledge-base stories. When an account has used every recent story, the next post is a **synthesis take**: one write call reads the room across the three biggest recent stories (`generated_posts.story_id` and `content_item_id` are NULL). Only an empty knowledge base produces no post. The old per-niche `independent_take` job was removed.

---

## Pipeline / Data Flow

1. **Fetch** — sources are polled in one shared pass for every workspace (about 5 minutes before any account's next post, or on demand); new items are de-duplicated and stored, then linked to niches.
2. **Filter** — each niche applies its blocklist/keyword/age filters (whole words) and a near-duplicate check, marking items candidate, filtered, or duplicate.
3. **Knowledge base** — every item that passed a niche is grouped into a **story** (`stories`): several outlets reporting one event become one story, so coverage is a signal. Grouping uses headline word overlap plus shared names and figures; no LLM.
4. **Shortlist** — per account, unused stories are scored for free: coverage, freshness, momentum, relevance, source trust and headline postability, discounted by similarity to what the account already posted, topic repetition, and how often the story was passed over (`story_usage`).
5. **Pick + write** — when the account's next post is due, the top story wins outright, or one tiny AI call picks among the top few when scores are within 15%. The winner's article is fetched and reduced to a fact sheet (free), then **one** write call produces the post from the niche persona, the account's voice samples and a tone shape. Code cleans markdown, dashes, hashtags, emoji, clichés and length; a rewrite call happens only when code can't fix it. The story is marked used for that account.
6. **Publish** — each account posts on its own cadence: inside its daily window, about every X minutes with random jitter (`next_post_at` rolled once and stored). In `auto` mode the post is published (a fresh waiting draft goes first, costing no tokens); in `manual` mode it waits in the queue, and drafting pauses once a few are waiting. The source link is included or omitted per the niche's setting, and the result (success/failure, cost) is recorded.

Safety and modes (absorbed from the reference implementation):

- **Dry-run by default** — publishing is live only when explicitly enabled (`POST_DRY_RUN=false`); any other value stays dry and only logs what *would* post. Fail-safe so nothing goes out by accident.
- **`app_mode: manual | auto`** — every posting mark writes one draft. In `manual`, the engine never publishes on its own; drafts wait until "Post now". In `auto`, each mark also publishes the highest-priority draft written in the last 6 hours.
- **Posting state machine** — a failed publish increments `post_attempts` and records `post_error`; after the max attempts the draft is marked `failed`.
- **Overlap guard** — a scheduled job that is still running when its next tick fires skips that tick rather than running twice.

Each account's daily cap (with the global cap as a backstop) and minimum gap still apply on top of the cadence. Live mode with no X credentials holds posts rather than silently simulating them.

---

## Database Schema

SQLite tables (SQLAlchemy models + Alembic migrations). JSON columns are stored as serialized text. Each table is tagged with the build phase that introduces it.

```sql
-- Phase 1
content_items (
  id              TEXT PRIMARY KEY,   -- sha256(source_name + url)
  source_name     TEXT NOT NULL,
  source_category TEXT NOT NULL,
  title           TEXT NOT NULL,
  body            TEXT,               -- full text/transcript/abstract, when the source provides it
  summary         TEXT,
  url             TEXT NOT NULL,
  author          TEXT,
  published_at    DATETIME NOT NULL,
  fetched_at      DATETIME NOT NULL,
  media_urls      JSON,               -- list[str]
  tags            JSON,               -- list[str]
  language        TEXT DEFAULT 'en',
  sentiment       REAL,               -- -1..1; no shipped source populates it today
  engagement      JSON,               -- shape varies per source, e.g. {"score":, "comments":}
  raw_metadata    JSON NOT NULL       -- full original API/feed item, for forward-compat
)

content_item_niches (
  content_item_id TEXT NOT NULL REFERENCES content_items(id),
  niche_slug      TEXT NOT NULL,
  status          TEXT NOT NULL,      -- candidate | filtered | duplicate
  relevance_score REAL,
  PRIMARY KEY (content_item_id, niche_slug)
)

niche_profiles (
  slug          TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  enabled       BOOLEAN NOT NULL DEFAULT 1,
  config_yaml   TEXT NOT NULL,        -- full niche config as JSON (column name kept for
                                      -- back-compat; the on-disk source of truth is
                                      -- config/niches/<slug>.json)
  updated_at    DATETIME NOT NULL
)

source_configs (
  source_name       TEXT PRIMARY KEY,
  enabled_globally  BOOLEAN NOT NULL DEFAULT 1,
  last_fetch_at     DATETIME,
  last_fetch_status TEXT,
  extra_config      JSON
)

-- Phase 3
generated_posts (
  id                TEXT PRIMARY KEY,
  content_item_id   TEXT REFERENCES content_items(id),  -- the story's best item; NULL = synthesis take
  story_id          TEXT,             -- knowledge-base story it was written from
  platform_account_id TEXT,           -- the workspace that wrote it
  niche_slug        TEXT NOT NULL,
  post_type         TEXT NOT NULL,    -- news | spotlight | insight | take | tip | question | meme
  text              TEXT NOT NULL,
  media_path        TEXT,
  media_url         TEXT,
  media_attribution TEXT,
  ai_text_provider  TEXT NOT NULL,
  ai_image_provider TEXT,
  status            TEXT NOT NULL,    -- draft | published | rejected | failed
                                      -- (the optional Telegram approval gate is handled by
                                      -- approval.py, not a separate status)
  priority_score    REAL,             -- the story's shortlist score when it was written
  scheduled_at      DATETIME,
  post_attempts     INTEGER NOT NULL DEFAULT 0,
  post_error        TEXT,
  created_at        DATETIME NOT NULL,
  updated_at        DATETIME NOT NULL
)

-- Phase 4
platform_accounts (
  id                    TEXT PRIMARY KEY,
  platform              TEXT NOT NULL DEFAULT 'x',
  account_label         TEXT NOT NULL,
  credentials_encrypted BLOB NOT NULL,
  daily_post_cap        INTEGER
)

post_history (
  id                    TEXT PRIMARY KEY,
  generated_post_id     TEXT NOT NULL REFERENCES generated_posts(id),
  platform              TEXT NOT NULL DEFAULT 'x',
  platform_account_id   TEXT NOT NULL REFERENCES platform_accounts(id),
  platform_post_id      TEXT,
  platform_post_url     TEXT,
  status                TEXT NOT NULL,  -- success | failed
  error_message         TEXT,
  attempted_at          DATETIME NOT NULL,
  included_source_link  BOOLEAN NOT NULL,
  cost_estimate         REAL NOT NULL
)

-- (The planned standalone api_keys table was folded into app_settings: dashboard
-- secrets are stored there as Fernet-encrypted `secret:<ENV_NAME>` rows.)

-- Phase 4 — dashboard ↔ service bridge. The dashboard calls the service's HTTP
-- API; slow actions are enqueued here, and the background worker started by
-- `opensocial serve` polls, executes them with already-loaded modules, and
-- records the outcome.
commands (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  type        TEXT NOT NULL,    -- fetch_sources | generate_posts | post_now | ...
  payload     JSON,             -- e.g. {"generated_post_id": "..."} for post_now
  status      TEXT NOT NULL,    -- pending | running | done | failed
  result      JSON,
  created_at  DATETIME NOT NULL,
  finished_at DATETIME
)

-- Console output mirrored here so the dashboard can show service logs.
logs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  level     TEXT NOT NULL,      -- info | warn | error
  message   TEXT NOT NULL,
  logged_at DATETIME NOT NULL
)

-- Knowledge base (shared stories + per-workspace memory)
stories (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,     -- headline of the most trusted outlet
  summary           TEXT,
  best_item_id      TEXT,              -- fetched for full text / media passthrough
  first_seen_at     DATETIME NOT NULL,
  last_seen_at      DATETIME NOT NULL,
  updated_at        DATETIME NOT NULL,
  item_count        INTEGER NOT NULL,
  outlets           JSON NOT NULL,     -- distinct outlets (coverage)
  niche_relevance   JSON NOT NULL,     -- {niche_slug: best relevance}
  tokens            JSON NOT NULL,     -- headline token sets used for matching
  entities          JSON NOT NULL,     -- names/tickers ('#'-prefixed figures)
  trust             REAL NOT NULL,     -- best source trust (Source.trust)
  recent_item_times JSON NOT NULL      -- for momentum
)
-- content_items.story_id links each grouped item to its story.

story_usage (
  story_id            TEXT REFERENCES stories(id),
  platform_account_id TEXT,            -- per workspace
  status              TEXT NOT NULL,   -- used | shown
  times_shown         INTEGER NOT NULL,-- shortlisted without winning (fades the score)
  last_shown_at       DATETIME,
  used_at             DATETIME,
  generated_post_id   TEXT,
  PRIMARY KEY (story_id, platform_account_id)
)

-- Runtime settings the dashboard can change without a service restart, plus
-- Fernet-encrypted secrets saved from the dashboard. resolve_settings() overlays
-- these on top of the environment each scheduler tick.
app_settings (
  key   TEXT PRIMARY KEY,   -- dry_run | app_mode | global_daily_cap | secret:<ENV_NAME> |
                            -- ws:<id>:cadence | ws:<id>:next_post_at | ws:<id>:voice_samples |
                            -- kb_last_fetch | ...
  value TEXT NOT NULL
)
```

---

## Dashboard (Separate Project)

The web dashboard is a **React + Vite** app (`dashboard/`). It does **not** touch SQLite directly (the original plan had it sharing the file); instead it calls the service's FastAPI backend over HTTP. The backend (`opensocial/api.py`, started by `opensocial serve`) serves reads from the DB, applies fast state changes synchronously, and routes slow actions through the `commands` queue to the background worker.

Pages: **Dashboard** (overview), **Niches** (full per-niche config editor), **Queue** (draft posts — approve/reject/edit), **Schedule** (the account's window, post-every-X-minutes, jitter, safety limits, today's timeline), **Sources** (per-source toggles, settings, credentials), **History** (publish history + cost), **Logs** (mirrored service logs), **Settings** (dry-run/app-mode, voice samples, caps, secrets), **RawData** (knowledge-base stories as ranked for the account, plus raw items).

Key API groups (`/api/*`): `status`/`settings`/`overview`, `niches` (+ `followed`), `schedule` (account cadence), `voice`, `stories`, `posts` (+ approve/reject/edit), `content`, `history`, `logs`, `sources` (+ origins), `ai`, `commands`, `credentials`.

---

## Cost Tracking

Cost per post is recorded in `post_history.cost_estimate` as $0.015 (text-only) or $0.20 (with link), plus $0.001 per analytics read. The dashboard sums these for spend tracking and shows the cost difference live when toggling whether a post includes the source link.

---

## Build Phasing

All phases below are **complete**, plus a dashboard/API phase (Phase 6) not in the original plan: the FastAPI backend (`opensocial/api.py`), the `app_settings` runtime-settings/secrets table, the `serve` command (API + background worker), autopilot draft refresh, and the React + Vite dashboard.

**Phase 1 — Sources**
Core data models (`ContentItem`) and database tables for `content_items` and `content_item_niches`; the source plugin framework (`Source` ABC + registry) and the generic RSS plugin; a first set of real plugins covering each source category (e.g. generic RSS, Hacker News, Google News, Reddit, YouTube) to prove the pattern across both API- and feed-based sources; one simplified niche profile (with the user-selected subreddits/YouTube channels and category-based source toggles); and a CLI command to fetch and store content, de-duplicated across repeat runs.

**Phase 2 — Data Extraction & Prioritization**
Filtering (keyword blocklist, relevance keywords/threshold, age limits) and near-duplicate detection against recently seen content, marking each item as candidate, filtered, or duplicate; candidate prioritization by recency, engagement, and sentiment match, producing an ordered queue of candidates per niche.

**Phase 3 — Post Creation**
AI text generation via LiteLLM (Ollama by default, no API key needed) that turns prioritized candidates into standalone, no-link draft posts via the niche's persona/prompt, with a **post type** per draft (news/spotlight/insight/take/tip/question/meme) selecting the template and visual rule; the cross-cutting prompt rules (no source references, strong hook, URL-aware length with one rewrite-to-fit pass, quote stripping); AI/stock image attachment per the type's visual rule; the **independent post** path (generate a take/etc. with no source item) including the **daily Take** job; the `generated_posts` table (with `post_type`, `priority_score`, and the `post_attempts`/`post_error` state-machine columns); and CLI commands to generate drafts and preview (dry-run) what would be published.

**Phase 4 — Automation**
The X publisher (Tweepy, text-only by default per the cost strategy) with **dry-run as the fail-safe default** and the `post_attempts`/`post_error` retry state machine; the scheduler (APScheduler) with randomized posting windows, **jitter rolled once per day and cached**, **best-at-slot-time** post selection (not pre-assignment) with catch-up, per-type/per-niche/global caps, an overlap guard, and the `manual`/`auto` app mode; the dashboard↔service **command queue** (`commands` table) and **log mirror** (`logs` table); the `post_history` table and cost tracking; remaining source plugins and niche templates (crypto, finance, science, news, business) so every content category is fully populated.

**Phase 5 — Post lifecycle & approval**
The post-lifecycle transitions (edit / regenerate / reject), driven from the dashboard. A draft's status is one of `draft`, `published`, `rejected`, `failed`. On top of that, each niche can opt into an **optional Telegram approval gate** (`approval.py`): when `approval.required` is set, a fresh draft is sent to Telegram for approve/edit/regenerate/reject before it can publish, and `sweep_timeouts` enforces the configured `on_timeout` behavior (auto-post or discard) when no decision arrives in time.

**Phase 6 — Dashboard & API** (added beyond the original plan)
The FastAPI backend the dashboard calls (`opensocial/api.py`); the `serve` command running the API and a background worker together; `resolve_settings` overlaying dashboard runtime toggles (`app_settings`) on the environment; autopilot draft refresh inside the posting window; Fernet-encrypted secrets saved from the dashboard; and the React + Vite dashboard (`dashboard/`).

---

## Verification

- **Phase 1**: Fetching a niche pulls content from its enabled sources, normalizes it to `ContentItem`, and stores it without creating duplicates on repeat runs.
- **Phase 2**: Filtering and de-duplication correctly mark items as candidate/filtered/duplicate, and the resulting candidate queue is ordered by recency, engagement, and sentiment match.
- **Phase 3**: Generating posts for a niche produces draft text via the AI provider that reads as a standalone post (no dangling "read more"/link references), confirming the no-link-by-default design; each draft carries a valid post type with the correct visual rule applied; the daily Take job produces an independent draft (no `content_item_id`) with an image; a dry-run publish step prints what would be posted without calling the real X API; an automated test covers fetch → filter → generate end-to-end using a mocked AI response.
- **Phase 4**: A slot fires at a randomized (once-per-day, cached) time within its niche's posting window, publishes the best-scoring eligible queued post at that moment (not a pre-assigned one), respects the minimum gap and per-type/per-niche/global caps, and records a `post_history` row with the correct cost estimate; with `POST_DRY_RUN` unset nothing is actually published; a failed publish increments `post_attempts` and lands on `failed` after the max; a `post_now` row in `commands` triggers an on-demand publish.
- **Phase 5**: A draft requiring approval is sent to Telegram with working approve/edit/regenerate/reject buttons, and the configured timeout behavior (auto-post or discard) fires correctly.
