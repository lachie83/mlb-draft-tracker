PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS prospects (
    prospect_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mlb_person_id INTEGER,
    bis_player_id INTEGER,
    full_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    use_name TEXT,
    use_last_name TEXT,
    rank INTEGER,
    position_code TEXT,
    position_name TEXT,
    position_type TEXT,
    position_abbreviation TEXT,
    bats TEXT,
    throws TEXT,
    school_name TEXT,
    school_class TEXT,
    school_state TEXT,
    school_country TEXT,
    home_city TEXT,
    home_state TEXT,
    home_country TEXT,
    birth_date TEXT,
    current_age INTEGER,
    birth_city TEXT,
    birth_state_province TEXT,
    birth_country TEXT,
    height TEXT,
    weight INTEGER,
    active INTEGER,
    headshot_link TEXT,
    scouting_report TEXT,
    blurb TEXT,
    draft_year INTEGER,
    draft_type_code TEXT,
    draft_type_description TEXT,
    is_drafted INTEGER DEFAULT 0,
    is_pass INTEGER DEFAULT 0,
    pick_round TEXT,
    pick_number INTEGER,
    draft_team_id INTEGER,
    draft_team_name TEXT,
    draft_team_abbreviation TEXT,
    source TEXT NOT NULL DEFAULT 'baseballr_mlb_draft_prospects',
    source_rank_updated_at TEXT,
    source_pick_updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_payload TEXT,
    UNIQUE(mlb_person_id, draft_year),
    UNIQUE(full_name, school_name, draft_year)
);

CREATE INDEX IF NOT EXISTS idx_prospects_rank ON prospects(draft_year, rank);
CREATE INDEX IF NOT EXISTS idx_prospects_pick ON prospects(draft_year, pick_number);
CREATE INDEX IF NOT EXISTS idx_prospects_drafted ON prospects(draft_year, is_drafted);

CREATE TABLE IF NOT EXISTS draft_slots (
    slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_year INTEGER NOT NULL,
    round_label TEXT NOT NULL,
    round_pick_number INTEGER,
    pick_number INTEGER,
    team_id INTEGER,
    team_name TEXT NOT NULL,
    team_abbrev TEXT,
    slot_type TEXT NOT NULL,
    pick_value NUMERIC,
    bonus_pool_value NUMERIC,
    compensation_for TEXT,
    acquired_from TEXT,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'official_mlb_order',
    source_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_payload TEXT,
    UNIQUE(draft_year, pick_number)
);

CREATE INDEX IF NOT EXISTS idx_draft_slots_round ON draft_slots(draft_year, round_label, round_pick_number);

CREATE TABLE IF NOT EXISTS actual_picks (
    pick_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_year INTEGER NOT NULL,
    pick_number INTEGER NOT NULL,
    round_label TEXT,
    round_pick_number INTEGER,
    team_id INTEGER,
    team_name TEXT NOT NULL,
    team_abbrev TEXT,
    prospect_id INTEGER,
    mlb_person_id INTEGER,
    player_name TEXT NOT NULL,
    player_position TEXT,
    school_name TEXT,
    source TEXT NOT NULL,
    source_event_id TEXT,
    picked_at TEXT,
    signed_status TEXT,
    bonus_amount NUMERIC,
    slot_value NUMERIC,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_payload TEXT,
    FOREIGN KEY(prospect_id) REFERENCES prospects(prospect_id),
    UNIQUE(draft_year, pick_number)
);

CREATE INDEX IF NOT EXISTS idx_actual_picks_team ON actual_picks(draft_year, team_name);
CREATE INDEX IF NOT EXISTS idx_actual_picks_player ON actual_picks(draft_year, player_name);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_year INTEGER NOT NULL,
    pick_number INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    prospect_id INTEGER,
    mlb_person_id INTEGER,
    player_name TEXT NOT NULL,
    predicted_probability REAL NOT NULL,
    rank_score REAL,
    mock_score REAL,
    fit_score REAL,
    buzz_score REAL,
    model_version TEXT NOT NULL,
    prediction_source TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    raw_payload TEXT,
    FOREIGN KEY(prospect_id) REFERENCES prospects(prospect_id)
);

CREATE INDEX IF NOT EXISTS idx_predictions_pick ON predictions(draft_year, pick_number, predicted_probability DESC);

CREATE TABLE IF NOT EXISTS source_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    run_type TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    records_seen INTEGER DEFAULT 0,
    records_written INTEGER DEFAULT 0,
    error_message TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS telegram_events_sent (
    event_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_hash TEXT,
    channel TEXT DEFAULT 'telegram',
    pick_number INTEGER,
    message_text TEXT
);

CREATE TABLE IF NOT EXISTS config (
    config_key TEXT PRIMARY KEY,
    config_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW IF NOT EXISTS v_top_250 AS
SELECT *
FROM prospects
WHERE draft_year = 2026 AND rank IS NOT NULL AND rank <= 250
ORDER BY rank;

CREATE VIEW IF NOT EXISTS v_best_available AS
SELECT *
FROM prospects
WHERE draft_year = 2026
  AND rank IS NOT NULL
  AND COALESCE(is_drafted, 0) = 0
ORDER BY rank;

CREATE VIEW IF NOT EXISTS v_pick_results AS
SELECT
    s.draft_year,
    s.pick_number,
    s.round_label,
    s.round_pick_number,
    s.team_name AS scheduled_team_name,
    s.team_abbrev AS scheduled_team_abbrev,
    a.player_name,
    a.player_position,
    a.school_name,
    p.rank AS board_rank,
    a.picked_at,
    a.source
FROM draft_slots s
LEFT JOIN actual_picks a
    ON s.draft_year = a.draft_year
   AND s.pick_number = a.pick_number
LEFT JOIN prospects p
    ON a.mlb_person_id = p.mlb_person_id
   AND a.draft_year = p.draft_year
ORDER BY s.pick_number;
