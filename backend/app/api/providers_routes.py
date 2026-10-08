"""Provider and provider-chain API endpoints.

Extracted verbatim from `app/api/routes.py` (refactor; no behaviour change).
These endpoints are mounted on the SAME public paths as before: `routes.py`
includes this router without a prefix, and `main.py` applies the `/api` prefix
to the aggregate router exactly as it did previously.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

# A sub-router included into `routes.router` without a prefix, so every path and
# method is identical to the pre-refactor module.
router = APIRouter()


class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    base_url: str = Field(..., min_length=8, max_length=500)
    api_key: str = Field(..., min_length=1, max_length=2000)
    model: str = Field(..., min_length=1, max_length=200)
    # Optional: older clients omit it and the store falls back to `model`.
    model_name: str | None = Field(default=None, max_length=200)
    # Optional sampling temperature for providers that restrict it. Omitted =
    # unset (the global default applies); 0 is a real value, not "unset", so it
    # is not defaulted here. The store validates the 0-2 range.
    temperature: float | None = Field(default=None, ge=0, le=2)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=200)
    model_name: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ProviderTestIn(BaseModel):
    timeout_sec: float | None = None


class ChainIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class ChainUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=60)


class ChainMembersIn(BaseModel):
    # Ordered provider ids: index 0 is the primary, the rest are fallbacks.
    provider_ids: list[int] = Field(default_factory=list, max_length=12)


class ChainEnabledIn(BaseModel):
    enabled: bool = True


def _providers_db(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")
    return settings.database_url


@router.get("/providers")
async def list_llm_providers(request: Request) -> Dict[str, Any]:
    """Providers tab data: saved providers (keys masked) plus active id."""
    from app.core import providers as provider_store

    rows = await provider_store.list_providers(_providers_db(request))
    active = next((r for r in rows if r.get("is_active")), None)
    return {"providers": rows, "active_id": active["id"] if active else None}


@router.post("/providers", status_code=201)
async def create_llm_provider(body: ProviderIn, request: Request) -> Dict[str, Any]:
    from app.core import providers as provider_store

    try:
        row = await provider_store.save_provider(
            _providers_db(request), name=body.name, base_url=body.base_url,
            model=body.model, api_key=body.api_key, model_name=body.model_name,
            temperature=body.temperature,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"provider": row}


@router.put("/providers/{provider_id}")
async def update_llm_provider(provider_id: int, body: ProviderUpdate, request: Request) -> Dict[str, Any]:
    """Partial update; omit api_key to keep the stored key."""
    from app.core import providers as provider_store

    db_path = _providers_db(request)
    existing = await provider_store.get_provider(db_path, provider_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider id: {provider_id}")
    # Only forwarded when the request actually carried the field.
    # `model_fields_set` distinguishes "client omitted it" from "client sent
    # null or 0"; a plain None default cannot, and forwarding an absent field
    # would clear a temperature the caller never mentioned. The store's own
    # sentinel default does the same job for direct callers.
    update_kwargs: Dict[str, Any] = {}
    if "temperature" in body.model_fields_set:
        update_kwargs["temperature"] = body.temperature
    try:
        row = await provider_store.save_provider(
            db_path,
            provider_id=provider_id,
            name=body.name if body.name is not None else existing["name"],
            base_url=body.base_url if body.base_url is not None else existing["base_url"],
            model=body.model if body.model is not None else existing["model"],
            api_key=body.api_key,
            model_name=body.model_name,
            **update_kwargs,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail=f"Unknown provider id: {provider_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"provider": row}


@router.delete("/providers/{provider_id}")
async def delete_llm_provider(provider_id: int, request: Request) -> Dict[str, Any]:
    from app.core import providers as provider_store

    db_path = _providers_db(request)
    if not await provider_store.delete_provider(db_path, provider_id):
        raise HTTPException(status_code=404, detail=f"Unknown provider id: {provider_id}")
    # Cascade through chain membership: a deleted provider must never leave a
    # dangling (and subsequently un-resolvable) member in an enabled chain.
    await provider_store.remove_provider_from_chains(db_path, provider_id)
    return {"deleted": True}


@router.post("/providers/{provider_id}/active")
async def set_active_llm_provider(provider_id: int, request: Request) -> Dict[str, Any]:
    """Serving mode = Single Model. Exactly one active provider, and it
    disables any enabled fallback chain: the two serving modes are mutually
    exclusive, so selecting a single model automatically turns off chains."""
    from app.core import providers as provider_store

    db_path = _providers_db(request)
    try:
        row = await provider_store.set_active_provider(db_path, provider_id)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"Unknown provider id: {provider_id}")
    enabled = await provider_store.get_enabled_chain(db_path)
    if enabled is not None:
        await provider_store.set_chain_enabled(db_path, enabled["id"], False)
    return {"active": row}


@router.post("/providers/active/clear")
async def clear_active_llm_provider(request: Request) -> Dict[str, Any]:
    """Deselect: fall back to the legacy env/Groq/HF chain."""
    from app.core import providers as provider_store

    await provider_store.clear_active_provider(_providers_db(request))
    return {"active": None}


@router.post("/providers/{provider_id}/test")
async def test_llm_provider(
    provider_id: int, request: Request, body: ProviderTestIn | None = None
) -> Dict[str, Any]:
    """Single-shot connection probe (bypasses breakers, spends one call).
    The key never appears in logs or responses. timeout_sec is clamped to
    5..120s (default 15) — slow free-tier models need the headroom, but an
    unbounded probe could outlive the caller's patience."""
    import time as _time

    import httpx as _httpx

    from app.core import providers as provider_store

    timeout = 15.0
    if body is not None and body.timeout_sec is not None:
        timeout = min(max(float(body.timeout_sec), 5.0), 120.0)
    secret = await provider_store.get_provider_secret(_providers_db(request), provider_id)
    if secret is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider id: {provider_id}")
    base = str(secret["base_url"]).rstrip("/")
    url = base if base.lower().endswith("/chat/completions") else base + "/chat/completions"
    started = _time.monotonic()
    try:
        async with _httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {secret['api_key']}", "Content-Type": "application/json"},
                json={
                    "model": secret["model"],
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                },
            )
            response.raise_for_status()
    except Exception as exc:
        # Key material never logged: only the exception type and host-level
        # detail leave this handler.
        logger.warning("[Providers] test probe failed for id=%s: %s", provider_id, type(exc).__name__)
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    latency_ms = int((_time.monotonic() - started) * 1000)
    return {"ok": True, "latency_ms": latency_ms}


