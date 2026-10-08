"""SQLite persistence: schema, sessions, records and resume state.

Refactor note
-------------
The implementation now lives in the `app.db.store` package, split into
single-responsibility modules (`schema`, `sessions`, `records`, `resume`). This
module is the stable facade: it re-exports every name the rest of the codebase
imports from `app.db.sqlite`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

from app.db.store.schema import (  # noqa: F401
    CREATE_TABLE_SQL,
    MAX_PROVIDER_CHAINS,
    MAX_CHAIN_MEMBERS,
    SCHEMA_VERSION,
    _initialized_paths,
    normalize_db_path,
    _AutoInitConnect,
    _connect,
    init_db,
)
from app.db.store.sessions import (  # noqa: F401
    save_report,
    _now,
    ensure_session,
    touch_session,
    list_sessions,
    get_session,
    start_research_run,
    complete_research_run,
)
from app.db.store.records import (  # noqa: F401
    save_agent_tasks,
    save_sources,
    save_claims,
    mark_challenged_claims,
    record_event,
    save_trace_frames,
    load_trace_frames,
    save_evidence,
    save_critic_review,
    save_decisions,
    save_contradictions,
    save_verification_results,
    save_citations,
    save_final_report,
)
from app.db.store.resume import (  # noqa: F401
    load_state_for_resume,
    mark_run_resumable_reset,
    get_run_trace,
)
