"""User-managed LLM providers: encrypted store, active exclusivity, routes."""
import asyncio

import pytest
from cryptography.fernet import Fernet

from app.core.llm import AllProvidersFailedError, LLMClient


@pytest.fixture
def _secret(monkeypatch):
    monkeypatch.setenv("DEEPSCOUT_SECRET_KEY", Fernet.generate_key().decode("utf-8"))


@pytest.fixture
def db_path(tmp_path, _secret):
    import asyncio

    from app.db.sqlite import init_db

    path = str(tmp_path / "providers.db")
    asyncio.run(init_db(path))
    return path


def _settings(**over):
    from app.core.config import Settings

    base = {"groq_api_key": "test-key", "_env_file": None}
    base.update(over)
    return Settings(**base)


async def _seed(db_path, name="alpha", url="https://llm.example.com/v1", model="m-1", key="sk-test-key-1234"):
    from app.core import providers as store

    return await store.save_provider(db_path, name=name, base_url=url, model=model, api_key=key)


async def test_crud_masks_key(db_path):
    from app.core import providers as store

    row = await _seed(db_path)
    assert row["key_hint"] == "••••1234"
    listed = await store.list_providers(db_path)
    assert len(listed) == 1
    assert "api_key" not in listed[0] and "api_key_enc" not in listed[0]
    assert listed[0]["has_key"] is True and listed[0]["is_active"] is False

    updated = await store.save_provider(db_path, provider_id=row["id"], name="alpha",
                                        base_url="https://llm.example.com/v2", model="m-2")
    assert updated["base_url"].endswith("/v2") and updated["key_hint"] == "••••1234"

    with pytest.raises(ValueError):
        await _seed(db_path, name="alpha")
    with pytest.raises(ValueError):
        await _seed(db_path, name="bad", url="not-a-url")
    assert await store.delete_provider(db_path, row["id"]) is True
    assert await store.delete_provider(db_path, row["id"]) is False


async def test_active_exclusivity_and_decrypt(db_path):
    from app.core import providers as store

    a = await _seed(db_path, name="alpha")
    b = await _seed(db_path, name="beta", key="sk-other-key-9999")
    assert await store.get_active_provider(db_path) is None
    await store.set_active_provider(db_path, a["id"])
    assert (await store.get_active_provider(db_path))["name"] == "alpha"
    await store.set_active_provider(db_path, b["id"])
    rows = {r["name"]: r["is_active"] for r in await store.list_providers(db_path)}
    assert rows == {"alpha": False, "beta": True}
    active = await store.get_active_provider(db_path)
    assert active["api_key"] == "sk-other-key-9999"
    await store.delete_provider(db_path, b["id"])
    assert await store.get_active_provider(db_path) is None
    with pytest.raises(LookupError):
        await store.set_active_provider(db_path, 424242)


async def test_wrong_secret_cannot_decrypt(db_path, monkeypatch):
    from app.core import providers as store

    row = await _seed(db_path)
    await store.set_active_provider(db_path, row["id"])
    monkeypatch.setenv("DEEPSCOUT_SECRET_KEY", Fernet.generate_key().decode("utf-8"))
    with pytest.raises(ValueError, match="cannot be decrypted"):
        await store.get_active_provider(db_path)


async def test_exclusive_chain_skips_groq(db_path):
    """Active provider set + failing: Groq must not be touched (exclusive)."""
    import httpx
    import respx

    from app.core import providers as store

    await _seed(db_path)
    providers = await store.list_providers(db_path)
    await store.set_active_provider(db_path, providers[0]["id"])
    client = LLMClient(_settings(database_url=db_path))
    with respx.mock(assert_all_called=False) as mock:
        custom_route = mock.post("https://llm.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "down"}))
        groq_route = mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}))
        with pytest.raises(AllProvidersFailedError, match="Active provider"):
            await client.generate_json("sp", "up")
        assert custom_route.call_count == 4  # tenacity still retries fast 5xx in place
        assert groq_route.call_count == 0


