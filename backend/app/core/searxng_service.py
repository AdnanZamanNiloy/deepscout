"""Lifecycle supervision for the self-hosted SearXNG metasearch service.

SearXNG is a SEPARATE application -- the third-party searxng/searxng Flask
project, with its own virtualenv, its own `settings.local.yml` (which carries a
required `server.secret_key`) and its own listening port. It cannot be imported
as a library, so the backend can only ever reach it over HTTP.

What this module removes is therefore not the network boundary but the MANUAL
step: today the user has to clone searxng, then start it in a third terminal
before the backend is useful. With autostart on, the backend spawns it, waits
for it to answer, and stops it again on shutdown -- one command runs the whole
system.

Three rules keep this from becoming a liability:

  * An already-reachable instance ALWAYS WINS. If something else (systemd,
    Docker, or a developer's own run) already serves the endpoint, this module
    logs and does nothing. It never adopts -- and above all never kills -- a
    process it did not start.
  * Failure NEVER blocks startup. SearXNG being unavailable degrades a run to
    Wikipedia/arXiv/Crossref, a state the search stack already handles with a
    circuit breaker. A backend that refused to boot without a metasearch engine
    would be strictly worse than one that starts and says so.
  * The child is ALWAYS reaped. An orphaned SearXNG still holding port 8080 is
    the exact "two backends on one port" failure this repo has already been
    bitten by (see AGENTS.md section 2), so shutdown terminates explicitly and
    escalates to SIGKILL if the child ignores it.

The child is started WITHOUT start_new_session so it stays in this process
group: a Ctrl-C in the launching terminal then stops the whole stack, which is
the behaviour "one command runs everything" implies.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


# Repo layout: backend/app/core/searxng_service.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Cheap readiness probe. `/healthz` is SearXNG's own liveness route and does not
# fan out to upstream engines, unlike `/search?q=...` which would cost a real
# metasearch fan-out on every startup.
_HEALTHZ_PATH = "/healthz"

# How often to poll while the webapp boots.
_POLL_INTERVAL_SEC = 0.5

# Grace period between SIGTERM and SIGKILL on shutdown.
_TERM_GRACE_SEC = 5.0

# The interpreter must be SearXNG's OWN venv: flask, lxml, curl_cffi, valkey
# and babel are not backend dependencies, so sys.executable cannot run it.
_PYTHON_RELATIVE = Path(".venv/bin/python")


def _resolve_home(settings: Settings) -> Path:
    """Where the searxng clone lives (defaults to <repo_root>/searxng)."""
    configured = str(getattr(settings, "searxng_home", "") or "").strip()
    return Path(configured).expanduser() if configured else _REPO_ROOT / "searxng"


def _resolve_python(settings: Settings, home: Path) -> Path:
    """The SearXNG interpreter. Falls back to the plain name on the PATH."""
    configured = str(getattr(settings, "searxng_python", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return home / _PYTHON_RELATIVE


async def _healthz_ok(base_url: str, timeout: float = 3.0) -> bool:
    """True when the endpoint answers /healthz. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{base_url}{_HEALTHZ_PATH}")
        return response.status_code == 200
    except Exception as exc:  # unreachable/timeout/DNS: all mean "not up yet"
        log.debug("searxng_healthz_failed", url=base_url, error=str(exc))
        return False


