# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

OpenX is an open-source, plugin-based AI social media posting automation tool for **X (Twitter)**, with niche-driven content sourcing, AI text generation, image attachment, scheduling with anti-bot randomness, an optional Telegram approval gate, and a React dashboard.

The original architecture plan — tech stack, data model, config format, pipeline, DB schema — lives in [project.md](project.md). It has been updated to reflect what actually shipped, so prefer it over the early git history for intent. The MVP is built: all of the pipeline (fetch → filter → generate → schedule → publish), the FastAPI dashboard backend, and the React dashboard exist.

## Repository Layout

Two independently-deployable projects sharing one SQLite file:

- **[service/](service/)** (Python 3.12) — the automation engine and the dashboard's HTTP API. Owns the pipeline and writes the content/post/history tables.
- **[dashboard/](dashboard/)** (React + Vite) — the configuration & monitoring UI. Talks to the service over HTTP (`/api/*`); it does not touch SQLite directly.

### service/

- `opensocial/cli.py` — the Typer CLI (entry point). Every capability is reachable here, and the `serve` command holds the background worker loop.
- `opensocial/api.py` — FastAPI app for the dashboard (`create_app`).
- `opensocial/core/` — shared internals:
  - `models.py` (`ContentItem` dataclass), `db.py` (SQLAlchemy models + helpers), `config.py` (niche JSON loading), `settings.py` (env + DB runtime settings).
  - `filtering.py` (blocklist/relevance/age + near-dup, candidate queue), `generate.py` (draft generation), `posttypes.py` (post-type taxonomy + visual rules).
  - `scheduler.py` (per-day slot resolution with cached jitter), `engine.py` (publish + best-at-slot-time selection), `approval.py` (Telegram gate + timeout sweep).
  - `commands.py` (dashboard→service command queue + autopilot), `secrets.py` (Fernet credential encryption).
- `opensocial/sources/` — one plugin per source behind the `Source` ABC + `@register` (`base.py`). 10 plugins: `rss`, `hackernews`, `googlenews`, `medium`, `devto`, `arxiv`, `yfinance`, `reddit`, `youtube`, `guardian`. Each plugin class is the single source of truth for its metadata (`label`, `description`, `auth`, `key_envs`, `key_help`, `settings`, origin spec). `api.py` and `Sources.jsx` read it, so don't add per-source tables elsewhere.
- `opensocial/ai/` — `text.py` (LiteLLM + offline template provider), `images.py` (Unsplash / source-media / none), `prompts.py`, `ranking.py`.
- `opensocial/publish/` — `base.py` publisher interface + `x.py` (Tweepy).
- `config/niches/*.json` — 18 niche profiles (the source of truth for what runs).
- `tests/` — pytest suite.

### dashboard/

Vite app under `src/`: `pages/` (Dashboard, Niches, Queue, Schedule, Sources, History, Logs, Settings, RawData), `components/`, `api.js` (fetch wrapper), `AppContext.jsx`.

## Common Commands

Run from `service/` (`python -m opensocial …` and `python main.py …` are equivalent):

```bash
# Pipeline (per niche or all niches)
python main.py fetch --niche tech        # poll sources, store deduped content
python main.py filter --niche tech       # mark candidate/filtered/duplicate
python main.py queue --niche tech        # show prioritized candidate queue
python main.py generate --niche tech     # write draft posts (+ daily independent take)
python main.py preview --niche tech      # dry-run generation, write nothing
python main.py publish --niche tech      # publish slots due now (one-shot)

# Automation
python main.py run --interval 60         # blocking scheduler loop
python main.py serve -v                  # dashboard API + background worker in one process
python main.py commands                  # drain the dashboard command queue once

# Accounts / secrets
python main.py keygen                    # print a Fernet key for OPENSOCIAL_SECRET_KEY
python main.py account-add --label main  # enroll an X account (reads X_* env vars, encrypts)
python main.py account-list              # also: account-rm, workspace-list, workspace-rm
python main.py niche-account             # assign a niche to an owning account
python main.py post-now <generated_post_id>

python main.py sources                   # list registered source plugins

pytest                                   # run the test suite
pytest tests/test_filtering.py -k near_dup   # single file / single test
```

Dashboard: `cd dashboard && npm install && npm run dev` (Vite on `127.0.0.1:5174`, proxies `/api` to the service on `127.0.0.1:8765`; `npm run build` for production). There is no linter or dashboard test setup.

One-command bootstrap from the repo root: `python setup.py` creates `service/.venv`, installs deps, and runs `serve` + the Vite dev server together.

## Architecture: Dashboard → Worker Command Queue

The API doesn't run pipeline work inline. Dashboard actions (fetch, generate, run slots, post now, regenerate) go to `POST /api/commands` and are stored as rows in the `commands` table (`enqueue_command`). The `serve` background thread ticks every `--interval` seconds (default 5) and runs these steps in order: `process_commands` (claim and execute pending commands), `autopilot_refresh` (top up drafts inside the posting window for `auto`-mode workspaces), `sweep_timeouts` (Telegram approval timeouts), then publishes due slots for every workspace. `python main.py commands` drains the queue once without the server. A new dashboard action means adding a command type to `process_commands` in `core/commands.py`, not a long-running API handler.

## Architecture: Workspaces

A **workspace = one X account** (`PlatformAccount`). Niches are a *shared catalog*; a workspace *follows* niches and its drafts/history are isolated by `GeneratedPost.platform_account_id`. Per-workspace settings live in `app_settings` under `ws:<workspace_id>:*` keys (dry_run, app_mode, AI config, followed niches, secrets), with a legacy global-key fallback. Niche JSON can carry an `account_id` owner. Anything touching posts, settings, or throttling (e.g. the min-gap check) must be scoped to the workspace; see `delete_workspace` / `reset_workspace` in `core/db.py` and `tests/test_workspaces.py` for the isolation rules. `ensure_default_workspace` guarantees at least one exists.

## Conventions & Gotchas

- **Niche config is JSON** (`config/niches/<slug>.json`), not YAML — despite the `niche_profiles.config_yaml` column name kept for back-compat. CLI defaults assume `config_dir=config/niches` and `db=opensocial.db`.
- **Publishing is dry-run by default.** It goes live only when `POST_DRY_RUN` is an explicit falsey string (`false`/`0`/`off`); anything else stays dry. `APP_MODE` is `manual` by default (`auto` lets slots publish unattended). The dashboard can override `dry_run`/`app_mode`/caps at runtime via the `app_settings` table without a restart (`resolve_settings`).
- **No AI image generation.** Pollinations/DALL·E were removed; images come only from Unsplash, the source item's own media (`image_source: content`), or none. Some niche JSON still carries a stale `ai.image` block — the per-niche `image_source` field is what's read.
- **Sources removed:** GDELT, Finnhub, Product Hunt, NASA and GitHub Releases. Don't reintroduce them. Google News RSS replaced GDELT as the universal feed.
- **Adding a source:** subclass `Source`, decorate with `@register` (unique `name`), fill in its metadata attributes (`tests/test_sources_meta.py` enforces them), *and* import the module in `sources/__init__.py`. Registration happens at import time, so a plugin that is never imported won't appear in the registry.
- **Sources start disabled on a fresh DB** (no `source_configs` row means off). A fetch that runs nothing fails with the reason (turned off / missing required key / no followed niche) rather than reporting "no changes".
- **One bad source never sinks a run** — fetch catches per-source exceptions. Keep that pattern when adding sources.
- Primary dev platform is Windows/PowerShell (the `tzdata` dependency exists for that); macOS also works. Run service commands from the `service/` directory.