async def test_provider_routes_crud_and_test(tmp_path, _secret):
    """Endpoints: create/list/set-active/test/delete round trip."""
    import httpx
    import respx
    from fastapi import FastAPI

    from app.api.routes import limiter, router as api_router

    from app.db.sqlite import init_db

    db_path = str(tmp_path / "routes.db")
    await init_db(db_path)
    app = FastAPI()
    app.state.settings = _settings(database_url=db_path)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = (await client.post("/api/providers", json={
            "name": "pro", "base_url": "https://llm.example.com/v1",
            "api_key": "sk-live-1234", "model": "m-1"})).json()
        assert created["provider"]["key_hint"] == "••••1234"
        assert "api_key" not in created["provider"]
        pid = created["provider"]["id"]

        listed = (await client.get("/api/providers")).json()
        assert listed["active_id"] is None and len(listed["providers"]) == 1

        with respx.mock(assert_all_called=False) as mock:
            mock.post("https://llm.example.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
            probed = (await client.post(f"/api/providers/{pid}/test")).json()
        assert probed["ok"] is True and probed["latency_ms"] >= 0

        activated = (await client.post(f"/api/providers/{pid}/active")).json()
        assert activated["active"]["is_active"] is True
        assert (await client.get("/api/providers")).json()["active_id"] == pid

        renamed = (await client.put(f"/api/providers/{pid}", json={"name": "pro2"})).json()
        assert renamed["provider"]["name"] == "pro2"
        assert renamed["provider"]["key_hint"] == "••••1234"

        assert (await client.delete("/api/providers/424242")).status_code == 404
        assert (await client.delete(f"/api/providers/{pid}")).json() == {"deleted": True}
        assert (await client.get("/api/providers")).json() == {"providers": [], "active_id": None}


async def test_provider_routes_accept_sqlite_url_form(tmp_path, _secret):
    """Regression: a sqlite:// DATABASE_URL must resolve provider routes.

    `init_db` normalizes the URL form to a filesystem path, but the provider
    routes read `settings.database_url` directly. An unnormalized value opened
    a literal `sqlite:/...` filename with no tables, so every provider route
    500'd while the rest of the app worked — a deployment-only failure that
    unit tests using a raw path never hit.
    """
    import httpx
    from fastapi import FastAPI

    from app.api.routes import limiter, router as api_router
    from app.db.sqlite import init_db

    file_path = str(tmp_path / "urlform.db")
    await init_db(file_path)
    url_form = f"sqlite:///{file_path}"
    app = FastAPI()
    app.state.settings = _settings(database_url=url_form)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/providers")
        assert listed.status_code == 200, listed.text
        assert listed.json() == {"providers": [], "active_id": None}
        assert (await client.get("/api/provider-chains")).status_code == 200


async def test_probe_timeout_clamped_and_optional(tmp_path, _secret):
    """timeout_sec is optional (default 15s) and clamped to 5..120s."""
    import httpx
    import respx
    from fastapi import FastAPI

    from app.api.routes import limiter, router as api_router

    from app.db.sqlite import init_db

    db_path = str(tmp_path / "timeout.db")
    await init_db(db_path)
    app = FastAPI()
    app.state.settings = _settings(database_url=db_path)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        pid = (await client.post("/api/providers", json={
            "name": "slow", "base_url": "https://slow.example.com/v1",
            "api_key": "sk-slow-key", "model": "m-slow"})).json()["provider"]["id"]
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post("https://slow.example.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
            defaulted = (await client.post(f"/api/providers/{pid}/test")).json()
            assert defaulted["ok"] is True
            huge = (await client.post(f"/api/providers/{pid}/test", json={"timeout_sec": 9999})).json()
            assert huge["ok"] is True
            assert route.call_count == 2


async def test_breaker_resets_when_active_provider_switches(db_path):
    """Switching the active provider must not inherit the previous
    provider's breaker state: a provider that timed out once would
    otherwise block the freshly-selected healthy one for the cooldown.
    Exercises the real reset inside _generate_with_fallback."""
    import respx
    import httpx

    from app.core import providers as provider_store
    from app.core.llm import LLMClient

    row_a = await provider_store.save_provider(
        db_path, name="prov-a", base_url="https://a.example.com/v1",
        model="model-a", api_key="key-a",
    )
    await provider_store.set_active_provider(db_path, row_a["id"])
    settings = _settings(database_url=db_path)
    client = LLMClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        route_a = mock.post("https://a.example.com/v1/chat/completions").mock(
            side_effect=httpx.ReadTimeout(""))  # empty message: also covers the formatter
        # A times out: exclusive selection fails fast (no env fallback),
        # and the timeout opens A's breaker.
        with pytest.raises(AllProvidersFailedError, match="prov-a"):
            await client.generate_json("sp", "up")
        assert client.custom_breaker.is_open()

        # User switches to provider B. Despite A's open breaker, B must be
        # attempted — the identity switch resets the breaker.
        await provider_store.set_active_provider(db_path, row_a["id"])  # same row replaced below
        await provider_store.save_provider(
            db_path, name="prov-b", base_url="https://b.example.com/v1",
            model="model-b", api_key="key-b",
        )
        rows = await provider_store.list_providers(db_path)
        row_b = next(r for r in rows if r["name"] == "prov-b")
        await provider_store.set_active_provider(db_path, row_b["id"])

        route_b = mock.post("https://b.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\": true}"}}]}))
        result = await client.generate_json("sp", "up")
        assert result == {"ok": True}
        assert route_b.call_count == 1, "switched provider must be attempted despite stale breaker"
        assert route_a.call_count == 1