class SearxngSupervisor:
    """Starts and stops the SearXNG child process this backend owns.

    Instance state, not module state: the process handle lives on the instance
    the FastAPI lifespan owns, so it cannot leak across requests or outlive
    the app (AGENTS.md 4.3).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: Optional[asyncio.subprocess.Process] = None
        self.managed = False

    @property
    def base_url(self) -> str:
        return str(getattr(self.settings, "searxng_url", "") or "").strip().rstrip("/")

    async def start(self) -> bool:
        """Ensure SearXNG is reachable, spawning it if we are allowed to.

        Returns True only when THIS process started the instance. Never raises.
        """
        if not bool(getattr(self.settings, "searxng_enabled", True)):
            log.info("searxng_autostart_skipped", reason="disabled")
            return False
        base = self.base_url
        if not base:
            log.warning("searxng_autostart_skipped", reason="no_searxng_url")
            return False
        if not bool(getattr(self.settings, "searxng_autostart", True)):
            log.info("searxng_autostart_skipped", reason="disabled_by_config")
            return False

        # Someone else is already serving it: never adopt, never kill.
        if await _healthz_ok(base):
            log.info("searxng_already_running", url=base)
            return False

        home = _resolve_home(self.settings)
        python = _resolve_python(self.settings, home)
        if not home.is_dir():
            log.warning(
                "searxng_clone_missing",
                home=str(home),
                hint="git clone https://github.com/searxng/searxng.git "
                "(search falls back to Wikipedia/arXiv/Crossref without it)",
            )
            return False
        if not python.exists():
            log.warning(
                "searxng_venv_missing",
                python=str(python),
                hint="python3 -m venv searxng/.venv && "
                "searxng/.venv/bin/pip install -U -r searxng/requirements.txt",
            )
            return False

        env = dict(os.environ)
        # SearXNG reads its settings from this variable. An explicit
        # SEARXNG_SETTINGS_PATH in the environment wins over our default, so a
        # deployment that already points elsewhere keeps working.
        if "SEARXNG_SETTINGS_PATH" not in env:
            local = home / "settings.local.yml"
            if local.exists():
                env["SEARXNG_SETTINGS_PATH"] = str(local)

        started = await self._spawn(python, home, env)
        if not started:
            return False
        return await self._wait_ready(base)

    async def _spawn(
        self, python: Path, home: Path, env: dict
    ) -> bool:
        """Launch the webapp. Never raises."""
        log_file = home / "searxng.log"
        handle = None
        try:
            # Truncate: the tail of a previous run is noise, and a failure here
            # must be diagnosable from the CURRENT attempt.
            handle = log_file.open("w", encoding="utf-8")
        except OSError as exc:
            log.warning("searxng_logfile_failed", path=str(log_file), error=str(exc))
            handle = subprocess.DEVNULL

        try:
            self.process = await asyncio.create_subprocess_exec(
                str(python),
                "-m",
                "searx.webapp",
                cwd=str(home),
                env=env,
                stdout=handle,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            log.warning("searxng_spawn_failed", error=str(exc), exc_info=exc)
            self.process = None
            return False
        finally:
            # The child holds its own dup of the fd; ours is no longer needed.
            if handle is not subprocess.DEVNULL and handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass

        self.managed = True
        log.info("searxng_spawned", pid=self.process.pid, home=str(home))
        return True

    async def _wait_ready(self, base: str) -> bool:
        """Poll /healthz until the webapp answers or the timeout expires."""
        timeout = float(
            getattr(self.settings, "searxng_startup_timeout_sec", 45.0) or 45.0
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.process is not None and self.process.returncode is not None:
                # It exited during boot -- a bad secret_key or a port clash.
                # Reap it here so it never lingers holding 8080.
                log.warning(
                    "searxng_exited_during_startup",
                    returncode=self.process.returncode,
                    log_file=str(_resolve_home(self.settings) / "searxng.log"),
                )
                await self.stop()
                return False
            if await _healthz_ok(base):
                log.info("searxng_ready", url=base)
                return True
            await asyncio.sleep(_POLL_INTERVAL_SEC)

        log.warning(
            "searxng_startup_timeout",
            timeout_sec=timeout,
            log_file=str(_resolve_home(self.settings) / "searxng.log"),
            hint="search falls back to Wikipedia/arXiv/Crossref",
        )
        # A child that never became ready is stopped rather than left behind.
        await self.stop()
        return False

    async def stop(self) -> None:
        """Terminate the child we started. Never raises, always clears state."""
        process = self.process
        self.process = None
        if process is None or process.returncode is not None:
            self.managed = False
            return
        try:
            process.terminate()
        except ProcessLookupError:
            self.managed = False
            return
        except Exception as exc:
            log.warning("searxng_terminate_failed", error=str(exc))
            self.managed = False
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=_TERM_GRACE_SEC)
            log.info("searxng_stopped", pid=process.pid)
        except asyncio.TimeoutError:
            # Escalate: a live child still holding the port is the overlapping-
            # backends failure mode, which is worse than an unclean stop.
            log.warning("searxng_killing", pid=process.pid)
            await self._force_kill(process)
        except asyncio.CancelledError:
            # Shutdown was cancelled, but leaving the child alive is still the
            # worse outcome -- kill it, then let the cancellation propagate.
            await self._force_kill(process)
            raise
        finally:
            self.managed = False

    async def _force_kill(self, process: asyncio.subprocess.Process) -> None:
        """SIGKILL a child that ignored SIGTERM, then reap it."""
        try:
            process.kill()
            await process.wait()
        except Exception as exc:
            log.warning("searxng_kill_failed", pid=process.pid, error=str(exc))