# ---------------------------------------------------------------------------
# Provider fallback chains
# ---------------------------------------------------------------------------

@router.get("/provider-chains")
async def list_provider_chains(request: Request) -> Dict[str, Any]:
    """All chains (ordered members included) plus the enabled chain id."""
    from app.core import providers as provider_store

    chains = await provider_store.list_chains(_providers_db(request))
    enabled = next((c["id"] for c in chains if c.get("is_enabled")), None)
    return {"chains": chains, "enabled_id": enabled}


@router.post("/provider-chains", status_code=201)
async def create_provider_chain(body: ChainIn, request: Request) -> Dict[str, Any]:
    from app.core import providers as provider_store

    try:
        row = await provider_store.save_chain(_providers_db(request), name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"chain": row}


@router.put("/provider-chains/{chain_id}")
async def update_provider_chain(chain_id: int, body: ChainUpdate, request: Request) -> Dict[str, Any]:
    """Rename a chain. Omit name to no-op."""
    from app.core import providers as provider_store

    db_path = _providers_db(request)
    existing = await provider_store.get_chain(db_path, chain_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown chain id: {chain_id}")
    try:
        row = await provider_store.save_chain(
            db_path, chain_id=chain_id, name=body.name if body.name is not None else existing["name"],
        )
    except LookupError:
        raise HTTPException(status_code=404, detail=f"Unknown chain id: {chain_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"chain": row}


@router.delete("/provider-chains/{chain_id}")
async def delete_provider_chain(chain_id: int, request: Request) -> Dict[str, Any]:
    from app.core import providers as provider_store

    if not await provider_store.delete_chain(_providers_db(request), chain_id):
        raise HTTPException(status_code=404, detail=f"Unknown chain id: {chain_id}")
    return {"deleted": True}


@router.put("/provider-chains/{chain_id}/members")
async def set_provider_chain_members(
    chain_id: int, body: ChainMembersIn, request: Request
) -> Dict[str, Any]:
    """Replace the chain's ordered members (index 0 = primary). Adding,
    removing and reordering all go through here."""
    from app.core import providers as provider_store

    try:
        row = await provider_store.set_chain_members(
            _providers_db(request), chain_id, body.provider_ids
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"chain": row}


@router.post("/provider-chains/{chain_id}/reorder")
async def reorder_provider_chain(
    chain_id: int, body: ChainMembersIn, request: Request
) -> Dict[str, Any]:
    """Reorder the chain's EXISTING members (same set, new order)."""
    from app.core import providers as provider_store

    try:
        row = await provider_store.reorder_chain_members(
            _providers_db(request), chain_id, body.provider_ids
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"chain": row}


@router.post("/provider-chains/{chain_id}/enabled")
async def set_provider_chain_enabled(
    chain_id: int, request: Request, body: ChainEnabledIn | None = None
) -> Dict[str, Any]:
    """Serving mode = Fallback Chain. Enabling a chain clears any active
    single model: the two serving modes are mutually exclusive."""
    from app.core import providers as provider_store

    enabled = True if body is None else bool(body.enabled)
    db_path = _providers_db(request)
    try:
        row = await provider_store.set_chain_enabled(db_path, chain_id, enabled)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"Unknown chain id: {chain_id}")
    if enabled:
        await provider_store.clear_active_provider(db_path)
    return {"chain": row}


@router.post("/provider-chains/clear")
async def clear_provider_chain(request: Request) -> Dict[str, Any]:
    """Disable whichever chain is enabled (returns to single-provider mode)."""
    from app.core import providers as provider_store

    db_path = _providers_db(request)
    enabled = await provider_store.get_enabled_chain(db_path)
    if enabled is not None:
        await provider_store.set_chain_enabled(db_path, enabled["id"], False)
    return {"enabled_id": None}
