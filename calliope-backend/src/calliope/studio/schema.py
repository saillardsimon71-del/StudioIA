from calliope.db import get_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_projects (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    aspect_ratio TEXT NOT NULL DEFAULT '16:9',
    script_source TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS studio_takes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK(kind IN ('image','video')),
    status TEXT NOT NULL DEFAULT 'pending',
    prompt TEXT NOT NULL,
    seed INTEGER,
    engine TEXT NOT NULL,
    source_take_id INTEGER REFERENCES studio_takes(id) ON DELETE SET NULL,
    request_id TEXT NOT NULL,
    request_index INTEGER NOT NULL DEFAULT 0,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    modal_call_id TEXT,
    submission_state TEXT NOT NULL DEFAULT 'new',
    render_state_json TEXT NOT NULL DEFAULT '{}',
    output_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(clip_id,kind,request_id,request_index)
);
CREATE INDEX IF NOT EXISTS idx_studio_takes_clip ON studio_takes(clip_id);
CREATE TABLE IF NOT EXISTS studio_shots (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL DEFAULT '',
    image_prompt TEXT NOT NULL DEFAULT '',
    dialogue TEXT NOT NULL DEFAULT '',
    audio_mode TEXT NOT NULL DEFAULT 'native',
    continue_from_previous INTEGER NOT NULL DEFAULT 0,
    trim_start_sec REAL,
    trim_end_sec REAL,
    selected_image_id INTEGER REFERENCES studio_takes(id) ON DELETE SET NULL,
    selected_video_id INTEGER REFERENCES studio_takes(id) ON DELETE SET NULL,
    approved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS studio_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    request_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    aspect_ratio TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    output_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id,request_id)
);
CREATE TABLE IF NOT EXISTS studio_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    concept TEXT NOT NULL,
    bible_json TEXT NOT NULL DEFAULT '{}',
    directing_profile_json TEXT NOT NULL DEFAULT '{}',
    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
    language TEXT NOT NULL DEFAULT 'en',
    target_seconds INTEGER NOT NULL DEFAULT 65,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS studio_episodes (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    series_id INTEGER NOT NULL REFERENCES studio_series(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    idea TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL,
    context_json TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    events_json TEXT NOT NULL DEFAULT '[]',
    ongoing_plot TEXT NOT NULL DEFAULT '',
    canon_revision INTEGER,
    UNIQUE(series_id,number),
    UNIQUE(series_id,request_id)
);
CREATE TABLE IF NOT EXISTS studio_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL REFERENCES studio_series(id) ON DELETE CASCADE,
    entity_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('character','location')),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    reference_path TEXT,
    identity_json TEXT NOT NULL DEFAULT '{}',
    reference_request_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(series_id,entity_key)
);
CREATE TABLE IF NOT EXISTS studio_shot_context (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS studio_productions (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT NOT NULL DEFAULT 'writing',
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT 'Écriture en attente',
    error TEXT,
    pause_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS studio_episode_archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id,request_id)
);
CREATE TABLE IF NOT EXISTS studio_audio (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS studio_audio_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    duration_sec REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS studio_director_projects (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    approved_hash TEXT,
    budget_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS studio_director_shots (
    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    contract_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS studio_director_reservations (
    take_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    estimated_eur REAL NOT NULL,
    video_seconds REAL NOT NULL
);
"""


def migrate_studio(db_path):
    conn = get_db(db_path)
    try:
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(studio_shots)")}
        for column in ("trim_start_sec", "trim_end_sec"):
            if column not in columns:
                conn.execute(f"ALTER TABLE studio_shots ADD COLUMN {column} REAL")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(studio_series)")}
        if "archived" not in columns:
            conn.execute("ALTER TABLE studio_series ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        for table, column in (
            ("studio_series", "directing_profile_json"),
            ("studio_takes", "render_state_json"),
            ("studio_entities", "identity_json"),
            ("studio_entities", "reference_request_json"),
        ):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{{}}'"
                )
        conn.commit()
    finally:
        conn.close()
