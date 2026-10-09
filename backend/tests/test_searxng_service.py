"""SearXNG lifecycle supervision (auto-start).

The point of the supervisor is that the user never runs SearXNG by hand, so the
tests are mostly about what it must NOT do: never adopt or kill an instance it
did not start, never block backend startup on a metasearch that will not come
up, and never leave a child holding port 8080 (the overlapping-backends failure
this repo has already been bitten by -- AGENTS.md section 2).

No network and no real SearXNG: /healthz is mocked with respx and the child
process is never actually launched except in the one test that exercises the
spawn path with a stub interpreter.
"""
import asyncio
import sys

import httpx
import respx

from app.core.searxng_service import SearxngSupervisor

BASE = "http://localhost:8080"
HEALTHZ = f"{BASE}/healthz"


def _settings(**over):
    from app.core.config import Settings

    base = {"groq_api_key": "test-key", "_env_file": None}
    base.update(over)
    return Settings(**base)


async def test_already_running_instance_is_never_adopted():
    """Something else owns the endpoint: log and leave it completely alone."""
    supervisor = SearxngSupervisor(_settings(searxng_url=BASE))
    with respx.mock:
        route = respx.get(HEALTHZ).mock(return_value=httpx.Response(200))
        started = await supervisor.start()

    assert started is False
    assert supervisor.process is None
    assert supervisor.managed is False
    assert route.called


async def test_disabled_autostart_does_not_probe_or_spawn():
    """SEARXNG_AUTOSTART=false restores the old manual-start behaviour."""
    settings = _settings(searxng_url=BASE, searxng_autostart=False)
    supervisor = SearxngSupervisor(settings)
    assert await supervisor.start() is False
    assert supervisor.managed is False


async def test_missing_clone_degrades_without_raising(tmp_path):
    """No clone is a documented degraded state, not a startup failure."""
    settings = _settings(searxng_url=BASE, searxng_home=str(tmp_path / "nope"))
    supervisor = SearxngSupervisor(settings)
    with respx.mock:
        respx.get(HEALTHZ).mock(side_effect=httpx.ConnectError("refused"))
        assert await supervisor.start() is False
    assert supervisor.managed is False


async def test_missing_venv_degrades_without_raising(tmp_path):
    """The clone exists but was never given its virtualenv."""
    home = tmp_path / "searxng"
    home.mkdir()
    settings = _settings(searxng_url=BASE, searxng_home=str(home))
    supervisor = SearxngSupervisor(settings)
    with respx.mock:
        respx.get(HEALTHZ).mock(side_effect=httpx.ConnectError("refused"))
        assert await supervisor.start() is False
    assert supervisor.managed is False


async def test_startup_timeout_stops_the_child(monkeypatch, tmp_path):
    """A child that never becomes ready is terminated, not orphaned.

    This is the overlapping-backends guard: the process must be gone, not merely
    un-referenced. The child here is a real one (`time.sleep`), so the assertion
    is on its exit status rather than on bookkeeping.
    """
    home = tmp_path / "searxng"
    (home / ".venv/bin").mkdir(parents=True)
    python = home / ".venv/bin/python"
    python.write_text("")
    python.chmod(0o755)

    settings = _settings(
        searxng_url=BASE,
        searxng_home=str(home),
        searxng_startup_timeout_sec=1.0,
    )
    supervisor = SearxngSupervisor(settings)
    spawned = {}

    async def _fake_spawn(*_a, **_k):
        # A child that stays alive and never serves /healthz.
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        spawned["proc"] = proc
        supervisor.process = proc
        supervisor.managed = True
        return True

    monkeypatch.setattr(supervisor, "_spawn", _fake_spawn)

    with respx.mock:
        respx.get(HEALTHZ).mock(side_effect=httpx.ConnectError("refused"))
        assert await supervisor.start() is False

    proc = spawned["proc"]
    assert proc.returncode is not None, "orphaned child still running"
    assert supervisor.process is None
    assert supervisor.managed is False


async def test_stop_is_a_noop_when_nothing_was_started():
    """Idempotent shutdown: safe to call on a backend that never spawned."""
    supervisor = SearxngSupervisor(_settings(searxng_url=BASE))
    await supervisor.stop()
    await supervisor.stop()
    assert supervisor.managed is False