async def test_probe_all_reports_dead_providers(db_path):
    """Pre-flight probe, env chain (no active provider): all providers
    failing -> (False, per-provider detail); one succeeding -> (True, '')."""
    import respx
    import httpx

    from app.core import providers as provider_store
    from app.core.llm import LLMClient

    settings = _settings(groq_api_key="k", huggingface_api_key="hf", database_url=db_path)
    client = LLMClient(settings)
    assert await provider_store.get_active_provider(db_path) is None

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "quota"}))
        mock.post("https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct").mock(
            return_value=httpx.Response(503, json={"error": "loading"}))
        ok, detail = await client.probe_all(timeout=5.0)
    assert ok is False
    assert "groq" in detail and "huggingface" in detail

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
        mock.post("https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct").mock(
            return_value=httpx.Response(503, json={"error": "loading"}))
        ok, detail = await client.probe_all(timeout=5.0)
    assert ok is True and detail == ""


async def test_exclusive_probe_pings_active_only(db_path):
    """While a provider is active, the probe must NOT ping Groq/HF —
    selection is exclusive, and pinging irrelevant keys wastes quota."""
    import respx
    import httpx

    from app.core import providers as provider_store
    from app.core.llm import LLMClient

    row = await provider_store.save_provider(
        db_path, name="only", base_url="https://only.example.com/v1",
        model="m", api_key="key",
    )
    await provider_store.set_active_provider(db_path, row["id"])
    settings = _settings(groq_api_key="k", huggingface_api_key="hf", database_url=db_path)
    client = LLMClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://only.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
        groq_route = mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
        ok, _ = await client.probe_all(timeout=5.0)
    assert ok is True
    assert route.call_count == 1
    assert groq_route.call_count == 0, "exclusive selection must not ping the env chain"


async def test_active_provider_fallback_rescues_the_run(db_path, monkeypatch):
    """ACTIVE_PROVIDER_FALLBACK=true: the UI-selected provider stays primary,
    but when it fails the env chain (Groq) serves the call instead of the run
    degrading to deterministic extraction."""
    import httpx
    import respx

    from app.core import providers as store
    from app.core.config import Settings
    from app.core.llm import LLMClient

    await _seed(db_path)
    providers = await store.list_providers(db_path)
    await store.set_active_provider(db_path, providers[0]["id"])
    settings = Settings(groq_api_key="test-key", database_url=db_path,
                        active_provider_fallback=True, _env_file=None)
    client = LLMClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        custom_route = mock.post("https://llm.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "down"}))
        groq_route = mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}))
        result = await client.generate_json("sp", "up")
        assert result == {"ok": True}
        assert custom_route.call_count >= 1
        assert groq_route.call_count == 1


async def test_exclusive_probe_covers_env_fallbacks_when_enabled(db_path):
    """With active_provider_fallback on, the pre-flight probe must ping the
    active provider AND the env fallbacks — the probe's job is to detect a
    doomed chain, and the chain now includes Groq/HF."""
    import httpx
    import respx

    from app.core.config import Settings
    from app.core import providers as store
    from app.core.llm import LLMClient

    row = await store.save_provider(
        db_path, name="only2", base_url="https://only2.example.com/v1",
        model="m", api_key="key",
    )
    await store.set_active_provider(db_path, row["id"])
    settings = Settings(
        groq_api_key="k", huggingface_api_key="hf",
        active_provider_fallback=True, database_url=db_path, _env_file=None,
    )
    client = LLMClient(settings)
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://only2.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
        ok, _ = await client.probe_all(timeout=5.0)
    assert ok is True


# ---------------------------------------------------------------------------
# Regression: the orphaned `model_name` column broke /api/providers with 500.
#
# The persisted table had a `model_name` column and every row stored '' for it,
# but `_public()` never selected it and no migration/INSERT/UPDATE mentioned it.
# The store resolves at boot against whatever column set the on-disk database
# actually has, so a database written by the build that added the column could
# never be read back by the build that did not know about it.
# ---------------------------------------------------------------------------


