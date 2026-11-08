"""Dashboard HTTP API tests (FastAPI TestClient — no server, no network).

Covers the runtime-settings overlay, the read endpoints' shapes, the approval
transitions over HTTP, niche config, the account cadence, voice samples, the knowledge base, source toggles, the
command queue, and credential storage (env secrets + X account enrollment).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from opensocial.api import create_app, inject_stored_secrets
from opensocial.core.db import insert_generated_post, store_items
from opensocial.core.models import ContentItem
from opensocial.core.settings import resolve_settings

NICHE = "tech"

NICHE_CONFIG = {
    "slug": NICHE,
    "display_name": "Technology",
    "enabled": True,
    "persona": {"voice": "test voice"},
    "post_types": {"news": {"enabled": True}},
    "schedule": {
        "windows": [["09:00", "12:00"]],
        "posts_per_day": [2, 2],
        "min_gap_minutes": 30,
    },
    "sources": {"hackernews": {"limit": 5}},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("POST_DRY_RUN", raising=False)
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("OPENSOCIAL_SECRET_KEY", raising=False)
    monkeypatch.setenv("OPENSOCIAL_KEYFILE", str(tmp_path / "test.key"))
    config_dir = tmp_path / "niches"
    config_dir.mkdir()
    (config_dir / f"{NICHE}.json").write_text(json.dumps(NICHE_CONFIG))
    # Most API tests assert on a clean slate; the Default-workspace seeding is
    # covered by its own test below.
    app = create_app(tmp_path / "test.db", config_dir, seed_default=False)
    return TestClient(app)


def _add_post(client, **over):
    sf = client.app.state.session_factory
    with sf() as s:
        post = insert_generated_post(
            s,
            niche_slug=over.pop("niche_slug", NICHE),
            post_type=over.pop("post_type", "news"),
            text=over.pop("text", "a test post"),
            ai_text_provider="template",
            **over,
        )
        s.commit()
        return post.id


# --- status & runtime settings --------------------------------------------


def test_status_defaults_fail_safe(client):
    body = client.get("/api/status").json()
    assert body["dry_run"] is True  # fail-safe: dry unless explicitly off
    assert body["app_mode"] == "manual"
    assert body["published_today"] == 0


def test_settings_patch_persists_and_resolves(client):
    body = client.patch(
        "/api/settings", json={"dry_run": False, "app_mode": "auto"}
    ).json()
    assert body["dry_run"] is False and body["app_mode"] == "auto"

    # the worker resolves the same values from the DB
    with client.app.state.session_factory() as s:
        settings = resolve_settings(s)
    assert settings.dry_run is False and settings.app_mode == "auto"


def test_settings_patch_rejects_bad_mode(client):
    assert client.patch("/api/settings", json={"app_mode": "yolo"}).status_code == 422


# --- posts & lifecycle transitions -----------------------------------------


def test_posts_list_and_transitions(client):
    pid = _add_post(client, status="draft")

    posts = client.get("/api/posts").json()
    assert [p["id"] for p in posts] == [pid]
    assert posts[0]["status"] == "draft"
    assert posts[0]["independent"] is True

    # edit keeps the post a publishable draft; reject is terminal.
    body = client.post(f"/api/posts/{pid}/edit", json={"text": "better text"}).json()
    assert body["status"] == "draft" and body["text"] == "better text"
    assert client.post(f"/api/posts/{pid}/reject").json()["status"] == "rejected"
    assert client.post("/api/posts/nope/reject").status_code == 404


def test_posts_filter_by_niche_and_status(client):
    _add_post(client, status="draft")
    _add_post(client, status="rejected")
    assert len(client.get("/api/posts?status=draft").json()) == 1
    assert len(client.get("/api/posts?status=rejected").json()) == 1
    assert len(client.get(f"/api/posts?niche={NICHE}").json()) == 2
    assert client.get("/api/posts?niche=other").json() == []


# --- raw fetched content ----------------------------------------------------


def _store_item(client, **over):
    item = ContentItem(
        source_name=over.pop("source_name", "hackernews"),
        source_category=over.pop("source_category", "tech"),
        title=over.pop("title", "A fetched story"),
        url=over.pop("url", "https://example.com/a"),
        published_at=over.pop("published_at", __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )),
        engagement=over.pop("engagement", {"score": 42}),
        raw_metadata=over.pop("raw_metadata", {"objectID": "1"}),
        **over,
    )
    with client.app.state.session_factory() as s:
        store_items(s, [item], over.get("niche_slug", NICHE))
    return item


def test_content_returns_raw_items(client):
    _store_item(client, title="Hello world", raw_metadata={"objectID": "9"})
    body = client.get("/api/content").json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["source"] == "hackernews"
    assert row["title"] == "Hello world"
    assert row["raw_metadata"] == {"objectID": "9"}
    assert row["engagement"] == {"score": 42}
    assert {n["niche"] for n in row["niches"]} == {NICHE}


def test_content_filters(client):
    _store_item(client, source_name="hackernews", url="https://example.com/hn", title="HN item")
    _store_item(client, source_name="reddit", url="https://example.com/rd", title="Reddit item")
    assert client.get("/api/content").json()["total"] == 2
    assert client.get("/api/content?source=reddit").json()["total"] == 1
    assert client.get("/api/content?niche=other").json()["total"] == 0
    assert client.get("/api/content?q=reddit").json()["total"] == 1
    # comma-separated niche list: a real slug plus a bogus one still matches.
    assert client.get(f"/api/content?niche={NICHE},nope").json()["total"] == 2
    assert client.get("/api/content?niche=nope,alsonope").json()["total"] == 0


def test_content_paginates(client):
    for i in range(5):
        _store_item(client, url=f"https://example.com/{i}", title=f"Item {i}")
    page1 = client.get("/api/content?limit=2&offset=0").json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page3 = client.get("/api/content?limit=2&offset=4").json()
    assert page3["total"] == 5
    assert len(page3["items"]) == 1
    # offsets across pages return disjoint rows
    seen = {r["id"] for r in page1["items"]} | {r["id"] for r in page3["items"]}
    assert len(seen) == 3


# --- overview / schedule ----------------------------------------------------


def test_overview_shape(client):
    _add_post(client, status="draft")
    client.put("/api/niches/followed", json={"slugs": [NICHE]})
    client.patch("/api/sources/hackernews", json={"enabled": True})  # funnel needs an enabled source
    body = client.get("/api/overview").json()
    assert body["pending_total"] == 1
    funnel = {f["slug"]: f for f in body["funnel"]}
    assert NICHE in funnel


def test_overview_source_count_is_all_plugins(client):
    """sources_total counts every installed plugin, not just niche-referenced ones."""
    from opensocial.sources import available_sources

    body = client.get("/api/overview").json()
    total = len(available_sources())
    assert body["sources_total"] == total
    # Fresh DB → every source starts disabled until the user enables one.
    assert body["sources_active"] == 0


def test_overview_funnel_hides_untracked_niches(client, tmp_path):
    # selected niche with no sources → excluded (nothing ingesting)
    (tmp_path / "niches" / "empty.json").write_text(
        json.dumps({"slug": "empty", "display_name": "Empty", "enabled": True, "sources": {}})
    )
    # selected niche with no enabled sources (source explicitly disabled) → excluded
    (tmp_path / "niches" / "dead.json").write_text(
        json.dumps({"slug": "dead", "display_name": "Dead", "enabled": True,
                    "sources": {"hackernews": {"enabled": False}}})
    )
    # a niche with a good source but NOT selected → excluded (selection gates)
    (tmp_path / "niches" / "off.json").write_text(
        json.dumps({"slug": "off", "display_name": "Off", "enabled": True,
                    "sources": {"hackernews": {"limit": 5}}})
    )
    client.put("/api/niches/followed", json={"slugs": [NICHE, "empty", "dead"]})
    client.patch("/api/sources/hackernews", json={"enabled": True})  # enable the shared source
    slugs = {f["slug"] for f in client.get("/api/overview").json()["funnel"]}
    assert NICHE in slugs        # selected + has enabled source → shown
    assert "empty" not in slugs  # selected but no sources
    assert "dead" not in slugs   # selected but source explicitly off
    assert "off" not in slugs    # has source but not selected


def _account(client, label="main"):
    return client.post("/api/accounts", json={"label": label}).json()["id"]


def test_schedule_returns_account_cadence(client):
    acct = _account(client)
    client.put(f"/api/niches/followed?account={acct}", json={"slugs": [NICHE]})
    body = client.get(f"/api/schedule?account={acct}").json()
    # Seeded from the followed niche's legacy window on first read.
    assert body["cadence"]["window"] == ["09:00", "12:00"]
    assert body["cadence"]["every_minutes"] == 60
    assert body["mode"] == "manual" and body["dry_run"] is True
    assert body["posted_today"] == [] and body["pending_drafts"] == 0
    assert len(body["window_today"]) == 2


def test_schedule_put_saves_cadence_and_validates(client):
    acct = _account(client)
    res = client.put(
        f"/api/schedule?account={acct}",
        json={"window": ["08:00", "22:00"], "every_minutes": 30, "jitter_pct": 25,
              "min_gap_minutes": 15, "max_pending_drafts": 4},
    )
    assert res.status_code == 200
    cad = res.json()["cadence"]
    assert cad == {"window": ["08:00", "22:00"], "every_minutes": 30, "jitter_pct": 25,
                   "min_gap_minutes": 15}
    assert client.get(f"/api/schedule?account={acct}").json()["cadence"]["every_minutes"] == 30
    bad = {"window": ["22:00", "08:00"], "every_minutes": 30}
    assert client.put(f"/api/schedule?account={acct}", json=bad).status_code == 422
    bad = {"window": ["8am", "10"], "every_minutes": 30}
    assert client.put(f"/api/schedule?account={acct}", json=bad).status_code == 422
    bad = {"window": ["08:00", "22:00"], "every_minutes": 1}
    assert client.put(f"/api/schedule?account={acct}", json=bad).status_code == 422


def test_old_per_niche_schedule_endpoints_are_gone(client):
    assert client.post("/api/schedule/randomize", json={}).status_code in (404, 405)
    assert client.delete(f"/api/niches/{NICHE}/schedule").status_code in (404, 405)


def test_voice_samples_default_save_and_reset(client):
    acct = _account(client)
    body = client.get(f"/api/voice?account={acct}").json()
    assert body["default"] is True and len(body["samples"]) == 5
    saved = client.put(f"/api/voice?account={acct}",
                       json={"samples": ["  one  ", "", "two", "one"]}).json()
    assert saved == {"samples": ["one", "two"], "default": False}
    other = _account(client, "other")
    assert client.get(f"/api/voice?account={other}").json()["default"] is True  # isolated
    assert client.delete(f"/api/voice?account={acct}").json()["default"] is True


def test_stories_endpoint_lists_the_shortlist(client):
    from datetime import datetime, timezone

    from opensocial.core.filtering import filter_niche
    from opensocial.core.knowledge import ingest

    acct = _account(client)
    client.put(f"/api/niches/followed?account={acct}", json={"slugs": [NICHE]})
    sf = client.app.state.session_factory
    with sf() as s:
        store_items(s, [ContentItem(
            source_name="hackernews", source_category="tech", title="Open model ships",
            url="https://a.com/1", published_at=datetime.now(timezone.utc),
        )], NICHE)
        filter_niche(s, NICHE, NICHE_CONFIG)
        ingest(s)
    body = client.get(f"/api/stories?account={acct}").json()
    assert body["total_stories"] == 1
    assert body["shortlist"][0]["title"] == "Open model ships"
    assert "coverage" in body["shortlist"][0]["signals"]
    assert body["used"] == []


def test_niche_put_roundtrip(client, tmp_path):
    cfg = client.get(f"/api/niches/{NICHE}").json()
    cfg["persona"]["voice"] = "new voice"
    assert client.put(f"/api/niches/{NICHE}", json=cfg).status_code == 200
    assert client.get(f"/api/niches/{NICHE}").json()["persona"]["voice"] == "new voice"
    # slug mismatch rejected
    cfg["slug"] = "other"
    assert client.put(f"/api/niches/{NICHE}", json=cfg).status_code == 422


# --- sources -----------------------------------------------------------------


def test_sources_list_and_toggle(client):
    sources = client.get("/api/sources").json()
    hn = next(s for s in sources if s["id"] == "hackernews")
    assert hn["enabled"] is False and NICHE in hn["niches"]  # fresh DB → disabled

    # enable, confirm, then disable again
    assert client.patch("/api/sources/hackernews", json={"enabled": True}).json()["enabled"] is True
    sources = client.get("/api/sources").json()
    assert next(s for s in sources if s["id"] == "hackernews")["enabled"] is True
    assert client.patch("/api/sources/hackernews", json={"enabled": False}).json()["enabled"] is False
    assert client.patch("/api/sources/nope", json={"enabled": True}).status_code == 404


def test_upcoming_source_is_flagged_and_locked(client):
    sources = {s["id"]: s for s in client.get("/api/sources").json()}
    yt = sources["youtube"]
    assert yt["upcoming"] is True
    # an upcoming source can't be enabled, and stays disabled
    assert client.patch("/api/sources/youtube", json={"enabled": True}).status_code == 409
    yt = next(s for s in client.get("/api/sources").json() if s["id"] == "youtube")
    assert yt["enabled"] is False
    # disabling (the no-op direction) is still allowed
    assert client.patch("/api/sources/youtube", json={"enabled": False}).status_code == 200


def test_sources_static_vs_dynamic(client):
    sources = {s["id"]: s for s in client.get("/api/sources").json()}
    # static: fixed origin, no add-origin affordance
    assert sources["hackernews"]["kind"] == "static"
    assert sources["hackernews"]["origin_label"] is None
    # dynamic: carries an origin label + (empty) origins list
    assert sources["reddit"]["kind"] == "dynamic"
    assert sources["reddit"]["origin_label"]
    assert sources["reddit"]["origins"] == []
    assert sources["hackernews"]["category"] == "tech"


def test_sources_describe_key_requirement(client, monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    sources = {s["id"]: s for s in client.get("/api/sources").json()}
    hn, guardian = sources["hackernews"], sources["guardian"]
    assert hn["auth"] == "none" and hn["key"] is None and hn["needs_key"] is False
    assert hn["description"] and {s["name"] for s in hn["settings"]} >= {"query", "limit"}
    assert guardian["auth"] == "required" and guardian["needs_key"] is True and guardian["key_help"]
    assert guardian["key_envs"] == ["GUARDIAN_API_KEY"]
    # source keys in the credentials list come from the registry
    platforms = {g["platform"] for g in client.get("/api/credentials").json()}
    assert {"The Guardian", "YouTube"} <= platforms
    assert not {"Product Hunt", "NASA", "GitHub", "GitHub Releases"} & platforms


def test_origin_add_and_remove_roundtrip(client, tmp_path):
    # add a subreddit origin to the tech niche from a full reddit URL
    res = client.post(
        "/api/sources/reddit/origins",
        json={"niche": NICHE, "url": "https://www.reddit.com/r/programming/"},
    )
    assert res.status_code == 200 and res.json()["value"] == "programming"

    on_disk = json.loads((tmp_path / "niches" / f"{NICHE}.json").read_text())
    assert on_disk["sources"]["reddit"]["subreddits"] == ["programming"]
    # block seeded with sensible defaults
    assert on_disk["sources"]["reddit"]["sort"] == "hot"

    # it surfaces as an origin on the source listing
    reddit = next(s for s in client.get("/api/sources").json() if s["id"] == "reddit")
    assert reddit["origins"] == [
        {"niche": NICHE, "value": "programming", "display": "r/programming"}
    ]

    # edit the origin in place (new subreddit, same niche)
    edited = client.put(
        "/api/sources/reddit/origins",
        json={"niche": NICHE, "value": "programming", "url": "r/rust"},
    )
    assert edited.status_code == 200 and edited.json()["value"] == "rust"
    on_disk = json.loads((tmp_path / "niches" / f"{NICHE}.json").read_text())
    assert on_disk["sources"]["reddit"]["subreddits"] == ["rust"]

    # removing the last origin drops the empty source block
    assert client.request(
        "DELETE", "/api/sources/reddit/origins",
        json={"niche": NICHE, "value": "rust"},
    ).status_code == 200
    on_disk = json.loads((tmp_path / "niches" / f"{NICHE}.json").read_text())
    assert "reddit" not in on_disk["sources"]


def test_origin_add_capped_at_max(client):
    # fill reddit up to the 10-origin cap, then expect the 11th to be rejected
    for i in range(10):
        res = client.post(
            "/api/sources/reddit/origins",
            json={"niche": NICHE, "url": f"r/sub{i}"},
        )
        assert res.status_code == 200
    reddit = next(s for s in client.get("/api/sources").json() if s["id"] == "reddit")
    assert len(reddit["origins"]) == 10
    assert reddit["max_origins"] == 10

    over = client.post(
        "/api/sources/reddit/origins", json={"niche": NICHE, "url": "r/onemore"}
    )
    assert over.status_code == 422
    # editing an existing origin still works at the cap (remove + re-add nets even)
    assert client.put(
        "/api/sources/reddit/origins",
        json={"niche": NICHE, "value": "sub0", "url": "r/renamed"},
    ).status_code == 200


def test_origin_rejects_unparseable_and_non_dynamic(client):
    # static source has no origins endpoint
    assert client.post(
        "/api/sources/hackernews/origins", json={"niche": NICHE, "url": "x"}
    ).status_code == 404
    # garbage that yields no subreddit
    assert client.post(
        "/api/sources/reddit/origins", json={"niche": NICHE, "url": "https://reddit.com/"}
    ).status_code == 422


# --- global AI config --------------------------------------------------------


def test_ai_config_default_and_put(client):
    body = client.get("/api/ai").json()
    assert body["text"]["provider"] == "local"
    # AI image generation was removed — config is text-only now.
    assert "image" not in body
    # key presence is surfaced for the inline key fields (AI keys + the
    # per-workspace Unsplash image key)
    assert set(body["key_status"]) == {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "DEEPSEEK_API_KEY",
        "LOCAL_LLM_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "CUSTOM_LLM_API_KEY",
        "UNSPLASH_ACCESS_KEY",
    }

    saved = client.put(
        "/api/ai",
        json={"text": {"provider": "template", "model": "gpt-4o-mini"}},
    ).json()
    assert saved["text"]["provider"] == "template"
    assert saved["text"]["model"] == "gpt-4o-mini"
    # unspecified fields fall back to defaults
    assert saved["text"]["temperature"] == 0.7
    assert "image" not in saved


def test_ai_local_provider_requires_endpoint(client):
    # local provider with a blank endpoint is rejected
    res = client.put(
        "/api/ai",
        json={"text": {"provider": "local", "endpoint": ""}},
    )
    assert res.status_code == 422
    # with an endpoint it saves fine
    ok = client.put(
        "/api/ai",
        json={"text": {"provider": "local", "endpoint": "http://localhost:11434"}},
    )
    assert ok.status_code == 200
    assert ok.json()["text"]["endpoint"] == "http://localhost:11434"


def test_ai_providers_registry(client):
    providers = {p["id"]: p for p in client.get("/api/ai/providers").json()}
    assert {"claude", "chatgpt", "gemini", "local", "template"} <= set(providers)
    # local takes an optional key and needs an endpoint
    assert providers["local"]["key_env"] == "LOCAL_LLM_API_KEY"
    assert providers["local"]["key_required"] is False
    assert providers["local"]["endpoint_required"] is True
    assert providers["template"]["key_env"] is None
    # an unknown provider is rejected
    assert client.put("/api/ai", json={"text": {"provider": "nope"}}).status_code == 422


def test_ai_test_hosted_provider_needs_key(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = client.post("/api/ai/test", json={"text": {"provider": "gemini"}}).json()
    assert res["ok"] is False and "GEMINI_API_KEY" in res["error"]


def test_qualify_model_adds_provider_route():
    from opensocial.ai.text import qualify_model

    assert qualify_model("claude", "claude-haiku-5-5") == "anthropic/claude-haiku-5-5"
    assert qualify_model("claude", "anthropic/claude-haiku-5-5") == "anthropic/claude-haiku-5-5"
    assert qualify_model("openrouter", "meta-llama/llama-3.3-70b-instruct") == (
        "openrouter/meta-llama/llama-3.3-70b-instruct"
    )
    assert qualify_model("local", "ollama/gemma3:4b") == "ollama/gemma3:4b"
    # custom: OpenAI-compatible unless the id already names a LiteLLM route
    assert qualify_model("custom", "my-model") == "openai/my-model"
    assert qualify_model("custom", "meta-llama/Llama-3.3-70B") == "openai/meta-llama/Llama-3.3-70B"
    assert qualify_model("custom", "together_ai/x/y") == "together_ai/x/y"


def test_custom_provider_needs_endpoint_and_model(client):
    providers = {p["id"]: p for p in client.get("/api/ai/providers").json()}
    custom = providers["custom"]
    assert custom["key_env"] == "CUSTOM_LLM_API_KEY" and custom["key_required"] is False
    assert client.put("/api/ai", json={"text": {"provider": "custom", "model": "m"}}).status_code == 422
    assert client.put(
        "/api/ai", json={"text": {"provider": "custom", "endpoint": "https://x.test/v1"}}
    ).status_code == 422
    ok = client.put(
        "/api/ai",
        json={"text": {"provider": "custom", "endpoint": "https://x.test/v1", "model": "m"}},
    )
    assert ok.status_code == 200 and ok.json()["text"]["provider"] == "custom"


def test_cloudflare_provider_builds_endpoint_from_account_id(client):
    from opensocial.ai.text import get_text_provider

    acct = "f" * 32
    # account id is required
    assert client.put("/api/ai", json={"text": {"provider": "cloudflare"}}).status_code == 422
    # pasted as a dashboard URL, it is stored as the bare id
    saved = client.put(
        "/api/ai",
        json={"text": {"provider": "cloudflare", "account_id": f"https://dash.cloudflare.com/{acct}"
                       .replace("dash.cloudflare.com/", "dash.cloudflare.com/accounts/")}},
    ).json()["text"]
    assert saved["account_id"] == acct
    # no inherited localhost endpoint or ollama model from the local default
    assert "endpoint" not in saved and "model" not in saved

    p = get_text_provider({"ai": {"text": {**saved, "api_key": "t"}}})
    assert p.api_base == f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1"
    assert p.model == "openai/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    # an explicit base URL (AI Gateway) wins over the template
    gw = get_text_provider({"ai": {"text": {**saved, "endpoint": "https://gw.test/v1", "model": "@cf/qwen/qwen3.8-27b"}}})
    assert gw.api_base == "https://gw.test/v1" and gw.model == "openai/@cf/qwen/qwen3.8-27b"


def test_custom_provider_builds_keyless_openai_route():
    from opensocial.ai.text import get_text_provider

    p = get_text_provider(
        {"ai": {"text": {"provider": "custom", "endpoint": "https://x.test/v1", "model": "m"}}}
    )
    assert p.model == "openai/m" and p.api_base == "https://x.test/v1" and p.api_key == "local"


def test_settings_patch_install_limits(client):
    body = client.patch(
        "/api/settings", json={"global_daily_cap": 12, "max_post_attempts": 5}
    ).json()
    assert body["global_daily_cap"] == 12
    assert body["max_post_attempts"] == 5
    assert client.patch("/api/settings", json={"max_post_attempts": 0}).status_code == 422
    assert client.patch("/api/settings", json={"global_daily_cap": -1}).status_code == 422


# --- commands -----------------------------------------------------------------


def test_command_enqueue_and_poll(client):
    row = client.post(
        "/api/commands", json={"type": "generate_posts", "payload": {"niche": NICHE}}
    ).json()
    assert row["status"] == "pending"
    fetched = client.get(f"/api/commands?ids={row['id']}").json()
    assert fetched[0]["type"] == "generate_posts"
    assert client.post("/api/commands", json={"type": "rm -rf"}).status_code == 422


# --- credentials -----------------------------------------------------------------


def test_env_secret_set_encrypted_and_injected(client, tmp_path, monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    assert client.post(
        "/api/credentials", json={"key": "GUARDIAN_API_KEY", "value": "sek-123"}
    ).json()["set"] is True
    # keyfile auto-created; value encrypted in app_settings, env updated
    assert (tmp_path / "test.key").exists()
    import os

    assert os.environ["GUARDIAN_API_KEY"] == "sek-123"

    groups = client.get("/api/credentials").json()
    guardian = next(g for g in groups if g["platform"] == "The Guardian")
    assert guardian["keys"][0]["set"] is True

    # unknown env names rejected
    assert client.post(
        "/api/credentials", json={"key": "PATH", "value": "x"}
    ).status_code == 422

    # a fresh process re-injects from the DB
    monkeypatch.delenv("GUARDIAN_API_KEY")
    assert inject_stored_secrets(client.app.state.session_factory) == 1
    assert os.environ["GUARDIAN_API_KEY"] == "sek-123"
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)


def test_x_account_enroll(client):
    body = client.post(
        "/api/credentials/x",
        json={
            "label": "main",
            "api_key": "k", "api_secret": "s",
            "access_token": "t", "access_token_secret": "ts",
        },
    ).json()
    assert body["set"] is True
    groups = client.get("/api/credentials").json()
    x = next(g for g in groups if g["type"] == "x_account")
    assert x["accounts"] == ["main"] and all(k["set"] for k in x["keys"])


def test_create_workspace_by_name(client):
    # Name-first creation: a workspace exists immediately, no credentials yet.
    row = client.post("/api/accounts", json={"label": "Crypto brand", "daily_post_cap": 5}).json()
    assert row["label"] == "Crypto brand" and row["daily_post_cap"] == 5 and row["id"]
    accounts = client.get("/api/accounts").json()
    assert any(a["id"] == row["id"] and a["label"] == "Crypto brand" for a in accounts)


def test_create_workspace_rejects_blank_and_duplicate(client):
    assert client.post("/api/accounts", json={"label": "   "}).status_code == 422
    client.post("/api/accounts", json={"label": "Dup"})
    assert client.post("/api/accounts", json={"label": "Dup"}).status_code == 409


def test_default_workspace_is_seeded(tmp_path):
    config_dir = tmp_path / "niches"
    config_dir.mkdir()
    (config_dir / f"{NICHE}.json").write_text(json.dumps(NICHE_CONFIG))
    app = create_app(tmp_path / "seed.db", config_dir)  # seeding on by default
    client = TestClient(app)
    accounts = client.get("/api/accounts").json()
    assert [a["label"] for a in accounts] == ["Default"]
    # Re-opening doesn't create a second Default.
    client2 = TestClient(create_app(tmp_path / "seed.db", config_dir))
    assert len(client2.get("/api/accounts").json()) == 1


def test_ai_keys_are_per_workspace(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    a = client.post("/api/accounts", json={"label": "A"}).json()["id"]
    b = client.post("/api/accounts", json={"label": "B"}).json()["id"]
    # set an AI key for workspace A only (scoped, not global env)
    r = client.post("/api/credentials?account=" + a, json={"key": "OPENAI_API_KEY", "value": "sk-a"})
    assert r.status_code == 200
    assert client.get("/api/ai?account=" + a).json()["key_status"]["OPENAI_API_KEY"] is True
    assert client.get("/api/ai?account=" + b).json()["key_status"]["OPENAI_API_KEY"] is False
    # scoped AI keys never leak into the process env
    import os
    assert os.environ.get("OPENAI_API_KEY") is None

    # removing A's key clears it for A only
    r = client.delete(f"/api/credentials?key=OPENAI_API_KEY&account={a}").json()
    assert r["removed"] is True and r["set"] is False
    assert client.get("/api/ai?account=" + a).json()["key_status"]["OPENAI_API_KEY"] is False
    assert client.delete("/api/credentials?key=PATH").status_code == 422


def test_global_credential_delete_drops_env(client, monkeypatch):
    import os

    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    client.post("/api/credentials", json={"key": "GUARDIAN_API_KEY", "value": "sek-del"})
    assert os.environ["GUARDIAN_API_KEY"] == "sek-del"
    r = client.delete("/api/credentials?key=GUARDIAN_API_KEY").json()
    assert r["removed"] is True and r["set"] is False
    assert "GUARDIAN_API_KEY" not in os.environ


def test_x_credentials_are_per_workspace(client):
    wid = client.post("/api/accounts", json={"label": "WSX"}).json()["id"]
    assert any(a["id"] == wid and a["configured"] is False for a in client.get("/api/accounts").json())
    r = client.post(
        f"/api/accounts/{wid}/credentials",
        json={"api_key": "k", "api_secret": "s", "access_token": "t", "access_token_secret": "ts"},
    )
    assert r.json()["configured"] is True
    assert any(a["id"] == wid and a["configured"] is True for a in client.get("/api/accounts").json())


def test_niches_are_a_shared_catalog_with_per_workspace_selection(client):
    # Niches are shared: every workspace sees the whole catalog and selecting
    # one only updates *that* workspace's followed list (no ownership claim), so
    # two workspaces can follow the same niche and generate independently.
    a = client.post("/api/accounts", json={"label": "A"}).json()["id"]
    b = client.post("/api/accounts", json={"label": "B"}).json()["id"]

    def followed(acct):
        return set(client.get(f"/api/niches/followed?account={acct}").json()["followed"])

    def catalog(acct):
        return {n["slug"] for n in client.get(f"/api/niches?account={acct}").json()}

    # Both workspaces see the identical full catalog.
    assert catalog(a) == catalog(b)
    assert NICHE in catalog(a)

    # Selecting in A follows it for A only — never stamps ownership and never
    # affects B's (empty) selection.
    client.put("/api/niches/followed?account=" + a, json={"slugs": [NICHE]})
    assert followed(a) == {NICHE}
    assert followed(b) == set()
    assert client.get(f"/api/niches/{NICHE}").json().get("account_id") is None

    # B can follow the *same* niche; A keeps it. Both now follow it.
    client.put("/api/niches/followed?account=" + b, json={"slugs": [NICHE]})
    assert followed(a) == {NICHE}
    assert followed(b) == {NICHE}

    # The /api/niches followed flag is per workspace.
    a_view = {n["slug"]: n["followed"] for n in client.get(f"/api/niches?account={a}").json()}
    assert a_view[NICHE] is True

    # Deselecting in A leaves B untouched (no shared release).
    client.put("/api/niches/followed?account=" + a, json={"slugs": []})
    assert followed(a) == set()
    assert followed(b) == {NICHE}


def test_queue_is_isolated_per_workspace_for_a_shared_niche(client):
    # Two workspaces follow the same shared niche; each one's queue and pending
    # count show only its own drafts (isolated by platform_account_id), so a
    # shared niche never leaks drafts between workspaces.
    a = client.post("/api/accounts", json={"label": "A"}).json()["id"]
    b = client.post("/api/accounts", json={"label": "B"}).json()["id"]
    client.put("/api/niches/followed?account=" + a, json={"slugs": [NICHE]})
    client.put("/api/niches/followed?account=" + b, json={"slugs": [NICHE]})

    pa = _add_post(client, status="draft", platform_account_id=a)
    pb = _add_post(client, status="draft", platform_account_id=b)

    assert [p["id"] for p in client.get("/api/posts?account=" + a).json()] == [pa]
    assert [p["id"] for p in client.get("/api/posts?account=" + b).json()] == [pb]
    assert client.get("/api/status?account=" + a).json()["queue_pending"] == 1
    assert client.get("/api/status?account=" + b).json()["queue_pending"] == 1
    # Unscoped (admin) view still sees both drafts.
    assert len(client.get("/api/posts").json()) == 2


def test_reset_workspace_clears_only_its_data(client):
    acct = client.post("/api/accounts", json={"label": "WS"}).json()
    wid = acct["id"]
    # a draft owned by the workspace + a per-workspace setting
    _add_post(client, status="draft", platform_account_id=wid)
    client.patch("/api/settings?account=" + wid, json={"dry_run": False})
    assert client.get("/api/status?account=" + wid).json()["dry_run"] is False

    r = client.post(f"/api/accounts/{wid}/reset").json()
    assert r["ok"] is True and r["deleted"]["generated_posts"] == 1
    # workspace still exists; its settings reverted to default (dry-run on)
    assert any(a["id"] == wid for a in client.get("/api/accounts").json())
    assert client.get("/api/status?account=" + wid).json()["dry_run"] is True


def test_reset_requires_confirm(client):
    _add_post(client, status="draft")
    assert client.post("/api/reset", json={"confirm": False}).status_code == 422
    # data survives a rejected reset
    assert client.get("/api/overview").json()["pending_total"] == 1


def test_reset_clears_data_keeps_credentials(client, monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_KEY", "sek-keep")
    client.post("/api/credentials", json={"key": "GUARDIAN_API_KEY", "value": "sek-keep"})
    _add_post(client, status="draft")

    body = client.post("/api/reset", json={"confirm": True}).json()
    assert body["ok"] is True and body["cleared_credentials"] is False
    assert body["deleted"]["generated_posts"] == 1
    assert client.get("/api/overview").json()["pending_total"] == 0
    # credentials untouched unless explicitly cleared
    assert inject_stored_secrets(client.app.state.session_factory) >= 0
    creds = client.get("/api/credentials").json()
    guardian = next(g for g in creds if g["platform"] == "The Guardian")
    assert all(k["set"] for k in guardian["keys"])


def test_reset_clears_selected_niches_and_sources(client):
    # turn things on, then reset → everything back to the fresh-DB baseline
    client.put("/api/niches/followed", json={"slugs": [NICHE]})
    client.patch("/api/sources/hackernews", json={"enabled": True})

    body = client.post("/api/reset", json={"confirm": True}).json()
    assert body["deleted"].get("followed_niches") == 1

    assert client.get("/api/niches/followed").json()["followed"] == []
    hn = next(s for s in client.get("/api/sources").json() if s["id"] == "hackernews")
    assert hn["enabled"] is False
    assert client.get("/api/overview").json()["setup"]["needs_setup"] is True


def test_reset_clear_credentials_wipes_secrets(client, monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_KEY", "sek-gone")
    client.post("/api/credentials", json={"key": "GUARDIAN_API_KEY", "value": "sek-gone"})
    client.post(
        "/api/credentials/x",
        json={"label": "main", "api_key": "k", "api_secret": "s",
              "access_token": "t", "access_token_secret": "ts"},
    )

    body = client.post(
        "/api/reset", json={"confirm": True, "clear_credentials": True}
    ).json()
    assert body["cleared_credentials"] is True

    creds = client.get("/api/credentials").json()
    guardian = next(g for g in creds if g["platform"] == "The Guardian")
    assert not any(k["set"] for k in guardian["keys"])
    x = next(g for g in creds if g["type"] == "x_account")
    # Reset re-seeds one credential-less Default workspace (the app always has a
    # workspace), but the enrolled X credentials themselves are gone.
    assert x["accounts"] == ["Default"]
    assert not any(k["set"] for k in x["keys"])
    # nothing left to re-inject from the DB
    assert inject_stored_secrets(client.app.state.session_factory) == 0


def test_story_ttl_setting(client):
    assert client.get("/api/status").json()["story_ttl_hours"] == 24
    assert client.patch("/api/settings", json={"story_ttl_hours": 12}).json()["story_ttl_hours"] == 12
    assert client.patch("/api/settings", json={"story_ttl_hours": 0}).status_code == 422


def test_schedule_reports_status_and_day_tagged_upcoming(client):
    acct = _account(client)
    client.put(f"/api/niches/followed?account={acct}", json={"slugs": [NICHE]})
    body = client.get(f"/api/schedule?account={acct}").json()
    assert body["status"]["state"] in {"starting", "ok"}
    assert isinstance(body["now_minute"], int)
    for u in body["upcoming"]:
        assert set(u) == {"at", "day", "minute"} and u["day"] in (0, 1)
