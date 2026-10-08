"""Per-provider sampling temperature.

Until now the OpenAI-compatible call sent a hardcoded `temperature: 0.1`. A
model that accepts only 0, 0.6 or 1 rejected EVERY call with a 400
("invalid temperature"), so every agent fell back to deterministic extraction
and the run came back degraded — with nothing the user could configure to fix
it, because the value was not theirs to set.

These tests pin the three things that make the fix safe:

* `None` (unset) and `0` are DIFFERENT states. Collapsing them — the obvious
  `value or default` bug — sends 0.1 to exactly the provider that cannot take
  it, so the distinction is asserted from the validator up to the HTTP payload.
* An update that never mentions temperature keeps the stored value.
* The column is added idempotently to a database that predates it.
"""
import asyncio
import json

import pytest

from app.core.config import Settings
from app.core.llm import LLMClient
from app.core.providers import (
    _public,
    _validate_temperature,
    get_active_provider,
    list_providers,
    save_provider,
)


def _settings(**over):
    base = {"groq_api_key": "k", "_env_file": None}
    base.update(over)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Validation: unset vs zero
# ---------------------------------------------------------------------------


def test_unset_values_normalise_to_none():
    for value in (None, "", "   "):
        assert _validate_temperature(value) is None


def test_zero_is_a_real_temperature_not_unset():
    """The whole point: 0 must survive as 0.0, never be treated as "absent"."""
    assert _validate_temperature(0) == 0.0
    assert _validate_temperature(0.0) == 0.0
    assert _validate_temperature("0") == 0.0


def test_allowed_values_round_trip():
    assert _validate_temperature(0.6) == 0.6
    assert _validate_temperature(1) == 1.0
    assert _validate_temperature(2) == 2.0


def test_out_of_range_is_rejected_not_clamped():
    """A typo must surface; silently clamping would change sampling behind the
    user's back."""
    for bad in (-0.1, 2.1, 10):
        with pytest.raises(ValueError):
            _validate_temperature(bad)


def test_non_numeric_is_rejected():
    for bad in ("abc", [], {}):
        with pytest.raises(ValueError):
            _validate_temperature(bad)
    with pytest.raises(ValueError):
        _validate_temperature(float("nan"))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    from app.db.sqlite import init_db

    path = str(tmp_path / "temp.db")
    asyncio.run(init_db(path))
    return path


def test_temperature_round_trips_through_the_store(db):
    async def run():
        await save_provider(db, name="strict", base_url="https://x.example/v1",
                            api_key="sk-1", model="m", temperature=0)
        rows = await list_providers(db)
        return rows[0]
    row = asyncio.run(run())
    assert row["temperature"] == 0.0, "an explicit 0 must come back as 0, not None"


def test_omitted_temperature_is_unset(db):
    async def run():
        await save_provider(db, name="plain", base_url="https://x.example/v1",
                            api_key="sk-1", model="m")
        rows = await list_providers(db)
        return rows[0]
    assert asyncio.run(run())["temperature"] is None


def test_update_without_temperature_keeps_the_stored_one(db):
    """Editing a name must not wipe a temperature the caller never mentioned."""
    async def run():
        created = await save_provider(db, name="strict", base_url="https://x.example/v1",
                                      api_key="sk-1", model="m", temperature=0.6)
        await save_provider(db, provider_id=created["id"], name="renamed",
                            base_url="https://x.example/v1", model="m")
        return (await list_providers(db))[0]
    row = asyncio.run(run())
    assert row["name"] == "renamed"
    assert row["temperature"] == 0.6, "an omitted field must preserve the stored value"


def test_update_can_clear_temperature_back_to_unset(db):
    async def run():
        created = await save_provider(db, name="strict", base_url="https://x.example/v1",
                                      api_key="sk-1", model="m", temperature=1)
        # An explicit None through the update path clears it.
        await save_provider(db, provider_id=created["id"], name="strict",
                            base_url="https://x.example/v1", model="m",
                            temperature=None)
        return (await list_providers(db))[0]
    assert asyncio.run(run())["temperature"] is None


def test_active_provider_carries_temperature_for_the_llm_chain(db):
    async def run():
        from app.core.providers import set_active_provider

        created = await save_provider(db, name="strict", base_url="https://x.example/v1",
                                      api_key="sk-1", model="m", temperature=0)
        await set_active_provider(db, created["id"])
        return await get_active_provider(db)
    active = asyncio.run(run())
    assert active["temperature"] == 0.0


def test_public_shape_reports_unset_distinctly_from_zero():
    assert _public({"id": 1, "name": "n", "base_url": "u", "model": "m",
                    "model_name": "", "is_active": 0, "api_key_enc": "x",
                    "key_hint": "", "created_at": "", "updated_at": "",
                    "temperature": 0.0})["temperature"] == 0.0
    assert _public({"id": 1, "name": "n", "base_url": "u", "model": "m",
                    "model_name": "", "is_active": 0, "api_key_enc": "x",
                    "key_hint": "", "created_at": "", "updated_at": "",
                    "temperature": None})["temperature"] is None


