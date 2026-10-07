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
"""


def migrate_studio(db_path):
    conn = get_db(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
