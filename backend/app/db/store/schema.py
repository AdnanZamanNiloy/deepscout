from __future__ import annotations

import aiosqlite
import os



CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    report TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    query TEXT NOT NULL,
    complexity TEXT,
    agent_count INTEGER,
    status TEXT NOT NULL,
    estimated_cost REAL,
    confidence REAL,
    max_iterations INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    question TEXT NOT NULL,
    axis TEXT,
    search_type TEXT,
    priority INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    url TEXT NOT NULL,
    reliability_score REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    claim TEXT NOT NULL,
    source_url TEXT NOT NULL,
    confidence REAL,
    verified INTEGER,
    agent TEXT NOT NULL DEFAULT '',
    challenged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    node TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    started_at TEXT,
    ended_at TEXT
);

-- The exact NDJSON frames emitted for a run, in order.
--
-- agent_events records WHICH NODE ran and when; it does not record what the
-- user saw. The pipeline trace renders the emitted frames (search queries,
-- their sources, findings), so replaying from agent_events alone produced an
-- empty trace on restore — the session looked like it had no history at all.
-- Storing the frames verbatim means a restored session replays the same trace
-- the live run showed, instead of a reconstruction of it.
CREATE TABLE IF NOT EXISTS run_trace_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    seq INTEGER NOT NULL,
    frame TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    source_id INTEGER REFERENCES sources(id),
    raw_snippet TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS critic_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    iteration INTEGER NOT NULL,
    is_sufficient INTEGER,
    reason TEXT,
    confidence REAL,
    breakdown TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    option_label TEXT NOT NULL,
    description TEXT NOT NULL,
    is_recommended INTEGER,
    rationale TEXT,
    risk_note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS final_reports (
    run_id TEXT PRIMARY KEY REFERENCES research_runs(id),
    report_markdown TEXT NOT NULL,
    confidence REAL,
    generated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    claim_a TEXT NOT NULL,
    source_a TEXT NOT NULL,
    claim_b TEXT NOT NULL,
    source_b TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    claim TEXT NOT NULL,
    verified INTEGER NOT NULL,
    score REAL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_runs(id),
    marker INTEGER NOT NULL,
    domain TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    api_key_enc TEXT NOT NULL,
    key_hint TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    -- Human-readable label for the endpoint (`model` stays the id sent to the
    -- provider API). Kept nullable-with-default so every existing row and the
    -- ALTER in init_db resolve to the same shape.
    model_name TEXT DEFAULT '',
    -- Sampling temperature for this provider. NULL = unset (use the global
    -- LLM_TEMPERATURE default). Kept nullable so an explicit 0 stays distinct
    -- from "unset": a provider limited to 0/0.6/1 must be able to say 0.
    temperature REAL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_chains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_chain_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id INTEGER NOT NULL REFERENCES provider_chains(id) ON DELETE CASCADE,
    provider_id INTEGER NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


MAX_PROVIDER_CHAINS = 20


MAX_CHAIN_MEMBERS = 12


SCHEMA_VERSION = 5


_initialized_paths: set[str] = set()


def normalize_db_path(database_path: str) -> str:
    """Resolve DATABASE_URL-ish values to a plain filesystem path.

    Accepts plain paths ("./research.db"), sqlite/file URI forms
    ("file:/abs/path.db", "sqlite:///abs/path.db") and strips query
    fragments. Missing parent directories are created so first-run
    deployments with a nested DATABASE_URL never fail with
    "unable to open database file".
    """
    path = str(database_path or "./research.db").strip()
    if not path:
        path = "./research.db"
    for prefix in ("sqlite://", "file:"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    # sqlite:///abs/path.db -> //abs/path.db after prefix strip
    while path.startswith("//"):
        path = path[1:]
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path:
        path = "./research.db"
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass  # unwritable location: let aiosqlite surface a clear error
    return path


class _AutoInitConnect:
    """Drop-in replacement for ``aiosqlite.connect(path)`` as an async
    context manager that first ensures the schema exists.

    Usage is unchanged at call sites::

        async with _connect(database_path) as db:
            ...

    A fresh database (or a route-level test that never ran app startup)
    transparently gets the schema created instead of raising
    ``no such table``.
    """

    __slots__ = ("_path", "_conn")

    def __init__(self, database_path: str):
        self._path = normalize_db_path(database_path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        if self._path not in _initialized_paths:
            await init_db(self._path)
            _initialized_paths.add(self._path)
        self._conn = aiosqlite.connect(self._path)
        return await self._conn.__aenter__()

    async def __aexit__(self, *exc_info) -> None:
        if self._conn is not None:
            await self._conn.__aexit__(*exc_info)


def _connect(database_path: str) -> _AutoInitConnect:
    return _AutoInitConnect(database_path)


async def init_db(database_path: str) -> None:
    async with aiosqlite.connect(normalize_db_path(database_path)) as db:
        # WAL allows concurrent readers alongside a writer; busy_timeout
        # stops spurious "database is locked" errors under contention.
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
        # executescript handles the multi-statement CREATE TABLE block.
        await db.executescript(CREATE_TABLE_SQL)
        review_cols = await db.execute("PRAGMA table_info(critic_reviews)")
        review_names = {r[1] for r in await review_cols.fetchall()}
        if "breakdown" not in review_names:
            await db.execute("ALTER TABLE critic_reviews ADD COLUMN breakdown TEXT NOT NULL DEFAULT '{}'")
        if "improved_queries" not in review_names:
            await db.execute("ALTER TABLE critic_reviews ADD COLUMN improved_queries TEXT NOT NULL DEFAULT '[]'")
        run_cols = await db.execute("PRAGMA table_info(research_runs)")
        run_names = {r[1] for r in await run_cols.fetchall()}
        if "max_iterations" not in run_names:
            await db.execute("ALTER TABLE research_runs ADD COLUMN max_iterations INTEGER NOT NULL DEFAULT 3")
        # Chat sessions (additive, idempotent): every run belongs to exactly
        # one session so multiple questions reuse the same chat identity.
        if "session_id" not in run_names:
            await db.execute("ALTER TABLE research_runs ADD COLUMN session_id TEXT")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_session ON research_runs(session_id, created_at, id)"
        )
        claim_cols = await db.execute("PRAGMA table_info(claims)")
        claim_names = {r[1] for r in await claim_cols.fetchall()}
        if "agent" not in claim_names:
            await db.execute("ALTER TABLE claims ADD COLUMN agent TEXT NOT NULL DEFAULT ''")
        if "challenged" not in claim_names:
            await db.execute("ALTER TABLE claims ADD COLUMN challenged INTEGER NOT NULL DEFAULT 0")
        # Separate audit/trace document (additive, idempotent). The primary
        # answer no longer carries pipeline metadata, so the audit is persisted
        # alongside it for replay and export.
        report_cols = await db.execute("PRAGMA table_info(final_reports)")
        report_names = {r[1] for r in await report_cols.fetchall()}
        if "audit_markdown" not in report_names:
            await db.execute(
                "ALTER TABLE final_reports ADD COLUMN audit_markdown TEXT NOT NULL DEFAULT ''"
            )
        # Provider model label (additive, idempotent). `model` is the id sent to
        # the provider API; `model_name` is the human label shown in the UI.
        # Gated on PRAGMA (not on SCHEMA_VERSION) so a database written by any
        # build converges to one shape: CREATE TABLE IF NOT EXISTS never adds
        # columns to an existing table, so a DB created before this column
        # existed would otherwise keep it missing forever.
        provider_cols = await db.execute("PRAGMA table_info(llm_providers)")
        provider_names = {r[1] for r in await provider_cols.fetchall()}
        if "model_name" not in provider_names:
            await db.execute("ALTER TABLE llm_providers ADD COLUMN model_name TEXT DEFAULT ''")
        # Source identity vs retrieval provenance on persisted sources
        # (additive, idempotent). A source row used to carry only a URL, so the
        # only way to name the publisher of a hit was to re-derive it later --
        # and nothing at all recorded WHICH index returned it. These four
        # columns are the durable form of that split. DEFAULT '' keeps every
        # pre-existing row valid and every existing reader working.
        source_cols = await db.execute("PRAGMA table_info(sources)")
        source_names = {r[1] for r in await source_cols.fetchall()}
        for column, ddl in (
            ("source_domain", "TEXT NOT NULL DEFAULT ''"),
            ("publisher", "TEXT NOT NULL DEFAULT ''"),
            ("retrieval_provider", "TEXT NOT NULL DEFAULT ''"),
            ("retrieval_engine", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in source_names:
                await db.execute(f"ALTER TABLE sources ADD COLUMN {column} {ddl}")
        # Per-provider sampling temperature (additive, idempotent). Deliberately
        # NULLABLE with no default: NULL means "unset, use the global default",
        # which must stay distinguishable from an explicit 0.0 — 0 is a VALID
        # and commonly required temperature (some models accept only 0, 0.6 or
        # 1), so a DEFAULT would silently blur "the user chose 0" into "unset"
        # and send 0.1 to a provider that rejects it.
        if "temperature" not in provider_names:
            await db.execute("ALTER TABLE llm_providers ADD COLUMN temperature REAL")
        # Provider fallback chains (additive, idempotent): ordered lists of
        # existing providers. A single enabled chain drives the runtime chain;
        # members are ordered by `position` and unique per chain.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chain_members ON provider_chain_members(chain_id, position, id)"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_chain_member_unique "
            "ON provider_chain_members(chain_id, provider_id)"
        )
        # One-time repair of a pre-exclusivity state: a single active model and
        # an enabled chain could coexist. The runtime prefers the chain, so
        # clear the stale active flag to make serving mode unambiguous.
        cur = await db.execute("SELECT id FROM provider_chains WHERE is_enabled = 1 LIMIT 1")
        if await cur.fetchone() is not None:
            await db.execute("UPDATE llm_providers SET is_active = 0")
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);"
        )
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        current = row[0] if row and row[0] is not None else 0
        if current < SCHEMA_VERSION:
            await db.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        await db.commit()