async def test_reads_provider_rows_written_with_model_name_column(db_path):
    """A row carrying model_name must serialize, not raise. `model_name` blank
    resolves to the model id so the wire shape is always a usable label."""
    import aiosqlite

    from app.core import providers as store

    await _seed(db_path, name="alpha")
    async with aiosqlite.connect(db_path) as raw:
        await raw.execute(
            "UPDATE llm_providers SET model_name = ? WHERE name = ?",
            ("GPT-4o mini", "alpha"),
        )
        await raw.commit()

    rows = await store.list_providers(db_path)
    row = next(r for r in rows if r["name"] == "alpha")
    assert row["model_name"] == "GPT-4o mini"

    # The column is present in the fresh schema, so a blank label is the
    # documented "no separate label" state and must fall back to the id.
    async with aiosqlite.connect(db_path) as raw:
        await raw.execute("UPDATE llm_providers SET model_name = '' WHERE name = ?", ("alpha",))
        await raw.commit()
    row = next(r for r in await store.list_providers(db_path) if r["name"] == "alpha")
    assert row["model_name"] == row["model"]


async def test_fresh_schema_carries_the_model_name_column(db_path):
    """init_db must create the column outright, and the ALTER path must still
    converge an older database onto the same shape."""
    import aiosqlite

    from app.db.sqlite import init_db

    async with aiosqlite.connect(db_path) as db:
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(llm_providers)")).fetchall()}
    assert "model_name" in cols, "fresh databases must include model_name"

    # Simulate a database created before the column existed by rebuilding the
    # table without it, then re-running the migration.
    async with aiosqlite.connect(db_path) as db:
        await db.execute("ALTER TABLE llm_providers RENAME TO _old")
        await db.execute(
            """CREATE TABLE llm_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL, api_key_enc TEXT NOT NULL,
                key_hint TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        await db.execute(
            "INSERT INTO llm_providers (name, base_url, api_key_enc, key_hint, model, is_active, created_at, updated_at)"
            " SELECT name, base_url, api_key_enc, key_hint, model, is_active, created_at, updated_at FROM _old"
        )
        await db.execute("DROP TABLE _old")
        await db.commit()
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(llm_providers)")).fetchall()}
        assert "model_name" not in cols, "precondition: the legacy shape has no model_name"

    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(llm_providers)")).fetchall()}
    assert "model_name" in cols, "init_db must migrate a legacy table forward"


async def test_model_name_round_trips_and_is_independent_of_model(db_path):
    """Model name is a display label; Model ID is what goes to the provider.
    They must be independently settable, and an omitted label on partial
    update must preserve the stored one rather than blanking it."""
    from app.core import providers as store

    created = await store.save_provider(
        db_path, name="labelled", base_url="https://llm.example.com/v1",
        model="gpt-4o-mini", api_key="sk-live-1234", model_name="GPT-4o mini",
    )
    assert created["model"] == "gpt-4o-mini"
    assert created["model_name"] == "GPT-4o mini"

    # Omitting model_name (None) keeps the stored label.
    updated = await store.save_provider(
        db_path, provider_id=created["id"], name="labelled",
        base_url="https://llm.example.com/v1", model="gpt-4o-mini",
    )
    assert updated["model_name"] == "GPT-4o mini", "an omitted label must be preserved"

    # An explicit blank clears the label back to the model id.
    cleared = await store.save_provider(
        db_path, provider_id=created["id"], name="labelled",
        base_url="https://llm.example.com/v1", model="gpt-4o-mini", model_name="",
    )
    assert cleared["model_name"] == "gpt-4o-mini"


async def test_provider_routes_accept_model_name(db_path):
    """/api/providers must round-trip model_name end to end: the 500 this
    guards against surfaced on exactly this route."""
    import httpx
    from fastapi import FastAPI

    from app.api.routes import limiter, router as api_router

    app = FastAPI()
    app.state.settings = _settings(database_url=db_path)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/providers", json={
            "name": "seeded", "base_url": "https://seeded.example.com/v1",
            "api_key": "sk-seeded-7777", "model": "seeded-model",
        })
        listed = await client.get("/api/providers")
        assert listed.status_code == 200, "GET /api/providers must not 500"
        assert listed.json()["providers"], "the seeded provider must be readable"

        created = await client.post("/api/providers", json={
            "name": "routed", "base_url": "https://routed.example.com/v1",
            "api_key": "sk-routed-9999", "model": "routed-model",
            "model_name": "Routed Model Label",
        })
        assert created.status_code == 201, created.text
        assert created.json()["provider"]["model_name"] == "Routed Model Label"

        # A legacy client that omits the field entirely still succeeds.
        legacy = await client.post("/api/providers", json={
            "name": "legacy", "base_url": "https://legacy.example.com/v1",
            "api_key": "sk-legacy-8888", "model": "legacy-model",
        })
        assert legacy.status_code == 201, legacy.text
        assert legacy.json()["provider"]["model_name"] == "legacy-model"

        renamed = await client.put(
            f"/api/providers/{created.json()['provider']['id']}",
            json={"model_name": "Renamed Label"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["provider"]["model_name"] == "Renamed Label"


# --- zero-env-key bootstrap ------------------------------------------------
#
# The Providers tab is the primary way to configure the LLM: keys live in the
# database, not the environment. So an untouched `.env.example` must boot, and
# the "no provider" failure has to surface at call time as a message that
# sends the user to that tab — not as an import-time Settings error that takes
# the whole API down before the tab can be opened.


def test_settings_construct_with_no_provider_keys():
    """No env key, no CUSTOM_LLM_* trio, no ValidationError.

    Regression for the removed `_require_llm_provider` validator, which made a
    stock setup unbootable and therefore unreachable from the Providers tab.
    """
    from app.core.config import Settings

    settings = Settings(
        groq_api_key="", huggingface_api_key="",
        custom_llm_api_key="", custom_llm_base_url="", custom_llm_model="",
        _env_file=None,
    )
    assert settings.groq_api_key == ""
    assert settings.custom_llm_api_key == ""


def test_env_placeholders_are_inert():
    """The `.env.example` `your_...` values must not read as real keys."""
    from app.core.config import Settings
    from app.core.llm import _real_key

    settings = Settings(
        groq_api_key="your_groq_api_key_here",
        huggingface_api_key="your_huggingface_api_key_here",
        custom_llm_api_key="your_custom_key_here",
        custom_llm_base_url="https://your-provider.example/v1",
        custom_llm_model="your-model-id-here",
        _env_file=None,
    )
    # Settings accepts them verbatim...
    assert settings.groq_api_key == "your_groq_api_key_here"
    # ...but the LLM layer treats every one of them as unset.
    assert _real_key(settings.groq_api_key) == ""
    assert _real_key(settings.huggingface_api_key) == ""
    assert _real_key(settings.custom_llm_api_key) == ""


async def test_no_provider_error_points_at_the_providers_tab(db_path):
    """The runtime failure must name the UI path and say a selection applies
    without a restart — the whole point of dropping the boot-time gate."""
    from app.core.llm import NoProviderConfiguredError

    llm = LLMClient(_settings(
        groq_api_key="", huggingface_api_key="",
        custom_llm_api_key="", custom_llm_base_url="", custom_llm_model="",
        database_url=db_path,
    ))
    with pytest.raises(NoProviderConfiguredError, match="No LLM provider configured") as exc:
        await llm.generate_json("sys", "user")
    message = str(exc.value)
    assert "model-controls" in message, message
    assert "applies immediately" in message, message


async def test_no_provider_error_is_not_retried(db_path, monkeypatch):
    """Deterministic 'nothing configured' must fail fast.

    Retrying it three times with sleeps buys nothing — no provider can appear
    inside the same request — and it used to bury the real cause under two
    warnings and ~1.4s of latency on every single agent call.
    """
    from app.core.llm import NoProviderConfiguredError

    llm = LLMClient(_settings(
        groq_api_key="", huggingface_api_key="",
        custom_llm_api_key="", custom_llm_base_url="", custom_llm_model="",
        database_url=db_path,
    ))
    calls = 0

    async def _counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise NoProviderConfiguredError("No LLM provider configured.")

    monkeypatch.setattr(llm, "_generate_with_fallback", _counting)
    sleeps = []
    async def _no_sleep(delay):
        sleeps.append(delay)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    with pytest.raises(NoProviderConfiguredError):
        await llm.generate_json("sys", "user")
    assert calls == 1, f"expected exactly one attempt, got {calls}"
    assert not sleeps, f"expected no backoff sleep, got {sleeps}"


# --- missing sealing secret ------------------------------------------------
#
# The old failure mode was silent and looked like misconfiguration: with the
# key file gone, `_fernet_key()` minted a fresh key, every stored row became
# undecryptable, the InvalidToken degraded into a log warning, and a run then
# reported "no provider configured" while the Providers tab still listed a
# selected provider. Reads now fail loudly and name the fix. WRITES must keep
# working, because re-entering the key in the Providers tab is the repair.


@pytest.fixture
def _no_secret(monkeypatch, tmp_path):
    """No env secret, no `.env` secret, no key file: the lost-secret state."""
    from app.core import config as config_mod
    from app.core import providers as store

    monkeypatch.delenv("DEEPSCOUT_SECRET_KEY", raising=False)
    monkeypatch.delenv("MARS_SECRET_KEY", raising=False)

    class _NoSecret:
        deepscout_secret_key = ""

    monkeypatch.setattr(config_mod, "get_settings", lambda: _NoSecret())
    keyfile = tmp_path / "absent_secret"
    monkeypatch.setattr(store, "_secret_file_path", lambda: keyfile)
    return keyfile


async def _store_active_provider(db_path, *, key="sk-secret-1", name="stored"):
    """A provider row that is SELECTED — the state a run actually reads."""
    from app.core import providers as store

    created = await store.save_provider(
        db_path, name=name, base_url="https://x.example.com/v1",
        api_key=key, model="m",
    )
    provider_id = int(created["id"])
    await store.set_active_provider(db_path, provider_id)
    return provider_id


async def test_missing_secret_is_reported_not_silently_ignored(db_path, _no_secret):
    """A selected provider whose key cannot be decrypted must raise, and the
    message must name the repair rather than blaming the provider."""
    from app.core import providers as store

    await _store_active_provider(db_path)
    _no_secret.unlink(missing_ok=True)  # the key file goes away

    with pytest.raises(store.ProviderSecretUnavailableError) as exc:
        await store.get_active_provider(db_path)
    message = str(exc.value)
    assert "re-enter the API key" in message, message
    assert "DEEPSCOUT_SECRET_KEY" in message, message


async def test_wrong_secret_is_reported_too(db_path, _no_secret):
    """The live case: the key file EXISTS but is not the one the rows were
    sealed with (restored DB, replaced secret, cloned research.db).

    This used to be the worst variant — decryption failed, the error was
    swallowed into "provider store unreadable, using env config", and the run
    reported "no provider configured" while the Providers tab still listed the
    provider as selected.
    """
    from cryptography.fernet import Fernet

    from app.core import providers as store

    await _store_active_provider(db_path)
    _no_secret.write_bytes(Fernet.generate_key())  # present, but different

    with pytest.raises(store.ProviderSecretUnavailableError) as exc:
        await store.get_active_provider(db_path)
    message = str(exc.value)
    assert "does not match" in message, message
    assert "re-enter the API key" in message, message


async def test_secret_failure_reaches_the_llm_chain(db_path, _no_secret):
    """The LLM layer must not fold this into "store unreadable, use env".

    That branch sets active=None, which is indistinguishable from a system with
    no providers at all — the exact lie this guard exists to prevent.
    """
    from app.core import providers as store

    await _store_active_provider(db_path)
    _no_secret.unlink(missing_ok=True)

    llm = LLMClient(_settings(database_url=db_path))
    with pytest.raises(store.ProviderSecretUnavailableError):
        await llm.probe_targets()


async def test_missing_secret_is_fine_with_no_stored_keys(db_path, _no_secret):
    """Nothing stored means nothing to decrypt: no error, no false alarm.

    This is the brand-new-install case the whole setup flow depends on.
    """
    from app.core import providers as store

    assert await store.get_active_provider(db_path) is None


async def test_reattering_the_key_repairs_the_store(db_path, _no_secret):
    """The documented repair must work end to end: a write mints the new
    secret, and the row becomes readable again."""
    from app.core import providers as store

    provider_id = await _store_active_provider(db_path, key="sk-orphan-1")
    _no_secret.unlink(missing_ok=True)
    with pytest.raises(store.ProviderSecretUnavailableError):
        await store.get_active_provider(db_path)

    # Repair = re-enter the key through the normal write path.
    await store.save_provider(
        db_path, provider_id=provider_id, name="stored",
        base_url="https://x.example.com/v1", model="m",
        api_key="sk-repaired-2",
    )
    restored = await store.get_active_provider(db_path)
    assert restored is not None
    assert restored["api_key"] == "sk-repaired-2"