def test_migration_adds_the_column_to_a_predating_database(tmp_path):
    """CREATE TABLE IF NOT EXISTS never adds a column, so a database written
    before this change must gain it via the PRAGMA check."""
    import sqlite3

    from app.db.sqlite import init_db

    path = str(tmp_path / "old.db")
    # A minimal pre-change llm_providers table: same shape, no temperature.
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE llm_providers ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,"
        " base_url TEXT NOT NULL, api_key_enc TEXT NOT NULL,"
        " key_hint TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,"
        " model_name TEXT DEFAULT '', is_active INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO llm_providers (name, base_url, api_key_enc, key_hint, model,"
        " model_name, is_active, created_at, updated_at)"
        " VALUES ('legacy','https://l.example/v1','enc','••••1','m','',1,'t','t')"
    )
    conn.commit()
    conn.close()

    asyncio.run(init_db(path))  # must not raise, must add the column

    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_providers)")}
    # The pre-existing row survives untouched and reads back as unset.
    # Read by SQL rather than through get_active_provider: that path decrypts
    # the key, and this fixture's ciphertext is intentionally not real.
    row = conn.execute(
        "SELECT name, temperature FROM llm_providers WHERE name = 'legacy'"
    ).fetchone()
    conn.close()
    assert "temperature" in cols
    assert row is not None, "the migration must not drop existing rows"
    assert row[1] is None, "an existing row resolves to 'unset', not to a default"


def test_migration_is_idempotent(tmp_path):
    from app.db.sqlite import init_db

    path = str(tmp_path / "twice.db")
    asyncio.run(init_db(path))
    asyncio.run(init_db(path))  # second run must be a no-op, not an error


# ---------------------------------------------------------------------------
# The HTTP payload — the actual bug
# ---------------------------------------------------------------------------


def _capture_custom_payload(monkeypatch, custom, *, settings=None):
    """Run _post_custom against a stub transport; return the JSON it sent."""
    import httpx

    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        )

    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", _Patched)
    llm = LLMClient(settings or _settings())
    asyncio.run(llm._post_custom("sys", "user", custom, 5.0))
    return captured


_CUSTOM = {
    "api_key": "sk-x",
    "endpoint": "http://localhost:9/v1/chat/completions",
    "model": "m",
    "name": "p",
}


def test_explicit_zero_reaches_the_provider_as_zero(monkeypatch):
    """The regression: a provider limited to 0/0.6/1 gets 0, not the default."""
    payload = _capture_custom_payload(monkeypatch, {**_CUSTOM, "temperature": 0})
    assert payload["temperature"] == 0.0


def test_explicit_allowed_value_is_sent(monkeypatch):
    payload = _capture_custom_payload(monkeypatch, {**_CUSTOM, "temperature": 0.6})
    assert payload["temperature"] == 0.6


def test_unset_temperature_falls_back_to_the_settings_default(monkeypatch):
    payload = _capture_custom_payload(
        monkeypatch, dict(_CUSTOM),
        settings=_settings(llm_temperature=0.2),
    )
    assert payload["temperature"] == 0.2


def test_default_is_the_old_hardcoded_value_for_compatibility(monkeypatch):
    """Every existing provider that never set a temperature keeps behaving
    exactly as before."""
    payload = _capture_custom_payload(monkeypatch, dict(_CUSTOM))
    assert payload["temperature"] == 0.1


def test_none_temperature_uses_the_default_not_zero(monkeypatch):
    """The falsy-coercion trap: None must not be read as 0."""
    payload = _capture_custom_payload(monkeypatch, {**_CUSTOM, "temperature": None},
                                      settings=_settings(llm_temperature=1.0))
    assert payload["temperature"] == 1.0


# ---------------------------------------------------------------------------
# Route round-trip
# ---------------------------------------------------------------------------


def test_route_accepts_and_returns_temperature(tmp_path):
    import httpx
    from fastapi import FastAPI

    from app.api.routes import limiter, router as api_router
    from app.db.sqlite import init_db

    db_path = str(tmp_path / "r.db")
    asyncio.run(init_db(db_path))
    app = FastAPI()
    app.state.settings = _settings(database_url=db_path)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/providers", json={
                "name": "strict", "base_url": "http://localhost:20128/v1",
                "api_key": "sk-x", "model": "oc/fledge-alpha-free",
                "temperature": 0,
            })
            assert created.status_code == 201, created.text
            assert created.json()["provider"]["temperature"] == 0.0

            listed = await client.get("/api/providers")
            assert listed.json()["providers"][0]["temperature"] == 0.0

            # Update omitting the field keeps 0.
            renamed = await client.put(
                f"/api/providers/{created.json()['provider']['id']}",
                json={"model_name": "Fledge"},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["provider"]["temperature"] == 0.0, \
                "an edit that omits temperature must not clear it"

            # Explicit null clears it back to unset.
            cleared = await client.put(
                f"/api/providers/{created.json()['provider']['id']}",
                json={"temperature": None},
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["provider"]["temperature"] is None

    asyncio.run(run())


def test_route_rejects_an_out_of_range_temperature(tmp_path):
    import httpx
    from fastapi import FastAPI

    from app.api.routes import limiter, router as api_router
    from app.db.sqlite import init_db

    db_path = str(tmp_path / "r2.db")
    asyncio.run(init_db(db_path))
    app = FastAPI()
    app.state.settings = _settings(database_url=db_path)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bad = await client.post("/api/providers", json={
                "name": "n", "base_url": "http://x.example/v1",
                "api_key": "sk-x", "model": "m", "temperature": 3,
            })
            return bad.status_code

    assert asyncio.run(run()) == 422
